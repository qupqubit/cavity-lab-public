"""Bootstrap boundary tests; no package downloads or real user process changes."""
import importlib.util
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


boot = load_script('bootstrap')
builder = load_script('build_source_zip')


@pytest.fixture
def app(tmp_path):
    root = tmp_path / '日本語 and spaces'
    root.mkdir()
    (root / 'requirements.txt').write_text('ngsolve==6.2.2506\nnumpy==2.0.2\n', encoding='utf-8')
    (root / 'cavity_fem').mkdir()
    (root / 'cavity_fem' / '__init__.py').write_text('__version__ = "0.1.0"\n', encoding='utf-8')
    return root


@pytest.fixture
def expected(app, monkeypatch):
    for key in ('CAVITY_FEM_DATA', 'CAVITY_FEM_DEV'):
        monkeypatch.delenv(key, raising=False)
    return boot.expected_identity(app)


def healthy(expected):
    return dict(expected, status='ok', instance_id='our-new-process')


@pytest.mark.parametrize('key,value', [
    ('app', 'unrelated-app'), ('status', 'starting'), ('version', '0.3.0'),
    ('project_root', '/different-folder'), ('data_root', '/different-data'),
    ('development', True), ('development', 'false'),
])
def test_refuse_foreign_or_stale_server(expected, key, value, monkeypatch):
    response = healthy(expected)
    response[key] = value
    monkeypatch.setattr(boot, 'port_is_listening', lambda port: True)
    monkeypatch.setattr(boot, 'server_health', lambda port: response)
    assert not boot.identity_matches(response, expected)
    with pytest.raises(boot.PortConflict, match='既存プロセスは停止していません'):
        boot.existing_server(8768, expected)


def test_reuse_only_matching_server(expected, monkeypatch):
    monkeypatch.setattr(boot, 'port_is_listening', lambda port: True)
    monkeypatch.setattr(boot, 'server_health', lambda port: healthy(expected))
    assert boot.existing_server(8768, expected)
    assert boot.identity_matches(healthy(expected), expected, 'our-new-process')
    assert not boot.identity_matches(healthy(expected), expected, 'another-process')


def test_unrecognized_service_on_port_is_not_reused(expected, monkeypatch):
    monkeypatch.setattr(boot, 'port_is_listening', lambda port: True)
    monkeypatch.setattr(boot, 'server_health', lambda port: None)
    with pytest.raises(boot.PortConflict):
        boot.existing_server(8768, expected)


def test_new_server_readiness_requires_launch_nonce(expected, monkeypatch):
    monkeypatch.setattr(boot, 'server_health', lambda port: healthy(expected))
    process = SimpleNamespace(poll=lambda: None)
    with pytest.raises(boot.PortConflict):
        boot.wait_healthy(process, 8768, expected, 'different-process')
    assert boot.wait_healthy(process, 8768, expected, 'our-new-process')['status'] == 'ok'


def test_version_read_without_executing_package(app, monkeypatch):
    monkeypatch.delenv('CAVITY_FEM_DATA', raising=False)
    (app/'cavity_fem/__init__.py').write_text('raise RuntimeError("must not import")\n__version__="0.1.0"\n', encoding='utf-8')
    assert boot.expected_identity(app)['version'] == '0.1.0'


def test_version_expression_cannot_execute(app):
    (app/'cavity_fem/__init__.py').write_text('__version__=str(4)\n', encoding='utf-8')
    with pytest.raises(boot.BootstrapError, match='バージョン'):
        boot.expected_identity(app)


def test_data_root_and_startup_mode_identity(app, monkeypatch):
    monkeypatch.setenv('CAVITY_FEM_DATA', '日本語 data')
    monkeypatch.setenv('CAVITY_FEM_DEV', '1')
    identity = boot.expected_identity(app)
    assert identity['data_root'] == str((app/'日本語 data').resolve())
    assert identity['development'] is False


def test_missing_python_has_official_installation_action(monkeypatch):
    monkeypatch.setattr(boot, 'probe_python', lambda command: None)
    with pytest.raises(boot.BootstrapError, match='https://www.python.org/downloads/'):
        boot.discover_python()


