# Windows portable build and release qualification

Status on 2026-10-02: **Windows x64 development version 0.3.0.dev5 is built and
checked under a private WineHQ 11 runtime**. The native Windows PowerShell recipe
is also available. No native Windows installation has been qualified, and the
PowerShell recipe has not been run on this Linux host. Build and application
checks use synthetic temporary files; printer support, original-software parity
and production readiness remain open.

The [reviewed Windows development ZIP](../build/windows/run-20261002T160849Z-431026ba447a/BIFMA_TRADERS-0.3.0.dev5-windows-x64-DEVELOPMENT-WINE.zip)
is 15,777,127 bytes. Its SHA-256 is:

```text
98dc74675c145f7f01e95c1c69ef6a6a2fc6249d3045592cad80671334f8ef81
```

The [build receipt](windows_build_verification.json) records 41 source/recipe
inputs, 37 verification inputs, the official Python/Wine/toolchain hashes, and
the actual checks. The final run passed 50 host packaging tests, 491 Windows
application tests, 35 Windows packaging tests, and the expanded native GUI
workflow. Two POSIX-only application tests and two Linux-only packaging fixtures
are skipped on Windows; the host fixtures pass on Linux. Both frozen launchers,
data-free welcome, explicit history path, synthetic historical preview,
relocation, the full 17-step frozen native workflow and extracted-ZIP startup
checks passed. The frozen workflow verified 11 posted documents, nine restored,
revoked copied sessions before fresh login, preserved cash/contact bindings,
exact stock/ledger balances, full/filtered cash-book and city report output,
widget variable/trace cleanup under worker collection, and successful temporary-data
cleanup. Ignored finalizer errors fail the workflow. The manifest explicitly records
`built_under_wine: true`, `native_windows_qualified: false`, and
`production_ready: false`.
The separate [independent archive review](windows_archive_verification.json)
checks the published ZIP's checksum, CRCs, confined extraction, exact inventory,
AMD64 binaries and application-source equivalence with the Linux development
artifact; it does not claim a second runtime execution on native Windows.

The recipe creates a Windows x64 one-folder bundle with two programs:

| Program | Purpose |
| --- | --- |
| `BIFMA_TRADERS.exe` | Native workspace. With no arguments it opens the welcome screen; no database or account is created automatically. |
| `BIFMA_HISTORY.exe --database PATH` | Historical preview using an explicitly selected external recovered archive. It has a console for errors and smoke-test output. |

There is no bundled business database, original Oracle export, saved credential,
backup, recovery log or V2 prototype. The `BIFMA_TRADERS.py` source entry point
remains the native launcher. The packaged history wrapper requires `--database`
instead of relying on the source preview's development default path.

## Verified tooling and prerequisites

Use a Windows x64 desktop and a regular, supported 64-bit CPython installation
with `venv`, pip, SQLite and Tcl/Tk. CPython 3.13 or 3.14 is a suitable build
candidate; the actual chosen version still needs the gates below. The code's
minimum is Python 3.10, SQLite 3.37 with JSON support, and Tcl/Tk 8.6 for this
portable recipe. Free-threaded CPython builds and Windows ARM/32-bit targets are
outside this recipe's qualification.

