from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mantle.agents import AgentStore
from mantle.credentials import CredentialStore, is_shared_link
from mantle.launcher import AgentLauncher
from mantle.models import RuntimeMetadata
from mantle.paths import MantlePaths
from mantle.storage import write_json

# Corporate proxies with their own certificate authority need these to reach npm.
CA_VARIABLES = {"NODE_EXTRA_CA_CERTS", "NPM_CONFIG_CAFILE", "SSL_CERT_FILE"}


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
            if key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"} | CA_VARIABLES
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

    # `mantle auth login --oauth` drives this Pi's login API directly instead of opening its TUI.
    from mantle.login import HELPER_INCOMPATIBLE, LOGIN_HELPER
    from mantle.runtimes import PI_PACKAGE
    login = subprocess.run(
        ["node", str(LOGIN_HELPER), str(runtime_root / "node_modules" / PI_PACKAGE), "deepseek", str(tmp_path / "auth.json")],
        env=runtime_environment, capture_output=True, text=True, timeout=60, check=False,
    )
    assert login.returncode not in (0, HELPER_INCOMPATIBLE), login.stderr
    assert 'no browser login for "deepseek"' in login.stderr

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


@pytest.fixture(scope="session")
def real_pi_runtime(tmp_path_factory):
    """Install Pi 1.0.0 once per session; tests copy it into their own MANTLE_HOME."""
    root = tmp_path_factory.mktemp("pi-runtime")
    home = root / "home"
    home.mkdir()
    (root / "npmrc").write_text("")
    environment = {
        **{key: value for key, value in os.environ.items() if key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"} | CA_VARIABLES},
        "PATH": os.environ.get("PATH", os.defpath), "HOME": str(home),
        "NPM_CONFIG_USERCONFIG": str(root / "npmrc"), "NPM_CONFIG_CACHE": str(root / "npm-cache"), "CI": "1",
    }
    runtime = root / "1.0.0"
    install = subprocess.run(
        ["npm", "install", "--prefix", str(runtime), "--ignore-scripts", "--no-audit", "--no-fund",
         "@earendil-works/pi-coding-agent@1.0.0"],
        env=environment, capture_output=True, text=True, timeout=300, check=False,
    )
    assert install.returncode == 0, install.stderr or install.stdout
    write_json(runtime / "metadata.json", RuntimeMetadata(version="1.0.0").model_dump(mode="json"))
    return runtime


@pytest.mark.skipif(
    os.environ.get("MANTLE_RUN_REAL_PI_TEST") != "1",
    reason="set MANTLE_RUN_REAL_PI_TEST=1 to install and verify Pi 1.0.0 in a temporary home",
)
def test_real_pi_agents_share_one_provider_login(mantle_home, tmp_path, monkeypatch, real_pi_runtime):
    """Two agents authenticate with a key that exists only in Mantle's shared store."""
    seen: list[str] = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            seen.append(self.headers.get("Authorization", ""))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, finish in (({"role": "assistant", "content": "SHARED_LOGIN_OK"}, None), ({}, "stop")):
                chunk = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
                         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    paths = MantlePaths.discover()
    home = tmp_path / "runtime-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    shutil.copytree(real_pi_runtime, paths.runtimes / "1.0.0", symlinks=True)
    CredentialStore(paths).set_api_key("local-test", "shared-secret-key")
    project = tmp_path / "project"
    project.mkdir()
    try:
        for name in ("first", "second"):
            config = AgentStore(paths).create(name, runtime_version="1.0.0")
            config.model.provider, config.model.name = "local-test", "m"
            AgentStore(paths).save(config)
            write_json(paths.agent(name) / "models.json", {"providers": {"local-test": {
                "baseUrl": f"http://127.0.0.1:{server.server_port}/v1", "api": "openai-completions",
                "models": [{"id": "m"}],
            }}})
            spec = AgentLauncher(paths).build(name, current=project, pi_arguments=["--print", "hello"])
            assert is_shared_link(paths, paths.agent(name))
            result = subprocess.run(spec.command, cwd=spec.cwd, env=spec.environment, capture_output=True,
                                    text=True, timeout=120, check=False)
            assert result.returncode == 0, result.stderr or result.stdout
            assert "SHARED_LOGIN_OK" in result.stdout
    finally:
        server.shutdown()
    assert seen == ["Bearer shared-secret-key", "Bearer shared-secret-key"]
    # Pi must not have replaced the link with a private copy.
    assert all(is_shared_link(paths, paths.agent(name)) for name in ("first", "second"))
