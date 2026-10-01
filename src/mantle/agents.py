from __future__ import annotations

import re
import shutil
from pathlib import Path

from mantle.configuration import load_agent_yaml, write_agent_yaml
from mantle.errors import ConflictError, MantleError, NotFoundError
from mantle.models import AGENT_NAME_PATTERN, AgentConfig, default_agent_config
from mantle.paths import MantlePaths
from mantle.storage import read_json, write_json


AGENT_NAME = re.compile(AGENT_NAME_PATTERN)
AGENT_DIRS = (
    "skills", "packages", "context", "npm", "git", "extensions", "prompts", "themes",
    "sessions", "cache", "logs", "tmp",
)


def validate_agent_name(name: str) -> str:
    if len(name) > 64 or not AGENT_NAME.fullmatch(name):
        raise MantleError(
            "Agent names must contain only lowercase letters, numbers, hyphens, and underscores, "
            "cannot begin with a hyphen, and must be 1–64 characters long."
        )
    return name


def prepare_agent_root(root: Path, config: AgentConfig) -> None:
    for child in AGENT_DIRS:
        (root / child).mkdir(parents=True, exist_ok=True)
    for filename in ("settings.json", "auth.json"):
        path = root / filename
        if not path.exists():
            write_json(path, {})
    normalize_models_config(root)
    write_agent_yaml(root / "agent.yaml", config)


def normalize_models_config(root: Path) -> None:
    """Create Pi's empty model config and repair Mantle's legacy `{}` placeholder."""
    path = root / "models.json"
    if path.is_file():
        try:
            if read_json(path) != {}:
                return
        except (OSError, ValueError, MantleError):
            # Leave user-authored invalid files intact so Pi can report the error.
            return
    write_json(path, {"providers": {}})


class AgentStore:
    def __init__(self, paths: MantlePaths):
        self.paths = paths

    def exists(self, name: str) -> bool:
        validate_agent_name(name)
        return self.paths.yaml(name).is_file()

    def create(
        self,
        name: str,
        *,
        description: str = "",
        runtime_version: str = "latest",
        config: AgentConfig | None = None,
    ) -> AgentConfig:
        validate_agent_name(name)
        root = self.paths.agent(name)
        if root.exists():
            raise ConflictError(f'Agent "{name}" already exists at {root}.')
        selected = config or default_agent_config(name, description, runtime_version)
        if selected.agent.name != name:
            raise MantleError(f'Configuration declares agent "{selected.agent.name}", not "{name}".')
        try:
            prepare_agent_root(root, selected)
            return selected
        except BaseException:
            if root.exists():
                shutil.rmtree(root)
            raise

    def save(self, config: AgentConfig) -> None:
        config = AgentConfig.model_validate(config.model_dump())
        if not self.exists(config.agent.name):
            raise NotFoundError(f'Agent "{config.agent.name}" is not installed locally.')
        write_agent_yaml(self.paths.yaml(config.agent.name), config)

    def get(self, name: str) -> AgentConfig:
        validate_agent_name(name)
        if not self.paths.yaml(name).is_file():
            if self.paths.agent(name).exists():
                raise MantleError(f"Agent configuration was not found: {self.paths.yaml(name)}. Restore agent.yaml or remove the damaged agent with: mantle delete {name}")
            raise NotFoundError(f'Agent "{name}" is not installed locally. Use mantle create {name} or mantle install {name}.')
        return load_agent_yaml(self.paths.yaml(name), expected_name=name)

    def list(self) -> list[AgentConfig]:
        if not self.paths.agents.is_dir():
            return []
        result = []
        for path in sorted(self.paths.agents.iterdir()):
            if path.is_dir() and not path.name.startswith(".") and (path / "agent.yaml").is_file():
                result.append(load_agent_yaml(path / "agent.yaml", expected_name=path.name))
        return result

    def delete(self, name: str, *, keep_sessions: bool = False) -> Path | None:
        self.require_local(name)
        root = self.paths.agent(name)
        kept: Path | None = None
        if keep_sessions and (root / "sessions").exists():
            kept = self.paths.home / "retained-sessions" / name
            if kept.exists():
                raise ConflictError(f"Retained sessions already exist at {kept}.")
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(root / "sessions", kept)
        shutil.rmtree(root)
        return kept

    def require_local(self, name: str) -> Path:
        validate_agent_name(name)
        root = self.paths.agent(name)
        if not root.is_dir():
            raise NotFoundError(f'Agent "{name}" is not installed locally.')
        return root

    def project_count(self, name: str) -> int:
        sessions = self.paths.agent(name) / "sessions"
        return sum(1 for path in sessions.iterdir() if path.is_dir()) if sessions.is_dir() else 0
