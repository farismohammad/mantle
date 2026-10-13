#!/usr/bin/env python3
"""Exercise independently installed CLIs against a disposable local registry.

Uses real npm/Git/skills/Pi processes and a deterministic local model HTTP fixture.
No provider credentials, developer Mantle state, or existing registry data are used.
Run only against an isolated registry; evidence and installations are retained.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

PI_VERSION = "1.0.0"
SKILL_SOURCE = "https://github.com/vercel-labs/agent-skills/tree/063bee94c3f4df8453406c830b0a7df0f2860278/skills/web-design-guidelines"
PACKAGE = "npm:pi-lens@4.2.1"


class ModelFixture(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def log_message(self, *_args):
        pass

    def do_GET(self):
        # A deliberately malformed successful registry response for CLI errors.
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"data":{"yaml":42,"revision":{"number":1}}}')

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(payload)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta, finish in [({"role": "assistant", "content": "MANTLE_ACCEPTANCE_OK"}, None), ({}, "stop")]:
            event = {"id": "acceptance", "object": "chat.completion.chunk", "created": int(time.time()),
                     "model": "acceptance", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-url", default="http://127.0.0.1:3317")
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--existing-cli", type=Path, help="Use an independently installed CLI (for VM follow-up checks).")
    args = parser.parse_args()
    if not args.registry_url.startswith(("http://127.0.0.1:", "http://localhost:")):
        parser.error("Only a loopback registry is allowed; use an SSH tunnel for VM tests.")
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=False)
    evidence = []
    prefix = f"acceptance-{uuid.uuid4().hex[:10]}"
    model_server = ThreadingHTTPServer(("127.0.0.1", 0), ModelFixture)
    threading.Thread(target=model_server.serve_forever, daemon=True).start()
    model_url = f"http://127.0.0.1:{model_server.server_port}/v1"

    def get(suffix):
        with urllib.request.urlopen(args.registry_url.rstrip("/") + "/api/agents" + suffix, timeout=15) as response:
            return json.load(response)["data"]

    def record(user, command, result):
        evidence.append({"user": user, "command": command, "exit": result.returncode,
                         "stdout": result.stdout, "stderr": result.stderr})
        (work / "commands.json").write_text(json.dumps(evidence, indent=2))

    environments = {}
    executables = {}
    for user in ("publisher", "consumer"):
        home = work / user
        home.mkdir()
        env = {key: value for key, value in os.environ.items()
               if key in {"PATH", "LANG", "LC_ALL", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy"}}
        env.update({"HOME": str(home), "MANTLE_HOME": str(home / ".mantle"),
                    "MANTLE_CLOUD_URL": args.registry_url, "UV_TOOL_DIR": str(home / ".local/share/uv/tools"),
                    "UV_TOOL_BIN_DIR": str(home / ".local/bin"), "UV_CACHE_DIR": str(home / ".cache/uv"),
                    "XDG_CACHE_HOME": str(home / ".cache"), "XDG_CONFIG_HOME": str(home / ".config"),
                    "XDG_DATA_HOME": str(home / ".local/share"), "npm_config_cache": str(home / ".cache/npm"),
                    "DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"})
        environments[user] = env
        if args.existing_cli:
            executables[user] = str(args.existing_cli.resolve())
        else:
            source = home / "source"
            shutil.copytree(args.source, source, ignore=shutil.ignore_patterns(
                ".git", ".venv", "dist", ".pytest_cache", "__pycache__", ".coverage", ".env", "*.egg-info"))
            command = ["sh", str(source / "install.sh")]
            print(f"Installing the CLI independently for {user}", flush=True)
            result = subprocess.run(command, env=env, cwd=source, text=True, capture_output=True, timeout=600)
            record(user, command, result)
            assert result.returncode == 0, result.stdout + result.stderr
            executables[user] = str(home / ".local/bin/mantle")
        env["PATH"] = str(home / ".local/bin") + os.pathsep + env["PATH"]
        (home / "project").mkdir()

    def run(user, *arguments, expected=0, input=None, env_extra=None):
        command = [executables[user], *arguments]
        print(user + ": mantle " + " ".join(arguments), flush=True)
        env = {**environments[user], **(env_extra or {})}
        result = subprocess.run(command, env=env, cwd=work / user / "project", input=input,
                                text=True, capture_output=True, timeout=600)
        record(user, command, result)
        assert result.returncode == expected, result.stdout + result.stderr
        assert "Traceback" not in result.stderr, result.stderr
        return result.stdout if expected == 0 else result.stderr

    def root(user, name):
        return work / user / ".mantle/agents" / name

    def configure_model(user, name):
        (root(user, name) / "models.json").write_text(json.dumps({"providers": {"acceptance": {
            "baseUrl": model_url, "api": "openai-completions", "apiKey": "local-test-fixture",
            "models": [{"id": "acceptance", "contextWindow": 32000, "maxTokens": 256}],
        }}}))

    def configuration(user, name):
        return next(config for config in json.loads(run(user, "agent", "list", "--json")) if config["agent"]["name"] == name)

    def save(user, name, config):
        (root(user, name) / "agent.yaml").write_text(json.dumps(config, indent=2) + "\n")

    def poison(user):
        home = work / user
        (home / "AGENTS.md").write_text("FORBIDDEN_PARENT_CONTEXT")
        project = home / "project"
        (project / "AGENTS.md").write_text("INTENDED_PROJECT_CONTEXT")
        for directory in (home / ".pi/agent", home / ".agents", home / ".claude", project / ".pi", project / ".agents"):
            skill = directory / "skills/forbidden-global"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: forbidden-global\ndescription: FORBIDDEN_GLOBAL_SKILL\n---\nDo not load me.\n")
            extension = directory / "extensions/forbidden.ts"
            extension.parent.mkdir(parents=True)
            extension.write_text('throw new Error("FORBIDDEN_GLOBAL_EXTENSION");\n')
            (directory / "settings.json").write_text(json.dumps({"extensions": [str(extension)]}))
            (directory / "AGENTS.md").write_text("FORBIDDEN_GLOBAL_CONTEXT")
            (directory / "SYSTEM.md").write_text("FORBIDDEN_GLOBAL_SYSTEM")

    def exercise(user, name, skill=True, project=True):
        configure_model(user, name)
        count = len(ModelFixture.requests)
        output = run(user, "agent", "run", name, *([] if project else ["--no-project-context"]), "--", "--print", "Verify this release.")
        assert "MANTLE_ACCEPTANCE_OK" in output
        assert len(ModelFixture.requests) > count, "Pi never reached the model fixture"
        request = ModelFixture.requests[-1]
        messages = json.dumps(request["messages"])
        assert "FORBIDDEN_" not in messages, messages
        assert ("INTENDED_PROJECT_CONTEXT" in messages) == project
        assert ("web-design-guidelines" in messages) == skill
        return request

    def loaded_commands(user, name):
        output = run(user, "agent", "run", name, "--", "--mode", "rpc", input='{"type":"get_commands"}\n')
        responses = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
        response = next(item for item in responses if item.get("command") == "get_commands")
        assert response["success"] is True
        return {item["name"] for item in response["data"]["commands"]}

    try:
        for user in environments:
            run(user, "--help")
            run(user, "--version")
            poison(user)
        name = prefix + "-agent"
        run("publisher", "agent", "create", name, "--runtime-version", PI_VERSION)
        config = configuration("publisher", name)
        config["model"] = {"provider": "acceptance", "name": "acceptance"}
        config["system_prompt"] = "INTENDED_AGENT_PROMPT"
        save("publisher", name, config)
        run("publisher", "skills", "add", SKILL_SOURCE, "--agent", name, "--skill", "web-design-guidelines")
        run("publisher", "skills", "add", SKILL_SOURCE, "--agent", name, "--skill", "web-design-guidelines", expected=1)
        run("publisher", "skills", "add", SKILL_SOURCE, "--agent", name, "--skill", "web-design-guidelines", "--force")
        extra_source = work / "publisher/extra-skills/acceptance-notes"
        extra_source.mkdir(parents=True)
        (extra_source / "SKILL.md").write_text("---\nname: acceptance-notes\ndescription: Write short acceptance notes.\n---\nKeep notes brief.\n")
        run("publisher", "skills", "add", str(extra_source.parent), "--agent", name, "--skill", "acceptance-notes")
        assert len(configuration("publisher", name)["resources"]["skills"]) == 2
        run("publisher", "skills", "remove", "acceptance-notes", "--agent", name)
        run("publisher", "package", "add", PACKAGE, "--agent", name)
        run("publisher", "package", "add", PACKAGE, "--agent", name, expected=1)
        exercise("publisher", name)
        run("publisher", "cloud", "push", name)
        first = get("/" + name)
        assert first["revision"]["number"] == 1
        assert "mantle cloud pull " + name in run("consumer", "agent", "run", name, expected=1)
        run("consumer", "cloud", "pull", name)
        assert (root("consumer", name) / "agent.yaml").read_text() == first["yaml"]
        exercise("consumer", name)
        exercise("consumer", name, project=False)

        # A second local agent gets its own resources and prompt, no inherited package/skills.
        other = prefix + "-other"
        run("consumer", "agent", "create", other, "--runtime-version", PI_VERSION)
        other_config = configuration("consumer", other)
        other_config["model"] = config["model"]
        other_config["system_prompt"] = "SECOND_AGENT_PROMPT"
        save("consumer", other, other_config)
        second_request = exercise("consumer", other, skill=False)
        assert "INTENDED_AGENT_PROMPT" not in json.dumps(second_request)
        assert "SECOND_AGENT_PROMPT" not in json.dumps(exercise("consumer", name))
        first_launch = json.loads(run("consumer", "agent", "run", name, "--dry-run"))
        other_launch = json.loads(run("consumer", "agent", "run", other, "--dry-run"))
        assert first_launch["command"][0] == other_launch["command"][0], "Agents duplicated the shared runtime"
        assert first_launch["session_dir"] != other_launch["session_dir"]
        assert any("pi-lens" in argument for argument in first_launch["command"])
        assert not any("pi-lens" in argument for argument in other_launch["command"])
        assert "lens-toggle" in loaded_commands("consumer", name), "Installed Pi package did not load"
        assert "skill:web-design-guidelines" in loaded_commands("consumer", name)
        assert loaded_commands("consumer", other) == set(), "A separate agent inherited resources"

        config = configuration("publisher", name)
        config["agent"]["description"] = "Second revision"
        config["permissions"] = {"filesystem": "read", "shell": "none"}
        save("publisher", name, config)
        assert get("/" + name)["yaml"] == first["yaml"], "Local edits published automatically"
        run("publisher", "cloud", "push", name)
        assert get("/" + name)["revision"]["number"] == 2
        run("publisher", "cloud", "push", name)
        assert [r["number"] for r in get("/" + name + "/revisions")] == [2, 1]
        assert get("/" + name + "/revisions/1")["yaml"] == first["yaml"]
        context = root("consumer", name) / "context/retained.md"
        context.write_text("RETAINED_LOCAL_CONTEXT")
        run("consumer", "cloud", "update", name)
        assert context.read_text() == "RETAINED_LOCAL_CONTEXT"
        assert (root("consumer", name) / "agent.yaml").read_text() == get("/" + name)["yaml"]
        request = exercise("consumer", name)
        tools = {t["function"]["name"] for t in request.get("tools", [])}
        assert not tools.intersection({"write", "edit", "bash", "powershell"}), tools

        # Routine errors must leave usable state and avoid Python tracebacks.
        run("consumer", "agent", "run", prefix + "-unknown", expected=1)
        run("consumer", "cloud", "pull", prefix + "-unknown", expected=1)
        run("consumer", "package", "add", "npm:../bad", "--agent", name, "--name", "bad", expected=1)
        run("consumer", "skills", "add", str(work / "missing-source"), "--agent", name, "--skill", "missing", expected=1)
        run("consumer", "skills", "add", SKILL_SOURCE, "--agent", name, "--skill", "missing", expected=1)
        run("consumer", "cloud", "push", name, expected=1, env_extra={"MANTLE_CLOUD_URL": "http://127.0.0.1:1"})
        malformed = run("consumer", "cloud", "pull", prefix + "-malformed", expected=1, env_extra={"MANTLE_CLOUD_URL": model_url})
        assert "invalid agent response" in malformed
        other_config["secrets"] = ["MANTLE_ABSENT_TEST_SECRET"]
        save("consumer", other, other_config)
        assert "Missing required local secret" in run("consumer", "agent", "run", other, expected=1)
        other_config["secrets"] = []
        save("consumer", other, other_config)
        valid = (root("consumer", name) / "agent.yaml").read_text()
        (root("consumer", name) / "agent.yaml").write_text("agent: [")
        run("consumer", "agent", "run", name, expected=1)
        (root("consumer", name) / "agent.yaml").write_text(valid)
        result = {"status": "PASS", "pi": PI_VERSION, "package": PACKAGE, "skill_source": SKILL_SOURCE,
                  "registry": args.registry_url, "agents": [name, other], "model": "local deterministic HTTP fixture",
                  "cli_installation": "existing CLI" if args.existing_cli else "two independent installations",
                  "checks": ["independent agent environments", "real Pi execution", "global and agent isolation", "project context",
                             "skill and package installation", "revisions", "explicit publication", "update", "tool permissions", "failure handling"]}
        (work / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2), flush=True)
    finally:
        model_server.shutdown()
        (work / "model-requests.json").write_text(json.dumps(ModelFixture.requests, indent=2))


if __name__ == "__main__":
    main()
