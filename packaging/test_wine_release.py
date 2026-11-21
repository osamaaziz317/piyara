"""Synthetic Wine-wrapper boundaries; these tests do not invoke Wine/network."""

from contextlib import redirect_stdout
import io
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import subprocess
import unittest
from unittest.mock import patch
import zipfile

import build_windows_wine as builder
import native_entry
import release_support as shared
import frozen_checks as gates
import wine_release as wine

ROOT = Path(__file__).resolve().parents[1]


def pe(machine=0x8664, optional=0x20b):
    header = bytearray(64 + 26)
    header[:2] = b'MZ'
    struct.pack_into('<I', header, 0x3c, 64)
    header[64:68] = b'PE\0\0'
    struct.pack_into('<H', header, 68, machine)
    struct.pack_into('<H', header, 88, optional)
    return bytes(header)


def workflow_fixture(version):
    return {"schema_version": 1, "scenario": "native-workflow-v2", "application_version": version,
            "frozen": True, "passed": True, "temporary_data_removed": True,
            "steps": [{"step": step, "passed": True} for step in gates.WORKFLOW_STEPS],
            "effects": dict(gates.WORKFLOW_EFFECTS)}

class WinePackagingTests(unittest.TestCase):
    def setUp(self):
        # The Linux prefix/runtime guards deliberately require direct /tmp
        # children, even when the build's normal TMPDIR is a deeper private path.
        self.directory = tempfile.TemporaryDirectory(dir='/tmp' if os.name == 'posix' else None)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def checkout(self):
        source = self.root / 'source'
        source.mkdir()
        definition = shared.manifest(ROOT)
        names = set(wine.VERIFICATION_FILES)
        for field in ('application_files', 'build_files', 'review_files'):
            names.update(definition[field])
        for name in names:
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        return source

    def bundle(self):
        root = self.root / 'bundle'
        (root / '_internal').mkdir(parents=True)
        for name in ('BIFMA_TRADERS.exe', 'BIFMA_HISTORY.exe', '_internal/_tkinter.pyd',
                     '_internal/_sqlite3.pyd', '_internal/sqlite3.dll', '_internal/tk86t.dll', '_internal/tcl86t.dll'):
            (root / name).write_bytes(pe())
        return root

    def test_verification_copy_is_closed_and_excludes_private_payloads(self):
        source = self.checkout()
        (source / 'BT-270926.EXE').write_bytes(b'private synthetic dump')
        (source / 'private.sqlite3').write_bytes(b'SQLite format 3\0synthetic')
        (source / 'data').mkdir()
        (source / 'data/credentials.json').write_text('{}')
        receipt = wine.snapshot_checkout(source, self.root / 'copied')
        wine.verify_checkout(self.root / 'copied', receipt)
        actual = {p.relative_to(self.root / 'copied').as_posix() for p in (self.root / 'copied').rglob('*') if p.is_file()}
        self.assertNotIn('BT-270926.EXE', actual)
        self.assertNotIn('private.sqlite3', actual)
        self.assertNotIn('data/credentials.json', actual)
        self.assertIn('tools/recovery_oracle.py', actual)
        self.assertIn('docs/legacy_schema.json', actual)
        self.assertNotIn('tools/test_inspect_legacy.py', actual)

    def test_new_test_module_and_changed_receipt_are_refused(self):
        source = self.checkout()
        receipt = wine.snapshot_checkout(source, self.root / 'copied')
        (source / 'tests/unreviewed.py').write_text('pass\n')
        with self.assertRaisesRegex(shared.ReleaseError, 'test module set'):
            wine.verification_inventory(source)
        (self.root / 'copied/tools/recovery_oracle.py').write_text('pass\n')
        with self.assertRaisesRegex(shared.ReleaseError, 'changed'):
            wine.verify_checkout(self.root / 'copied', receipt)

    def test_verification_copy_never_reuses_existing_directory(self):
        source = self.checkout()
        destination = self.root / 'copied'
        destination.mkdir()
        (destination / 'keep.txt').write_text('keep')
        with self.assertRaises(FileExistsError):
            wine.snapshot_checkout(source, destination)
        self.assertEqual((destination / 'keep.txt').read_text(), 'keep')

    def test_real_pe_signature_machine_and_optional_header_are_required(self):
        bundle = self.bundle()
        self.assertEqual(len(wine.pe_inventory(bundle)), 7)
        file = bundle / 'BIFMA_TRADERS.exe'
        for data in (b'MZ marker only', pe(machine=0x14c), pe(optional=0x10b), pe()[:64] + b'NOPE' + pe()[68:]):
            file.write_bytes(data)
            with self.subTest(data=data[:4]), self.assertRaises(shared.ReleaseError):
                wine.pe_inventory(bundle)
        file.write_bytes(pe())
        (bundle / '_internal/_sqlite3.pyd').write_bytes(pe(machine=0xaa64))
        with self.assertRaises(shared.ReleaseError):
            wine.pe_inventory(bundle)

    def test_wine_paths_cannot_select_another_drive_or_host_directory(self):
        prefix = self.root / 'prefix'
        (prefix / 'drive_c/run').mkdir(parents=True)
        self.assertEqual(builder.windows_path(prefix / 'drive_c/run/python.exe', prefix), 'C:\\run\\python.exe')
        with self.assertRaises(shared.ReleaseError):
            builder.windows_path(self.root / 'outside.py', prefix)

    def test_host_wine_output_uses_pipe_and_preserves_failure_or_timeout_log(self):
        probe = self.root / 'probe'
        run = probe / 'prefix/drive_c/run'
        run.mkdir(parents=True)
        executable = probe / 'prefix/drive_c/Python313/python.exe'
        log = run / 'captured.log'
        runtime = {'loader': '/tmp/verified-wine/bin/wine', 'server': '/tmp/verified-wine/bin/wineserver',
                   'dll_path': '/tmp/verified-wine/lib/wine'}
        completed = subprocess.CompletedProcess([], 0, b'synthetic Windows output\r\n')
        with patch.dict(os.environ, {'WINELOADER': '/untrusted/wine', 'WINESERVER': '/untrusted/server', 'WINEDLLPATH': '/untrusted/dlls'}), patch.object(builder.subprocess, 'run', return_value=completed) as call:
            output, _ = builder.invoke_windows(probe, run, executable, ['--version'], log, runtime=runtime)
        self.assertEqual(output, 'synthetic Windows output\r\n')
        self.assertEqual(call.call_args.args[0][0], runtime['loader'])
        for variable, field in (('WINELOADER', 'loader'), ('WINESERVER', 'server'), ('WINEDLLPATH', 'dll_path')):
            self.assertEqual(call.call_args.kwargs['env'][variable], runtime[field])
        self.assertEqual(call.call_args.kwargs['env']['WINEPREFIX'], str(probe / 'prefix'))
        self.assertEqual(call.call_args.kwargs['stdout'], subprocess.PIPE)
        self.assertEqual(log.read_bytes(), completed.stdout)
        with patch.object(builder.subprocess, 'run', side_effect=subprocess.TimeoutExpired('synthetic', 1, output=b'partial')):
            with self.assertRaises(subprocess.TimeoutExpired):
                builder.invoke_windows(probe, run, executable, [], run / 'timeout.log', runtime=runtime)
        self.assertEqual((run / 'timeout.log').read_bytes(), b'partial')

    @unittest.skipUnless(os.name == 'posix', 'private Wine runtime is a Linux host validation')
    def test_private_wine_runtime_requires_pinned_packages_and_exact_extracted_inventory(self):
        download = self.root / 'downloads/synthetic.deb'
        download.parent.mkdir()
        download.write_bytes(b'verified synthetic package')
        extracted = self.root / 'extracted'
        files = ['opt/wine-stable/bin/wine', 'opt/wine-stable/bin/wineserver', 'opt/wine-stable/lib/wine/synthetic.dll']
        rows = []
        for name in files:
            path = extracted / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'verified synthetic runtime')
            rows.append({'path': name, 'sha256': shared.digest(path), 'bytes': path.stat().st_size})
        checksum = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with patch.object(builder, 'WINE_PACKAGES', {'synthetic.deb': shared.digest(download)}), patch.object(builder, 'WINE_INVENTORY_SHA256', checksum):
            runtime = builder.validate_wine_runtime(self.root)
            self.assertEqual(runtime['loader'], str(extracted / files[0]))
            self.assertEqual(runtime['server'], str(extracted / files[1]))
            self.assertEqual(runtime['provenance']['extracted_inventory_sha256'], checksum)
            (extracted / files[0]).write_bytes(b'changed')
            with self.assertRaisesRegex(shared.ReleaseError, 'inventory'):
                builder.validate_wine_runtime(self.root)
            (extracted / files[0]).write_bytes(b'verified synthetic runtime')
            (extracted / 'outside-link').symlink_to(download)
            with self.assertRaisesRegex(shared.ReleaseError, 'link leaves'):
                builder.validate_wine_runtime(self.root)
            (extracted / 'outside-link').unlink()
            download.write_bytes(b'changed package')
            with self.assertRaisesRegex(shared.ReleaseError, 'package checksum'):
                builder.validate_wine_runtime(self.root)

    def test_cleanup_uses_only_validated_matching_private_server(self):
        probe = self.root / 'probe'
        runtime = {'server': '/tmp/verified-wine/bin/wineserver'}
        argv = ['build_windows_wine.py', '--probe-root', str(probe), '--wine-runtime-root', '/tmp/verified-wine']
        with patch.object(builder.sys, 'argv', argv), patch.object(builder, 'validate_probe', return_value=(probe / 'prefix', {})), patch.object(builder, 'validate_wine_runtime', return_value=runtime), patch.object(builder, 'host_build', side_effect=RuntimeError('synthetic failure')), patch.object(builder.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as call:
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                builder.main()
        self.assertEqual(call.call_args.args[0], [runtime['server'], '-k'])
        self.assertEqual(call.call_args.kwargs['env']['WINEPREFIX'], str(probe / 'prefix'))

    @unittest.skipUnless(os.name == 'posix', 'Wine prefix links are a Linux host validation')
    def test_private_prefix_rejects_linked_drive_map_or_user_roots(self):
        prefix = self.root / 'prefix'
        (prefix / 'drive_c/users').mkdir(parents=True)
        (prefix / 'dosdevices').mkdir()
        (prefix / 'dosdevices/c:').symlink_to('../drive_c')
        receipt = {'official_python': {
            'source_url': 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.zip',
            'sha256': 'bbf675bb5e763c1efbb09a3a461b259d81598a63c30c4b0d7ea11b9f063df159'}}
        with patch.object(shared, '_json', return_value=receipt), patch.object(shared, 'digest', return_value=receipt['official_python']['sha256']):
            for directory in (prefix / 'dosdevices', prefix / 'drive_c/users'):
                moved = directory.with_name(directory.name + '-moved')
                directory.rename(moved)
                directory.symlink_to(moved, target_is_directory=True)
                with self.assertRaisesRegex(shared.ReleaseError, 'ordinary directories'):
                    builder.validate_probe(self.root)
                directory.unlink()
                moved.rename(directory)
            internal = prefix / 'another-user-directory'
            internal.mkdir()
            (prefix / 'drive_c/users/indirect').symlink_to(internal, target_is_directory=True)
            with self.assertRaisesRegex(shared.ReleaseError, 'no links'):
                builder.validate_probe(self.root)

    def test_offline_windows_toolchain_includes_marker_specific_dependencies(self):
        self.assertEqual(set(builder.WINDOWS_WHEEL_REQUIREMENTS),
                         {'pefile>=2022.5.30', 'pywin32-ctypes>=0.2.1'})

    def test_diagnostic_output_is_explicit_new_file_only(self):
        output = self.root / 'runtime.json'
        with patch('runtime_probe.check_runtime', return_value={'probe': 'ok'}), redirect_stdout(io.StringIO()):
            self.assertEqual(native_entry.main(['--check-runtime', '--output', str(output)]), 0)
            with self.assertRaises(FileExistsError):
                native_entry.main(['--check-runtime', '--output', str(output)])
        self.assertEqual(json.loads(output.read_text()), {'probe': 'ok'})

    def test_review_zip_refuses_traversal_duplicates_and_symlink_metadata(self):
        for index, name in enumerate(('../escape', 'BIFMA_TRADERS/../../escape', 'C:/escape', 'BIFMA_TRADERS/data/secret.db',
                                      'BIFMA_TRADERS/_internal//alias.txt', 'BIFMA_TRADERS/_internal/./alias.txt',
                                      'BIFMA_TRADERS/_internal/alias.txt.', 'BIFMA_TRADERS/_internal/alias.txt ',
                                      'BIFMA_TRADERS/_internal/a?.txt', 'BIFMA_TRADERS/_internal/CON.txt',
                                      'BIFMA_TRADERS/_internal/LPT1', 'BIFMA_TRADERS/_internal/a\x01.txt')):
            source = self.root / ('bad' + str(index) + '.zip')
            with zipfile.ZipFile(source, 'x') as archive:
                archive.writestr(name, b'synthetic')
            destination = self.root / ('out' + str(index))
            with self.assertRaises(shared.ReleaseError):
                wine.extract_review_archive(source, destination)
            self.assertFalse(destination.exists())
        source = self.root / 'symlink.zip'
        with zipfile.ZipFile(source, 'x') as archive:
            link = zipfile.ZipInfo('BIFMA_TRADERS/linked')
            link.external_attr = 0o120777 << 16
            archive.writestr(link, '../outside')
        with self.assertRaises(shared.ReleaseError):
            wine.extract_review_archive(source, self.root / 'link-output')
        source = self.root / 'case-alias.zip'
        with zipfile.ZipFile(source, 'x') as archive:
            archive.writestr('BIFMA_TRADERS/_internal/Alias.txt', 'first')
            archive.writestr('BIFMA_TRADERS/_internal/alias.txt', 'second')
        with self.assertRaisesRegex(shared.ReleaseError, 'duplicate'):
            wine.extract_review_archive(source, self.root / 'case-output')
        self.assertFalse((self.root / 'case-output').exists())

    def test_wine_release_requires_provenance_and_complete_frozen_checks(self):
        source = self.checkout()
        receipt = {'source': shared.source_inventory(source), 'runtime': {}}
        stage = shared.stage_source(source, self.root / 'stage', receipt)
        frozen = self.root / 'frozen.json'
        frozen.write_text('{}')
        with self.assertRaisesRegex(shared.ReleaseError, 'provenance'):
            wine.finish_release(self.bundle(), stage, self.root / 'release', frozen)
        receipt.update(built_under_wine=True, native_windows_qualified=False)
        (stage / '.release-stage.json').write_text(json.dumps(receipt))
        with self.assertRaisesRegex(shared.ReleaseError, 'incomplete'):
            wine.finish_release(self.root / 'bundle', stage, self.root / 'release', frozen)
        self.assertFalse((self.root / 'release').exists())

    def test_published_wine_zip_keeps_provenance_and_passes_extraction_audit(self):
        source = self.checkout()
        runtime = {'application_version': shared.application_version(source)[0],
                   'python_version': '3.13.16', 'sqlite_version': '3.50.4',
                   'tk_version': '8.6.15', 'platform': 'win32', 'bits': 64}
        receipt = {'source': shared.source_inventory(source), 'runtime': runtime,
                   'built_under_wine': True, 'native_windows_qualified': False,
                   'build_provenance': {'wine_version': 'synthetic'},
                   'verification_inputs': [{'path': 'tests/synthetic.py', 'sha256': 'synthetic'}],
                   'toolchain_wheels': [{'filename': 'synthetic.whl', 'sha256': 'synthetic'}],
                   'checks': [{'check': key, 'passed': True} for key in
                              ('application-tests', 'packaging-tests', 'native-gui-smoke')]}
        stage = shared.stage_source(source, self.root / 'stage', receipt)
        frozen = self.root / 'frozen.json'
        frozen.write_text(json.dumps({'native_runtime': runtime, 'history_runtime': runtime,
                         'checks': [{'check': key, 'passed': True} for key in wine.FROZEN_CHECKS],
                         'workflow': workflow_fixture(runtime['application_version'])}))
        result = wine.finish_release(self.bundle(), stage, self.root / 'release', frozen)
        self.assertTrue(result['archive'].endswith('-DEVELOPMENT-WINE.zip'))
        extracted = wine.extract_review_archive(Path(result['archive']), self.root / 'extracted')
        report = json.loads((extracted / 'release-manifest.json').read_text())
        self.assertTrue(report['built_under_wine'])
        self.assertFalse(report['native_windows_qualified'])
        self.assertFalse(report['production_ready'])
        self.assertEqual(report['build_provenance'], receipt['build_provenance'])
        self.assertEqual(report['verification_inputs'], receipt['verification_inputs'])
        self.assertEqual(report['toolchain_wheels'], receipt['toolchain_wheels'])


if __name__ == '__main__':
    unittest.main()
