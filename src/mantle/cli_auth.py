"""`mantle auth` (shared provider logins) and `mantle auth secrets` (other agent secrets)."""
from __future__ import annotations

import json
import os
import sys
from typing import Annotated

import typer
from rich.table import Table

from mantle.cli_common import (
    UserCommand, command_group, complete_agent, console, err_console, fail, interactive, paths, prompt_secret,
    readable_agents, store,
)
from mantle.credentials import (
    CredentialStore, auth_file_problem, describe, is_isolated, isolate_agent, mask, provider_credential, share_agent,
    shared_api_key_environment,
)
from mantle.errors import MantleError
from mantle.login import oauth_login
from mantle.models import AgentConfig
from mantle.progress import step
from mantle.providers import COMMON, PROVIDERS, Provider, check_api_key, env_variables, lookup
from mantle.secrets import parse_dotenv, remove_dotenv_value, set_dotenv_value

auth_app = command_group("Sign in to model providers once for every agent, and manage other secrets.")
secrets_app = command_group("Manage other secrets (MCP tokens, custom keys) in MANTLE_HOME/.env.")
auth_app.add_typer(secrets_app, name="secrets")


def _provider_users(provider_id: str, agents: list[AgentConfig] | None = None) -> list[str]:
    return [agent.agent.name for agent in (store().list() if agents is None else agents) if agent.model.provider == provider_id]


def choose_provider(prompt: str = "Provider") -> str:
    signed_in = CredentialStore(paths()).load()
    console.print("Model providers:")
    for index, provider_id in enumerate(COMMON, 1):
        provider = PROVIDERS[provider_id]
        methods = " / ".join(filter(None, ("API key" if provider.api_key else "", "browser login" if provider.oauth else "")))
        status = "  ✔ signed in" if provider_id in signed_in else ""
        console.print(f"  {index:>2}. {provider.name} [{provider_id}] ({methods}){status}")
    console.print("  Or type any other Pi provider ID (see `mantle auth providers`).")
    while True:
        answer = typer.prompt(prompt).strip()
        if not answer.isdigit():
            return answer
        if 1 <= int(answer) <= len(COMMON):
            return COMMON[int(answer) - 1]
        err_console.print(f"Choose a number from 1 to {len(COMMON)}, or type a provider ID.")


def _choose_method(provider: Provider) -> str:
    if provider.oauth and not provider.api_key:
        return "oauth"
    if provider.api_key and not provider.oauth:
        return "api_key"
    console.print(f"How do you want to sign in to {provider.name}?")
    console.print("  1. Paste an API key")
    console.print("  2. Sign in with your account in a browser (subscription plans)")
    return "oauth" if typer.prompt("Choose 1 or 2", default="1").strip() == "2" else "api_key"


def login_provider(
    provider_id: str,
    *,
    method: str | None = None,
    key: str | None = None,
    from_env: bool = False,
    verify: bool = True,
) -> bool:
    """Authenticate one provider for every agent. Returns whether a credential is stored."""
    mantle_paths = paths()
    provider = lookup(provider_id)
    if provider is None:
        if not provider_id or not all(character.isalnum() or character in "-_." for character in provider_id):
            raise MantleError(f'"{provider_id}" is not a valid provider ID. List known providers with: mantle auth providers')
        err_console.print(f'Note: "{provider_id}" is not a built-in Pi provider. Mantle stores the key; '
                          "the provider must be defined in the agent's models.json.")
        provider = Provider(provider_id, provider_id)
    if from_env:
        variables = provider.env or (provider_id.upper().replace("-", "_") + "_API_KEY",)
        found = next((variable for variable in variables if os.environ.get(variable)), None)
        if found is None:
            raise MantleError(f"None of {', '.join(variables)} is set in this shell.")
        key, method = os.environ[found], "api_key"
    if key is not None:
        method = "api_key"
    if method is None:
        if not interactive():
            raise MantleError(
                f"Choose how to sign in: --key-stdin (pipe the key), --from-env, or --oauth "
                f"(interactive). Example: printenv {provider.primary_env or 'API_KEY'} | mantle auth login {provider_id} --key-stdin"
            )
        method = _choose_method(provider) if lookup(provider_id) else "api_key"
    if method == "oauth":
        if not provider.oauth:
            raise MantleError(f"{provider.name} does not offer a browser login in Pi. Use an API key instead.")
        if not interactive():
            raise MantleError("Browser login needs an interactive terminal.")
        outcome = oauth_login(mantle_paths, provider.id)
        if outcome != "saved":
            kept = " Your existing login is unchanged." if CredentialStore(mantle_paths).get(provider.id) else ""
            err_console.print(f"No new {provider.name} login was saved.{kept} "
                              f"Run `mantle auth login {provider.id} --oauth` to try again.")
            return False
        console.print(f"✔ {provider.name} login saved; every agent can use it.")
        _print_users(provider.id)
        return True
    if key is None:
        key = prompt_secret(f"{provider.name} API key")
    key = key.strip()
    if verify:
        with step(f"Checking the key with {provider.name}…"):
            result = check_api_key(provider, key)
        if result.status == "invalid":
            raise MantleError(f"{result.detail} Nothing was saved. Check the key, or save it without the check: "
                              f"mantle auth login {provider.id} --no-verify")
        if result.status == "unknown":
            err_console.print(f"Note: {result.detail}")
    CredentialStore(mantle_paths).set_api_key(provider.id, key)
    console.print(f"✔ Saved {provider.name} API key ({mask(key)}) for all agents.")
    _print_users(provider.id)
    return True


