"""Desktop-controller boundaries without downloads or user-process signals."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip('fcntl')
SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    spec = importlib.util.spec_from_file_location('macos_launcher_test', SCRIPTS / 'macos_launcher.py')
    desktop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(desktop)
finally:
    sys.path.remove(str(SCRIPTS))


@pytest.fixture
def resources(tmp_path):
    resources = tmp_path / 'Example.app/Contents/Resources'
    source = resources / 'app'
    for name in ('cavity_fem/api.py', 'scripts/bootstrap.py', 'requirements.txt', 'static/index.html'):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('example')
    for arch in ('arm64', 'x86_64'):
        python = resources / 'python' / arch / 'bin/python3'
        python.parent.mkdir(parents=True)
        python.write_text('example executable')
        python.chmod(0o755)
    manifest = {'app_version': '0.1.7', 'source_sha256': desktop.source_digest(source),
                'runtimes': {'runtimes': {arch: {'archive_sha256': letter * 64}
                            for arch, letter in (('arm64', 'a'), ('x86_64', 'b'))}}}
    (resources / 'BUNDLE-MANIFEST.json').write_text(json.dumps(manifest))
    return resources


@pytest.mark.parametrize('arch', ['arm64', 'x86_64'])
def test_prepare_is_relocatable_and_keeps_bundle_unmodified(resources, tmp_path, arch):
    before = desktop.source_digest(resources)
    support = tmp_path / 'support 日本語 and spaces'
    root, python, version = desktop.prepare_application(resources, support, arch)
    assert root.is_dir() and python.is_file() and os.access(python, os.X_OK)
    assert version == '0.1.7'
    assert str(python).startswith(str(support))
    (root / 'user-preserved.txt').write_text('retain')
    moved = resources.parent.parent.with_name('Moved.app')
    resources.parent.parent.rename(moved)
    again = desktop.prepare_application(moved / 'Contents/Resources', support, arch)
    assert again == (root, python, version)
    assert (root / 'user-preserved.txt').read_text() == 'retain'
    assert desktop.source_digest(moved / 'Contents/Resources') == before


def test_changed_source_is_rejected_before_install(resources, tmp_path):
    (resources / 'app/static/index.html').write_text('changed')
    with pytest.raises(desktop.DesktopError, match='一致'):
        desktop.prepare_application(resources, tmp_path / 'support', 'arm64')
    assert not (tmp_path / 'support').exists()


def test_source_symlinks_and_unsupported_cpu_are_rejected(resources, tmp_path):
    with pytest.raises(desktop.DesktopError, match='CPU'):
        desktop.prepare_application(resources, tmp_path / 'support', 'unknown')
    (resources / 'app/link').symlink_to(tmp_path)
    with pytest.raises(desktop.DesktopError, match='リンク'):
        desktop.prepare_application(resources, tmp_path / 'support', 'arm64')


def test_second_controller_cannot_share_running_application_data(tmp_path):
    with desktop.desktop_lock(tmp_path):
        with pytest.raises(desktop.DesktopError, match='既に起動'):
            with desktop.desktop_lock(tmp_path):
                pytest.fail('lock admitted a duplicate controller')
    with desktop.desktop_lock(tmp_path):
        pass


def test_occupied_port_is_skipped_without_touching_other_service():
    import socket
    with socket.socket() as other:
        other.bind(('127.0.0.1', 0))
        port = other.getsockname()[1]
        if port > 65505:
            pytest.skip('ephemeral port too near upper range')
        selected = desktop.choose_port(port)
        assert selected != port
        assert other.getsockname()[1] == port


def test_shutdown_cancels_only_jobs_of_verified_instance(monkeypatch):
    calls = []
    expected = {'app': 'cavity-lab-public', 'version': '0.1.7', 'project_root': '/example',
                'data_root': '/example/data', 'development': False}
    health = {**expected, 'status': 'ok', 'instance_id': 'owned'}
    monkeypatch.setattr(desktop.bootstrap, 'server_health', lambda _: health)
    active_id = '00000000-0000-0000-0000-000000000001'
    def request(port, path, method='GET'):
        calls.append((port, path, method))
        return [{'id': active_id, 'status': 'running'}, {'id': 'completed', 'status': 'completed'}]
    monkeypatch.setattr(desktop, 'local_json', request)
    desktop.cancel_owned_jobs(8770, expected, 'different')
    assert calls == []
    desktop.cancel_owned_jobs(8770, expected, 'owned')
    assert calls == [(8770, '/api/jobs', 'GET'), (8770, '/api/jobs/' + active_id + '/cancel', 'POST')]


def test_cleanup_checks_group_even_after_leader_exits(monkeypatch):
    calls = []
    process = SimpleNamespace(pid=12345, poll=lambda: 0)
    def killpg(pid, action):
        calls.append((pid, action))
        if action == 0:
            raise ProcessLookupError
    monkeypatch.setattr(desktop.os, 'killpg', killpg)
    desktop.stop_group(process)
    assert calls == [(12345, signal.SIGINT), (12345, 0)]


def test_controller_never_launches_browser_or_installs_in_bundle(resources, tmp_path, monkeypatch):
    support = tmp_path / 'support'
    events, captured = [], {}
    monkeypatch.setattr(desktop, 'emit', lambda event, **data: events.append((event, data)))
    monkeypatch.setattr(desktop, 'choose_port', lambda: 8770)
    monkeypatch.setattr(desktop.platform, 'machine', lambda: 'arm64')
    monkeypatch.setattr(desktop, 'stop_group', lambda p: None)
    monkeypatch.setattr(desktop, 'cancel_owned_jobs', lambda *args: None)
    def spawn(command, **options):
        captured.update(command=command, **options)
        return SimpleNamespace(stdout=['キャビティ解析: http://127.0.0.1:8770/\n'], wait=lambda: 0)
    monkeypatch.setattr(desktop.subprocess, 'Popen', spawn)
    def health(port):
        return {'app': 'cavity-lab-public', 'status': 'ok', 'version': '0.1.7',
                'project_root': str(captured['cwd']), 'data_root': str(support / 'data'),
                'development': False, 'instance_id': 'owned'}
    monkeypatch.setattr(desktop.bootstrap, 'server_health', health)
    assert desktop.run(resources, support) == 0
    assert '--no-browser' in captured['command']
    assert captured['start_new_session'] is True
    assert captured['env']['CAVITY_FEM_DESKTOP'] == '1'
    assert captured['env']['PYTHONDONTWRITEBYTECODE'] == '1'
    assert captured['env']['PIP_ONLY_BINARY'] == ':all:'
    assert str(captured['cwd']).startswith(str(support))
    assert str(captured['command'][0]).startswith(str(support))
    assert events[-1][0] == 'ready'
    assert events[-1][1]['url'].startswith('http://127.0.0.1:8770/')


def test_controller_emits_error_for_failed_setup(resources, tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(desktop, 'emit', lambda event, **data: events.append((event, data)))
    monkeypatch.setattr(desktop, 'prepare_application', lambda *args: (_ for _ in ()).throw(desktop.DesktopError('test failure')))
    assert desktop.run(resources, tmp_path / 'support') == 1
    assert events[-1][0] == 'error' and events[-1][1]['message'] == 'test failure'
    assert Path(events[-1][1]['log_path']).is_file()
