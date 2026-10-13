"""`mantle cloud`: connect to, inspect and list a self-hosted Mantle Cloud registry."""
from __future__ import annotations

import json
import os
from typing import Annotated

import typer
from rich.table import Table

from mantle.cli_common import UserCommand, command_group, console, err_console, interactive, next_steps, paths
from mantle import cloud_server
from mantle.cloud import (
    DEFAULT_CLOUD_URL, DISCOVERY_CANDIDATES, CloudClient, _env_flag, is_loopback, probe, sync_status,
)
from mantle.errors import MantleError
from mantle.progress import step
from mantle.settings import load_settings, save_settings
from mantle.models import MantleConfig
from mantle.storage import load_json

cloud_app = command_group("Run or connect to Mantle Cloud and push, pull or update agents through it.")


def _settings_for_update() -> MantleConfig:
    """Current settings, or a fresh copy (keeping the runtime default) when config.json is invalid."""
    mantle_paths = paths()
    try:
        return load_settings(mantle_paths)
    except MantleError as error:
        err_console.print(f"Note: replacing invalid settings. {error.args[0].splitlines()[0]}")
        try:
            data = load_json(mantle_paths.config, {})
            runtime = data.get("default_runtime") if isinstance(data, dict) else None
            return MantleConfig(default_runtime=runtime if isinstance(runtime, str) else None)
        except (MantleError, ValueError):
            return MantleConfig()


def discover() -> list[str]:
    found = []
    with step("Looking for Mantle Cloud on this machine…"):
        for candidate in DISCOVERY_CANDIDATES:
            if probe(paths(), candidate, timeout=1.5).ok:
                found.append(candidate)
    return found


def connect_cloud(url: str | None, *, insecure: bool = False, yes: bool = False, force: bool = False,
                  discovered: list[str] | None = None, suggest_start: bool = True) -> bool:
    """Validate and save the registry URL. Returns whether it was saved."""
    if url is None:
        found = discover() if discovered is None else discovered
        if found:
            console.print(f"Found Mantle Cloud at {found[0]}.")
            url = found[0] if (yes or not interactive()) else typer.prompt("Mantle Cloud URL", default=found[0])
        elif interactive() and not yes:
            console.print("No Mantle Cloud was found on this machine (checked ports 3100 and 3000)."
                          + (" To run one here with Docker, press Ctrl+C and use: mantle cloud start" if suggest_start else ""))
            url = typer.prompt("Mantle Cloud URL", default=DEFAULT_CLOUD_URL)
        else:
            raise MantleError(
                "No Mantle Cloud was found on this machine. Pass its address, for example: "
                f"mantle cloud connect {DEFAULT_CLOUD_URL}\nOr run one here with Docker: mantle cloud start"
            )
    with step(f"Checking {url.strip()}…"):
        result = probe(paths(), url, insecure=insecure)
    for note in result.notes:
        console.print(note)
    if not result.ok:
        message = result.summary + (f"\n{result.hint}" if result.hint else "")
        if not force:
            raise MantleError(message + "\nNothing was saved. Use --force to save the address anyway.")
        err_console.print(f"Warning: {message}\nSaving anyway because of --force.")
    else:
        count = f" ({result.agents} agent{'s' if result.agents != 1 else ''})" if result.agents is not None else ""
        console.print(f"✔ {result.summary}{count}")
    if result.url.startswith("http://") and not is_loopback(result.url):
        console.print(
            "Warning: this connection is not encrypted and Mantle Cloud has no login. Anyone on the network "
            "path can read or change your agents. Prefer https:// behind a reverse proxy with access control."
        )
        if not yes and interactive() and not typer.confirm("Save this address anyway?", default=False):
            raise typer.Abort()
    if insecure:
        console.print("Warning: TLS certificate checks are disabled for this registry (--insecure).")
    save_cloud_address(result.url, insecure=insecure)
    console.print(f"Saved to {paths().config}.")
    return True


