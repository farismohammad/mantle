"""Environment and agent health checks shared by `mantle doctor`, setup and the installer."""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass

from mantle.agents import AgentStore
from mantle.credentials import (
    CredentialStore, alternative_provider, auth_file_problem, is_isolated, login_step, provider_credential,
    shared_api_key_environment,
)
from mantle.errors import MantleError
from mantle.launcher import AgentLauncher
from mantle.paths import MantlePaths
from mantle.providers import lookup
from mantle.secrets import parse_dotenv

MIN_NODE = (22, 20, 0)


@dataclass
class Check:
    group: str
    name: str
    status: str  # "ok", "warn", "fail" or "info"
    detail: str
    fix: str = ""


def _version(command: list[str]) -> str | None:
    executable = shutil.which(command[0])
    if executable is None:
        return None
    try:
        result = subprocess.run([executable, *command[1:]], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (result.stdout or result.stderr).strip().splitlines()[0] if result.returncode == 0 and (result.stdout or result.stderr).strip() else ""


def parse_node_version(text: str) -> tuple[int, int, int] | None:
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", text.strip())
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def node_install_hint() -> str:
    if platform.system() == "Darwin":
        return "Install Node.js 22 LTS: brew install node@22, or rerun the Mantle installer with --with-node."
    return "Install Node.js 22 LTS with your version manager (fnm/nvm) or rerun the Mantle installer with --with-node."


def prerequisite_checks() -> list[Check]:
    checks: list[Check] = []
    system = platform.system()
    if system in {"Linux", "Darwin"}:
        checks.append(Check("System", "Operating system", "ok", f"{system} {platform.machine()}"))
    else:
        checks.append(Check("System", "Operating system", "fail", f"{system} is not supported", "Use Linux, macOS or WSL."))
    node = _version(["node", "--version"])
    parsed = parse_node_version(node or "")
    if node is None:
        checks.append(Check("System", "Node.js", "fail", "not found on PATH", node_install_hint()))
    elif parsed is None:
        checks.append(Check("System", "Node.js", "fail", f"could not read the version ({node!r})", node_install_hint()))
    elif parsed < MIN_NODE:
        checks.append(Check("System", "Node.js", "fail", f"{node} is older than 22.20.0", node_install_hint()))
    else:
        checks.append(Check("System", "Node.js", "ok", node))
    npm = _version(["npm", "--version"])
    if npm is None:
        checks.append(Check("System", "npm", "fail", "not found on PATH", "npm ships with Node.js; reinstall Node.js 22 or put its bin directory on PATH."))
    else:
        checks.append(Check("System", "npm", "ok", npm or "found"))
    git = _version(["git", "--version"])
    if git is None:
        fix = "xcode-select --install" if system == "Darwin" else "Install git with your package manager (for example: sudo apt-get install git)."
        checks.append(Check("System", "Git", "warn", "not found; needed for Git-based skills and packages", fix))
    else:
        checks.append(Check("System", "Git", "ok", git.removeprefix("git version ")))
    return checks


def home_checks(paths: MantlePaths) -> list[Check]:
    checks: list[Check] = []
    probe_dir = paths.home if paths.home.exists() else paths.home.parent
    if os.access(probe_dir, os.W_OK):
        checks.append(Check("Mantle", "Mantle home", "ok", str(paths.home)))
    else:
        checks.append(Check("Mantle", "Mantle home", "fail", f"{paths.home} is not writable", "Fix its permissions or set MANTLE_HOME."))
    try:
        from mantle.runtimes import RuntimeManager
        manager = RuntimeManager(paths)
        default = manager.config().default_runtime
        installed = [runtime.version for runtime in manager.list()]
        if installed:
            detail = ", ".join(f"pi@{version}" + (" (default)" if version == default else "") for version in installed)
            checks.append(Check("Mantle", "Pi runtime", "ok", detail))
        else:
            checks.append(Check("Mantle", "Pi runtime", "info", "not downloaded yet; the first run downloads it",
                                "Download it now with: mantle runtime install"))
    except MantleError as error:
        checks.append(Check("Mantle", "Settings", "fail", str(error)))
    try:
        if paths.dotenv.exists():
            parse_dotenv(paths.dotenv)
            mode = paths.dotenv.stat().st_mode & 0o077
            if mode:
                checks.append(Check("Mantle", "Secrets file", "warn", f"{paths.dotenv} is readable by other users", f"chmod 600 {paths.dotenv}"))
    except MantleError as error:
        checks.append(Check("Mantle", "Secrets file", "fail", str(error)))
    return checks


def credential_checks(paths: MantlePaths) -> list[Check]:
    try:
        data = CredentialStore(paths).load()
    except MantleError as error:
        return [Check("Providers", "Shared logins", "fail", str(error), "Fix or remove the file, then sign in again with: mantle auth login")]
    if not data:
        return [Check("Providers", "Shared logins", "warn", "no providers signed in", "Sign in once for all agents: mantle auth login")]
    return [Check("Providers", provider, "ok", "signed in (shared by all agents)") for provider in sorted(data)]


def agent_checks(paths: MantlePaths) -> list[Check]:
    checks: list[Check] = []
    store = AgentStore(paths)
    if not paths.agents.is_dir():
        return [Check("Agents", "Agents", "info", "none yet", "Create one with: mantle agent create NAME")]
    dotenv = None
    for root in sorted(paths.agents.iterdir()):
        if not root.is_dir() or root.name.startswith("."):
            continue
        try:
            config = store.get(root.name)
        except MantleError as error:
            checks.append(Check("Agents", root.name, "fail", str(error).splitlines()[0], f"mantle agent edit {root.name}"))
            continue
        problems: list[tuple[str, str]] = []
        provider = lookup(config.model.provider)
        if dotenv is None:
            dotenv = parse_dotenv_safe(paths)
        # What `agent run` resolves declared secrets from; isolated agents get no shared API keys.
        environment = {**shared_api_key_environment(paths, root), **dotenv}
        auth_problem = auth_file_problem(paths, root)
        if auth_problem:
            problems.append((auth_problem, f"mantle auth share {root.name}"))
        elif provider and not provider_credential(paths, root, provider.id) and not any(
            variable in config.secrets and (os.environ.get(variable) or environment.get(variable)) for variable in provider.env
        ):
            fix = login_step(root, provider, config.secrets)[0]
            alternative = alternative_provider(paths, root, provider.id)
            if alternative:
                fix += f", or use your {alternative} login: mantle agent edit {root.name} --provider {alternative}"
            problems.append((f"no {provider.name} credentials", fix))
        missing = [name for name in config.secrets if not (os.environ.get(name) or environment.get(name))]
        if missing:
            from mantle.secrets import secret_fix
            problems.append((f"missing secret {', '.join(missing)}", secret_fix(missing[0], isolated=is_isolated(root))))
        for skill in config.resources.skills:
            if not (root / "skills" / skill.name / "SKILL.md").is_file():
                problems.append((f"skill {skill.name} is missing", f"mantle skills add {skill.source} --skill {skill.name} --agent {root.name} --force"))
        for package in config.resources.packages:
            try:
                installed = AgentLauncher._find_package_root(root, package) is not None
            except MantleError:
                installed = False
            if not installed:
                problems.append((f"package {package.name} is missing",
                                 f"mantle package add {package.source} --name {package.name} --agent {root.name} --force"))
        label = f"{config.model.provider}/{config.model.name}" + (", isolated logins" if is_isolated(root) else "")
        if problems:
            checks.append(Check("Agents", root.name, "warn", f"{label}: " + "; ".join(problem for problem, _ in problems), problems[0][1]))
        else:
            checks.append(Check("Agents", root.name, "ok", label))
    return checks or [Check("Agents", "Agents", "info", "none yet", "Create one with: mantle agent create NAME")]


def parse_dotenv_safe(paths: MantlePaths) -> dict[str, str]:
    try:
        return parse_dotenv(paths.dotenv)
    except MantleError:
        return {}


def cloud_checks(paths: MantlePaths) -> list[Check]:
    from mantle.cloud import _env_flag, probe
    from mantle.settings import load_settings
    try:
        settings = load_settings(paths)
        insecure = _env_flag("MANTLE_CLOUD_INSECURE", default=settings.cloud_insecure)
    except MantleError:
        return []  # Already reported by the settings check.
    url = os.environ.get("MANTLE_CLOUD_URL") or settings.cloud_url
    if not url:
        return [Check("Cloud", "Mantle Cloud", "info", "not configured (optional)", "Sync agents between machines: mantle cloud connect")]
    result = probe(paths, url, insecure=insecure, timeout=5)
    if result.ok:
        count = f", {result.agents} agent(s)" if result.agents is not None else ""
        return [Check("Cloud", "Mantle Cloud", "ok", f"{result.url}{count}")]
    from mantle.cloud_server import managed, saved
    local = saved(paths) if managed(paths) else None
    if local and local.url == result.url:
        return [Check("Cloud", "Mantle Cloud", "fail", result.summary, "Start the local server with: mantle cloud start")]
    return [Check("Cloud", "Mantle Cloud", "fail", result.summary, result.hint or "mantle cloud connect")]


def run_checks(paths: MantlePaths, *, cloud: bool = True) -> list[Check]:
    checks = prerequisite_checks() + home_checks(paths) + credential_checks(paths) + agent_checks(paths)
    if cloud:
        checks += cloud_checks(paths)
    return checks