def _print_users(provider_id: str) -> None:
    """Say which agents use the new login, and which cannot because they are set to another provider."""
    agents = store().list()
    users = _provider_users(provider_id, agents)
    if users:
        console.print(f"  Used by: {', '.join(users)}")
    known = lookup(provider_id)
    if known is None or known.default_model is None:
        return
    mantle_paths = paths()
    others = [agent for agent in agents if agent.model.provider != provider_id
              and lookup(agent.model.provider) is not None
              and not is_isolated(root := mantle_paths.agent(agent.agent.name))
              and not auth_file_problem(mantle_paths, root)
              and not provider_credential(mantle_paths, root, agent.model.provider)]
    if others:
        listed = ", ".join(f"{agent.agent.name} ({agent.model.provider})" for agent in others)
        console.print(f"  Agents on other providers with no login yet: {listed}.")
        console.print(f"  Switch one to this login with: mantle agent edit {others[0].agent.name} --provider {provider_id}")


@auth_app.command("login", cls=UserCommand)
def auth_login(
    provider: Annotated[str | None, typer.Argument(help="Provider ID, such as anthropic, openai or github-copilot. Prompted when omitted.", metavar="PROVIDER")] = None,
    key_stdin: Annotated[bool, typer.Option("--key-stdin", help="Read the API key from standard input (for scripts).")] = False,
    from_env: Annotated[bool, typer.Option("--from-env", help="Save the provider's API-key variable from this shell, e.g. OPENAI_API_KEY.")] = False,
    oauth: Annotated[bool, typer.Option("--oauth", help="Sign in with your account in a browser.")] = False,
    no_verify: Annotated[bool, typer.Option("--no-verify", help="Save the key without checking it with the provider.")] = False,
) -> None:
    """Sign in to a model provider once; every agent can use the login.

    Examples:

      mantle auth login                      # pick a provider and method

      mantle auth login anthropic --oauth    # Claude subscription via browser

      printenv OPENAI_API_KEY | mantle auth login openai --key-stdin
    """
    if sum((key_stdin, from_env, oauth)) > 1:
        fail(MantleError("Use only one of --key-stdin, --from-env and --oauth."))
    if provider is None:
        if not interactive():
            fail(MantleError("Name the provider, for example: mantle auth login openai"))
        provider = choose_provider()
    key = sys.stdin.read().strip() if key_stdin else None
    if key_stdin and not key:
        fail(MantleError("No API key was received on standard input."))
    if not login_provider(provider, method="oauth" if oauth else None, key=key, from_env=from_env, verify=not no_verify):
        raise typer.Exit(1)


@auth_app.command("list", cls=UserCommand)
def auth_list(as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False) -> None:
    """Show provider logins available to agents (values are never printed)."""
    mantle_paths = paths()
    shared = CredentialStore(mantle_paths).load()
    agents = readable_agents()
    rows = []
    for provider_id, credential in sorted(shared.items()):
        rows.append({"provider": provider_id, "source": "Mantle (shared)", "detail": describe(credential), "used_by": _provider_users(provider_id, agents)})
    known = env_variables()
    try:
        dotenv = parse_dotenv(mantle_paths.dotenv)
    except MantleError:
        dotenv = {}
    for variable, provider_id in sorted(known.items()):
        if variable in dotenv and dotenv[variable]:
            rows.append({"provider": provider_id, "source": f".env ({variable})", "detail": f"API key {mask(dotenv[variable])}; only agents declaring {variable} in secrets", "used_by": []})
        if os.environ.get(variable):
            rows.append({"provider": provider_id, "source": f"shell ({variable})", "detail": f"not passed to agents unless declared; save it with: mantle auth login {provider_id} --from-env", "used_by": []})
    isolated = [agent.agent.name for agent in agents if is_isolated(mantle_paths.agent(agent.agent.name))]
    if as_json:
        typer.echo(json.dumps({"credentials": rows, "isolated_agents": isolated, "store": str(CredentialStore(mantle_paths).path)}, indent=2))
        return
    if not rows:
        console.print("No provider logins yet. Sign in once for all agents with: mantle auth login")
    else:
        table = Table("PROVIDER", "SOURCE", "DETAILS", "USED BY")
        for row in rows:
            table.add_row(row["provider"], row["source"], row["detail"], ", ".join(row["used_by"]))
        console.print(table)
    if isolated:
        console.print(f"Agents with their own logins (not shared): {', '.join(isolated)}")


