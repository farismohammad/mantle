"""Command-line experience: creation, next steps, runtime defaults, setup, doctor and safety prompts."""
from __future__ import annotations

import json
import sys

import yaml
from rich.text import Text

from mantle.cloud import record_sync
from mantle.credentials import CredentialStore
from mantle.paths import MantlePaths
from test_cli_workflows import cloud, external_tools  # noqa: F401  (fixtures)


def plain(result) -> str:
    """Help text without terminal styling, which Rich adds when CI forces colour."""
    return Text.from_ansi(result.stdout).plain


def test_no_arguments_shows_command_groups(cli):
    result = cli()
    assert result.exit_code == 0
    for group in ("agent", "skills", "package", "auth", "cloud", "setup", "doctor", "runtime"):
        assert f"│ {group} " in plain(result)
    for hidden in ("create", "run", "push", "secrets"):  # Grouped or shortcuts, so not listed.
        assert f"│ {hidden} " not in plain(result)
    assert plain(cli("--help")) == plain(result)


def test_group_help_lists_its_commands(cli):
    agent = cli("agent", "--help")
    assert agent.exit_code == 0
    for command in ("create", "list", "show", "run", "edit", "delete", "validate"):
        assert f"│ {command} " in plain(agent)
    for group in (("agent",), ("skills",), ("package",), ("runtime",), ("auth",), ("auth", "secrets"), ("cloud",)):
        bare = cli(*group)  # A bare group shows its help too, like bare `mantle`.
        assert bare.exit_code == 0 and "Usage:" in plain(bare), group
    assert "│ create " in plain(cli("agent"))
    cloud = plain(cli("cloud", "--help"))
    for command in ("connect", "status", "push", "pull", "update", "diff"):
        assert f"│ {command} " in cloud
    assert "│ secrets " in plain(cli("auth", "--help"))


def test_create_and_run_shortcuts(cli, mantle_home):
    created = cli("create", "demo")
    assert created.exit_code == 0, created.output
    assert "deprecated" not in created.stderr
    assert "demo" in cli("agent", "list").stdout
    run = cli("run", "demo", "--dry-run")
    assert run.exit_code == 0, run.output
    assert "deprecated" not in run.stderr
    for removed in (("list",), ("secrets", "list"), ("install", "demo"), ("remove-package", "demo", "demo")):
        assert cli(*removed).exit_code == 2, removed


def test_create_with_provider(cli, mantle_home):
    result = cli("agent", "create", "rev", "--provider", "anthropic")
    assert result.exit_code == 0, result.output
    data = yaml.safe_load((mantle_home / "agents/rev/agent.yaml").read_text())
    assert data["model"] == {"provider": "anthropic", "name": "claude-opus-4-8"}
    assert "mantle auth login anthropic" in result.stdout
    assert cli("agent", "create", "x", "--provider", "custom").exit_code == 1


def test_create_hint_disappears_once_signed_in(cli, mantle_home):
    CredentialStore(MantlePaths.discover()).set_api_key("openai", "sk-abcdef123456")
    result = cli("agent", "create", "demo")
    assert "auth login" not in result.stdout


def test_resource_commands(cli, external_tools, mantle_home):  # noqa: F811
    assert cli("agent", "create", "demo").exit_code == 0
    added = cli("skills", "add", "https://github.com/org/repo/tree/abc123/skills/docs", "--agent", "demo")
    assert added.exit_code == 0, added.output
    assert (mantle_home / "agents/demo/skills/docs/SKILL.md").is_file()
    assert yaml.safe_load((mantle_home / "agents/demo/agent.yaml").read_text())["resources"]["skills"][0]["name"] == "docs"
    assert "docs" in cli("skills", "list", "--agent", "demo").stdout
    no_name = cli("skills", "add", "https://github.com/org/repo", "-a", "demo")
    assert no_name.exit_code == 1 and "--skill NAME" in no_name.stderr
    assert cli("package", "add", "npm:demo@1.0.0", "--agent", "demo").exit_code == 0
    assert "npm:demo@1.0.0" in cli("package", "list", "--agent", "demo").stdout
    assert cli("package", "remove", "demo", "--agent", "demo").exit_code == 0
    assert cli("skills", "remove", "docs", "--agent", "demo").exit_code == 0


