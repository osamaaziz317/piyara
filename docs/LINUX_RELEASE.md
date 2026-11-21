# Linux development portable build

Linux and Windows are both required target platforms. This recipe builds a
**Linux x86_64 development artifact**, version `0.3.0.dev6`; it is not production
qualified. The independent [Windows recipe](WINDOWS_RELEASE.md) remains in place.
Running a Linux build does not produce or validate a Windows executable.

The current [development archive](../build/linux/run-20261002T133804Z-6b84588ef23c/attempt-20261002T163713Z-2e180b80be6a/release/BIFMA_TRADERS-0.3.0.dev6-linux-x86_64-DEVELOPMENT.tar.gz)
passed 495 application tests, 50 packaging tests, the source native GUI smoke and
seven frozen checks. An independent extraction also passed checksum, source-receipt,
bundle, runtime, welcome and full frozen-workflow checks. The [verification receipt](linux_build_verification.json)
records the archive path/hash and scope. This artifact is 13,957,349 bytes; its
SHA-256 is `27516ca140d1cff5f9b384a32f2cb988a4d9e061db6163d3d1d6169ade592e56`.

## Recorded development build

An actual build completed on 2026-10-02: [Linux x86_64 development archive](../build/linux/run-20261002T133804Z-6b84588ef23c/attempt-20261002T163713Z-2e180b80be6a/release/BIFMA_TRADERS-0.3.0.dev6-linux-x86_64-DEVELOPMENT.tar.gz)
(13,957,349 bytes), with [SHA-256 checksum](../build/linux/run-20261002T133804Z-6b84588ef23c/attempt-20261002T163713Z-2e180b80be6a/release/BIFMA_TRADERS-0.3.0.dev6-linux-x86_64-DEVELOPMENT.tar.gz.sha256)
and [build receipt](../build/linux/run-20261002T133804Z-6b84588ef23c/attempt-20261002T163713Z-2e180b80be6a/release/release-manifest.json).

SHA-256: `27516ca140d1cff5f9b384a32f2cb988a4d9e061db6163d3d1d6169ade592e56`.
Its gates passed 495 application tests, 50 packaging tests covering Windows,
Linux and the Wine build wrapper, the source native GUI smoke and all seven frozen checks described
below. The receipt covers 42 source/recipe inputs and 38 verification inputs.
It records 371 bundled file/link entries and inspection of 55 ELF binaries, with
an observed GLIBC symbol requirement of 2.35. These results establish this host's
development build, not production or cross-distribution acceptance.

All four new real-Tk lifecycle tests executed without skips: delayed/superseded
stock reads, responsive close and direct destroy, queued cancellation, variable/
trace/root release, and startup/error cleanup with an unrelated root. Synthetic
archive bytes remain unchanged. Both source GUI and the complete fixed frozen
workflow pass in this build. The
independent extracted-archive check also repeats the frozen workflow: 11 posted
documents, nine restored, unchanged saved cash bindings/contacts, balanced
journals, revoked copied sessions and successful temporary-data cleanup.
Earlier dev3/dev4 artifacts and their original manifests remain available in
their respective `dist/development-0.3.0.dev3/` and `dist/development-0.3.0.dev4/`
folders. Dev5 is also preserved at `dist/development-0.3.0.dev5/`; current
application code is dev6.

## Build from source

Use native Linux x86_64, regular CPython 3.10 or newer with Tk 8.6 or newer and
SQLite 3.37 or newer (STRICT tables and JSON support), a working graphical display,
`readelf`, and at least 2 GiB free disk space. `requirements-build.txt` pins
PyInstaller `6.22.3`. Build dependencies are confined to a new virtualenv; the
application runtime still uses Python's standard library only.

```sh
python3 -B packaging/build_linux.py
```

The optional `--output-directory PATH` selects the parent of a unique build run;
the default is `build/linux`. Each run has its own virtualenv, wheelhouse, pip
cache, PyInstaller cache and temporary directory. Downloading uses the official
PyPI index, ignores user pip configuration, and accepts wheels only. Installation
uses that local wheelhouse without an index. Wheel hashes, installed package
versions and the exact PyInstaller pin are recorded. No global package install
or business-data migration occurs.

For coordinated development, `--prepare-only` prepares the toolchain without
staging source. Later use `--prepared-run PATH` to start a **new checked build
attempt** within that run. Tests always rerun; an old pass receipt cannot bypass
the checks. The prepared wheel and requirement hashes must still match.

The required gates are the full synthetic `tests/` suite, **both** platform
packaging test suites, and `tools.smoke_native_ui`. Source and test input hashes
are compared before/after verification. Required verification inputs include
the public recovered schema inventory, not the original private export. A failed
check or changed input aborts the attempt. Private logs remain in the build run;
they are never copied into the portable artifact.

Only `source_manifest.json` application/build files enter a new staging tree.
PyInstaller runs with isolated Python import settings and that staging directory
as its working directory. Its collection is checked against approved code and
runtime roots. The original export, databases, credentials, backups, recovery
logs, V2 prototype and test tools cannot enter the artifact. Renamed SQLite/export
payloads and disallowed names are also rejected during artifact inspection.

