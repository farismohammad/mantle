#!/usr/bin/env python3
"""A small JSON boundary between the native Mac app and Mantle's existing engine.

One JSON request on stdin produces one JSON response on stdout. Secrets are sent
over stdin, never arguments. `--cli` preserves the complete interactive CLI.
"""
from __future__ import annotations

import contextlib
import dataclasses
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone
from typing import Any
import uuid

# Source checkout; packaged builds place the engine on PYTHONPATH instead.
SOURCE = Path(__file__).resolve().parents[2] / "src"
if SOURCE.is_dir():
    sys.path.insert(0, str(SOURCE))

from mantle.agents import AgentStore, validate_agent_name
from mantle.cloud import CloudClient, normalize_cloud_url, read_sync, record_sync, sync_status
from mantle.configuration import parse_agent_yaml, dump_agent_yaml, format_validation_error, update_agent_yaml
from mantle.credentials import CredentialStore, is_isolated, isolate_agent, share_agent
from mantle.errors import MantleError
from mantle.models import AgentConfig, default_agent_config
from mantle.paths import MantlePaths
from mantle.providers import PROVIDERS
from mantle.runtimes import RuntimeManager
from mantle.secrets import parse_dotenv, set_dotenv_value, remove_dotenv_value
from mantle.settings import load_settings, save_settings
from mantle.storage import atomic_write_text
from pydantic import ValidationError

MAX_REQUEST_BYTES = 1_500_000
MAX_SESSION_BYTES = 12_000_000


