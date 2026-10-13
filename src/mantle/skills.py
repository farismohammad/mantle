from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yaml

from mantle.agents import AgentStore
from mantle.environment import without_pi_overrides
from mantle.errors import ConflictError, MantleError, NotFoundError
from mantle.models import AgentConfig, ResourceDefinition
from mantle.paths import MantlePaths
from mantle.processes import run_installer
from mantle.progress import step
from mantle.storage import resource_transaction


SKILL_DIRECTORIES = (Path(".pi") / "skills", Path(".agents") / "skills")
_TERMINAL_CODES = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_STATUS_GLYPHS = "│┌└├◇◆■●▲○◒◐◓◑"


def skill_directory_name(name: str) -> str:
    """The directory the skills CLI installs a skill into (its sanitizeName)."""
    cleaned = re.sub(r"^[.\-]+|[.\-]+$", "", re.sub(r"[^a-z0-9._]+", "-", name.lower()))[:255]
    return cleaned or "unnamed-skill"


def frontmatter_name(skill_file: Path) -> str | None:
    """The `name` in a SKILL.md frontmatter, which is what skill installers match --skill against."""
    try:
        text = skill_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    match = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", text, re.DOTALL)
    try:
        data = yaml.safe_load(match.group(1)) if match else None
    except yaml.YAMLError:
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


def skill_directory_source(source: str) -> str:
    """The tree URL of the directory a GitHub blob URL points into.

    The skills CLI reads a blob URL as its whole repository, while a tree URL
    limits it to that directory's skill.
    """
    parsed = urlparse(source)
    if not parsed.scheme or "/blob/" not in parsed.path:
        return source
    path = parsed.path.replace("/blob/", "/tree/", 1).rstrip("/").removesuffix("/SKILL.md")
    return parsed._replace(path=path).geturl()


def absolute_local_source(source: str) -> str:
    """Local paths as an absolute path: the installer runs in a temporary directory, and
    agent.yaml must still point at the skill when it is reinstalled from elsewhere."""
    if source in (".", "..") or source.startswith(("./", "../", "~")) or Path(source).is_absolute():
        return str(Path(source).expanduser().resolve())
    return source


def installer_message(output: str) -> str:
    """The skills CLI's own messages, without its spinner animation and box drawing."""
    lines = []
    for line in output.split("\n"):
        # Spinner frames redraw the line; only the final frame was visible.
        line = re.split(r"\r|\x1b\[1G", line)[-1]
        line = _TERMINAL_CODES.sub("", line).strip().lstrip(_STATUS_GLYPHS).strip()
        if line:
            lines.append(line)
    return "\n  ".join(lines)


class DirectSkillProvider:
    """Install one named skill from a user-provided upstream source."""

    def install(self, source: str, skill: str | None, destination: Path) -> str:
        """Install a skill into `destination` and return its name.

        Without `skill`, SOURCE must hold exactly one skill, and that skill's own
        name (from its SKILL.md) is used.
        """
        with tempfile.TemporaryDirectory(prefix="mantle-skill-") as temporary:
            work = Path(temporary)
            environment = without_pi_overrides(os.environ.copy())
            environment["DISABLE_TELEMETRY"] = "1"
            label = f"skill {skill}" if skill else "skill"
            with step(f"Installing {label} from {source}…", f"Installed {label}"):
                result = run_installer(
                    [
                        "npx", "--yes", "skills@1.7.0", "add", source, "--skill", skill or "*",
                        "--agent", "pi", "--copy", "--yes",
                    ],
                    cwd=work,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
            if skill:
                candidates = [work / directory / name for directory in SKILL_DIRECTORIES
                              for name in dict.fromkeys((skill, skill_directory_name(skill)))]
                installed = next((path for path in candidates if (path / "SKILL.md").is_file()), None)
            else:
                found: dict[str, Path] = {}
                for directory in SKILL_DIRECTORIES:
                    if (work / directory).is_dir():
                        for path in sorted((work / directory).iterdir()):
                            if (path / "SKILL.md").is_file():
                                found.setdefault(path.name, path)
                if result.returncode == 0 and len(found) > 1:
                    raise MantleError(f"{source} contains {len(found)} skills: {', '.join(found)}. Choose one with --skill NAME.")
                installed = next(iter(found.values()), None)
            if result.returncode != 0 or installed is None:
                detail = (installer_message(result.stderr) or installer_message(result.stdout)
                          or "the skills CLI did not create SKILL.md")
                what = f'skill "{skill}"' if skill else "a skill"
                raise MantleError(f"Could not install {what} from {source}:\n  {detail}")
            self._validate_tree(installed, work)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(installed, destination)
            return skill or installed.name

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
        staged = root / "tmp" / f"skill-{reference.name}"
        if staged.exists():
            shutil.rmtree(staged)
        try:
            self.provider.install(reference.source, reference.name, staged)
            self._activate(staged, root, reference.name)
        finally:
            if staged.exists():
                shutil.rmtree(staged)

    @staticmethod
    def _activate(staged: Path, root: Path, name: str) -> None:
        target = root / "skills" / name
        if not (staged / "SKILL.md").is_file():
            raise MantleError(f'Skill "{name}" did not contain SKILL.md.')
        DirectSkillProvider._validate_tree(staged, root)
        DirectSkillProvider._validate_tree(target, root)
        if target.exists():
            shutil.rmtree(target)
        staged.rename(target)

    def add(self, source: str, skill: str | None, agent_name: str, *, force: bool = False) -> ResourceDefinition:
        """Install a skill into one agent and record it in agent.yaml.

        Without `skill`, SOURCE must hold exactly one skill, and it is recorded
        under its own name, which may differ from its directory's name.
        """
        config = self.agents.get(agent_name)
        root = self.paths.agent(agent_name)
        source = absolute_local_source(source)
        if skill is not None:
            record = self._record(config, skill, source, agent_name, force)
            with resource_transaction(root, ("skills", "agent.yaml")):
                self.install_reference(record, root)
                self.agents.save(config)
            return record
        ResourceDefinition(name="skill", source=source)  # Reject a malformed source before downloading.
        if source.startswith("-"):
            raise MantleError("Skill source must not begin with an option marker.")
        staged = root / "tmp" / "skill-from-source"
        if staged.exists():
            shutil.rmtree(staged)
        try:
            # Reinstalls name the skill, so the recorded source can stay as given.
            name = self.provider.install(skill_directory_source(source), None, staged)
            record = self._record(config, name, source, agent_name, force)
            with resource_transaction(root, ("skills", "agent.yaml")):
                self._activate(staged, root, name)
                self.agents.save(config)
            return record
        finally:
            if staged.exists():
                shutil.rmtree(staged)

    @staticmethod
    def _record(config: AgentConfig, skill: str, source: str, agent_name: str, force: bool) -> ResourceDefinition:
        existing = next((item for item in config.resources.skills if item.name == skill), None)
        if existing and not force:
            raise ConflictError(f'Skill "{skill}" is already installed for agent "{agent_name}".')
        record = ResourceDefinition(name=skill, source=source)
        config.resources.skills = [item for item in config.resources.skills if item.name != skill] + [record]
        AgentConfig.model_validate(config.model_dump())
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
