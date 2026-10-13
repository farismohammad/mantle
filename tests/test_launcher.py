from __future__ import annotations

import json

import pytest

from mantle.agents import AgentStore
from mantle.credentials import CredentialStore, isolate_agent
from mantle.errors import MantleError
from mantle.launcher import AgentLauncher
from mantle.models import McpServerDefinition, ResourceDefinition, RuntimeMetadata
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


@pytest.mark.parametrize("isolated", [False, True])
def test_declared_provider_keys_fall_back_to_the_shared_login_only_when_not_isolated(
    mantle_home, tmp_path, monkeypatch, isolated
):
    paths = setup(mantle_home)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    CredentialStore(paths).set_api_key("openai", "sk-shared-123456")
    if isolated:
        isolate_agent(paths, paths.agent("developer"), copy_credentials=False)
        with pytest.raises(MantleError, match="Set it with: mantle auth secrets set OPENAI_API_KEY") as error:
            AgentLauncher(paths).build("developer", current=tmp_path)
        assert "mantle auth login" not in str(error.value)
    else:
        spec = AgentLauncher(paths).build("developer", current=tmp_path)
        assert spec.environment["OPENAI_API_KEY"] == "sk-shared-123456"

    # .env and the shell reach every agent that declares the variable.
    paths.dotenv.write_text("OPENAI_API_KEY=sk-dotenv-7890\n")
    assert AgentLauncher(paths).build("developer", current=tmp_path).environment["OPENAI_API_KEY"] == "sk-dotenv-7890"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-shell-4321")
    assert AgentLauncher(paths).build("developer", current=tmp_path).environment["OPENAI_API_KEY"] == "sk-shell-4321"


def test_launch_creates_missing_models_config(mantle_home, tmp_path, monkeypatch):
    paths = setup(mantle_home)
    models = paths.agent("developer") / "models.json"
    models.unlink()
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")

    AgentLauncher(paths).build("developer", current=tmp_path)

    assert models.read_text() == '{\n  "providers": {}\n}\n'


def test_launch_keeps_only_declared_secrets_and_ignores_pi_overrides(
    mantle_home, tmp_path, monkeypatch
):
    paths = setup(mantle_home)
    monkeypatch.setenv("OPENAI_API_KEY", "declared-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "undeclared-secret")
    monkeypatch.setenv("PI_PACKAGE_DIR", "/host/pi-package")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "/host/pi-agent")
    monkeypatch.setenv("PI_HOST_OVERRIDE", "host-value")
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.test")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")

    spec = AgentLauncher(paths).build("developer", current=tmp_path)

    assert spec.environment["OPENAI_API_KEY"] == "declared-secret"
    assert "ANTHROPIC_API_KEY" not in spec.environment
    assert spec.environment["PATH"] == "/bin"
    assert spec.environment["HTTP_PROXY"] == "http://proxy.example.test"
    assert spec.environment["LC_ALL"] == "C.UTF-8"
    assert spec.environment["PI_CODING_AGENT_DIR"] == str(paths.agent("developer"))
    assert spec.environment["PI_CODING_AGENT_SESSION_DIR"] == str(spec.session_dir)
    assert {key for key in spec.environment if key.startswith("PI_")} == {
        "PI_CODING_AGENT_DIR", "PI_CODING_AGENT_SESSION_DIR", "PI_TELEMETRY",
        "PI_SKIP_VERSION_CHECK",
    }


def test_launch_rejects_session_directory_symlink_escape(mantle_home, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    paths = setup(mantle_home)
    session_root = paths.agent("developer") / "sessions"
    session_root.rmdir()
    outside = tmp_path / "outside-sessions"
    outside.mkdir()
    session_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(MantleError, match="outside its isolated directory"):
        AgentLauncher(paths).build("developer", current=tmp_path)

    assert list(outside.iterdir()) == []


def test_launch_rejects_context_symlink_escape(mantle_home, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    paths = setup(mantle_home)
    outside = tmp_path / "outside.md"
    outside.write_text("private instructions")
    (paths.agent("developer") / "context/private.md").symlink_to(outside)

    with pytest.raises(MantleError, match="outside its isolated directory"):
        AgentLauncher(paths).build("developer", current=tmp_path)


@pytest.mark.parametrize("filename", ["agent.yaml", "settings.json", "auth.json", "models.json"])
def test_launch_rejects_pi_config_symlink_escape(
    mantle_home, tmp_path, monkeypatch, filename
):
    paths = setup(mantle_home)
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    selected = paths.agent("developer") / filename
    outside = tmp_path / filename
    if filename == "agent.yaml":
        selected.replace(outside)
        selected.symlink_to(outside)
    else:
        outside.write_text("{}\n")
        selected.unlink()
        selected.symlink_to(outside)

    with pytest.raises(MantleError, match="outside its isolated directory"):
        AgentLauncher(paths).build("developer", current=tmp_path)


def test_launch_rejects_nested_skill_symlink_escape(mantle_home, tmp_path, monkeypatch):
    paths = setup(mantle_home)
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    skill_root = paths.agent("developer") / "skills/docs"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text("---\nname: docs\n---\n")
    outside = tmp_path / "outside.md"
    outside.write_text("private instructions")
    (skill_root / "references").mkdir()
    (skill_root / "references/private.md").symlink_to(outside)
    config = AgentStore(paths).get("developer")
    config.resources.skills = [ResourceDefinition(name="docs", source="https://example.test/docs")]
    AgentStore(paths).save(config)

    with pytest.raises(MantleError, match="outside its isolated directory"):
        AgentLauncher(paths).build("developer", current=tmp_path)


def test_launch_rejects_nested_package_resource_symlink_escape(mantle_home, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    paths = setup(mantle_home)
    package_root = paths.agent("developer") / "npm/node_modules/demo"
    extension_dir = package_root / "extensions"
    extension_dir.mkdir(parents=True)
    outside = tmp_path / "outside.ts"
    outside.write_text("export default () => {};\n")
    (extension_dir / "nested.ts").symlink_to(outside)
    (package_root / "package.json").write_text(json.dumps({
        "name": "demo", "pi": {"extensions": ["extensions/**/*.ts"]},
    }))
    config = AgentStore(paths).get("developer")
    config.resources.packages = [ResourceDefinition(name="demo", source="npm:demo")]
    AgentStore(paths).save(config)

    with pytest.raises(MantleError, match="outside its isolated directory"):
        AgentLauncher(paths).build("developer", current=tmp_path)


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
