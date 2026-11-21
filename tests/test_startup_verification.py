"""Scoped startup checks still reject mutations, aliases and recovery hazards."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bifma import baseline, native_schema as native
from bifma.database import session
from bifma.document_register import DocumentRegister
from bifma.security import SecurityService
from tests.test_baseline_import import synthetic_archive


class StartupVerificationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="bifma-startup-check-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.source = self.root / "archive.sqlite3"
        self.path = self.root / "working.sqlite3"
        synthetic_archive(self.source)
        baseline.ensure_baseline(self.source, self.path)

    @contextmanager
    def checks(self):
        with patch.object(native, "_check_integrity", wraps=native._check_integrity) as integrity, \
                patch.object(baseline, "_immutable_digest", wraps=baseline._immutable_digest) as immutable:
            yield integrity, immutable

    def alter_sealed_stock(self):
        # Deliberate out-of-contract damage in a temporary fixture only.
        with native.native_transaction(self.path) as connection:
            connection.execute("DROP TRIGGER baseline_stock_balances_update_guard")
            connection.execute("UPDATE baseline_stock_balances SET quantity='999'")

    def test_real_prepare_service_chain_checks_once_and_preserves_database_bytes(self):
        from bifma.native_ui import _NativeInterface
        original = {path: hashlib.sha256(path.read_bytes()).digest() for path in (self.source, self.path)}
        with self.checks() as (integrity, immutable), native.startup_verification():
            result = baseline.ensure_baseline(self.source, self.path)
            services = _NativeInterface._services(SimpleNamespace(archive_path=self.source), self.path)
            self.assertEqual(services[6].source_sha256, result.source_sha256)
            self.assertEqual(services[7].baseline.cutoff_date, result.cutoff_date)
            self.assertEqual((integrity.call_count, immutable.call_count), (1, 1))
        self.assertEqual({path: hashlib.sha256(path.read_bytes()).digest() for path in original}, original)

    def test_nested_scope_reuses_but_later_startups_and_unscoped_calls_check_again(self):
        with self.checks() as (integrity, immutable):
            with native.startup_verification():
                info = baseline.baseline_info(self.path)
                info.manifest["format"] = "caller changed its own result"
                with native.startup_verification():
                    self.assertEqual(baseline.baseline_info(self.path).manifest["format"], 1)
                self.assertEqual((integrity.call_count, immutable.call_count), (1, 1))
            with native.startup_verification():
                baseline.baseline_info(self.path)
            baseline.baseline_info(self.path)
            self.assertEqual((integrity.call_count, immutable.call_count), (3, 3))

    def test_scope_exception_discards_its_cached_checks(self):
        with self.checks() as (integrity, immutable):
            with self.assertRaisesRegex(RuntimeError, "synthetic"), native.startup_verification():
                baseline.baseline_info(self.path)
                raise RuntimeError("synthetic startup failure")
            with native.startup_verification():
                baseline.baseline_info(self.path)
            self.assertEqual((integrity.call_count, immutable.call_count), (2, 2))

    def test_committed_master_change_invalidates_both_checks(self):
        with self.checks() as (integrity, immutable), native.startup_verification():
            baseline.baseline_info(self.path)
            with native.native_transaction(self.path) as connection:
                connection.execute("UPDATE companies SET name='Changed synthetic company'")
            baseline.baseline_info(self.path)
            self.assertEqual((integrity.call_count, immutable.call_count), (2, 2))

    def test_committed_immutable_damage_is_refused_inside_same_scope(self):
        with self.checks() as (integrity, immutable), native.startup_verification():
            baseline.baseline_info(self.path)
            self.alter_sealed_stock()
            with self.assertRaisesRegex(baseline.BaselineError, "no longer matches"):
                baseline.baseline_info(self.path)
            self.assertEqual((integrity.call_count, immutable.call_count), (2, 2))

    def test_stamp_is_checked_after_snapshot_establishment_not_before_session(self):
        original_session = baseline.native_session
        owner = self
        changed = []

        class Connection:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, statement, *args):
                if statement.startswith("SELECT * FROM baseline_imports") and not changed:
                    changed.append(True)
                    owner.alter_sealed_stock()
                return self.connection.execute(statement, *args)

        @contextmanager
        def change_before_snapshot(path):
            with original_session(path) as connection:
                yield Connection(connection)

        with native.startup_verification():
            baseline.baseline_info(self.path)
            with patch.object(baseline, "native_session", change_before_snapshot):
                with self.assertRaisesRegex(baseline.BaselineError, "no longer matches"):
                    baseline.baseline_info(self.path)
        self.assertEqual(changed, [True])

    def test_replacement_at_same_path_cannot_inherit_previous_baseline(self):
        replacement = self.root / "replacement.sqlite3"
        native.initialize_database(replacement)
        with self.checks() as (integrity, immutable), native.startup_verification():
            baseline.baseline_info(self.path)
            os.replace(replacement, self.path)
            with self.assertRaisesRegex(baseline.BaselineError, "does not belong"):
                baseline.ensure_baseline(self.source, self.path)
            self.assertEqual(integrity.call_count, 2)
            self.assertEqual(immutable.call_count, 1)

    def test_changed_migration_lineage_cannot_reuse_native_validation(self):
        with native.startup_verification():
            native.open_native_database(self.path)
            with native.native_transaction(self.path) as connection:
                connection.execute("DROP TRIGGER schema_migrations_immutable_update")
                connection.execute("UPDATE schema_migrations SET checksum=? WHERE version=1", ("0" * 64,))
            with self.assertRaisesRegex(native.NativeSchemaError, "checksum"):
                native.open_native_database(self.path)

    def test_foreign_key_damage_still_runs_full_integrity_check(self):
        with native.startup_verification():
            native.open_native_database(self.path)
            with session(self.path, readonly=False) as connection:
                connection.execute("PRAGMA foreign_keys=OFF")
                connection.execute(
                    "INSERT INTO company_stores(company_id,store_id) "
                    "SELECT '00000000-0000-0000-0000-000000000000',id FROM stores LIMIT 1")
            with self.assertRaisesRegex(native.NativeSchemaError, "integrity checks"):
                native.open_native_database(self.path)

    def test_new_hard_link_is_refused_even_after_cached_success(self):
        alias = self.root / "alias.sqlite3"
        with native.startup_verification():
            native.open_native_database(self.path)
            try:
                os.link(self.path, alias)
            except OSError:
                self.skipTest("Hard links unavailable")
            with self.assertRaisesRegex(native.NativeSchemaError, "aliases"):
                native.open_native_database(self.path)

    def test_schema_spec_and_current_version_policy_remain_part_of_check(self):
        with native.startup_verification():
            native.open_native_database(self.path)
            extra = native.Migration(len(native.MIGRATIONS) + 1, "synthetic_future", ("CREATE TABLE future_test(id INTEGER)",))
            with patch.object(native, "MIGRATIONS", native.MIGRATIONS + (extra,)):
                self.assertEqual(native.open_native_database(self.path, require_current=False), extra.version - 1)
                with self.assertRaisesRegex(native.NativeSchemaError, "upgrade is required"):
                    native.open_native_database(self.path)

    def test_thread_and_process_identity_do_not_inherit_verification(self):
        def other_thread():
            with native.startup_verification():
                baseline.baseline_info(self.path)
                baseline.baseline_info(self.path)

        with self.checks() as (integrity, immutable), native.startup_verification():
            baseline.baseline_info(self.path)
            with ThreadPoolExecutor(max_workers=1) as worker:
                worker.submit(other_thread).result(timeout=10)
            self.assertEqual((integrity.call_count, immutable.call_count), (2, 2))
            # A fork inherits thread-local memory but has a different PID.
            with patch.object(native.os, "getpid", return_value=os.getpid() + 1):
                baseline.baseline_info(self.path)
            self.assertEqual((integrity.call_count, immutable.call_count), (3, 3))
            baseline.baseline_info(self.path)
            self.assertEqual((integrity.call_count, immutable.call_count), (3, 3))

    def test_separate_python_process_performs_its_own_full_checks(self):
        program = """
