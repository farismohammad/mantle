"""`mantle cloud start/stop/logs`: running Mantle Cloud locally through Docker Compose."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mantle import cloud_server
from mantle.cloud import ProbeResult
from mantle.errors import MantleError
from mantle.paths import MantlePaths


class FakeDocker:
    """Answers the docker commands Mantle runs and records them."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.environments: list[dict[str, str]] = []
        self.container: str | None = None
        self.info_error = ""
        self.compose_missing = False
        self.pull_error = ""
        self.image_present = True
        self.up_error = ""
        self.crash = False

    def compose_commands(self) -> list[str]:
        return [call[6] for call in self.calls if call[1:2] == ["compose"] and len(call) > 6]

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        self.environments.append(kwargs.get("env") or {})
        args = argv[1:]

        def done(code=0, stdout="", stderr=""):
            return subprocess.CompletedProcess(argv, code, stdout, stderr)

        if args[0] == "info":
            return done(1, stderr=self.info_error) if self.info_error else done(stdout="28.2.2\n")
        if args[:2] == ["compose", "version"]:
            return done(1, stderr="unknown command: docker compose") if self.compose_missing else done()
        if args[0] == "ps":
            return done(stdout=f"{self.container}\n" if self.container else "")
        if args[:2] == ["image", "inspect"]:
            return done(0 if self.image_present else 1)
        command = args[5]
        if command == "pull":
            return done(1, stderr=self.pull_error) if self.pull_error else done()
        if command == "up":
            if self.up_error:
                return done(1, stderr=self.up_error)
            changed = self.container != "running"
            self.container = "restarting" if self.crash else "running"
            return done(stderr=" Container mantle-cloud-1  " + ("Started" if changed else "Running"))
        if command == "stop":
            self.container = "exited"
            return done()
        if command == "down":
            self.container = None
            return done()
        if command == "logs":
            return done(stdout="mantle-cloud-1  | Ready\n")
        raise AssertionError(f"unexpected docker command: {argv}")


