"""`mantle setup` (guided first-run configuration) and `mantle doctor` (health checks)."""
from __future__ import annotations

import json
import os
from typing import Annotated

import typer

from mantle.agents import validate_agent_name
from mantle.cli_auth import choose_provider, login_provider
from mantle.cli_cloud import connect_cloud, discover, start_cloud
from mantle.cloud_server import docker_available
from mantle.cli_common import console, err_console, interactive, paths, store
from mantle.credentials import CredentialStore, preferred_provider
from mantle.doctor import Check, run_checks, prerequisite_checks
from mantle.errors import MantleError
from mantle.models import default_agent_config
from mantle.providers import env_variables, lookup
from mantle.runtimes import RuntimeManager

SYMBOLS = {"ok": "✔", "warn": "!", "fail": "✖", "info": "·"}


def print_checks(checks: list[Check]) -> None:
    group = None
    for check in checks:
        if check.group != group:
            group = check.group
            console.print(f"\n{group}")
        console.print(f"  {SYMBOLS[check.status]} {check.name}: {check.detail}")
        if check.fix and check.status != "ok":
            console.print(f"      → {check.fix}")


def doctor(
    as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False,
    offline: Annotated[bool, typer.Option("--offline", help="Skip the Mantle Cloud reachability check.")] = False,
) -> None:
    """Check prerequisites, logins, agents and Cloud, with a fix for each problem."""
    checks = run_checks(paths(), cloud=not offline)
    failed = any(check.status == "fail" for check in checks)
    if as_json:
        typer.echo(json.dumps([check.__dict__ for check in checks], indent=2))
    else:
        print_checks(checks)
        warnings = sum(check.status == "warn" for check in checks)
        console.print("\n" + ("✖ Some checks failed." if failed else f"✔ Ready{f' ({warnings} suggestion(s) above)' if warnings else ''}."))
    if failed:
        raise typer.Exit(1)


def _ask(question: str, default: bool, assume_yes: bool) -> bool:
    if assume_yes:
        return default
    return typer.confirm(question, default=default)


def _ask_agent_name() -> str:
    """Ask until the name is valid, before asking anything else about the agent."""
    while True:
        name = typer.prompt("  Agent name", default="assistant").strip()
        try:
            return validate_agent_name(name)
        except MantleError as error:
            err_console.print(f"  {error}")


def setup(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Accept the default answer to every question.")] = False,
) -> None:
    """Guided setup: check prerequisites, download Pi, sign in to providers, connect Cloud, create an agent.

    Every step can be skipped and run later on its own; setup is safe to rerun.
    """
    if not interactive() and not yes:
        err_console.print("Setup is interactive. Rerun it in a terminal, or use --yes to accept defaults.")
        raise typer.Exit(2)
    mantle_paths = paths()
    console.print("Welcome to Mantle. This sets up everything once; each step can be skipped.\n")

    console.print("1. Checking prerequisites")
    checks = prerequisite_checks()
    print_checks(checks)
    if any(check.status == "fail" for check in checks):
        console.print("\nFix the items marked ✖, then run `mantle setup` again.")
        raise typer.Exit(1)

    console.print("\n2. Pi runtime")
    manager = RuntimeManager(mantle_paths)
    installed = manager.list()
    if installed:
        console.print(f"  Pi {installed[-1].version} is already downloaded.")
    elif _ask("  Download the Pi runtime now so your first run starts immediately?", True, yes):
        try:
            metadata = manager.install()
            manager.set_default(metadata.version)
        except (MantleError, OSError) as error:
            err_console.print(f"  Could not download Pi now ({error}). It will be downloaded on first run.")

    console.print("\n3. Model providers (shared by all agents)")
    credentials = CredentialStore(mantle_paths)
    signed_in = set(credentials.load())
    known = env_variables()
    for variable, provider_id in sorted(known.items()):
        provider = lookup(provider_id)
        if provider_id in signed_in or not os.environ.get(variable) or provider is None or variable != provider.primary_env:
            continue
        if _ask(f"  {variable} is set in your shell. Save it as the {provider.name} login for all agents?", True, yes):
            try:
                if login_provider(provider_id, from_env=True, verify=True):
                    signed_in.add(provider_id)
            except MantleError as error:
                err_console.print(f"  {error}")
    if signed_in:
        console.print(f"  Signed in: {', '.join(sorted(signed_in))}")
    elif yes:
        console.print("  Skipped. Sign in later with: mantle auth login")
    while not yes:
        question = "  Sign in to another model provider?" if signed_in else "  Sign in to a model provider now?"
        if not typer.confirm(question, default=not signed_in):
            break
        provider_id = choose_provider("  Provider")
        try:
            if login_provider(provider_id):
                signed_in.add(provider_id)
        except MantleError as error:
            err_console.print(f"  {error}")
        except typer.Abort:
            break

    console.print("\n4. Mantle Cloud (optional; syncs agent definitions between machines)")
    try:
        configured = bool(os.environ.get("MANTLE_CLOUD_URL") or RuntimeManager(mantle_paths).config().cloud_url)
    except MantleError:
        configured = False
    if configured:
        console.print("  Already connected. Check it with: mantle cloud status")
    elif yes:
        console.print("  Skipped. Connect later with: mantle cloud connect")
    elif typer.confirm("  Use a Mantle Cloud registry?", default=False):
        try:
            found = discover()
            if (not found and docker_available()
                    and typer.confirm("  None is running on this machine. Start one here with Docker?", default=True)):
                start_cloud(hints=False)
            else:
                connect_cloud(None, discovered=found, suggest_start=False)
        except MantleError as error:
            err_console.print(f"  {error}\n  Later, run one with `mantle cloud start` or connect with `mantle cloud connect URL`.")

    console.print("\n5. Your first agent")
    agents = store().list()
    created = None
    if agents:
        console.print(f"  You already have: {', '.join(agent.agent.name for agent in agents)}")
    elif _ask("  Create an agent now?", True, yes):
        default_provider = preferred_provider(mantle_paths)
        name = "assistant" if yes else _ask_agent_name()
        provider_id = default_provider if yes else typer.prompt("  Provider", default=default_provider)
        provider = lookup(provider_id)
        model = (provider.default_model if provider else None) or ""
        model = model if (yes and model) else typer.prompt("  Model", default=model or None)
        try:
            config = default_agent_config(name)
            config.model.provider, config.model.name = provider_id, model
            store().create(name, config=config)
            created = name
            console.print(f"  ✔ Created {name} ({provider_id}/{model})")
        except MantleError as error:
            err_console.print(f"  {error}\n  Create one later with: mantle agent create NAME")

    console.print("\nSetup complete.")
    console.print("  mantle doctor                # re-check everything at any time")
    if created:
        console.print(f"  cd your-project && mantle agent run {created}")
    else:
        console.print("  mantle agent create NAME     # create an agent")
