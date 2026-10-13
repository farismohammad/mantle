"""Helpers shared by every CLI command module."""
from __future__ import annotations

import os
import subprocess
import sys

import typer
from pydantic import ValidationError
from rich.console import Console
from typer.core import TyperCommand

from mantle.agents import AgentStore
from mantle.configuration import format_validation_error
from mantle.errors import MantleError
from mantle.models import AgentConfig
from mantle.paths import MantlePaths

console = Console(markup=False, highlight=False, soft_wrap=True)
err_console = Console(stderr=True, markup=False, highlight=False, soft_wrap=True)

COMMANDS_PANEL = "Commands"
SETUP_PANEL = "Setup and maintenance"


def command_group(help: str) -> typer.Typer:
    """A command group that shows its help, successfully, when run without a command."""
    group = typer.Typer(help=help)

    @group.callback(invoke_without_command=True)
    def show_help(context: typer.Context) -> None:
        if context.invoked_subcommand is None:
            typer.echo(context.get_help())
            raise typer.Exit()

    return group


def fail(error: Exception) -> None:
    typer.echo(f"Error: {error}", err=True)
    raise typer.Exit(1)


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
            if error.filename in {"npm", "npx", "node"}:
                fail(MantleError(
                    f"Required program {error.filename!r} was not found. Install Node.js/npm (22.20 or newer) "
                    "and ensure it is on PATH. Run `mantle doctor` to check your setup."
                ))
            fail(error)
        except (MantleError, OSError, UnicodeError) as error:
            fail(error)


class RunCommand(UserCommand):
    """`mantle agent run` forwards Pi options only after `--`; say so when they are misplaced."""

    def parse_args(self, ctx, args):
        try:
            return super().parse_args(ctx, args)
        except Exception as error:
            if type(error).__name__ == "NoSuchOption" and hasattr(error, "message"):
                option = getattr(error, "option_name", "") or "OPTION"
                error.message += f"\nTo pass options to Pi, put them after --, for example: mantle agent run AGENT -- {option} …"
            raise


def paths() -> MantlePaths:
    return MantlePaths.discover()


def store() -> AgentStore:
    return AgentStore(paths())


def readable_agents() -> list[AgentConfig]:
    """Agents whose agent.yaml can be read, with a note naming any that were skipped."""
    agents, damaged = store().scan()
    note_damaged(damaged)
    return agents


def note_damaged(damaged: dict[str, str]) -> None:
    if damaged:
        err_console.print(f"Note: skipped damaged agent(s): {', '.join(damaged)}. See why with: mantle doctor")


def complete_agent(incomplete: str) -> list[str]:
    try:
        root = paths().agents
        return sorted(item.name for item in root.iterdir()
                      if item.is_dir() and not item.name.startswith(".") and item.name.startswith(incomplete))
    except OSError:
        return []


def prompt_secret(text: str) -> str:
    """Ask without echoing. Piped input is never echoed, so it is read without getpass's terminal warning."""
    return typer.prompt(text, hide_input=sys.stdin.isatty()).strip()


def interactive() -> bool:
    """Whether prompts may be shown. MANTLE_INTERACTIVE=1/0 overrides terminal detection."""
    override = os.environ.get("MANTLE_INTERACTIVE")
    if override in {"0", "1"}:
        return override == "1"
    return sys.stdin.isatty() and sys.stdout.isatty()


def next_steps(*commands: tuple[str, str]) -> None:
    """Print a short "Next:" block of commands with explanations."""
    shown = [(command, why) for command, why in commands if command]
    if not shown:
        return
    width = max(len(command) for command, _ in shown)
    console.print("\nNext:")
    for command, why in shown:
        console.print(f"  {command.ljust(width)}   # {why}" if why else f"  {command}")
