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


class _AgentDumper(yaml.SafeDumper):
    """Write multi-line strings, such as system prompts, as readable `|` blocks."""


def _represent_string(dumper: yaml.SafeDumper, value: str):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_AgentDumper.add_representer(str, _represent_string)


def _dump(data: Any) -> str:
    return yaml.dump(data, Dumper=_AgentDumper, sort_keys=False, allow_unicode=True, width=100)


def dump_agent_yaml(config: AgentConfig) -> str:
    return _dump(config.model_dump(mode="json", exclude_none=True))


def write_agent_yaml(path: Path, config: AgentConfig, *, previous: str | None = None) -> None:
    text = dump_agent_yaml(config) if previous is None else update_agent_yaml(previous, config)
    atomic_write_text(path, text, mode=0o644)


class _CannotEdit(Exception):
    """The file's layout cannot be changed in place; it is regenerated instead."""


def update_agent_yaml(text: str, config: AgentConfig) -> str:
    """Render `config` by editing `text` in place.

    Only the values that changed are rewritten, so comments, formatting and
    omitted defaults survive resource and model changes. When the layout cannot
    be edited safely (flow-style mappings, anchors, removed keys), or the edit
    would not read back as `config`, the whole file is regenerated.
    """
    try:
        before = parse_agent_yaml(text).model_dump(mode="json", exclude_none=True)
        after = config.model_dump(mode="json", exclude_none=True)
        if set(before) - set(after):
            raise _CannotEdit
        updated = text
        for key, value in after.items():
            if before.get(key) == value:
                continue
            if isinstance(value, dict) and isinstance(before.get(key), dict):
                if set(before[key]) - set(value):
                    raise _CannotEdit
                for child in value:
                    if before[key].get(child) != value[child]:
                        updated = _set_yaml_value(updated, (key, child), after)
            else:
                updated = _set_yaml_value(updated, (key,), after)
        if parse_agent_yaml(updated) != config:
            raise _CannotEdit
        return updated
    except (_CannotEdit, MantleError, yaml.YAMLError):
        return dump_agent_yaml(config)


def _set_yaml_value(text: str, path: tuple[str, ...], data: dict[str, Any]) -> str:
    """Replace (or add) the value at `path` with the one in `data`, leaving other lines untouched."""
    if any(character in text for character in "\r\x85  "):
        raise _CannotEdit  # Keep PyYAML's line numbers and ours in agreement.
    for event in yaml.parse(text, Loader=UniqueKeyLoader):
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            raise _CannotEdit  # An alias shares its node with another location in the file.
    lines = text.split("\n")
    node = yaml.compose(text, Loader=UniqueKeyLoader)
    for depth, key in enumerate(path):
        if not _is_block_mapping(node):
            raise _CannotEdit
        value = data
        for part in path[:depth + 1]:
            value = value[part]
        pair = next(((k, v) for k, v in node.value if isinstance(k, yaml.ScalarNode) and k.value == key), None)
        if pair is None:
            # Add only the missing path (not its defaulted siblings) after the
            # mapping's last line, at the indentation of its other keys.
            nested = value
            for part in path[depth + 1:]:
                nested = nested[part]
            for part in reversed(path[depth + 1:]):
                nested = {part: nested}
            indent = node.value[0][0].start_mark.column
            end = _last_line(lines, node)
            return "\n".join(lines[:end + 1] + _render(key, nested, indent) + lines[end + 1:])
        key_node, value_node = pair
        if depth == len(path) - 1 or not _is_block_mapping(value_node):
            start = key_node.start_mark.line
            if lines[start][:key_node.start_mark.column].strip():
                raise _CannotEdit
            end = max(start, _last_line(lines, value_node))
            return "\n".join(lines[:start] + _render(key, value, key_node.start_mark.column) + lines[end + 1:])
        node = value_node
    raise _CannotEdit


def _is_block_mapping(node: yaml.Node) -> bool:
    return isinstance(node, yaml.MappingNode) and not node.flow_style and bool(node.value)


def _last_line(lines: list[str], node: yaml.Node) -> int:
    """The last line holding part of `node` (block collections end where their next sibling starts)."""
    if isinstance(node, (yaml.MappingNode, yaml.SequenceNode)) and not node.flow_style:
        children = [item for pair in node.value for item in pair] if isinstance(node, yaml.MappingNode) else node.value
        return max((_last_line(lines, child) for child in children), default=node.start_mark.line)
    mark = node.end_mark
    line = mark.line
    if line >= len(lines) or not lines[line][:mark.column].strip():
        line -= 1  # The node ended at the very start of this line.
    while line > node.start_mark.line and not lines[line].strip():
        line -= 1
    return line


def _render(key: str, value: Any, indent: int) -> list[str]:
    block = _dump({key: value}).rstrip("\n").split("\n")
    return [" " * indent + line if line else line for line in block]
