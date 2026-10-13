from __future__ import annotations

import os
import re
from pathlib import Path

from mantle.errors import MantleError
from mantle.models import SECRET_NAME_PATTERN
from mantle.storage import atomic_write_text


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
            raise MantleError(f"Invalid entry in {path} at line {number}: expected NAME=value.")
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


def declared_secret_environment(
    path: Path, names: list[str], fallback: dict[str, str] | None = None, *, isolated: bool = False,
) -> dict[str, str]:
    """Resolve declared secrets from the environment, then `.env`, then shared provider keys.

    An `isolated` agent gets no shared keys, so the fix for a missing provider key points to `.env`.
    """
    if not names:
        return {}
    local = parse_dotenv(path)
    fallback = fallback or {}
    selected: dict[str, str] = {}
    missing = []
    for name in names:
        value = os.environ.get(name) or local.get(name) or fallback.get(name)
        if not value:
            missing.append(name)
        else:
            selected[name] = value
    if missing:
        raise MantleError("Missing required local secret(s): " + ", ".join(missing) + ". "
                          + secret_fix(missing[0], path, isolated=isolated))
    return selected


def secret_fix(name: str, path: Path | None = None, *, isolated: bool = False) -> str:
    """How to provide a missing secret: provider keys are shared logins, others live in .env.

    An isolated agent never reads the shared logins, so its provider keys live in .env as well.
    """
    from mantle.providers import env_variables
    provider = None if isolated else env_variables().get(name)
    if provider:
        return f"Sign in once for every agent with: mantle auth login {provider}"
    return f"Set it with: mantle auth secrets set {name}" + (f"  (stored in {path})" if path else "")


def _validate_name(name: str) -> None:
    if not SECRET_NAME_PATTERN.fullmatch(name):
        raise MantleError("Secret names must be uppercase environment variable names, such as DOCS_TOKEN.")


def set_dotenv_value(path: Path, name: str, value: str) -> None:
    """Add or replace NAME=value, keeping other lines and comments, with private permissions."""
    _validate_name(name)
    if not value or any(character in value for character in "\r\n\0"):
        raise MantleError("Secret values must be a single, non-empty line.")
    parse_dotenv(path)  # Refuse to rewrite a file Mantle cannot parse.
    rendered = f"{name}={value}" if re.fullmatch(r"[^\s\"'#]+", value) else f"{name}='{value}'" if "'" not in value else f'{name}="{value}"'
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(name)}\s*=")
    replaced = False
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = rendered
            replaced = True
    if not replaced:
        lines.append(rendered)
    atomic_write_text(path, "\n".join(lines) + "\n", mode=0o600)


def remove_dotenv_value(path: Path, name: str) -> bool:
    _validate_name(name)
    if not path.is_file():
        return False
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(name)}\s*=")
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if not pattern.match(line)]
    if len(kept) == len(lines):
        return False
    atomic_write_text(path, "\n".join(kept) + ("\n" if kept else ""), mode=0o600)
    return True
