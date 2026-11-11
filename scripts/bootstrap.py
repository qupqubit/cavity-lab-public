"""Standard-library ZIP bootstrap for a local キャビティ解析 instance (Python 3.9+)."""
import argparse
import ast
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import webbrowser

APP_ID = 'cavity-lab-public'
SUPPORTED = ((3, 9), (3, 10), (3, 11), (3, 12))
PYTHON_URL = 'https://www.python.org/downloads/'
VERSION_PROBE = "import json,sys;print(json.dumps({'version':list(sys.version_info[:3]),'executable':sys.executable}))"
IMPORT_PROBE = """import json, importlib.metadata as metadata
import ngsolve, netgen.occ, numpy, scipy, fastapi, uvicorn, multipart
print(json.dumps({name:metadata.version(name) for name in ('ngsolve','numpy','scipy','fastapi','uvicorn','python-multipart')}))
"""


class BootstrapError(RuntimeError):
    pass


class PortConflict(BootstrapError):
    pass


def python_environment():
    env = os.environ.copy()
    env.update(PYTHONUTF8='1', PYTHONUNBUFFERED='1', OPENBLAS_NUM_THREADS='1')
    env.setdefault('OMP_NUM_THREADS', '2')
    return env


def probe_python(command):
    try:
        result = subprocess.run(list(command)+['-X', 'utf8', '-I', '-c', VERSION_PROBE],
                                text=True, encoding='utf-8', errors='replace', capture_output=True,
                                env=python_environment(), timeout=12)
        value = json.loads(result.stdout.strip()) if result.returncode == 0 else None
        if not value or tuple(value['version'][:2]) not in SUPPORTED:
            return None
        return value
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return None


def discover_python(override=None):
    if override:
        value = probe_python([override])
        if value is None:
            raise BootstrapError('CAVITY_FEM_PYTHONにはPython 3.9〜3.12の実行ファイルを指定してください。')
        return value['executable']
    candidates = []
    if os.name == 'nt' and shutil.which('py'):
        candidates.extend((['py', '-3.%d' % n] for n in (12, 11, 10, 9)))
    candidates.extend(([name] for name in ('python3.12', 'python3.11', 'python3.10', 'python3.9', 'python3', 'python')))
    candidates.append([sys.executable])
    for command in candidates:
        value = probe_python(command)
        if value:
            return value['executable']
    raise BootstrapError('Python 3.9〜3.12が見つかりません（3.12推奨）。'+PYTHON_URL+' からインストールして再度起動してください。')


def import_versions(executable):
    try:
        result = subprocess.run([str(executable), '-X', 'utf8', '-I', '-c', IMPORT_PROBE],
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                env=python_environment(), timeout=60)
        if result.returncode == 0:
            for line in reversed(result.stdout.splitlines()):
                try:
                    return json.loads(line), ''
                except ValueError:
                    continue
        return None, (result.stderr or result.stdout)[-6000:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)


def pinned_versions(requirements):
    result = {}
    for line in requirements.splitlines():
        line = line.split('#', 1)[0].strip()
        if '==' in line:
            name, version = line.split('==', 1)
            result[name.strip()] = version.strip()
    return result


def versions_match(found, requirements):
    return isinstance(found, dict) and all(found.get(name) == version
                                         for name, version in pinned_versions(requirements).items())


def stop_child(process):
    if process.poll() is not None:
        return
    try:
        process.send_signal(signal.CTRL_BREAK_EVENT if os.name == 'nt' else signal.SIGINT)
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt):
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()


