from __future__ import annotations

import os
from pathlib import Path

from mantle.errors import MantleError


def parse_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise MantleError(f"Invalid .env entry at line {number}: expected NAME=value.")
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


def declared_secret_environment(path: Path, names: list[str]) -> dict[str, str]:
    local = parse_dotenv(path)
    selected: dict[str, str] = {}
    missing = []
    for name in names:
        value = os.environ.get(name, local.get(name))
        if value is None or value == "":
            missing.append(name)
        else:
            selected[name] = value
    if missing:
        raise MantleError(
            "Missing required local secret(s): " + ", ".join(missing) + f". Add them to {path}."
        )
    return selected
