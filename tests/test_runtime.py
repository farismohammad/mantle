from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mantle.errors import MantleError
from mantle.paths import MantlePaths
from mantle.runtimes import RuntimeManager


class FakeNpm:
    def __init__(self, fail=False):
        self.commands = []
        self.fail = fail

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if command[1] == "view":
            return subprocess.CompletedProcess(command, 0, '"0.84.2"\n', "")
        if self.fail:
            return subprocess.CompletedProcess(command, 1, "", "network unavailable")
        prefix = Path(command[command.index("--prefix") + 1])
        executable = prefix / "node_modules/.bin/pi"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\n")
        return subprocess.CompletedProcess(command, 0, "installed", "")


def test_shared_runtime_is_installed_once_and_latest_reuses_default(mantle_home):
    npm = FakeNpm()
    manager = RuntimeManager(MantlePaths.discover(), runner=npm)
    first = manager.install()
    second = manager.ensure("latest")
    assert first.version == second.version == "0.84.2"
    assert len([command for command in npm.commands if command[1] == "install"]) == 1
    assert manager.executable("0.84.2").is_file()


def test_failed_runtime_install_is_atomic(mantle_home):
    manager = RuntimeManager(MantlePaths.discover(), runner=FakeNpm(fail=True))
    with pytest.raises(MantleError, match="network unavailable"):
        manager.install()
    assert manager.list() == []


def test_get_migrates_legacy_installed_at_metadata(mantle_home):
    path = mantle_home / "runtimes/pi/0.80.10/metadata.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "name": "pi",
        "version": "0.80.10",
        "package": "@earendil-works/pi-coding-agent",
        "installed_at": "2026-07-20T14:40:00Z",
    }))

    metadata = RuntimeManager(MantlePaths.discover()).get("0.80.10")

    assert metadata.version == "0.80.10"
    assert "installed_at" not in json.loads(path.read_text())
