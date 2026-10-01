from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from mantle.errors import MantleError


@dataclass(frozen=True)
class MantlePaths:
    home: Path

    @classmethod
    def discover(cls) -> "MantlePaths":
        override = os.environ.get("MANTLE_HOME")
        home = Path(override).expanduser() if override else Path.home() / ".mantle"
        return cls(home.resolve())

    @property
    def config(self) -> Path:
        return self.home / "config.json"

    @property
    def dotenv(self) -> Path:
        return self.home / ".env"

    @property
    def agents(self) -> Path:
        return self.home / "agents"

    @property
    def runtimes(self) -> Path:
        return self.home / "runtimes" / "pi"

    def agent(self, name: str) -> Path:
        root = self.agents / name
        if root.is_symlink() or self.agents.is_symlink():
            raise MantleError(f"Agent directories must not be symlinks: {root}")
        return root

    def yaml(self, name: str) -> Path:
        return self.agent(name) / "agent.yaml"
