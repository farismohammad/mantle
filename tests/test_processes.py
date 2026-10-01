import subprocess
import sys
import time

import pytest

from mantle.processes import run_installer


def test_installer_timeout_stops_child_writers(tmp_path):
    marker = tmp_path / 'late-write'
    child = f'import time; from pathlib import Path; time.sleep(0.5); Path({str(marker)!r}).touch()'
    parent = f'import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", {child!r}]); time.sleep(10)'
    with pytest.raises(subprocess.TimeoutExpired):
        run_installer([sys.executable, '-c', parent], timeout=0.2)
    time.sleep(0.5)
    assert not marker.exists()


def test_installer_preserves_output_and_exit_code():
    result = run_installer([sys.executable, '-c', 'import sys; print("output"); print("error", file=sys.stderr); sys.exit(3)'])
    assert result.returncode == 3
    assert result.stdout == 'output\n'
    assert result.stderr == 'error\n'
