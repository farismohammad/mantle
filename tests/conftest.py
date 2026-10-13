from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mantle.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def mantle_home(tmp_path, monkeypatch):
    home = tmp_path / "mantle-home"
    monkeypatch.setenv("MANTLE_HOME", str(home))
    user_home = tmp_path / "user-home"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.delenv("MANTLE_CLOUD_URL", raising=False)
    monkeypatch.delenv("MANTLE_CLOUD_INSECURE", raising=False)
    return home


@pytest.fixture
def cli(runner, mantle_home):
    def invoke(*arguments: str, input: str | None = None):
        return runner.invoke(app, list(arguments), input=input)
    return invoke
