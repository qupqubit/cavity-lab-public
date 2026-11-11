# Third-party notices

The [MIT license](LICENSE) covers the original application source, documentation,
and newly generated generic sample CAD and its generator in this repository.
It does not relicense third-party dependencies or their notices. Package names
and trademarks identify their owners and do not imply endorsement.

This source distribution does **not** bundle Python, dependency implementations,
NGSolve/Netgen/OCCT native libraries, or downloaded wheels. The launchers install
separate PyPI distributions into a local environment. Those distributions retain
their own license terms; installing them does not change those terms.

The inventory below was checked against installed distribution metadata on
**2026-11-11**, using the tested **macOS arm64 / Python 3.9** runtime environment.
That environment contained **20 runtime distributions**, plus pip 22.0.4 and
setuptools 60.10.0 as environment tooling (22 distributions total). A separate
development environment supplied the **9 optional development/test** entries
also present in `requirements-tested.txt`. Thus 29 dependency distributions are
documented below; the normal launcher does not install all 29.

The files linked below preserve the upstream license and attribution text from
those distributions, without changing their terms. The machine-readable
[inventory](LICENSES/dependency-inventory.json) records versions, package URLs,
original relative metadata paths, and SHA-256 hashes. This is an environment
snapshot, not a claim that all OS/Python combinations resolve to an identical set.

## Native FEM, geometry, and numerical libraries

