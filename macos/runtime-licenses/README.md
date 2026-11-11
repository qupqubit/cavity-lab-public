# Bundled Python runtime notices and source availability

This directory accompanies CPython 3.12.16 from Astral python-build-standalone release 20261110, for aarch64-apple-darwin and x86_64-apple-darwin. It must accompany redistributed runtime binaries. The app stores it in `Contents/Resources/PythonNotices/`.

Official binaries and build source:

- Release and runtime archives: https://github.com/astral-sh/python-build-standalone/releases/tag/20261110
- Build scripts and patches: https://github.com/astral-sh/python-build-standalone/tree/20261110
- CPython source: https://www.python.org/ftp/python/3.12.16/Python-3.12.16.tar.xz
- Exact component source URLs and SHA-256 hashes: `PROVENANCE.json` in this directory.

`LICENSE.*` files preserve the notices referenced by the exact full-archive `PYTHON.json` metadata for these targets. Corresponding copies were identical for both architectures. Additional CPython, OpenSSL, Tcl/Tk, Itcl, Thread and LibTomMath notices were copied verbatim from the exact upstream source archives recorded in `PROVENANCE.json`.

`pip/` preserves the pip 26.3 wheel's license directory and vendor version list. The runtime also retains its own original notice files. `certifi-source/` contains the actual readable certifi source and certificate bundle shipped in pip. That source is available under MPL-2.0, with its original notice, and may be modified and redistributed under that license. A full copy of MPL-2.0 is in `LICENSE.python-build-standalone.txt`. The exact certifi 2026.10.23 upstream source archive and SHA-256 are recorded in `PROVENANCE.json`; its source metadata is also available at https://pypi.org/pypi/certifi/2026.10.23/json . Recipients' rights in that source under MPL-2.0 are not restricted by the application's own license.

The PBS build system's MPL-2.0 does not replace the individual licenses of CPython or its linked libraries. No FEM package binaries or user CAD/results are included here. Some Python documentation notices describe optional historical components; the exact runtime dependency manifest determines which linked components are present.

The full source repository also contains `macos/RUNTIME_NOTICES.md` and `macos/runtime-manifest.json`, including acquisition verification, relocation checks and reproduction instructions. Preserve the original licenses inside the Python runtimes when packaging, signing or relocating them. No upstream source changes were made for this distribution.
