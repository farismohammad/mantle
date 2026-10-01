from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from mantle.agents import AgentStore
from mantle.launcher import AgentLauncher
from mantle.models import RuntimeMetadata
from mantle.paths import MantlePaths
from mantle.storage import write_json


@pytest.mark.skipif(
    os.environ.get("MANTLE_RUN_REAL_PI_TEST") != "1",
    reason="set MANTLE_RUN_REAL_PI_TEST=1 to install and verify Pi 1.0.0 in a temporary home",
)
def test_real_pi_ignores_host_and_project_resources(mantle_home, tmp_path, monkeypatch):
    paths = MantlePaths.discover()
    home = tmp_path / "runtime-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    runtime_root = paths.runtimes / "1.0.0"
    runtime_root.mkdir(parents=True)
    npm_config = tmp_path / "npmrc"
    npm_config.write_text("")
    npm_cache = tmp_path / "npm-cache"
    runtime_environment = {
        **{
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
        },
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home),
        "NPM_CONFIG_USERCONFIG": str(npm_config),
        "NPM_CONFIG_CACHE": str(npm_cache),
        "CI": "1",
    }
    install = subprocess.run(
        [
            "npm", "install", "--prefix", str(runtime_root), "--ignore-scripts",
            "--no-audit", "--no-fund", "@earendil-works/pi-coding-agent@1.0.0",
        ],
        env=runtime_environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert install.returncode == 0, install.stderr or install.stdout
    write_json(runtime_root / "metadata.json", RuntimeMetadata(version="1.0.0").model_dump(mode="json"))

    AgentStore(paths).create("developer", runtime_version="1.0.0")
    agent_root = paths.agent("developer")
    project = tmp_path / "project"
    project.mkdir()
    host_extensions = home / ".pi/agent/extensions"
    project_extensions = project / ".pi/extensions"
    host_extensions.mkdir(parents=True)
    project_extensions.mkdir(parents=True)
    agent_extensions = agent_root / "extensions"
    agent_extensions.mkdir(exist_ok=True)
    markers = {name: tmp_path / f"{name}.loaded" for name in ("host", "project", "agent")}

    for name, extension_root in (
        ("host", host_extensions),
        ("project", project_extensions),
        ("agent", agent_extensions),
    ):
        marker = json.dumps(str(markers[name]))
        (extension_root / "probe.mjs").write_text(
            'import { writeFileSync } from "node:fs";\n'
            f"export default (pi) => pi.on('session_start', () => writeFileSync({marker}, 'loaded'));\n"
        )

    spec = AgentLauncher(paths).build(
        "developer", current=project, pi_arguments=["--mode", "rpc"]
    )
    result = subprocess.run(
        spec.command,
        cwd=spec.cwd,
        env=spec.environment,
        input=json.dumps({"type": "get_state"}) + "\n",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    responses = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    state = next((line for line in responses if line.get("command") == "get_state"), None)
    assert state is not None and state["success"] is True, result.stdout
    assert state["data"]["sessionFile"].startswith(str(spec.session_dir))
    assert markers["agent"].is_file()
    assert not markers["host"].exists()
    assert not markers["project"].exists()