- **NGSolve and Netgen** are provided under LGPL 2.1 terms. Upstream source and
  license files: [NGSolve v6.2.2506](https://github.com/NGSolve/ngsolve/tree/v6.2.2506)
  and [Netgen v6.2.2506](https://github.com/NGSolve/netgen/tree/v6.2.2506).
- **This application makes use of facilities provided by Open CASCADE Technology**
  through Netgen's OCCT geometry support. OCCT 7.8.1 uses LGPL 2.1 with the
  [Open CASCADE exception](LICENSES/OCCT_LGPL_EXCEPTION.txt).
  [Upstream source](https://github.com/Open-Cascade-SAS/OCCT/tree/V7_8_1) and
  [upstream exception](https://github.com/Open-Cascade-SAS/OCCT/blob/V7_8_1/OCCT_LGPL_EXCEPTION.txt)
  are available independently. The exception supplements the LGPL; it does not
  replace that license.
- **NumPy and SciPy** have BSD licenses for their own code. Their inspected wheel
  notices additionally describe bundled components, including OpenBLAS, LAPACK,
  GCC runtime libraries with the GCC Runtime Library Exception, and libquadmath.
  The complete wheel notices below are retained; these packages should not be
  described as containing only BSD-licensed code. Binary contents and notices
  can vary by platform and wheel.
- The **python-multipart** wheel supplies an Apache 2.0 notice. A copy of the
  [full Apache 2.0 text](LICENSES/Apache-2.0.txt) is also included; see the
  [Apache Software Foundation's canonical license](https://www.apache.org/licenses/LICENSE-2.0).

## Runtime dependency snapshot

| Package | Inspected version | License summary | Preserved upstream notices |
| --- | --- | --- | --- |
| [annotated-doc](https://pypi.org/project/annotated-doc/0.0.5/) | 0.0.5 | MIT | [annotated-doc-0.0.5-LICENSE](LICENSES/annotated-doc-0.0.5-LICENSE) |
| [annotated-types](https://pypi.org/project/annotated-types/0.7.0/) | 0.7.0 | MIT | [annotated-types-0.7.0-LICENSE](LICENSES/annotated-types-0.7.0-LICENSE) |
| [anyio](https://pypi.org/project/anyio/4.12.1/) | 4.12.1 | MIT | [anyio-4.12.1-LICENSE](LICENSES/anyio-4.12.1-LICENSE) |
| [click](https://pypi.org/project/click/8.1.8/) | 8.1.8 | BSD-3-Clause | [click-8.1.8-LICENSE.txt](LICENSES/click-8.1.8-LICENSE.txt) |
| [exceptiongroup](https://pypi.org/project/exceptiongroup/1.3.1/) | 1.3.1 | MIT and PSF-2.0 | [exceptiongroup-1.3.1-LICENSE](LICENSES/exceptiongroup-1.3.1-LICENSE) |
| [fastapi](https://pypi.org/project/fastapi/0.128.8/) | 0.128.8 | MIT | [fastapi-0.128.8-LICENSE](LICENSES/fastapi-0.128.8-LICENSE) |
| [h11](https://pypi.org/project/h11/0.16.0/) | 0.16.0 | MIT | [h11-0.16.0-LICENSE.txt](LICENSES/h11-0.16.0-LICENSE.txt) |
| [idna](https://pypi.org/project/idna/3.20/) | 3.20 | BSD-3-Clause | [idna-3.20-LICENSE.md](LICENSES/idna-3.20-LICENSE.md) |
| [netgen-mesher](https://pypi.org/project/netgen-mesher/6.2.2506/) | 6.2.2506 | LGPL 2.1 | [netgen-mesher-6.2.2506-AUTHORS](LICENSES/netgen-mesher-6.2.2506-AUTHORS), [netgen-mesher-6.2.2506-LICENSE](LICENSES/netgen-mesher-6.2.2506-LICENSE) |
| [netgen_occt](https://pypi.org/project/netgen_occt/7.8.1/) | 7.8.1 | LGPL 2.1 with Open CASCADE exception 1.0 | [netgen-occt-7.8.1-LICENSE_LGPL_21.txt](LICENSES/netgen-occt-7.8.1-LICENSE_LGPL_21.txt) |
| [ngsolve](https://pypi.org/project/ngsolve/6.2.2506/) | 6.2.2506 | LGPL 2.1 | [ngsolve-6.2.2506-LICENSE](LICENSES/ngsolve-6.2.2506-LICENSE) |
| [numpy](https://pypi.org/project/numpy/2.0.2/) | 2.0.2 | BSD-3-Clause plus bundled-component notices | [numpy-2.0.2-LICENSE.txt](LICENSES/numpy-2.0.2-LICENSE.txt) |
| [pydantic](https://pypi.org/project/pydantic/2.13.5/) | 2.13.5 | MIT | [pydantic-2.13.5-LICENSE](LICENSES/pydantic-2.13.5-LICENSE) |
| [pydantic_core](https://pypi.org/project/pydantic_core/2.46.5/) | 2.46.5 | MIT | [pydantic-core-2.46.5-LICENSE](LICENSES/pydantic-core-2.46.5-LICENSE) |
| [python-multipart](https://pypi.org/project/python-multipart/0.0.20/) | 0.0.20 | Apache-2.0 | [python-multipart-0.0.20-LICENSE.txt](LICENSES/python-multipart-0.0.20-LICENSE.txt) |
| [scipy](https://pypi.org/project/scipy/1.13.1/) | 1.13.1 | BSD-3-Clause plus bundled-component notices | [scipy-1.13.1-LICENSE.txt](LICENSES/scipy-1.13.1-LICENSE.txt) |
| [starlette](https://pypi.org/project/starlette/0.49.3/) | 0.49.3 | BSD-3-Clause | [starlette-0.49.3-LICENSE.md](LICENSES/starlette-0.49.3-LICENSE.md) |
| [typing_extensions](https://pypi.org/project/typing_extensions/4.16.0/) | 4.16.0 | PSF-2.0 and historical Python notices | [typing-extensions-4.16.0-LICENSE](LICENSES/typing-extensions-4.16.0-LICENSE) |
| [typing-inspection](https://pypi.org/project/typing-inspection/0.4.2/) | 0.4.2 | MIT | [typing-inspection-0.4.2-LICENSE](LICENSES/typing-inspection-0.4.2-LICENSE) |
| [uvicorn](https://pypi.org/project/uvicorn/0.39.0/) | 0.39.0 | BSD-3-Clause | [uvicorn-0.39.0-LICENSE.md](LICENSES/uvicorn-0.39.0-LICENSE.md) |

## Optional development and test dependencies

| Package | Inspected version | License summary | Preserved upstream notices |
| --- | --- | --- | --- |
| [certifi](https://pypi.org/project/certifi/2026.10.23/) | 2026.10.23 | MPL-2.0 | [certifi-2026.10.23-LICENSE](LICENSES/certifi-2026.10.23-LICENSE) |
| [httpcore](https://pypi.org/project/httpcore/1.0.9/) | 1.0.9 | BSD-3-Clause | [httpcore-1.0.9-LICENSE.md](LICENSES/httpcore-1.0.9-LICENSE.md) |
| [httpx](https://pypi.org/project/httpx/0.28.1/) | 0.28.1 | BSD-3-Clause | [httpx-0.28.1-LICENSE.md](LICENSES/httpx-0.28.1-LICENSE.md) |
| [iniconfig](https://pypi.org/project/iniconfig/2.1.0/) | 2.1.0 | MIT | [iniconfig-2.1.0-LICENSE](LICENSES/iniconfig-2.1.0-LICENSE) |
| [packaging](https://pypi.org/project/packaging/26.3/) | 26.3 | Apache-2.0 OR BSD-2-Clause | [packaging-26.3-LICENSE](LICENSES/packaging-26.3-LICENSE), [packaging-26.3-LICENSE.APACHE](LICENSES/packaging-26.3-LICENSE.APACHE), [packaging-26.3-LICENSE.BSD](LICENSES/packaging-26.3-LICENSE.BSD) |
| [pluggy](https://pypi.org/project/pluggy/1.6.0/) | 1.6.0 | MIT | [pluggy-1.6.0-LICENSE](LICENSES/pluggy-1.6.0-LICENSE) |
| [Pygments](https://pypi.org/project/Pygments/2.21.0/) | 2.21.0 | BSD-2-Clause | [pygments-2.21.0-AUTHORS](LICENSES/pygments-2.21.0-AUTHORS), [pygments-2.21.0-LICENSE](LICENSES/pygments-2.21.0-LICENSE) |
| [pytest](https://pypi.org/project/pytest/8.4.2/) | 8.4.2 | MIT | [pytest-8.4.2-AUTHORS](LICENSES/pytest-8.4.2-AUTHORS), [pytest-8.4.2-LICENSE](LICENSES/pytest-8.4.2-LICENSE) |
| [tomli](https://pypi.org/project/tomli/2.5.0/) | 2.5.0 | MIT | [tomli-2.5.0-LICENSE](LICENSES/tomli-2.5.0-LICENSE) |

## Platform and installation tooling

On Windows, Click conditionally requires **Colorama** (BSD-3-Clause). It was not
installed in the audited macOS runtime and is not counted among its 20 runtime
distributions. The [Colorama 0.4.6 upstream notice](LICENSES/colorama-0.4.6-LICENSE.txt)
is included as a reference, with [upstream source](https://github.com/tartley/colorama/tree/0.4.6).
The actual resolved version and its installed notice should be checked when
building a Windows distribution. `exceptiongroup` is conditional on the Python
version; other dependency markers can likewise change the installed set.

Python and installation tools are separately obtained, not redistributed here.
Their terms remain applicable: [Python license](https://docs.python.org/3/license.html),
[pip license](https://github.com/pypa/pip/blob/22.0.4/LICENSE.txt), and
[setuptools license](https://github.com/pypa/setuptools/blob/v60.10.0/LICENSE).
A launcher may install a newer tool version; consult its installed metadata.

## If you change the distribution

Keep the original application copyright and MIT notice when redistributing its
source. Preserve applicable upstream notices when copying third-party code or
binaries. A future release that bundles environments, native libraries, modified
dependencies, or compiled application binaries requires a fresh review of that
actual release, including LGPL source, modification, and relinking requirements
where applicable. The upstream source links here are informational; they are
not a substitute for any required corresponding-source delivery or offer.

This notice records inspected provenance and license texts. It does not expand
upstream permissions or guarantee clearance of every possible patent, trademark,
or third-party claim. Imported user CAD remains subject to its owner's rights.
