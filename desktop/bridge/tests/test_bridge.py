"""Contract and boundary tests; all fixture folders are retained for inspection.

No real credentials, network, runtime installation, resource removal, or user
Mantle folder is touched. Mutating engine operations are replaced with mocks.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BRIDGE = Path(__file__).resolve().parents[1] / "mantle_bridge.py"
spec = importlib.util.spec_from_file_location("mantle_desktop_bridge", BRIDGE)
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)

YAML = """# Preserved author comment
mantle: '0.1'
agent:
  name: reviewer
  description: Reviews code carefully.
model:
  provider: anthropic
  name: custom-model
system_prompt: |-
  Review the project.
permissions:
  filesystem: read
  shell: none
"""


class BridgeTests(unittest.TestCase):
    def setUp(self):
        # mkdtemp is intentional: no cleanup calls or automatic deletion.
        self.fixture = Path(tempfile.mkdtemp(prefix="mantle-desktop-test-")).resolve()
        self.paths = bridge.MantlePaths(self.fixture / "home")

    def agent_fixture(self):
        root = self.paths.agents / "reviewer"
        root.mkdir(parents=True)
        (root / "agent.yaml").write_text(YAML, encoding="utf-8")
        return root

    def request(self, operation, **args):
        return bridge.dispatch({"operation": operation, **args}, self.paths)

    def resource_agent_fixture(self):
        root = self.paths.agents / "reviewer"
        root.mkdir(parents=True)
        text = YAML + "resources:\n  skills:\n    - name: review-rules\n      source: https://example.com/skills\n  packages:\n    - name: fixture-package\n      source: npm:fixture-package@1.0.0\n"
        (root / "agent.yaml").write_text(text, encoding="utf-8")
        return root

    def test_empty_snapshot_is_read_only(self):
        result = self.request("snapshot")
        self.assertEqual(result["agents"], [])
        self.assertEqual(result["runtimes"], [])
        self.assertEqual(result["secretNames"], [])
        self.assertFalse(self.paths.home.exists())
        self.assertTrue(any(p["id"] == "anthropic" for p in result["providers"]))

    def test_snapshot_preserves_configuration_and_comments(self):
        self.agent_fixture()
        result = self.request("snapshot")
        agent = result["agents"][0]
        self.assertEqual(agent["yaml"], YAML)
        self.assertEqual(agent["config"]["permissions"]["filesystem"], "read")
        self.assertEqual(agent["provider"], "anthropic")
        self.assertEqual(agent["model"], "custom-model")
        self.assertEqual(agent["digest"], bridge.digest(YAML))

    def test_validate_accepts_entire_engine_schema(self):
        text = YAML + "secrets:\n  - REGISTRY_TOKEN\nmcp:\n  servers:\n    docs:\n      url: https://example.com/mcp\n      auth: bearer\n      bearer_token_env: REGISTRY_TOKEN\n      request_timeout_ms: 10000\n"
        result = self.request("agent.validate", name="reviewer", yaml=text)
        self.assertTrue(result["valid"])
        self.assertEqual(result["config"]["mcp"]["servers"]["docs"]["request_timeout_ms"], 10000)

    def test_snapshot_declared_resources_are_missing_until_installed(self):
        root = self.resource_agent_fixture()
        result = self.request("snapshot")["agents"][0]
        self.assertEqual(result["skillCount"], 1)
        self.assertEqual(result["packageCount"], 1)
        self.assertEqual(result["installedSkills"], [])
        self.assertEqual(result["installedPackages"], [])
        self.assertFalse((root / "skills").exists())
        self.assertFalse((root / "npm").exists())

    def test_snapshot_reports_only_present_resource_paths(self):
        root = self.resource_agent_fixture()
        skill = root / "skills" / "review-rules"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("Fixture skill")
        package = root / "npm" / "node_modules" / "fixture-package"
        package.mkdir(parents=True)
        (package / "package.json").write_text('{"name":"fixture-package"}')
        result = self.request("snapshot")["agents"][0]
        self.assertEqual(result["installedSkills"], ["review-rules"])
        self.assertEqual(result["installedPackages"], ["fixture-package"])

    def test_snapshot_does_not_mark_external_symlinks_installed(self):
        root = self.resource_agent_fixture()
        outside = self.fixture / "outside-resources"
        outside.mkdir()
        (outside / "SKILL.md").write_text("Outside fixture")
        (outside / "package.json").write_text('{"name":"fixture-package"}')
        (root / "skills").mkdir()
        (root / "skills" / "review-rules").symlink_to(outside, target_is_directory=True)
        (root / "npm").mkdir()
        (root / "npm" / "fixture-package").symlink_to(outside, target_is_directory=True)
        result = self.request("snapshot")["agents"][0]
        self.assertEqual(result["installedSkills"], [])
        self.assertEqual(result["installedPackages"], [])
        self.assertEqual(len(result["resourceWarnings"]), 2)

    def test_validate_rejects_undeclared_secret(self):
        text = YAML + "mcp:\n  servers:\n    docs:\n      url: https://example.com/mcp\n      auth: bearer\n      bearer_token_env: UNDECLARED_TOKEN\n"
        with self.assertRaises(bridge.MantleError):
            self.request("agent.validate", name="reviewer", yaml=text)

    def test_name_mismatch_rejected(self):
        with self.assertRaises(bridge.MantleError):
            self.request("agent.validate", name="different", yaml=YAML)

    def test_save_requires_explicit_confirmation(self):
        root = self.agent_fixture()
        with self.assertRaisesRegex(bridge.MantleError, "confirm"):
            self.request("agent.save", name="reviewer", yaml=YAML)
        self.assertEqual((root / "agent.yaml").read_text(), YAML)

    def test_save_rejects_stale_editor(self):
        root = self.agent_fixture()
        with self.assertRaisesRegex(bridge.MantleError, "changed elsewhere"):
            self.request("agent.save", name="reviewer", yaml=YAML, confirmed=True, expectedDigest="stale")
        self.assertEqual((root / "agent.yaml").read_text(), YAML)
        self.assertFalse((self.paths.home / "backups").exists())

    def test_backup_is_exclusive_and_preserves_original(self):
        root = self.agent_fixture()
        first = bridge.backup_file(self.paths, root / "agent.yaml", "reviewer")
        second = bridge.backup_file(self.paths, root / "agent.yaml", "reviewer")
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_text(), YAML)
        self.assertEqual(Path(second).read_text(), YAML)
        self.assertEqual(Path(first).stat().st_mode & 0o777, 0o600)

    def test_confirmed_save_creates_backup_before_engine_write(self):
        root = self.agent_fixture()
        changed = YAML.replace("Reviews code carefully.", "New description.")
        # The write is mocked: this test verifies the backup and call ordering
        # without replacing or deleting an existing file.
        with patch.object(bridge, "atomic_write_text") as write:
            result = self.request("agent.save", name="reviewer", yaml=changed,
                                  expectedDigest=bridge.digest(YAML), confirmed=True)
            self.assertEqual(Path(result["backup"]).read_text(), YAML)
            write.assert_called_once_with(root / "agent.yaml", changed, mode=0o600)

    def test_destructive_agent_action_requires_confirmation(self):
        with patch.object(bridge.AgentStore, "delete") as delete:
            with self.assertRaisesRegex(bridge.MantleError, "confirm"):
                self.request("agent.delete", name="reviewer")
            delete.assert_not_called()

    def test_details_save_preserves_existing_yaml_comments(self):
        self.agent_fixture()
        changed = YAML.replace("Reviews code carefully.", "Updated description.").replace("# Preserved author comment\n", "")
        with patch.object(bridge, "atomic_write_text") as write:
            self.request("agent.save", name="reviewer", yaml=changed, confirmed=True, preserveComments=True)
            output = write.call_args.args[1]
            self.assertIn("# Preserved author comment", output)
            self.assertIn("Updated description.", output)

    def test_symlink_configuration_is_rejected(self):
        outside = self.fixture / "outside.yaml"
        outside.write_text(YAML)
        root = self.paths.agents / "reviewer"
        root.mkdir(parents=True)
        (root / "agent.yaml").symlink_to(outside)
        with self.assertRaisesRegex(bridge.MantleError, "symbolic link"):
            self.request("agent.save", name="reviewer", yaml=YAML, confirmed=True)
        self.assertEqual(outside.read_text(), YAML)

    def test_snapshot_never_returns_credential_or_secret_values(self):
        auth = self.paths.home / "auth"
        auth.mkdir(parents=True)
        (auth / "auth.json").write_text(json.dumps({"anthropic": {"type": "api_key", "key": "sensitive-test-key"}}))
        self.paths.dotenv.write_text("PRIVATE_TOKEN=private-test-value\n")
        result = self.request("snapshot")
        self.assertEqual(result["credentials"], [{"provider": "anthropic", "type": "api_key"}])
        self.assertEqual(result["secretNames"], ["PRIVATE_TOKEN"])
        serialized = json.dumps(result)
        self.assertNotIn("sensitive-test-key", serialized)
        self.assertNotIn("private-test-value", serialized)

    def test_sessions_read_and_list(self):
        root = self.agent_fixture()
        folder = root / "sessions" / "project"
        folder.mkdir(parents=True)
        entries = [
            {"type": "session", "id": "session-one", "cwd": "/example/project", "timestamp": "2026-11-01T00:00:00Z"},
            {"type": "message", "id": "m1", "message": {"role": "user", "content": [{"type": "text", "text": "Review my changes"}]}},
            {"type": "message", "id": "m2", "message": {"role": "assistant", "content": [{"type": "text", "text": "Here is the review."}]}},
        ]
        file = folder / "one.jsonl"
        file.write_text("\n".join(json.dumps(e) for e in entries))
        sessions = self.request("sessions.list", agent="reviewer")
        self.assertEqual(sessions[0]["id"], "session-one")
        self.assertEqual(sessions[0]["title"], "Review my changes")
        result = self.request("sessions.read", agent="reviewer", path=str(file))
        self.assertEqual(result["session"]["cwd"], "/example/project")
        self.assertEqual(result["messages"][1]["text"], "Here is the review.")
        self.assertFalse(result["truncated"])

    def session_fixture(self, root, text="Saved conversation"):
        folder = root / "sessions" / "project"
        folder.mkdir(parents=True)
        (folder / "one.jsonl").write_text(json.dumps({"type": "message", "id": "m1", "message": {"role": "user", "content": text}}))

    def test_deleted_agent_sessions_stay_readable_but_not_as_a_live_agent(self):
        self.session_fixture(self.agent_fixture())
        self.request("agent.delete", name="reviewer", confirmed=True, keepSessions=True)
        sessions = self.request("sessions.list")
        self.assertEqual([(s["agent"], s["retained"], s["title"]) for s in sessions], [("reviewer", True, "Saved conversation")])
        result = self.request("sessions.read", agent="reviewer", path=sessions[0]["path"], retainedFolder=sessions[0]["retainedFolder"])
        self.assertEqual(result["messages"][0]["text"], "Saved conversation")

    def test_retained_folder_cannot_escape_retained_sessions(self):
        self.agent_fixture()
        outside = self.fixture / "home" / "agents" / "reviewer" / "agent.jsonl"
        outside.write_text('{"message":{"role":"user","content":"private"}}')
        for folder in ("../agents/reviewer", "..", "reviewer/../../agents", ""):
            with self.assertRaises(bridge.MantleError):
                self.request("sessions.read", agent="reviewer", path=str(outside), retainedFolder=folder)

    def test_agent_reusing_a_name_can_still_be_deleted_and_keeps_all_sessions(self):
        self.session_fixture(self.agent_fixture(), "First agent")
        self.request("agent.delete", name="reviewer", confirmed=True, keepSessions=True)
        self.session_fixture(self.agent_fixture(), "Second agent")
        self.request("agent.delete", name="reviewer", confirmed=True, keepSessions=True)
        self.assertFalse((self.paths.agents / "reviewer").exists())
        sessions = self.request("sessions.list")
        self.assertEqual(sorted(s["title"] for s in sessions), ["First agent", "Second agent"])
        self.assertEqual({s["agent"] for s in sessions}, {"reviewer"})
        for session in sessions:
            self.assertEqual(self.request("sessions.read", agent="reviewer", path=session["path"], retainedFolder=session["retainedFolder"])["messages"][0]["text"], session["title"])

    def on_disk(self, *values):
        files = [path for path in self.paths.home.rglob("*") if path.is_file() and not path.is_symlink()]
        return sorted(value for value in values if any(value in path.read_text(errors="ignore") for path in files))

    def test_sign_out_removes_the_credential_from_every_backup(self):
        self.request("auth.saveKey", provider="openai", key="openai-first", confirmed=True)
        self.request("auth.saveKey", provider="anthropic", key="anthropic-kept", confirmed=True)
        self.request("auth.saveKey", provider="openai", key="openai-second", confirmed=True)
        self.request("auth.logout", provider="openai", confirmed=True)
        self.assertEqual(self.on_disk("openai-first", "openai-second", "anthropic-kept"), ["anthropic-kept"])

    def test_removing_a_secret_removes_its_value_from_every_backup(self):
        self.request("secrets.set", name="FIRST_TOKEN", value="first-value", confirmed=True)
        self.request("secrets.set", name="OTHER_TOKEN", value="other-value", confirmed=True)
        self.request("secrets.set", name="FIRST_TOKEN", value="second-value", confirmed=True)
        self.request("secrets.remove", name="FIRST_TOKEN", confirmed=True)
        self.assertEqual(self.on_disk("first-value", "second-value", "other-value"), ["other-value"])

    def test_only_the_latest_credential_backup_is_kept(self):
        for number in range(4):
            self.request("auth.saveKey", provider="openai", key=f"value-{number}", confirmed=True)
            self.request("secrets.set", name="TOKEN", value=f"secret-{number}", confirmed=True)
        self.assertEqual(self.on_disk("value-0", "value-1", "value-2", "value-3"), ["value-2", "value-3"])
        self.assertEqual(self.on_disk("secret-0", "secret-1", "secret-2", "secret-3"), ["secret-2", "secret-3"])

    def test_unreadable_credential_backup_is_removed_on_sign_out(self):
        backups = self.paths.home / "backups" / "credentials"
        backups.mkdir(parents=True)
        (backups / "20260101-auth.json").write_text("{not json with old-value")
        self.request("auth.logout", provider="openai", confirmed=True)
        self.assertEqual(self.on_disk("old-value"), [])

    def test_session_traversal_is_rejected(self):
        self.agent_fixture()
        outside = self.fixture / "outside.jsonl"
        outside.write_text('{"message":{"role":"user","content":"private"}}')
        with self.assertRaisesRegex(bridge.MantleError, "outside"):
            self.request("sessions.read", agent="reviewer", path=str(outside))

    def test_session_rename_after_first_prompt_is_preserved(self):
        root = self.agent_fixture()
        folder = root / "sessions" / "project"
        folder.mkdir(parents=True)
        entries = [
            {"type": "session", "id": "renamed", "cwd": "/example/project"},
            {"type": "message", "message": {"role": "user", "content": "Original prompt"}},
            {"type": "session_info", "name": "My named work"},
        ]
        (folder / "renamed.jsonl").write_text("\n".join(json.dumps(e) for e in entries))
        result = self.request("sessions.list", agent="reviewer")
        self.assertEqual(result[0]["title"], "My named work")

    def test_sensitive_errors_are_redacted(self):
        error = RuntimeError("Failed with secretABC and Bearer tokenABC")
        text = bridge.clean_error(error, {"key": "secretABC"})
        self.assertNotIn("secretABC", text)
        self.assertNotIn("tokenABC", text)

    def test_registry_rejects_wrong_service_without_saving(self):
        with patch.object(bridge, "registry_client") as make:
            make.return_value.health.return_value = {"service": "some-other-api"}
            with self.assertRaisesRegex(bridge.MantleError, "not a Mantle"):
                self.request("cloud.connect", url="https://example.com", confirmed=True)
        self.assertFalse(self.paths.config.exists())

    def test_registry_list_retains_revision_and_install_state(self):
        self.agent_fixture()
        with patch.object(bridge, "registry_client") as make:
            make.return_value.base_url = "https://registry.example"
            make.return_value.list.return_value = [{"name": "reviewer", "description": "Remote", "revision": 3}]
            result = self.request("cloud.list")
        self.assertEqual(result[0]["revision"], 3)
        self.assertTrue(result[0]["installed"])
        self.assertIn("unknown", result[0]["syncStatus"])

    def test_registry_publish_sends_only_yaml(self):
        self.agent_fixture()
        with patch.object(bridge, "registry_client") as make, patch.object(bridge, "record_sync"):
            client = make.return_value
            client.base_url = "https://registry.example"
            client.put.return_value = {"created": True, "revision": {"number": 4}}
            self.request("cloud.push", name="reviewer", confirmed=True)
            client.put.assert_called_once_with("reviewer", YAML)

    def test_registry_delete_requires_confirmation(self):
        with patch.object(bridge, "registry_client") as make:
            with self.assertRaisesRegex(bridge.MantleError, "confirm"):
                self.request("cloud.delete", name="reviewer")
            make.return_value.delete.assert_not_called()

    def test_registry_publish_rejects_stale_revision(self):
        self.agent_fixture()
        with patch.object(bridge, "registry_client") as make:
            make.return_value.latest.return_value = {"revision": {"number": 3}}
            with self.assertRaisesRegex(bridge.MantleError, "newer revision"):
                self.request("cloud.push", name="reviewer", confirmed=True, expectedRevision=2)
            make.return_value.put.assert_not_called()

    def test_runtime_remove_requires_confirmation(self):
        with patch.object(bridge.RuntimeManager, "remove") as remove:
            with self.assertRaisesRegex(bridge.MantleError, "confirm"):
                self.request("runtime.remove", version="1.0.0")
            remove.assert_not_called()

    def test_cli_preflight_rejects_mcp_destination_before_launch(self):
        from mantle.launcher import AgentLauncher
        root = self.agent_fixture()
        outside = self.fixture / "outside-extensions"
        outside.mkdir()
        (outside / "mantle-mcp.ts").write_text("original")
        (root / "extensions").symlink_to(outside, target_is_directory=True)
        reached = []

        def original_build(instance, agent_name, **kwargs):
            reached.append(agent_name)

        with patch.object(AgentLauncher, "build", original_build):
            bridge.install_cli_preflight()
            with self.assertRaisesRegex(bridge.MantleError, "symbolic link"):
                AgentLauncher(self.paths).build("reviewer", current=self.fixture)
        self.assertEqual(reached, [])
        self.assertEqual((outside / "mantle-mcp.ts").read_text(), "original")

    def test_package_mutation_preflights_nested_links_before_install(self):
        from mantle.packages import PackageManager
        root = self.agent_fixture()
        package_root = root / "npm"
        package_root.mkdir()
        outside = self.fixture / "outside-package"
        outside.mkdir()
        (package_root / "linked").symlink_to(outside, target_is_directory=True)
        with patch.object(PackageManager, "add") as add:
            with self.assertRaisesRegex(bridge.MantleError, "outside"):
                self.request("packages.add", agent="reviewer", source="npm:test")
            add.assert_not_called()

    def test_cloud_push_preflights_sync_file_before_upload(self):
        root = self.agent_fixture()
        outside = self.fixture / "outside-sync.json"
        outside.write_text("{}")
        (root / "mantle-state.json").symlink_to(outside)
        with patch.object(bridge, "registry_client") as make:
            with self.assertRaisesRegex(bridge.MantleError, "symbolic link"):
                self.request("cloud.push", name="reviewer", confirmed=True)
            make.return_value.put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