## Artifact and launch

A successful attempt creates:

```text
build/linux/run-.../attempt-.../release/
  BIFMA_TRADERS-0.3.0.dev6-linux-x86_64-DEVELOPMENT.tar.gz
  BIFMA_TRADERS-0.3.0.dev6-linux-x86_64-DEVELOPMENT.tar.gz.sha256
  release-manifest.json
```

The archive contains a single `BIFMA_TRADERS` directory with two ELF launchers,
the shared `_internal` runtime, `DEVELOPMENT.txt` and its release manifest. It
contains no database. Output directories/files must be new; existing releases
are not overwritten. Tar preserves executable permissions and reviewed relative
runtime symlinks, without recording the build account's UID/name. Absolute,
escaping, broken, cyclic and directory symlinks are refused; Windows retains its
separate rule refusing artifact symlinks entirely.

Verify and extract into a new directory on a filesystem that supports symbolic
links and executable files:

```sh
sha256sum -c BIFMA_TRADERS-0.3.0.dev6-linux-x86_64-DEVELOPMENT.tar.gz.sha256
tar -xzf BIFMA_TRADERS-0.3.0.dev6-linux-x86_64-DEVELOPMENT.tar.gz
cd BIFMA_TRADERS
./BIFMA_TRADERS
```

The native launcher opens the welcome screen, where a new empty native SQLite
database can be created explicitly. Store databases and backups outside the
portable folder. Replacing an application folder is not a database upgrade.
Use the explicit upgrade workflow after a verified backup; restore creates a new
database and requires a backed-up administrator's credentials. Test those paths
on disposable copies before qualifying a workstation.

Historical preview requires an external, explicitly selected recovered archive:

```sh
./BIFMA_HISTORY --database /path/to/recovered-history.sqlite3
```

Without `--database`, it refuses to open an archive. Runtime diagnostics are
data-free: `./BIFMA_HISTORY --check-runtime`, `./BIFMA_TRADERS --check-runtime`
and `./BIFMA_TRADERS --smoke-welcome`. The fixed operational diagnostic is
`./BIFMA_TRADERS --check-workflow --output NEW.json`; it creates and removes its
own synthetic data and accepts no database or credentials. See its
[protocol](../packaging/DIAGNOSTICS.md). A graphical display is required.

## Provenance and limits

`release-manifest.json` records source/recipe SHA-256 values, verification input
hashes, passing test counts/log hashes, installed build packages, downloaded wheel
hashes, interpreter/Tk/SQLite versions, distribution/glibc baseline, required GLIBC
symbol version found through `readelf`, frozen checks and every bundled file/link.
The archive SHA-256 file covers the complete archive including that manifest.
This is an auditable build receipt, not a claim of byte-for-byte reproducibility.

The current build host is Ubuntu 22.04.5 x86_64 with glibc 2.35, CPython 3.10.12,
Tk 8.6 and SQLite 3.37.2. The actual artifact receipt records precise values.
Other distributions are unqualified until tested. The GLIBC symbol requirement
is an observed binary requirement; it alone does not prove compatibility with a
distribution, display system, fonts, GPU drivers or printers. Musl-based systems
such as Alpine and ARM systems are outside this x86_64/glibc recipe.

The frozen checks run both entrypoints' Tk/SQLite STRICT/JSON probes, render and
close the native welcome without opening data, verify the history launcher's
explicit-path requirement, and visit all historical screens using two empty
synthetic companies. The synthetic archive's hash must remain unchanged. A copied
bundle in a different directory with spaces must also render its native welcome;
checks run from an empty working directory. The seventh gate runs all 17 fixed
`native-workflow-v2` steps from that relocated frozen bundle, including widget
variable cleanup under worker collection, cash trades, linked returns/reversal,
cash book/city balances and their full/filtered printable totals, permissions and
cash-inclusive backup/restore. It rejects ignored finalizer errors and validates
exact results and temporary-data cleanup. These checks do not
establish original Forms/report-template parity or printer acceptance.

Before production acceptance, qualify the actual supported Linux distributions
and Windows machines, a clean installation with no Python, English/Urdu fonts and
text entry, target print/PDF output, file permissions, backup/restore and explicit
upgrade drills, power/crash recovery and concurrent use. Original forms, report
assets and unresolved workflow decisions remain separate acceptance dependencies.

## Primary tooling references

PyInstaller does not bundle glibc; build on the oldest Linux baseline intended
for support. See its [GNU/Linux compatibility guidance](https://pyinstaller.org/en/stable/usage.html#making-gnu-linux-apps-forward-compatible).
PyInstaller 6 uses runtime symlinks on POSIX, so copying and archiving must preserve
them; see [symbolic-link requirements](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#requirements-imposed-by-symbolic-links-in-frozen-application).
The pinned tool comes from [PyInstaller 6.22.3 on PyPI](https://pypi.org/project/pyinstaller/6.22.3/),
and the isolated interpreter follows Python's [venv documentation](https://docs.python.org/3/library/venv.html).
