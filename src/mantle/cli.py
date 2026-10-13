from __future__ import annotations

import difflib
import json
import os
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import typer
from rich.table import Table

from mantle import __version__
from mantle.agents import validate_agent_name
from mantle.cli_auth import auth_app
from mantle.cli_cloud import cloud_app
from mantle.cli_common import (
    COMMANDS_PANEL, SETUP_PANEL, RunCommand, UserCommand, command_group, complete_agent, console, err_console, fail,
    interactive, next_steps, note_damaged, paths, store,
)
from mantle.cli_setup import doctor, setup
from mantle.cloud import CloudClient, local_changes, read_sync, record_sync
from mantle.configuration import load_agent_yaml, parse_agent_yaml
from mantle.credentials import (
    alternative_provider, auth_file_problem, is_isolated, login_step, preferred_provider, provider_credential,
    switch_reason,
)
from mantle.errors import MantleError, NotFoundError
from mantle.launcher import AgentLauncher
from mantle.models import AgentConfig, default_agent_config
from mantle.packages import PackageManager
from mantle.progress import step
from mantle.provisioning import Provisioner
from mantle.providers import env_variables, lookup as lookup_provider
from mantle.runtimes import RuntimeManager
from mantle.secrets import parse_dotenv
from mantle.skills import SkillManager, frontmatter_name
from mantle.storage import atomic_write_text

# Re-exported for callers and tests that import these from mantle.cli.
__all__ = ["app", "main", "UserCommand", "CloudClient", "SkillManager", "PackageManager"]

app = typer.Typer(
    help="Create, run and sync isolated Pi agents. New here? Run: mantle setup",
    epilog="Run `mantle COMMAND --help` to see what a command can do, for example: mantle agent --help",
    no_args_is_help=False,
    invoke_without_command=True,
    pretty_exceptions_enable=False,
)
agent_app = command_group("Create, run and manage agents.")
runtime_app = command_group("Manage the shared Pi runtime versions agents run on.")
skills_app = command_group("Add, remove and list an agent's skills.")
package_app = command_group("Add, remove and list an agent's Pi packages.")

app.add_typer(agent_app, name="agent", rich_help_panel=COMMANDS_PANEL)
app.add_typer(skills_app, name="skills", rich_help_panel=COMMANDS_PANEL)
app.add_typer(package_app, name="package", rich_help_panel=COMMANDS_PANEL)
app.add_typer(auth_app, name="auth", rich_help_panel=COMMANDS_PANEL)
app.add_typer(cloud_app, name="cloud", rich_help_panel=COMMANDS_PANEL)
app.command("setup", cls=UserCommand, rich_help_panel=SETUP_PANEL)(setup)
app.command("doctor", cls=UserCommand, rich_help_panel=SETUP_PANEL)(doctor)
app.add_typer(runtime_app, name="runtime", rich_help_panel=SETUP_PANEL)

RUN_SETTINGS = {"allow_extra_args": True}
AgentArg = Annotated[str, typer.Argument(help="Agent name.", autocompletion=complete_agent, show_default=False, metavar="AGENT")]
# Commands that act on something inside an agent (a skill, a package) name the agent with --agent.
AgentOption = Annotated[str, typer.Option("--agent", "-a", help="Agent to act on.", autocompletion=complete_agent, show_default=False, metavar="AGENT")]


def show_version(value: bool) -> None:
    if value:
        typer.echo(f"mantle {__version__}")
        raise typer.Exit()


@app.callback()
def options(
    context: typer.Context,
    version: Annotated[bool, typer.Option("--version", callback=show_version, is_eager=True, help="Show Mantle's version and exit.")] = False,
) -> None:
    if context.invoked_subcommand is None:
        typer.echo(context.get_help())
        raise typer.Exit()


