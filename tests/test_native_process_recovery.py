"""Actual process interruption/restart checks against synthetic native files.

The child exits with os._exit, bypassing Python transaction context cleanup.
No shell, live database, historical archive, or application fault hook is used.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import closing
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from bifma.database import check_integrity
from bifma.masters import MasterService
from bifma import database, native_schema as native
from bifma.native_backup import NativeBackupService
from bifma.native_schema import initialize_database, native_session
from bifma.security import SecurityService
from bifma.transactions import RevisionConflict, TransactionService


ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "Synthetic process recovery lanterns 73!"
BEFORE_COMMIT_EXIT = 73
AFTER_COMMIT_EXIT = 74
DUPLICATE_REJECTED_EXIT = 75

# Values are command-line arguments, never interpolated into Python or a shell.
# The temporary fixture password goes over stdin, and no result is acknowledged
# before either deliberate process exit. Assertions prove the requested boundary
# was reached: an unrelated child exception cannot be mistaken for our fault.
CHILD = r"""
import os
import sys

from bifma.native_schema import native_session
from bifma.security import SecurityService
from bifma.transactions import RevisionConflict, TransactionService

path, company, document_id, revision, mode = sys.argv[1:]
security = SecurityService(path)
actor = security.authenticate("owner", sys.stdin.read())
service = TransactionService(path, security)
revision = int(revision)

