"""CLI workflows with real subprocess boundaries and disposable external tools.

npm/npx/Pi are controlled executables, so these tests work offline and never
install software globally. Cloud tests exercise the real client over MockTransport.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest
import yaml

from mantle.cloud import CloudClient
from mantle.configuration import dump_agent_yaml
from mantle.models import default_agent_config


PI = r'''import json, os, pathlib, sys
root = pathlib.Path(os.environ['PI_CODING_AGENT_DIR'])
args = sys.argv[1:]
if args and args[0] in ('install', 'remove'):
    source = args[1]
    name = source.removeprefix('npm:').rsplit('@', 1)[0] if '@' in source.removeprefix('npm:')[1:] else source.removeprefix('npm:')
    package = root / 'npm/node_modules' / name
    if args[0] == 'install':
        package.mkdir(parents=True, exist_ok=True)
        (package / 'package.json').write_text(json.dumps({'name': name, 'pi': {'extensions': ['index.js']}}))
        (package / 'index.js').write_text('export default function () {}')
    elif package.exists():
        import shutil
        shutil.rmtree(package)
    (root / 'settings.json').write_text(json.dumps({'packages': [source] if args[0] == 'install' else []}))
    if os.environ.get('FAIL_PI'):
        print('fixture package failure', file=sys.stderr)
        sys.exit(1)
else:
    print(json.dumps({'args': args, 'cwd': os.getcwd(), 'agent': str(root)}))
    exit_file = pathlib.Path.home() / '.mantle-test-pi-exit'
    sys.exit(int(exit_file.read_text()) if exit_file.is_file() else 0)
'''


@pytest.fixture
def external_tools(tmp_path, monkeypatch):
    tools = tmp_path / 'bin'
    tools.mkdir()
    scripts = {
        'npm': """import os, pathlib, sys
args = sys.argv[1:]
if os.environ.get('FAIL_NPM'):
    print('fixture npm failure', file=sys.stderr)
    sys.exit(1)
if args[0] == 'view':
    print('\"1.0.0\"')
else:
    root = pathlib.Path(args[args.index('--prefix') + 1])
    binary = root / 'node_modules/.bin/pi'
    binary.parent.mkdir(parents=True)
    binary.write_text(%r)
    binary.chmod(0o755)
""" % (f'#!{sys.executable}\n' + PI),
        'npx': """import os, pathlib, sys
args = sys.argv[1:]
name = args[args.index('--skill') + 1]
root = pathlib.Path.cwd() / '.pi/skills' / name
root.mkdir(parents=True)
(root / 'SKILL.md').write_text('---\\nname: ' + name + '\\n---\\nSkill content')
if os.environ.get('FAIL_NPX'):
    print('fixture skill failure', file=sys.stderr)
    sys.exit(1)
