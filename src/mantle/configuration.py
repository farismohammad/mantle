from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from yaml.error import MarkedYAMLError

from mantle.errors import MantleError
from mantle.models import AgentConfig
from mantle.storage import atomic_write_text


def format_validation_error(error: ValidationError) -> str:
    lines = []
    for issue in error.errors(include_url=False):
        path = ".".join(str(part) for part in issue["loc"]) or "configuration"
        lines.append(f"  {path}: {issue['msg']}")
    return "Configuration does not satisfy the Mantle schema:\n" + "\n".join(lines)


def parse_agent_yaml(text: str, *, expected_name: str | None = None) -> AgentConfig:
    try:
        data: Any = yaml.safe_load(text)
    except MarkedYAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        detail = getattr(error, "problem", None) or str(error)
        raise MantleError(f"Invalid YAML{location}: {detail}") from error
    if not isinstance(data, dict):
        raise MantleError("Invalid YAML: the document root must be a mapping.")
    try:
        config = AgentConfig.model_validate(data)
    except ValidationError as error:
        raise MantleError(format_validation_error(error)) from error
    if expected_name is not None and config.agent.name != expected_name:
        raise MantleError(
            f'Configuration declares agent "{config.agent.name}", but "{expected_name}" was requested.'
        )
    return config


def load_agent_yaml(path: Path, *, expected_name: str | None = None) -> AgentConfig:
    if not path.is_file():
        raise MantleError(f"Agent configuration was not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise MantleError(f"Could not read {path}: {error}") from error
    return parse_agent_yaml(text, expected_name=expected_name)


def dump_agent_yaml(config: AgentConfig) -> str:
    data = config.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def write_agent_yaml(path: Path, config: AgentConfig) -> None:
    atomic_write_text(path, dump_agent_yaml(config), mode=0o644)
