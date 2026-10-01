from __future__ import annotations

import json
import os
import tempfile
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from mantle.errors import MantleError


def atomic_write_text(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def read_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (ValueError, OSError) as error:
        raise MantleError(f"Could not read JSON from {path}: {error}") from error


def load_json(path: Path, default: Any) -> Any:
    return read_json(path) if path.is_file() else default


@contextmanager
def resource_transaction(root: Path, names: tuple[str, ...]):
    """Restore agent-owned resource state if installation or YAML saving fails."""
    with tempfile.TemporaryDirectory(prefix=".resources-", dir=root.parent) as directory:
        backup = Path(directory)
        for name in names:
            source = root / name
            if source.is_symlink():
                raise MantleError(f"Resource state must not be a symlink: {source}")
            if source.is_dir():
                shutil.copytree(source, backup / name, symlinks=True)
            elif source.exists():
                shutil.copy2(source, backup / name)
        try:
            yield
        except BaseException:
            for name in names:
                target = root / name
                saved = backup / name
                if target.is_symlink() or target.is_file():
                    target.unlink()
                elif target.exists():
                    shutil.rmtree(target)
                if saved.exists():
                    saved.rename(target)
            raise
