from __future__ import annotations

import pytest
import yaml

from mantle.configuration import parse_agent_yaml
from mantle.errors import MantleError


@pytest.mark.parametrize("field, message", [
    ("permissions: {filesystem: workspace}", "permissions.filesystem.*workspace confinement"),
    ("resources: {skills: [{name: docs, source: local, version: v1}]}", "resources.skills.0.version.*never used"),
    ("resources: {packages: [{name: docs, source: 'npm:../foreign'}]}", "resources.packages.0.source.*Invalid npm"),
    ("resources: {packages: [{name: docs, source: 'https://token@example.test/o/repo'}]}", "must not contain credentials"),
    ("resources: {packages: [{name: docs, source: 'example.test/o/repo'}]}", "Package source must"),
    ("resources: {packages: [{name: docs, source: 'https://example.test/o/%2e%2e/repo'}]}", "Git package source"),
    ("resources: {packages: [{name: docs, source: 'https://example.test/o/%2F..%2Frepo'}]}", "Git package source"),
    ("resources: {packages: [{name: docs, source: 'https://example.test/o/%ZZ'}]}", "Git package source.*percent escape"),
    ("resources: {packages: [{name: docs, source: 'https://example.test/o/%00repo'}]}", "Git package source"),
    ("resources: {packages: [{name: docs, source: 'https://example.test/o/%FFrepo'}]}", "Git package source.*encoded path"),
    ("mcp: {servers: {docs: {url: https://example.test, disabled: 'false'}}}", "disabled.*boolean"),
    ("mcp: {servers: {docs: {url: https://example.test, request_timeout_ms: true}}}", "request_timeout_ms.*integer"),
    ("mantle: 0.1", "mantle"),
    ("secrets: [PI_PACKAGE_DIR]", "secrets.*reserved"),
    ("resources: {skills: [{name: docs, source: x}, {name: docs, source: y}]}", "skill names must be unique"),
    ("resources: {packages: [{name: old, source: 'https://example.test/o/r.git@v1'}, {name: new, source: 'git:example.test/o/r@v2'}]}", "package sources must be unique"),
    ("resources: {packages: [{name: old, source: 'https://example.test/o/repo@v1'}, {name: new, source: 'https://example.test/o/%72epo@v2'}]}", "package sources must be unique"),
])
def test_public_schema_rejects_ignored_or_ambiguous_configuration(field, message):
    with pytest.raises(MantleError, match=message):
        parse_agent_yaml(f"agent: {{name: demo}}\n{field}\n")


def test_yaml_12_scalars_match_registry_and_do_not_change_pyyaml_defaults():
    config = parse_agent_yaml("agent: {name: on, description: 2026-10-02}\nmcp: {servers: {docs: {url: https://example.test, request_timeout_ms: 010}}}")
    assert config.agent.name == "on"
    assert config.agent.description == "2026-10-02"
    assert config.mcp.servers["docs"].request_timeout_ms == 10
    assert yaml.safe_load("on") is True


@pytest.mark.parametrize("text, expected", [
    ("agent: {name: demo}\nx: " + "[" * 1500 + "0" + "]" * 1500, "nesting"),
    ("agent: {name: demo}\nx: &x [*x]", "expanded configuration"),
    ("agent: {name: demo}\n1: anything", "mapping keys must be strings"),
    ("agent: {name: demo}\n" + "#" * 1_000_000, "1 MB"),
    ("agent: {name: demo, name: other}", "duplicate key"),
    ("agent: {name: demo}\n<<: {system_prompt: hidden}", "Extra inputs"),
])
def test_malformed_configuration_has_clean_cli_errors(cli, tmp_path, text, expected):
    source = tmp_path / "agent.yaml"
    source.write_text(text)
    result = cli("agent", "validate", str(source))
    assert result.exit_code == 1
    assert expected in result.stderr
    assert "Traceback" not in result.stderr
    assert str(source) in result.stderr


@pytest.mark.parametrize("source", [
    "npm:example@1.2.3", "npm:@example/package@1.2.3", "npm:example@latest",
    "https://example.test/owner/repo.git@v1", "git@example.test:owner/repo.git@v1",
])
def test_valid_package_sources_remain_supported(source):
    config = parse_agent_yaml(f"agent: {{name: demo}}\nresources: {{packages: [{{name: example, source: '{source}'}}]}}")
    assert config.resources.packages[0].source == source
