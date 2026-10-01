from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPTS = [PACKAGE / 'install.sh']
if (PACKAGE.parent / 'install.sh').is_file():
    SCRIPTS.append(PACKAGE.parent / 'install.sh')


@pytest.mark.parametrize('script', SCRIPTS)
def test_installer_local_checkout_and_argument_errors(script, tmp_path, monkeypatch):
    tools = tmp_path / 'bin'
    tools.mkdir()
    installed = tmp_path / 'installed bin'
    installed.mkdir()
    log = tmp_path / 'uv-arguments.json'
    source = tmp_path / 'checkout with spaces'
    source.mkdir()
    (source / 'pyproject.toml').write_text('[project]\nname="mantle-agent-cli"\n')
    for name, body in {
        'node': 'import os, sys\nif sys.argv[1:] == ["--version"]: print(os.getenv("MANTLE_TEST_NODE_VERSION", "v22.22.0"))\n',
        'npm': '', 'git': '',
        'uv': f'import pathlib, json, sys\npathlib.Path({str(log)!r}).write_text(json.dumps(sys.argv[1:]))\n',
    }.items():
        executable = tools / name
        executable.write_text(f'#!{sys.executable}\n' + body)
        executable.chmod(0o755)
    mantle = installed / 'mantle'
    mantle.write_text('#!/bin/sh\nexit 0\n')
    mantle.chmod(0o755)
    monkeypatch.setenv('PATH', str(tools) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('UV_TOOL_BIN_DIR', str(installed))
    monkeypatch.delenv('MANTLE_SOURCE', raising=False)
    monkeypatch.delenv('MANTLE_REF', raising=False)
    def invoke(*args):
        return subprocess.run(['sh', str(script), *args], capture_output=True, text=True, cwd=tmp_path, timeout=10)
    assert invoke('--help').returncode == 0
    result = invoke('--source', str(source))
    assert result.returncode == 0, result.stderr
    assert 'installed successfully' in result.stdout
    assert f'export PATH="{installed}:$PATH"' in result.stdout
    assert json.loads(log.read_text()) == ['tool', 'install', '--force', '--reinstall-package', 'mantle-agent-cli', str(source)]
    monkeypatch.setenv('MANTLE_TEST_NODE_VERSION', 'v22.19.9')
    result = invoke('--source', str(source))
    assert result.returncode == 1
    assert 'Node.js 22.20.0 or newer is required' in result.stderr
    monkeypatch.setenv('MANTLE_TEST_NODE_VERSION', 'v22.20.0')
    result = invoke('--source', str(source))
    assert result.returncode == 0, result.stderr
    for args, detail in [(('--source',), 'requires a value'), (('--unknown',), 'unknown argument'),
                         (('--source', str(source), '--ref', 'main'), 'only be used with a Git URL'),
                         (('--source', str(tmp_path / 'missing')), 'does not exist')]:
        result = invoke(*args)
        assert result.returncode == 1
        assert detail in result.stderr