@auth_app.command("providers", cls=UserCommand)
def auth_providers() -> None:
    """List providers Mantle knows, their sign-in methods and default models."""
    table = Table("PROVIDER", "NAME", "SIGN-IN", "API KEY VARIABLE", "DEFAULT MODEL")
    for provider in PROVIDERS.values():
        methods = " + ".join(filter(None, ("API key" if provider.api_key else "", "browser" if provider.oauth else "")))
        table.add_row(provider.id, provider.name, methods, provider.primary_env or "", provider.default_model or "")
    console.print(table)
    console.print("Other Pi providers (and models.json custom providers) also accept an API key: mantle auth login ID")


@auth_app.command("logout", cls=UserCommand)
def auth_logout(
    provider: Annotated[str, typer.Argument(help="Provider ID to remove.", metavar="PROVIDER")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Remove a shared provider login from Mantle (does not revoke it at the provider)."""
    credentials = CredentialStore(paths())
    if credentials.get(provider) is None:
        fail(MantleError(f'No shared login for "{provider}". See logins with: mantle auth list'))
    users = _provider_users(provider)
    warning = f" Agents using it: {', '.join(users)}." if users else ""
    if not yes and not typer.confirm(f'Remove the shared "{provider}" login?{warning}', default=False):
        raise typer.Abort()
    credentials.remove(provider)
    console.print(f"Removed the shared {provider} login. Revoke the key at the provider if it was exposed.")


@auth_app.command("isolate", cls=UserCommand)
def auth_isolate(
    agent: Annotated[str, typer.Argument(help="Agent that should keep its own logins.", autocompletion=complete_agent, metavar="AGENT")],
    copy_credentials: Annotated[bool, typer.Option("--copy-credentials", help="Start from a copy of the current shared logins.")] = False,
) -> None:
    """Give one agent its own provider logins instead of the shared ones."""
    root = store().require_local(agent)
    isolate_agent(paths(), root, copy_credentials=copy_credentials)
    console.print(f"{agent} now uses its own auth.json. Sign in inside Pi with /login, or undo with: mantle auth share {agent}")


@auth_app.command("share", cls=UserCommand)
def auth_share(agent: Annotated[str, typer.Argument(help="Agent to switch back to shared logins.", autocompletion=complete_agent, metavar="AGENT")]) -> None:
    """Switch an agent back to Mantle's shared provider logins."""
    root = store().require_local(agent)
    result = share_agent(paths(), root)
    for message in result.notices:
        err_console.print(message)
    console.print(f"{agent} uses the shared provider logins.")


@secrets_app.command("set", cls=UserCommand)
def secrets_set(
    name: Annotated[str, typer.Argument(help="Environment variable name, such as DOCS_MCP_TOKEN.", metavar="NAME")],
    value_stdin: Annotated[bool, typer.Option("--value-stdin", help="Read the value from standard input.")] = False,
) -> None:
    """Store a secret in MANTLE_HOME/.env (agents receive it when they list it under `secrets`)."""
    provider_id = env_variables().get(name)
    if provider_id:
        err_console.print(f"Tip: provider keys are easier to share with: mantle auth login {provider_id}")
    value = sys.stdin.read().strip() if value_stdin else prompt_secret(name)
    set_dotenv_value(paths().dotenv, name, value)
    console.print(f"Saved {name} in {paths().dotenv} (readable only by you).")


@secrets_app.command("list", cls=UserCommand)
def secrets_list() -> None:
    """List secret names in MANTLE_HOME/.env and which agents declare them."""
    values = parse_dotenv(paths().dotenv)
    agents = readable_agents()
    declared = {name for agent in agents for name in agent.secrets}
    names = sorted(set(values) | declared)
    if not names:
        console.print("No secrets yet. Add one with: mantle auth secrets set NAME")
        return
    # Agents receive declared provider-key variables from the shared login (see `agent run`), unless
    # they are isolated: then the variable is missing for them.
    shared = shared_api_key_environment(paths(), None)
    table = Table("NAME", "STATUS", "DECLARED BY")
    for name in names:
        users = [agent.agent.name for agent in agents if name in agent.secrets]
        isolated = [user for user in users if is_isolated(paths().agent(user))]
        if values.get(name) or os.environ.get(name) or not shared.get(name):
            status = "set in .env" if values.get(name) else "set in shell" if os.environ.get(name) else "missing"
        elif isolated and len(isolated) == len(users):
            status = f"missing for {', '.join(isolated)} (isolated)"
        else:
            status = f"from the shared {env_variables()[name]} login"
            if isolated:
                status += f"; missing for {', '.join(isolated)} (isolated)"
        table.add_row(name, status, ", ".join(users))
    console.print(table)


@secrets_app.command("remove", cls=UserCommand)
def secrets_remove(name: Annotated[str, typer.Argument(help="Secret name to remove from .env.", metavar="NAME")]) -> None:
    """Remove a secret from MANTLE_HOME/.env."""
    if not remove_dotenv_value(paths().dotenv, name):
        fail(MantleError(f"{name} is not in {paths().dotenv}."))
    console.print(f"Removed {name}.")


__all__ = ["auth_app", "secrets_app", "login_provider", "choose_provider"]
