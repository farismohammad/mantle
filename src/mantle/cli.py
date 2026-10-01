from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from typer.core import TyperCommand
from rich.console import Console
from rich.table import Table

from mantle.agents import AgentStore, validate_agent_name
from mantle.cloud import CloudClient
from mantle.configuration import format_validation_error, load_agent_yaml, parse_agent_yaml
from mantle.errors import MantleError, NotFoundError
from mantle.launcher import AgentLauncher
from mantle.packages import PackageManager
from mantle.paths import MantlePaths
from mantle.provisioning import Provisioner
from mantle.runtimes import RuntimeManager
from mantle.skills import SkillManager
from mantle import __version__


class UserCommand(TyperCommand):
    """Keep expected input and filesystem failures consistent at every entrypoint."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except ValidationError as error:
            fail(MantleError(format_validation_error(error)))
        except subprocess.TimeoutExpired:
            fail(MantleError("Installation timed out. Check your network connection and retry."))
        except FileNotFoundError as error:
            if error.filename in {"npm", "npx"}:
                fail(MantleError(f"Required program {error.filename!r} was not found. Install Node.js/npm and ensure it is on PATH."))
            fail(error)
        except (MantleError, OSError, UnicodeError) as error:
            fail(error)


app = typer.Typer(help="Create, reproduce, and run isolated personal Pi agents.", no_args_is_help=True, pretty_exceptions_enable=False)
runtime_app = typer.Typer(help="Manage Mantle's shared Pi runtime cache.", no_args_is_help=True)
skills_app = typer.Typer(help="Manage agent skills.", no_args_is_help=True)
app.add_typer(runtime_app, name="runtime")
app.add_typer(skills_app, name="skills")
console = Console(markup=False, highlight=False)


def show_version(value: bool) -> None:
    if value:
        typer.echo(f"mantle {__version__}")
        raise typer.Exit()


@app.callback()
def options(version: Annotated[bool, typer.Option("--version", callback=show_version, is_eager=True, help="Show Mantle's version and exit.")] = False) -> None:
    pass


def paths() -> MantlePaths:
    return MantlePaths.discover()


def store() -> AgentStore:
    return AgentStore(paths())


def fail(error: Exception) -> None:
    typer.echo(f"Error: {error}", err=True)
    raise typer.Exit(1)


@app.command("create", cls=UserCommand)
def create_agent(
    name: str,
    description: Annotated[str, typer.Option(help="Human-readable purpose.")] = "",
    runtime_version: Annotated[str, typer.Option("--runtime-version", help="Pi version or npm tag to use.")] = "latest",
) -> None:
    """Create a local agent. Nothing is sent to Mantle Cloud."""
    try:
        store().create(name, description=description, runtime_version=runtime_version)
    except MantleError as error:
        fail(error)
    console.print(f"Created local agent: {name}\n  {paths().yaml(name)}")


@app.command("list", cls=UserCommand)
def list_agents(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List locally installed agents."""
    try:
        agents = store().list()
    except MantleError as error:
        fail(error)
    if as_json:
        typer.echo(json.dumps([item.model_dump(mode="json") for item in agents], indent=2))
        return
    if not agents:
        console.print("No local agents. Create one with: mantle create <name>\nInstall a Cloud agent with: mantle install <name>")
        return
    table = Table("AGENT", "MODEL", "RUNTIME", "SKILLS", "PACKAGES", "MCP", "LOCATION")
    for item in agents:
        table.add_row(
            item.agent.name,
            f"{item.model.provider}/{item.model.name}",
            f"pi@{item.runtime.version}",
            str(len(item.resources.skills)),
            str(len(item.resources.packages)),
            str(len(item.mcp.servers)),
            str(paths().agent(item.agent.name)),
        )
    console.print(table)


@app.command("delete", cls=UserCommand)
def delete_agent(
    name: str,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the local deletion confirmation.")] = False,
    keep_sessions: Annotated[bool, typer.Option("--keep-sessions", help="Move sessions to MANTLE_HOME/retained-sessions before deleting.")] = False,
) -> None:
    """Delete a local agent. The Cloud copy is untouched."""
    try:
        store().require_local(name)
        if not yes and not typer.confirm(f'Delete local agent "{name}"?', default=False):
            raise typer.Abort()
        kept = store().delete(name, keep_sessions=keep_sessions)
    except MantleError as error:
        fail(error)
    console.print(f"Deleted local agent: {name}")
    if kept:
        console.print(f"Sessions retained at: {kept}")


