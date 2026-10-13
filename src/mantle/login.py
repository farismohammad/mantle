"""Interactive provider sign-in that stores credentials once for every agent."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from mantle.credentials import CredentialStore
from mantle.environment import pi_login_environment
from mantle.paths import MantlePaths
from mantle.progress import note
from mantle.runtimes import PI_PACKAGE, RuntimeManager

LOGIN_HELPER = Path(__file__).with_name("pi_login.mjs")
# pi_login.mjs exits with these: the installed Pi lacks the login API it uses, or the user cancelled.
HELPER_INCOMPATIBLE = 3
HELPER_CANCELLED = 130


def oauth_login(paths: MantlePaths, provider_id: str) -> str:
    """Run Pi's own provider login against the shared store.

    Returns "saved", or why nothing new was saved: "cancelled", "failed",
    "unchanged" (Pi was closed without signing in again) or "missing".

    Mantle does not implement OAuth itself: Pi owns each provider's browser or
    device flow. Mantle drives that flow directly in the terminal, and Pi writes
    the resulting tokens straight into the shared credentials file.
    """
    store = CredentialStore(paths)
    store.ensure()
    before = store.get(provider_id)
    runtimes = RuntimeManager(paths)
    runtime = runtimes.ensure("latest")
    executable = runtimes.executable(runtime.version)
    auth_root = store.path.parent
    with tempfile.TemporaryDirectory(prefix="mantle-login-") as sessions:
        environment = pi_login_environment(auth_root, Path(sessions))
        status = _direct_login(paths.runtimes / runtime.version, provider_id, store.path, environment)
        if status is None:
            _login_inside_pi(executable, provider_id, sessions, environment)
    after = store.get(provider_id)
    if status == HELPER_CANCELLED:
        return "cancelled"
    if status not in (None, 0):
        return "failed"
    if after is None:
        return "missing"
    # The direct login reports success itself; inside Pi, only a changed credential shows one.
    return "saved" if status == 0 or after != before else "unchanged"


def _direct_login(runtime_root: Path, provider_id: str, auth_file: Path, environment: dict[str, str]) -> int | None:
    """Sign in without opening Pi and return the helper's exit status, or None when this Pi version cannot."""
    node = shutil.which("node", path=environment.get("PATH"))
    package = runtime_root / "node_modules" / PI_PACKAGE
    if node is None or not package.is_dir():
        return None
    note(f"\nSigning in to {provider_id}. The login is saved once and shared by all of your Mantle agents.")
    try:
        result = subprocess.run(
            [node, str(LOGIN_HELPER), str(package), provider_id, str(auth_file)],
            cwd=Path.home(), env=environment, check=False,
        )
    except KeyboardInterrupt:
        return HELPER_CANCELLED
    return None if result.returncode == HELPER_INCOMPATIBLE else result.returncode


def _login_inside_pi(executable: Path, provider_id: str, sessions: str, environment: dict[str, str]) -> None:
    """Fallback for Pi versions without a scriptable login: open Pi and let the user run /login."""
    command = [
        str(executable),
        "--no-context-files", "--no-approve", "--no-extensions", "--no-skills",
        "--no-prompt-templates", "--no-themes", "--session-dir", sessions,
    ]
    note(
        f"\nThis Pi version can only sign in from inside Pi. Pi will open; type:\n\n    /login {provider_id}\n\n"
        "Follow the browser or device-code prompts, then leave Pi with /quit (or Ctrl+C twice).\n"
        "The login is saved once and shared by all of your Mantle agents.\n"
    )
    try:
        subprocess.run(command, cwd=Path.home(), env=environment, check=False)
    except KeyboardInterrupt:
        pass
