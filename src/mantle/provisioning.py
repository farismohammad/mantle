from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from mantle.agents import AgentStore, normalize_models_config, prepare_agent_root
from mantle.errors import ConflictError, MantleError, NotFoundError
from mantle.mcp import MCP_ADAPTER
from mantle.models import AgentConfig
from mantle.packages import PackageManager
from mantle.paths import MantlePaths
from mantle.runtimes import RuntimeManager
from mantle.skills import SkillManager
from mantle.storage import atomic_write_text


PRESERVED_DIRECTORIES = ("context", "sessions", "logs")
PRESERVED_FILES = ("auth.json", "models.json")


class Provisioner:
    def __init__(
        self,
        paths: MantlePaths,
        *,
        runtime_manager: RuntimeManager | None = None,
        skill_manager: SkillManager | None = None,
        package_manager: PackageManager | None = None,
    ):
        self.paths = paths
        self.runtimes = runtime_manager or RuntimeManager(paths)
        self.skills = skill_manager or SkillManager(paths)
        self.packages = package_manager or PackageManager(paths)
        self.agents = AgentStore(paths)

    def install(self, config: AgentConfig, *, yaml_text: str | None = None) -> None:
        name = config.agent.name
        if self.paths.agent(name).exists():
            raise ConflictError(f'Agent "{name}" is already installed locally. Use mantle update {name}.')
        self._replace(config, previous=None, yaml_text=yaml_text)

    def update(self, config: AgentConfig, *, yaml_text: str | None = None) -> None:
        name = config.agent.name
        if not self.paths.agent(name).is_dir():
            raise NotFoundError(f'Agent "{name}" is not installed locally. Use mantle install {name}.')
        self._replace(config, previous=self.paths.agent(name), yaml_text=yaml_text)

    def _replace(self, config: AgentConfig, previous: Path | None, yaml_text: str | None) -> None:
        name = config.agent.name
        self.paths.agents.mkdir(parents=True, exist_ok=True)
        staging = self.paths.agents / f".{name}.staging-{uuid.uuid4().hex}"
        backup = self.paths.agents / f".{name}.backup-{uuid.uuid4().hex}"
        target = self.paths.agent(name)
        previous_settings = self.paths.config.read_text(encoding="utf-8") if self.paths.config.is_file() else None
        try:
            prepare_agent_root(staging, config)
            if yaml_text is not None:
                atomic_write_text(staging / "agent.yaml", yaml_text, mode=0o644)
            runtime = self.runtimes.ensure(config.runtime.version, refresh_latest=config.runtime.version == "latest")
            for skill in config.resources.skills:
                self.skills.install_reference(skill, staging)
            for package in config.resources.packages:
                self.packages.install_reference(package, staging, runtime.version)
            if config.mcp.servers:
                self.packages.install_reference(MCP_ADAPTER, staging, runtime.version)
            if previous is not None:
                self._preserve_local_state(previous, staging)
                previous.rename(backup)
            staging.rename(target)
            if backup.exists():
                try:
                    shutil.rmtree(backup)
                except OSError:
                    # The new agent is already active; a stale hidden backup is safer than rollback.
                    pass
        except BaseException as error:
            if backup.exists() and not target.exists():
                backup.rename(target)
            if staging.exists():
                shutil.rmtree(staging)
            # Refreshing latest can select a shared default before resources
            # finish installing. A failed reconstruction must not switch other
            # agents to a different runtime.
            current_settings = self.paths.config.read_text(encoding="utf-8") if self.paths.config.is_file() else None
            if current_settings != previous_settings:
                if previous_settings is None:
                    self.paths.config.unlink(missing_ok=True)
                else:
                    atomic_write_text(self.paths.config, previous_settings)
            if isinstance(error, (MantleError, KeyboardInterrupt, SystemExit)):
                raise
            raise MantleError(f'Could not reconstruct agent "{name}": {error}') from error

    @staticmethod
    def _preserve_local_state(previous: Path, staging: Path) -> None:
        for name in PRESERVED_DIRECTORIES:
            source = previous / name
            if source.exists():
                shutil.copytree(source, staging / name, dirs_exist_ok=True)
        for name in PRESERVED_FILES:
            source = previous / name
            if source.is_file():
                shutil.copy2(source, staging / name)
        normalize_models_config(staging)
