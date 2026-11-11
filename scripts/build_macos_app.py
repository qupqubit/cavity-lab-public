"""Build an ad-hoc signed universal macOS app from supplied CPython runtimes.

No downloads or FEM installations occur here. Runtime archives and their
upstream checksums must be verified by the caller before extraction.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURES = ("arm64", "x86_64")
CPU_TYPES = {0x0100000C: "arm64", 0x01000007: "x86_64"}
SOURCE_FILES = {
    "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "pyproject.toml",
    "requirements.txt", "requirements-tested.txt", "scripts/bootstrap.py",
    "scripts/macos_launcher.py", "examples/cylinder-vacuum.step",
    "examples/cylinder.json", "manual/methods.md", "macos/icon-NOTICE.md",
    "macos/RUNTIME_NOTICES.md",
}
SOURCE_TREES = {"cavity_fem", "static", "LICENSES"}
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".git", ".venv", ".bootstrap", "data", "node_modules"}
STATIC_SUFFIXES = {".html", ".css", ".js", ".svg", ".png", ".ico", ".webp", ".woff", ".woff2"}
REQUIRED_FILES = {
    "cavity_fem/__init__.py", "cavity_fem/api.py", "cavity_fem/worker.py",
    "cavity_fem/solver.py", "cavity_fem/spectrum.py", "static/index.html",
    "static/app.js", "static/style.css", "scripts/bootstrap.py",
    "scripts/macos_launcher.py", "requirements.txt", "LICENSE",
    "THIRD_PARTY_NOTICES.md", "examples/cylinder-vacuum.step", "macos/icon-NOTICE.md",
}


def _within(path, directory):
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _regular_tree(directory, allow_symlinks=False):
    """Validate before copying; never follow an external or absolute link."""
    directory = Path(directory).resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("Expected a directory: " + str(directory))
    for current, dirs, files in os.walk(directory, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(current) / name
            if path.is_symlink():
                if not allow_symlinks:
                    raise ValueError("Symlinks are not allowed in app source/notices: " + str(path))
                link = os.readlink(path)
                if os.path.isabs(link):
                    raise ValueError("Runtime symlink must be relative for relocation: " + str(path))
                try:
                    resolved = path.resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise ValueError("Broken or cyclic runtime symlink: " + str(path)) from exc
                if not _within(resolved, directory):
                    raise ValueError("Runtime symlink escapes its runtime: " + str(path))
            elif not path.is_dir() and not path.is_file():
                raise ValueError("Only regular files/directories are allowed: " + str(path))
    return directory


def runtime_source_files(root):
    """The executable app gets only reviewed runtime roots and named assets."""
    root = Path(root).resolve(strict=True)
    files = []
    for top in sorted(SOURCE_TREES):
        directory = root / top
        if not directory.exists():
            continue
        if directory.is_symlink():
            raise ValueError("Source directory must not be a symlink: " + str(directory))
        for current, dirs, names in os.walk(directory, followlinks=False):
            dirs[:] = sorted(name for name in dirs if name not in EXCLUDED_PARTS)
            for name in dirs + sorted(names):
                path = Path(current) / name
                relative = path.relative_to(root)
                if path.is_symlink():
                    raise ValueError("Source symlink is not allowed: " + str(relative))
                if not path.is_file() or path.name == ".DS_Store":
                    continue
                if top == "cavity_fem" and path.suffix != ".py":
                    continue
                if top == "static" and path.suffix.lower() not in STATIC_SUFFIXES:
                    continue
                if top == "LICENSES" and (path.suffix.lower() in {".so", ".dylib", ".a", ".pyc", ".zip"}
                                          or path.name.startswith(".")):
                    continue
                files.append((path, relative.as_posix()))
    for relative in sorted(SOURCE_FILES):
        path = root / relative
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root and _within(parent, root)):
            raise ValueError("Named source asset must not be a symlink: " + relative)
        if path.is_file():
            files.append((path, relative))
    files.sort(key=lambda item: item[1])
    missing = REQUIRED_FILES - {relative for _, relative in files}
    if missing:
        raise ValueError("Required app source files are missing: " + ", ".join(sorted(missing)))
    return files


def app_version(root):
    tree = ast.parse((Path(root) / "cavity_fem/__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and re.fullmatch(r"\d+\.\d+\.\d+", value):
                return value
    raise ValueError("App __version__ must be a literal three-part version")


def macho_architectures(path):
    """Read Mach-O headers without executing supplied binaries."""
    with Path(path).open("rb") as stream:
        data = stream.read(4096)
    magic = data[:4]
    if magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce"):
        if len(data) < 8:
            raise ValueError("Truncated Mach-O header")
        endian = "<" if magic[0] in (0xCF, 0xCE) else ">"
        name = CPU_TYPES.get(struct.unpack_from(endian + "I", data, 4)[0])
        return {name} if name else {"other"}
    if magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf", b"\xbe\xba\xfe\xca", b"\xbf\xba\xfe\xca"):
        endian = ">" if magic[0] == 0xCA else "<"
        if len(data) < 8:
            raise ValueError("Truncated universal Mach-O header")
        count = struct.unpack_from(endian + "I", data, 4)[0]
        stride = 32 if magic in (b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca") else 20
        if not 1 <= count <= 64 or len(data) < 8 + count * stride:
            raise ValueError("Invalid universal Mach-O architecture table")
        return {CPU_TYPES.get(struct.unpack_from(endian + "I", data, 8 + i * stride)[0], "other") for i in range(count)}
    return set()


def validate_runtime(path, architecture):
    root = _regular_tree(path, allow_symlinks=True)
    executable = root / "bin/python3"
    if not executable.is_file() or not executable.stat().st_mode & 0o111:
        raise ValueError("Runtime bin/python3 must exist and be executable")
    if architecture not in macho_architectures(executable):
        raise ValueError("Runtime Python architecture does not match " + architecture)
    if not (root / "lib/python3.12/os.py").is_file():
        raise ValueError("Expected a complete CPython 3.12 install_only tree")
    if (root / "pyvenv.cfg").exists():
        raise ValueError("A user virtual environment is not a relocatable base runtime")
    allowed_bootstrap = re.compile(r"(?:pip|setuptools|wheel)(?:-[^/]+\.dist-info)?$|_distutils_hack$|pkg_resources$|distutils-precedence\.pth$")
    for packages in root.rglob("site-packages"):
        for entry in packages.iterdir():
            if entry.name in {"README.txt", "__pycache__"}:
                continue
            if not allowed_bootstrap.fullmatch(entry.name):
                raise ValueError("Bundled runtime contains a non-bootstrap package: " + entry.name)
    return root


def validate_runtime_manifest(manifest):
    if not isinstance(manifest, dict) or not re.fullmatch(r"3\.12\.\d+", str(manifest.get("python_version", ""))):
        raise ValueError("Runtime manifest requires python_version 3.12.x")
    if not isinstance(manifest.get("provider"), str) or not manifest["provider"].strip():
        raise ValueError("Runtime manifest requires provider")
    if not isinstance(manifest.get("runtimes"), dict):
        raise ValueError("Runtime manifest requires architecture records")
    for architecture in ARCHITECTURES:
        entry = manifest.get("runtimes", {}).get(architecture, {})
        if not isinstance(entry, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("archive_sha256", ""))):
            raise ValueError("Runtime manifest requires archive_sha256 for " + architecture)
        if not str(entry.get("source_url", "")).startswith("https://"):
            raise ValueError("Runtime manifest requires an HTTPS source_url for " + architecture)
    def check(value):
        if isinstance(value, str) and (value.startswith(("/", "file:")) or re.match(r"^[A-Za-z]:[\\/]", value)):
            raise ValueError("Runtime manifest must not contain machine-local absolute paths")
        if isinstance(value, dict):
            for item in value.values(): check(item)
        elif isinstance(value, list):
            for item in value: check(item)
    check(manifest)
    # Also reject values which cannot be represented in the shipped JSON.
    json.dumps(manifest, allow_nan=False)
    return manifest


def _has_license(directory):
    return any(path.is_file() and ("license" in path.name.lower() or "licence" in path.name.lower()
                                  or "copyright" in path.name.lower()) for path in Path(directory).rglob("*"))


def copy_app_source(root, destination):
    hashes = {}
    for path, relative in runtime_source_files(root):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(0o644)
        hashes[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
    entries = sorted(hashes.items())
    canonical = json.dumps(entries, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {"source_sha256": hashlib.sha256(canonical).hexdigest(), "files": hashes}


def run(command):
    try:
        return subprocess.run([str(value) for value in command], check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Build command failed: %s\n%s" % (command[0], exc.stderr or exc.stdout or str(exc))) from exc


def compile_launcher(source, destination, temporary):
    sdk = run(["xcrun", "--sdk", "macosx", "--show-sdk-path"]).stdout.strip()
    module_cache = temporary / "ModuleCache"
    module_cache.mkdir(parents=True, exist_ok=True)
    binaries = []
    for architecture in ARCHITECTURES:
        binary = temporary / ("launcher-" + architecture)
        run(["xcrun", "swiftc", "-target", architecture + "-apple-macosx12.0", "-sdk", sdk,
             "-module-cache-path", module_cache,
             "-O", "-framework", "AppKit", "-framework", "WebKit", source, "-o", binary])
        binaries.append(binary)
    run(["xcrun", "lipo", "-create", *binaries, "-output", destination])
    destination.chmod(0o755)


def sign_bundle(bundle):
    signed = 0
    # Preserve valid upstream signatures. Sign only unsigned/invalid Mach-O
    # files, inside out, before sealing Resources and the outer application.
    native = [path for path in bundle.rglob("*") if path.is_file() and not path.is_symlink() and macho_architectures(path)]
    for path in sorted(native, key=lambda path: len(path.parts), reverse=True):
        verify = subprocess.run(["codesign", "--verify", "--strict", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if verify.returncode:
            run(["codesign", "--force", "--sign", "-", "--timestamp=none", path])
            signed += 1
    run(["codesign", "--force", "--sign", "-", "--timestamp=none", bundle])
    run(["codesign", "--verify", "--deep", "--strict", bundle])
    return {"kind": "ad_hoc", "newly_signed_macho_files": signed, "notarized": False}


def validate_icon(path):
    """Check the ICNS container and return its content provenance digest."""
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.parent.is_symlink():
        raise ValueError("A regular macos/CavityFEM.icns icon file is required")
    data = path.read_bytes()
    if len(data) < 16 or data[:4] != b"icns" or struct.unpack_from(">I", data, 4)[0] != len(data):
        raise ValueError("CavityFEM.icns must be a complete ICNS container")
    offset = 8
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("CavityFEM.icns has a truncated icon record")
        size = struct.unpack_from(">I", data, offset + 4)[0]
        if size < 8 or offset + size > len(data):
            raise ValueError("CavityFEM.icns has an invalid icon record")
        offset += size
    return hashlib.sha256(data).hexdigest()


def validate_bundle(bundle):
    contents = bundle / "Contents"
    metadata = plistlib.loads((contents / "Info.plist").read_bytes())
    if metadata["CFBundleIdentifier"] != "jp.qupqubit.cavitylab":
        raise ValueError("Unexpected app identity")
    if any(metadata.get(key) != "CavityFEM" for key in
           ("CFBundleName", "CFBundleDisplayName", "CFBundleExecutable", "CFBundleIconFile")):
        raise ValueError("App name, executable and icon must be CavityFEM")
    executable = contents / "MacOS" / metadata["CFBundleExecutable"]
    if not executable.stat().st_mode & 0o111 or macho_architectures(executable) != set(ARCHITECTURES):
        raise ValueError("Launcher must be executable and universal arm64/x86_64")
    resources = contents / "Resources"
    runtime_source_files(resources / "app")
    for architecture in ARCHITECTURES:
        validate_runtime(resources / "python" / architecture, architecture)
    manifest = json.loads((resources / "BUNDLE-MANIFEST.json").read_text(encoding="utf-8"))
    if metadata["CFBundleShortVersionString"] != manifest["app_version"]:
        raise ValueError("Bundle version does not match source inventory")
    icon_hash = validate_icon(resources / "CavityFEM.icns")
    if manifest.get("icon") != {"file": "CavityFEM.icns", "source": "macos/CavityFEM.icns", "sha256": icon_hash}:
        raise ValueError("Bundle icon does not match its provenance digest")
    return metadata


def build_app(root, python_arm64, python_x86_64, output, runtime_manifest, runtime_notices=None, archive=None):
    if sys.platform != "darwin":
        raise RuntimeError("A macOS host with Xcode command-line tools is required")
    root = Path(root).resolve(strict=True)
    output = Path(output).absolute()
    if output.suffix != ".app" or output.exists() or output.is_symlink():
        raise ValueError("Output must be a new, non-existing .app path")
    manifest = validate_runtime_manifest(runtime_manifest)
    runtimes = {architecture: validate_runtime(path, architecture)
                for architecture, path in zip(ARCHITECTURES, (python_arm64, python_x86_64))}
    notices = _regular_tree(runtime_notices) if runtime_notices else None
    input_trees = [root, *runtimes.values()] + ([notices] if notices else [])
    if any(_within(output.resolve(), path) for path in input_trees):
        raise ValueError("Output must be outside all input trees")
    if not all(_has_license(path) or (notices and _has_license(notices)) for path in runtimes.values()):
        raise ValueError("Runtime license notices are required in each runtime or --runtime-notices")
    runtime_source_files(root)
    swift = root / "macos/CavityLab.swift"
    if not swift.is_file() or swift.is_symlink():
        raise ValueError("macos/CavityLab.swift is required")
    icon = root / "macos/CavityFEM.icns"
    icon_hash = validate_icon(icon)
    version = app_version(root)
    archive = Path(archive).absolute() if archive else None
    if archive and (archive.exists() or archive.is_symlink() or archive.suffix != ".zip"):
        raise ValueError("Archive must be a new, non-existing .zip path")
    if archive and any(_within(archive.resolve(), path) for path in input_trees + [output.resolve()]):
        raise ValueError("Archive must be outside input trees and the output bundle")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".cavity-lab-build-", dir=output.parent) as temporary:
        temporary = Path(temporary)
        bundle = temporary / output.name
        contents = bundle / "Contents"
        resources = contents / "Resources"
        (contents / "MacOS").mkdir(parents=True)
        resources.mkdir()
        source_inventory = copy_app_source(root, resources / "app")
        shutil.copyfile(icon, resources / "CavityFEM.icns")
        (resources / "CavityFEM.icns").chmod(0o644)
        for architecture, runtime in runtimes.items():
            shutil.copytree(runtime, resources / "python" / architecture, symlinks=True, copy_function=shutil.copy2)
        if notices:
            shutil.copytree(notices, resources / "PythonNotices", copy_function=shutil.copy2)
        metadata = {"CFBundleIdentifier": "jp.qupqubit.cavitylab", "CFBundleName": "CavityFEM",
                    "CFBundleDisplayName": "CavityFEM", "CFBundleExecutable": "CavityFEM",
                    "CFBundleIconFile": "CavityFEM",
                    "CFBundlePackageType": "APPL", "CFBundleShortVersionString": version,
                    "CFBundleVersion": version, "LSMinimumSystemVersion": "12.0",
                    "LSApplicationCategoryType": "public.app-category.utilities",
                    "CFBundleDevelopmentRegion": "ja", "NSPrincipalClass": "NSApplication",
                    "NSHighResolutionCapable": True,
                    "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True}}
        (contents / "Info.plist").write_bytes(plistlib.dumps(metadata, sort_keys=True))
        compile_launcher(swift, contents / "MacOS/CavityFEM", temporary)
        bundle_manifest = {"format": "cavity-lab-macos-bundle-v1", "app_version": version,
                           **source_inventory, "runtimes": manifest,
                           "icon": {"file": "CavityFEM.icns", "source": "macos/CavityFEM.icns", "sha256": icon_hash},
                           "runtime_archive_verification": "Pinned archive checksums supplied and verified by the build invoker before extraction.",
                           "fem_libraries_bundled": False, "signing": "ad_hoc_not_notarized",
                           "installation": "FEM dependencies are installed into Application Support on first launch."}
        (resources / "BUNDLE-MANIFEST.json").write_text(json.dumps(bundle_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        validate_bundle(bundle)
        signing = sign_bundle(bundle)
        # Signature output stays outside the signed bundle to preserve its seal.
        if archive:
            archive.parent.mkdir(parents=True, exist_ok=True)
            staged_archive = temporary / "app.zip"
            run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", bundle, staged_archive])
        bundle.rename(output)
        if archive:
            shutil.move(str(staged_archive), str(archive))
    result = {"path": str(output), "app_version": version,
              "source_sha256": source_inventory["source_sha256"], "signing": signing}
    if archive:
        result["archive"] = {"path": str(archive), "bytes": archive.stat().st_size,
                             "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-arm64", type=Path, required=True)
    parser.add_argument("--python-x86_64", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--runtime-notices", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.runtime_manifest.read_text(encoding="utf-8"))
    print(json.dumps(build_app(ROOT, args.python_arm64, args.python_x86_64, args.output,
                               manifest, args.runtime_notices, args.archive), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
