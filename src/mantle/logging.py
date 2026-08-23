from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mantle.storage import atomic_write_text


def append_event(path: Path, event: dict[str, Any]) -> None:
    """Append a deliberately allowlisted, secret-free event as JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    atomic_write_text(path, existing + json.dumps(record, sort_keys=True) + "\n")