def save_cloud_address(url: str, *, insecure: bool = False) -> None:
    settings = _settings_for_update()
    settings.cloud_url = url
    settings.cloud_insecure = insecure
    save_settings(paths(), settings)
    if os.environ.get("MANTLE_CLOUD_URL") and os.environ["MANTLE_CLOUD_URL"].strip().rstrip("/") != url:
        err_console.print("Note: MANTLE_CLOUD_URL is set in this shell and takes precedence over the saved address. Unset it to use the saved one.")


@cloud_app.command("connect", cls=UserCommand)
def cloud_connect(
    url: Annotated[str | None, typer.Argument(help="Registry address, e.g. http://localhost:3100. Discovered or prompted when omitted.", metavar="URL")] = None,
    insecure: Annotated[bool, typer.Option("--insecure", help="Accept a self-signed TLS certificate (trusted private instances only).")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Accept discovered addresses and warnings without prompting.")] = False,
    force: Annotated[bool, typer.Option("--force", help="Save the address even if it cannot be verified now.")] = False,
) -> None:
    """Find, verify and save your Mantle Cloud address.

    Mantle checks that the address answers like a Mantle Cloud before saving it,
    fixes common typos (missing http://, trailing /api), and warns about
    unencrypted connections to other machines.
    """
    connect_cloud(url, insecure=insecure, yes=yes, force=force)


def start_cloud(*, port: int | None = None, version: str | None = None, connect: bool = True, yes: bool = False,
                hints: bool = True) -> str:
    """Start (or keep running) the local Mantle Cloud, print its address and connect to it. Returns the address."""
    mantle_paths = paths()
    with step("Checking Docker…"):
        cloud_server.require_docker()
        local = cloud_server.resolve(mantle_paths, port=port, version=version)
        previous = cloud_server.saved(mantle_paths) if cloud_server.managed(mantle_paths) else None
        running = cloud_server.container_state(mantle_paths) == "running"
        # Check the port unless this server already holds it: a failed bind would take a running server down.
        holds_port = running and previous is not None and previous.port == local.port
        occupied = not holds_port and cloud_server.port_in_use(local.port)
    if occupied:
        if not probe(mantle_paths, local.url, timeout=3).ok:
            raise MantleError(cloud_server.port_taken(local.port))
        console.print(f"A Mantle Cloud is already running at {local.url}. It was not started by `mantle cloud start`, "
                      "so it is used as it is and `mantle cloud stop` will not stop it.")
        console.print(f"Open it in your browser: {local.url}")
        if connect:
            _connect_started(local.url, None, yes=yes)
        return local.url
    cloud_server.write_compose(mantle_paths)
    with step(f"Pulling {local.reference}…"):
        problem = cloud_server.pull(mantle_paths, local)
    if problem:
        if not cloud_server.image_present(local):
            raise MantleError(f"Could not download Mantle Cloud: {problem}.")
        err_console.print(f"Note: could not check for a newer {local.reference}: {problem}. Using the copy on this machine.")
    with step("Starting Mantle Cloud…"):
        started = cloud_server.up(mantle_paths, local)
        cloud_server.save(mantle_paths, local)
        result = cloud_server.wait_until_ready(mantle_paths, local)
    already = running and not started
    count = f" with {result.agents} agent{'s' if result.agents != 1 else ''}" if result.agents is not None else ""
    console.print(f"✔ Mantle Cloud is {'already ' if already else ''}running{count} ({local.reference}).")
    console.print(f"Open it in your browser: {local.url}")
    if connect:
        _connect_started(local.url, previous.url if previous else None, yes=yes)
    if hints:
        next_steps(
            ("mantle cloud push AGENT", "publish an agent to it"),
            ("mantle cloud logs", "see its output"),
            ("mantle cloud stop", "stop it (your agents are kept)"),
        )
    return local.url


def _connect_started(url: str, previous_url: str | None, *, yes: bool) -> None:
    """Point the CLI at a server `start` brought up, asking before replacing a different registry."""
    current = _settings_for_update().cloud_url
    if current == url:
        console.print("The CLI is connected to it.")
        return
    if current and current != previous_url and not yes:
        if not interactive():
            console.print(f"The CLI still uses {current}. Switch to this server with: mantle cloud connect {url}")
            return
        if not typer.confirm(f"The CLI uses {current}. Switch it to {url}?", default=True):
            console.print(f"Kept {current}. Switch later with: mantle cloud connect {url}")
            return
    save_cloud_address(url)
    console.print(f"Connected the CLI to it (saved to {paths().config}).")


def _local_server() -> dict[str, str] | None:
    """The server `mantle cloud start` runs here, if it was ever started: its address and container state."""
    mantle_paths = paths()
    if not cloud_server.managed(mantle_paths):
        return None
    local = cloud_server.saved(mantle_paths) or cloud_server.resolve(mantle_paths)
    try:
        state = cloud_server.container_state(mantle_paths) or "stopped"
    except MantleError:
        state = "unknown (Docker is not available)"
    return {"url": local.url, "state": state}


def _describe_local(local: dict[str, str], registry: str | None = None) -> str:
    where = "started by `mantle cloud start`" if local["url"] == registry else f"{local['url']} (mantle cloud start)"
    if local["state"] == "running" or local["state"].startswith("unknown"):
        return f"{where}, {local['state']}"
    return f"{where}, {local['state']}. Start it with: mantle cloud start"


@cloud_app.command("start", cls=UserCommand)
def cloud_start(
    port: Annotated[int | None, typer.Option("--port", min=1, max=65535, help=f"Port on this machine. Default: the previous start's port, else {cloud_server.DEFAULT_PORT}.")] = None,
    version: Annotated[str | None, typer.Option("--version", help=f"Mantle Cloud image version. Default: {cloud_server.CLOUD_IMAGE_TAG}, which follows its patch releases.")] = None,
    no_connect: Annotated[bool, typer.Option("--no-connect", help="Do not point the CLI at this server.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Switch the CLI from another registry without asking.")] = False,
) -> None:
    """Run Mantle Cloud on this machine with Docker and print its address.

    Downloads the newest image for the version (ghcr.io/farismohammad/mantle-web),
    starts it on localhost only, waits until it answers and connects the CLI to it.
    Agents are kept in a Docker volume when it stops. Needs Docker with Compose v2.
    Running it again is safe: it applies a new port or version, or reports that
    the server is already running.
    """
    start_cloud(port=port, version=version, connect=not no_connect, yes=yes)


@cloud_app.command("stop", cls=UserCommand)
def cloud_stop() -> None:
    """Stop the Mantle Cloud that `mantle cloud start` runs. Its agents are kept."""
    mantle_paths = paths()
    if not cloud_server.managed(mantle_paths):
        console.print("No Mantle Cloud was started with `mantle cloud start`, so there is nothing to stop. "
                      "If you started one with docker compose, stop it there with: docker compose down")
        return
    cloud_server.require_docker()
    if cloud_server.container_state(mantle_paths) is None:
        console.print("Mantle Cloud is not running. Start it with: mantle cloud start")
        return
    local = cloud_server.saved(mantle_paths) or cloud_server.resolve(mantle_paths)
    with step("Stopping Mantle Cloud…"):
        cloud_server.down(mantle_paths, local)
    console.print(f"✔ Mantle Cloud stopped. Its agents are kept in the Docker volume {local.volume}.")
    console.print("Start it again with: mantle cloud start")


@cloud_app.command("logs", cls=UserCommand)
def cloud_logs(
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Keep printing new output until Ctrl+C.")] = False,
    lines: Annotated[int, typer.Option("--tail", "-n", min=0, help="How many recent lines to show first.")] = 100,
) -> None:
    """Show the output of the Mantle Cloud that `mantle cloud start` runs."""
    mantle_paths = paths()
    if not cloud_server.managed(mantle_paths):
        raise MantleError("No Mantle Cloud was started with `mantle cloud start`, so there are no logs to show. "
                          "Start one with: mantle cloud start")
    cloud_server.require_docker()
    if cloud_server.container_state(mantle_paths) is None:
        raise MantleError("Mantle Cloud is not running, so there are no logs to show. Start it with: mantle cloud start")
    local = cloud_server.saved(mantle_paths) or cloud_server.resolve(mantle_paths)
    # Next.js reports its port inside the container, which differs from the published one.
    err_console.print(f"Mantle Cloud is at {local.url} (the server reports port 3000 inside its container).")
    code = cloud_server.stream_logs(mantle_paths, local, follow=follow, lines=lines)
    if code:
        raise typer.Exit(code)


@cloud_app.command("status", cls=UserCommand)
def cloud_status(as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False) -> None:
    """Show which registry is configured, where the setting comes from, and whether it answers."""
    mantle_paths = paths()
    settings = load_settings(mantle_paths)
    env_url = os.environ.get("MANTLE_CLOUD_URL")
    url = env_url or settings.cloud_url
    source = "MANTLE_CLOUD_URL environment variable" if env_url else str(mantle_paths.config) if url else None
    local = _local_server()
    if not url:
        if as_json:
            typer.echo(json.dumps({"configured": False, **({"local_server": local} if local else {})}))
            return
        console.print("Mantle Cloud is not configured (it is optional). Connect with: mantle cloud connect")
        if local:
            console.print(f"Local server: {_describe_local(local)}")
        else:
            console.print("Or run one on this machine with Docker: mantle cloud start")
        return
    insecure = _env_flag("MANTLE_CLOUD_INSECURE", default=settings.cloud_insecure)
    with step(f"Checking {url}…"):
        result = probe(mantle_paths, url, insecure=insecure)
    if as_json:
        typer.echo(json.dumps({"configured": True, "url": result.url, "source": source, "reachable": result.ok,
                               "agents": result.agents, "detail": result.summary, "insecure": insecure,
                               **({"local_server": local} if local else {})}, indent=2))
    else:
        console.print(f"Registry: {result.url}")
        console.print(f"Set by:   {source}")
        if insecure:
            console.print("TLS:      certificate checks disabled")
        if local:
            label = "Server:" if local["url"] == result.url else "Local:"
            console.print(f"{label.ljust(9)} {_describe_local(local, result.url)}")
        console.print(("✔ " if result.ok else "✖ ") + result.summary + (f", {result.agents} agent(s)" if result.ok and result.agents is not None else ""))
        if result.hint:
            console.print(result.hint)
    if not result.ok:
        raise typer.Exit(1)


@cloud_app.command("disconnect", cls=UserCommand)
def cloud_disconnect() -> None:
    """Forget the saved registry address (local agents are unaffected)."""
    settings = _settings_for_update()
    if not settings.cloud_url:
        console.print("No saved Mantle Cloud address.")
    settings.cloud_url = None
    settings.cloud_insecure = False
    save_settings(paths(), settings)
    console.print("Mantle Cloud disconnected. Local agents keep working.")
    if os.environ.get("MANTLE_CLOUD_URL"):
        err_console.print("Note: MANTLE_CLOUD_URL is still set in this shell.")


@cloud_app.command("list", cls=UserCommand)
def cloud_list(as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False) -> None:
    """List agents in Mantle Cloud and how each compares with this machine."""
    mantle_paths = paths()
    client = CloudClient(mantle_paths)
    remote = {item["name"]: item for item in client.list()}
    local = {path.name for path in mantle_paths.agents.iterdir()
             if path.is_dir() and not path.name.startswith(".")} if mantle_paths.agents.is_dir() else set()
    rows = []
    for name in sorted(set(remote) | local):
        item = remote.get(name)
        root = mantle_paths.agent(name) if name in local else None
        rows.append({
            "name": name,
            "revision": item["revision"] if item else None,
            "updated": item.get("updatedAt") if item else None,
            "status": sync_status(root, client.base_url, item["revision"] if item else None),
        })
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        console.print(f"No agents in Mantle Cloud at {client.base_url} or on this machine.")
        return
    table = Table("AGENT", "CLOUD REVISION", "UPDATED", "ON THIS MACHINE")
    for row in rows:
        table.add_row(row["name"], str(row["revision"] or "—"), (row["updated"] or "")[:16].replace("T", " "), row["status"])
    console.print(table)
    console.print(f"Registry: {client.base_url}")

