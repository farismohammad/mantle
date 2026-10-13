from __future__ import annotations

import hashlib
import re
from pathlib import Path

from mantle.errors import MantleError


def resolve_project_root(current: Path, explicit: Path | None = None) -> Path:
    current = current.resolve()
    root = (explicit or current).expanduser().resolve()
    if not root.is_dir():
        raise MantleError(f"Project root is not a directory: {root}")
    if explicit is not None and not current.is_relative_to(root):
        raise MantleError(
            f"The current directory is not inside the specified project root.\n\n"
            f"Current directory:\n  {current}\n\nSpecified root:\n  {root}"
        )
    return root


def project_id(root: Path) -> str:
    canonical = str(root.resolve())
    name = re.sub(r"[^a-z0-9_-]+", "-", root.name.lower()).strip("-") or "project"
    suffix = hashlib.sha256(canonical.encode()).hexdigest()[:6]
    return f"{name}-{suffix}"
