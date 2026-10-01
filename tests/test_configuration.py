from __future__ import annotations

import pytest
import yaml

from mantle.agents import AgentStore, validate_agent_name
from mantle.configuration import parse_agent_yaml
from mantle.errors import MantleError
from mantle.paths import MantlePaths


@pytest.mark.parametrize("name", ["developer", "soc-analyst", "agent_2", "0agent"])
def test_valid_agent_names(name):
    assert validate_agent_name(name) == name


@pytest.mark.parametrize("name", ["-agent", "Agent", "agent space", "", "../agent"])
def test_invalid_agent_names(name):
    with pytest.raises(MantleError):
        validate_agent_name(name)


def test_create_writes_declarative_yaml_as_only_agent_metadata(mantle_home):
    paths = MantlePaths.discover()
    config = AgentStore(paths).create("developer", description="Writes software")
    root = mantle_home / "agents" / "developer"
    assert config.agent.name == "developer"
    for child in ("skills", "packages", "context", "npm", "git", "sessions", "logs"):
        assert (root / child).is_dir()
    assert not (root / "metadata.json").exists()
    document = yaml.safe_load((root / "agent.yaml").read_text())
    assert document["agent"] == {"name": "developer", "description": "Writes software"}
    assert document["runtime"] == {"name": "pi", "version": "latest"}


def test_yaml_syntax_and_schema_errors_are_actionable():
    with pytest.raises(MantleError, match=r"line 1, column"):
        parse_agent_yaml("agent: [")
    with pytest.raises(MantleError, match=r"runtime.name"):
        parse_agent_yaml("""mantle: '0.1'
agent: {name: demo}
runtime: {name: python, version: latest}
""")


def test_mcp_servers_require_one_supported_transport():
    config = parse_agent_yaml("""mantle: '0.1'
agent: {name: research}
secrets: [DOCS_TOKEN]
mcp:
  servers:
    docs:
      url: https://mcp.example.test/mcp
      auth: bearer
      bearer_token_env: DOCS_TOKEN
    local:
      command: npx
      args: [-y, example-mcp-server]
""")
    assert set(config.mcp.servers) == {"docs", "local"}

    with pytest.raises(MantleError, match="exactly one of command or url"):
        parse_agent_yaml("""mantle: '0.1'
agent: {name: broken}
mcp:
  servers:
    invalid: {}
""")


def test_first_class_mcp_rejects_ambient_adapter_package():
    with pytest.raises(MantleError, match="managed automatically"):
        parse_agent_yaml("""mantle: '0.1'
agent: {name: research}
resources:
  packages:
    - {name: pi-mcp-adapter, source: 'npm:pi-mcp-adapter'}
mcp:
  servers:
    docs: {url: 'https://mcp.example.test/mcp'}
""")


def test_cli_uses_simple_top_level_lifecycle(cli, mantle_home):
    created = cli("create", "developer", "--description", "Writes software")
    assert created.exit_code == 0, created.stdout
    listed = cli("list", "--json")
    assert listed.exit_code == 0
    assert '"name": "developer"' in listed.stdout
    assert cli("delete", "developer", "--yes").exit_code == 0
    assert not (mantle_home / "agents" / "developer").exists()


def test_run_recommends_install_when_agent_exists_only_in_cloud(cli, monkeypatch):
    class FakeCloud:
        def __init__(self, _paths):
            pass

        def exists(self, name):
            return name == "research"

    monkeypatch.setattr("mantle.cli.CloudClient", FakeCloud)
    result = cli("run", "research")
    assert result.exit_code == 1
    assert "available in Mantle Cloud" in result.stderr
    assert "mantle install research" in result.stderr