def test_validate_accepts_agent_names(cli, mantle_home):
    assert cli("agent", "create", "demo").exit_code == 0
    assert "Valid" in cli("agent", "validate", "demo").stdout
    assert cli("agent", "validate", "ghost").exit_code == 1


def test_runtime_latest_moves_default_and_use_switches(cli, external_tools, mantle_home):  # noqa: F811
    assert cli("runtime", "install", "1.0.0").exit_code == 0
    assert cli("agent", "create", "demo").exit_code == 0
    npm = external_tools / "npm"
    npm.write_text(npm.read_text().replace('"1.0.0"', '"2.0.0"'))
    result = cli("runtime", "install", "latest")
    assert result.exit_code == 0, result.output
    assert "pi@2.0.0 is now the default" in result.stdout and "demo" in result.stdout
    assert json.loads((mantle_home / "config.json").read_text())["default_runtime"] == "2.0.0"
    listing = cli("runtime", "list")
    assert "USED BY" in listing.stdout
    assert cli("runtime", "use", "1.0.0").exit_code == 0
    assert json.loads((mantle_home / "config.json").read_text())["default_runtime"] == "1.0.0"
    assert "runtime use" in cli("runtime", "remove", "1.0.0").stderr


def test_update_protects_unpushed_local_edits(cli, cloud, external_tools, mantle_home):  # noqa: F811
    state, _ = cloud
    assert cli("agent", "create", "demo").exit_code == 0
    assert cli("cloud", "push", "demo").exit_code == 0
    yaml_path = mantle_home / "agents/demo/agent.yaml"
    yaml_path.write_text(yaml_path.read_text().replace("description: ''", "description: local edit"))
    refused = cli("cloud", "update", "demo")
    assert refused.exit_code == 1
    assert "local changes that are not in Cloud" in refused.stderr
    assert "+  description: ''" in refused.stdout
    assert "local edit" in yaml_path.read_text()
    diff = cli("cloud", "diff", "demo")
    assert diff.exit_code == 0 and "local edit" in diff.stdout
    accepted = cli("cloud", "update", "demo", "--yes")
    assert accepted.exit_code == 0, accepted.output
    backups = list((mantle_home / "backups/demo").iterdir())
    assert len(backups) == 1 and "local edit" in backups[0].read_text()
    assert "local edit" not in yaml_path.read_text()


def test_update_without_local_edits_needs_no_confirmation(cli, cloud, external_tools, mantle_home):  # noqa: F811
    state, _ = cloud
    assert cli("agent", "create", "demo").exit_code == 0
    assert cli("cloud", "push", "demo").exit_code == 0
    changed = yaml.safe_load(state["demo"]["yaml"])
    changed["model"]["name"] = "gpt-6"
    state["demo"] = {"yaml": yaml.safe_dump(changed), "revision": {"number": 2}}
    result = cli("cloud", "update", "demo")
    assert result.exit_code == 0, result.output
    assert "model openai/gpt-5.6 → openai/gpt-6" in result.stdout


def test_install_lists_what_is_still_needed(cli, cloud, external_tools, mantle_home):  # noqa: F811
    state, _ = cloud
    config = {"agent": {"name": "demo"}, "model": {"provider": "anthropic", "name": "claude-opus-4-8"}, "secrets": ["DOCS_TOKEN"]}
    state["demo"] = {"yaml": yaml.safe_dump(config), "revision": {"number": 1}}
    result = cli("cloud", "pull", "demo")
    assert result.exit_code == 0, result.output
    assert "mantle auth login anthropic" in result.stdout
    assert "mantle auth secrets set DOCS_TOKEN" in result.stdout


