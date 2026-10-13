from __future__ import annotations

import pytest
import yaml

from mantle.agents import AgentStore, validate_agent_name
from mantle.configuration import parse_agent_yaml, update_agent_yaml
from mantle.errors import MantleError
from mantle.models import ResourceDefinition
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
    created = cli("agent", "create", "developer", "--description", "Writes software")
    assert created.exit_code == 0, created.stdout
    listed = cli("agent", "list", "--json")
    assert listed.exit_code == 0
    assert '"name": "developer"' in listed.stdout
    assert cli("agent", "delete", "developer", "--yes").exit_code == 0
    assert not (mantle_home / "agents" / "developer").exists()


def test_run_recommends_install_when_agent_exists_only_in_cloud(cli, monkeypatch):
    class FakeCloud:
        def __init__(self, _paths):
            pass

        def set_timeout(self, seconds):
            pass

        def exists(self, name):
            return name == "research"

    monkeypatch.setattr("mantle.cli.CloudClient", FakeCloud)
    result = cli("agent", "run", "research")
    assert result.exit_code == 1
    assert "available in Mantle Cloud" in result.stderr
    assert "mantle cloud pull research" in result.stderr


HAND_WRITTEN = """# Hand-written agent
agent:
  name: cmt  # keep this name
model:
  provider: openai
  name: gpt-5.6
system_prompt: |
  Line one.

  Line two.

# Read-only reviewer
permissions:
  filesystem: read
"""


def test_saving_rewrites_only_the_changed_values(mantle_home):
    paths = MantlePaths.discover()
    store = AgentStore(paths)
    store.create("cmt")
    paths.yaml("cmt").write_text(HAND_WRITTEN)
    config = store.get("cmt")
    config.resources.skills.append(ResourceDefinition(name="docs", source="https://example.test/skills"))
    store.save(config)
    text = paths.yaml("cmt").read_text()
    # Comments, the block scalar and omitted defaults are untouched; only the new key is added.
    assert text == HAND_WRITTEN + "resources:\n  skills:\n  - name: docs\n    source: https://example.test/skills\n"
    config.model.provider, config.model.name = "anthropic", "claude-opus-4-8"
    config.resources.skills = []
    store.save(config)
    text = paths.yaml("cmt").read_text()
    assert text == HAND_WRITTEN.replace("provider: openai\n  name: gpt-5.6", "provider: anthropic\n  name: claude-opus-4-8") + "resources:\n  skills: []\n"
    assert store.get("cmt") == config


@pytest.mark.parametrize("text", [
    "agent: {name: demo}\nresources: {skills: [], packages: []}  # flow style\n",
    "{agent: {name: demo}, model: {provider: openai, name: gpt-5.6}}\n",
    "agent:\r\n  name: demo\r\nresources:\r\n  skills: []\r\n",
    "agent: &agent\n  name: demo\nresources:\n  skills: []\n",
    "agent:\n  name: demo\nsystem_prompt: |+\n  Keep trailing lines.\n\n",
    "agent:\n  name: demo\nresources:\n  # skills go here\n  skills:\n    - name: old\n      source: repo  # pinned\n  packages: []\n# end\n",
])
def test_every_layout_saves_the_requested_configuration(text):
    config = parse_agent_yaml(text)
    config.resources.skills = [ResourceDefinition(name="docs", source="https://example.test/skills")]
    config.model.name = "gpt-6"
    # Edited in place where that is safe, regenerated otherwise; never a different configuration.
    assert parse_agent_yaml(update_agent_yaml(text, config)) == config


def test_create_sets_description_and_runtime_version(cli, mantle_home):
    result = cli("agent", "create", "docs", "--runtime-version", "1.0.0", "--description", "Docs")
    assert result.exit_code == 0, result.output
    config = parse_agent_yaml((mantle_home / "agents/docs/agent.yaml").read_text(), expected_name="docs")
    assert config.runtime.version == "1.0.0" and config.agent.description == "Docs"
