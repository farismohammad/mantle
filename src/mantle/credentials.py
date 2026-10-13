"""Provider credentials shared by every agent.

The store is `MANTLE_HOME/auth/auth.json`, in Pi's own auth.json format
(`{"provider": {"type": "api_key", "key": ...}}` or OAuth tokens). Each agent's
`auth.json` is a relative symlink to it, so Pi reads one credential set in every
agent. Pi writes the file in place under a lock and re-checks OAuth expiry under
that lock before refreshing, so a token refreshed by one agent is picked up by
the others instead of being refreshed again.

An agent can opt out with `mantle auth isolate AGENT`; it then keeps a private
`auth.json` exactly as before shared authentication existed, and its declared
secrets no longer fall back to shared API keys either.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mantle.errors import MantleError
from mantle.paths import MantlePaths
from mantle.providers import COMMON, Provider, lookup
from mantle.storage import atomic_write_text

ISOLATED_MARKER = ".mantle-auth-isolated"
PRE_SHARED_BACKUP = "auth.json.pre-shared"
INVALID_BACKUP = "auth.json.invalid"
_LOCK_STALE_SECONDS = 30


def shared_path(paths: MantlePaths) -> Path:
    return paths.home / "auth" / "auth.json"


def _validate(data: Any, source: Path) -> dict[str, dict[str, Any]]:
    if not isinstance(data, dict):
        raise MantleError(f"Invalid credentials file {source}: expected a JSON object.")
    for provider, credential in data.items():
        if not isinstance(credential, dict) or credential.get("type") not in {"api_key", "oauth"}:
            raise MantleError(f'Invalid credentials file {source}: entry "{provider}" is not a Pi credential.')
    return data


def _read(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8").strip()
        data = json.loads(text) if text else {}
    except (OSError, ValueError) as error:
        raise MantleError(f"Could not read credentials file {path}: {error}") from error
    return _validate(data, path)


class CredentialStore:
    def __init__(self, paths: MantlePaths):
        self.paths = paths
        self.path = shared_path(paths)

    def ensure(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        if not self.path.exists():
            atomic_write_text(self.path, "{}\n", mode=0o600)

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Use Pi's lock convention (a `<file>.lock` directory) for Mantle's own writes."""
        self.ensure()
        lock = Path(f"{self.path}.lock")
        deadline = time.monotonic() + 10
        while True:
            try:
                lock.mkdir()
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > _LOCK_STALE_SECONDS:
                        lock.rmdir()
                        continue
                except OSError:
                    continue
                if time.monotonic() > deadline:
                    raise MantleError(f"Credentials are locked by another process ({lock}). Retry in a moment.")
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                lock.rmdir()
            except OSError:
                pass

    def load(self) -> dict[str, dict[str, Any]]:
        return _read(self.path)

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        # Write in place like Pi does, so open handles and symlinks see the change.
        self.ensure()
        descriptor = os.open(self.path, os.O_WRONLY | os.O_TRUNC | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(self.path, 0o600)

    def get(self, provider: str) -> dict[str, Any] | None:
        return self.load().get(provider)

    def set_api_key(self, provider: str, key: str) -> None:
        if not key or key != key.strip() or any(ord(character) < 32 for character in key):
            raise MantleError("API keys must be a single line without surrounding whitespace.")
        with self.lock():
            data = self.load()
            data[provider] = {"type": "api_key", "key": key}
            self._write(data)

    def merge(self, entries: dict[str, dict[str, Any]]) -> None:
        with self.lock():
            data = self.load()
            data.update(entries)
            self._write(data)

    def remove(self, provider: str) -> bool:
        with self.lock():
            data = self.load()
            if provider not in data:
                return False
            del data[provider]
            self._write(data)
            return True


def mask(key: str) -> str:
    if key.startswith("!"):
        return "command: " + key[1:].split()[0] if key[1:].strip() else "command"
    return "…" + key[-4:] if len(key) > 8 else "set"


def describe(credential: dict[str, Any]) -> str:
    if credential.get("type") == "api_key":
        key = credential.get("key")
        return f"API key {mask(key)}" if isinstance(key, str) and key else "API key"
    expires = credential.get("expires")
    if isinstance(expires, (int, float)):
        remaining = expires / 1000 - time.time()
        when = datetime.fromtimestamp(expires / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if remaining > 0:
            return f"OAuth login (token valid until {when}; refreshed automatically)"
        return "OAuth login (token expired; refreshed on next use)"
    return "OAuth login"


# Agent linking ---------------------------------------------------------------

@dataclass(frozen=True)
class LinkResult:
    mode: str  # "shared", "isolated" or "private" (unreadable private file left alone)
    notices: tuple[str, ...] = ()


def is_isolated(agent_root: Path) -> bool:
    return (agent_root / ISOLATED_MARKER).exists()


def is_shared_link(paths: MantlePaths, agent_root: Path) -> bool:
    target = agent_root / "auth.json"
    if not target.is_symlink():
        return False
    try:
        return target.resolve() == shared_path(paths).resolve()
    except OSError:
        return False


def _relative_link(paths: MantlePaths, agent_root: Path) -> str:
    return os.path.relpath(shared_path(paths), agent_root)


def link_agent(paths: MantlePaths, agent_root: Path) -> LinkResult:
    """Point an agent's auth.json at the shared store, importing any private logins."""
    target = agent_root / "auth.json"
    if is_isolated(agent_root):
        if target.is_symlink():
            raise MantleError(f"Resource {target} points outside its isolated directory {agent_root}.")
        return LinkResult("isolated")
    store = CredentialStore(paths)
    store.ensure()
    if target.is_symlink():
        if is_shared_link(paths, agent_root):
            return LinkResult("shared")
        raise MantleError(f"Resource {target} points outside its isolated directory {agent_root}.")
    notices: list[str] = []
    if target.exists():
        try:
            private = _read(target)
        except MantleError:
            return LinkResult("private", (
                f"{target} is not a valid Pi credentials file, so this agent keeps it and does not use "
                f"shared provider logins. Repair it, or set it aside and share logins with: mantle auth share {agent_root.name}",
            ))
        if private:
            with store.lock():
                shared = store.load()
                imported = [provider for provider in private if provider not in shared]
                conflicts = [provider for provider in private if provider in shared and private[provider] != shared[provider]]
                if imported:
                    shared.update({provider: private[provider] for provider in imported})
                    store._write(shared)
            if conflicts:
                backup = agent_root / PRE_SHARED_BACKUP
                shutil.copy2(target, backup)
                os.chmod(backup, 0o600)
                notices.append(
                    f"{agent_root.name} had its own login for {', '.join(conflicts)}; the shared login is used now. "
                    f"The agent's previous credentials were saved to {backup}."
                )
            if imported:
                notices.append(
                    f"Moved {agent_root.name}'s existing login for {', '.join(imported)} into Mantle's shared "
                    "credentials; every agent can use it now."
                )
    temporary = agent_root / ".auth.json.link"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(_relative_link(paths, agent_root))
    os.replace(temporary, target)
    return LinkResult("shared", tuple(notices))


def isolate_agent(paths: MantlePaths, agent_root: Path, *, copy_credentials: bool) -> None:
    target = agent_root / "auth.json"
    data = CredentialStore(paths).load() if copy_credentials else {}
    if target.is_symlink():
        target.unlink()
    elif target.exists() and not copy_credentials:
        data = _read(target)
    atomic_write_text(target, json.dumps(data, indent=2) + "\n", mode=0o600)
    atomic_write_text(agent_root / ISOLATED_MARKER, "This agent uses its own auth.json instead of Mantle's shared provider logins.\n", mode=0o644)


def share_agent(paths: MantlePaths, agent_root: Path) -> LinkResult:
    (agent_root / ISOLATED_MARKER).unlink(missing_ok=True)
    target = agent_root / "auth.json"
    notices: tuple[str, ...] = ()
    if target.exists() and not target.is_symlink():
        try:
            _read(target)
        except MantleError:
            # Asked to share explicitly: set the unreadable file aside instead of keeping it.
            backup = agent_root / INVALID_BACKUP
            os.replace(target, backup)
            os.chmod(backup, 0o600)
            notices = (f"{target} was not a valid Pi credentials file; it was moved to {backup}.",)
    result = link_agent(paths, agent_root)
    return LinkResult(result.mode, notices + result.notices)


def auth_file_problem(paths: MantlePaths, agent_root: Path) -> str | None:
    """Why Pi cannot read this agent's logins, if it cannot (see `link_agent`)."""
    target = agent_root / "auth.json"
    if target.is_symlink():
        if is_isolated(agent_root) or not is_shared_link(paths, agent_root):
            return f"{target} links outside the agent instead of to Mantle's shared logins"
        return None
    if not target.exists():
        return None
    try:
        _read(target)
    except MantleError:
        return f"{target} is not a valid Pi credentials file"
    return None


def agent_logins(paths: MantlePaths, agent_root: Path | None) -> dict[str, dict[str, Any]]:
    """The logins Pi can use in this agent: shared ones, or the agent's own when isolated."""
    if agent_root is not None and auth_file_problem(paths, agent_root):
        return {}
    try:
        shared = CredentialStore(paths).load()
    except MantleError:
        shared = {}
    target = agent_root / "auth.json" if agent_root is not None else None
    if target is None or target.is_symlink() or not target.exists():
        return {} if agent_root is not None and is_isolated(agent_root) else shared
    private = _read(target)
    if is_isolated(agent_root):
        return private
    # A private file is merged into the shared store on the next run; the shared login wins.
    return {**private, **shared}


def provider_credential(paths: MantlePaths, agent_root: Path | None, provider: str) -> dict[str, Any] | None:
    """The stored credential Pi would use for `provider` in this agent, if any."""
    return agent_logins(paths, agent_root).get(provider)


def preferred_provider(paths: MantlePaths) -> str:
    """The provider new agents use by default: OpenAI, unless only other providers are signed in."""
    try:
        signed_in = CredentialStore(paths).load()
    except MantleError:
        signed_in = {}
    if not signed_in or "openai" in signed_in:
        return "openai"
    return _usable_alternative(set(signed_in), exclude="openai") or "openai"


def alternative_provider(paths: MantlePaths, agent_root: Path | None, provider: str) -> str | None:
    """A provider this agent is already signed in to that it could switch to, if any."""
    return _usable_alternative(set(agent_logins(paths, agent_root)), exclude=provider)


def switch_reason(provider: str, alternative: str) -> str:
    """Why a login for `alternative` does not already cover an agent set to `provider`."""
    if provider == "openai" and alternative == "openai-codex":
        return "a ChatGPT subscription login (openai-codex) is a separate provider from openai (API keys)"
    return "a login only applies to its own provider"


def login_step(agent_root: Path, provider: Provider, secrets: list[str]) -> tuple[str, str]:
    """The command that gives this agent credentials for `provider`, and why.

    `mantle auth login` saves a shared login, which an isolated agent never reads: it signs in
    inside Pi instead, or gets a provider key variable it declares in `secrets` from `.env`.
    """
    if not is_isolated(agent_root):
        return f"mantle auth login {provider.id}", f"sign in to {provider.name} once; every agent shares it"
    name = agent_root.name
    declared = [variable for variable in provider.env if variable in secrets]
    if declared:
        return f"mantle auth secrets set {declared[-1]}", f"save the key in .env; {name} does not use the shared logins"
    return f"mantle agent run {name}, then sign in with /login", f"{name} does not use the shared logins"


def _usable_alternative(signed_in: set[str], exclude: str) -> str | None:
    # A ChatGPT subscription login (openai-codex) is the usual stand-in for OpenAI API keys.
    preferred = ("openai-codex",) if exclude == "openai" else ()
    ranked = [*preferred, *COMMON, *sorted(signed_in)]
    return next((provider for provider in ranked
                 if provider in signed_in and provider != exclude and (known := lookup(provider)) and known.default_model), None)


def shared_api_key_environment(paths: MantlePaths, agent_root: Path | None) -> dict[str, str]:
    """Shared API keys as their provider variables, for agents that declare them in `secrets`.

    An isolated agent gets none, so its declared secrets come only from the shell and `.env`.
    Without an agent, every shared key is returned.
    """
    if agent_root is not None and is_isolated(agent_root):
        return {}
    try:
        data = CredentialStore(paths).load()
    except MantleError:
        return {}
    environment: dict[str, str] = {}
    for provider_id, credential in data.items():
        provider = lookup(provider_id)
        key = credential.get("key")
        if provider and provider.primary_env and credential.get("type") == "api_key" and isinstance(key, str) and key and not key.startswith("!"):
            environment[provider.primary_env] = key
    return environment