def test_show_never_prints_secret_values(cli, mantle_home, monkeypatch):
    assert cli("agent", "create", "demo").exit_code == 0
    CredentialStore(MantlePaths.discover()).set_api_key("openai", "sk-very-secret-1234")
    record_sync(mantle_home / "agents/demo", "http://cloud.test", 3, (mantle_home / "agents/demo/agent.yaml").read_text())
    result = cli("agent", "show", "demo")
    assert result.exit_code == 0, result.output
    assert "shared login" in result.stdout and "revision 3" in result.stdout
    assert "sk-very-secret" not in result.output


def test_edit_validates_before_saving(cli, mantle_home, tmp_path, monkeypatch):
    assert cli("agent", "create", "demo").exit_code == 0
    path = mantle_home / "agents/demo/agent.yaml"
    before = path.read_text()
    editor = tmp_path / "editor"
    editor.write_text(f"#!{sys.executable}\nimport sys, pathlib\np = pathlib.Path(sys.argv[1])\np.write_text(p.read_text().replace('shell: all', 'shell: {{os.environ}}'.replace('{{os.environ}}', __import__('os').environ['EDIT_TO'])))\n")
    editor.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(editor))
    monkeypatch.setenv("EDIT_TO", "sometimes")
    invalid = cli("agent", "edit", "demo")
    assert invalid.exit_code == 1 and "discarded" in invalid.stderr
    assert path.read_text() == before
    monkeypatch.setenv("EDIT_TO", "none")
    assert cli("agent", "edit", "demo").exit_code == 0
    assert "shell: none" in path.read_text()
    monkeypatch.setenv("EDITOR", str(tmp_path / "missing-editor"))
    missing = cli("agent", "edit", "demo")
    assert missing.exit_code == 1 and "Could not start the editor" in missing.stderr and "Errno" not in missing.stderr


def test_edit_switches_the_model_without_an_editor(cli, mantle_home, monkeypatch):
    assert cli("agent", "create", "demo").exit_code == 0
    path = mantle_home / "agents/demo/agent.yaml"
    path.write_text("# My agent\n" + path.read_text())
    monkeypatch.setenv("EDITOR", "/nonexistent/editor")  # Never opened.
    result = cli("agent", "edit", "demo", "--provider", "anthropic")
    assert result.exit_code == 0, result.output
    assert "demo now uses anthropic/claude-opus-4-8" in result.stdout
    assert "mantle auth login anthropic" in result.stdout
    assert cli("agent", "edit", "demo", "-m", "claude-sonnet-5").exit_code == 0
    data = yaml.safe_load(path.read_text())
    assert data["model"] == {"provider": "anthropic", "name": "claude-sonnet-5"}
    assert path.read_text().startswith("# My agent\n")
    assert cli("agent", "edit", "demo", "--provider", "custom").exit_code == 1  # No default model to pick.


def test_empty_provider_or_model_is_rejected(cli, mantle_home):
    for option in ("--provider", "--model"):
        result = cli("agent", "create", "demo", option, "")
        assert result.exit_code == 1 and f"{option} must not be empty" in result.stderr
    assert not (mantle_home / "agents/demo").exists()


def test_run_hints(cli, mantle_home):
    assert cli("agent", "create", "demo").exit_code == 0
    misplaced = cli("agent", "run", "demo", "--print", "hi")
    assert misplaced.exit_code == 2 and "after --" in misplaced.output
    protected = cli("agent", "run", "demo", "--", "--tools", "read")
    assert protected.exit_code == 1 and "permissions in agent.yaml" in protected.stderr
    for subcommand, suggestion in (("list", "mantle package list --agent demo"), ("remove", "mantle package remove NAME --agent demo"),
                                   ("install", "mantle package add SOURCE --agent demo"), ("update", "--force")):
        refused = cli("agent", "run", "demo", "--", subcommand)
        assert refused.exit_code == 1 and suggestion in refused.stderr, subcommand