def credential_status(name: str, config: AgentConfig) -> tuple[str, list[tuple[str, str]]]:
    """How this agent's model provider is authenticated, and the commands that fix it when it is not."""
    mantle_paths = paths()
    root = mantle_paths.agent(name)
    problem = auth_file_problem(mantle_paths, root)
    if problem:
        return f"unusable: {problem}", [(f"mantle auth share {name}", "set the unreadable file aside and use the shared logins")]
    if provider_credential(mantle_paths, root, config.model.provider):
        return ("isolated logins" if is_isolated(root) else "shared login"), []
    provider = lookup_provider(config.model.provider)
    if provider is None:
        return f"not checked: {config.model.provider} is a custom provider, set up in {root / 'models.json'}", []
    missing = missing_secrets(config)
    declared = next((variable for variable in provider.env if variable in config.secrets and variable not in missing), None)
    if declared:
        return f"via declared secret {declared}", []
    login = login_step(root, provider, config.secrets)
    label = f"missing → {login[0]}"
    steps = [login]
    alternative = alternative_provider(mantle_paths, root, provider.id)
    if alternative:
        switch = f"mantle agent edit {name} --provider {alternative}"
        label += f", or use your {alternative} login: {switch}"
        steps.append((switch, f"or use your {alternative} login ({switch_reason(provider.id, alternative)})"))
    return label, steps


def missing_secrets(config: AgentConfig) -> list[str]:
    from mantle.credentials import shared_api_key_environment
    try:
        local = parse_dotenv(paths().dotenv)
    except MantleError:
        local = {}
    shared = shared_api_key_environment(paths(), paths().agent(config.agent.name))
    return [name for name in config.secrets if not (os.environ.get(name) or local.get(name) or shared.get(name))]


