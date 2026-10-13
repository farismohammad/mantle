"""Shared provider logins: one credential store linked into every agent."""
from __future__ import annotations

import json
import os

import httpx
import pytest

from mantle.agents import AgentStore
from mantle.credentials import (
    PRE_SHARED_BACKUP, CredentialStore, is_shared_link, link_agent, shared_api_key_environment, shared_path,
)
from mantle.errors import MantleError
from mantle.launcher import AgentLauncher
from mantle.models import RuntimeMetadata
from mantle.paths import MantlePaths
from mantle.providers import PROVIDERS, check_api_key
from mantle.provisioning import Provisioner
from mantle.secrets import parse_dotenv, remove_dotenv_value, set_dotenv_value
from mantle.storage import write_json


def runtime(paths):
    executable = paths.runtimes / "1.0.0/node_modules/.bin/pi"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n")
    write_json(paths.runtimes / "1.0.0/metadata.json", RuntimeMetadata(version="1.0.0").model_dump(mode="json"))


def agent(paths, name, provider="openai"):
    config = AgentStore(paths).create(name, runtime_version="1.0.0")
    config.model.provider = provider
    AgentStore(paths).save(config)
    return paths.agent(name)


def declare(paths, name, *secrets):
    config = AgentStore(paths).get(name)
    config.secrets = list(secrets)
    AgentStore(paths).save(config)


def test_every_agent_links_to_one_private_store(mantle_home, tmp_path):
    paths = MantlePaths.discover()
    runtime(paths)
    first, second = agent(paths, "first"), agent(paths, "second")
    CredentialStore(paths).set_api_key("openai", "sk-shared-123456")
    for name in ("first", "second"):
        AgentLauncher(paths).build(name, current=tmp_path)
    for root in (first, second):
        assert is_shared_link(paths, root)
        assert os.readlink(root / "auth.json") == "../../auth/auth.json"
        assert json.loads((root / "auth.json").read_text())["openai"]["key"] == "sk-shared-123456"
    assert shared_path(paths).stat().st_mode & 0o777 == 0o600
    assert shared_path(paths).parent.stat().st_mode & 0o777 == 0o700


def test_a_refreshed_login_written_through_one_agent_is_seen_by_all(mantle_home, tmp_path):
    paths = MantlePaths.discover()
    runtime(paths)
    first, second = agent(paths, "first", "anthropic"), agent(paths, "second", "anthropic")
    for name in ("first", "second"):
        AgentLauncher(paths).build(name, current=tmp_path)
    # Pi writes auth.json in place through the agent's path.
    (first / "auth.json").write_text(json.dumps({"anthropic": {"type": "oauth", "access": "a", "refresh": "r", "expires": 1}}))
    assert json.loads((second / "auth.json").read_text())["anthropic"]["refresh"] == "r"


def test_existing_private_logins_are_imported_and_conflicts_backed_up(mantle_home, tmp_path):
    paths = MantlePaths.discover()
    runtime(paths)
    root = agent(paths, "legacy")
    CredentialStore(paths).set_api_key("openai", "sk-shared-123456")
    private = {"openai": {"type": "api_key", "key": "sk-private-9876"}, "google": {"type": "api_key", "key": "g-key-123456"}}
    (root / "auth.json").write_text(json.dumps(private))
    spec = AgentLauncher(paths).build("legacy", current=tmp_path)
    shared = CredentialStore(paths).load()
    assert shared["openai"]["key"] == "sk-shared-123456"
    assert shared["google"]["key"] == "g-key-123456"
    assert json.loads((root / PRE_SHARED_BACKUP).read_text()) == private
    assert any("Moved legacy's existing login for google" in notice for notice in spec.notices)
    assert any("had its own login for openai" in notice for notice in spec.notices)


def test_isolated_agent_keeps_its_own_file(cli, mantle_home, tmp_path):
    paths = MantlePaths.discover()
    runtime(paths)
    root = agent(paths, "private")
    CredentialStore(paths).set_api_key("openai", "sk-shared-123456")
    assert cli("auth", "isolate", "private").exit_code == 0
    AgentLauncher(paths).build("private", current=tmp_path)
    assert not (root / "auth.json").is_symlink()
    assert json.loads((root / "auth.json").read_text()) == {}
    assert cli("auth", "share", "private").exit_code == 0
    assert is_shared_link(paths, root)


