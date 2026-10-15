from __future__ import annotations

import glob
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from mantle.agents import AgentStore, normalize_models_config
from mantle.errors import MantleError
from mantle.environment import pi_launch_environment
from mantle.logging import append_event
from mantle.mcp import MCP_ADAPTER, MCP_CONFIG_FILENAME, MCP_EXTENSION_FILENAME, remove_mcp_bridge, write_mcp_bridge
from mantle.models import ResourceDefinition
from mantle.packages import PackageManager, git_package_path, package_name_from_source
from mantle.paths import MantlePaths
from mantle.projects import project_id, resolve_project_root
from mantle.runtimes import RuntimeManager
from mantle.secrets import declared_secret_environment


@dataclass(frozen=True)
class LaunchSpec:
    command: list[str]
    environment: dict[str, str]
    cwd: Path
    session_dir: Path
    agent_name: str
    runtime_version: str


class AgentLauncher:
    def __init__(self, paths: MantlePaths):
        self.paths = paths

    def build(
        self,
        agent_name: str,
        *,
        current: Path,
        project_root: Path | None = None,
        project_context: bool = True,
        session: str | None = None,
        new_session: bool = False,
        pi_arguments: list[str] | None = None,
    ) -> LaunchSpec:
        agent_root = self.paths.agent(agent_name)
        for filename in (
            "agent.yaml", "settings.json", "auth.json", "models.json",
            "extensions", MCP_CONFIG_FILENAME, f"extensions/{MCP_EXTENSION_FILENAME}",
        ):
            self._within(agent_root / filename, agent_root)
        config = AgentStore(self.paths).get(agent_name)
        root = resolve_project_root(current, project_root)
        if new_session and any(arg in {"--continue", "-c", "--resume", "-r", "--session-id"} for arg in pi_arguments or []):
            raise MantleError("--new-session cannot be combined with Pi session-resume options.")
        protected = {"--session-dir", "--extension", "-e", "--skill", "--prompt-template", "--theme", "--tools", "-t", "--exclude-tools", "-xt", "--session", "--fork", "--approve", "-a"}
        for argument in pi_arguments or []:
            if argument.split("=", 1)[0] in protected or (argument.startswith("-e") and not argument.startswith("--")):
                raise MantleError(f"Pi argument {argument!r} would override Mantle isolation. Configure agent.yaml or use Mantle's --session option.")
        if pi_arguments and pi_arguments[0] in {"install", "remove", "update", "config", "list"}:
            raise MantleError("Use Mantle's resource commands to manage this agent's packages.")
        secrets = declared_secret_environment(self.paths.dotenv, config.secrets)
        for skill in config.resources.skills:
            if not (agent_root / "skills" / skill.name / "SKILL.md").is_file():
                raise MantleError(f'Skill "{skill.name}" is missing. Reinstall it with: mantle skills add {skill.source} --skill {skill.name} --agent {agent_name} --force')
        runtime = RuntimeManager(self.paths).ensure(config.runtime.version)
        executable = RuntimeManager(self.paths).executable(runtime.version)
        normalize_models_config(agent_root)
        sessions = agent_root / "sessions" / project_id(root)
        self._within(sessions, agent_root)
        sessions.mkdir(parents=True, exist_ok=True)
        self._within(sessions, agent_root)
        command = [
            str(executable),
            "--no-context-files",
            "--no-approve",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--session-dir", str(sessions),
            "--provider", config.model.provider,
            "--model", config.model.name,
        ]
        if config.mcp.servers:
            adapter_root = self._find_package_root(agent_root, MCP_ADAPTER)
            if adapter_root is None:
                PackageManager(self.paths).install_reference(MCP_ADAPTER, agent_root, runtime.version)
                adapter_root = self._find_package_root(agent_root, MCP_ADAPTER)
            if adapter_root is None:
                raise MantleError(
                    f'MCP adapter installation is missing for agent "{agent_name}". Run: mantle update {agent_name}'
                )
            self._validate_tree(adapter_root, agent_root)
            write_mcp_bridge(agent_root, config, adapter_root)
        else:
            remove_mcp_bridge(agent_root)
        command += self._explicit_resources(agent_root, config.resources.packages)
        excluded_tools = []
        if config.permissions.shell == "none":
            excluded_tools.extend(("bash", "powershell"))
        if config.permissions.filesystem == "read":
            excluded_tools.extend(("edit", "write"))
        elif config.permissions.filesystem == "none":
            excluded_tools.extend(("read", "edit", "write", "grep", "find", "ls"))
        if excluded_tools:
            command += ["--exclude-tools", ",".join(excluded_tools)]
        if config.system_prompt:
            command += ["--append-system-prompt", config.system_prompt]
        instruction_files = sorted((agent_root / "context").glob("*.md"))
        if project_context:
            instruction_files += [root / "AGENTS.md", root / "CLAUDE.md"]
        for path in instruction_files:
            if path.is_file():
                try:
                    self._within(path, agent_root if path.parent == agent_root / "context" else root)
                    command += ["--append-system-prompt", path.read_text(encoding="utf-8")]
                except OSError as error:
                    raise MantleError(f"Could not read context file {path}: {error}") from error
        if session:
            session_path = Path(session).expanduser()
            if session_path.is_absolute() or "/" in session or "\\" in session or session.endswith(".jsonl"):
                session_path = (current / session_path).resolve()
                if not session_path.is_relative_to(sessions.resolve()) or not session_path.is_file():
                    raise MantleError(f"Session file must exist inside this agent's project session directory: {sessions}")
                session = str(session_path)
            command += ["--session", session]
        command += pi_arguments or []
        environment = pi_launch_environment(agent_root, sessions, secrets)
        return LaunchSpec(command, environment, root, sessions, agent_name, runtime.version)

    @classmethod
    def _explicit_resources(cls, agent_root: Path, packages: list[ResourceDefinition]) -> list[str]:
        command: list[str] = []
        mappings = (
            ("skills", "--skill"),
            ("extensions", "--extension"),
            ("prompts", "--prompt-template"),
            ("themes", "--theme"),
        )
        for directory, flag in mappings:
            root = agent_root / directory
            if root.is_dir():
                for path in sorted(root.iterdir()):
                    cls._validate_tree(path, agent_root)
                    command += [flag, str(path)]
        for package in packages:
            package_root = cls._find_package_root(agent_root, package)
            if package_root is None:
                raise MantleError(
                    f'Installed package "{package.name}" is missing. Reinstall it with: mantle add-package {package.source} {agent_root.name} --name {package.name} --force'
                )
            command += cls._package_resources(package_root, mappings)
        return command

    @staticmethod
    def _find_package_root(agent_root: Path, package: ResourceDefinition) -> Path | None:
        candidates: list[Path] = []
        if package.source.startswith("npm:"):
            name = package_name_from_source(package.source)
            candidates.extend((
                agent_root / "npm" / name,
                agent_root / "npm" / "node_modules" / name,
                agent_root / "pi" / "npm" / name,
                agent_root / "pi" / "npm" / "node_modules" / name,
            ))
        else:
            candidates.append(agent_root / "git" / git_package_path(package.source))
        for candidate in candidates:
            if candidate.is_dir():
                AgentLauncher._within(candidate, agent_root)
                return candidate
        return None

    @staticmethod
    def _package_resources(package_root: Path, mappings: Iterable[tuple[str, str]]) -> list[str]:
        manifest_path = package_root / "package.json"
        AgentLauncher._within(manifest_path, package_root)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        except (OSError, ValueError) as error:
            raise MantleError(f"Invalid package manifest at {manifest_path}: {error}") from error
        if not isinstance(manifest, dict):
            raise MantleError(f"Invalid package manifest at {manifest_path}: expected an object.")
        declared = manifest.get("pi", {})
        if not isinstance(declared, dict):
            raise MantleError(f"Invalid Pi resources in {manifest_path}: pi must be an object.")
        command: list[str] = []
        for resource_type, flag in mappings:
            patterns = declared.get(resource_type)
            if patterns is None:
                conventional = package_root / resource_type
                patterns = [resource_type] if "pi" not in manifest and conventional.exists() else []
            if isinstance(patterns, str):
                patterns = [patterns]
            if not isinstance(patterns, list) or not all(isinstance(value, str) for value in patterns):
                raise MantleError(f"Invalid {resource_type} in {manifest_path}: expected a list of paths.")
            exclusions = [value[1:] for value in patterns if isinstance(value, str) and value.startswith("!")]
            for pattern in patterns:
                if not isinstance(pattern, str) or pattern.startswith(("!", "-")):
                    continue
                pattern = pattern[1:] if pattern.startswith("+") else pattern
                if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                    raise MantleError(f"Package resource paths must stay inside {package_root}: {pattern}")
                for matched in sorted(glob.glob(str(package_root / pattern), recursive=True)):
                    AgentLauncher._validate_tree(Path(matched), package_root)
                    relative = str(Path(matched).relative_to(package_root))
                    if any(Path(relative).match(exclusion) for exclusion in exclusions):
                        continue
                    command += [flag, matched]
        return command

    @staticmethod
    def _within(path: Path, root: Path) -> None:
        try:
            inside = path.resolve().is_relative_to(root.resolve())
        except (OSError, RuntimeError) as error:
            raise MantleError(f"Could not validate resource path {path}: {error}") from error
        if not inside:
            raise MantleError(f"Resource {path} points outside its isolated directory {root}.")

    @classmethod
    def _validate_tree(cls, path: Path, root: Path) -> None:
        pending = [path]
        visited: set[Path] = set()
        while pending:
            current = pending.pop()
            cls._within(current, root)
            resolved = current.resolve()
            if resolved in visited:
                continue
            visited.add(resolved)
            if current.is_dir():
                pending.extend(current.iterdir())

    def run(self, spec: LaunchSpec) -> int:
        result = subprocess.run(spec.command, cwd=spec.cwd, env=spec.environment, check=False)
        try:
            append_event(
                self.paths.agent(spec.agent_name) / "logs" / "mantle.jsonl",
                {
                    "command": "run",
                    "agent": spec.agent_name,
                    "runtime": spec.runtime_version,
                    "project_root": str(spec.cwd),
                    "result": result.returncode,
                },
            )
        except (OSError, UnicodeError) as error:
            print(f"Warning: could not write the local run log: {error}", file=sys.stderr)
        return result.returncode if result.returncode >= 0 else 128 - result.returncode
