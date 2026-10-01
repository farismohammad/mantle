from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from mantle.agents import AgentStore
from mantle.environment import without_pi_overrides
from mantle.errors import ConflictError, MantleError, NotFoundError
from mantle.models import AgentConfig, ResourceDefinition
from mantle.paths import MantlePaths
from mantle.processes import run_installer
from mantle.storage import resource_transaction


class DirectSkillProvider:
    """Install one named skill from a user-provided upstream source."""

    def install(self, source: str, skill: str, destination: Path) -> None:
        with tempfile.TemporaryDirectory(prefix="mantle-skill-") as temporary:
            work = Path(temporary)
            environment = without_pi_overrides(os.environ.copy())
            environment["DISABLE_TELEMETRY"] = "1"
            result = run_installer(
                [
                    "npx", "--yes", "skills@1.7.0", "add", source, "--skill", skill,
                    "--agent", "pi", "--copy", "--yes",
                ],
                cwd=work,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
            )
            candidates = (
                work / ".pi" / "skills" / skill,
                work / ".agents" / "skills" / skill,
            )
            installed = next((path for path in candidates if (path / "SKILL.md").is_file()), None)
            if result.returncode != 0 or installed is None:
                detail = result.stderr.strip() or result.stdout.strip() or "the skills CLI did not create SKILL.md"
                raise MantleError(f'Could not install skill "{skill}" from {source}: {detail}')
            self._validate_tree(installed, work)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(installed, destination)

    @staticmethod
    def _validate_tree(path: Path, root: Path) -> None:
        pending = [path]
        visited: set[Path] = set()
        while pending:
            current = pending.pop()
            try:
                inside = current.resolve().is_relative_to(root.resolve())
            except (OSError, RuntimeError) as error:
                raise MantleError(f"Could not validate skill path {current}: {error}") from error
            if not inside:
                raise MantleError(f"Skill resource {current} points outside its isolated directory {root}.")
            resolved = current.resolve()
            if resolved in visited:
                continue
            visited.add(resolved)
            if current.is_dir():
                pending.extend(current.iterdir())


class SkillManager:
    def __init__(self, paths: MantlePaths, provider: DirectSkillProvider | None = None):
        self.paths = paths
        self.provider = provider or DirectSkillProvider()
        self.agents = AgentStore(paths)

    def install_reference(self, reference: ResourceDefinition, root: Path) -> None:
        if reference.source.startswith("-"):
            raise MantleError("Skill source must not begin with an option marker.")
        target = root / "skills" / reference.name
        staged = root / "tmp" / f"skill-{reference.name}"
        if staged.exists():
            shutil.rmtree(staged)
        try:
            self.provider.install(reference.source, reference.name, staged)
            if not (staged / "SKILL.md").is_file():
                raise MantleError(f'Skill "{reference.name}" did not contain SKILL.md.')
            DirectSkillProvider._validate_tree(staged, root)
            DirectSkillProvider._validate_tree(target, root)
            if target.exists():
                shutil.rmtree(target)
            staged.rename(target)
        finally:
            if staged.exists():
                shutil.rmtree(staged)

    def add(self, source: str, skill: str, agent_name: str, *, force: bool = False) -> ResourceDefinition:
        config = self.agents.get(agent_name)
        existing = next((item for item in config.resources.skills if item.name == skill), None)
        if existing and not force:
            raise ConflictError(f'Skill "{skill}" is already installed for agent "{agent_name}".')
        record = ResourceDefinition(name=skill, source=source)
        config.resources.skills = [item for item in config.resources.skills if item.name != skill] + [record]
        AgentConfig.model_validate(config.model_dump())
        root = self.paths.agent(agent_name)
        with resource_transaction(root, ("skills", "agent.yaml")):
            self.install_reference(record, root)
            self.agents.save(config)
        return record

    def remove(self, skill: str, agent_name: str) -> None:
        config = self.agents.get(agent_name)
        record = next((item for item in config.resources.skills if item.name == skill), None)
        if not record:
            raise NotFoundError(f'Skill "{skill}" is not installed for agent "{agent_name}".')
        target = self.paths.agent(agent_name) / "skills" / skill
        backup = self.paths.agent(agent_name) / "tmp" / f"removed-skill-{skill}"
        DirectSkillProvider._validate_tree(target, self.paths.agent(agent_name))
        DirectSkillProvider._validate_tree(backup, self.paths.agent(agent_name))
        if backup.exists():
            shutil.rmtree(backup)
        backup.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.rename(backup)
        try:
            config.resources.skills.remove(record)
            self.agents.save(config)
        except BaseException:
            if backup.exists():
                backup.rename(target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
