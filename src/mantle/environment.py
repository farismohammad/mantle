from __future__ import annotations

import os
from pathlib import Path


_LAUNCH_ENVIRONMENT = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TMP", "TEMP",
    "TERM", "COLORTERM", "LANG", "TZ",
}
_PROXY_ENVIRONMENT = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}


def pi_launch_environment(
    agent_root: Path,
    session_dir: Path,
    secrets: dict[str, str],
) -> dict[str, str]:
    """Build Pi's environment without inheriting unrelated credentials or Pi overrides."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in _LAUNCH_ENVIRONMENT
        or key.upper() in _PROXY_ENVIRONMENT
        or key.startswith(("LC_", "XDG_"))
    }
    environment.update(secrets)
    environment.update({
        "PI_CODING_AGENT_DIR": str(agent_root),
        "PI_CODING_AGENT_SESSION_DIR": str(session_dir),
        "PI_TELEMETRY": "0",
        "PI_SKIP_VERSION_CHECK": "1",
    })
    return environment


def pi_operation_environment(agent_root: Path) -> dict[str, str]:
    """Keep installer credentials available while ignoring ambient Pi path overrides."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PI_")}
    environment.update({
        "PI_CODING_AGENT_DIR": str(agent_root),
        "PI_TELEMETRY": "0",
        "PI_SKIP_VERSION_CHECK": "1",
    })
    return environment


def without_pi_overrides(environment: dict[str, str]) -> dict[str, str]:
    """Remove host Pi configuration overrides from a third-party resource installer."""
    return {key: value for key, value in environment.items() if not key.startswith("PI_")}
