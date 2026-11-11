"""Private local-server controller for the macOS application.

stdout is a newline-delimited JSON protocol for the native window. The bundled
Python is copied to a stable user-writable location before creating a venv, so
moving or updating the .app does not break its calculation environment. No
libraries are installed into the signed application bundle or system Python.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import bootstrap


class DesktopError(RuntimeError):
    pass


def emit(event, **data):
    print(json.dumps(dict(event=event, **data), ensure_ascii=False), flush=True)


def support_directory():
    override = os.environ.get('CAVITY_MAC_SUPPORT_DIR')
    return (Path(override).expanduser() if override else
            Path.home() / 'Library/Application Support/Cavity Lab').resolve()


@contextlib.contextmanager
def desktop_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'desktop.lock').open('a+b') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DesktopError('Cavity Labは既に起動しています。開いているアプリを確認してください。') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def validate_digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def source_digest(root):
    """Must agree with the builder's content-addressed source manifest."""
    entries = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise DesktopError('アプリの解析ソースに予期しないリンクが含まれています。')
        if path.is_file():
            entries.append((path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    # The builder and controller both use this public convention.
    return hashlib.sha256(json.dumps(entries, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def atomic_copy_tree(source, destination):
    """Install only a new content-addressed directory; retain existing data."""
    if destination.is_dir():
        return
    if destination.exists():
        raise DesktopError('計算環境の保存先に同名のファイルがあります: ' + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.preparing-', dir=str(destination.parent)))
    try:
        shutil.copytree(source, staging / 'payload', symlinks=True)
        (staging / 'payload').rename(destination)
    finally:
        # Only remove the unique temporary folder made by this invocation.
        shutil.rmtree(staging, ignore_errors=True)


def prepare_application(resources, support, machine=None):
    machine = machine or platform.machine()
    arch = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'x86_64'}.get(machine)
    if arch is None:
        raise DesktopError('このCPUには対応していません。Apple SiliconまたはIntelのMacが必要です。')
    source = resources / 'app'
    runtime = resources / 'python' / arch
    try:
        manifest = json.loads((resources / 'BUNDLE-MANIFEST.json').read_text())
        version = manifest['app_version']
        digest = manifest['source_sha256']
        runtime_record = manifest['runtimes']['runtimes'][arch]
        runtime_digest = runtime_record['archive_sha256']
    except (OSError, ValueError, TypeError, KeyError):
        raise DesktopError('アプリの構成情報を読み込めません。配布ZIPを展開し直してください。') from None
    if (not isinstance(version, str) or not version or
            not all(c.isdigit() or c == '.' for c in version) or
            not validate_digest(digest) or not validate_digest(runtime_digest)):
        raise DesktopError('アプリのバージョン情報が不正です。')
    if not all((source / name).is_file() for name in
               ('cavity_fem/api.py', 'scripts/bootstrap.py', 'requirements.txt', 'static/index.html')):
        raise DesktopError('アプリに必要な解析ファイルがありません。')
    if not (runtime / 'bin/python3').is_file():
        raise DesktopError('このMac用のPythonがアプリに含まれていません。')
    if source_digest(source) != digest:
        raise DesktopError('アプリの解析ファイルが配布時の内容と一致しません。展開し直してください。')
    root = support / 'versions' / (version + '-' + digest[:16])
    python_home = support / 'python' / (arch + '-' + runtime_digest[:16])
    emit('status', message='このMacの計算環境を準備しています。', data_root=str(support / 'data'))
    atomic_copy_tree(runtime, python_home)
    atomic_copy_tree(source, root)
    executable = python_home / 'bin/python3'
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise DesktopError('保存したPythonを起動できません。計算環境の保存先を確認してください。')
    return root, executable, version


def choose_port(preferred=8770):
    for port in range(preferred, min(preferred + 30, 65536)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            try:
                candidate.bind(('127.0.0.1', port))
            except OSError:
                continue
            return port
    raise DesktopError('アプリ用のローカル接続を作成できません。別のCavity Labを終了してください。')


def local_json(port, path, method='GET'):
    """Never use proxies or follow an unexpected redirect away from loopback."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path),
                                     data=b'' if method == 'POST' else None, method=method)
    with opener.open(request, timeout=3) as response:
        return json.load(response)


def cancel_owned_jobs(port, expected, instance_id):
    if not bootstrap.identity_matches(bootstrap.server_health(port), expected, instance_id):
        return
    try:
        for job in local_json(port, '/api/jobs'):
            if job.get('status') in ('running', 'queued'):
                # UUID validation in the API also protects this local route.
                import uuid
                identifier = str(uuid.UUID(job['id']))
                local_json(port, '/api/jobs/' + identifier + '/cancel', 'POST')
    except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError):
        pass  # Process-group cleanup still guarantees termination below.


def stop_group(process):
    """Stop the dedicated process group created by this controller only."""
    if process is None:
        return
    for action, timeout in ((signal.SIGINT, 12), (signal.SIGTERM, 3), (signal.SIGKILL, 3)):
        try:
            os.killpg(process.pid, action)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            process.poll()  # Reap the leader even if a descendant remains.
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return
            time.sleep(.05)


def run(resources, support, no_install=False):
    process = None
    port = None
    expected = None
    instance_id = None
    support.mkdir(parents=True, exist_ok=True)
    log_dir = support / 'logs'
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / ('launch-' + time.strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid()) + '.log')
    emit('status', message='アプリを準備しています。', log_path=str(log_path), data_root=str(support / 'data'))
    try:
        with desktop_lock(support), log_path.open('a', encoding='utf-8') as log:
            try:
                root, python, version = prepare_application(resources, support)
                data = support / 'data'
                data.mkdir(exist_ok=True)
                port = choose_port()
                expected = {'app': bootstrap.APP_ID, 'version': version,
                            'project_root': str(root.resolve()), 'data_root': str(data.resolve()),
                            'development': False}
                env = bootstrap.python_environment()
                env.update(CAVITY_FEM_PYTHON=str(python), CAVITY_FEM_PORT=str(port),
                           CAVITY_FEM_DATA=str(data), CAVITY_FEM_NO_BROWSER='1',
                           CAVITY_FEM_DESKTOP='1', PYTHONDONTWRITEBYTECODE='1',
                           PIP_ONLY_BINARY=':all:')
                command = [str(python), '-B', '-X', 'utf8', str(root / 'scripts/bootstrap.py'), '--no-browser']
                if no_install:
                    command.append('--no-install')
                emit('status', message='計算ライブラリを確認しています。初回の準備にはネット接続と数分が必要です。')
                process = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                           errors='replace', bufsize=1, start_new_session=True)
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    if line.startswith('キャビティ解析: '):
                        health = bootstrap.server_health(port)
                        if not bootstrap.identity_matches(health, expected):
                            raise DesktopError('このアプリの計算サーバーを確認できませんでした。')
                        instance_id = health['instance_id']
                        emit('ready', url='http://127.0.0.1:%d/?v=%s' % (port, version),
                             data_root=str(data), log_path=str(log_path))
                    elif line.startswith(('このPCに専用', '計算ライブラリを準備', '既存のローカル計算環境')):
                        emit('status', message=line.strip(), log_path=str(log_path))
                code = process.wait()
                if code:
                    install_log = root / '.bootstrap/install.log'
                    if install_log.is_file():
                        with install_log.open('rb') as detail:
                            detail.seek(max(0, install_log.stat().st_size - 200_000))
                            log.write('\n--- 計算ライブラリの準備ログ ---\n')
                            log.write(detail.read().decode('utf-8', errors='replace'))
                    raise DesktopError('計算環境を起動できませんでした。「ログを開く」で詳細を確認できます。')
            finally:
                if port is not None and instance_id is not None:
                    cancel_owned_jobs(port, expected, instance_id)
                stop_group(process)
        return 0
    except KeyboardInterrupt:
        emit('status', message='計算を停止してアプリを終了します。')
        return 0
    except Exception as exc:
        with log_path.open('a', encoding='utf-8') as log:
            log.write('\n' + str(exc) + '\n')
        emit('error', message=str(exc), log_path=str(log_path))
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-install', action='store_true', help='Use an already prepared runtime only (validation).')
    args = parser.parse_args(argv)
    resources = Path(os.environ.get('CAVITY_MAC_RESOURCE_DIR',
                                    str(Path(__file__).resolve().parents[2]))).resolve()
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    return run(resources, support_directory(), args.no_install)


if __name__ == '__main__':
    raise SystemExit(main())
