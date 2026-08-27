from __future__ import annotations

import json

from mantle.agents import AgentStore
from mantle.launcher import AgentLauncher
from mantle.models import McpServerDefinition, RuntimeMetadata
from mantle.paths import MantlePaths
from mantle.storage import write_json


def setup(mantle_home):
    paths = MantlePaths.discover()
    executable = paths.runtimes / "1.0.0/node_modules/.bin/pi"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n")
    write_json(paths.runtimes / "1.0.0/metadata.json", RuntimeMetadata(version="1.0.0").model_dump(mode="json"))
    config = AgentStore(paths).create("developer", runtime_version="1.0.0")
    config.secrets = ["OPENAI_API_KEY"]
    AgentStore(paths).save(config)
    return paths


def test_launch_isolated_with_declared_secret_and_project_context(mantle_home, tmp_path, monkeypatch):
    paths = setup(mantle_home)
    paths.dotenv.write_text("OPENAI_API_KEY=local-secret\nUNDECLARED_SECRET=do-not-load\n")
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("project")
    (project / ".pi").mkdir()
    (paths.agent("developer") / "context/agent.md").write_text("agent")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("UNDECLARED_SECRET", raising=False)
    spec = AgentLauncher(paths).build("developer", current=project)
    for flag in ("--no-context-files", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes"):
        assert flag in spec.command
    assert spec.environment["PI_CODING_AGENT_DIR"] == str(paths.agent("developer"))
    assert spec.environment["OPENAI_API_KEY"] == "local-secret"
    assert "UNDECLARED_SECRET" not in spec.environment
    assert "agent" in spec.command
    assert "project" in spec.command
    assert not any(str(project / ".pi") in value for value in spec.command)
    assert spec.command[spec.command.index("--provider") + 1] == "openai"
    assert spec.command[spec.command.index("--model") + 1] == "gpt-5.6"


def test_launch_repairs_legacy_empty_models_config(mantle_home, tmp_path, monkeypatch):
    paths = setup(mantle_home)
    models = paths.agent("developer") / "models.json"
    write_json(models, {})
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")

    AgentLauncher(paths).build("developer", current=tmp_path)

    assert models.read_text() == '{\n  "providers": {}\n}\n'


def test_launch_uses_only_agent_declared_mcp_config(mantle_home, tmp_path, monkeypatch):
    paths = setup(mantle_home)
    config = AgentStore(paths).get("developer")
    config.mcp.servers = {
        "docs": McpServerDefinition(
            url="https://mcp.example.test/mcp",
            auth="bearer",
            bearer_token_env="DOCS_TOKEN",
            direct_tools=["search"],
        )
    }
    config.secrets.append("DOCS_TOKEN")
    AgentStore(paths).save(config)
    adapter = paths.agent("developer") / "npm/node_modules/pi-mcp-adapter"
    adapter.mkdir(parents=True)
    (adapter / "index.ts").write_text("export const createMcpAdapter = () => {};\n")
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("DOCS_TOKEN", "docs-secret")

    spec = AgentLauncher(paths).build("developer", current=tmp_path)

    bridge = paths.agent("developer") / "extensions/mantle-mcp.ts"
    assert spec.command.count(str(bridge)) == 1
    assert "createMcpAdapter({ config })" in bridge.read_text()
    generated = json.loads((paths.agent("developer") / "mantle-mcp.json").read_text())
    assert generated == {
        "mcpServers": {
            "docs": {
                "auth": "bearer",
                "bearerTokenEnv": "DOCS_TOKEN",
                "directTools": ["search"],
                "url": "https://mcp.example.test/mcp",
            }
        },
        "settings": {"hostConfigDiscovery": "off"},
    }
    assert spec.environment["DOCS_TOKEN"] == "docs-secret"


def test_launch_removes_stale_generated_mcp_files(mantle_home, tmp_path, monkeypatch):
    paths = setup(mantle_home)
    bridge = paths.agent("developer") / "extensions/mantle-mcp.ts"
    bridge.write_text("stale")
    (paths.agent("developer") / "mantle-mcp.json").write_text("{}")
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")

    spec = AgentLauncher(paths).build("developer", current=tmp_path)

    assert not bridge.exists()
    assert not (paths.agent("developer") / "mantle-mcp.json").exists()
    assert str(bridge) not in spec.command