def test_link_to_anything_but_the_store_is_rejected(mantle_home, tmp_path):
    paths = MantlePaths.discover()
    root = agent(paths, "demo")
    outside = tmp_path / "elsewhere.json"
    outside.write_text("{}")
    (root / "auth.json").unlink()
    (root / "auth.json").symlink_to(outside)
    with pytest.raises(MantleError, match="outside its isolated directory"):
        link_agent(paths, root)


def test_invalid_private_file_is_left_alone(mantle_home):
    paths = MantlePaths.discover()
    root = agent(paths, "demo")
    (root / "auth.json").write_text('{"key": "not a Pi credential"}')
    result = link_agent(paths, root)
    assert result.mode == "private"
    assert (root / "auth.json").read_text() == '{"key": "not a Pi credential"}'


def test_update_keeps_the_link_not_a_copy(mantle_home):
    paths = MantlePaths.discover()
    root = agent(paths, "demo")
    CredentialStore(paths).set_api_key("openai", "sk-shared-123456")
    link_agent(paths, root)

    class Runtimes:
        def ensure(self, version, **_):
            return RuntimeMetadata(version="1.0.0")

    Provisioner(paths, runtime_manager=Runtimes()).update(AgentStore(paths).get("demo"))
    assert is_shared_link(paths, root)


def test_shared_api_keys_satisfy_declared_provider_secrets(mantle_home, tmp_path, monkeypatch):
    paths = MantlePaths.discover()
    runtime(paths)
    agent(paths, "demo")
    config = AgentStore(paths).get("demo")
    config.secrets = ["OPENAI_API_KEY"]
    AgentStore(paths).save(config)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(MantleError, match="mantle auth login openai"):
        AgentLauncher(paths).build("demo", current=tmp_path)
    CredentialStore(paths).set_api_key("openai", "sk-shared-123456")
    assert shared_api_key_environment(paths, paths.agent("demo")) == {"OPENAI_API_KEY": "sk-shared-123456"}
    spec = AgentLauncher(paths).build("demo", current=tmp_path)
    assert spec.environment["OPENAI_API_KEY"] == "sk-shared-123456"


def test_status_commands_agree_that_isolated_agents_get_no_shared_api_keys(cli, mantle_home, monkeypatch):
    paths = MantlePaths.discover()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("COLUMNS", "200")  # Keep each `auth secrets list` row on one line.
    CredentialStore(paths).set_api_key("openai", "sk-shared-123456")
    for name in ("shared", "iso"):
        agent(paths, name)
        declare(paths, name, "OPENAI_API_KEY")
    assert cli("auth", "isolate", "iso").exit_code == 0
    assert shared_api_key_environment(paths, paths.agent("iso")) == {}
    assert shared_api_key_environment(paths, None) == {"OPENAI_API_KEY": "sk-shared-123456"}

    shared, isolated = (json.loads(cli("agent", "show", name, "--json").stdout) for name in ("shared", "iso"))
    assert (shared["credentials"], shared["secrets"]) == ("shared login", {"OPENAI_API_KEY": "set"})
    assert isolated["credentials"] == "missing → mantle auth secrets set OPENAI_API_KEY"
    assert isolated["secrets"] == {"OPENAI_API_KEY": "missing"}
    doctor = {check["name"]: check for check in json.loads(cli("doctor", "--json", "--offline").stdout)}
    assert doctor["shared"]["status"] == "ok"
    assert doctor["iso"]["status"] == "warn" and "missing secret OPENAI_API_KEY" in doctor["iso"]["detail"]
    assert doctor["iso"]["fix"] == "mantle auth secrets set OPENAI_API_KEY"
    hints = cli("agent", "edit", "iso", "--model", "gpt-5.6").stdout
    assert "mantle auth secrets set OPENAI_API_KEY" in hints and "auth login" not in hints
    assert "from the shared openai login; missing for iso (isolated)" in cli("auth", "secrets", "list").stdout
    declare(paths, "shared")
    listing = cli("auth", "secrets", "list").stdout
    assert "missing for iso (isolated)" in listing and "from the shared" not in listing

    assert cli("auth", "secrets", "set", "OPENAI_API_KEY", "--value-stdin", input="sk-dotenv-9876\n").exit_code == 0
    isolated = json.loads(cli("agent", "show", "iso", "--json").stdout)
    assert (isolated["credentials"], isolated["secrets"]) == ("via declared secret OPENAI_API_KEY", {"OPENAI_API_KEY": "set"})
    doctor = {check["name"]: check for check in json.loads(cli("doctor", "--json", "--offline").stdout)}
    assert doctor["iso"]["status"] == "ok"