Python's official Windows setup instructions describe the available installation
options. `python -m tkinter` opens a diagnostic window that also identifies the
installed Tcl/Tk version. Use a complete Python installation that passes that
check. [Python on Windows](https://docs.python.org/3/using/windows.html),
[Tkinter installation check](https://docs.python.org/3/library/tkinter.html).

`requirements-build.txt` pins **PyInstaller 6.22.3**, the stable version verified
in the official documentation on 2026-10-02. It is build tooling only; the normal
application remains standard-library-only. PyInstaller must run under Windows Python to produce Windows executables. The
PowerShell recipe refuses a Linux/macOS interpreter; the separate Wine fallback
also runs Windows CPython and retains the Windows platform/architecture guards.
[PyInstaller manual](https://pyinstaller.org/en/stable/index.html).

The one-folder layout keeps executables and their support files together.
PyInstaller discovers Python imports and collects the runtime; the spec supplies
no project data directories. Tcl/Tk assets are runtime dependencies collected by
the standard hooks. [How one-folder bundles work](https://pyinstaller.org/en/stable/operating-mode.html),
[PyInstaller spec files](https://pyinstaller.org/en/stable/spec-files.html).

Use a normal local checkout and a local NTFS build/output directory. Source
symlinks/junctions and linked artifact contents are rejected. PowerShell 5.1 or
newer is required. An interactive desktop is needed for the mandatory native GUI
smoke test. The build script does not change machine execution policies or
request administrator elevation; use an environment where the reviewed script
is permitted to run.

The checkout must contain `bifma/`, both current source launchers, `tests/`,
`tools/`, `packaging/`, `requirements-build.txt` and `docs/legacy_schema.json`.
The latter contains schema metadata used by synthetic historical-service tests.
**Do not copy private business files to a build machine.** The build does not
run `tools.test_inspect_legacy.SuppliedBackupTests`, which needs the original
private dump; actual Oracle/source reconciliation is a separate qualification.

## Build on Windows

From the project root in PowerShell, choose the installed Python interpreter and
confirm Tk works. Close the diagnostic window before continuing:

```powershell
$BuildPython = py -3.13 -c "import sys; print(sys.executable)"
& $BuildPython -m tkinter
.\packaging\build_windows.ps1 -Python $BuildPython
```

If that Python version is installed under a known path, it can be passed
directly instead:

```powershell
.\packaging\build_windows.ps1 -Python "C:\Python313\python.exe" -OutputDirectory "C:\BifmaBuilds"
```

Each invocation creates a new `run-<UTC timestamp>-<random ID>` directory. It does
not reuse or delete previous build directories. The script creates a fresh venv
there and invokes its interpreter by full path, so no environment activation is
needed. This follows Python's documented venv usage. [Python virtual environments](https://docs.python.org/3/library/venv.html).

The mandatory sequence is:

1. Install the exact build-tool pin into the new venv; reject the wrong platform,
   interpreter, SQLite/Tk capabilities or PyInstaller version.
2. Check the explicit application allowlist, compile source/spec syntax without
   running application code, and record source/recipe SHA-256 fingerprints.
3. Run every test in `tests/`, packaging boundary tests and
   `python -m tools.smoke_native_ui`. These use synthetic temporary databases.
   A failure or empty discovered test suite stops the build.
4. Recheck source fingerprints and stage only allowlisted Python files plus the
   reviewed build recipe. Source changes during testing require another run.
5. Build from that isolated staging directory using the spec, a separate cache
   and a new output directory. Inspect PyInstaller's analysis inputs and refuse
   any project data collection or `bifma` module from outside staging. Other
   inputs must be under the build/base Python installation, PyInstaller work
   directory or Windows runtime directory.
6. Run all seven shared frozen gates: both runtime probes, native welcome,
   mandatory history path, read-only synthetic historical screens, relocated
   welcome and the full fixed native workflow. The 17-step workflow exercises
   trades, returns/reversal, reports, permissions and cash-inclusive backup/restore
   in the relocated executable. Runtime versions, ordered steps, exact effects
   and cleanup must match the checked protocol; missing evidence stops publication.
7. Inspect bundle filenames, runtime ZIP members and database/export file
   signatures, require Windows executables and SQLite/Tk runtime libraries, then
   write the release manifest, ZIP and SHA-256 sidecar into a **new** directory.

`packaging/build_windows.ps1` is the supported build entry point. A direct spec
invocation against the project root fails because it lacks the checked staging
receipt. No switch skips the required build checks. A failed build leaves its
owned scratch directory/logs for diagnosis and produces no completed release
archive. The archive step removes its owned incomplete output on failure.

Only these final files should leave the build directory for release review:

```text
run-.../release/BIFMA_TRADERS-<version>-windows-x64.zip
run-.../release/BIFMA_TRADERS-<version>-windows-x64.zip.sha256
```

The ZIP contains:

```text
BIFMA_TRADERS/
  BIFMA_TRADERS.exe
  BIFMA_HISTORY.exe
  _internal/                 Python, SQLite, Tcl/Tk and required runtime files
  release-manifest.json      Source/runtime fingerprints and completed checks
```

Distribute the complete folder; the two EXEs depend on `_internal`. The release
manifest records the exact application version from `bifma/__init__.py`, source
and recipe hashes, installed build-package versions, per-file payload hashes,
source test results and frozen runtime versions. The manifest itself is covered
by the ZIP's SHA-256. Both Windows version resources
use that same source version. The numerical resource contains major/minor/patch
and development build number (zero for a non-development version); its textual
version retains `.devN`. It is not an automatic updater's ordering protocol.
[Windows version resources](https://pyinstaller.org/en/stable/usage.html#capturing-windows-version-data).

Only PyInstaller itself is pinned by the shared requirements file; resolved
transitive build packages are recorded in the manifest. The Wine fallback also
records the SHA-256 of every downloaded wheel and installs them offline. This is an auditable recipe, not a claim of hermetic or
bit-for-bit reproducible builds. Dependency-wheel hashes and a qualified locked
toolchain can be added for a signed production release. ZIP hashing identifies
the reviewed artifact; it does not authenticate a publisher. Code signing and
required runtime license notices remain release-review items.

## Check and run the portable artifact

Compare the ZIP's SHA-256 with its sidecar before extracting:

```powershell
Get-FileHash -Algorithm SHA256 .\BIFMA_TRADERS-0.3.0.dev5-windows-x64-DEVELOPMENT-WINE.zip
Get-Content .\BIFMA_TRADERS-0.3.0.dev5-windows-x64-DEVELOPMENT-WINE.zip.sha256
```

These commands use the current reviewed Wine-built artifact. A new native
PowerShell build uses its own generated ZIP name without the Wine suffix.

Extract to a new ordinary folder, keep `_internal`, and run:

```powershell
Expand-Archive -LiteralPath .\BIFMA_TRADERS-0.3.0.dev5-windows-x64-DEVELOPMENT-WINE.zip -DestinationPath .\BIFMA-0.3.0.dev5
Set-Location .\BIFMA-0.3.0.dev5
.\BIFMA_TRADERS\BIFMA_TRADERS.exe
.\BIFMA_TRADERS\BIFMA_HISTORY.exe --check-runtime
.\BIFMA_TRADERS\BIFMA_HISTORY.exe --database "D:\Bifma Data\History\legacy_recovered.sqlite3"
```

The first command opens native welcome/setup. Select **Create new database**,
choose a new file outside the application folder, create the initial administrator
and complete company/account/unit/store/item setup. The native database starts
empty, with no imported historical accounts or passwords. Follow
[native workspace notes](NATIVE_WORKSPACE.md) for operational setup.

Use **Open database** to reopen a current native file. Store operational files
and backups separately from application binaries, on a filesystem qualified for
SQLite and the application's atomic hard-link publication. Do not use the
historical archive as a native database. Historical preview cannot initialize or
upgrade it. Moving a folder of application binaries does not migrate data.

The historical program opens no default archive: invoking it without
`--database` exits with an argument error. An authorized historical smoke run is
explicit as well:

```powershell
.\BIFMA_TRADERS\BIFMA_HISTORY.exe --database "D:\Bifma Data\History\legacy_recovered.sqlite3" --smoke-test
```

The fixed native workflow accepts only a new evidence filename:

```powershell
.\BIFMA_TRADERS\BIFMA_TRADERS.exe --check-workflow --output .\workflow-result.json
```

It creates and removes its own synthetic records and accepts no database,
credential or script input. A graphical desktop is required.

No historical archive is needed for the packaging build or native GUI smoke.
Testing real historical parity is separate and must preserve the reviewed archive.

## Upgrade and recovery drill

Use the existing native program's administrator **Create backup** action before
an upgrade. Preserve the old program and its backup separately. Test the newer
program against a disposable copy first, using welcome's **Upgrade an existing
native database** action when required. Explicit native migration records its
own pre-upgrade backup; opening files never silently upgrades them.

Use **Restore backup to a new database** for current-version restore. It requires
an active administrator's credentials from the backup, publishes a new file and
revokes all restored sessions. Sign in again. For an older native backup, preserve
the original, copy it to a separate file, explicitly upgrade that copy, then
restore the upgraded copy. Never replace an active file to simulate this workflow.
See [backup/restore contract](BACKUP_RESTORE.md) and
[native migration design](NATIVE_DESIGN.md).

## Target-machine release gates

Every generated manifest intentionally says `production_ready: false` and
`target_acceptance: pending`. Passing build gates makes an artifact reviewable;
the following evidence still needs collection on the actual Windows target.

| Gate | Required evidence |
| --- | --- |
| Clean machine | Extract/run under an ordinary user on each intended Windows version with no Python, PyInstaller or developer tools installed; record runtime probe and executable versions. |
| UI and paths | Native welcome, first administrator, login/logout, every supported entry/report, dialogs, keyboard navigation, scrolling, close-during-work behavior and permission errors; test spaces/Urdu in paths and 100/125/150/200% scaling on supported display sizes. |
| Exact transactions | Approved examples for every implemented kind, discounts/rounding, free goods, stock availability, company isolation, duplicate documents, linked returns, reversals and immutable posted snapshots. |
| Urdu and mixed text | Actual Urdu names, English codes and exact numbers in forms, tables, saved snapshots and printable output; verify shaping/direction with fonts actually installed on target machines. |
| Printing | Save a posted document with **Save printable copy**, open the HTML in the intended browser, and print/PDF on each actual printer and paper size. Verify multi-page rows/headings, Urdu, totals and margins against approved originals. No direct printer-driver integration or legacy layout parity is implied. |
| Historical preview | Use a separately supplied, qualified archive; visit company-scoped screens and reports, compare accepted historical results and verify no database/WAL changes. |
| Upgrade | Test a known previous native schema on a disposable copy; verify version/checksum history, pre-upgrade backup, interruption recovery and rejection of newer/unrecognized databases. |
| Backup/restore | Admin-only backup, failed/wrong credentials, current and older copied-backup recovery, all-session revocation, no-overwrite races, exact restored balances/stock, WAL capture and file/directory access controls. |
| Filesystem/failure | Qualify destination hard-link support and Windows ACLs; test locked files, denied writes, out-of-space errors, application interruption, antivirus interaction and concurrent application processes. Shared/network-folder deployments need their own qualification. |
| Release approval | Record application/source/artifact hashes, machine/OS/browser/printer versions, test evidence, known limitations, runtime notices and publisher-signing decision; obtain original-workflow and business acceptance. |

Original source forms/report assets and accepted business results remain necessary
for complete parity. A portable build does not resolve missing operational
features, verified historical promotion or accounting/workflow acceptance.

## Safe checks available without a Windows build

These checks require no PyInstaller installation and read no business files:

```sh
python3 -B tools/check_release.py
python3 -B -m unittest discover -s packaging -p 'test_release.py' -v
```

The tests cover allowlist changes, renamed/private payloads, nested runtime ZIPs,
symlinks, traversal paths, stale source receipts, missing dependencies, failed
check gates, explicit history paths and checksum/manifest generation with dummy
test files. The PowerShell script remains unexecuted on this Linux host. The Wine-specific
record separately identifies Windows-Python source tests and frozen checks; it
does not claim a native Windows execution environment.


## Isolated Wine development fallback

`packaging/build_windows_wine.py` is an exploratory Linux x86_64 orchestrator for
an already verified private prefix. It does not use the default Wine prefix,
install system packages or alter the user's home directory. The normal Windows
PowerShell recipe above remains available. PyInstaller describes Windows builds
under Wine as a possible route with limited documentation; this is why every
result explicitly records `built_under_wine: true` and
`native_windows_qualified: false`.
[PyInstaller multiple-platform guidance](https://pyinstaller.org/en/stable/usage.html#supporting-multiple-operating-systems).

The current reviewed probe uses privately extracted WineHQ 11.0 x64 and the official **full**, regular CPython
3.13.16 amd64 ZIP. This is the Python install-manager runtime, not the embedded
ZIP that omits Tk and pip. Its published release-manifest SHA-256 is
`bbf675bb5e763c1efbb09a3a461b259d81598a63c30c4b0d7ea11b9f063df159`.
The probe verified 64-bit Windows Python, Tk 8.6.15, SQLite 3.50.4 STRICT/JSON,
writable-handle `fsync`, hard links, file identities, `shutil.copy2`/`CopyFile2`,
and creation of a real Windows virtual environment with pip. Read-only-handle `fsync`
failed as expected under Windows; the application's publication helper now uses
its owned writable staging handle.
[Official Python release manifest](https://www.python.org/ftp/python/3.13.16/windows-3.13.16.json),
[Python Windows distributions](https://docs.python.org/3.13/using/windows.html).

A prefix is not a security sandbox. Before the official runtime probe, its
host filesystem drive mapping and host shell-folder symlinks were removed;
local private directories replaced the shell folders. Every invocation supplies
that explicit prefix, disables menu creation and optional runtime installers,
and uses private cache/temp directories. The wrapper refuses other filesystem
drive mappings, linked drive-map/user-directory roots, or any user-folder links,
and rechecks every extracted Python
file against the checksum-verified official ZIP. It never changes `HOME`.

WineHQ's Jammy `wine-stable` and `wine-stable-amd64` packages are version
`11.0.0.0~jammy-1`. Their SHA-256 values were checked against the repository's
signed `InRelease` and `Packages.bz2`, with signing-key fingerprint
`D43F640145369C51D786DDEA76F1A20FF987672F`. The runtime is extracted with
`dpkg-deb -x`; no system package or maintainer script is installed. The wrapper
pins both package hashes and the complete 1,525-entry extracted inventory. It
requires an explicit ordinary runtime directory under `/tmp`, uses its matching
`bin/wine`, `bin/wineserver` and `lib/wine`, and rejects changed bytes or external
links before execution. These two packages and existing host libraries suffice
for the checked x64 probes; no 32-bit runtime qualification is claimed.
[Official WineHQ signed repository](https://dl.winehq.org/wine-builds/ubuntu/dists/jammy/InRelease),
[Official WineHQ package index](https://dl.winehq.org/wine-builds/ubuntu/dists/jammy/main/binary-amd64/Packages.bz2).

The recorded prefix is deliberately required rather than auto-detecting a user
installation. With that probe directory available, the checked command is:

```sh
env DISPLAY=:92 python3 -B packaging/build_windows_wine.py \
  --probe-root /tmp/bifma-windows-probe-0_wsnaxk \
  --wine-runtime-root /tmp/bifma-wine11-runtime-hyywc99u
```

The display and verified probe path are specific to this build host; a new host
needs its own explicit prefix preparation and compatibility probe. The wrapper
creates a fresh run/venv each time. Only manifest code plus the explicit
verification list in `wine_release.py` enters the verification checkout. That
list includes synthetic test modules, their recovery-tool dependencies, native
GUI smoke code and schema metadata. The actual PyInstaller staging folder then
contains only application/build allowlisted files. No business files are copied.

All packaging tests run on the Linux host. The complete application suite,
Windows packaging tests, Wine-wrapper tests and native GUI workflow smoke run
under the Windows interpreter. Linux-only ELF/mode/symlink packaging fixtures
remain host checks. Existing test skips are recorded; the recipe supplies no
switch to ignore failures. Source and verification hashes are checked before
and after testing. Pinned PyInstaller and dependency wheels come only from the
official PyPI index, are individually hashed, and are installed offline into the
new Windows venv.

Both launchers receive runtime probes. Frozen checks exercise the data-free
native welcome, mandatory explicit historical archive path, every preview screen
against an empty synthetic archive, and relocation to a path containing spaces.
The seventh gate runs the complete fixed native workflow from that relocated
bundle; see [the workflow protocol](../packaging/DIAGNOSTICS.md).
The windowed native EXE supports exclusive `--output FILE` JSON diagnostics so
checks do not depend on an attached console. Every EXE/DLL/PYD is checked for an
AMD64 PE32+ header as well as the existing filename/payload boundaries. The ZIP
is extracted into a new directory, audited again, and its native welcome/history
runtime are tested again before publication to `build/windows/run-...`.

The archive name includes `DEVELOPMENT-WINE`. Its manifest records source/test
hashes, official interpreter/wheel provenance, checks, runtime versions and the
explicit Wine qualification limits. Adjacent receipts also record the checks
performed after ZIP extraction. Source GUI and the 17-step frozen native
workflow are separate required checks. Passing that fixed synthetic workflow
does not prove every business scenario. Native Windows fonts, printer drivers,
filesystem/ACL behavior, desktop scaling and real workflow acceptance still need
the target-machine gates above.


### Earlier Wine 6 build blocker and Wine 11 retry

The 2026-10-02 attempt passed all 38 then-current host packaging tests and
downloaded official PyInstaller wheels. It created no Windows application
artifact. Redirecting Wine-hosted CPython directly to a Linux regular log file
first caused `init_sys_streams: WinError 6`; pipe capture resolved that handle
issue. The retry reached virtual-environment setup and then aborted with:

```text
unimplemented function KERNEL32.dll.CopyFile2
```

The checksum-verified CPython 3.13.16 standard library invokes that Windows API
while preparing its venv. The installed Wine 6.0.3 implementation cannot execute
it. Basic Python/Tk/SQLite/fsync/hard-link probes had passed, but they did not
exercise this copy operation. Neither Python nor the application was patched,
and no failing assertion was disabled. Windows application tests, PyInstaller
build and frozen Windows checks therefore did not run. The private prefix has
no remaining processes; its diagnostic files remain separate from all business
data. See the [attempt receipt](windows_build_attempt.json) for the exact runtime
and log hashes. The subsequent private WineHQ 11 probe implements the previously
missing operation and passes real venv creation. A complete checked Wine 11
build subsequently produced the development artifact linked above. Wine success
still does not establish native Windows qualification.

The recipe now captures Wine output via pipes and explicitly downloads the
pinned PyInstaller wheel's Windows-only `pefile` and `pywin32-ctypes` dependencies:
host pip's `--platform` option selects wheel tags but does not change all host
marker evaluation. Each resolved wheel remains hashed and installed offline.
These recipe corrections have synthetic regression coverage. The original
installed Wine 6 runtime and its failed attempt are unchanged; the retry uses
the separately checksum-verified official Wine 11 files.

The [earlier Wine 11 gate receipts](windows_wine11_attempts.json) preserve four
failed runs before the successful build. They exposed a private-TMPDIR test
fixture assumption, unclosed raw SQLite fixture handles, Wine's read-only reset
behavior for `Path.chmod`, and a literal path-separator expectation. Corrections
made fixture cleanup explicit and used native path/permission APIs; application
assertions, source isolation, publication guards and required check gates were
preserved. No Python/Wine runtime patch or failing assertion bypass was used.
Python documents the differing Windows defaults for
[os.chmod](https://docs.python.org/3.13/library/os.html#os.chmod) and
[Path.chmod](https://docs.python.org/3.13/library/pathlib.html#pathlib.Path.chmod).
