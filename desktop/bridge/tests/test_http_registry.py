"""Real HTTP transport regression tests against an in-memory Mantle registry.

Every filesystem fixture is new and retained. The HTTP server binds only loopback,
uses an ephemeral port, and keeps revisions in memory. No real registry, runtime
installer, credentials, or user Mantle home is accessed. DELETE is never sent.
"""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import unittest

BRIDGE = Path(__file__).resolve().parents[1] / "mantle_bridge.py"
spec = importlib.util.spec_from_file_location("mantle_http_test_bridge", BRIDGE)
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)

YAML = """# exact bytes and comments survive HTTP
mantle: '0.1'
agent:
  name: reviewer
  description: Remote review agent.
permissions:
  filesystem: read
  shell: none
"""


class RegistryHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, value, status=200):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def detail(self, name, number=None):
        versions = self.server.revisions[name]
        index = len(versions) if number is None else number
        yaml = versions[index - 1]
        config = bridge.parse_agent_yaml(yaml).model_dump(mode="json")
        return {
            "name": name, "description": config["agent"]["description"],
            "revision": {"number": index, "sha256": hashlib.sha256(yaml.encode()).hexdigest(), "createdAt": "2026-11-01T05:00:00.000Z"},
            "yaml": yaml, "config": config,
        }

    def do_GET(self):
        self.server.requests.append(("GET", self.path))
        if self.path == "/v1/health":
            return self.reply({"data": {"status": "ok", "service": "mantle-cloud", "mode": "agent-repository"}})
        if self.path == "/api/agents":
            items = []
            for name in sorted(self.server.revisions):
                agent = self.detail(name)
                items.append({"name": name, "description": agent["description"], "revision": agent["revision"]["number"], "updatedAt": agent["revision"]["createdAt"], "runtime": agent["config"]["runtime"], "skillCount": 0, "packageCount": 0})
            return self.reply({"data": items})
        parts = self.path.strip("/").split("/")
        if len(parts) >= 3 and parts[:2] == ["api", "agents"] and parts[2] in self.server.revisions:
            name = parts[2]
            if len(parts) == 3:
                return self.reply({"data": self.detail(name)})
            if len(parts) == 4 and parts[3] == "revisions":
                return self.reply({"data": [self.detail(name, n)["revision"] for n in range(len(self.server.revisions[name]), 0, -1)]})
            if len(parts) == 5 and parts[3] == "revisions":
                number = int(parts[4])
                if 1 <= number <= len(self.server.revisions[name]):
                    return self.reply({"data": self.detail(name, number)})
        return self.reply({"error": {"code": "NOT_FOUND", "message": "The configuration does not exist."}}, 404)

    def do_PUT(self):
        self.server.requests.append(("PUT", self.path))
        yaml = self.rfile.read(int(self.headers["Content-Length"])).decode()
        name = self.path.rsplit("/", 1)[-1]
        bridge.parse_agent_yaml(yaml, expected_name=name)
        versions = self.server.revisions.setdefault(name, [])
        created = not versions or versions[-1] != yaml
        if created:
            versions.append(yaml)
        return self.reply({"data": {"created": created, "revision": self.detail(name)["revision"]}})


class HTTPRegistryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Path(tempfile.mkdtemp(prefix="mantle-registry-http-")).resolve()
        self.paths = bridge.MantlePaths(self.fixture / "home")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), RegistryHandler)
        self.server.revisions = {"reviewer": [YAML]}
        self.server.requests = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, operation, **parameters):
        return bridge.dispatch({"operation": operation, "url": self.url, **parameters}, self.paths)

    def local_agent(self):
        root = self.paths.agents / "reviewer"
        root.mkdir(parents=True)
        local = YAML.replace("Remote review agent.", "Local review agent.")
        (root / "agent.yaml").write_text(local, encoding="utf-8")
        return local

    def test_connect_and_list_use_real_health_and_http_envelopes(self):
        result = self.request("cloud.connect", confirmed=True)
        self.assertTrue(result["connected"])
        self.assertEqual(bridge.load_settings(self.paths).cloud_url, self.url)
        items = self.request("cloud.list")
        self.assertEqual(items[0]["name"], "reviewer")
        self.assertFalse(items[0]["installed"])
        self.assertIn(("GET", "/v1/health"), self.server.requests)
        self.assertIn(("GET", "/api/agents"), self.server.requests)

    def test_latest_history_and_revision_preserve_exact_yaml(self):
        changed = YAML.replace("Remote review agent.", "A second revision.")
        self.server.revisions["reviewer"].append(changed)
        latest = self.request("cloud.latest", name="reviewer")
        history = self.request("cloud.history", name="reviewer")
        first = self.request("cloud.revision", name="reviewer", revision=1)
        self.assertEqual(latest["yaml"], changed)
        self.assertEqual([r["number"] for r in history], [2, 1])
        self.assertEqual(first["yaml"], YAML)
        self.assertIn(("GET", "/api/agents/reviewer/revisions/1"), self.server.requests)

    def test_save_revision_and_identical_save_are_distinguished(self):
        changed = YAML.replace("Remote review agent.", "New published description.")
        result = self.request("cloud.save", name="reviewer", yaml=changed, confirmed=True, expectedRevision=1)
        unchanged = self.request("cloud.save", name="reviewer", yaml=changed, confirmed=True, expectedRevision=2)
        self.assertTrue(result["created"])
        self.assertEqual(result["revision"]["number"], 2)
        self.assertFalse(unchanged["created"])
        self.assertEqual(len(self.server.revisions["reviewer"]), 2)

    def test_stale_registry_edit_does_not_issue_put(self):
        with self.assertRaisesRegex(bridge.MantleError, "newer revision"):
            self.request("cloud.save", name="reviewer", yaml=YAML, confirmed=True, expectedRevision=99)
        self.assertFalse(any(method == "PUT" for method, _ in self.server.requests))

    def test_compare_returns_real_remote_yaml_and_local_digest(self):
        local = self.local_agent()
        result = self.request("cloud.diff", name="reviewer")
        self.assertEqual(result["local"], local)
        self.assertEqual(result["remote"], YAML)
        self.assertEqual(result["localDigest"], bridge.digest(local))
        self.assertIn("Local review agent.", result["diff"])
        self.assertIn("Remote review agent.", result["diff"])

    def test_push_publishes_exact_local_yaml_and_records_sync(self):
        local = self.local_agent()
        result = self.request("cloud.push", name="reviewer", confirmed=True)
        self.assertEqual(result["revision"]["number"], 2)
        self.assertEqual(self.server.revisions["reviewer"][-1], local)
        self.assertEqual(self.request("cloud.list")[0]["syncStatus"], "in sync")

    def test_unknown_agent_reports_actionable_error(self):
        with self.assertRaisesRegex(bridge.MantleError, "does not exist"):
            self.request("cloud.latest", name="missing")

    def test_desktop_json_process_boundary_reaches_http_registry(self):
        environment = dict(os.environ, MANTLE_HOME=str(self.paths.home), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, str(BRIDGE)],
            input=json.dumps({"operation": "cloud.list", "url": self.url}),
            text=True, capture_output=True, env=environment, timeout=15, check=True)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"][0]["name"], "reviewer")
        self.assertIn(("GET", "/api/agents"), self.server.requests)

    def test_token_refused_on_http_before_any_request(self):
        with self.assertRaisesRegex(bridge.MantleError, "HTTPS"):
            self.request("cloud.status", registryToken="test-only-gateway-token")
        self.assertEqual(self.server.requests, [])


if __name__ == "__main__":
    unittest.main()