import json,sys
from unittest.mock import patch
from bifma import baseline,native_schema as native
with patch.object(native,'_check_integrity',wraps=native._check_integrity) as integrity, patch.object(baseline,'_immutable_digest',wraps=baseline._immutable_digest) as immutable:
    with native.startup_verification():
        baseline.baseline_info(sys.argv[1])
        baseline.baseline_info(sys.argv[1])
    print(json.dumps([integrity.call_count,immutable.call_count]))
"""
        with native.startup_verification():
            baseline.baseline_info(self.path)
            result = subprocess.run([sys.executable, "-B", "-c", program, str(self.path)],
                                    capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [1, 1])

    def test_journal_wal_and_shm_markers_disable_previous_reuse(self):
        for suffix in ("-journal", "-wal", "-shm"):
            with self.subTest(suffix=suffix), self.checks() as (integrity, immutable), native.startup_verification():
                baseline.baseline_info(self.path)
                marker = Path(str(self.path) + suffix)
                marker.write_bytes(b"")
                try:
                    baseline.baseline_info(self.path)
                    self.assertEqual((integrity.call_count, immutable.call_count), (2, 2))
                finally:
                    marker.unlink(missing_ok=True)

    def test_cached_startup_still_refuses_nesting_inside_managed_session(self):
        with native.startup_verification():
            native.open_native_database(self.path)
            with native.native_session(self.path):
                with self.assertRaisesRegex(native.NativeSchemaError, "active database session"):
                    native.open_native_database(self.path)

    def test_cached_startup_waits_for_another_threads_write_and_rechecks_commit(self):
        held, release, committed = threading.Event(), threading.Event(), threading.Event()

        def writer():
            with native.native_transaction(self.path) as connection:
                connection.execute("UPDATE companies SET name='Concurrent synthetic update'")
                held.set()
                if not release.wait(10):
                    raise AssertionError("Synthetic writer was never released")
                connection.commit()
                committed.set()

        with self.checks() as (integrity, immutable), native.startup_verification():
            baseline.baseline_info(self.path)
            with ThreadPoolExecutor(max_workers=1) as worker:
                future = worker.submit(writer)
                self.assertTrue(held.wait(10))
                timer = threading.Timer(.1, release.set)
                timer.start()
                try:
                    baseline.baseline_info(self.path)
                    self.assertTrue(committed.is_set(), "Cached startup bypassed the writer lifetime gate")
                    future.result(timeout=10)
                finally:
                    release.set()
                    timer.join(timeout=10)
            self.assertEqual((integrity.call_count, immutable.call_count), (2, 2))

    def test_hot_journal_after_cached_open_is_recovered_then_reverified(self):
        fault = """