if mode == "before_commit":
    original_write = service._write_effects

    def interrupt_after_effects(connection, company_id, identifier, calculation, notes):
        # Force this tiny fixture to spill dirty pages before commit, exercising
        # recovery of a real rollback journal rather than only lost RAM writes.
        connection.execute("PRAGMA cache_size=1")
        original_write(connection, company_id, identifier, calculation, notes)
        assert connection.in_transaction
        assert identifier == document_id
        assert connection.execute("SELECT COUNT(*) FROM journal_entries WHERE document_id=?", (identifier,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id WHERE e.document_id=?", (identifier,)).fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM stock_movements WHERE document_id=?", (identifier,)).fetchone()[0] == 1
        assert tuple(connection.execute("SELECT status,revision FROM documents WHERE id=?", (identifier,)).fetchone()) == ("draft", revision)
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        with open(path + "-journal", "rb") as journal:
            header = journal.read(8)
        assert len(header) == 8 and any(header), "The fault must leave an on-disk rollback journal requiring recovery"
        os._exit(73)

    service._write_effects = interrupt_after_effects
    service.post(actor, company, document_id, revision)
    raise AssertionError("The interruption hook did not run")

elif mode == "after_commit":
    posted = service.post(actor, company, document_id, revision)
    assert posted["status"] == "posted"
    # A separate connection must see the committed status and effects before
    # this client exits without sending an acknowledgement to its caller.
    with native_session(path) as connection:
        assert tuple(connection.execute("SELECT status,revision FROM documents WHERE id=?", (document_id,)).fetchone()) == ("posted", revision + 1)
        assert connection.execute("SELECT COUNT(*) FROM posted_stock_movements WHERE document_id=?", (document_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE entity_id=? AND action='transaction.posted'", (document_id,)).fetchone()[0] == 1
    os._exit(74)

elif mode == "retry":
    try:
        service.post(actor, company, document_id, revision)
    except RevisionConflict:
        os._exit(75)
    raise AssertionError("An already committed document was posted twice")

else:
    raise AssertionError("Unsupported test mode")
"""


class NativeProcessRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # Spaces exercise path argument handling on POSIX and Windows alike.
        self.path = Path(temporary.name) / "synthetic recovery.sqlite3"
        initialize_database(self.path)
        self.security = SecurityService(self.path)
        self.actor = self.security.bootstrap_admin("owner", PASSWORD)
        self.masters = MasterService(self.path, self.security)
        self.service = TransactionService(self.path, self.security)
        self.company = self.create("companies", code="CO", name="Synthetic recovery company", fiscal_start="2026-01-01", fiscal_end="2026-12-31")
        self.store = self.create("stores", code="STORE", name="Synthetic store")
        self.create("company_stores", company_id=self.company, store_id=self.store)
        self.unit = self.create("units", code="PC", name="Piece")
        self.customer = self.create("accounts", code="CUSTOMER", name="Synthetic customer", kind="customer")
        self.sales = self.create("accounts", code="SALES", name="Sales", classification="income")
        self.inventory = self.create("accounts", code="STOCK", name="Inventory", classification="asset")
        self.equity = self.create("accounts", code="EQUITY", name="Opening counterpart", classification="equity")
        self.product = self.create("products", code="ITEM", name="Synthetic item", small_unit_id=self.unit, sale_account_id=self.sales, inventory_account_id=self.inventory)
        opening_values = {
            "document_number": "OPEN-1", "business_date": "2026-10-01", "rounding_policy": "cents", "posting_places": 2,
            "contra_account_id": self.equity,
            "lines": [{"product_id": self.product, "store_id": self.store, "quantity": "10", "value": "100", "pool": "regular"}],
        }
        opening = self.service.save_draft(self.actor, self.company, "stock_opening", opening_values)
        self.opening = self.service.post(self.actor, self.company, opening["id"], opening["revision"])
        sale_values = {
            "document_number": "SALE-1", "business_date": "2026-10-02", "rounding_policy": "cents", "posting_places": 2,
            "account_id": self.customer,
            "lines": [{"product_id": self.product, "store_id": self.store, "quantity": "2", "rate": "17.25", "discount_percent": "0"}],
        }
        self.draft = self.service.save_draft(self.actor, self.company, "sale", sale_values)

    def create(self, entity, **values):
        return self.masters.create(self.actor, entity, values)["id"]

    def child(self, mode, expected_exit):
        completed = subprocess.run(
            [sys.executable, "-c", CHILD, str(self.path), self.company, self.draft["id"], str(self.draft["revision"]), mode],
            input=PASSWORD, text=True, capture_output=True, cwd=ROOT, timeout=30,
            check=False, shell=False,
        )
        self.assertEqual(completed.returncode, expected_exit, completed.stderr)
        self.assertEqual(completed.stdout, "", "A crashed client must not report success")

    def reopen(self):
        self.security = SecurityService(self.path)
        self.actor = self.security.authenticate("owner", PASSWORD)
        self.service = TransactionService(self.path, self.security)
        return self.service.get(self.actor, self.company, self.draft["id"])

    def posting_state(self):
        """Inspect all effects, including draft effects hidden by posted views."""
        identifier = self.draft["id"]
        with native_session(self.path) as connection:
            return {
                "entries": [tuple(row) for row in connection.execute("SELECT * FROM journal_entries WHERE document_id=? ORDER BY id", (identifier,))],
                "journal": [tuple(row) for row in connection.execute("SELECT l.* FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id WHERE e.document_id=? ORDER BY l.line_number", (identifier,))],
                "stock": [tuple(row) for row in connection.execute("SELECT * FROM stock_movements WHERE document_id=? ORDER BY id", (identifier,))],
                "posted_stock": connection.execute("SELECT COUNT(*) FROM posted_stock_movements WHERE document_id=?", (identifier,)).fetchone()[0],
                "audit": [tuple(row) for row in connection.execute("SELECT * FROM audit_events WHERE entity_id=? ORDER BY id", (identifier,))],
                "posting_audit_count": connection.execute("SELECT COUNT(*) FROM audit_events WHERE entity_id=? AND action='transaction.posted'", (identifier,)).fetchone()[0],
            }

    def assert_quantity(self, expected):
        balances = self.service.stock_balance(self.actor, self.company, product_id=self.product, store_id=self.store)
        self.assertEqual(len(balances), 1)
        self.assertEqual(balances[0]["quantity"], expected)

    def assert_committed_once(self, posted):
        self.assertEqual((posted["status"], posted["revision"], posted["total_amount"]), ("posted", self.draft["revision"] + 1, "34.5"))
        self.assertEqual(posted["posting_sequence"], self.opening["posting_sequence"] + 1)
        self.assertEqual(sum(Decimal(row["debit"]) for row in posted["journal_lines"]), Decimal("34.5"))
        self.assertEqual(sum(Decimal(row["credit"]) for row in posted["journal_lines"]), Decimal("34.5"))
        state = self.posting_state()
        self.assertEqual((len(state["entries"]), len(state["journal"]), len(state["stock"]), state["posted_stock"], state["posting_audit_count"]), (1, 2, 1, 1, 1))
        self.assert_quantity("8")
        self.assertTrue(check_integrity(self.path)["ok"])
        return state

    def test_exit_with_uncommitted_effects_recovers_intact_draft_then_posts_once(self):
        before = self.posting_state()
        self.assertEqual((before["entries"], before["journal"], before["stock"], before["posted_stock"], before["posting_audit_count"]), ([], [], [], 0, 0))
        self.child("before_commit", BEFORE_COMMIT_EXIT)
        # The explicit readonly contract remains unchanged: it must neither
        # repair a hot journal nor silently reopen this file writable.
        journal = Path(str(self.path) + "-journal")
        interrupted_bytes = self.path.read_bytes(), journal.read_bytes()
        with self.assertRaises(native.NativeSchemaError), native_session(self.path):
            pass
        self.assertEqual((self.path.read_bytes(), journal.read_bytes()), interrupted_bytes)
        recovered = self.reopen()
        # Includes saved inputs, labels, line IDs, calculation, revision and all
        # header timestamps: no portion of the interrupted post may survive.
        self.assertEqual(recovered, self.draft)
        self.assertEqual(self.posting_state(), before)
        self.assert_quantity("10")
        self.assertEqual(self.service.get(self.actor, self.company, self.opening["id"]), self.opening)
        self.assertTrue(check_integrity(self.path)["ok"])

        posted = self.service.post(self.actor, self.company, recovered["id"], recovered["revision"])
        committed = self.assert_committed_once(posted)
        self.child("retry", DUPLICATE_REJECTED_EXIT)
        self.assertEqual(self.posting_state(), committed)
        self.assert_committed_once(self.reopen())

    def test_exit_after_commit_without_acknowledgement_cannot_post_twice(self):
        self.child("after_commit", AFTER_COMMIT_EXIT)
        posted = self.reopen()
        committed = self.assert_committed_once(posted)
        # Both the original caller and a separate new process retry using the
        # stale pre-acknowledgement revision. Neither may add money or stock.
        with self.assertRaises(RevisionConflict):
            self.service.post(self.actor, self.company, self.draft["id"], self.draft["revision"])
        self.child("retry", DUPLICATE_REJECTED_EXIT)
        self.assertEqual(self.posting_state(), committed)
        self.assertEqual(self.reopen(), posted)
        self.assert_committed_once(posted)

    def test_direct_master_and_backup_startup_recover_before_readonly_validation(self):
        for constructor in (MasterService, NativeBackupService):
            with self.subTest(service=constructor.__name__):
                self.child("before_commit", BEFORE_COMMIT_EXIT)
                constructor(self.path)
                self.assertEqual(self.reopen(), self.draft)
                self.assert_quantity("10")
                self.assertTrue(check_integrity(self.path)["ok"])


class NativeStartupBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "native.sqlite3"

    def test_healthy_startup_preserves_bytes_and_readonly_session_stays_readonly(self):
        initialize_database(self.path)
        before = self.path.read_bytes()
        self.assertEqual(native.open_native_database(self.path), native.MIGRATIONS[-1].version)
        SecurityService(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        with native_session(self.path) as connection, self.assertRaises(sqlite3.OperationalError):
            connection.execute("UPDATE native_meta SET created_at=created_at+1")
        self.assertEqual(self.path.read_bytes(), before)

    def test_startup_during_existing_transaction_does_not_drop_its_process_lock(self):
        initialize_database(self.path)
        probe = "import sqlite3,sys\nc=sqlite3.connect(sys.argv[1],timeout=0,isolation_level=None)\ntry:\n c.execute('BEGIN IMMEDIATE')\n print('acquired')\nexcept sqlite3.OperationalError:\n print('blocked')\nfinally:\n c.close()"
        with native.native_transaction(self.path):
            # Startup may reject a nested lifecycle call, but it must never
            # release the transaction's POSIX advisory lock as a side effect.
            try:
                native.open_native_database(self.path)
            except native.NativeSchemaError:
                pass
            result = subprocess.run([sys.executable, "-c", probe, str(self.path)], text=True, capture_output=True, timeout=10, check=True)
            self.assertEqual(result.stdout.strip(), "blocked")

    def test_startup_waits_for_other_thread_and_allows_existing_nested_session(self):
        initialize_database(self.path)
        started = Event()

        def startup():
            started.set()
            return native.open_native_database(self.path)

        with ThreadPoolExecutor(max_workers=1) as executor:
            with native.native_transaction(self.path):
                pending = executor.submit(startup)
                self.assertTrue(started.wait(5))
                with self.assertRaises(TimeoutError):
                    pending.result(timeout=0.1)
                # Online backup uses another session in the existing thread.
                # Waiting startup must not block this and deadlock its caller.
                with native_session(self.path) as nested:
                    self.assertEqual(native.require_native(nested), native.MIGRATIONS[-1].version)
                self.assertFalse(pending.done())
            self.assertEqual(pending.result(timeout=5), native.MIGRATIONS[-1].version)

    def test_managed_readonly_sessions_remain_concurrent(self):
        initialize_database(self.path)

        def reader():
            with native_session(self.path) as second:
                return native.require_native(second)

        with ThreadPoolExecutor(max_workers=1) as executor:
            with native_session(self.path) as first:
                first.execute("BEGIN")
                first.execute("SELECT * FROM native_meta").fetchall()
                self.assertEqual(executor.submit(reader).result(timeout=5), native.MIGRATIONS[-1].version)

    def test_cross_file_nested_startup_rejects_without_two_thread_deadlock(self):
        initialize_database(self.path)
        other = self.root / "other.sqlite3"
        initialize_database(other)
        before = self.path.read_bytes(), other.read_bytes()
        probe = r"""
import json, sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from bifma.native_schema import native_session, open_native_database, NativeSchemaError
barrier = Barrier(2)
def attempt(paths):
    first, second = paths
    with native_session(first):
        barrier.wait(timeout=3)
        try:
            open_native_database(second)
        except NativeSchemaError:
            return "rejected"
        return "unexpected startup"
with ThreadPoolExecutor(max_workers=2) as executor:
    print(json.dumps(list(executor.map(attempt, [(sys.argv[1],sys.argv[2]), (sys.argv[2],sys.argv[1])]))))
"""
        completed = subprocess.run([sys.executable, "-c", probe, str(self.path), str(other)], text=True, capture_output=True, cwd=ROOT, timeout=10, check=True)
        self.assertEqual(json.loads(completed.stdout), ["rejected", "rejected"])
        self.assertEqual((self.path.read_bytes(), other.read_bytes()), before)

    def test_startup_cannot_nest_startup_or_managed_session_for_another_file(self):
        initialize_database(self.path)
        other = self.root / "other.sqlite3"
        initialize_database(other)
        with database.exclusive_file_access(self.path):
            with self.assertRaisesRegex(native.NativeSchemaError, "active database session"):
                native.open_native_database(other)
            with self.assertRaisesRegex(database.DatabaseError, "inside native startup"), database.session(other):
                pass

    def test_connection_setup_failure_closes_traceback_retained_handle(self):
        initialize_database(self.path)
        handles = []
        original_connect = sqlite3.connect

        class BrokenConfiguration(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql == "PRAGMA foreign_keys=ON":
                    raise sqlite3.OperationalError("Synthetic configuration failure")
                return super().execute(sql, *args, **kwargs)

        def faulty_connect(*args, **kwargs):
            result = original_connect(*args, factory=BrokenConfiguration, **kwargs)
            handles.append(result)
            return result

        with patch("bifma.database.sqlite3.connect", side_effect=faulty_connect), self.assertRaisesRegex(sqlite3.OperationalError, "configuration"):
            with database.session(self.path):
                self.fail("An incompletely configured connection must not be exposed")
        with self.assertRaises(sqlite3.ProgrammingError):
            handles[0].execute("SELECT 1")
        self.assertEqual(native.open_native_database(self.path), native.MIGRATIONS[-1].version)

    @unittest.skipUnless(os.name == "posix", "Open-file rename uses POSIX filesystem semantics")
    def test_renamed_active_file_cannot_bypass_startup_gate_and_drop_writer_lock(self):
        initialize_database(self.path)
        renamed = self.root / "renamed.sqlite3"
        probe = "import sqlite3,sys\nc=sqlite3.connect(sys.argv[1],timeout=0,isolation_level=None)\ntry:\n c.execute('BEGIN IMMEDIATE')\n print('acquired')\nexcept sqlite3.OperationalError:\n print('blocked')\nfinally:\n c.close()"
        with native.native_transaction(self.path):
            self.path.rename(renamed)
            try:
                with self.assertRaisesRegex(native.NativeSchemaError, "active database session"):
                    native.open_native_database(renamed)
                result = subprocess.run([sys.executable, "-c", probe, str(renamed)], text=True, capture_output=True, timeout=10, check=True)
                self.assertEqual(result.stdout.strip(), "blocked")
            finally:
                # Renaming an active SQLite file is unsupported. Restore this
                # deliberately moved test file before the existing tx closes.
                renamed.rename(self.path)

    def test_missing_empty_text_and_readonly_foreign_files_never_reach_writable_sqlite(self):
        empty, text, archive, v2 = [self.root / name for name in ("empty.sqlite3", "text.sqlite3", "archive.sqlite3", "v2.sqlite3")]
        empty.write_bytes(b"")
        text.write_bytes(b"Synthetic Oracle export, not SQLite")
        for path, table in ((archive, "_recovery_metadata"), (v2, "prototype_data")):
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(f"CREATE TABLE {table}(value TEXT)")
                connection.execute(f"INSERT INTO {table} VALUES ('Synthetic only')")
        archive.chmod(0o400)
        try:
            with patch("bifma.native_schema.sqlite3.connect", side_effect=AssertionError("Unrecognized input must not reach SQLite mode=rw")):
                with self.assertRaises(sqlite3.OperationalError):
                    native.open_native_database(self.path)
                self.assertFalse(self.path.exists())
                for path in (empty, text, archive, v2):
                    before = path.read_bytes()
                    with self.subTest(path=path.name), self.assertRaises(native.NativeSchemaError):
                        native.open_native_database(path)
                    self.assertEqual(path.read_bytes(), before)
                with self.assertRaises(native.NativeSchemaError):
                    SecurityService(archive)
        finally:
            # Use os.chmod's native platform default: Windows does not follow
            # links, avoiding Wine's readonly reset error in the handle path.
            os.chmod(archive, 0o600)

    def test_final_symlink_is_rejected_before_any_writable_open(self):
        initialize_database(self.path)
        link = self.root / "alias.sqlite3"
        try:
            link.symlink_to(self.path)
        except OSError:
            self.skipTest("Symlinks unavailable")
        before = self.path.read_bytes()
        with patch("bifma.native_schema.sqlite3.connect", side_effect=AssertionError("No writable open")), self.assertRaises(native.NativeSchemaError):
            native.open_native_database(link)
        self.assertEqual(self.path.read_bytes(), before)

    def test_hard_link_alias_is_rejected_to_avoid_separate_journal_paths(self):
        initialize_database(self.path)
        link = self.root / "hardlink.sqlite3"
        try:
            os.link(self.path, link)
        except OSError:
            self.skipTest("Hard links unavailable")
        with patch("bifma.native_schema.sqlite3.connect", side_effect=AssertionError("No writable open")), self.assertRaisesRegex(native.NativeSchemaError, "aliases"):
            native.open_native_database(link)

    @unittest.skipUnless(os.name == "posix", "Open-file rename race uses POSIX filesystem semantics")
    def test_path_replaced_between_header_read_and_sqlite_open_is_refused_before_recovery(self):
        initialize_database(self.path)
        original = self.path.read_bytes()
        foreign = self.root / "foreign.sqlite3"
        with closing(sqlite3.connect(foreign)) as connection, connection:
            connection.execute("CREATE TABLE external_data(value TEXT)")
        foreign_bytes = foreign.read_bytes()
        preserved = self.root / "preserved-native.sqlite3"
        connect = sqlite3.connect

        def replace_before_open(*args, **kwargs):
            os.replace(self.path, preserved)
            os.replace(foreign, self.path)
            return connect(*args, **kwargs)

        with patch("bifma.native_schema.sqlite3.connect", side_effect=replace_before_open), self.assertRaisesRegex(native.NativeSchemaError, "path changed"):
            native.open_native_database(self.path)
        self.assertEqual(self.path.read_bytes(), foreign_bytes)
        self.assertEqual(preserved.read_bytes(), original)
        self.assertFalse(Path(str(self.path) + "-journal").exists())

    def test_header_marker_alone_is_not_accepted_as_native_lineage(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(f"PRAGMA application_id={native.APPLICATION_ID}")
            connection.execute("CREATE TABLE foreign_data(value TEXT)")
        before = self.path.read_bytes()
        with self.assertRaises(native.NativeSchemaError):
            native.open_native_database(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_old_known_schema_can_be_checked_without_automatic_upgrade(self):
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS[:1]):
            initialize_database(self.path)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(native.NativeSchemaError, "upgrade is required"):
            native.open_native_database(self.path)
        self.assertEqual(native.open_native_database(self.path, require_current=False), 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_explicit_upgrade_recovers_old_hot_journal_then_backs_up_committed_version(self):
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS[:1]):
            initialize_database(self.path)
        fault = "import os,sqlite3,sys\nc=sqlite3.connect(sys.argv[1],isolation_level=None)\nc.execute('PRAGMA synchronous=FULL')\nc.execute('PRAGMA cache_size=1')\nc.execute('BEGIN IMMEDIATE')\nc.execute('CREATE TABLE uncommitted_recovery_marker(value BLOB)')\nc.execute('INSERT INTO uncommitted_recovery_marker VALUES(zeroblob(20000))')\nos._exit(76)"
        completed = subprocess.run([sys.executable, "-c", fault, str(self.path)], text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(completed.returncode, 76, completed.stderr)
        journal = Path(str(self.path) + "-journal")
        self.assertTrue(any(journal.read_bytes()[:8]))
        result = native.migrate_database(self.path)
        self.assertEqual(result.version, native.MIGRATIONS[-1].version)
        self.assertEqual(result.applied_versions, tuple(migration.version for migration in native.MIGRATIONS[1:]))
        self.assertTrue(result.backup_path.is_file())
        with native_session(self.path) as connection:
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='uncommitted_recovery_marker'").fetchone())
        with database.session(result.backup_path) as connection:
            native.prepare_connection(connection)
            self.assertEqual(native.require_native(connection, require_current=False), 1)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='uncommitted_recovery_marker'").fetchone())
        self.assertTrue(check_integrity(self.path)["ok"])
        self.assertTrue(check_integrity(result.backup_path)["ok"])

    def test_version_mismatch_and_integrity_violation_are_not_silently_repaired(self):
        initialize_database(self.path)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("PRAGMA user_version=999")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(native.NativeSchemaError, "version"):
            native.open_native_database(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(f"PRAGMA user_version={native.MIGRATIONS[-1].version}")
            connection.execute("INSERT INTO accounts(id,code,name,parent_id) VALUES ('00000000-0000-0000-0000-000000000001','BAD','Synthetic orphan','00000000-0000-0000-0000-000000000002')")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(native.NativeSchemaError, "integrity"):
            native.open_native_database(self.path)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