def required_text(request: dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MantleError(f"{key} is required.")
    return value


def confirmation(request: dict[str, Any]) -> None:
    if request.get("confirmed") is not True:
        raise MantleError("Review this change and confirm it before continuing.")


def reject_symlinks(path: Path) -> None:
    """Reject an untrusted file or ancestor before reading or writing user state."""
    for item in (path, *path.parents):
        if item.is_symlink():
            raise MantleError(f"This operation cannot follow a symbolic link: {item}")


def agent_root(paths: MantlePaths, name: str) -> Path:
    validate_agent_name(name)
    root = paths.agent(name)
    reject_symlinks(root)
    reject_symlinks(root / "agent.yaml")
    return root


def preflight_agent_mutation(paths: MantlePaths, name: str, *, preserve: bool = False) -> Path:
    """Check every destination before the engine can perform the first effect."""
    from mantle.launcher import AgentLauncher
    from mantle.mcp import MCP_CONFIG_FILENAME, MCP_EXTENSION_FILENAME
    root = agent_root(paths, name)
    for relative in ("settings.json", "models.json", "mantle-state.json", "tmp", "npm", "git",
                     "skills", "packages", "extensions", ".mantle-auth-isolated", ".auth.json.link",
                     MCP_CONFIG_FILENAME, f"extensions/{MCP_EXTENSION_FILENAME}"):
        reject_symlinks(root / relative)
    reject_symlinks(paths.config)
    reject_symlinks(paths.runtimes)
    reject_symlinks(paths.home / "auth" / "auth.json")
    for relative in ("skills", "packages", "npm", "git", "extensions"):
        AgentLauncher._validate_tree(root / relative, root)
    if preserve:
        for relative in ("context", "sessions", "logs", "auth.json.pre-shared", "auth.json.invalid"):
            reject_symlinks(root / relative)
            AgentLauncher._validate_tree(root / relative, root)
    return root


def install_cli_preflight() -> None:
    """Cover interactive/embedded CLI launches without modifying upstream files."""
    from mantle.launcher import AgentLauncher
    original = AgentLauncher.build
    if getattr(original, "_desktop_preflight", False):
        return

    def guarded_build(self, agent_name, **kwargs):
        preflight_agent_mutation(self.paths, agent_name, preserve=True)
        return original(self, agent_name, **kwargs)

    guarded_build._desktop_preflight = True
    AgentLauncher.build = guarded_build


def backup_file(paths: MantlePaths, source: Path, group: str) -> str | None:
    reject_symlinks(source)
    if not source.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = paths.home / "backups" / group / f"{stamp}-{uuid.uuid4().hex[:8]}-{source.name}"
    reject_symlinks(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(source.read_bytes())
        target.flush()
        os.fsync(target.fileno())
    return str(destination)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_credential_summary(paths: MantlePaths) -> list[dict[str, Any]]:
    reject_symlinks(paths.home / "auth" / "auth.json")
    return [
        {"provider": name, "type": entry.get("type", "unknown")}
        for name, entry in sorted(CredentialStore(paths).load().items())
    ]


def summarize_agent(paths: MantlePaths, config: AgentConfig) -> dict[str, Any]:
    from mantle.launcher import AgentLauncher
    root = agent_root(paths, config.agent.name)
    text = (root / "agent.yaml").read_text(encoding="utf-8")
    sessions = list_sessions(paths, {"agent": config.agent.name}, include_titles=False)
    installed_skills: list[str] = []
    installed_packages: list[str] = []
    resource_warnings: list[str] = []
    for skill in config.resources.skills:
        try:
            skill_file = root / "skills" / skill.name / "SKILL.md"
            AgentLauncher._within(skill_file, root)
            if skill_file.is_file():
                installed_skills.append(skill.name)
        except (MantleError, OSError, RuntimeError) as error:
            resource_warnings.append(f"{skill.name}: {clean_error(error, {})}")
    for package in config.resources.packages:
        try:
            package_root = AgentLauncher._find_package_root(root, package)
            if package_root is not None:
                AgentLauncher._within(package_root / "package.json", root)
                installed_packages.append(package.name)
        except (MantleError, OSError, RuntimeError) as error:
            resource_warnings.append(f"{package.name}: {clean_error(error, {})}")
    return {
        "name": config.agent.name,
        "description": config.agent.description,
        "provider": config.model.provider,
        "model": config.model.name,
        "runtime": config.runtime.version,
        "config": config.model_dump(mode="json"),
        "yaml": text,
        "digest": digest(text),
        "path": str(root),
        "skillCount": len(config.resources.skills),
        "packageCount": len(config.resources.packages),
        "installedSkills": installed_skills,
        "installedPackages": installed_packages,
        "resourceWarnings": resource_warnings,
        "sessionCount": len(sessions),
        "isolated": is_isolated(root),
        "sync": read_sync(root),
    }


def snapshot(paths: MantlePaths) -> dict[str, Any]:
    warnings: list[str] = []
    agents: list[dict[str, Any]] = []
    damaged: dict[str, str] = {}
    reject_symlinks(paths.agents)
    if paths.agents.is_dir():
        for root in sorted(paths.agents.iterdir()):
            if root.name.startswith(".") or not root.is_dir():
                continue
            try:
                agent_root(paths, root.name)
                agents.append(summarize_agent(paths, AgentStore(paths).get(root.name)))
            except (MantleError, OSError, ValidationError) as error:
                damaged[root.name] = clean_error(error, {})
    settings: dict[str, Any] = {}
    try:
        reject_symlinks(paths.config)
        settings = load_settings(paths).model_dump(mode="json")
    except (MantleError, OSError, ValidationError) as error:
        warnings.append(clean_error(error, {}))
    runtimes: list[dict[str, Any]] = []
    try:
        reject_symlinks(paths.runtimes)
        manager = RuntimeManager(paths)
        for runtime in manager.list():
            runtimes.append({
                **runtime.model_dump(mode="json"),
                "default": runtime.version == settings.get("default_runtime"),
                "isDefault": runtime.version == settings.get("default_runtime"),
                "users": [a["name"] for a in agents if a["runtime"] == runtime.version or
                          (a["runtime"] == "latest" and settings.get("default_runtime") == runtime.version)],
            })
    except (MantleError, OSError, ValidationError) as error:
        warnings.append(clean_error(error, {}))
    credentials: list[dict[str, Any]] = []
    secret_names: list[str] = []
    try:
        credentials = safe_credential_summary(paths)
        reject_symlinks(paths.dotenv)
        secret_names = sorted(parse_dotenv(paths.dotenv))
    except (MantleError, OSError) as error:
        warnings.append(clean_error(error, {}))
    return {
        "home": str(paths.home), "agents": agents, "damaged": damaged,
        "runtimes": runtimes, "credentials": credentials, "secretNames": secret_names,
        "settings": settings, "warnings": warnings,
        "providers": [{"id": p.id, "name": p.name, "api_key": p.api_key,
                       "oauth": p.oauth, "default_model": p.default_model} for p in PROVIDERS.values()],
    }


def session_files(paths: MantlePaths, agent: str) -> list[Path]:
    base = agent_root(paths, agent) / "sessions"
    reject_symlinks(base)
    if not base.is_dir():
        return []
    files = []
    for current, directories, names in os.walk(base, followlinks=False):
        directories[:] = [d for d in directories if not (Path(current) / d).is_symlink()]
        files.extend(Path(current) / n for n in names if n.endswith(".jsonl") and not (Path(current) / n).is_symlink())
    return files


def session_message(entry: dict[str, Any]) -> dict[str, Any] | None:
    message = entry.get("message", entry if "role" in entry else None)
    if not isinstance(message, dict):
        return None
    role = message.get("role", "unknown")
    content = message.get("content", "")
    if isinstance(content, list):
        content = "\n".join(part.get("text", "") for part in content
                            if isinstance(part, dict) and isinstance(part.get("text"), str))
    if not isinstance(content, str) or not content.strip():
        return None
    return {"id": str(entry.get("id", "")), "role": role, "text": content,
            "timestamp": entry.get("timestamp", message.get("timestamp"))}


def list_sessions(paths: MantlePaths, request: dict[str, Any], *, include_titles: bool = True) -> list[dict[str, Any]]:
    selected = request.get("agent")
    names = [selected] if isinstance(selected, str) and selected else [a.agent.name for a in AgentStore(paths).list()]
    result = []
    for name in names:
        for file in session_files(paths, name):
            info = {"id": file.stem, "agent": name, "path": str(file), "title": "Untitled session",
                    "updatedAt": datetime.fromtimestamp(file.stat().st_mtime, timezone.utc).isoformat(), "cwd": ""}
            if include_titles:
                try:
                    with file.open(encoding="utf-8") as stream:
                        for index, line in enumerate(stream):
                            if index >= 100 or len(line) > 1_000_000:
                                break
                            try:
                                entry = json.loads(line)
                            except ValueError:
                                continue
                            if not isinstance(entry, dict):
                                continue
                            if entry.get("type") == "session":
                                info["id"] = str(entry.get("id", file.stem))
                                info["cwd"] = str(entry.get("cwd", ""))
                            if entry.get("type") == "session_info" and isinstance(entry.get("name"), str):
                                info["title"] = entry["name"]
                            message = session_message(entry)
                            if message and message["role"] == "user" and info["title"] == "Untitled session":
                                info["title"] = message["text"].strip().replace("\n", " ")[:100]
                    # Pi appends session renames, often long after the first prompt.
                    with file.open("rb") as stream:
                        stream.seek(max(0, file.stat().st_size - 65536))
                        tail = stream.read().decode("utf-8", errors="replace")
                    for line in reversed(tail.split("\n")):
                        try:
                            entry = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(entry, dict) and entry.get("type") == "session_info" and isinstance(entry.get("name"), str):
                            info["title"] = entry["name"] or info["title"]
                            break
                except (OSError, UnicodeError):
                    info["title"] = "Unreadable session"
            result.append(info)
    return sorted(result, key=lambda item: item["updatedAt"], reverse=True)


def read_session(paths: MantlePaths, request: dict[str, Any]) -> dict[str, Any]:
    name = required_text(request, "agent")
    base = agent_root(paths, name) / "sessions"
    file = Path(required_text(request, "path")).expanduser()
    if not file.is_absolute():
        file = base / file
    reject_symlinks(file)
    try:
        file.resolve().relative_to(base.resolve())
    except ValueError as error:
        raise MantleError("The selected session is outside this agent's sessions folder.") from error
    if file.suffix != ".jsonl" or not file.is_file():
        raise MantleError("The selected session file could not be found.")
    if file.stat().st_size > MAX_SESSION_BYTES:
        raise MantleError("This session is too large to preview. Open it in Finder or resume it in Terminal.")
    messages = []
    header: dict[str, Any] = {}
    with file.open(encoding="utf-8") as stream:
        for index, line in enumerate(stream):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "session":
                header = {key: entry.get(key) for key in ("id", "cwd", "timestamp")}
            message = session_message(entry)
            if message:
                if not message["id"]:
                    message["id"] = str(index)
                messages.append(message)
    return {"session": header, "messages": messages[-1000:], "truncated": len(messages) > 1000}


def registry_client(paths: MantlePaths, request: dict[str, Any]) -> CloudClient:
    import httpx
    base = request.get("url")
    insecure = request.get("insecure")
    if insecure is not None and not isinstance(insecure, bool):
        raise MantleError("insecure must be true or false.")
    token = request.get("registryToken")
    client = CloudClient(paths, base_url=base, insecure=insecure, timeout=15)
    if token:
        if not isinstance(token, str) or any(ord(c) < 32 for c in token):
            raise MantleError("The registry token must be a single line.")
        if not client.base_url.startswith("https://"):
            raise MantleError("Use HTTPS before sending a registry access token.")
        expected_origin = request.get("registryTokenOrigin")
        if not isinstance(expected_origin, str) or not expected_origin:
            raise MantleError("Re-enter the registry access token for this registry address.")
        expected = httpx.URL(expected_origin)
        actual = httpx.URL(client.base_url)
        if (expected.scheme, expected.host, expected.port or 443) != (actual.scheme, actual.host, actual.port or 443):
            raise MantleError("This registry access token belongs to a different address. Re-enter it for this registry.")
        client.client.headers["Authorization"] = f"Bearer {token}"
        # Prevent a redirect from carrying proxy credentials to a different origin.
        client.client.follow_redirects = False
    return client


def registry_result(response) -> Any:
    try:
        payload = response.json()
        return payload["data"]
    except (ValueError, KeyError, TypeError) as error:
        raise MantleError("The registry returned an invalid response.") from error


def cloud_operation(paths: MantlePaths, request: dict[str, Any], operation: str) -> Any:
    if operation == "cloud.disconnect":
        confirmation(request)
        reject_symlinks(paths.config)
        settings = load_settings(paths)
        backup_file(paths, paths.config, "settings")
        settings.cloud_url, settings.cloud_insecure = None, False
        save_settings(paths, settings)
        return {"connected": False}
    if operation in {"cloud.start", "cloud.stop", "cloud.logs"}:
        from mantle import cloud_server
        reject_symlinks(paths.local_cloud)
        reject_symlinks(cloud_server.compose_file(paths))
        reject_symlinks(cloud_server.env_file(paths))
        if operation == "cloud.logs":
            saved = cloud_server.saved(paths)
            return {"logs": cloud_server.recent_logs(paths, saved, lines=100) if saved else "No local registry has been started."}
        confirmation(request)
        if operation == "cloud.stop":
            saved = cloud_server.saved(paths)
            if saved is None:
                raise MantleError("No local registry has been configured.")
            cloud_server.down(paths, saved)
            return {"stopped": True}
        from mantle.cli_cloud import start_cloud
        start_cloud(port=request.get("port"), version=request.get("version"), connect=True, yes=True)
        return {"started": True}
    client = registry_client(paths, request)
    try:
        if operation in {"cloud.connect", "cloud.status"}:
            if operation == "cloud.connect":
                confirmation(request)
            health = client.health()
            if health.get("service") != "mantle-cloud":
                raise MantleError("This address is not a Mantle registry.")
            if operation == "cloud.connect":
                reject_symlinks(paths.config)
                settings = load_settings(paths)
                backup_file(paths, paths.config, "settings")
                settings.cloud_url = client.base_url
                settings.cloud_insecure = bool(request.get("insecure", False))
                save_settings(paths, settings)
            return {"connected": True, "url": client.base_url, "health": health}
        if operation == "cloud.list":
            result = client.list()
            for item in result:
                root = agent_root(paths, item["name"])
                item["installed"] = root.is_dir()
                item["syncStatus"] = sync_status(root if root.is_dir() else None, client.base_url, item["revision"])
            return result
        name = required_text(request, "name")
        root = agent_root(paths, name)
        if operation in {"cloud.push", "cloud.pull", "cloud.update"}:
            preflight_agent_mutation(paths, name, preserve=operation == "cloud.update")
        if operation == "cloud.latest":
            return client.latest(name)
        if operation == "cloud.history":
            return registry_result(client._request("GET", f"/api/agents/{name}/revisions", agent=name))
        if operation == "cloud.revision":
            revision = request.get("revision")
            if type(revision) is not int or revision < 1:
                raise MantleError("Select a valid revision number.")
            return registry_result(client._request("GET", f"/api/agents/{name}/revisions/{revision}", agent=name))
        if operation == "cloud.delete":
            confirmation(request)
            client.delete(name)
            return {"deleted": name}
        if operation in {"cloud.push", "cloud.save"}:
            confirmation(request)
            if operation == "cloud.save":
                text = required_text(request, "yaml")
            else:
                AgentStore(paths).get(name)
                text = (root / "agent.yaml").read_text(encoding="utf-8")
            parse_agent_yaml(text, expected_name=name)
            if request.get("expectedRevision") is not None:
                remote = client.latest(name)
                if remote["revision"]["number"] != request["expectedRevision"]:
                    raise MantleError("The registry has a newer revision. Refresh and review the changes before publishing.")
            result = client.put(name, text)
            if operation == "cloud.push":
                reject_symlinks(root / "mantle-state.json")
                record_sync(root, client.base_url, result["revision"]["number"], text)
            return result
        remote = client.latest(name)
        config = parse_agent_yaml(remote["yaml"], expected_name=name)
        revision = remote["revision"]["number"]
        if operation == "cloud.diff":
            local = (root / "agent.yaml").read_text(encoding="utf-8") if (root / "agent.yaml").is_file() else ""
            difference = "".join(difflib.unified_diff(local.splitlines(keepends=True), remote["yaml"].splitlines(keepends=True),
                fromfile=f"{name} · This Mac", tofile=f"{name} · Registry revision {revision}"))
            return {"name": name, "revision": revision, "diff": difference,
                    "local": local, "remote": remote["yaml"], "localDigest": digest(local)}
        from mantle.provisioning import Provisioner
        if operation == "cloud.pull":
            if root.exists():
                raise MantleError(f'Agent "{name}" already exists. Compare it with the registry before updating.')
            Provisioner(paths).install(config, yaml_text=remote["yaml"])
            reject_symlinks(root / "mantle-state.json")
            record_sync(root, client.base_url, revision, remote["yaml"])
            return {"name": name, "revision": revision}
        if operation == "cloud.update":
            confirmation(request)
            AgentStore(paths).require_local(name)
            if request.get("expectedRevision") is not None and request["expectedRevision"] != revision:
                raise MantleError("The registry changed after the comparison. Refresh and review it again.")
            local = (root / "agent.yaml").read_text(encoding="utf-8")
            if request.get("expectedDigest") is not None and request["expectedDigest"] != digest(local):
                raise MantleError("Your local configuration changed after the comparison. Refresh and review it again.")
            backup = backup_file(paths, root / "agent.yaml", name)
            Provisioner(paths).update(config, yaml_text=remote["yaml"])
            record_sync(root, client.base_url, revision, remote["yaml"])
            return {"name": name, "revision": revision, "backup": backup}
        raise MantleError(f"Unknown registry action: {operation}")
    finally:
        client.client.close()


def dispatch(request: dict[str, Any], paths: MantlePaths | None = None) -> Any:
    paths = paths or MantlePaths.discover()
    operation = required_text(request, "operation")
    reject_symlinks(paths.home)
    store = AgentStore(paths)
    if operation == "snapshot":
        return snapshot(paths)
    if operation == "doctor":
        from mantle.doctor import run_checks
        return [dataclasses.asdict(check) for check in run_checks(paths, cloud=request.get("cloud") is True)]
    if operation == "sessions.list":
        return list_sessions(paths, request)
    if operation == "sessions.read":
        return read_session(paths, request)
    if operation.startswith("cloud."):
        return cloud_operation(paths, request, operation)
    if operation in {"agent.create", "agent.save", "agent.validate", "agent.format"}:
        text = required_text(request, "yaml")
        name = request.get("name") or None
        config = parse_agent_yaml(text, expected_name=name)
        if operation in {"agent.validate", "agent.format"}:
            return {"valid": True, "config": config.model_dump(mode="json"), "yaml": dump_agent_yaml(config)}
        root = agent_root(paths, config.agent.name)
        if operation == "agent.create":
            store.create(config.agent.name, config=config)
            # Keep the author's comments and spacing from the initially supplied YAML.
            atomic_write_text(root / "agent.yaml", text, mode=0o600)
            return summarize_agent(paths, config)
        confirmation(request)
        store.require_local(config.agent.name)
        current = (root / "agent.yaml").read_text(encoding="utf-8")
        if request.get("expectedDigest") is not None and request["expectedDigest"] != digest(current):
            raise MantleError("This configuration changed elsewhere. Reload it before saving.")
        if request.get("preserveComments") is True:
            text = update_agent_yaml(current, config)
        backup = backup_file(paths, root / "agent.yaml", config.agent.name)
        atomic_write_text(root / "agent.yaml", text, mode=0o600)
        return {**summarize_agent(paths, config), "backup": backup}
    if operation == "agent.delete":
        confirmation(request)
        name = required_text(request, "name")
        root = agent_root(paths, name)
        backup_file(paths, root / "agent.yaml", name)
        kept = store.delete(name, keep_sessions=request.get("keepSessions", True) is True)
        return {"deleted": name, "retainedSessions": str(kept) if kept else None}
    if operation.startswith("skills.") or operation.startswith("packages."):
        name = required_text(request, "agent")
        root = preflight_agent_mutation(paths, name)
        reject_symlinks(root / "skills")
        reject_symlinks(root / "packages")
        if operation.endswith(".remove") or request.get("force") is True:
            confirmation(request)
        backup_file(paths, root / "agent.yaml", name)
        if operation.startswith("skills."):
            from mantle.skills import SkillManager
            manager = SkillManager(paths)
            if operation == "skills.add":
                return manager.add(required_text(request, "source"), request.get("skill") or None,
                                   name, force=request.get("force") is True).model_dump(mode="json")
            if operation == "skills.remove":
                manager.remove(required_text(request, "name"), name)
                return {"removed": request["name"]}
        else:
            from mantle.packages import PackageManager
            manager = PackageManager(paths)
            if operation == "packages.add":
                return manager.add(required_text(request, "source"), name, name=request.get("name") or None,
                                   force=request.get("force") is True).model_dump(mode="json")
            if operation == "packages.remove":
                return manager.remove(required_text(request, "name"), name).model_dump(mode="json")
    if operation.startswith("runtime."):
        confirmation(request)
        reject_symlinks(paths.runtimes)
        reject_symlinks(paths.config)
        version = str(request.get("version") or "latest").removeprefix("pi@")
        RuntimeManager.validate_version(version)
        reject_symlinks(paths.runtimes / version)
        manager = RuntimeManager(paths)
        backup_file(paths, paths.config, "settings")
        if operation == "runtime.install":
            metadata = manager.install(version)
            if request.get("makeDefault", version == "latest"):
                manager.set_default(metadata.version)
            return metadata.model_dump(mode="json")
        if operation == "runtime.use":
            metadata = manager.ensure(version) if version != "latest" else manager.install()
            manager.set_default(metadata.version)
            return metadata.model_dump(mode="json")
        if operation == "runtime.remove":
            manager.remove(version)
            return {"removed": version}
    if operation.startswith("auth."):
        confirmation(request)
        credentials = CredentialStore(paths)
        reject_symlinks(credentials.path)
        if operation in {"auth.saveKey", "auth.logout"}:
            provider = required_text(request, "provider")
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", provider):
                raise MantleError("Enter a valid provider ID.")
            backup_file(paths, credentials.path, "credentials")
            if operation == "auth.saveKey":
                credentials.set_api_key(provider, required_text(request, "key"))
                return {"provider": provider, "saved": True}
            return {"provider": provider, "removed": credentials.remove(provider)}
        name = required_text(request, "agent")
        root = agent_root(paths, name)
        store.require_local(name)
        from mantle.credentials import auth_file_problem
        problem = auth_file_problem(paths, root)
        if problem:
            raise MantleError(problem)
        if not (root / "auth.json").is_symlink():
            backup_file(paths, root / "auth.json", f"credentials-{name}")
        if operation == "auth.isolate":
            isolate_agent(paths, root, copy_credentials=request.get("copyCredentials") is True)
            return {"agent": name, "isolated": True}
        if operation == "auth.share":
            result = share_agent(paths, root)
            return {"agent": name, "isolated": False, "notices": list(result.notices)}
    if operation in {"secrets.set", "secrets.remove"}:
        confirmation(request)
        reject_symlinks(paths.dotenv)
        name = required_text(request, "name")
        backup_file(paths, paths.dotenv, "secrets")
        if operation == "secrets.set":
            set_dotenv_value(paths.dotenv, name, required_text(request, "value"))
            return {"name": name, "saved": True}
        return {"name": name, "removed": remove_dotenv_value(paths.dotenv, name)}
    raise MantleError(f"Unknown action: {operation}")


def clean_error(error: Exception, request: dict[str, Any]) -> str:
    if isinstance(error, ValidationError):
        message = format_validation_error(error)
    else:
        message = str(error) or type(error).__name__
    for key in ("key", "value", "registryToken"):
        value = request.get(key)
        if isinstance(value, str) and value:
            message = message.replace(value, "[redacted]")
    # Raw provider/registry responses are never allowed to echo authorization headers.
    message = re.sub(r"(?i)(Bearer\s+)[^\s\"']+", r"\1[redacted]", message)
    return message[:8000]


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        install_cli_preflight()
        from mantle.cli import app
        app(args=sys.argv[2:], prog_name="mantle")
        return
    request: dict[str, Any] = {}
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise MantleError("The request is too large.")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise MantleError("The request must be a JSON object.")
        request = parsed
        os.environ["MANTLE_QUIET"] = "1"
        with contextlib.redirect_stdout(sys.stderr):
            result = dispatch(request)
        response = {"ok": True, "result": result}
    except Exception as error:
        response = {"ok": False, "error": clean_error(error, request)}
    sys.stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