def secret_steps(config: AgentConfig, skip: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    """Commands that provide missing secrets, leaving out commands already in `skip`."""
    isolated = is_isolated(paths().agent(config.agent.name))
    steps = []
    for secret in missing_secrets(config):
        # An isolated agent never reads the shared logins, so its provider keys go in .env too.
        provider = None if isolated else env_variables().get(secret)
        command = f"mantle auth login {provider}" if provider else f"mantle auth secrets set {secret}"
        if command not in [existing for existing, _ in [*(skip or []), *steps]]:
            steps.append((command, f"provides {secret}, which this agent declares"))
    return steps


def select_model(config: AgentConfig, provider: str | None, model: str | None) -> None:
    """Apply --provider/--model. A provider without --model gets its recommended model."""
    if model is not None and not model.strip():
        raise MantleError("--model must not be empty.")
    if provider is None:
        if model is not None:
            config.model.name = model
        return
    if not provider.strip():
        raise MantleError("--provider must not be empty. See the providers with: mantle auth providers")
    known = lookup_provider(provider)
    selected = model or (known.default_model if known else None)
    if not selected:
        raise MantleError(f'Mantle has no default model for provider "{provider}". Pass one with --model.')
    config.model.provider, config.model.name = provider, selected


# Agents ----------------------------------------------------------------------

@agent_app.command("create", cls=UserCommand)
def create_agent(
    name: Annotated[str, typer.Argument(help="New agent name: lowercase letters, numbers, - and _.", show_default=False, metavar="NAME")],
    provider: Annotated[str | None, typer.Option("--provider", "-p", help="Model provider, e.g. anthropic, openai (see `mantle auth providers`).")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model ID (default: the provider's recommended model).")] = None,
    description: Annotated[str, typer.Option(help="Human-readable purpose.")] = "",
    runtime_version: Annotated[str | None, typer.Option("--runtime-version", help="Pi version or npm tag to use (default: latest).", show_default=False)] = None,
) -> None:
    """Create a local agent. Nothing is sent to Mantle Cloud.

    Without --provider, the agent uses OpenAI, or the provider you are signed in
    to when you are not signed in to OpenAI.

    Examples:

      mantle agent create reviewer --provider anthropic

      mantle agent create docs --model gpt-5.6
    """
    validate_agent_name(name)
    config = default_agent_config(name)
    if description:
        config.agent.description = description
    if runtime_version is not None:
        config.runtime.version = runtime_version
    signed_in_default = None
    if provider is None:
        provider = preferred_provider(paths())
        signed_in_default = provider if provider != config.model.provider else None
    select_model(config, provider, model)
    config = AgentConfig.model_validate(config.model_dump())
    store().create(name, config=config)
    console.print(f'Created agent "{name}" ({config.model.provider}/{config.model.name})')
    if signed_in_default:
        console.print(f"  Uses {signed_in_default} because that is the provider you are signed in to; choose another with --provider.")
    console.print(f"  Config: {paths().yaml(name)}")
    steps = credential_status(name, config)[1]
    steps += secret_steps(config, skip=steps)
    steps += [(f"mantle agent edit {name}", "change the model, prompt or permissions"),
              (f"mantle agent run {name}", "start it in the current project directory")]
    next_steps(*steps)


@agent_app.command("list", cls=UserCommand)
def list_agents(as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False) -> None:
    """List local agents. Damaged agents are named on stderr and do not stop the listing."""
    agents, damaged = store().scan()
    if as_json:
        typer.echo(json.dumps([item.model_dump(mode="json") for item in agents], indent=2))
    elif not agents and not damaged:
        console.print("No local agents yet.")
        next_steps(("mantle agent create NAME", "create one"), ("mantle cloud pull NAME", "or pull one from Mantle Cloud"))
        return
    else:
        table = Table("AGENT", "MODEL", "RUNTIME", "SKILLS", "PACKAGES", "MCP", "DESCRIPTION")
        for item in agents:
            table.add_row(
                item.agent.name,
                f"{item.model.provider}/{item.model.name}",
                f"pi@{item.runtime.version}",
                str(len(item.resources.skills)),
                str(len(item.resources.packages)),
                str(len(item.mcp.servers)),
                item.agent.description,
            )
        for agent_name in damaged:
            table.add_row(agent_name, "(damaged; see below)", "", "", "", "", "")
        console.print(table)
        console.print(f"Stored in {paths().agents}. Details: mantle agent show NAME")
    for agent_name, error in damaged.items():
        err_console.print(f"Warning: could not read agent {agent_name}: {error}")
        link = paths().agents / agent_name
        if link.is_symlink():
            err_console.print(f"  Move the agent's directory to {link} instead of linking to it (rm {link} removes only the link).")
        else:
            err_console.print(f"  Repair it with: mantle agent edit {agent_name}  (or remove it: mantle agent delete {agent_name})")


@agent_app.command("show", cls=UserCommand)
def show_agent(name: AgentArg, as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False) -> None:
    """Show an agent's configuration, credentials and sync state (secret values are never shown)."""
    config = store().get(name)
    root = paths().agent(name)
    manager = RuntimeManager(paths())
    runtime = config.runtime.version
    if runtime == "latest":
        default = manager.config().default_runtime
        runtime_detail = f"latest → pi@{default}" if default else "latest (downloaded on first run)"
    else:
        runtime_detail = f"pi@{runtime}" + ("" if (paths().runtimes / runtime / "metadata.json").is_file() else " (downloaded on first run)")
    provider = lookup_provider(config.model.provider)
    login = credential_status(name, config)[0]
    missing = missing_secrets(config)
    sync = read_sync(root)
    changed = local_changes(root)
    cloud = "not synced from this machine" if sync is None else (
        f"revision {sync.get('revision')} from {sync.get('url')}" + (" + local changes" if changed else ""))
    data = {
        "name": name, "description": config.agent.description, "path": str(root),
        "model": f"{config.model.provider}/{config.model.name}", "credentials": login,
        "runtime": runtime_detail,
        "permissions": config.permissions.model_dump(),
        "skills": [skill.name for skill in config.resources.skills],
        "packages": [package.name for package in config.resources.packages],
        "mcp_servers": list(config.mcp.servers),
        "secrets": {secret: ("missing" if secret in missing else "set") for secret in config.secrets},
        "cloud": cloud,
    }
    if as_json:
        typer.echo(json.dumps(data, indent=2))
        return
    console.print(f"{name}" + (f" — {config.agent.description}" if config.agent.description else ""))
    rows = [
        ("Location", str(root)),
        ("Model", data["model"] + (f" ({provider.name})" if provider else "")),
        ("Credentials", login),
        ("Runtime", runtime_detail),
        ("Permissions", f"filesystem {config.permissions.filesystem}, shell {config.permissions.shell}"),
        ("Skills", ", ".join(data["skills"]) or "none"),
        ("Packages", ", ".join(data["packages"]) or "none"),
        ("MCP servers", ", ".join(data["mcp_servers"]) or "none"),
        ("Secrets", ", ".join(f"{key} ({value})" for key, value in data["secrets"].items()) or "none"),
        ("Cloud", cloud),
    ]
    for label, value in rows:
        console.print(f"  {label + ':':<13} {value}")


@agent_app.command("run", cls=RunCommand, context_settings=RUN_SETTINGS)
def run_agent(
    context: typer.Context,
    name: AgentArg,
    project_root: Annotated[Path | None, typer.Option("--project-root", help="Project directory containing the current directory (default: cwd).")] = None,
    session: Annotated[str | None, typer.Option("--session", help="Resume a session ID or file for this agent and project.")] = None,
    no_project_context: Annotated[bool, typer.Option("--no-project-context", help="Omit root AGENTS.md and CLAUDE.md instructions.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Prepare and print the launch as JSON without starting Pi.")] = False,
) -> None:
    """Run an agent in the current directory. Pass Pi options after --.

    Examples:

      mantle agent run reviewer

      mantle agent run reviewer -- --print "Summarize this project."
    """
    if not store().exists(name):
        if paths().agent(name).exists():
            store().get(name)
        try:
            client = CloudClient(paths())
        except MantleError:
            fail(NotFoundError(f'Agent "{name}" is not installed locally. Create it with: mantle agent create {name}  (see all agents: mantle agent list)'))
        try:
            client.set_timeout(5)
            with step(f'"{name}" is not installed locally; checking Mantle Cloud…'):
                found = client.exists(name)
        except MantleError as error:
            fail(MantleError(f'Agent "{name}" is not installed locally, and Mantle Cloud could not be checked: {error}\nCreate it with: mantle agent create {name}'))
        if found:
            typer.echo(
                f"Agent '{name}' is available in Mantle Cloud but is not installed locally.\n\n"
                f"Pull it with:\n\n  mantle cloud pull {name}", err=True
            )
            raise typer.Exit(1)
        fail(NotFoundError(f'Agent "{name}" was not found locally or in Mantle Cloud. Create it with: mantle agent create {name}'))
    forwarded = list(context.args)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    spec = AgentLauncher(paths()).build(
        name,
        current=Path.cwd(),
        project_root=project_root,
        project_context=not no_project_context,
        session=session,
        pi_arguments=forwarded,
    )
    for message in spec.notices:
        err_console.print(f"Note: {message}")
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


def _edit_hints(name: str, config: AgentConfig, steps: list[tuple[str, str]]) -> None:
    steps += credential_status(name, config)[1]
    if read_sync(paths().agent(name)) is not None:
        steps.append((f"mantle cloud push {name}", "publish the change to Mantle Cloud"))
    next_steps(*steps)


@agent_app.command("edit", cls=UserCommand)
def edit_agent(
    name: AgentArg,
    provider: Annotated[str | None, typer.Option("--provider", "-p", help="Switch to this model provider without opening an editor (with its recommended model unless --model is given).")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Switch to this model without opening an editor.")] = None,
) -> None:
    """Open agent.yaml in your editor and validate it before saving.

    With --provider or --model, change the model directly; the rest of agent.yaml,
    including comments, is kept as it is.

    Examples:

      mantle agent edit reviewer

      mantle agent edit reviewer --provider openai-codex
    """
    if provider is not None or model is not None:
        config = store().get(name)
        select_model(config, provider, model)
        store().save(config)
        console.print(f"✔ {name} now uses {config.model.provider}/{config.model.name}")
        _edit_hints(name, config, [])
        return
    path = paths().yaml(name)
    if not path.is_file():
        store().get(name)
    original = path.read_text(encoding="utf-8")
    try:
        before = parse_agent_yaml(original, expected_name=name)
    except MantleError:
        before = None
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or next((e for e in ("nano", "vi") if shutil.which(e)), None)
    if not editor:
        fail(MantleError(f"No editor found. Set EDITOR, or edit {path} directly and run: mantle agent validate {name}"))
    draft = paths().agent(name) / "tmp" / "agent.edit.yaml"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text(original, encoding="utf-8")
    try:
        while True:
            try:
                subprocess.run([*shlex.split(editor), str(draft)], check=False)
            except OSError as error:
                fail(MantleError(f'Could not start the editor "{editor}": {error.strerror or error}. Set VISUAL or EDITOR '
                                 f"to an installed editor, or edit {path} directly and run: mantle agent validate {name}"))
            text = draft.read_text(encoding="utf-8")
            if text == original:
                console.print("No changes.")
                return
            try:
                config = parse_agent_yaml(text, expected_name=name)
                break
            except MantleError as error:
                err_console.print(f"Error: {error}")
                if not interactive() or not typer.confirm("Edit again? (No discards your changes)", default=True):
                    fail(MantleError("Changes discarded; agent.yaml was not modified."))
        atomic_write_text(path, text, mode=0o644)
    finally:
        draft.unlink(missing_ok=True)
    console.print(f"✔ Saved and validated {path}")
    added_skills = [s for s in config.resources.skills if before is None or s not in before.resources.skills]
    added_packages = [p for p in config.resources.packages if before is None or p not in before.resources.packages]
    steps = [(f"mantle skills add {s.source} --skill {s.name} --agent {name} --force", "install the new skill") for s in added_skills]
    steps += [(f"mantle package add {p.source} --name {p.name} --agent {name} --force", "install the new package") for p in added_packages]
    _edit_hints(name, config, steps)


@agent_app.command("delete", cls=UserCommand)
def delete_agent(
    name: AgentArg,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the local deletion confirmation.")] = False,
    keep_sessions: Annotated[bool, typer.Option("--keep-sessions", help="Move sessions to MANTLE_HOME/retained-sessions before deleting.")] = False,
) -> None:
    """Delete a local agent. The Cloud copy is untouched."""
    store().require_local(name)
    if not yes and not typer.confirm(f'Delete local agent "{name}"?', default=False):
        raise typer.Abort()
    kept = store().delete(name, keep_sessions=keep_sessions)
    console.print(f"Deleted local agent: {name}")
    if kept:
        console.print(f"Sessions retained at: {kept}")


@agent_app.command("validate", cls=UserCommand)
def validate(target: Annotated[str, typer.Argument(help="Agent name or path to an agent.yaml file.", autocompletion=complete_agent, show_default=False, metavar="AGENT|PATH")]) -> None:
    """Validate an agent's YAML syntax and the Mantle schema."""
    path = Path(target).expanduser()
    looks_like_path = path.exists() or "/" in target or target.endswith((".yaml", ".yml"))
    expected = None
    if not looks_like_path:
        validate_agent_name(target)
        path, expected = paths().yaml(target), target
        if not path.is_file():
            fail(NotFoundError(f'Agent "{target}" was not found. Pass an agent name or a path to an agent.yaml file.'))
    elif path.name == "agent.yaml" and path.resolve().parent.parent == paths().agents:
        expected = path.resolve().parent.name  # An installed agent's file must declare that agent.
    config = load_agent_yaml(path, expected_name=expected)
    console.print(f"✔ Valid Mantle configuration for {config.agent.name}.")


# Skills and packages ---------------------------------------------------------

def derive_skill_name(source: str) -> str | None:
    """A local skill directory's name: the `name` in its SKILL.md, which installers match, not the folder name."""
    local = Path(source).expanduser()
    if local.is_dir() and (local / "SKILL.md").is_file():
        return frontmatter_name(local / "SKILL.md") or local.resolve().name
    return None


def points_to_one_skill(source: str) -> bool:
    """A GitHub tree or blob URL names one skill's directory; the skill's own name is read when it is installed."""
    parsed = urlparse(source)
    return bool(parsed.scheme) and any(marker in parsed.path for marker in ("/tree/", "/blob/"))


def _add_skill(agent: str, source: str, skill: str | None, force: bool) -> None:
    skill = skill or derive_skill_name(source)
    if not skill and not points_to_one_skill(source):
        fail(MantleError("Name the skill inside SOURCE with --skill NAME (a repository can contain many skills)."))
    record = SkillManager(paths()).add(source, skill, agent, force=force)
    console.print(f"Installed skill {record.name} into {agent}. agent.yaml changed locally; Cloud was not updated.")
    if read_sync(paths().agent(agent)) is not None:
        next_steps((f"mantle cloud push {agent}", "publish the change to Mantle Cloud"))


def _add_package(agent: str, source: str, name: str | None, force: bool) -> None:
    record = PackageManager(paths()).add(source, agent, name=name, force=force)
    console.print(f"Installed package {record.name} into {agent}. agent.yaml changed locally; Cloud was not updated.")
    if read_sync(paths().agent(agent)) is not None:
        next_steps((f"mantle cloud push {agent}", "publish the change to Mantle Cloud"))


@skills_app.command("add", cls=UserCommand)
def skill_add(
    source: Annotated[str, typer.Argument(help="Git repository, GitHub tree URL or local directory.", show_default=False, metavar="SOURCE")],
    agent: AgentOption,
    skill: Annotated[str | None, typer.Option("--skill", "-s", help="Name of the skill within SOURCE (read from SKILL.md for tree URLs and local skill directories).")] = None,
    force: Annotated[bool, typer.Option("--force", help="Replace an existing skill of the same name.")] = False,
) -> None:
    """Install a skill into one agent and record it in its agent.yaml.

    Example: mantle skills add https://github.com/vercel-labs/agent-skills --skill web-design-guidelines --agent reviewer
    """
    _add_skill(agent, source, skill, force)


@skills_app.command("remove", cls=UserCommand)
def skill_remove(skill: Annotated[str, typer.Argument(help="Skill name.", show_default=False, metavar="SKILL")], agent: AgentOption) -> None:
    """Remove a skill from one agent."""
    SkillManager(paths()).remove(skill, agent)
    console.print(f"Removed skill {skill} from {agent}. Cloud was not updated.")


@skills_app.command("list", cls=UserCommand)
def skill_list(agent: AgentOption) -> None:
    """List an agent's skills."""
    config = store().get(agent)
    if not config.resources.skills:
        console.print(f"{agent} has no skills. Add one with: mantle skills add SOURCE --skill NAME --agent {agent}")
        return
    table = Table("SKILL", "SOURCE", "INSTALLED")
    for skill in config.resources.skills:
        installed = (paths().agent(agent) / "skills" / skill.name / "SKILL.md").is_file()
        table.add_row(skill.name, skill.source, "yes" if installed else "missing")
    console.print(table)


@package_app.command("add", cls=UserCommand)
def package_add(
    source: Annotated[str, typer.Argument(help="npm:package@version or a Git URL.", show_default=False, metavar="SOURCE")],
    agent: AgentOption,
    name: Annotated[str | None, typer.Option("--name", help="Local resource name (default: derived from source).")] = None,
    force: Annotated[bool, typer.Option("--force", help="Replace an existing package of the same name.")] = False,
) -> None:
    """Install a Pi package into one agent and record it in its agent.yaml.

    Example: mantle package add npm:pi-lens@4.2.1 --agent reviewer
    """
    _add_package(agent, source, name, force)


@package_app.command("remove", cls=UserCommand)
def package_remove(name: Annotated[str, typer.Argument(help="Package name.", show_default=False, metavar="NAME")], agent: AgentOption) -> None:
    """Remove a Pi package from one agent."""
    PackageManager(paths()).remove(name, agent)
    console.print(f"Removed package {name} from {agent}. Cloud was not updated.")


@package_app.command("list", cls=UserCommand)
def package_list(agent: AgentOption) -> None:
    """List an agent's packages."""
    config = store().get(agent)
    if not config.resources.packages:
        console.print(f"{agent} has no packages. Add one with: mantle package add npm:NAME@VERSION --agent {agent}")
        return
    table = Table("PACKAGE", "SOURCE")
    for package in config.resources.packages:
        table.add_row(package.name, package.source)
    console.print(table)


# Cloud -----------------------------------------------------------------------

@cloud_app.command("push", cls=UserCommand)
def push_agent(name: AgentArg) -> None:
    """Validate and upload this agent's agent.yaml to Mantle Cloud (nothing else is sent)."""
    config = store().get(name)
    yaml_text = paths().yaml(name).read_text(encoding="utf-8")
    parse_agent_yaml(yaml_text, expected_name=name)
    client = CloudClient(paths())
    with step(f"Uploading {name} to {client.base_url}…"):
        result = client.put(name, yaml_text)
    record_sync(paths().agent(name), client.base_url, result["revision"]["number"], yaml_text)
    if result["created"]:
        console.print(f"Pushed {config.agent.name}: Cloud revision {result['revision']['number']}")
    else:
        console.print(f"Cloud already has this exact configuration (revision {result['revision']['number']}).")


def _install_hints(name: str, config: AgentConfig) -> None:
    steps = credential_status(name, config)[1]
    steps += secret_steps(config, skip=steps)
    steps.append((f"mantle agent run {name}", "start it in the current project directory"))
    next_steps(*steps)


@cloud_app.command("pull", cls=UserCommand)
def pull_agent(name: AgentArg) -> None:
    """Download an agent from Mantle Cloud and set it up on this machine."""
    validate_agent_name(name)
    if paths().agent(name).exists():
        fail(MantleError(f'Agent "{name}" already exists locally. Get the latest Cloud revision with: mantle cloud update {name}'))
    client = CloudClient(paths())
    with step(f"Fetching {name} from {client.base_url}…"):
        remote = client.latest(name)
    config = parse_agent_yaml(remote["yaml"], expected_name=name)
    Provisioner(paths()).install(config, yaml_text=remote["yaml"])
    record_sync(paths().agent(name), client.base_url, remote["revision"]["number"], remote["yaml"])
    console.print(f"Pulled {name} from Cloud revision {remote['revision']['number']}.")
    _install_hints(name, config)


def _summarize_changes(before: AgentConfig | None, after: AgentConfig) -> list[str]:
    if before is None:
        return []
    changes = []
    if (before.model.provider, before.model.name) != (after.model.provider, after.model.name):
        changes.append(f"model {before.model.provider}/{before.model.name} → {after.model.provider}/{after.model.name}")
    if before.runtime.version != after.runtime.version:
        changes.append(f"runtime pi@{before.runtime.version} → pi@{after.runtime.version}")
    for field in ("filesystem", "shell"):
        old, new = getattr(before.permissions, field), getattr(after.permissions, field)
        if old != new:
            changes.append(f"permissions.{field} {old} → {new}")
    if before.system_prompt != after.system_prompt:
        changes.append("system prompt changed")
    for label, old, new in (
        ("skill", {s.name for s in before.resources.skills}, {s.name for s in after.resources.skills}),
        ("package", {p.name for p in before.resources.packages}, {p.name for p in after.resources.packages}),
        ("MCP server", set(before.mcp.servers), set(after.mcp.servers)),
    ):
        changes += [f"+ {label} {item}" for item in sorted(new - old)]
        changes += [f"- {label} {item}" for item in sorted(old - new)]
    return changes


def _diff(local: str, remote: str, name: str, revision: int) -> list[str]:
    return list(difflib.unified_diff(
        local.splitlines(keepends=True), remote.splitlines(keepends=True),
        fromfile=f"{name}/agent.yaml (this machine)", tofile=f"{name}/agent.yaml (Cloud revision {revision})",
    ))


@cloud_app.command("update", cls=UserCommand)
def update_agent(
    name: AgentArg,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Replace local agent.yaml changes without asking (a backup is still kept).")] = False,
) -> None:
    """Replace this agent's configuration and resources with the latest Cloud revision.

    Local context, logins and sessions are kept. If agent.yaml has changes that
    were never pushed, Mantle shows the difference and asks first, and always
    keeps a backup of the replaced file.
    """
    root = store().require_local(name)
    client = CloudClient(paths())
    with step(f"Fetching {name} from {client.base_url}…"):
        remote = client.latest(name)
    revision = remote["revision"]["number"]
    config = parse_agent_yaml(remote["yaml"], expected_name=name)
    yaml_path = paths().yaml(name)
    local_text = yaml_path.read_text(encoding="utf-8") if yaml_path.is_file() else None
    try:
        before = parse_agent_yaml(local_text, expected_name=name) if local_text is not None else None
    except MantleError:
        before = None  # A damaged file has nothing worth protecting beyond the backup.
    backup = None
    edited = local_changes(root) is not False  # True, or unknown for agents never synced here.
    if local_text is not None and local_text != remote["yaml"] and (edited or before is None):
        if before is not None:
            console.print(f"{name}/agent.yaml differs from Cloud revision {revision}:")
            console.print("".join(_diff(local_text, remote["yaml"], name, revision)).rstrip())
            if not yes:
                if not interactive():
                    fail(MantleError(
                        f"agent.yaml has local changes that are not in Cloud. Keep them with `mantle cloud push {name}`, "
                        f"or replace them with `mantle cloud update {name} --yes` (a backup is kept)."
                    ))
                if not typer.confirm("Replace the local agent.yaml with the Cloud version?", default=False):
                    raise typer.Abort()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = paths().home / "backups" / name / f"agent-{stamp}.yaml"
        atomic_write_text(backup, local_text, mode=0o600)
    manager = RuntimeManager(paths())
    default_before = manager.config().default_runtime
    Provisioner(paths()).update(config, yaml_text=remote["yaml"])
    record_sync(root, client.base_url, revision, remote["yaml"])
    console.print(f"Updated {name} to Cloud revision {revision}.")
    for change in _summarize_changes(before, config):
        console.print(f"  {change}")
    if backup:
        console.print(f"Previous agent.yaml saved to {backup}")
    default_after = manager.config().default_runtime
    if default_before and default_after != default_before:
        others = [agent for agent in manager.latest_users() if agent != name]
        message = f"The shared Pi default moved from {default_before} to {default_after}"
        console.print(message + (f"; it also applies to: {', '.join(others)}." if others else "."))


@cloud_app.command("diff", cls=UserCommand)
def diff_agent(name: AgentArg) -> None:
    """Show how this machine's agent.yaml differs from the latest Cloud revision."""
    path = paths().yaml(name)
    store().require_local(name)
    local = path.read_text(encoding="utf-8") if path.is_file() else ""
    client = CloudClient(paths())
    with step(f"Fetching {name} from {client.base_url}…"):
        remote = client.latest(name)
    lines = _diff(local, remote["yaml"], name, remote["revision"]["number"])
    if not lines:
        console.print(f"{name} matches Cloud revision {remote['revision']['number']}.")
        return
    typer.echo("".join(lines).rstrip())
    next_steps((f"mantle cloud push {name}", "publish this machine's version"),
               (f"mantle cloud update {name}", "take the Cloud version"))


# Runtime ---------------------------------------------------------------------

def _announce_default(manager: RuntimeManager, previous: str | None, current: str | None) -> None:
    if current is None or previous == current:
        return
    users = manager.latest_users()
    affected = f" ({', '.join(users)})" if users else ""
    console.print(f"pi@{current} is now the default; agents on `latest`{affected} use it from their next run.")


@runtime_app.command("install", cls=UserCommand)
def install_runtime(
    version: Annotated[str, typer.Argument(help="Pi version, e.g. 1.0.0, or latest.", metavar="VERSION")] = "latest",
    default: Annotated[bool | None, typer.Option("--default/--no-default", help="Make it the version agents on `latest` use (default for `latest`, and for the first runtime).")] = None,
) -> None:
    """Download a Pi version into the shared cache (default: latest from npm)."""
    version = version.removeprefix("pi@")
    RuntimeManager.validate_version(version)
    manager = RuntimeManager(paths())
    previous = manager.config().default_runtime
    latest = version in ("latest", "pi")
    selected = manager.latest_version() if latest else version
    cached = manager.installed(selected)
    metadata = manager.install(selected, adopt_default=default is not False)
    console.print(f"pi@{metadata.version} is already downloaded." if cached else f"Installed runtime: pi@{metadata.version}")
    if default if default is not None else latest:
        manager.set_default(metadata.version)
    _announce_default(manager, previous, manager.config().default_runtime)


@runtime_app.command("use", cls=UserCommand)
def use_runtime(version: Annotated[str, typer.Argument(help="Pi version to use for agents on `latest`.", metavar="VERSION")]) -> None:
    """Choose which installed Pi version agents on `latest` run (installs it if needed)."""
    version = version.removeprefix("pi@")
    RuntimeManager.validate_version(version)
    manager = RuntimeManager(paths())
    previous = manager.config().default_runtime
    metadata = manager.ensure(version) if version != "latest" else manager.install()
    manager.set_default(metadata.version)
    _announce_default(manager, previous, metadata.version)
    if previous == metadata.version:
        console.print(f"pi@{metadata.version} is already the default.")


@runtime_app.command("list", cls=UserCommand)
def list_runtimes() -> None:
    """List downloaded Pi versions, the default, and which agents use each."""
    manager = RuntimeManager(paths())
    default = manager.config().default_runtime
    runtimes = manager.list()
    if not runtimes:
        console.print("No Pi runtime downloaded yet. The first `mantle agent run` downloads one, or run: mantle runtime install")
        return
    table = Table("RUNTIME", "DEFAULT", "USED BY")
    for runtime in runtimes:
        table.add_row(f"pi@{runtime.version}", "yes" if runtime.version == default else "", ", ".join(manager.users(runtime.version)))
    console.print(table)
    note_damaged(store().scan()[1])


@runtime_app.command("remove", cls=UserCommand)
def remove_runtime(version: Annotated[str, typer.Argument(help="Pi version to remove.", metavar="VERSION")]) -> None:
    """Remove an unused Pi version from the cache."""
    RuntimeManager(paths()).remove(version.removeprefix("pi@"))
    console.print(f"Removed runtime: pi@{version.removeprefix('pi@')}")
    note_damaged(store().scan()[1])  # Their runtime could not be read, so they were not checked.


# `mantle create` and `mantle run` are shorter spellings of the agent commands, hidden from help.
app.command("create", cls=UserCommand, hidden=True)(create_agent)
app.command("run", cls=RunCommand, hidden=True, context_settings=RUN_SETTINGS)(run_agent)


def main() -> None:
    app()
