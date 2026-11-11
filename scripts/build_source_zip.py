"""Build a complete ZIP app without Git, environments, or local job data."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat
import zipfile

SOURCE_DIRS = {'cavity_fem', 'static', 'scripts', 'docs', 'manual', 'examples', 'tests', 'LICENSES', '.github'}
SOURCE_FILES = {'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'pyproject.toml', 'requirements.txt', 'requirements-tested.txt',
                'launch.command', 'launch.bat', '.gitignore'}
EXCLUDED_DIRS = {'.git', '.venv', '.bootstrap', '__pycache__', '.pytest_cache', 'data', 'data-development', 'node_modules'}
EXCLUDED_SUFFIXES = {'.pyc', '.class', '.mph', '.mphbin', '.vtu', '.vtk', '.zip'}
REQUIRED = {'launch.command', 'launch.bat', 'scripts/bootstrap.py', 'cavity_fem/api.py',
            'static/index.html', 'requirements.txt', 'examples/cylinder-vacuum.step'}


def source_files(root):
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if not path.is_file() or path.is_symlink():
            continue
        if any(part in EXCLUDED_DIRS or part.endswith('.egg-info') for part in relative.parts):
            continue
        if relative.parts[0] not in SOURCE_DIRS and str(relative) not in SOURCE_FILES:
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES or path.name == '.DS_Store' or path.name.endswith('.class.status'):
            continue
        yield path, relative.as_posix()


def build_zip(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    files = list(source_files(root))
    missing = REQUIRED - {relative for _, relative in files}
    if missing:
        raise ValueError('Complete application files are missing: '+', '.join(sorted(missing)))
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = {'format': 'cavity-lab-public-source-zip-v1', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'installation': 'Extract the whole cavity-lab-public folder and launch locally; no clone required.',
                'excluded': 'Git, virtual environments, local calculation data, compiled/native result binaries', 'sha256': {}}
    temporary = destination.with_suffix(destination.suffix+'.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path, relative in files:
                data = path.read_bytes()
                info = zipfile.ZipInfo('cavity-lab-public/'+relative)
                info.create_system = 3
                mode = 0o755 if relative.endswith('.command') else 0o644
                info.external_attr = (stat.S_IFREG | mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
                manifest['sha256'][relative] = hashlib.sha256(data).hexdigest()
            archive.writestr('cavity-lab-public/SOURCE-MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))
        with zipfile.ZipFile(temporary) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError('ZIP integrity failure: '+bad)
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {'path': str(destination), 'files': len(files)+1, 'bytes': destination.stat().st_size,
            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    print(json.dumps(build_zip(root, args.output or root.parent/'cavity-lab-public.zip'), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