def test_an_isolated_agents_own_login_does_not_provide_declared_secrets(cli, mantle_home, monkeypatch):
    paths = MantlePaths.discover()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    agent(paths, "iso")
    declare(paths, "iso", "OPENAI_API_KEY")
    CredentialStore(paths).set_api_key("openai", "sk-shared-123456")
    assert cli("auth", "isolate", "iso", "--copy-credentials").exit_code == 0
    shown = json.loads(cli("agent", "show", "iso", "--json").stdout)
    assert (shown["credentials"], shown["secrets"]) == ("isolated logins", {"OPENAI_API_KEY": "missing"})
    check = {check["name"]: check for check in json.loads(cli("doctor", "--json", "--offline").stdout)}["iso"]
    assert check["detail"].endswith("missing secret OPENAI_API_KEY")
    assert check["fix"] == "Set it with: mantle auth secrets set OPENAI_API_KEY"


def test_isolated_agents_without_credentials_are_told_to_sign_in_inside_pi(cli, mantle_home, tmp_path, monkeypatch):
    paths = MantlePaths.discover()
    runtime(paths)
    agent(paths, "iso")
    CredentialStore(paths).set_api_key("openai", "sk-shared-123456")
    assert cli("auth", "isolate", "iso").exit_code == 0
    monkeypatch.setenv("OPENAI_API_KEY", "sk-shell-4321")
    fix = "mantle agent run iso, then sign in with /login"
    assert json.loads(cli("agent", "show", "iso", "--json").stdout)["credentials"] == f"missing → {fix}"
    doctor = {check["name"]: check for check in json.loads(cli("doctor", "--json", "--offline").stdout)}
    assert doctor["iso"]["fix"] == fix
    notice = AgentLauncher(paths).build("iso", current=tmp_path).notices[0]
    assert "Sign in inside Pi with /login" in notice
    assert "List it under secrets with: mantle agent edit iso" in notice
    assert "auth login" not in notice


