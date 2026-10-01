from __future__ import annotations

from pathlib import Path
from typing import Any
import re

import yaml
from pydantic import ValidationError
from yaml.error import MarkedYAMLError

from mantle.errors import MantleError
from mantle.models import AgentConfig
from mantle.storage import atomic_write_text


class UniqueKeyLoader(yaml.SafeLoader):
    """Bounded YAML 1.2 core scalars, shared with the registry's YAML parser."""

    def compose_node(self, parent, index):
        self._depth = getattr(self, "_depth", 0) + 1
        try:
            if self._depth > 64:
                raise MantleError("Invalid YAML: nesting exceeds the 64-level configuration limit.")
            if self.check_event(yaml.AliasEvent):
                self._aliases = getattr(self, "_aliases", 0) + 1
                if self._aliases > 100:
                    raise MantleError("Invalid YAML: too many aliases (maximum 100).")
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key, _ in node.value:
            value = self.construct_object(key, deep=deep)
            if not isinstance(value, str):
                raise MantleError(f"Invalid YAML at line {key.start_mark.line + 1}: mapping keys must be strings.")
            try:
                if value in seen:
                    raise MantleError(f"Invalid YAML at line {key.start_mark.line + 1}: duplicate key {value!r}.")
                seen.add(value)
            except TypeError as error:
                raise MantleError("Invalid YAML: mapping keys must be scalar values.") from error
        return super().construct_mapping(node, deep=deep)


# PyYAML defaults to YAML 1.1 (where `on` is a boolean and dates are objects).
# Do not mutate SafeLoader: callers elsewhere in the process keep its semantics.
UniqueKeyLoader.yaml_implicit_resolvers = {
    key: [(tag, pattern) for tag, pattern in resolvers if tag not in {
        "tag:yaml.org,2002:bool", "tag:yaml.org,2002:int", "tag:yaml.org,2002:float",
        "tag:yaml.org,2002:timestamp", "tag:yaml.org,2002:merge",
    }]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
UniqueKeyLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))
UniqueKeyLoader.add_implicit_resolver("tag:yaml.org,2002:int", re.compile(r"^[-+]?(?:[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$"), list("-+0123456789"))
UniqueKeyLoader.add_implicit_resolver("tag:yaml.org,2002:float", re.compile(r"^(?:[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?|[-+]?[0-9]+[eE][-+]?[0-9]+|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"), list("-+0123456789."))


def _core_integer(loader, node):
    value = loader.construct_scalar(node)
    signless = value.lstrip("+-")
    return int(value, 0 if signless.startswith(("0o", "0x")) else 10)


UniqueKeyLoader.add_constructor("tag:yaml.org,2002:int", _core_integer)


def _check_document_size(data: Any) -> None:
    pending = [data]
    count = 0
    while pending:
        value = pending.pop()
        count += 1
        if count > 10_000:
            raise MantleError("Invalid YAML: expanded configuration exceeds 10000 values; remove recursive or excessive aliases.")
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def format_validation_error(error: ValidationError) -> str:
    lines = []
    for issue in error.errors(include_url=False):
        path = ".".join(str(part) for part in issue["loc"]) or "configuration"
        lines.append(f"  {path}: {issue['msg']}")
    return "Configuration does not satisfy the Mantle schema:\n" + "\n".join(lines)


def parse_agent_yaml(text: str, *, expected_name: str | None = None) -> AgentConfig:
    if len(text.encode("utf-8")) > 1_000_000:
        raise MantleError("agent.yaml must be 1 MB or smaller.")
    try:
        data: Any = yaml.load(text, Loader=UniqueKeyLoader)
    except MarkedYAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        detail = getattr(error, "problem", None) or str(error)
        raise MantleError(f"Invalid YAML{location}: {detail}") from error
    except yaml.YAMLError as error:
        raise MantleError(f"Invalid YAML: {error}") from error
    except RecursionError as error:
        raise MantleError("Invalid YAML: configuration is too deeply nested.") from error
    if not isinstance(data, dict):
        raise MantleError("Invalid YAML: the document root must be a mapping.")
    _check_document_size(data)
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
    except (OSError, UnicodeError) as error:
        raise MantleError(f"Could not read {path}: {error}") from error
    try:
        return parse_agent_yaml(text, expected_name=expected_name)
    except MantleError as error:
        raise MantleError(f"{path}: {error}") from error


def dump_agent_yaml(config: AgentConfig) -> str:
    data = config.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def write_agent_yaml(path: Path, config: AgentConfig) -> None:
    atomic_write_text(path, dump_agent_yaml(config), mode=0o644)
