from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from mantle.agents import AgentStore
from mantle.cloud import CloudClient
from mantle.errors import MantleError
from mantle.launcher import AgentLauncher
from mantle.models import ResourceDefinition
from mantle.packages import PackageManager
from mantle.paths import MantlePaths
from mantle.runtimes import RuntimeManager
from mantle.skills import SkillManager
from test_cli_workflows import external_tools, command, ok, failure


def test_yaml_save_failure_rolls_back_every_resource_operation(cli, external_tools, mantle_home, monkeypatch):
    assert cli('agent', 'create', 'demo').exit_code == 0
    assert cli('skills', 'add', 'source', '--skill', 'docs', '--agent', 'demo').exit_code == 0
    assert cli('package', 'add', 'npm:demo@1.0.0', '--agent', 'demo').exit_code == 0
    root = mantle_home / 'agents/demo'
    def snapshot():
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    before = snapshot()
    def fail_save(*args):
        raise PermissionError('Cannot save agent.yaml')
    monkeypatch.setattr(AgentStore, 'save', fail_save)
    for args in [
        ('skills', 'add', 'new-source', '--skill', 'docs', '--agent', 'demo', '--force'),
        ('skills', 'remove', 'docs', '--agent', 'demo'),
        ('package', 'add', 'npm:demo@2.0.0', '--agent', 'demo', '--force'),
        ('package', 'remove', 'demo', '--agent', 'demo'),
    ]:
        result = cli(*args)
        assert result.exit_code == 1
        assert 'Cannot save agent.yaml' in result.stderr
        assert snapshot() == before
        assert list(root.parent.iterdir()) == [root]


def test_runtime_config_failure_rolls_back(cli, external_tools, mantle_home, monkeypatch):
    def fail_save(*args):
        raise PermissionError('Cannot save config.json')
    monkeypatch.setattr(RuntimeManager, 'save_config', fail_save)
    result = cli('runtime', 'install', '1.0.0')
    assert result.exit_code == 1
    assert 'Cannot save config.json' in result.stderr
    assert not list((mantle_home / 'runtimes/pi').iterdir())


def test_delete_confirmation_and_damaged_config(command, mantle_home):
    ok(command('agent', 'create', 'demo'))
    root = mantle_home / 'agents/demo'
    result = command('agent', 'delete', 'demo', input='n\n')
    assert result.returncode == 1
    assert root.exists()
    (root / 'agent.yaml').unlink()
    failure(command('agent', 'run', 'demo'), 'Restore agent.yaml')
    ok(command('agent', 'delete', 'demo', input='y\n'))
    assert not root.exists()


def test_retained_session_conflict_preserves_agent(command, mantle_home):
    ok(command('agent', 'create', 'demo'))
    retained = mantle_home / 'retained-sessions/demo'
    retained.mkdir(parents=True)
    result = command('agent', 'delete', 'demo', '--yes', '--keep-sessions')
    failure(result, 'already exist')
    assert 'Move or rename that directory' in result.stderr
    assert (mantle_home / 'agents/demo/agent.yaml').is_file()


@pytest.mark.parametrize('body', [None, [], {}, {'data': {}}, {'data': {'yaml': 1, 'revision': {'number': 1}}}, {'data': {'yaml': '', 'revision': {'number': '1'}}}])
def test_invalid_cloud_success_is_an_actionable_error(cli, monkeypatch, body):
    client = CloudClient(MantlePaths.discover(), base_url='https://cloud.test', client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))))
    monkeypatch.setattr('mantle.cli.CloudClient', lambda _: client)
    result = cli('cloud', 'pull', 'demo')
    assert result.exit_code == 1
    assert 'invalid agent response' in result.stderr
    assert 'Traceback' not in result.stderr


@pytest.mark.parametrize('response', [httpx.Response(500, text='server unavailable'), httpx.Response(403, json={'error': {'message': 'access denied'}}), httpx.Response(200, text='<html>wrong host</html>')])
def test_cloud_server_errors(cli, monkeypatch, response):
    client = CloudClient(MantlePaths.discover(), base_url='https://cloud.test', client=httpx.Client(transport=httpx.MockTransport(lambda _: response)))
    monkeypatch.setattr('mantle.cli.CloudClient', lambda _: client)
    result = cli('cloud', 'pull', 'demo')
    assert result.exit_code == 1
    assert 'Mantle Cloud' in result.stderr
    assert 'Traceback' not in result.stderr


