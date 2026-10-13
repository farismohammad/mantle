"""Run Mantle Cloud on this machine with Docker Compose (`mantle cloud start/stop/logs`)."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from mantle.cloud import ProbeResult, probe
from mantle.errors import MantleError
from mantle.paths import MantlePaths
from mantle.secrets import parse_dotenv

CLOUD_IMAGE = "ghcr.io/farismohammad/mantle-web"
# The registry release line this CLI speaks to. A minor tag follows its patch
# releases, which `start` pulls; moving to a new minor version is a CLI change.
CLOUD_IMAGE_TAG = "0.1"
DEFAULT_PORT = 3100
PROJECT = SERVICE = "mantle-cloud"
COMPOSE_TEMPLATE = Path(__file__).with_name("cloud_compose.yaml")
INSTALL_DOCKER = "https://docs.docker.com/get-docker/"
_TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")


@dataclass(frozen=True)
class LocalCloud:
    port: int
    image: str
    version: str
    project: str

    @property
    def reference(self) -> str:
        return f"{self.image}:{self.version}"

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    @property
    def volume(self) -> str:
        return f"{self.project}_mantle_data"

    def environment(self) -> dict[str, str]:
        return {"COMPOSE_PROJECT_NAME": self.project, "MANTLE_PORT": str(self.port),
                "MANTLE_CLOUD_IMAGE": self.image, "MANTLE_CLOUD_VERSION": self.version}


def project_name(paths: MantlePaths) -> str:
    """The Compose project, so each MANTLE_HOME (and each user) runs its own server and volume."""
    if paths.home == (Path.home() / ".mantle").resolve():
        return PROJECT
    return f"{PROJECT}-{hashlib.sha256(str(paths.home).encode()).hexdigest()[:8]}"


def compose_file(paths: MantlePaths) -> Path:
    return paths.local_cloud / "compose.yaml"


def env_file(paths: MantlePaths) -> Path:
    return paths.local_cloud / ".env"


def managed(paths: MantlePaths) -> bool:
    """Whether `mantle cloud start` has started a Mantle Cloud for this MANTLE_HOME."""
    return compose_file(paths).is_file() and env_file(paths).is_file()


def saved(paths: MantlePaths) -> LocalCloud | None:
    """The settings of the last `mantle cloud start`."""
    try:
        values = parse_dotenv(env_file(paths))
        return LocalCloud(int(values["MANTLE_PORT"]), values["MANTLE_CLOUD_IMAGE"], values["MANTLE_CLOUD_VERSION"],
                          project_name(paths))
    except (MantleError, OSError, KeyError, ValueError):
        return None


def resolve(paths: MantlePaths, *, port: int | None = None, version: str | None = None) -> LocalCloud:
    """Settings for a start: options first, then the previous start's port, then the defaults."""
    if version is not None and not _TAG.fullmatch(version):
        raise MantleError(f"{version!r} is not a valid image version. Use a release such as {CLOUD_IMAGE_TAG} or 0.1.0.")
    previous = saved(paths)
    image = os.environ.get("MANTLE_CLOUD_IMAGE", "").strip() or CLOUD_IMAGE
    return LocalCloud(port or (previous.port if previous else DEFAULT_PORT), image, version or CLOUD_IMAGE_TAG,
                      project_name(paths))