def test_missing_provider_credentials_produce_a_notice(mantle_home, tmp_path, monkeypatch):
    paths = MantlePaths.discover()
    runtime(paths)
    agent(paths, "demo", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    spec = AgentLauncher(paths).build("demo", current=tmp_path)
    assert "mantle auth login anthropic" in spec.notices[0]
    assert "--from-env" in spec.notices[0]
    assert "ANTHROPIC_API_KEY" not in spec.environment


def test_auth_login_from_stdin_list_and_logout(cli, mantle_home):
    paths = MantlePaths.discover()
    result = cli("auth", "login", "openai", "--key-stdin", "--no-verify", input="sk-test-abcdef1234\n")
    assert result.exit_code == 0, result.output
    assert "…1234" in result.stdout
    assert "sk-test-abcdef1234" not in result.output
    assert CredentialStore(paths).get("openai") == {"type": "api_key", "key": "sk-test-abcdef1234"}
    listing = cli("auth", "list", "--json")
    assert json.loads(listing.stdout)["credentials"][0]["provider"] == "openai"
    assert "sk-test-abcdef1234" not in listing.output
    assert cli("auth", "logout", "openai", "--yes").exit_code == 0
    assert CredentialStore(paths).get("openai") is None


def test_auth_login_from_env_and_rejected_key(cli, mantle_home, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-from-shell-5678")
    assert cli("auth", "login", "deepseek", "--from-env", "--no-verify").exit_code == 0
    monkeypatch.setattr("mantle.cli_auth.check_api_key", lambda provider, key: type("R", (), {"status": "invalid", "detail": "OpenAI rejected the key (HTTP 401)."})())
    result = cli("auth", "login", "openai", "--key-stdin", input="bad-key-000000\n")
    assert result.exit_code == 1
    assert "Nothing was saved" in result.stderr
    assert CredentialStore(MantlePaths.discover()).get("openai") is None


def test_auth_login_needs_a_method_when_not_interactive(cli, mantle_home):
    result = cli("auth", "login", "openai")
    assert result.exit_code == 1
    assert "--key-stdin" in result.stderr


@pytest.mark.parametrize("status,expected", [(200, "valid"), (401, "invalid"), (403, "invalid"), (500, "unknown")])
def test_key_check_classifies_provider_responses(status, expected):
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(status)

    result = check_api_key(PROVIDERS["anthropic"], "sk-ant", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert result.status == expected
    assert seen["x-api-key"] == "sk-ant"


def test_key_check_network_failure_is_not_fatal():
    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    result = check_api_key(PROVIDERS["openai"], "sk", client=httpx.Client(transport=httpx.MockTransport(offline)))
    assert result.status == "unknown"


def test_dotenv_writer_preserves_other_lines_and_is_private(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# comment\nKEEP=1\nDOCS_TOKEN=old\n")
    set_dotenv_value(path, "DOCS_TOKEN", "new value")
    set_dotenv_value(path, "OTHER", "x")
    assert parse_dotenv(path) == {"KEEP": "1", "DOCS_TOKEN": "new value", "OTHER": "x"}
    assert path.read_text().startswith("# comment\n")
    assert path.stat().st_mode & 0o777 == 0o600
    assert remove_dotenv_value(path, "OTHER")
    assert "OTHER" not in parse_dotenv(path)
    with pytest.raises(MantleError):
        set_dotenv_value(path, "bad-name", "x")


def test_secrets_commands(cli, mantle_home):
    assert cli("auth", "secrets", "set", "DOCS_TOKEN", "--value-stdin", input="token-value\n").exit_code == 0
    listing = cli("auth", "secrets", "list")
    assert "DOCS_TOKEN" in listing.stdout
    assert "token-value" not in listing.output
    assert cli("auth", "secrets", "remove", "DOCS_TOKEN").exit_code == 0
    assert cli("auth", "secrets", "remove", "DOCS_TOKEN").exit_code == 1


FAKE_PI_LOGIN = """
import { writeFileSync } from "node:fs";
export class ModelRuntime {
    static async create(options) { return Object.assign(new ModelRuntime(), { authPath: options.authPath }); }
    getProvider(id) { return id === "anthropic" ? { auth: { oauth: {} } } : undefined; }
    async login(id, type, interaction) {
        const method = await interaction.prompt({ type: "select", message: "Method", options: [
            { id: "browser", label: "Browser" }, { id: "code", label: "Copy code" }] });
        interaction.notify({ type: "progress", message: `method ${method}` });
        const code = await interaction.prompt({ type: "manual_code", message: "Paste the code" });
        writeFileSync(this.authPath, JSON.stringify({ [id]: { type: "oauth", access: code, refresh: "r", expires: 1 } }));
    }
}
"""


def login_runtime(paths, tmp_path, index_js):
    from mantle.runtimes import PI_PACKAGE, RuntimeManager
    runtime(paths)
    RuntimeManager(paths).set_default("1.0.0")
    package = paths.runtimes / "1.0.0/node_modules" / PI_PACKAGE / "dist"
    package.mkdir(parents=True)
    (package / "index.js").write_text(index_js)
    (package.parent / "package.json").write_text('{"type": "module"}')
    # Opening Pi's TUI is the fallback; it signs in by writing the shared file directly.
    marker = tmp_path / "pi-opened"
    pi = paths.runtimes / "1.0.0/node_modules/.bin/pi"
    pi.write_text(f'#!/bin/sh\ntouch "{marker}"\n'
                  'printf \'{"anthropic": {"type": "oauth", "access": "from-pi", "refresh": "r", "expires": 1}}\' '
                  '>"$PI_CODING_AGENT_DIR/auth.json"\n')
    pi.chmod(0o755)
    return marker


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="Node.js is not installed")
def test_browser_login_runs_without_opening_pi(mantle_home, tmp_path, monkeypatch, capfd):
    import subprocess
    from mantle import login
    paths = MantlePaths.discover()
    marker = login_runtime(paths, tmp_path, FAKE_PI_LOGIN)
    real_run = subprocess.run
    monkeypatch.setattr(login.subprocess, "run", lambda *a, **k: real_run(*a, **k, input="2\nthe-code\n", text=True))
    assert login.oauth_login(paths, "anthropic") == "saved"
    assert CredentialStore(paths).get("anthropic")["access"] == "the-code"
    assert not marker.exists()
    output = capfd.readouterr().out
    assert "1. Browser" in output and "method code" in output


@pytest.mark.parametrize("node", [True, False])
def test_browser_login_falls_back_to_pi_when_it_cannot_run_directly(mantle_home, tmp_path, monkeypatch, node):
    import shutil
    from mantle import login
    paths = MantlePaths.discover()
    if not node:
        monkeypatch.setattr(login.shutil, "which", lambda *a, **k: None)
    elif not shutil.which("node"):
        pytest.skip("Node.js is not installed")
    # A Pi version without ModelRuntime cannot sign in outside its TUI.
    marker = login_runtime(paths, tmp_path, "export const VERSION = '0.9.0';\n")
    assert login.oauth_login(paths, "anthropic") == "saved"
    assert marker.exists()
    assert CredentialStore(paths).get("anthropic")["access"] == "from-pi"


def test_new_agents_default_to_the_provider_you_are_signed_in_to(cli, mantle_home):
    paths = MantlePaths.discover()
    store = CredentialStore(paths)
    store.merge({"openai-codex": {"type": "oauth", "access": "a", "refresh": "r", "expires": 1}})
    created = cli("agent", "create", "first")
    assert created.exit_code == 0, created.output
    assert "openai-codex/gpt-6.1-sol" in created.stdout and "provider you are signed in to" in created.stdout
    assert AgentStore(paths).get("first").model.provider == "openai-codex"
    store.set_api_key("openai", "sk-shared-123456")
    assert AgentStore(paths).create("unused").model.provider == "openai"  # The library default is unchanged.
    assert cli("agent", "create", "second").exit_code == 0
    assert AgentStore(paths).get("second").model.provider == "openai"  # OpenAI stays the default once signed in.


def test_a_login_for_another_provider_is_offered_where_credentials_are_missing(cli, mantle_home, tmp_path):
    paths = MantlePaths.discover()
    runtime(paths)
    agent(paths, "spec", "openai")
    CredentialStore(paths).merge({"openai-codex": {"type": "oauth", "access": "a", "refresh": "r", "expires": 1}})
    switch = "mantle agent edit spec --provider openai-codex"
    shown = cli("agent", "show", "spec")
    assert "missing → mantle auth login openai" in shown.stdout and switch in shown.stdout
    notice = AgentLauncher(paths).build("spec", current=tmp_path).notices[0]
    assert "separate provider from openai" in notice and switch in notice
    doctor = {check["name"]: check for check in json.loads(cli("doctor", "--json", "--offline").stdout)}
    assert switch in doctor["spec"]["fix"]
    switched = cli("agent", "edit", "spec", "--provider", "openai-codex")
    assert switched.exit_code == 0, switched.output
    assert AgentStore(paths).get("spec").model.name == "gpt-6.1-sol"
    assert "shared login" in cli("agent", "show", "spec").stdout


def test_login_names_agents_set_to_other_providers(cli, mantle_home):
    paths = MantlePaths.discover()
    agent(paths, "spec", "openai")
    result = cli("auth", "login", "deepseek", "--key-stdin", "--no-verify", input="ds-key-123456\n")
    assert result.exit_code == 0, result.output
    assert "spec (openai)" in result.stdout
    assert "mantle agent edit spec --provider deepseek" in result.stdout


def test_an_unreadable_agent_auth_file_is_reported_and_can_be_set_aside(cli, mantle_home):
    paths = MantlePaths.discover()
    root = agent(paths, "badauth")
    CredentialStore(paths).set_api_key("openai", "sk-shared-123456")
    (root / "auth.json").write_text("not json")
    shown = cli("agent", "show", "badauth", "--json")
    assert json.loads(shown.stdout)["credentials"].startswith("unusable:")
    check = {check["name"]: check for check in json.loads(cli("doctor", "--json", "--offline").stdout)}["badauth"]
    assert check["status"] == "warn" and check["fix"] == "mantle auth share badauth"
    shared = cli("auth", "share", "badauth")
    assert shared.exit_code == 0, shared.output
    assert is_shared_link(paths, root)
    assert (root / "auth.json.invalid").read_text() == "not json"
    assert json.loads(cli("agent", "show", "badauth", "--json").stdout)["credentials"] == "shared login"


def test_custom_provider_credentials_are_not_claimed(cli, mantle_home):
    assert cli("agent", "create", "local", "--provider", "local", "--model", "your-local-model").exit_code == 0
    shown = json.loads(cli("agent", "show", "local", "--json").stdout)
    assert shown["credentials"].startswith("not checked: local is a custom provider")
    CredentialStore(MantlePaths.discover()).set_api_key("local", "local-key-123456")
    assert json.loads(cli("agent", "show", "local", "--json").stdout)["credentials"] == "shared login"


def test_provider_key_secrets_satisfied_by_the_shared_login_are_listed_as_set(cli, mantle_home):
    paths = MantlePaths.discover()
    agent(paths, "demo", "anthropic")
    config = AgentStore(paths).get("demo")
    config.secrets = ["ANTHROPIC_API_KEY"]
    AgentStore(paths).save(config)
    assert "missing" in cli("auth", "secrets", "list").stdout
    CredentialStore(paths).set_api_key("anthropic", "sk-ant-123456")
    listing = cli("auth", "secrets", "list").stdout
    assert "from the shared anthropic login" in listing and "missing" not in listing


def test_provider_picker_rejects_numbers_outside_the_list(cli, mantle_home, monkeypatch):
    monkeypatch.setenv("MANTLE_INTERACTIVE", "1")
    result = cli("auth", "login", "--no-verify", input="99\n1\n1\nsk-ant-abcdef654321\n")
    assert result.exit_code == 0, result.output
    assert "Choose a number from 1 to 10" in result.stderr
    assert CredentialStore(MantlePaths.discover()).load() == {"anthropic": {"type": "api_key", "key": "sk-ant-abcdef654321"}}


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="Node.js is not installed")
@pytest.mark.parametrize("error,shown", [("Login cancelled", "Sign-in cancelled"), ("token exchange failed", "Sign-in failed")])
def test_an_abandoned_browser_login_is_not_reported_as_success(cli, mantle_home, tmp_path, monkeypatch, capfd, error, shown):
    paths = MantlePaths.discover()
    CredentialStore(paths).set_api_key("anthropic", "sk-ant-existing-1234")
    failing = FAKE_PI_LOGIN.replace("async login(id, type, interaction) {", f"async login() {{ throw new Error({error!r}); }}\n    async unused(id, type, interaction) {{")
    login_runtime(paths, tmp_path, failing)
    monkeypatch.setenv("MANTLE_INTERACTIVE", "1")
    result = cli("auth", "login", "anthropic", "--oauth")
    assert result.exit_code == 1
    assert "No new Anthropic login was saved. Your existing login is unchanged." in result.stderr
    assert "✔" not in result.stdout
    assert shown in capfd.readouterr().err
    assert CredentialStore(paths).get("anthropic") == {"type": "api_key", "key": "sk-ant-existing-1234"}
