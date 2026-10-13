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


def _installer_tools(tmp_path, node_version='v22.22.0', git=True, system=None):
    tools = tmp_path / 'bin'
    tools.mkdir()
    installed = tmp_path / 'installed'
    installed.mkdir()
    log = tmp_path / 'uv.log'
    scripts = {
        'node': f'print({node_version!r})',
        'npm': 'print("10.9.4")',
        'uv': f'import sys, json\nopen({str(log)!r}, "a").write(json.dumps(sys.argv[1:]) + "\\n")\n'
              'print("mantle-agent-cli v0.1.0") if sys.argv[1:3] == ["tool", "list"] else None',
    }
    if git:
        scripts['git'] = 'print("git version 2.43.0")'
    for name, body in scripts.items():
        executable = tools / name
        executable.write_text(f'#!{sys.executable}\n{body}\n')
        executable.chmod(0o755)
    (installed / 'mantle').write_text('#!/bin/sh\necho "mantle 0.1.0"\n')
    (installed / 'mantle').chmod(0o755)
    home = tmp_path / 'home'
    home.mkdir()
    # Core utilities only, so the real git/node on this machine stay invisible.
    core = tmp_path / 'core'
    core.mkdir()
    import shutil as _shutil
    for name in ('sh', 'uname', 'awk', 'sed', 'grep', 'mktemp', 'rm', 'cat', 'dirname', 'basename', 'mkdir', 'env', 'head'):
        found = _shutil.which(name)
        if found:
            (core / name).symlink_to(found)
    if system is not None:
        # Report another platform so platform-specific profile choices run anywhere.
        (core / 'uname').unlink()
        (core / 'uname').write_text(f'#!/bin/sh\ncase "$1" in -s) echo {system} ;; *) echo x86_64 ;; esac\n')
        (core / 'uname').chmod(0o755)
    environment = {'HOME': str(home), 'PATH': f'{tools}:{core}', 'UV_TOOL_BIN_DIR': str(installed), 'SHELL': '/bin/bash'}
    return environment, log, home, installed


def _run_installer(environment, *args, cwd=None, stdin=None):
    return subprocess.run(['sh', *args], capture_output=True, text=True, cwd=cwd, env=environment, timeout=20, stdin=stdin)


def test_installer_reports_every_missing_prerequisite_at_once(tmp_path):
    environment, *_ = _installer_tools(tmp_path, node_version='v18.19.0', git=False)
    result = _run_installer(environment, str(SCRIPTS[0]), '--source', str(PACKAGE))
    assert result.returncode == 1
    assert '✖ Git' in result.stdout and '✖ Node.js' in result.stdout
    assert 'found 18.19.0' in result.stdout
    assert '--with-node' in result.stderr


def test_installer_rejects_foreign_projects_and_never_guesses_from_cwd(tmp_path):
    environment, log, *_ = _installer_tools(tmp_path)
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / 'pyproject.toml').write_text('[project]\nname = "some-other-app"\n')
    result = _run_installer(environment, str(SCRIPTS[0]), '--source', str(foreign))
    assert result.returncode == 1 and 'is not the Mantle CLI' in result.stderr
    with open(SCRIPTS[0]) as script:
        piped = _run_installer(environment, cwd=foreign, stdin=script)
    assert piped.returncode == 1
    assert 'some-other-app' not in (log.read_text() if log.exists() else '')
    assert 'tool' not in (log.read_text() if log.exists() else '')


def test_installer_accepts_prerelease_node_versions(tmp_path):
    environment, *_ = _installer_tools(tmp_path, node_version='v24.0.0-rc.1')
    result = _run_installer(environment, str(SCRIPTS[0]), '--source', str(PACKAGE), '--no-setup')
    assert result.returncode == 0, result.stderr
    assert 'mantle 0.1.0' in result.stdout


@pytest.mark.parametrize('system,profile_name', [('Linux', '.bashrc'), ('Darwin', '.bash_profile')])
def test_installer_path_block_is_idempotent_and_uninstall_removes_it(tmp_path, system, profile_name):
    # macOS terminals start bash as a login shell, which reads ~/.bash_profile.
    environment, log, home, installed = _installer_tools(tmp_path, system=system)
    environment['MANTLE_INTERACTIVE'] = '1'
    for _ in range(2):
        result = _run_installer(environment, str(SCRIPTS[0]), '--source', str(PACKAGE), '--yes', '--no-setup')
        assert result.returncode == 0, result.stderr
    profile = (home / profile_name).read_text()
    assert profile.count('# >>> mantle >>>') == 1
    assert f'export PATH="{installed}:$PATH"' in profile
    result = _run_installer(environment, str(SCRIPTS[0]), '--uninstall', '--yes')
    assert result.returncode == 0, result.stderr
    assert 'mantle' not in (home / profile_name).read_text()
    assert json.loads(log.read_text().splitlines()[-1]) == ['tool', 'uninstall', 'mantle-agent-cli']


def test_installer_explains_unexpected_failures(tmp_path):
    environment, *_ = _installer_tools(tmp_path)
    broken = tmp_path / 'installed' / 'mantle'
    broken.write_text('#!/bin/sh\necho boom >&2\nexit 3\n')
    result = _run_installer(environment, str(SCRIPTS[0]), '--source', str(PACKAGE), '--no-setup')
    assert result.returncode == 1
    assert 'boom' in result.stderr and 'does not start' in result.stderr


@pytest.mark.parametrize('system,profile_name,flag', [('Linux', '.bashrc', '-i'), ('Darwin', '.bash_profile', '-l')])
def test_installer_opens_a_shell_that_has_mantle_on_path(tmp_path, system, profile_name, flag):
    # A child process cannot change its parent shell's PATH, so the installer
    # replaces itself with a new shell that reads the updated profile.
    import pty
    environment, _log, home, installed = _installer_tools(tmp_path, system=system)
    (tmp_path / 'shells').mkdir()
    shell = tmp_path / 'shells' / 'bash'
    record = tmp_path / 'shell.log'
    shell.write_text(f'#!/bin/sh\necho "$@" >{record}\n')
    shell.chmod(0o755)
    environment |= {'MANTLE_INTERACTIVE': '1', 'SHELL': str(shell)}
    controller, terminal = pty.openpty()
    os.write(controller, b'y\n')
    try:
        result = _run_installer(environment, str(SCRIPTS[0]), '--source', str(PACKAGE), '--no-setup', stdin=terminal)
    finally:
        os.close(terminal)
        os.close(controller)
    assert result.returncode == 0, result.stderr
    assert f'export PATH="{installed}:$PATH"' in (home / profile_name).read_text()
    assert 'Starting a new bash' in result.stdout
    assert record.read_text().split() == [flag]


def test_installer_without_a_terminal_explains_how_to_load_path(tmp_path):
    environment, _log, home, _installed = _installer_tools(tmp_path, system='Linux')
    environment['MANTLE_INTERACTIVE'] = '1'
    result = _run_installer(environment, str(SCRIPTS[0]), '--source', str(PACKAGE), '--yes', '--no-setup')
    assert result.returncode == 0, result.stderr
    assert f'run: . "{home}/.bashrc"' in result.stdout
