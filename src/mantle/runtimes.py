from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from mantle.agents import AgentStore
from mantle.errors import ConflictError, MantleError, NotFoundError
from mantle.models import MantleConfig, RuntimeMetadata
from mantle.paths import MantlePaths
from mantle.storage import load_json, read_json, write_json

PI_PACKAGE = "@earendil-works/pi-coding-agent"
Runner = Callable[..., subprocess.CompletedProcess[str]]


class RuntimeManager:
    def __init__(self, paths: MantlePaths, runner: Runner = subprocess.run):
        self.paths = paths
        self.runner = runner

    def config(self) -> MantleConfig:
        return MantleConfig.model_validate(load_json(self.paths.config, {}))

    def save_config(self, config: MantleConfig) -> None:
        write_json(self.paths.config, config.model_dump(mode="json"))

    def latest_version(self) -> str:
        result = self.runner(
            ["npm", "view", PI_PACKAGE, "version", "--json"],
            check=False, capture_output=True, text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "npm could not resolve the package"
            raise MantleError(f"Could not resolve the latest Pi version: {detail}")
        version = result.stdout.strip().strip('"')
        if not version or any(character.isspace() for character in version):
            raise MantleError("npm returned an invalid Pi version.")
        return version

    def install(self, version: str | None = None) -> RuntimeMetadata:
        selected = version or self.latest_version()
        destination = self.paths.runtimes / selected
        if (destination / "metadata.json").is_file():
            return self.get(selected)
        self.paths.runtimes.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{selected}.", dir=self.paths.runtimes))
        try:
            result = self.runner(
                ["npm", "install", "--prefix", str(temporary), "--ignore-scripts", "--no-audit", "--no-fund", f"{PI_PACKAGE}@{selected}"],
                check=False, capture_output=True, text=True,
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or "npm install failed"
                raise MantleError(f"Could not install pi@{selected}: {detail}")
            executable = temporary / "node_modules" / ".bin" / "pi"
            if not executable.is_file():
                raise MantleError(f"Pi installation verification failed: {executable} was not created.")
            metadata = RuntimeMetadata(version=selected)
            write_json(temporary / "metadata.json", metadata.model_dump(mode="json"))
            temporary.rename(destination)
            config = self.config()
            if config.default_runtime is None:
                config.default_runtime = selected
                self.save_config(config)
            return metadata
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def list(self) -> list[RuntimeMetadata]:
        if not self.paths.runtimes.is_dir():
            return []
        return [
            RuntimeMetadata.model_validate(read_json(path / "metadata.json"))
            for path in sorted(self.paths.runtimes.iterdir())
            if (path / "metadata.json").is_file()
        ]

    def get(self, version: str) -> RuntimeMetadata:
        path = self.paths.runtimes / version / "metadata.json"
        if not path.is_file():
            raise NotFoundError(f"Runtime pi@{version} is not installed.")
        return RuntimeMetadata.model_validate(read_json(path))

    def executable(self, version: str) -> Path:
        self.get(version)
        executable = self.paths.runtimes / version / "node_modules" / ".bin" / "pi"
        if not executable.is_file():
            raise MantleError(f"Runtime pi@{version} is damaged: {executable} is missing.")
        return executable

    def ensure(self, version: str, *, refresh_latest: bool = False) -> RuntimeMetadata:
        """Return an installed runtime, installing it when reconstruction requires it."""
        if version == "latest":
            if refresh_latest:
                metadata = self.install()
                self.set_default(metadata.version)
                return metadata
            config = self.config()
            if config.default_runtime:
                try:
                    return self.get(config.default_runtime)
                except NotFoundError:
                    pass
            return self.install()
        try:
            return self.get(version)
        except NotFoundError:
            return self.install(version)

    def set_default(self, version: str) -> None:
        self.get(version)
        config = self.config()
        config.default_runtime = version
        self.save_config(config)

    def remove(self, version: str) -> None:
        self.get(version)
        users = [agent.agent.name for agent in AgentStore(self.paths).list() if agent.runtime.version == version]
        if users:
            raise ConflictError(f"Cannot remove pi@{version}; it is used by: {', '.join(users)}.")
        shutil.rmtree(self.paths.runtimes / version)
        config = self.config()
        if config.default_runtime == version:
            config.default_runtime = None
            self.save_config(config)