def test_unsupported_python_override_rejected(monkeypatch):
    monkeypatch.setattr(boot, 'probe_python', lambda command: None)
    with pytest.raises(boot.BootstrapError, match='3.9〜3.12'):
        boot.discover_python('/custom/python')


def local_python(app):
    executable = app / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    executable.parent.mkdir(parents=True)
    executable.touch()
    return executable


def fail_install(*args, **kwargs):
    pytest.fail('A verified environment must not be reinstalled')


def test_verified_venv_reuse_checks_actual_imports(app, monkeypatch):
    executable = local_python(app)
    calls = []
    monkeypatch.setattr(boot, 'probe_python', lambda command: {'version': [3, 9, 25]})
    def imports(path):
        calls.append(str(path))
        return {'ngsolve': '6.2.2506', 'numpy': '2.0.2'}, ''
    monkeypatch.setattr(boot, 'import_versions', imports)
    monkeypatch.setattr(boot, 'run_logged', fail_install)
    assert boot.ensure_environment(app) == str(executable)
    assert calls == [str(executable)]


def test_explicit_complete_environment_never_pip_installs_external(app, monkeypatch):
    monkeypatch.setattr(boot, 'discover_python', lambda override: '/external/python')
    monkeypatch.setattr(boot, 'import_versions', lambda path: ({'ngsolve': '6.2.2506', 'numpy': '2.0.2'}, ''))
    monkeypatch.setattr(boot, 'run_logged', fail_install)
    assert boot.ensure_environment(app, override='/external/python') == '/external/python'
    assert not (app/'.venv').exists()


def test_incomplete_external_environment_installs_only_in_local_venv(app, monkeypatch):
    commands = []
    monkeypatch.setattr(boot, 'discover_python', lambda override: '/external/python')
    def imports(path):
        if str(path) == '/external/python':
            return None, 'ngsolve not installed'
        return {'ngsolve': '6.2.2506', 'numpy': '2.0.2'}, ''
    monkeypatch.setattr(boot, 'import_versions', imports)
    monkeypatch.setattr(boot, 'run_logged', lambda command, *args, **kwargs: commands.append([str(x) for x in command]))
    executable = boot.ensure_environment(app, override='/external/python')
    assert commands[0][:5] == ['/external/python', '-X', 'utf8', '-m', 'venv']
    assert commands[1][0] == executable
    assert str(app/'.venv') in executable
    assert commands[1][3:6] == ['-m', 'pip', 'install']


def test_broken_venv_is_preserved(app, monkeypatch):
    venv = app/'.venv'
    venv.mkdir()
    sentinel = venv/'do-not-delete.txt'
    sentinel.write_text('keep', encoding='utf-8')
    monkeypatch.setattr(boot, 'run_logged', fail_install)
    with pytest.raises(boot.BootstrapError, match='別名へ移動'):
        boot.ensure_environment(app)
    assert sentinel.read_text(encoding='utf-8') == 'keep'


def test_native_import_failure_is_logged_after_local_repair_attempt(app, monkeypatch):
    executable = local_python(app)
    commands = []
    monkeypatch.setattr(boot, 'probe_python', lambda command: {'version': [3, 9, 25]})
    monkeypatch.setattr(boot, 'import_versions', lambda path: (None, 'native DLL load failed'))
    monkeypatch.setattr(boot, 'run_logged', lambda command, *args, **kwargs: commands.append(command))
    with pytest.raises(boot.BootstrapError, match='計算ライブラリを読み込めません'):
        boot.ensure_environment(app)
    assert len(commands) == 1 and commands[0][0] == executable
    assert 'native DLL load failed' in (app/'.bootstrap/install.log').read_text(encoding='utf-8')


def test_no_install_refuses_missing_environment_without_creating_it(app, monkeypatch):
    monkeypatch.setattr(boot, 'run_logged', fail_install)
    with pytest.raises(boot.BootstrapError, match='ローカル計算環境がありません'):
        boot.ensure_environment(app, install=False)
    assert not (app/'.venv').exists()


