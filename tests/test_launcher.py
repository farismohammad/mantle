from __future__ import annotations

from mantle.agents import AgentStore
from mantle.launcher import AgentLauncher
from mantle.models import RuntimeMetadata
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
