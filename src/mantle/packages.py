from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from mantle.agents import AgentStore
from mantle.environment import pi_operation_environment
from mantle.errors import ConflictError, MantleError, NotFoundError
from mantle.models import AgentConfig, ResourceDefinition
from mantle.paths import MantlePaths
from mantle.processes import run_installer
from mantle.runtimes import RuntimeManager
from mantle.sources import git_package_path, validate_package_source
from mantle.storage import resource_transaction


PACKAGE_STATE = ("npm", "git", "packages", "settings.json", "agent.yaml")


def package_name_from_source(source: str) -> str:
    value = source.strip()
    if not value or any(character in value for character in "\r\n\0"):
        raise MantleError("Package source must be a non-empty Pi package source on one line.")
    if value.startswith("npm:"):
        value = value[4:]
        if value.startswith("@"):
            scope, _, rest = value.partition("/")
            return f"{scope}/{rest.rsplit('@', 1)[0]}" if rest else scope
        return value.rsplit("@", 1)[0]
    value = value.removeprefix("git:")
    if value.startswith("git@"):
        value = value.split(":", 1)[-1]
    try:
        parsed = urlparse(value)
    except ValueError as error:
        raise MantleError(f"Invalid package source: {error}") from error
    path = (parsed.path if parsed.scheme else value).split("@", 1)[0]
    name = Path(path.rstrip("/")).name.removesuffix(".git")
    normalized = re.sub(r"[^A-Za-z0-9._@/-]+", "-", name).strip("-")
    if not normalized:
        raise MantleError("Could not derive a package name from the source; pass --name explicitly.")
    return normalized


class PackageManager:
    def __init__(self, paths: MantlePaths, runner=run_installer):
        self.paths = paths
        self.runner = runner
        self.agents = AgentStore(paths)

    def _pi(self, root: Path, runtime_version: str, arguments: list[str]) -> None:
        executable = RuntimeManager(self.paths).executable(runtime_version)
        environment = pi_operation_environment(root)
        result = self.runner(
            [str(executable), *arguments],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            cwd=root,
            timeout=300,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "Pi package operation failed"
            raise MantleError(detail)

    def install_reference(self, reference: ResourceDefinition, root: Path, runtime_version: str) -> None:
        validate_package_source(reference.source)
        self._pi(root, runtime_version, ["install", reference.source])

    def add(
        self,
        source: str,
        agent_name: str,
        *,
        name: str | None = None,
        force: bool = False,
    ) -> ResourceDefinition:
        config = self.agents.get(agent_name)
        selected_name = name or package_name_from_source(source)
        existing = next((item for item in config.resources.packages if item.name == selected_name), None)
        if existing and not force:
            raise ConflictError(f'Package "{selected_name}" is already installed for agent "{agent_name}".')
        record = ResourceDefinition(name=selected_name, source=source)
        validate_package_source(record.source)
        config.resources.packages = [item for item in config.resources.packages if item.name != selected_name] + [record]
        AgentConfig.model_validate(config.model_dump())
        runtime = RuntimeManager(self.paths).ensure(config.runtime.version)
        root = self.paths.agent(agent_name)
        with resource_transaction(root, PACKAGE_STATE):
            if existing and existing.source != source:
                self._pi(root, runtime.version, ["remove", existing.source])
            self.install_reference(record, root, runtime.version)
            self.agents.save(config)
        return record

    def remove(self, name: str, agent_name: str) -> ResourceDefinition:
        config = self.agents.get(agent_name)
        record = next((item for item in config.resources.packages if item.name == name), None)
        if not record:
            raise NotFoundError(f'Package "{name}" is not installed for agent "{agent_name}".')
        runtime = RuntimeManager(self.paths).ensure(config.runtime.version)
        with resource_transaction(self.paths.agent(agent_name), PACKAGE_STATE):
            self._pi(self.paths.agent(agent_name), runtime.version, ["remove", record.source])
            config.resources.packages.remove(record)
            self.agents.save(config)
        return record