@pytest.mark.parametrize('value', ['0', '65536', 'not-a-port'])
def test_invalid_port_rejected(value, monkeypatch):
    monkeypatch.setenv('CAVITY_FEM_PORT', value)
    with pytest.raises(boot.BootstrapError, match='整数'):
        boot.selected_port()


def test_utf8_environment_inherited_by_workers(monkeypatch):
    monkeypatch.setenv('PYTHONUTF8', '0')
    monkeypatch.setenv('CAVITY_FEM_DATA', '日本語 path')
    env = boot.python_environment()
    assert env['PYTHONUTF8'] == '1'
    assert env['CAVITY_FEM_DATA'] == '日本語 path'


def test_source_zip_contains_assets_and_excludes_user_data(tmp_path):
    root = tmp_path/'source'
    for relative in builder.REQUIRED:
        target = root/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(relative, encoding='utf-8')
    for relative in ('data/private.step', '.git/config', '.venv/password.txt', '.bootstrap/install.log',
                     'cavity_fem/__pycache__/module.pyc', 'data/model.mph'):
        target = root/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('must not ship', encoding='utf-8')
    result = builder.build_zip(root, tmp_path/'app.zip')
    with zipfile.ZipFile(result['path']) as archive:
        names = archive.namelist()
        assert 'cavity-lab-public/static/index.html' in names
        assert 'cavity-lab-public/examples/cylinder-vacuum.step' in names
        assert not any('private.step' in name or '.venv/' in name or '.git/' in name or '.bootstrap/' in name or '.mph' in name for name in names)
        mode = archive.getinfo('cavity-lab-public/launch.command').external_attr >> 16
        assert stat.S_IMODE(mode) == 0o755
        manifest = json.loads(archive.read('cavity-lab-public/SOURCE-MANIFEST.json'))
        assert set(manifest['sha256']) >= builder.REQUIRED
        assert str(root) not in json.dumps(manifest)


def test_incomplete_zip_is_not_published(tmp_path):
    root = tmp_path/'source'
    root.mkdir()
    destination = tmp_path/'app.zip'
    with pytest.raises(ValueError, match='missing'):
        builder.build_zip(root, destination)
    assert not destination.exists()


def lifecycle_app(app, monkeypatch):
    (app/'cavity_fem/api.py').touch()
    (app/'static').mkdir()
    (app/'static/index.html').touch()
    monkeypatch.chdir(ROOT)  # Restore cwd after main changes into the extracted ZIP.
    monkeypatch.setattr(boot, '__file__', str(app/'scripts/bootstrap.py'))
    monkeypatch.setattr(boot, 'existing_server', lambda port, expected: False)
    monkeypatch.setattr(boot, 'ensure_environment', lambda *args, **kwargs: '/verified/python')
    process = SimpleNamespace(wait=lambda: 0)
    monkeypatch.setattr(boot.subprocess, 'Popen', lambda *args, **kwargs: process)
    return process


def test_browser_opens_once_only_after_readiness(app, monkeypatch):
    process = lifecycle_app(app, monkeypatch)
    events = []
    monkeypatch.setattr(boot, 'wait_healthy', lambda *args: events.append('healthy'))
    monkeypatch.setattr(boot, 'open_browser', lambda *args: events.append('browser'))
    monkeypatch.setattr(boot, 'stop_child', lambda child: events.append(('stop', child)))
    assert boot.main([]) == 0
    assert events == ['healthy', 'browser', ('stop', process)]


def test_startup_conflict_stops_only_owned_child_without_opening_browser(app, monkeypatch):
    process = lifecycle_app(app, monkeypatch)
    stopped = []
    def conflict(*args):
        raise boot.PortConflict('Another process won the port race')
    monkeypatch.setattr(boot, 'wait_healthy', conflict)
    monkeypatch.setattr(boot, 'open_browser', lambda *args: pytest.fail('Browser opened before readiness'))
    monkeypatch.setattr(boot, 'stop_child', lambda child: stopped.append(child))
    assert boot.main([]) == 1
    assert stopped == [process]