def test_missing_program_and_timeout_are_actionable(cli, monkeypatch):
    def missing(*args):
        raise FileNotFoundError(2, 'No such file or directory', 'npm')
    monkeypatch.setattr(RuntimeManager, 'latest_version', missing)
    result = cli('runtime', 'install')
    assert result.exit_code == 1
    assert 'Install Node.js/npm' in result.stderr
    def timeout(*args):
        raise subprocess.TimeoutExpired('npm', 60)
    monkeypatch.setattr(RuntimeManager, 'latest_version', timeout)
    result = cli('runtime', 'install')
    assert result.exit_code == 1
    assert 'timed out' in result.stderr


def test_resource_validation_precedes_install(cli, monkeypatch):
    assert cli('agent', 'create', 'demo').exit_code == 0
    def unexpected(*args, **kwargs):
        pytest.fail('install must not run for invalid input')
    monkeypatch.setattr(SkillManager, 'install_reference', unexpected)
    monkeypatch.setattr(PackageManager, 'install_reference', unexpected)
    monkeypatch.setattr(RuntimeManager, 'ensure', unexpected)
    for args in [
        ('skills', 'add', 'source', '--skill', '@scope/docs', '--agent', 'demo'),
        ('skills', 'add', ' ', '--skill', 'docs', '--agent', 'demo'),
        ('package', 'add', ' ', '--agent', 'demo', '--name', 'docs'),
        ('package', 'add', 'npm:demo', '--agent', 'demo', '--name', 'bad name'),
    ]:
        result = cli(*args)
        assert result.exit_code == 1
        assert 'schema' in result.stderr


@pytest.mark.parametrize('manifest', [[], {'pi': []}, {'pi': {'skills': 4}}, {'pi': {'skills': [4]}}])
def test_invalid_package_manifests_are_clean_errors(tmp_path, manifest):
    (tmp_path / 'package.json').write_text(json.dumps(manifest))
    with pytest.raises(MantleError, match='Invalid'):
        AgentLauncher._package_resources(tmp_path, [('skills', '--skill')])


def test_local_resources_and_permissions_are_explicit(cli, external_tools, tmp_path, monkeypatch):
    assert cli('agent', 'create', 'demo').exit_code == 0
    paths = MantlePaths.discover()
    config = AgentStore(paths).get('demo')
    config.permissions.filesystem = 'none'
    config.permissions.shell = 'none'
    AgentStore(paths).save(config)
    root = paths.agent('demo')
    (root / 'context/local.md').write_text('local instructions')
    (tmp_path / 'AGENTS.md').write_text('project instructions')
    monkeypatch.setenv('PI_PACKAGE_DIR', '/ambient/runtime')
    spec = AgentLauncher(paths).build('demo', current=tmp_path)
    assert '--no-approve' in spec.command
    assert 'local instructions' in spec.command
    assert 'project instructions' in spec.command
    assert 'PI_PACKAGE_DIR' not in spec.environment
    denied = spec.command[spec.command.index('--exclude-tools') + 1].split(',')
    assert set(denied) == {'read', 'write', 'edit', 'grep', 'find', 'ls', 'bash', 'powershell'}
    second = AgentLauncher(paths).build('demo', current=tmp_path, project_context=False)
    assert 'project instructions' not in second.command
    assert 'local instructions' in second.command


def test_unrelated_project_root_and_foreign_session_rejected(command, tmp_path):
    ok(command('agent', 'create', 'demo'))
    unrelated = tmp_path / 'unrelated'
    unrelated.mkdir()
    failure(command('agent', 'run', 'demo', '--project-root', str(unrelated)), 'not inside')
    failure(command('agent', 'run', 'demo', '--session', str(unrelated / 'session.jsonl')), 'Session file must exist inside')


