"""Reviewed Windows-under-Wine staging and validation; no Wine import required."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import sys
import time
import zipfile

import release_support as shared

VERIFICATION_FILES = (
    'tests/__init__.py', 'tests/test_audit.py', 'tests/test_breakage_transactions.py',
    'tests/test_calculations.py', 'tests/test_diagnostics.py',
    'tests/test_cash_transactions.py', 'tests/test_cash_migration.py',
    'tests/test_cash_reports.py', 'tests/test_cash_exports.py',
    'tests/test_city_cash_reports.py', 'tests/test_city_cash_export.py', 'tests/test_database.py', 'tests/test_document_export.py',
    'tests/test_legacy_ledger.py', 'tests/test_legacy_repository.py', 'tests/test_legacy_stock.py',
    'tests/test_master_contacts.py', 'tests/test_masters.py', 'tests/test_native_backup.py',
    'tests/test_native_integration.py', 'tests/test_native_process_recovery.py',
    'tests/test_native_reports.py', 'tests/test_native_schema.py', 'tests/test_native_ui_models.py',
    'tests/test_preview_lifecycle.py',
    'tests/test_posting_rules.py', 'tests/test_recovery_oracle.py', 'tests/test_report_export.py',
    'tests/test_security.py', 'tests/test_staging.py', 'tests/test_sub_trial.py',
    'tests/test_trade_detail.py', 'tests/test_trade_detail_export.py',
    'tests/test_transactions.py', 'tools/smoke_native_ui.py', 'tools/recovery_oracle.py',
    'tools/stage_recovery.py', 'docs/legacy_schema.json',
)
from frozen_checks import FROZEN_CHECKS, validate_report as validate_frozen_report


def _windows_member_name(name):
    """Reject Win32 spellings that ZipFile/Windows would normalize or reserve."""
    for part in PurePosixPath(name).parts:
        if part.endswith(('.', ' ')) or any(ord(char) < 32 or char in '<>:"|?*' for char in part):
            raise shared.ReleaseError('Unsafe Windows ZIP member spelling.')
        if re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', part.split('.')[0], re.IGNORECASE):
            raise shared.ReleaseError('Reserved Windows ZIP member name.')


def verification_inventory(root):
    actual = {p.relative_to(root).as_posix() for p in (root / 'tests').rglob('*.py')}
    if actual != {p for p in VERIFICATION_FILES if p.startswith('tests/')}:
        raise shared.ReleaseError('The test module set changed; review the explicit verification allowlist.')
    return [{'path': name, 'sha256': shared.digest(shared._source_path(root, name))}
            for name in sorted(VERIFICATION_FILES)]


def snapshot_checkout(root, destination):
    """Copy a closed code/schema set; private files are never traversed/copied."""
    before = {'source': shared.source_inventory(root), 'verification_inputs': verification_inventory(root)}
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Verification checkout requires a new directory.')
    destination.mkdir()
    try:
        rows = before['source']['files'] + before['verification_inputs']
        for row in rows:
            source = shared._source_path(root, row['path'])
            if shared.digest(source) != row['sha256']:
                raise shared.ReleaseError('Source changed during verification checkout.')
            target = destination / row['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(source.read_bytes())
        verify_checkout(destination, before)
        if before != {'source': shared.source_inventory(root), 'verification_inputs': verification_inventory(root)}:
            raise shared.ReleaseError('Source changed during verification checkout.')
    except BaseException:
        shutil.rmtree(destination)
        raise
    return before


def verify_checkout(root, expected):
    if expected['source'] != shared.source_inventory(root) or expected['verification_inputs'] != verification_inventory(root):
        raise shared.ReleaseError('Verification checkout changed after its input receipt.')
    allowed = {row['path'] for row in expected['source']['files'] + expected['verification_inputs']}
    actual = {path.relative_to(root).as_posix() for path in shared._files(root)}
    if actual != allowed:
        raise shared.ReleaseError('Verification checkout contains an unreviewed file.')


def pe_inventory(bundle):
    """Require actual AMD64 PE32+ headers, in addition to existing bundle checks."""
    shared.audit_bundle(bundle)
    rows = []
    for path in shared._files(bundle):
        if path.suffix.lower() not in {'.exe', '.dll', '.pyd'}:
            continue
        with path.open('rb') as stream:
            dos = stream.read(64)
            if len(dos) < 64 or dos[:2] != b'MZ':
                raise shared.ReleaseError('Missing Windows PE header.')
            offset = struct.unpack_from('<I', dos, 0x3c)[0]
            if offset < 64 or offset > 16 * 1024**2:
                raise shared.ReleaseError('Invalid Windows PE header offset.')
            stream.seek(offset)
            pe = stream.read(26)
        if len(pe) != 26 or pe[:4] != b'PE\0\0' or struct.unpack_from('<H', pe, 4)[0] != 0x8664 or struct.unpack_from('<H', pe, 24)[0] != 0x20b:
            raise shared.ReleaseError('Windows runtime and launchers must be AMD64 PE32+ binaries.')
        rows.append(path.relative_to(bundle).as_posix())
    return rows


def logged(command, log, *, cwd, timeout=600, expected=0, environment=None):
    started = time.monotonic()
    with log.open('x', encoding='utf-8') as stream:
        completed = subprocess.run(command, cwd=cwd, env=environment, stdout=stream,
                                   stderr=subprocess.STDOUT, text=True, timeout=timeout)
    if completed.returncode != expected:
        raise shared.ReleaseError('Required command failed; see ' + str(log))
    return log.read_text(encoding='utf-8', errors='replace'), round(time.monotonic() - started, 3)


def run_checks(root, logs):
    specifications = [
        ('application-tests', ['-m', 'unittest', 'discover', '-s', 'tests', '-v']),
        ('packaging-tests', ['-m', 'unittest', 'discover', '-s', 'packaging', '-p', 'test_release.py', '-v']),
        ('wine-packaging-tests', ['-m', 'unittest', 'discover', '-s', 'packaging', '-p', 'test_wine_release.py', '-v']),
        ('frozen-packaging-tests', ['-m', 'unittest', 'discover', '-s', 'packaging', '-p', 'test_frozen_checks.py', '-v']),
        ('native-gui-smoke', ['-m', 'tools.smoke_native_ui']),
    ]
    results = []
    for name, arguments in specifications:
        log = logs / (name + '.log')
        output, seconds = logged([sys.executable, '-B', '-E', '-s', *arguments], log, cwd=root)
        count = re.search(r'Ran (\d+) tests?', output)
        if name.endswith('tests') and (count is None or int(count[1]) == 0):
            raise shared.ReleaseError('A required Windows check discovered no tests.')
        item = {'check': name, 'passed': True, 'tests_run': int(count[1]) if count else None,
                'seconds': seconds, 'log_sha256': shared.digest(log),
                'skips': re.findall(r"^.*\.\.\. skipped (.*)$", output, flags=re.MULTILINE)}
        results.append(item)
        print(json.dumps(item), flush=True)
    # The shared release contract has one packaging gate; retain every packaging sub-receipt.
    extras = [results.pop(2), results.pop(2)]
    results[1]['additional_checks'] = extras
    results[1]['tests_run'] += sum(row['tests_run'] for row in extras)
    return results


def extract_review_archive(archive_path, destination):
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Archive review requires a new directory.')
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if sum(entry.file_size for entry in entries) > 512 * 1024**2:
            raise shared.ReleaseError('Review ZIP exceeds its size bound.')
        names = set()
        for entry in entries:
            name = entry.filename
            shared._safe_name(name)
            _windows_member_name(name)
            normalized = PurePosixPath(name).as_posix()
            spelling = name[:-1] if entry.is_dir() else name
            if not name.startswith('BIFMA_TRADERS/') or '\\' in name or normalized != spelling or normalized.casefold() in names or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise shared.ReleaseError('Unsafe or duplicate review ZIP member.')
            names.add(normalized.casefold())
        destination.mkdir()
        try:
            archive.extractall(destination)
            bundle = destination / 'BIFMA_TRADERS'
            pe_inventory(bundle)
            return bundle
        except BaseException:
            shutil.rmtree(destination)
            raise


def finish_release(bundle, staged, destination, frozen_report):
    receipt = shared.verify_staged_source(staged)
    frozen = shared._json(frozen_report)
    if receipt.get('built_under_wine') is not True or receipt.get('native_windows_qualified') is not False:
        raise shared.ReleaseError('Wine provenance must be explicit before publication.')
    if {row['check'] for row in frozen.get('checks', []) if row.get('passed')} != FROZEN_CHECKS:
        raise shared.ReleaseError('Frozen Windows checks are incomplete.')
    validate_frozen_report(frozen, receipt['source']['application_version'])
    frozen['pe_amd64_files'] = pe_inventory(bundle)
    for runtime in (frozen.get('native_runtime', {}), frozen.get('history_runtime', {})):
        for key in ('application_version', 'python_version', 'sqlite_version', 'tk_version', 'platform', 'bits'):
            if runtime.get(key) != receipt['runtime'].get(key):
                raise shared.ReleaseError('Frozen Windows runtime mismatch: ' + key)
    # Shared publisher verifies all existing source, runtime and bundle boundaries.
    combined = frozen
    augmented = frozen_report.with_name('frozen-publication.json')
    shared._write_new_json(augmented, combined)
    return shared.finish_release(bundle, staged, destination, augmented,
                                 archive_tag='-DEVELOPMENT-WINE')