@app.command("push", cls=UserCommand)
def push_agent(name: str) -> None:
    """Validate and explicitly upload only agent.yaml to Mantle Cloud."""
    try:
        config = store().get(name)
        yaml_text = paths().yaml(name).read_text(encoding="utf-8")
        parse_agent_yaml(yaml_text, expected_name=name)
        result = CloudClient(paths()).put(name, yaml_text)
    except (MantleError, OSError) as error:
        fail(error if isinstance(error, MantleError) else MantleError(str(error)))
    if result["created"]:
        console.print(f"Pushed {config.agent.name}: Cloud revision {result['revision']['number']}")
    else:
        console.print(f"Cloud already has this exact configuration (revision {result['revision']['number']}).")


@app.command("install", cls=UserCommand)
def install_agent(name: str) -> None:
    """Reconstruct a new local agent from the latest Cloud revision."""
    try:
        validate_agent_name(name)
        if paths().agent(name).exists():
            fail(MantleError(f'Agent "{name}" already exists locally. Use mantle update {name}.'))
        remote = CloudClient(paths()).latest(name)
        config = parse_agent_yaml(remote["yaml"], expected_name=name)
        Provisioner(paths()).install(config, yaml_text=remote["yaml"])
    except MantleError as error:
        fail(error)
    console.print(f"Installed {name} from Cloud revision {remote['revision']['number']}.")


@app.command("update", cls=UserCommand)
def update_agent(name: str) -> None:
    """Replace local reconstructible state with the latest Cloud configuration."""
    try:
        store().require_local(name)
        remote = CloudClient(paths()).latest(name)
        config = parse_agent_yaml(remote["yaml"], expected_name=name)
        Provisioner(paths()).update(config, yaml_text=remote["yaml"])
    except MantleError as error:
        fail(error)
    console.print(f"Updated {name} to Cloud revision {remote['revision']['number']}.")


@app.command("run", cls=UserCommand, context_settings={"allow_extra_args": True})
def run_agent(
    context: typer.Context,
    name: str,
    project_root: Annotated[Path | None, typer.Option("--project-root", help="Project directory containing the current directory (default: cwd).")] = None,
    new_session: Annotated[bool, typer.Option("--new-session", help="Start fresh; this is the default.")] = False,
    session: Annotated[str | None, typer.Option("--session", help="Resume a session ID or file for this agent and project.")] = None,
    no_project_context: Annotated[bool, typer.Option("--no-project-context", help="Omit root AGENTS.md and CLAUDE.md instructions.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Prepare and print the launch as JSON without starting Pi.")] = False,
) -> None:
    """Run a local agent. Pass Pi options after --, e.g. -- --print 'Hello'."""
    if new_session and session:
        fail(MantleError("--new-session and --session cannot be used together."))
    if not store().exists(name):
        if paths().agent(name).exists():
            store().get(name)
        try:
            if CloudClient(paths()).exists(name):
                typer.echo(
                    f"Agent '{name}' is available in Mantle Cloud but is not installed locally.\n\n"
                    f"Install it with:\n\n  mantle install {name}", err=True
                )
                raise typer.Exit(1)
        except NotFoundError:
            pass
        except MantleError as error:
            fail(MantleError(f'Agent "{name}" is not installed locally. {error}\nCreate it with: mantle create {name}\nOr configure Cloud and run: mantle install {name}'))
        fail(NotFoundError(f'Agent "{name}" was not found locally or in Mantle Cloud. Create it with: mantle create {name}'))
    forwarded = list(context.args)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    try:
        spec = AgentLauncher(paths()).build(
            name,
            current=Path.cwd(),
            project_root=project_root,
            project_context=not no_project_context,
            session=session,
            new_session=new_session,
            pi_arguments=forwarded,
        )
    except MantleError as error:
        fail(error)
    if dry_run:
        typer.echo(json.dumps({
            "command": spec.command,
            "cwd": str(spec.cwd),
            "session_dir": str(spec.session_dir),
            "environment": {
                "PI_CODING_AGENT_DIR": spec.environment["PI_CODING_AGENT_DIR"],
                "PI_CODING_AGENT_SESSION_DIR": spec.environment["PI_CODING_AGENT_SESSION_DIR"],
                "PI_TELEMETRY": spec.environment["PI_TELEMETRY"],
            },
        }, indent=2))
        return
    raise typer.Exit(AgentLauncher(paths()).run(spec))


