"""Bound installer lifetimes so rollback starts only after writers have stopped."""
from __future__ import annotations

import os
import signal
import subprocess


def run_installer(command, *, timeout=300, check=False, capture_output=True, text=True, **kwargs):
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=text,
        start_new_session=True,
        **kwargs,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException:
            # npm/npx/Pi spawn children. Stopping only the parent lets those
            # children continue modifying resource state after it is restored.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