def run_logged(command, log_path, failure_message, cwd=None):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open('a', encoding='utf-8') as log:
            process = subprocess.Popen([str(item) for item in command], cwd=cwd,
                                       env=python_environment(), stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True,
                                       encoding='utf-8', errors='replace', bufsize=1,
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
            try:
                for line in process.stdout:
                    print(line, end='', flush=True)
                    log.write(line); log.flush()
                code = process.wait()
            except KeyboardInterrupt:
                stop_child(process)
                raise
    except OSError as exc:
        raise BootstrapError(f'{failure_message}\n詳細: {exc}\n記録: {log_path}') from exc
    if code:
        raise BootstrapError(f'{failure_message}\n記録: {log_path}')


def ensure_environment(root, override=None, install=True):
    requirements = (root / 'requirements.txt').read_text(encoding='utf-8')
    state_dir = root / '.bootstrap'
    state_dir.mkdir(exist_ok=True)
    # Never install into a user-provided external interpreter. A complete
    # explicit environment can be reused; otherwise it only creates .venv.
    external = discover_python(override) if override else None
    if external:
        versions, _ = import_versions(external)
        if versions_match(versions, requirements):
            print('指定されたPython環境を確認しました。', flush=True)
            return external
    venv = root / '.venv'
    executable = venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if executable.exists():
        if probe_python([str(executable)]) is None:
            raise BootstrapError('.venvのPythonが動作しないか対応バージョン外です。.venvを別名へ移動し、再度起動してください。既存フォルダは自動削除しません。')
        versions, _ = import_versions(executable)
        if versions_match(versions, requirements):
            print('既存のローカル計算環境を確認しました。', flush=True)
            return str(executable)
    elif venv.exists():
        raise BootstrapError('.venvは存在しますがPython実行ファイルがありません。.venvを別名へ移動して再度起動してください。')
    else:
        if not install:
            raise BootstrapError('ローカル計算環境がありません。通常起動で初回準備を実行してください。')
        base = external or discover_python()
        print('このPCに専用のPython環境を作成します。', flush=True)
        run_logged([base, '-X', 'utf8', '-m', 'venv', str(venv)], state_dir / 'install.log',
                   '専用環境を作成できませんでした。書込み可能なフォルダへZIP全体を展開し、Pythonのvenv機能を確認してください。', cwd=root)
    if not install:
        raise BootstrapError('計算ライブラリの読込みまたはバージョン確認に失敗しました。通常起動で環境を修復してください。')
    print('計算ライブラリを準備します。初回はネット接続が必要で、数分かかる場合があります。', flush=True)
    run_logged([executable, '-X', 'utf8', '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(root / 'requirements.txt')],
               state_dir / 'install.log', 'ライブラリの準備に失敗しました。インターネット接続、空き容量、対応Pythonを確認してください。', cwd=root)
    versions, detail = import_versions(executable)
    if not versions_match(versions, requirements):
        with (state_dir / 'install.log').open('a', encoding='utf-8') as log:
            log.write('\nImport verification failed:\n'+detail)
        raise BootstrapError('計算ライブラリを読み込めません。OS・CPU・Pythonに対応する配布物が必要です。記録: '+str(state_dir / 'install.log'))
    (state_dir / 'environment.json').write_text(json.dumps({'requirements_sha256': hashlib.sha256(requirements.encode()).hexdigest(),
        'python': str(executable), 'versions': versions}, ensure_ascii=False, indent=2), encoding='utf-8')
    return str(executable)


def selected_port():
    try:
        value = int(os.environ.get('CAVITY_FEM_PORT', '8768'))
    except ValueError:
        raise BootstrapError('CAVITY_FEM_PORTは1〜65535の整数にしてください。') from None
    if not 1 <= value <= 65535:
        raise BootstrapError('CAVITY_FEM_PORTは1〜65535の整数にしてください。')
    return value


def expected_identity(root):
    data = Path(os.environ.get('CAVITY_FEM_DATA', str(root / 'data'))).expanduser()
    if not data.is_absolute():
        data = root / data
    version = None
    try:
        tree = ast.parse((root/'cavity_fem/__init__.py').read_text(encoding='utf-8'))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets):
                version = ast.literal_eval(node.value)
    except (SyntaxError, ValueError, TypeError) as exc:
        raise BootstrapError('配布物のバージョンを読み取れません。ZIP全体を展開し直してください。') from exc
    if not isinstance(version, str):
        raise BootstrapError('配布物のバージョンを読み取れません。ZIP全体を展開し直してください。')
    return {'app': APP_ID, 'version': version, 'project_root': str(root.resolve()), 'data_root': str(data.resolve()),
            'development': False}


def same_path(a, b):
    return isinstance(a, str) and os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def identity_matches(value, expected, instance_id=None):
    return (isinstance(value, dict) and value.get('app') == APP_ID and value.get('status') == 'ok'
            and value.get('version') == expected['version']
            and same_path(value.get('project_root'), expected['project_root'])
            and same_path(value.get('data_root'), expected['data_root'])
            and value.get('development') is expected['development']
            and (instance_id is None or value.get('instance_id') == instance_id))


def port_is_listening(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(.3)
        return probe.connect_ex(('127.0.0.1', port)) == 0


def server_health(port):
    # Loopback must bypass proxy settings, and redirects must not leave this PC.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
            body = response.read(65537)
            return json.loads(body.decode('utf-8')) if len(body) <= 65536 else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def existing_server(port, expected):
    if not port_is_listening(port):
        return False
    if identity_matches(server_health(port), expected):
        return True
    raise PortConflict(f'ポート{port}は別サービス、別フォルダ、別バージョン、または別モードで使用中です。そのサービスを終了するかCAVITY_FEM_PORTを変更してください。既存プロセスは停止していません。')


@contextlib.contextmanager
def preparation_lock(root, port, expected, timeout=600):
    directory = root / '.bootstrap'
    directory.mkdir(exist_ok=True)
    with (directory / 'launch.lock').open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        start, announced = time.monotonic(), False
        while True:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if not announced:
                    print('別の起動処理が準備中です。完了を待ちます。', flush=True); announced = True
                if existing_server(port, expected):
                    yield False
                    return
                if time.monotonic()-start > timeout:
                    raise BootstrapError('別の起動処理を待っています。先に開いた起動ウィンドウを確認してください。')
                time.sleep(.5)
        try:
            yield True
        finally:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)


def wait_healthy(process, port, expected, instance_id, timeout=90):
    start = time.monotonic()
    while time.monotonic()-start < timeout:
        if process.poll() is not None:
            raise BootstrapError('ローカルサーバーが起動前に終了しました。上のエラー表示を確認してください。')
        health = server_health(port)
        if health is not None:
            if identity_matches(health, expected, instance_id):
                return health
            raise PortConflict('起動中に別のサーバーが同じポートへ現れました。新しく開始したプロセスだけを終了します。')
        time.sleep(.3)
    raise BootstrapError('ローカルサーバーの起動確認が時間内に完了しませんでした。')


def open_browser(port, disabled=False):
    url = f'http://127.0.0.1:{port}'
    print('キャビティ解析: '+url, flush=True)
    if not disabled:
        try:
            if not webbrowser.open(url, new=2):
                print('ブラウザで上のアドレスを開いてください。', flush=True)
        except webbrowser.Error:
            print('ブラウザで上のアドレスを開いてください。', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='キャビティ解析 local ZIP launcher')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--no-install', action='store_true', help='準備済み環境だけを使う（検証用）')
    args = parser.parse_args(argv)
    root, child = Path(__file__).resolve().parents[1], None
    try:
        if not all((root / name).exists() for name in ('requirements.txt', 'cavity_fem/api.py', 'static/index.html')):
            raise BootstrapError('ソースZIP全体を展開してから起動してください。必要なファイルがありません。')
        os.chdir(root)
        port, expected = selected_port(), expected_identity(root)
        no_browser = args.no_browser or os.environ.get('CAVITY_FEM_NO_BROWSER') == '1'
        if existing_server(port, expected):
            print('このフォルダのキャビティ解析は既に起動しています。', flush=True)
            open_browser(port, no_browser)
            return 0
        with preparation_lock(root, port, expected) as acquired:
            if not acquired or existing_server(port, expected):
                open_browser(port, no_browser)
                return 0
            executable = ensure_environment(root, os.environ.get('CAVITY_FEM_PYTHON'), install=not args.no_install)
            # Recheck after potentially lengthy setup; never touch someone else's process.
            if existing_server(port, expected):
                open_browser(port, no_browser)
                return 0
            instance_id = str(uuid.uuid4())
            env = python_environment()
            env.update(CAVITY_FEM_PORT=str(port), CAVITY_FEM_INSTANCE_ID=instance_id, CAVITY_FEM_DATA=expected['data_root'])
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            child = subprocess.Popen([str(executable), '-X', 'utf8', '-m', 'cavity_fem.api'], cwd=root,
                                     env=env, creationflags=flags)
            wait_healthy(child, port, expected, instance_id)
            open_browser(port, no_browser)
        print('計算はこのPCで実行されます。終了するにはこのウィンドウでCtrl+Cを押してください。', flush=True)
        return child.wait()
    except KeyboardInterrupt:
        print('\nローカルサーバーを終了します。', flush=True)
        return 0
    except (BootstrapError, OSError) as exc:
        print('\n起動できませんでした: '+str(exc), file=sys.stderr, flush=True)
        return 1
    finally:
        if child is not None:
            stop_child(child)


if __name__ == '__main__':
    raise SystemExit(main())
