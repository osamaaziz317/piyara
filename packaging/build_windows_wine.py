#!/usr/bin/env python3
"""Checked DEVELOPMENT Windows build under an already verified private Wine prefix.

The existing user's Wine prefix is never selected. The Linux host downloads only
Windows wheels from official PyPI; Windows Python installs those wheels offline.
This does not qualify the artifact on a native Windows installation.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import shutil
import stat
import subprocess
import sys
import zipfile
from uuid import uuid4

import release_support as shared
import wine_release as wine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# pip --platform selects wheel tags but still evaluates dependency markers on
# the Linux host. These are the Windows-only requirements in the pinned
# PyInstaller 6.22.3 wheel's METADATA; their resolved bytes are hashed below.
WINDOWS_WHEEL_REQUIREMENTS = ('pefile>=2022.5.30', 'pywin32-ctypes>=0.2.1')
# WineHQ Jammy packages verified against the signed repository InRelease.
# The extracted inventory was captured directly after checksum-verified dpkg-deb
# extraction; pinning it also checks every loader, server and DLL before use.
WINE_PACKAGES = {
    'wine-stable_11.0.0.0~jammy-1_amd64.deb': '2866ffa79a28cbe64128f18a9172f8ba6dd7c2ff0c9491a92a8e6816faff8f1f',
    'wine-stable-amd64_11.0.0.0~jammy-1_amd64.deb': '39d85b8f51728e44b5186a43126498149a885d49008ec31ac41f4548be8773bf',
}
WINE_INVENTORY_SHA256 = '6e331e2e38bfab3238c3d5e23c90381a7a3837c8019a976331552ab460fa999b'
WINE_SIGNING_FINGERPRINT = 'D43F640145369C51D786DDEA76F1A20FF987672F'


def name(prefix):
    return prefix + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:12]


def windows_path(path, prefix):
    path = Path(path).absolute()
    if path.resolve() != path or not path.is_relative_to(prefix / 'drive_c'):
        raise shared.ReleaseError('Wine paths must remain in the private ordinary C: tree.')
    return 'C:\\' + '\\'.join(path.relative_to(prefix / 'drive_c').parts)


def validate_wine_runtime(root):
    """Use only the reviewed, privately extracted official WineHQ 11 runtime."""
    if root.is_symlink() or root.resolve() != root.absolute() or root.parent != Path('/tmp'):
        raise shared.ReleaseError('Wine runtime must be an ordinary private directory directly under /tmp.')
    for filename, expected in WINE_PACKAGES.items():
        if shared.digest(shared._source_path(root, 'downloads/' + filename)) != expected:
            raise shared.ReleaseError('Official WineHQ package checksum differs.')
    extracted = root / 'extracted'
    if extracted.is_symlink() or extracted.resolve() != extracted or not extracted.is_dir():
        raise shared.ReleaseError('Wine extraction root cannot be linked or missing.')
    rows = []
    for directory, folders, files in os.walk(extracted, followlinks=False):
        for filename in folders + files:
            path = Path(directory) / filename
            relative = path.relative_to(extracted).as_posix()
            if path.is_symlink():
                target = os.readlink(path)
                if Path(target).is_absolute() or not path.resolve().is_relative_to(extracted):
                    raise shared.ReleaseError('Wine runtime link leaves its private extraction.')
                rows.append({'path': relative, 'symlink': target})
            elif stat.S_ISREG(path.stat().st_mode):
                rows.append({'path': relative, 'sha256': shared.digest(path), 'bytes': path.stat().st_size})
            elif not path.is_dir():
                raise shared.ReleaseError('Unexpected Wine runtime entry.')
    rows.sort(key=lambda row: row['path'])
    checksum = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if checksum != WINE_INVENTORY_SHA256:
        raise shared.ReleaseError('Extracted official Wine runtime inventory differs.')
    base = extracted / 'opt/wine-stable'
    return {'root': str(root), 'loader': str(base / 'bin/wine'), 'server': str(base / 'bin/wineserver'),
            'dll_path': str(base / 'lib/wine'), 'provenance': {
                'version': '11.0.0.0~jammy-1', 'source': 'https://dl.winehq.org/wine-builds/ubuntu/',
                'signing_key_fingerprint': WINE_SIGNING_FINGERPRINT,
                'packages': [{'filename': filename, 'sha256': checksum} for filename, checksum in sorted(WINE_PACKAGES.items())],
                'extracted_inventory_sha256': checksum, 'extracted_entries': len(rows),
                'global_install': False, 'native_windows_qualified': False}}


def validate_probe(root):
    if root.is_symlink() or root.resolve() != root.absolute() or root.parent != Path('/tmp'):
        raise shared.ReleaseError('Use the verified ordinary private probe directory directly under /tmp.')
    receipt = shared._json(root / 'compatibility-result.json')
    official = receipt['official_python']
    if official['source_url'] != 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.zip' or official['sha256'] != 'bbf675bb5e763c1efbb09a3a461b259d81598a63c30c4b0d7ea11b9f063df159':
        raise shared.ReleaseError('This recipe requires the reviewed official CPython 3.13.16 x64 runtime.')
    if shared.digest(root / 'downloads/python-3.13.16-amd64.zip') != official['sha256']:
        raise shared.ReleaseError('Official interpreter archive integrity changed.')
    prefix = root / 'prefix'
    if prefix.is_symlink() or prefix.resolve() != prefix or (prefix / 'dosdevices/c:').resolve() != prefix / 'drive_c':
        raise shared.ReleaseError('Unsafe private Wine prefix.')
    for directory in (prefix / 'dosdevices', prefix / 'drive_c/users'):
        if directory.is_symlink() or directory.resolve() != directory or not directory.is_dir():
            raise shared.ReleaseError('Wine drive-map and user-directory roots must be ordinary directories.')
    for entry in (prefix / 'dosdevices').iterdir():
        if entry.name.endswith(':') and entry.name != 'c:':
            raise shared.ReleaseError('Remove host filesystem drive mappings from this private prefix first.')
    for directory, folders, files in os.walk(prefix / 'drive_c/users', followlinks=False):
        for entry in folders + files:
            path = Path(directory) / entry
            if path.is_symlink():
                # Do not accept an internal directory link that hides another
                # external link from this non-following traversal.
                raise shared.ReleaseError('User folders in this private prefix must contain no links.')
    if any(not receipt['probe'][key]['ok'] for key in ('sqlite', 'tk', 'fsync_rplusb', 'hardlinks', 'copy2')) or not receipt.get('venv_probe', {}).get('venv'):
        raise shared.ReleaseError('The required private-prefix compatibility probes did not pass.')
    # The archive checksum does not alone establish that its extracted files
    # remain unchanged after the earlier probe.
    with zipfile.ZipFile(root / 'downloads/python-3.13.16-amd64.zip') as archive:
        runtime_root = prefix / 'drive_c/Python313'
        if runtime_root.resolve() != runtime_root or runtime_root.is_symlink():
            raise shared.ReleaseError('The official runtime root cannot be linked.')
        for entry in archive.infolist():
            if not entry.is_dir():
                path = shared._source_path(runtime_root, entry.filename)
                if path.read_bytes() != archive.read(entry):
                    raise shared.ReleaseError('An extracted official interpreter file changed.')
    return prefix, receipt


def environment(probe, run, runtime):
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'LD_LIBRARY_PATH', 'LD_LIBRARY_PATH_ORIG'):
        env.pop(key, None)
    prefix = probe / 'prefix'
    temporary = run / 'temporary'
    temporary.mkdir(exist_ok=True)
    env.update(WINEPREFIX=str(prefix), WINEARCH='win64', WINEDEBUG='-all',
               WINELOADER=runtime['loader'], WINESERVER=runtime['server'], WINEDLLPATH=runtime['dll_path'],
               WINEDLLOVERRIDES='winemenubuilder.exe,mscoree,mshtml=d',
               XDG_CONFIG_HOME=str(probe / 'xdg/config'), XDG_CACHE_HOME=str(probe / 'xdg/cache'),
               XDG_DATA_HOME=str(probe / 'xdg/data'), TMPDIR=str(probe / 'tmp'),
               TEMP=windows_path(temporary, prefix), TMP=windows_path(temporary, prefix),
               PYINSTALLER_CONFIG_DIR=windows_path(run / 'pyinstaller-cache', prefix),
               PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    return env


def invoke_windows(probe, run, executable, arguments, log, *, runtime, cwd=None, timeout=900):
    # Wine 6 cannot initialize CPython's streams from this Linux regular-file
    # handle. A pipe translates correctly; save its exact output after capture.
    import time
    started = time.monotonic()
    with log.open('xb') as stream:
        try:
            result = subprocess.run(
                [runtime['loader'], windows_path(executable, probe / 'prefix'), *arguments],
                cwd=cwd or run, env=environment(probe, run, runtime), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            stream.write(error.stdout or b'')
            raise
        stream.write(result.stdout)
    if result.returncode:
        raise shared.ReleaseError('Required Windows command failed; see ' + str(log))
    return result.stdout.decode('utf-8', errors='replace'), round(time.monotonic() - started, 3)


def host_build(probe, output, runtime):
    if sys.platform != 'linux' or platform.machine().lower() != 'x86_64':
        raise shared.ReleaseError('This fallback orchestrator runs on Linux x86_64 only.')
    os.umask(0o077)
    prefix, compatibility = validate_probe(probe)
    build_root = prefix / 'drive_c/bifma-builds'
    build_root.mkdir(exist_ok=True)
    run = build_root / name('run-')
    run.mkdir(mode=0o700)
    print(json.dumps({'wine_build_run': str(run)}), flush=True)
    expected = wine.snapshot_checkout(ROOT, run / 'checkout')
    expected.update(built_under_wine=True, native_windows_qualified=False,
                    build_provenance={'wine_version': compatibility['wine_version'],
                                      'official_wine': runtime['provenance'],
                                      'official_python': compatibility['official_python'],
                                      'host_platform': platform.platform()})
    shared._write_new_json(run / 'input-receipt.json', expected)
    host_env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'PIP_INDEX_URL', 'PIP_EXTRA_INDEX_URL'):
        host_env.pop(key, None)
    host_env['TMPDIR'] = str(probe / 'tmp')
    # All platform-independent and Linux-specific packaging guards run on Linux.
    text, seconds = wine.logged([sys.executable, '-B', '-E', '-s', '-m', 'unittest', 'discover',
                                  '-s', 'packaging', '-p', 'test_*.py', '-v'],
                                 run / 'host-packaging-tests.log', cwd=run / 'checkout', environment=host_env)
    if 'Ran ' not in text:
        raise shared.ReleaseError('Host packaging tests discovered no tests.')
    wine.logged([sys.executable, '-B', '-E', '-s', '-m', 'venv', str(run / 'host-venv')],
                run / 'host-venv.log', cwd=run, environment=host_env)
    wheels = run / 'wheels'
    wheels.mkdir()
    wine.logged([str(run / 'host-venv/bin/python'), '-B', '-E', '-s', '-m', 'pip', '--isolated',
                 '--cache-dir', str(run / 'host-pip-cache'), 'download', '--disable-pip-version-check',
                 '--no-input', '--only-binary=:all:', '--platform', 'win_amd64', '--python-version', '313',
                 '--implementation', 'cp', '--abi', 'cp313', '--index-url', 'https://pypi.org/simple',
                 '--dest', str(wheels), '-r', str(run / 'checkout/requirements-build.txt'),
                 *WINDOWS_WHEEL_REQUIREMENTS],
                run / 'toolchain-download.log', cwd=run, environment=host_env)
    toolchain = [{'filename': p.name, 'sha256': shared.digest(p), 'bytes': p.stat().st_size} for p in sorted(wheels.iterdir())]
    expected['toolchain_wheels'] = toolchain
    expected['build_provenance']['host_packaging_check'] = {
        'passed': True, 'seconds': seconds, 'log_sha256': shared.digest(run / 'host-packaging-tests.log')}
    shared._write_new_json(run / 'prepared-receipt.json', expected)
    interpreter = prefix / 'drive_c/Python313/python.exe'
    invoke_windows(probe, run, interpreter, ['-B', '-I', '-m', 'venv', windows_path(run / 'venv', prefix)], run / 'windows-venv.log', runtime=runtime)
    python = run / 'venv/Scripts/python.exe'
    invoke_windows(probe, run, python, ['-B', '-E', '-s', '-m', 'pip', '--isolated', '--cache-dir',
                   windows_path(run / 'pip-cache', prefix), 'install', '--disable-pip-version-check', '--no-input',
                   '--require-virtualenv', '--no-index', '--find-links', windows_path(wheels, prefix),
                   '-r', windows_path(run / 'checkout/requirements-build.txt', prefix)], run / 'toolchain-install.log', runtime=runtime)
    invoke_windows(probe, run, python, ['-B', '-E', '-s', windows_path(run / 'checkout/packaging/build_windows_wine.py', prefix),
                   '--worker', windows_path(run, prefix)], run / 'windows-worker.log', runtime=runtime, cwd=run / 'checkout', timeout=1500)
    result = shared._json(run / 'result.json')
    # Host checks the final bytes independently of Wine's path/parser behavior.
    archive = run / 'release' / PureWindowsPath(result['archive']).name
    if shared.digest(archive) != result['sha256']:
        raise shared.ReleaseError('Final Windows artifact checksum differs.')
    extracted = wine.extract_review_archive(archive, run / 'host-extracted-review')
    wine.pe_inventory(extracted)
    if expected['source'] != shared.source_inventory(ROOT) or expected['verification_inputs'] != wine.verification_inventory(ROOT):
        raise shared.ReleaseError('Workspace changed during build; review a fresh checked attempt.')
    if validate_wine_runtime(Path(runtime['root'])) != runtime:
        raise shared.ReleaseError('Wine runtime changed during build.')
    output.mkdir(parents=True, exist_ok=True)
    destination = output / run.name
    destination.mkdir(mode=0o700)
    try:
        for path in (run / 'release').iterdir():
            if not path.is_file() or path.is_symlink():
                raise shared.ReleaseError('Unexpected release output.')
            with (destination / path.name).open('xb') as stream:
                stream.write(path.read_bytes())
        for filename in ('preflight.json', 'frozen-runtime.json', 'extracted-checks.json'):
            with (destination / filename).open('xb') as stream:
                stream.write((run / filename).read_bytes())
        with (destination / 'release-manifest.json').open('xb') as stream:
            stream.write((extracted / 'release-manifest.json').read_bytes())
    except BaseException:
        shutil.rmtree(destination)
        raise
    result.update(archive=str(destination / archive.name), built_under_wine=True, native_windows_qualified=False)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


def frozen_checks(bundle, run):
    import frozen_checks as gates
    def execute(name, arguments, cwd, expected):
        output, _ = wine.logged(arguments, run / ("frozen-" + name + ".log"),
                                cwd=cwd, timeout=180, expected=expected)
        return output
    return gates.run_checks(bundle, run, ROOT / "docs/legacy_schema.json",
                            execute=execute, audit_bundle=wine.pe_inventory, windows=True)


def worker(run):
    if sys.platform != 'win32' or sys.prefix == sys.base_prefix or Path(sys.prefix).resolve() != (run / 'venv').resolve():
        raise shared.ReleaseError('Windows worker must run inside this build\'s Windows virtualenv.')
    receipt = shared._json(run / 'prepared-receipt.json')
    wine.verify_checkout(ROOT, receipt)
    for wheel in receipt['toolchain_wheels']:
        if Path(wheel['filename']).name != wheel['filename'] or shared.digest(run / 'wheels' / wheel['filename']) != wheel['sha256']:
            raise shared.ReleaseError('Offline toolchain wheel integrity differs.')
    receipt['runtime'] = shared.runtime_report(windows=True, toolchain=True)
    if receipt['runtime']['pyinstaller'] != receipt['source']['pyinstaller_required']:
        raise shared.ReleaseError('PyInstaller differs from its exact reviewed pin.')
    receipt['checks'] = wine.run_checks(ROOT, run)
    wine.verify_checkout(ROOT, receipt)
    shared._write_new_json(run / 'preflight.json', receipt)
    staged = shared.stage_source(ROOT, run / 'source', receipt)
    wine.logged([sys.executable, '-B', '-I', '-m', 'PyInstaller', '--clean', '--distpath', str(run / 'dist'),
                 '--workpath', str(run / 'pyinstaller-work'), str(staged / 'packaging/BIFMA_TRADERS.spec')],
                run / 'pyinstaller.log', cwd=staged)
    bundle = run / 'dist/BIFMA_TRADERS'
    wine.pe_inventory(bundle)
    frozen = frozen_checks(bundle, run)
    shared._write_new_json(run / 'frozen-runtime.json', frozen)
    result = wine.finish_release(bundle, staged, run / 'release', run / 'frozen-runtime.json')
    extracted = wine.extract_review_archive(Path(result['archive']), run / 'extracted ZIP with spaces')
    output_file = run / 'extracted-welcome.json'
    wine.logged([str(extracted / 'BIFMA_TRADERS.exe'), '--smoke-welcome', '--output', str(output_file)],
                run / 'extracted-welcome.log', cwd=run / 'empty-working-directory', timeout=120)
    if shared._json(output_file) != {'native_welcome': 'passed', 'database_opened': False}:
        raise shared.ReleaseError('Extracted ZIP native welcome failed.')
    runtime_text, _ = wine.logged([str(extracted / 'BIFMA_HISTORY.exe'), '--check-runtime'],
                                 run / 'extracted-runtime.log', cwd=run / 'empty-working-directory', timeout=120)
    if json.loads(runtime_text) != frozen['history_runtime']:
        raise shared.ReleaseError('Extracted ZIP history runtime differs.')
    shared._write_new_json(run / 'extracted-checks.json', {'archive_sha256': result['sha256'],
        'native_welcome': 'passed', 'history_runtime': 'passed', 'built_under_wine': True,
        'native_windows_qualified': False})
    shared._write_new_json(run / 'result.json', result)
    print(json.dumps(result), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe-root', type=Path)
    parser.add_argument('--wine-runtime-root', type=Path)
    parser.add_argument('--output-directory', type=Path, default=ROOT / 'build/windows')
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker)
    if args.probe_root is None or args.wine_runtime_root is None:
        parser.error('--probe-root and --wine-runtime-root are required; no default Wine installation or prefix is selected')
    probe = args.probe_root.absolute()
    prefix, _ = validate_probe(probe)
    runtime = validate_wine_runtime(args.wine_runtime_root.absolute())
    try:
        return host_build(probe, args.output_directory.absolute(), runtime)
    finally:
        # This prefix is dedicated to the checked build; never select the user's
        # default server or kill Wine processes by executable name.
        cleanup = subprocess.run([runtime['server'], '-k'],
                                 env=dict(os.environ, WINEPREFIX=str(prefix), WINEARCH='win64'),
                                 capture_output=True, timeout=30)
        if cleanup.returncode not in {0, 1}:  # 1 also means no matching server.
            print('Private Wine server cleanup failed; inspect the owned prefix.', file=sys.stderr)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print('Wine development build failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