def test_runtime_install_defaults_and_messages(cli, external_tools, mantle_home):  # noqa: F811
    config = mantle_home / "config.json"
    kept = cli("runtime", "install", "1.0.0", "--no-default")
    assert kept.exit_code == 0, kept.output
    assert not config.exists() or json.loads(config.read_text())["default_runtime"] is None
    assert "now the default" not in kept.stdout
    assert cli("agent", "create", "demo", "--runtime-version", "1.0.0").exit_code == 0
    first = cli("runtime", "install", "1.0.0")  # Already cached; the first runtime becomes the default, visibly.
    assert "pi@1.0.0 is already downloaded." in first.stdout and "Installed runtime" not in first.stdout
    assert "pi@1.0.0 is now the default" in first.stdout
    assert json.loads(config.read_text())["default_runtime"] == "1.0.0"
    assert cli("runtime", "install", "2.0.0", "--default").exit_code == 0
    pinned = cli("runtime", "remove", "1.0.0")
    assert pinned.exit_code == 1
    assert "mantle agent edit demo" in pinned.stderr and "runtime use" not in pinned.stderr


def test_setup_with_defaults(cli, external_tools, mantle_home, monkeypatch):  # noqa: F811
    node = external_tools / "node"
    node.write_text(f"#!{sys.executable}\nprint('v22.22.0')\n")
    node.chmod(0o755)
    (external_tools / "git").write_text(f"#!{sys.executable}\nprint('git version 2.43.0')\n")
    (external_tools / "git").chmod(0o755)
    result = cli("setup", "--yes")
    assert result.exit_code == 0, result.output
    assert (mantle_home / "agents/assistant/agent.yaml").is_file()
    assert (mantle_home / "runtimes/pi/1.0.0/metadata.json").is_file()
    assert "Setup complete" in result.stdout


def test_setup_refuses_without_terminal(cli):
    result = cli("setup")
    assert result.exit_code == 2 and "interactive" in result.stderr


def test_interactive_setup_signs_in_and_creates_agent(cli, external_tools, mantle_home, monkeypatch):  # noqa: F811
    node = external_tools / "node"
    node.write_text(f"#!{sys.executable}\nprint('v22.22.0')\n")
    node.chmod(0o755)
    monkeypatch.setenv("MANTLE_INTERACTIVE", "1")
    monkeypatch.setattr("mantle.cli_auth.check_api_key", lambda provider, key: type("R", (), {"status": "valid", "detail": "ok"})())
    answers = "\n".join(["n", "y", "deepseek", "ds-key-123456", "n", "n", "y", "Bad Name", "helper", "deepseek", "deepseek-v4-pro"]) + "\n"
    result = cli("setup", input=answers)
    assert result.exit_code == 0, result.output
    assert "Agent names must" in result.stderr  # Asked again right away, before the provider and model.
    assert CredentialStore(MantlePaths.discover()).get("deepseek")["key"] == "ds-key-123456"
    assert yaml.safe_load((mantle_home / "agents/helper/agent.yaml").read_text())["model"]["provider"] == "deepseek"


def test_doctor_reports_problems_with_fixes(cli, mantle_home, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    assert cli("agent", "create", "demo").exit_code == 0
    result = cli("doctor", "--json", "--offline")
    checks = {check["name"]: check for check in json.loads(result.stdout)}
    assert result.exit_code == 1
    assert checks["Node.js"]["status"] == "fail"
    assert "mantle auth login openai" in checks["demo"]["fix"]


def test_doctor_reports_missing_packages(cli, external_tools, mantle_home):  # noqa: F811
    CredentialStore(MantlePaths.discover()).set_api_key("openai", "sk-abcdef123456")
    assert cli("agent", "create", "demo").exit_code == 0
    assert cli("package", "add", "npm:demo@1.0.0", "--agent", "demo").exit_code == 0
    assert {check["name"]: check for check in json.loads(cli("doctor", "--json", "--offline").stdout)}["demo"]["status"] == "ok"
    __import__("shutil").rmtree(mantle_home / "agents/demo/npm/node_modules/demo")
    check = {check["name"]: check for check in json.loads(cli("doctor", "--json", "--offline").stdout)}["demo"]
    assert check["status"] == "warn" and "package demo is missing" in check["detail"]
    assert check["fix"] == "mantle package add npm:demo@1.0.0 --name demo --agent demo --force"
