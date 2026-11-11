"""Structural distribution tests using tiny fake runtimes; no downloads."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import stat
import struct
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_macos_app", ROOT / "scripts/build_macos_app.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def thin_binary(architecture):
    cpu = {"arm64": 0x0100000C, "x86_64": 0x01000007}[architecture]
    return struct.pack("<8I", 0xFEEDFACF, cpu, 0, 2, 0, 0, 0, 0)


def universal_binary():
    return struct.pack(">12I", 0xCAFEBABE, 2, 0x0100000C, 0, 48, 32, 0,
                       0x01000007, 0, 80, 32, 0) + thin_binary("arm64") + thin_binary("x86_64")


def icon_container():
    # Structural fixture only; the release icon is rendered independently.
    record = b"ic07" + struct.pack(">I", 12) + b"icon"
    return b"icns" + struct.pack(">I", 8 + len(record)) + record


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source with spaces"
    for relative in builder.REQUIRED_FILES | {"LICENSES/NOTICE.txt", "macos/CavityLab.swift"}:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative + "\n", encoding="utf-8")
    (root / "cavity_fem/__init__.py").write_text('__version__ = "0.1.7"\n', encoding="utf-8")
    (root / "macos/CavityFEM.icns").write_bytes(icon_container())
    return root


@pytest.fixture
def runtimes(tmp_path):
    result = {}
    for architecture in builder.ARCHITECTURES:
        root = tmp_path / architecture / "python"
        executable = root / "bin/python3.12"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(thin_binary(architecture))
        executable.chmod(0o755)
        (root / "bin/python3").symlink_to("python3.12")
        (root / "lib/python3.12/site-packages").mkdir(parents=True)
        (root / "lib/python3.12/os.py").write_text("# stdlib placeholder\n", encoding="utf-8")
        (root / "LICENSE").write_text("Fixture license notice\n", encoding="utf-8")
        result[architecture] = root
    return result


@pytest.fixture
def provenance():
    return {"provider": "Astral python-build-standalone", "python_version": "3.12.13",
            "runtimes": {architecture: {"source_url": "https://example.invalid/" + architecture + ".tar.gz",
                                         "archive_sha256": str(index) * 64}
                         for index, architecture in enumerate(builder.ARCHITECTURES, 1)}}


@pytest.fixture
def native_tools(monkeypatch):
    monkeypatch.setattr(builder, "sys", SimpleNamespace(platform="darwin"))
    def compile_launcher(source, destination, temporary):
        destination.write_bytes(universal_binary())
        destination.chmod(0o755)
    monkeypatch.setattr(builder, "compile_launcher", compile_launcher)
    monkeypatch.setattr(builder, "sign_bundle", lambda bundle: {"kind": "ad_hoc", "notarized": False})
    calls = []
    def run(command):
        calls.append(command)
        assert command[:5] == ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent"]
        Path(command[-1]).write_bytes(b"fake ditto archive")
        return SimpleNamespace(stdout="")
    monkeypatch.setattr(builder, "run", run)
    return calls


def test_bundle_layout_digest_permissions_and_archive(source, runtimes, provenance, native_tools, tmp_path):
    output = tmp_path / "公開 outputs/CavityFEM.app"
    archive = tmp_path / "公開 outputs/CavityFEM-macOS.zip"
    report = builder.build_app(source, runtimes["arm64"], runtimes["x86_64"], output, provenance, archive=archive)
    resources = output / "Contents/Resources"
    metadata = plistlib.loads((output / "Contents/Info.plist").read_bytes())
    assert metadata["CFBundleIdentifier"] == "jp.qupqubit.cavitylab"
    assert metadata["CFBundleDisplayName"] == metadata["CFBundleName"] == "CavityFEM"
    assert metadata["CFBundleExecutable"] == metadata["CFBundleIconFile"] == "CavityFEM"
    assert metadata["LSMinimumSystemVersion"] == "12.0"
    assert metadata["CFBundleShortVersionString"] == "0.1.7"
    assert metadata["NSAppTransportSecurity"] == {"NSAllowsLocalNetworking": True}
    assert builder.macho_architectures(output / "Contents/MacOS/CavityFEM") == set(builder.ARCHITECTURES)
    assert (resources / "CavityFEM.icns").read_bytes() == icon_container()
    assert (resources / "app/macos/icon-NOTICE.md").is_file()
    for architecture in builder.ARCHITECTURES:
        copied = resources / "python" / architecture
        assert (copied / "bin/python3").is_symlink()
        assert os.readlink(copied / "bin/python3") == "python3.12"
        assert stat.S_IMODE((copied / "bin/python3.12").stat().st_mode) == 0o755
        assert (copied / "LICENSE").read_text() == "Fixture license notice\n"
    manifest = json.loads((resources / "BUNDLE-MANIFEST.json").read_text())
    assert manifest["runtimes"] == provenance
    assert manifest["fem_libraries_bundled"] is False
    assert manifest["signing"] == "ad_hoc_not_notarized"
    assert manifest["icon"] == {"file": "CavityFEM.icns", "source": "macos/CavityFEM.icns",
                                "sha256": hashlib.sha256(icon_container()).hexdigest()}
    entries = [(p.relative_to(resources / "app").as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
               for p in sorted((resources / "app").rglob("*")) if p.is_file()]
    digest = hashlib.sha256(json.dumps(entries, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert manifest["source_sha256"] == report["source_sha256"] == digest
    assert str(tmp_path) not in json.dumps(manifest)
    assert report["archive"]["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert len(native_tools) == 1
    assert not list(output.parent.glob(".cavity-lab-build-*"))


def test_runtime_source_allowlist_excludes_data_tests_native_files_and_extra_scripts(source):
    forbidden = ["data/private.step", "tests/private.py", ".venv/bin/python", "macos/private.txt",
                 "examples/user.step", "scripts/private-helper.py", "static/password.txt",
                 "cavity_fem/native.so", "cavity_fem/__pycache__/api.pyc", "LICENSES/library.dylib"]
    for relative in forbidden:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("must not ship", encoding="utf-8")
    included = {relative for _, relative in builder.runtime_source_files(source)}
    assert not included.intersection(forbidden)
    assert builder.REQUIRED_FILES <= included


@pytest.mark.parametrize("relative", ["cavity_fem/linked.py", "static/linked.js", "scripts/macos_launcher.py"])
def test_source_symlinks_are_rejected(source, relative, tmp_path):
    external = tmp_path / "private"
    external.write_text("private", encoding="utf-8")
    link = source / relative
    if link.exists(): link.unlink()
    link.symlink_to(external)
    with pytest.raises(ValueError, match="symlink"):
        builder.runtime_source_files(source)


@pytest.mark.parametrize("kind", ["external", "absolute_internal", "broken", "cycle"])
def test_nonrelocatable_runtime_links_are_rejected(runtimes, tmp_path, kind):
    root = runtimes["arm64"]
    target = root / "bad-link"
    if kind == "external":
        external = root.parent / "outside"
        external.write_text("private", encoding="utf-8")
        target.symlink_to("../outside")
    elif kind == "absolute_internal":
        target.symlink_to(root / "LICENSE")
    elif kind == "broken":
        target.symlink_to("does-not-exist")
    else:
        target.symlink_to("other-link")
        (root / "other-link").symlink_to("bad-link")
    with pytest.raises(ValueError, match="symlink"):
        builder.validate_runtime(root, "arm64")


def test_runtime_architecture_and_execute_permission_are_checked(runtimes):
    with pytest.raises(ValueError, match="architecture"):
        builder.validate_runtime(runtimes["arm64"], "x86_64")
    (runtimes["arm64"] / "bin/python3.12").chmod(0o644)
    with pytest.raises(ValueError, match="executable"):
        builder.validate_runtime(runtimes["arm64"], "arm64")


@pytest.mark.parametrize("package", ["ngsolve", "numpy", "private_code.py"])
def test_fem_or_user_packages_cannot_be_bundled(runtimes, package):
    path = runtimes["arm64"] / "lib/python3.12/site-packages" / package
    path.write_text("not a pristine runtime", encoding="utf-8")
    with pytest.raises(ValueError, match="non-bootstrap package"):
        builder.validate_runtime(runtimes["arm64"], "arm64")


def test_user_venv_cannot_be_used_as_base_runtime(runtimes):
    (runtimes["arm64"] / "pyvenv.cfg").write_text("home = external", encoding="utf-8")
    with pytest.raises(ValueError, match="virtual environment"):
        builder.validate_runtime(runtimes["arm64"], "arm64")


def test_manifest_requires_both_checksums_and_no_local_paths(provenance):
    provenance["runtimes"]["arm64"]["archive_sha256"] = "wrong"
    with pytest.raises(ValueError, match="archive_sha256"):
        builder.validate_runtime_manifest(provenance)
    provenance["runtimes"]["arm64"]["archive_sha256"] = "1" * 64
    provenance["local_archive"] = "/private/build/python.tar.gz"
    with pytest.raises(ValueError, match="absolute paths"):
        builder.validate_runtime_manifest(provenance)


def test_runtime_digest_matches_controllers_lowercase_convention(provenance):
    provenance["runtimes"]["arm64"]["archive_sha256"] = "A" * 64
    with pytest.raises(ValueError, match="archive_sha256"):
        builder.validate_runtime_manifest(provenance)


def test_version_reader_does_not_execute_package(source):
    (source / "cavity_fem/__init__.py").write_text('raise RuntimeError("do not import")\n__version__="0.1.7"\n', encoding="utf-8")
    assert builder.app_version(source) == "0.1.7"
    (source / "cavity_fem/__init__.py").write_text('__version__=str(1)\n', encoding="utf-8")
    with pytest.raises(ValueError):
        builder.app_version(source)


def test_existing_output_is_never_replaced(source, runtimes, provenance, native_tools, tmp_path):
    output = tmp_path / "CavityFEM.app"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="non-existing"):
        builder.build_app(source, runtimes["arm64"], runtimes["x86_64"], output, provenance)
    assert sentinel.read_text() == "keep"


def test_build_failure_never_publishes_partial_bundle(source, runtimes, provenance, native_tools, tmp_path, monkeypatch):
    def failed_compile(*args):
        raise RuntimeError("compiler failure")
    monkeypatch.setattr(builder, "compile_launcher", failed_compile)
    output = tmp_path / "CavityFEM.app"
    with pytest.raises(RuntimeError, match="compiler failure"):
        builder.build_app(source, runtimes["arm64"], runtimes["x86_64"], output, provenance)
    assert not output.exists()
    assert not list(tmp_path.glob(".cavity-lab-build-*"))


def test_output_cannot_recurse_into_input_notices(source, runtimes, provenance, native_tools, tmp_path):
    notices = tmp_path / "notices"
    notices.mkdir()
    (notices / "LICENSE").write_text("notice", encoding="utf-8")
    with pytest.raises(ValueError, match="outside all input"):
        builder.build_app(source, runtimes["arm64"], runtimes["x86_64"], notices / "nested.app",
                          provenance, runtime_notices=notices)
    output = tmp_path / "CavityFEM.app"
    with pytest.raises(ValueError, match="Archive must be outside"):
        builder.build_app(source, runtimes["arm64"], runtimes["x86_64"], output,
                          provenance, archive=output / "recursive.zip")


def test_compile_targets_and_frameworks(monkeypatch, tmp_path):
    commands = []
    def run(command):
        commands.append(command)
        if "--show-sdk-path" in command:
            return SimpleNamespace(stdout="/sdk/path\n")
        if "lipo" in command:
            Path(command[-1]).write_bytes(universal_binary())
        return SimpleNamespace(stdout="")
    monkeypatch.setattr(builder, "run", run)
    output = tmp_path / "CavityFEM"
    builder.compile_launcher(tmp_path / "CavityLab.swift", output, tmp_path)
    swift_calls = [command for command in commands if "swiftc" in command]
    assert len(swift_calls) == 2
    assert {command[3] for command in swift_calls} == {"arm64-apple-macosx12.0", "x86_64-apple-macosx12.0"}
    assert all("AppKit" in command and "WebKit" in command for command in swift_calls)
    assert all(command[command.index("-module-cache-path") + 1] == tmp_path / "ModuleCache" for command in swift_calls)
    assert (tmp_path / "ModuleCache").is_dir()
    assert stat.S_IMODE(output.stat().st_mode) == 0o755


def test_runtime_notices_can_be_supplied_separately(source, runtimes, provenance, native_tools, tmp_path):
    for runtime in runtimes.values():
        (runtime / "LICENSE").unlink()
    with pytest.raises(ValueError, match="license notices"):
        builder.build_app(source, runtimes["arm64"], runtimes["x86_64"], tmp_path / "missing.app", provenance)
    notices = tmp_path / "upstream-notices"
    notices.mkdir()
    (notices / "CPython-LICENSE.txt").write_text("supplied upstream notice", encoding="utf-8")
    output = tmp_path / "notices.app"
    builder.build_app(source, runtimes["arm64"], runtimes["x86_64"], output, provenance, runtime_notices=notices)
    assert (output / "Contents/Resources/PythonNotices/CPython-LICENSE.txt").is_file()


def test_missing_icon_prevents_bundle_publication(source, runtimes, provenance, native_tools, tmp_path):
    (source / "macos/CavityFEM.icns").unlink()
    output = tmp_path / "CavityFEM.app"
    with pytest.raises(ValueError, match="CavityFEM.icns"):
        builder.build_app(source, runtimes["arm64"], runtimes["x86_64"], output, provenance)
    assert not output.exists()


@pytest.mark.parametrize("content", [b"not an icon", b"icns" + struct.pack(">I", 100),
                                    b"icns" + struct.pack(">I", 16) + b"ic07" + struct.pack(">I", 20)])
def test_invalid_icon_container_is_rejected(tmp_path, content):
    icon = tmp_path / "CavityFEM.icns"
    icon.write_bytes(content)
    with pytest.raises(ValueError, match="CavityFEM.icns"):
        builder.validate_icon(icon)


def test_icon_symlink_is_rejected(tmp_path):
    original = tmp_path / "original.icns"
    original.write_bytes(icon_container())
    link = tmp_path / "CavityFEM.icns"
    link.symlink_to(original.name)
    with pytest.raises(ValueError, match="regular"):
        builder.validate_icon(link)


def test_changed_bundled_icon_fails_provenance_check(source, runtimes, provenance, native_tools, tmp_path):
    output = tmp_path / "CavityFEM.app"
    builder.build_app(source, runtimes["arm64"], runtimes["x86_64"], output, provenance)
    (output / "Contents/Resources/CavityFEM.icns").write_bytes(icon_container()[:-1] + b"X")
    with pytest.raises(ValueError, match="provenance"):
        builder.validate_bundle(output)


def test_source_plist_template_keeps_identity_and_uses_new_name():
    metadata = plistlib.loads((ROOT / "macos/Info.plist").read_bytes())
    assert metadata["CFBundleIdentifier"] == "jp.qupqubit.cavitylab"
    assert all(metadata[key] == "CavityFEM" for key in
               ("CFBundleName", "CFBundleDisplayName", "CFBundleExecutable", "CFBundleIconFile"))