""",
    }
    for name, source in scripts.items():
        file = tools / name
        file.write_text(f'#!{sys.executable}\n' + source)
        file.chmod(0o755)
    monkeypatch.setenv('PATH', str(tools) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('NO_COLOR', '1')
    monkeypatch.setenv('TERM', 'dumb')
    return tools


@pytest.fixture
def command(external_tools, tmp_path):
    project = tmp_path / 'project with spaces'
    project.mkdir()
    entrypoint = Path(os.environ.get('MANTLE_TEST_ENTRYPOINT', Path(sys.executable).parent / 'mantle'))
    def invoke(*args, input=None, cwd=None):
        return subprocess.run([str(entrypoint), *args], cwd=cwd or project,
                              capture_output=True, text=True, input=input, timeout=20)
    return invoke


def ok(result):
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Traceback' not in result.stderr
    return result.stdout


def failure(result, text, code=1):
    assert result.returncode == code, result.stdout + result.stderr
    assert text in result.stderr, result.stderr
    assert 'Traceback' not in result.stderr
    assert not result.stdout


COMMANDS = [('create',), ('list',), ('delete',), ('push',), ('install',), ('update',),
            ('run',), ('skills',), ('skills', 'add'), ('remove-skill',),
            ('add-package',), ('remove-package',), ('validate',), ('runtime',),
            ('runtime', 'install'), ('runtime', 'list'), ('runtime', 'remove')]


@pytest.mark.parametrize('args', [(), *COMMANDS])
def test_help_every_command(command, args):
    output = ok(command(*args, '--help'))
    assert 'Usage:' in output
    assert '--help' in output


def test_primary_local_workflow(command, mantle_home, tmp_path):
    assert json.loads(ok(command('list', '--json'))) == []
    ok(command('runtime', 'install'))
    ok(command('runtime', 'list'))
    ok(command('create', 'writer', '--description', 'Writes docs'))
    root = mantle_home / 'agents/writer'
    ok(command('validate', str(root / 'agent.yaml')))
    ok(command('skills', 'add', 'https://example.test/skills', '--skill', 'docs', '--agent', 'writer'))
    ok(command('add-package', 'npm:demo@1.0.0', 'writer', '--name', 'alias'))
    data = yaml.safe_load((root / 'agent.yaml').read_text())
    assert data['resources']['skills'][0]['name'] == 'docs'
    assert data['resources']['packages'][0]['name'] == 'alias'
    preview = json.loads(ok(command('run', 'writer', '--dry-run')))
    assert preview['environment']['PI_CODING_AGENT_DIR'] == str(root)
    assert '--no-context-files' in preview['command']
    assert str(root / 'npm/node_modules/demo/index.js') in preview['command']
    launched = json.loads(ok(command('run', 'writer', '--', '--print', 'hello')))
    assert launched['agent'] == str(root)
    assert launched['args'][-2:] == ['--print', 'hello']
    assert Path(launched['cwd']).name == 'project with spaces'
    assert (root / 'logs/mantle.jsonl').is_file()
    assert len(json.loads(ok(command('list', '--json')))) == 1
    ok(command('list'))
    ok(command('remove-skill', 'docs', 'writer'))
    ok(command('remove-package', 'alias', 'writer'))
    assert yaml.safe_load((root / 'agent.yaml').read_text())['resources'] == {'skills': [], 'packages': []}
    (root / 'sessions/saved.jsonl').write_text('{}')
    ok(command('delete', 'writer', '--yes', '--keep-sessions'))
    assert not root.exists()
    assert (mantle_home / 'retained-sessions/writer/saved.jsonl').read_text() == '{}'
    ok(command('runtime', 'remove', 'pi@1.0.0'))
    assert json.loads((mantle_home / 'config.json').read_text())['default_runtime'] is None
    assert not list((mantle_home / 'runtimes/pi').iterdir())


@pytest.mark.parametrize('args,text,code', [
    (('create', 'UPPER'), 'Agent names', 1),
    (('run', 'UPPER'), 'Agent names', 1),
    (('install', 'UPPER'), 'Agent names', 1),
    (('create', 'demo', '--runtime-version', ' '), 'runtime', 1),
    (('runtime', 'remove', 'bad version'), 'Runtime version', 1),
    (('runtime', 'install', 'bad version'), 'Runtime version', 1),
    (('run', 'missing'), 'not installed locally', 1),
    (('push', 'missing'), 'not installed locally', 1),
    (('update', 'missing'), 'not installed locally', 1),
    (('delete', 'missing', '--yes'), 'not installed locally', 1),
    (('validate', 'missing.yaml'), 'not found', 1),
    (('create',), 'Missing argument', 2),
    (('create', 'demo', '--bogus'), 'No such option', 2),
    (('skills', 'add', 'source', '--skill', 'docs'), '--agent', 2),
    (('run', 'demo', '--session', 'id', '--new-session'), 'cannot be used together', 1),
])
def test_expected_errors(command, args, text, code):
    failure(command(*args), text, code)


def test_duplicates_and_failed_resource_changes(command, mantle_home, monkeypatch):
    ok(command('create', 'demo'))
    root = mantle_home / 'agents/demo'
    failure(command('create', 'demo'), 'already exists')
    ok(command('skills', 'add', 'source', '--skill', 'docs', '--agent', 'demo'))
    ok(command('add-package', 'npm:demo@1.0.0', 'demo'))
    before = (root / 'agent.yaml').read_bytes()
    settings = (root / 'settings.json').read_bytes()
    failure(command('skills', 'add', 'source', '--skill', 'docs', '--agent', 'demo'), 'already installed')
    failure(command('add-package', 'npm:demo@2.0.0', 'demo'), 'already installed')
    monkeypatch.setenv('FAIL_NPX', '1')
    failure(command('skills', 'add', 'new-source', '--skill', 'docs', '--agent', 'demo', '--force'), 'fixture skill failure')
    monkeypatch.setenv('FAIL_PI', '1')
    failure(command('add-package', 'npm:demo@2.0.0', 'demo', '--force'), 'fixture package failure')
    failure(command('remove-package', 'demo', 'demo'), 'fixture package failure')
    assert (root / 'agent.yaml').read_bytes() == before
    assert (root / 'settings.json').read_bytes() == settings
    assert (root / 'skills/docs/SKILL.md').is_file()
    assert (root / 'npm/node_modules/demo/index.js').is_file()
    assert not list((root / 'tmp').iterdir())
    assert not list(root.parent.glob('.resources-*'))
    failure(command('remove-skill', 'missing', 'demo'), 'not installed')
    failure(command('remove-package', 'missing', 'demo'), 'not installed')
    failure(command('runtime', 'remove', '1.0.0'), 'used by: demo')


@pytest.mark.parametrize('content,detail', [(b'agent: [', 'Invalid YAML'), (b'\xff', 'Could not read'),
    (b'[]', 'mapping'), (b'agent: {name: demo}\nagent: {name: other}', 'duplicate key'),
    (b'agent: {name: other}', 'was requested'),
    (b'agent: {name: demo}\nresources: {skills: [{name: docs, source: repo}, {name: docs, source: repo}]}', 'unique')])
def test_malformed_agent_is_safe(command, mantle_home, content, detail):
    ok(command('create', 'demo'))
    config = mantle_home / 'agents/demo/agent.yaml'
    config.write_bytes(content)
    for args in [('run', 'demo'), ('push', 'demo'), ('list', '--json'), ('remove-skill', 'docs', 'demo')]:
        failure(command(*args), detail)
        assert config.read_bytes() == content


def test_runtime_failure_and_bad_config(command, mantle_home, monkeypatch):
    monkeypatch.setenv('FAIL_NPM', '1')
    failure(command('runtime', 'install', '1.0.0'), 'fixture npm failure')
    assert not list((mantle_home / 'runtimes/pi').iterdir())
    assert not (mantle_home / 'config.json').exists()
    (mantle_home / 'config.json').write_text('{')
    failure(command('runtime', 'list'), 'config.json')
    failure(command('install', 'demo'), 'config.json')


def test_launch_edge_cases(command, mantle_home, tmp_path, monkeypatch):
    ok(command('create', 'demo'))
    failure(command('run', 'demo', '--project-root', str(tmp_path / 'absent')), 'not a directory')
    failure(command('run', 'demo', '--', '--skill', 'external'), 'isolation')
    config_path = mantle_home / 'agents/demo/agent.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['secrets'] = ['MANTLE_TEST_SECRET']
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.delenv('MANTLE_TEST_SECRET', raising=False)
    failure(command('run', 'demo'), 'Missing required local secret')
    monkeypatch.setenv('MANTLE_TEST_SECRET', 'secret-value')
    (Path.home() / '.mantle-test-pi-exit').write_text('7')
    result = command('run', 'demo')
    assert result.returncode == 7
    assert 'secret-value' not in result.stdout + result.stderr
    ok(command('skills', 'add', 'source', '--skill', 'docs', '--agent', 'demo'))
    (mantle_home / 'agents/demo/skills/docs/SKILL.md').unlink()
    failure(command('run', 'demo'), 'Reinstall it with: mantle skills add')


@pytest.fixture
def cloud(cli, monkeypatch):
    state = {}
    requests = []
    def handler(request):
        requests.append((request.method, request.url.path))
        name = request.url.path.rsplit('/', 1)[-1]
        if request.method == 'PUT':
            content = request.content.decode()
            old = state.get(name)
            created = old is None or old['yaml'] != content
            number = (old['revision']['number'] if old else 0) + int(created)
            state[name] = {'yaml': content, 'revision': {'number': number}}
            return httpx.Response(200, json={'data': {**state[name], 'created': created}})
        if name not in state:
            return httpx.Response(404)
        return httpx.Response(200, json={'data': state[name]})
    monkeypatch.setattr('mantle.cli.CloudClient', lambda paths: CloudClient(paths, base_url='http://cloud.test', client=httpx.Client(transport=httpx.MockTransport(handler))))
    return state, requests


def test_cloud_primary_workflow(cli, cloud, external_tools, mantle_home):
    state, requests = cloud
    def invoke(*args):
        result = cli(*args)
        assert result.exit_code == 0, result.output
        return result.output
    invoke('create', 'demo')
    invoke('skills', 'add', 'source', '--skill', 'docs', '--agent', 'demo')
    invoke('add-package', 'npm:demo', 'demo')
    assert not requests
    yaml_path = mantle_home / 'agents/demo/agent.yaml'
    exact_yaml = yaml_path.read_text()
    assert 'revision 1' in invoke('push', 'demo')
    assert 'already has' in invoke('push', 'demo')
    assert state['demo']['yaml'] == exact_yaml
    invoke('delete', 'demo', '--yes')
    missing = cli('run', 'demo')
    assert missing.exit_code == 1
    assert 'mantle install demo' in missing.stderr
    invoke('install', 'demo')
    assert yaml_path.read_text() == exact_yaml
    (yaml_path.parent / 'sessions/keep.jsonl').write_text('session')
    (yaml_path.parent / 'auth.json').write_text('{"key":"local"}')
    updated = yaml.safe_load(exact_yaml)
    updated['agent']['description'] = 'Updated remotely'
    updated['resources'] = {'skills': [], 'packages': []}
    state['demo'] = {'yaml': yaml.safe_dump(updated), 'revision': {'number': 2}}
    assert 'revision 2' in invoke('update', 'demo')
    assert not (yaml_path.parent / 'skills/docs').exists()
    assert (yaml_path.parent / 'sessions/keep.jsonl').read_text() == 'session'
    assert (yaml_path.parent / 'auth.json').read_text() == '{"key":"local"}'
    assert [method for method, _ in requests].count('PUT') == 2
    assert 'DELETE' not in [method for method, _ in requests]


def test_failed_cloud_install_and_update(cli, cloud, external_tools, mantle_home, monkeypatch):
    state, _ = cloud
    config = default_agent_config('demo')
    state['demo'] = {'yaml': dump_agent_yaml(config), 'revision': {'number': 1}}
    monkeypatch.setenv('FAIL_NPM', '1')
    assert cli('install', 'demo').exit_code == 1
    assert not list((mantle_home / 'agents').iterdir())
    monkeypatch.delenv('FAIL_NPM')
    assert cli('install', 'demo').exit_code == 0
    root = mantle_home / 'agents/demo'
    before = (root / 'agent.yaml').read_text()
    changed = yaml.safe_load(before)
    changed['resources']['skills'] = [{'name': 'docs', 'source': 'source'}]
    state['demo']['yaml'] = yaml.safe_dump(changed)
    monkeypatch.setenv('FAIL_NPX', '1')
    result = cli('update', 'demo')
    assert result.exit_code == 1
    assert 'fixture skill failure' in result.stderr
    assert (root / 'agent.yaml').read_text() == before
    assert list(root.parent.iterdir()) == [root]


def test_failed_update_restores_shared_runtime_default(cli, cloud, external_tools, mantle_home, monkeypatch):
    state, _ = cloud
    assert cli('runtime', 'install', '9.0.0').exit_code == 0
    assert cli('create', 'demo').exit_code == 0
    old_settings = (mantle_home / 'config.json').read_bytes()
    desired = yaml.safe_load((mantle_home / 'agents/demo/agent.yaml').read_text())
    desired['resources']['skills'] = [{'name': 'docs', 'source': 'source'}]
    state['demo'] = {'yaml': yaml.safe_dump(desired), 'revision': {'number': 1}}
    monkeypatch.setenv('FAIL_NPX', '1')
    result = cli('update', 'demo')
    assert result.exit_code == 1
    assert 'fixture skill failure' in result.stderr
    assert (mantle_home / 'config.json').read_bytes() == old_settings
    assert (mantle_home / 'runtimes/pi/1.0.0/metadata.json').is_file()


@pytest.mark.parametrize('missing', [False, True])
def test_explicit_update_repairs_local_yaml_from_cloud(cli, cloud, external_tools, mantle_home, missing):
    state, _ = cloud
    original = dump_agent_yaml(default_agent_config('demo'))
    state['demo'] = {'yaml': original, 'revision': {'number': 1}}
    assert cli('create', 'demo').exit_code == 0
    root = mantle_home / 'agents/demo'
    (root / 'context/keep.md').write_text('local notes')
    if missing:
        (root / 'agent.yaml').unlink()
    else:
        (root / 'agent.yaml').write_text('agent: [')
    result = cli('update', 'demo')
    assert result.exit_code == 0, result.output
    assert (root / 'agent.yaml').read_text() == original
    assert (root / 'context/keep.md').read_text() == 'local notes'
