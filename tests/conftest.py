from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mantle.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def mantle_home(tmp_path, monkeypatch):
    home = tmp_path / "mantle-home"
    monkeypatch.setenv("MANTLE_HOME", str(home))
    return home


@pytest.fixture
def cli(runner, mantle_home):
    def invoke(*arguments: str, input: str | None = None):
        return runner.invoke(app, list(arguments), input=input)
    return invoke