@skills_app.command("add", cls=UserCommand)
def add_skill(
    source: str,
    skill: Annotated[str, typer.Option("--skill", help="Skill directory/name within the source.")],
    agent: Annotated[str, typer.Option("--agent", help="Agent to install the skill into.")],
    force: Annotated[bool, typer.Option("--force", help="Replace an existing skill of the same name.")] = False,
) -> None:
    """Install a skill from its source and update local agent.yaml only."""
    try:
        record = SkillManager(paths()).add(source, skill, agent, force=force)
    except MantleError as error:
        fail(error)
    console.print(f"Installed skill {record.name} into {agent}. agent.yaml changed locally; Cloud was not updated.")


@app.command("remove-skill", cls=UserCommand)
def remove_skill(skill: str, agent: str) -> None:
    """Remove an agent skill and its local YAML declaration."""
    try:
        SkillManager(paths()).remove(skill, agent)
    except MantleError as error:
        fail(error)
    console.print(f"Removed skill {skill} from {agent}. Cloud was not updated.")


@app.command("add-package", cls=UserCommand)
def add_package(
    source: str,
    agent: str,
    name: Annotated[str | None, typer.Option("--name", help="Local resource name (default: derived from source).")] = None,
    force: Annotated[bool, typer.Option("--force", help="Replace an existing package of the same name.")] = False,
) -> None:
    """Install a Pi package from its source and update local agent.yaml only."""
    try:
        record = PackageManager(paths()).add(source, agent, name=name, force=force)
    except MantleError as error:
        fail(error)
    console.print(f"Installed package {record.name} into {agent}. agent.yaml changed locally; Cloud was not updated.")


@app.command("remove-package", cls=UserCommand)
def remove_package(name: str, agent: str) -> None:
    """Remove a Pi package and its local YAML declaration."""
    try:
        PackageManager(paths()).remove(name, agent)
    except MantleError as error:
        fail(error)
    console.print(f"Removed package {name} from {agent}. Cloud was not updated.")


@app.command("validate", cls=UserCommand)
def validate(path: Path) -> None:
    """Validate YAML syntax and the Mantle schema."""
    try:
        config = load_agent_yaml(path.expanduser())
    except (MantleError, OSError) as error:
        fail(error if isinstance(error, MantleError) else MantleError(str(error)))
    console.print(f"Valid Mantle configuration for {config.agent.name}.")


@runtime_app.command("install", cls=UserCommand)
def install_runtime(version: Annotated[str, typer.Argument()] = "latest") -> None:
    """Install and cache a Pi version (default: latest from npm)."""
    try:
        version = version.removeprefix("pi@")
        RuntimeManager.validate_version(version)
        metadata = RuntimeManager(paths()).install(None if version in ("latest", "pi") else version)
    except MantleError as error:
        fail(error)
    console.print(f"Installed runtime: pi@{metadata.version}")


@runtime_app.command("list", cls=UserCommand)
def list_runtimes() -> None:
    """List cached Pi versions and the default used by latest agents."""
    table = Table("RUNTIME", "DEFAULT")
    manager = RuntimeManager(paths())
    default = manager.config().default_runtime
    for runtime in manager.list():
        table.add_row(f"pi@{runtime.version}", "yes" if runtime.version == default else "")
    console.print(table)


@runtime_app.command("remove", cls=UserCommand)
def remove_runtime(version: str) -> None:
    """Remove an unused cached Pi version."""
    try:
        RuntimeManager(paths()).remove(version.removeprefix("pi@"))
    except MantleError as error:
        fail(error)
    console.print(f"Removed runtime: pi@{version.removeprefix('pi@')}")


def main() -> None:
    app()