import os,sqlite3,sys
connection=sqlite3.connect(sys.argv[1],isolation_level=None)
connection.execute('PRAGMA synchronous=FULL')
connection.execute('PRAGMA cache_size=1')
connection.execute('BEGIN IMMEDIATE')
connection.execute('CREATE TABLE uncommitted_startup_probe(value BLOB)')
connection.execute('INSERT INTO uncommitted_startup_probe VALUES(zeroblob(30000))')
os._exit(76)
"""
        with self.checks() as (integrity, immutable), native.startup_verification():
            original = baseline.baseline_info(self.path)
            child = subprocess.run([sys.executable, "-B", "-c", fault, str(self.path)],
                                   capture_output=True, text=True, timeout=15)
            self.assertEqual(child.returncode, 76, child.stderr)
            journal = Path(str(self.path) + "-journal")
            self.assertTrue(any(journal.read_bytes()[:8]), "Fixture did not leave a hot rollback journal")
            recovered = baseline.baseline_info(self.path)
            self.assertEqual(recovered.manifest, original.manifest)
            self.assertEqual((integrity.call_count, immutable.call_count), (2, 2))
            with native.native_session(self.path) as connection:
                self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='uncommitted_startup_probe'").fetchone())

    def test_archive_hash_is_verified_again_for_each_new_register(self):
        with native.startup_verification():
            security = SecurityService(self.path)
            DocumentRegister(self.path, self.source, security=security)
            with session(self.source, readonly=False) as connection:
                connection.execute("UPDATE CITY SET CNAME='Changed original fixture'")
            with self.assertRaisesRegex(baseline.BaselineError, "does not match"):
                DocumentRegister(self.path, self.source, security=security)


if __name__ == "__main__":
    unittest.main()
