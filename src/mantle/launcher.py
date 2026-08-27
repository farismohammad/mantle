from __future__ import annotations

import glob
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from mantle.agents import AgentStore, normalize_models_config
from mantle.errors import MantleError
from mantle.logging import append_event
from mantle.mcp import MCP_ADAPTER, remove_mcp_bridge, write_mcp_bridge
from mantle.models import ResourceDefinition
from mantle.packages import PackageManager
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
        del new_session  # Starting fresh is Pi's default behavior.
        config = AgentStore(self.paths).get(agent_name)
        runtime = RuntimeManager(self.paths).ensure(config.runtime.version)
        executable = RuntimeManager(self.paths).executable(runtime.version)
        root = resolve_project_root(current, project_root)
        agent_root = self.paths.agent(agent_name)
        normalize_models_config(agent_root)
        sessions = agent_root / "sessions" / project_id(root)
        sessions.mkdir(parents=True, exist_ok=True)
        command = [
            str(executable),
            "--no-context-files",
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
            write_mcp_bridge(agent_root, config, adapter_root)
        else:
            remove_mcp_bridge(agent_root)
        command += self._explicit_resources(agent_root, config.resources.packages)
        excluded_tools = []
        if config.permissions.shell == "none":
            excluded_tools.append("bash")
        if config.permissions.filesystem == "read":
            excluded_tools.extend(("edit", "write"))
        elif config.permissions.filesystem == "none":
            excluded_tools.extend(("read", "edit", "write"))
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
                    command += ["--append-system-prompt", path.read_text(encoding="utf-8")]
                except OSError as error:
                    raise MantleError(f"Could not read context file {path}: {error}") from error
        if session:
            command += ["--session", session]
        command += pi_arguments or []
        environment = os.environ.copy()
        environment.update(declared_secret_environment(self.paths.dotenv, config.secrets))
        environment.update({
            "PI_CODING_AGENT_DIR": str(agent_root),
            "PI_CODING_AGENT_SESSION_DIR": str(sessions),
            "PI_TELEMETRY": "0",
            "PI_SKIP_VERSION_CHECK": "1",
        })
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
                    command += [flag, str(path)]
        for package in packages:
            package_root = cls._find_package_root(agent_root, package)
            if package_root is None:
                raise MantleError(
                    f'Installed package "{package.name}" is missing. Run: mantle update {agent_root.name}'
                )
            command += cls._package_resources(package_root, mappings)
        return command

    @staticmethod
    def _find_package_root(agent_root: Path, package: ResourceDefinition) -> Path | None:
        candidates: list[Path] = []
        if package.source.startswith("npm:"):
            name = package.name
            candidates.extend((
                agent_root / "npm" / name,
                agent_root / "npm" / "node_modules" / name,
                agent_root / "pi" / "npm" / name,
                agent_root / "pi" / "npm" / "node_modules" / name,
            ))
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        git_root = agent_root / "git"
        if git_root.is_dir():
            for manifest in git_root.rglob("package.json"):
                if "node_modules" in manifest.parts:
                    continue
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if data.get("name") == package.name or manifest.parent.name == package.name:
                    return manifest.parent
        return None

    @staticmethod
    def _package_resources(package_root: Path, mappings: Iterable[tuple[str, str]]) -> list[str]:
        manifest_path = package_root / "package.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        except (OSError, ValueError) as error:
            raise MantleError(f"Invalid package manifest at {manifest_path}: {error}") from error
        declared = manifest.get("pi", {})
        command: list[str] = []
        for resource_type, flag in mappings:
            patterns = declared.get(resource_type)
            if patterns is None:
                conventional = package_root / resource_type
                patterns = [resource_type] if conventional.exists() else []
            if isinstance(patterns, str):
                patterns = [patterns]
            exclusions = [value[1:] for value in patterns if isinstance(value, str) and value.startswith("!")]
            for pattern in patterns:
                if not isinstance(pattern, str) or pattern.startswith(("!", "-")):
                    continue
                pattern = pattern[1:] if pattern.startswith("+") else pattern
                for matched in sorted(glob.glob(str(package_root / pattern), recursive=True)):
                    relative = str(Path(matched).relative_to(package_root))
                    if any(Path(relative).match(exclusion) for exclusion in exclusions):
                        continue
                    command += [flag, matched]
        return command

    def run(self, spec: LaunchSpec) -> int:
        result = subprocess.run(spec.command, cwd=spec.cwd, env=spec.environment, check=False)
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
        return result.returncode