def write_compose(paths: MantlePaths) -> None:
    paths.local_cloud.mkdir(parents=True, exist_ok=True)
    compose_file(paths).write_text(COMPOSE_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")


def save(paths: MantlePaths, cloud: LocalCloud) -> None:
    """Record a successful start, so later commands (and plain `docker compose`) use the same settings."""
    lines = ["# Written by `mantle cloud start`. Use its --port and --version options to change these."]
    lines += [f"{name}={value}" for name, value in cloud.environment().items()]
    env_file(paths).write_text("\n".join(lines) + "\n", encoding="utf-8")


# Docker ----------------------------------------------------------------------

def _docker(*args: str, timeout: float = 60, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as error:
        raise MantleError(
            f"Docker did not answer within {timeout:.0f} seconds (docker {' '.join(args[:2])}). "
            "If Docker is still starting, wait for it and retry."
        ) from error
    except FileNotFoundError as error:
        raise MantleError(f"Docker is not installed. Install Docker with Compose v2: {INSTALL_DOCKER}") from error


def _tail(text: str, lines: int = 8) -> str:
    return "\n".join(line for line in text.strip().splitlines()[-lines:])


def _daemon_problem(stderr: str) -> str:
    detail = stderr.lower()
    if "permission denied" in detail:
        return (
            "Docker is installed, but your user may not use it (permission denied on the Docker socket). "
            "Add yourself to the docker group with: sudo usermod -aG docker $USER\n"
            "Then log out and back in, and retry."
        )
    if "cannot connect" in detail or "is the docker daemon running" in detail or "no such file" in detail:
        return "Docker is installed but not running. Start Docker Desktop, or on Linux: sudo systemctl start docker"
    return f"Docker is not working:\n{_tail(stderr) or 'docker info failed'}"


def docker_problem() -> str | None:
    """Why Docker cannot run Mantle Cloud here, with the fix; None when it can."""
    if shutil.which("docker") is None:
        return f"Docker is not installed. Install Docker with Compose v2: {INSTALL_DOCKER}"
    info = _docker("info", "--format", "{{.ServerVersion}}", timeout=30)
    if info.returncode != 0:
        return _daemon_problem(info.stderr)
    if _docker("compose", "version").returncode != 0:
        return (
            "Docker Compose v2 (`docker compose`) is not installed. Install the Compose plugin: "
            "https://docs.docker.com/compose/install/ (the older docker-compose v1 is not supported)."
        )
    return None


def require_docker() -> None:
    problem = docker_problem()
    if problem:
        raise MantleError(problem)


def docker_available() -> bool:
    try:
        return docker_problem() is None
    except MantleError:
        return False


def _compose(paths: MantlePaths, cloud: LocalCloud, *args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return _docker("compose", "-p", cloud.project, "-f", str(compose_file(paths)), *args,
                   timeout=timeout, env={**os.environ, **cloud.environment()})


def container_state(paths: MantlePaths) -> str | None:
    """`running`, `exited`, `restarting`… for the container `start` created, or None when there is none."""
    result = _docker("ps", "--all", "--filter", f"label=com.docker.compose.project={project_name(paths)}",
                     "--filter", f"label=com.docker.compose.service={SERVICE}", "--format", "{{.State}}", timeout=30)
    if result.returncode != 0:
        raise MantleError(_daemon_problem(result.stderr))
    states = result.stdout.split()
    return states[0] if states else None


def port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def pull(paths: MantlePaths, cloud: LocalCloud) -> str | None:
    """Download the newest image for the version. Returns why it failed, or None."""
    result = _compose(paths, cloud, "pull", "--quiet", timeout=900)
    if result.returncode == 0:
        return None
    detail = result.stderr.lower()
    if "denied" in detail or "unauthorized" in detail or "manifest unknown" in detail or "not found" in detail:
        registry = cloud.image.split("/")[0] if "/" in cloud.image else "docker.io"
        return (f"{cloud.reference} does not exist or is not public. Check the --version value; "
                f"for a private image, sign Docker in first with: docker login {registry}")
    if any(text in detail for text in ("no such host", "dial tcp", "timeout", "network is unreachable", "tls")):
        return "the image registry could not be reached. Check your internet connection"
    return _tail(result.stderr, 3) or "docker compose pull failed"


def image_present(cloud: LocalCloud) -> bool:
    return _docker("image", "inspect", cloud.reference, timeout=30).returncode == 0


def up(paths: MantlePaths, cloud: LocalCloud) -> bool:
    """Start or update the server. Returns whether a container was (re)started, as opposed to left running."""
    result = _compose(paths, cloud, "up", "--detach", "--remove-orphans", timeout=300)
    if result.returncode == 0:
        return "Started" in result.stderr
    detail = result.stderr.lower()
    if "port is already allocated" in detail or "address already in use" in detail:
        raise MantleError(port_taken(cloud.port))
    raise MantleError(f"Docker Compose could not start Mantle Cloud:\n{_tail(result.stderr)}")


def port_taken(port: int) -> str:
    return f"Port {port} is already used by another program. Choose another port: mantle cloud start --port {port + 1}"


def recent_logs(paths: MantlePaths, cloud: LocalCloud, lines: int = 20) -> str:
    result = _compose(paths, cloud, "logs", "--no-color", "--tail", str(lines), timeout=30)
    return _tail(result.stdout + result.stderr, lines)


def wait_until_ready(paths: MantlePaths, cloud: LocalCloud, *, timeout: float = 90) -> ProbeResult:
    deadline = time.monotonic() + timeout
    while True:
        result = probe(paths, cloud.url, timeout=2)
        if result.ok:
            return result
        state = container_state(paths)
        if state in {None, "exited", "dead", "restarting"}:
            logs = recent_logs(paths, cloud)
            # Stop the restart loop, but keep the container so `mantle cloud logs` still works.
            _compose(paths, cloud, "stop", timeout=60)
            raise MantleError(
                f"Mantle Cloud crashed while starting (container {state or 'removed'}) and has been stopped.\n"
                + (f"Last log lines:\n{logs}\n" if logs else "")
                + "See more with: mantle cloud logs"
            )
        if time.monotonic() >= deadline:
            raise MantleError(
                f"Mantle Cloud did not answer at {cloud.url} within {timeout:.0f} seconds ({result.summary}).\n"
                "See what it is doing with: mantle cloud logs"
            )
        time.sleep(0.5)


def down(paths: MantlePaths, cloud: LocalCloud) -> None:
    result = _compose(paths, cloud, "down", "--remove-orphans", timeout=120)
    if result.returncode != 0:
        raise MantleError(f"Docker Compose could not stop Mantle Cloud:\n{_tail(result.stderr)}")


def stream_logs(paths: MantlePaths, cloud: LocalCloud, *, follow: bool, lines: int) -> int:
    """Print the server's logs straight to the terminal. Returns Docker's exit code."""
    command = ["docker", "compose", "-p", cloud.project, "-f", str(compose_file(paths)), "logs", "--tail", str(lines)]
    if follow:
        command.append("--follow")
    try:
        return subprocess.run(command, env={**os.environ, **cloud.environment()}).returncode
    except KeyboardInterrupt:
        return 0