def test_version_and_module_entrypoint(command, tmp_path):
    assert ok(command('--version')).startswith('mantle ')
    result = subprocess.run([__import__('sys').executable, '-m', 'mantle', '--version'], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert ok(result).startswith('mantle ')


def test_run_typo_is_not_forwarded(command):
    failure(command('agent', 'run', 'demo', '--dryrun'), 'No such option', code=2)


def test_latest_runtime_alias_and_duplicate_package_identity(command, mantle_home):
    ok(command('runtime', 'install', 'pi@latest'))
    assert (mantle_home / 'runtimes/pi/1.0.0/metadata.json').exists()
    assert not (mantle_home / 'runtimes/pi/latest').exists()
    ok(command('agent', 'create', 'demo'))
    ok(command('package', 'add', 'npm:demo@1.0.0', '--agent', 'demo'))
    before = (mantle_home / 'agents/demo/agent.yaml').read_bytes()
    failure(command('package', 'add', 'npm:demo@2.0.0', '--agent', 'demo', '--name', 'alias'), 'package sources must be unique')
    assert (mantle_home / 'agents/demo/agent.yaml').read_bytes() == before


def test_local_package_reference_rejected_before_install(command, mantle_home, tmp_path):
    ok(command('agent', 'create', 'demo'))
    failure(command('package', 'add', str(tmp_path), '--agent', 'demo', '--name', 'local'), 'Local package references are not supported')
    assert not (mantle_home / 'runtimes').exists()


def test_git_package_names_preserve_repository_identity():
    from mantle.packages import package_name_from_source
    assert package_name_from_source('git:https://example.test/org/demo.git@v1') == 'demo'
    assert package_name_from_source('git@example.test:org/demo.git@v1') == 'demo'
    assert package_name_from_source('ssh://git@example.test/org/demo.git@v1') == 'demo'


def test_runtime_remove_save_failure_restores_runtime(cli, external_tools, mantle_home, monkeypatch):
    assert cli('runtime', 'install', '1.0.0').exit_code == 0
    before = (mantle_home / 'config.json').read_bytes()
    def fail_save(*args):
        raise PermissionError('Cannot save runtime config')
    monkeypatch.setattr(RuntimeManager, 'save_config', fail_save)
    result = cli('runtime', 'remove', '1.0.0')
    assert result.exit_code == 1
    assert (mantle_home / 'config.json').read_bytes() == before
    assert (mantle_home / 'runtimes/pi/1.0.0/node_modules/.bin/pi').is_file()


@pytest.mark.parametrize('source', ['https://example.test/owner/demo.git@v1', 'git@example.test:owner/demo.git@v1', 'ssh://git@example.test/owner/demo.git@v1', 'git:example.test/owner/demo@v1'])
def test_git_package_lookup_uses_source_instead_of_alias(tmp_path, source):
    root = tmp_path / 'agent'
    package = root / 'git/example.test/owner/demo'
    package.mkdir(parents=True)
    # Git packages can rely on conventional resource directories without package.json.
    (package / 'skills').mkdir()
    reference = ResourceDefinition(name='my-alias', source=source)
    assert AgentLauncher._find_package_root(root, reference) == package
    assert AgentLauncher._package_resources(package, [('skills', '--skill')]) == ['--skill', str(package / 'skills')]


def test_explicit_package_manifest_does_not_enable_undeclared_resources(tmp_path):
    (tmp_path / 'skills').mkdir()
    (tmp_path / 'package.json').write_text('{"pi":{"extensions":[]}}')
    assert AgentLauncher._package_resources(tmp_path, [('skills', '--skill')]) == []


def test_successful_resource_replacement_and_missing_package(command, mantle_home):
    ok(command('agent', 'create', 'demo'))
    ok(command('skills', 'add', 'first', '--skill', 'docs', '--agent', 'demo'))
    ok(command('skills', 'add', 'second', '--skill', 'docs', '--agent', 'demo', '--force'))
    ok(command('package', 'add', 'npm:first', '--agent', 'demo', '--name', 'alias'))
    ok(command('package', 'add', 'npm:second', '--agent', 'demo', '--name', 'alias', '--force'))
    root = mantle_home / 'agents/demo'
    assert not (root / 'npm/node_modules/first').exists()
    assert (root / 'npm/node_modules/second').is_dir()
    config = AgentStore(MantlePaths.discover()).get('demo')
    assert config.resources.skills[0].source == 'second'
    assert config.resources.packages[0].source == 'npm:second'
    __import__('shutil').rmtree(root / 'npm/node_modules/second')
    failure(command('agent', 'run', 'demo'), 'Reinstall it with: mantle package add npm:second --name alias --agent demo')


def test_unused_dotenv_and_log_error_do_not_change_runtime_exit(command, mantle_home, monkeypatch):
    ok(command('agent', 'create', 'demo'))
    (mantle_home / '.env').write_text('unrelated malformed entry')
    logs = mantle_home / 'agents/demo/logs'
    logs.rmdir()
    logs.write_text('not a directory')
    (Path.home() / '.mantle-test-pi-exit').write_text('7')
    result = command('agent', 'run', 'demo')
    assert result.returncode == 7
    assert 'Warning: could not write the local run log' in result.stderr
    assert 'Traceback' not in result.stderr


def test_relative_session_keeps_its_target_with_explicit_project_root(cli, external_tools, tmp_path):
    from mantle.projects import project_id
    import os
    assert cli('agent', 'create', 'demo').exit_code == 0
    project = tmp_path / 'project'
    current = project / 'subdirectory'
    current.mkdir(parents=True)
    sessions = MantlePaths.discover().agent('demo') / 'sessions' / project_id(project)
    sessions.mkdir()
    saved = sessions / 'existing.jsonl'
    saved.write_text('{}')
    relative = os.path.relpath(saved, current)
    spec = AgentLauncher(MantlePaths.discover()).build('demo', current=current, project_root=project, session=relative)
    assert spec.command[spec.command.index('--session') + 1] == str(saved)
    assert spec.cwd == project