@pytest.fixture
def docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(cloud_server.subprocess, "run", fake)
    monkeypatch.setattr(cloud_server.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(cloud_server, "port_in_use", lambda port: False)
    monkeypatch.delenv("MANTLE_CLOUD_IMAGE", raising=False)
    monkeypatch.setenv("MANTLE_INTERACTIVE", "0")

    def answers(paths, url, **kwargs):
        return ProbeResult(url, fake.container == "running", "Connected" if fake.container == "running" else "refused", agents=0)
    monkeypatch.setattr("mantle.cloud_server.probe", answers)
    monkeypatch.setattr("mantle.cli_cloud.probe", answers)
    monkeypatch.setattr("mantle.cloud.probe", answers)
    return fake


def saved_url(home: Path) -> str | None:
    return json.loads((home / "config.json").read_text()).get("cloud_url")


def test_project_name_is_per_mantle_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cloud_server.project_name(MantlePaths((tmp_path / ".mantle").resolve())) == "mantle-cloud"
    first = cloud_server.project_name(MantlePaths(tmp_path / "one"))
    second = cloud_server.project_name(MantlePaths(tmp_path / "two"))
    assert first.startswith("mantle-cloud-") and first != second


def test_resolve_defaults_previous_port_and_overrides(mantle_home, monkeypatch):
    paths = MantlePaths.discover()
    monkeypatch.delenv("MANTLE_CLOUD_IMAGE", raising=False)
    local = cloud_server.resolve(paths)
    assert (local.port, local.reference) == (3100, "ghcr.io/farismohammad/mantle-web:0.1")
    cloud_server.write_compose(paths)
    cloud_server.save(paths, cloud_server.resolve(paths, port=3200, version="0.1.0"))
    again = cloud_server.resolve(paths)
    assert again.port == 3200 and again.version == "0.1"  # The port is remembered; the version is not.
    monkeypatch.setenv("MANTLE_CLOUD_IMAGE", "localhost:5000/mantle-cloud")
    assert cloud_server.resolve(paths).reference == "localhost:5000/mantle-cloud:0.1"
    with pytest.raises(MantleError, match="not a valid image version"):
        cloud_server.resolve(paths, version="bad tag")


def test_compose_template_matches_published_cloud_file():
    published = Path(__file__).resolve().parents[2] / "mantle-web/docker-compose.yml"
    if not published.is_file():
        pytest.skip("Companion Mantle Cloud checkout is not present")

    def body(path: Path) -> list[str]:
        return [line for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]
    assert body(cloud_server.COMPOSE_TEMPLATE) == body(published)


def test_start_runs_connects_and_prints_the_address(cli, docker, mantle_home):
    result = cli("cloud", "start")
    assert result.exit_code == 0, result.output
    assert "Open it in your browser: http://localhost:3100" in result.stdout
    assert "Connected the CLI to it" in result.stdout
    assert saved_url(mantle_home) == "http://localhost:3100"
    assert docker.compose_commands() == ["pull", "up"]
    assert docker.environments[-1]["MANTLE_CLOUD_VERSION"] == "0.1"
    env = (mantle_home / "cloud/.env").read_text()
    assert "MANTLE_PORT=3100" in env and "COMPOSE_PROJECT_NAME=mantle-cloud-" in env
    assert (mantle_home / "cloud/compose.yaml").read_text() == cloud_server.COMPOSE_TEMPLATE.read_text()

    again = cli("cloud", "start")
    assert "already running" in again.stdout and "The CLI is connected to it." in again.stdout


def test_start_with_port_and_version(cli, docker, mantle_home):
    result = cli("cloud", "start", "--port", "3200", "--version", "0.1.0")
    assert result.exit_code == 0, result.output
    assert "http://localhost:3200" in result.stdout and "mantle-web:0.1.0" in result.stdout
    assert docker.environments[-1]["MANTLE_PORT"] == "3200"


@pytest.mark.parametrize("stderr,message", [
    ("Error response from daemon: manifest unknown", "does not exist or is not public"),
    ("error from registry: denied", "does not exist or is not public"),
    ("dial tcp: lookup ghcr.io: no such host", "could not be reached"),
])
def test_failed_pull_without_a_local_image_changes_nothing(cli, docker, mantle_home, stderr, message):
    docker.pull_error, docker.image_present = stderr, False
    result = cli("cloud", "start", "--version", "9.9.9")
    assert result.exit_code == 1 and message in result.stderr
    if "public" in message:
        assert "docker login ghcr.io" in result.stderr
    assert "up" not in docker.compose_commands()
    assert not cloud_server.managed(MantlePaths.discover())


def test_failed_pull_uses_the_local_image(cli, docker, mantle_home):
    docker.pull_error = "dial tcp: i/o timeout"
    result = cli("cloud", "start")
    assert result.exit_code == 0, result.output
    assert "Using the copy on this machine" in result.stderr


def test_port_used_by_another_program(cli, docker, mantle_home, monkeypatch):
    monkeypatch.setattr(cloud_server, "port_in_use", lambda port: True)
    result = cli("cloud", "start", "--port", "3300")
    assert result.exit_code == 1 and "mantle cloud start --port 3301" in result.stderr
    docker.up_error = "Bind for 127.0.0.1:3300 failed: port is already allocated"
    monkeypatch.setattr(cloud_server, "port_in_use", lambda port: False)
    assert "already used by another program" in cli("cloud", "start", "--port", "3300").stderr


def test_existing_mantle_cloud_on_the_port_is_used_as_is(cli, docker, mantle_home, monkeypatch):
    monkeypatch.setattr(cloud_server, "port_in_use", lambda port: True)
    monkeypatch.setattr("mantle.cli_cloud.probe", lambda paths, url, **kwargs: ProbeResult(url, True, "Connected"))
    result = cli("cloud", "start")
    assert result.exit_code == 0, result.output
    assert "not started by `mantle cloud start`" in result.stdout
    assert saved_url(mantle_home) == "http://localhost:3100"
    assert docker.compose_commands() == [] and not (mantle_home / "cloud").exists()


def test_start_asks_before_replacing_another_registry(cli, docker, mantle_home, monkeypatch):
    mantle_home.mkdir(parents=True)
    (mantle_home / "config.json").write_text(json.dumps({"cloud_url": "https://mantle.example.com"}))
    kept = cli("cloud", "start")
    assert kept.exit_code == 0 and "The CLI still uses https://mantle.example.com" in kept.stdout
    assert saved_url(mantle_home) == "https://mantle.example.com"
    monkeypatch.setenv("MANTLE_INTERACTIVE", "1")
    assert "Kept https://mantle.example.com" in cli("cloud", "start", input="n\n").stdout
    assert saved_url(mantle_home) == "https://mantle.example.com"
    assert cli("cloud", "start", input="y\n").exit_code == 0
    assert saved_url(mantle_home) == "http://localhost:3100"


def test_start_yes_and_no_connect(cli, docker, mantle_home):
    mantle_home.mkdir(parents=True)
    (mantle_home / "config.json").write_text(json.dumps({"cloud_url": "https://mantle.example.com"}))
    assert cli("cloud", "start", "--no-connect").exit_code == 0
    assert saved_url(mantle_home) == "https://mantle.example.com"
    assert cli("cloud", "start", "--yes").exit_code == 0
    assert saved_url(mantle_home) == "http://localhost:3100"


def test_start_follows_its_own_port_change(cli, docker, mantle_home):
    assert cli("cloud", "start").exit_code == 0
    result = cli("cloud", "start", "--port", "3200")
    assert result.exit_code == 0 and "Connected the CLI to it" in result.stdout
    assert saved_url(mantle_home) == "http://localhost:3200"


def test_crash_while_starting_stops_the_restart_loop(cli, docker, mantle_home):
    docker.crash = True
    result = cli("cloud", "start")
    assert result.exit_code == 1
    assert "crashed while starting" in result.stderr and "mantle-cloud-1  | Ready" in result.stderr
    assert docker.compose_commands()[-1] == "stop" and docker.container == "exited"


@pytest.mark.parametrize("setup,message", [
    ({"which": None}, "Docker is not installed"),
    ({"info_error": "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"},
     "not running"),
    ({"info_error": "permission denied while trying to connect to the Docker daemon socket"}, "usermod -aG docker"),
    ({"compose_missing": True}, "Docker Compose v2"),
])
def test_docker_problems_explain_the_fix(cli, docker, mantle_home, monkeypatch, setup, message):
    if "which" in setup:
        monkeypatch.setattr(cloud_server.shutil, "which", lambda name: None)
    docker.info_error = setup.get("info_error", "")
    docker.compose_missing = setup.get("compose_missing", False)
    result = cli("cloud", "start")
    assert result.exit_code == 1 and message in result.stderr
    assert not cloud_server.docker_available()


def test_docker_timeout_is_explained(monkeypatch):
    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    monkeypatch.setattr(cloud_server.subprocess, "run", slow)
    with pytest.raises(MantleError, match="did not answer within 30 seconds"):
        cloud_server.container_state(MantlePaths.discover())


def test_stop_and_logs_before_any_start(cli, docker, mantle_home):
    stopped = cli("cloud", "stop")
    assert stopped.exit_code == 0 and "nothing to stop" in stopped.stdout
    logs = cli("cloud", "logs")
    assert logs.exit_code == 1 and "no logs to show" in logs.stderr
    assert docker.calls == []


def test_stop_keeps_data_and_logs_need_a_container(cli, docker, mantle_home):
    assert cli("cloud", "start").exit_code == 0
    logs = cli("cloud", "logs", "-n", "5", "--follow")
    assert logs.exit_code == 0 and "localhost:3100" in logs.stderr
    assert docker.calls[-1][-3:] == ["--tail", "5", "--follow"]
    stopped = cli("cloud", "stop")
    assert stopped.exit_code == 0 and "_mantle_data" in stopped.stdout
    assert docker.compose_commands()[-1] == "down" and "--volumes" not in docker.calls[-1]
    assert "not running" in cli("cloud", "stop").stdout
    assert "not running" in cli("cloud", "logs").stderr
    assert saved_url(mantle_home) == "http://localhost:3100"


def test_status_reports_the_local_server(cli, docker, mantle_home):
    assert "mantle cloud start" in cli("cloud", "status").stdout
    assert cli("cloud", "start").exit_code == 0
    status = cli("cloud", "status")
    assert "Server:   started by `mantle cloud start`, running" in status.stdout
    assert cli("cloud", "stop").exit_code == 0
    stopped = cli("cloud", "status", "--json")
    assert stopped.exit_code == 1
    assert json.loads(stopped.stdout)["local_server"] == {"url": "http://localhost:3100", "state": "stopped"}


def test_doctor_suggests_starting_the_local_server(docker, mantle_home):
    from mantle.cli_cloud import start_cloud
    from mantle.doctor import cloud_checks
    start_cloud(hints=False)
    docker.container = None
    (check,) = cloud_checks(MantlePaths.discover())
    assert check.status == "fail" and check.fix == "Start the local server with: mantle cloud start"


def test_moving_to_a_used_port_keeps_the_running_server(cli, docker, mantle_home, monkeypatch):
    assert cli("cloud", "start").exit_code == 0
    monkeypatch.setattr(cloud_server, "port_in_use", lambda port: port in {3100, 3200})
    monkeypatch.setattr("mantle.cli_cloud.probe", lambda paths, url, **kwargs: ProbeResult(url, False, "not Mantle Cloud"))
    moved = cli("cloud", "start", "--port", "3200")
    assert moved.exit_code == 1 and "Port 3200 is already used" in moved.stderr
    assert docker.compose_commands() == ["pull", "up"] and docker.container == "running"
    again = cli("cloud", "start")  # Its own port is in use by itself, which is fine.
    assert again.exit_code == 0 and "already running" in again.stdout
