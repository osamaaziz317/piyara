"""Native lifecycle and financial invariants using synthetic temporary files."""

from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma.database import backup_database, session
from bifma import native_schema as native


def new_id():
    return str(uuid4())


class NativeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "native.sqlite3"

    def test_initialize_is_explicit_private_and_seeds_no_users_or_permissions(self):
        result = native.initialize_database(self.path)
        self.assertEqual(result.applied_versions, tuple(m.version for m in native.MIGRATIONS))
        self.assertIsNone(result.backup_path)
        with native.native_session(self.path) as connection:
            self.assertEqual(native.require_native(connection), native.MIGRATIONS[-1].version)
            for table in ("users", "companies", "user_company_grants", "user_menu_grants", "documents"):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA recursive_triggers").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM users")
        if __import__("os").name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o077, 0)

    def test_existing_files_are_never_initialized_or_promoted(self):
        for label, content in (("text", b"not a database"), ("empty", b"")):
            with self.subTest(label=label):
                path = self.root / label
                path.write_bytes(content)
                with self.assertRaises(FileExistsError):
                    native.initialize_database(path)
                self.assertEqual(path.read_bytes(), content)
        connection = sqlite3.connect(self.path)
        connection.execute("CREATE TABLE source_records (value TEXT)")
        connection.commit()
        connection.close()
        before = self.path.read_bytes()
        with self.assertRaises(native.NativeSchemaError):
            native.migrate_database(self.path)
        with self.assertRaises(native.NativeSchemaError), native.native_session(self.path):
            pass
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(FileExistsError):
            native.initialize_database(self.path)

    def test_missing_read_does_not_create_database(self):
        with self.assertRaises(sqlite3.OperationalError), native.native_session(self.path):
            pass
        self.assertFalse(self.path.exists())

    def test_initialization_failure_does_not_publish_partial_schema(self):
        bad = native.Migration(native.MIGRATIONS[-1].version + 1, "broken", ("CREATE TABLE unfinished(id INTEGER)", "INSERT INTO missing VALUES (1)"))
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS + (bad,)):
            with self.assertRaises(sqlite3.OperationalError):
                native.initialize_database(self.path)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_publish_race_never_replaces_competing_file(self):
        publish = native.publish_new_file

        def competing_publish(staged, destination):
            destination.write_bytes(b"competitor")
            publish(staged, destination)

        with patch.object(native, "publish_new_file", competing_publish):
            with self.assertRaises(FileExistsError):
                native.initialize_database(self.path)
        self.assertEqual(self.path.read_bytes(), b"competitor")
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_up_to_date_reads_and_migrate_leave_file_unchanged(self):
        native.initialize_database(self.path)
        before = hashlib.sha256(self.path.read_bytes()).digest()
        with native.native_session(self.path) as connection:
            connection.execute("SELECT * FROM companies").fetchall()
        result = native.migrate_database(self.path)
        self.assertEqual(result.applied_versions, ())
        self.assertIsNone(result.backup_path)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), before)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_upgrade_backs_up_committed_state_while_blocking_other_writers(self):
        native.initialize_database(self.path)
        migration = native.Migration(native.MIGRATIONS[-1].version + 1, "example_upgrade", ("CREATE TABLE upgrade_example(value TEXT)",))
        observed = []

        def checked_backup(source, destination):
            with closing(sqlite3.connect(source, isolation_level=None, timeout=0)) as competitor, competitor:
                with self.assertRaises(sqlite3.OperationalError):
                    competitor.execute("BEGIN IMMEDIATE")
            observed.append(True)
            return backup_database(source, destination)

        with patch.object(native, "MIGRATIONS", native.MIGRATIONS + (migration,)):
            with self.assertRaisesRegex(native.NativeSchemaError, "upgrade is required"), native.native_session(self.path):
                pass
            with patch.object(native, "backup_database", checked_backup):
                result = native.migrate_database(self.path)
            with native.native_session(self.path) as connection:
                self.assertEqual(native.require_native(connection), migration.version)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM upgrade_example").fetchone()[0], 0)
        self.assertEqual(observed, [True])
        self.assertEqual(result.applied_versions, (migration.version,))
        self.assertTrue(result.backup_path.is_file())
        with session(result.backup_path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], native.MIGRATIONS[-1].version)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='upgrade_example'").fetchone())

    def test_all_pending_migrations_roll_back_together_and_backup_survives_failure(self):
        native.initialize_database(self.path)
        before = hashlib.sha256(self.path.read_bytes()).digest()
        good = native.Migration(native.MIGRATIONS[-1].version + 1, "first_pending", ("CREATE TABLE upgrade_example(value TEXT)",))
        bad = native.Migration(native.MIGRATIONS[-1].version + 2, "second_pending", ("INSERT INTO missing VALUES ('x')",))
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS + (good, bad)):
            with self.assertRaises(sqlite3.OperationalError):
                native.migrate_database(self.path)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), before)
        self.assertEqual(len(list(self.root.glob("*.preupgrade-*.sqlite3"))), 1)
        with native.native_session(self.path) as connection:
            self.assertEqual(native.require_native(connection), native.MIGRATIONS[-1].version)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='upgrade_example'").fetchone())

    def test_backup_failure_prevents_upgrade(self):
        native.initialize_database(self.path)
        before = self.path.read_bytes()
        migration = native.Migration(native.MIGRATIONS[-1].version + 1, "pending", ("CREATE TABLE upgrade_example(value TEXT)",))
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS + (migration,)):
            with patch.object(native, "backup_database", side_effect=OSError("synthetic backup failure")):
                with self.assertRaises(OSError):
                    native.migrate_database(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_altered_migration_history_is_rejected(self):
        native.initialize_database(self.path)
        with session(self.path, readonly=False) as connection:
            connection.execute("DROP TRIGGER schema_migrations_immutable_update")
            connection.execute("UPDATE schema_migrations SET checksum=?", ("0" * 64,))
        with self.assertRaisesRegex(native.NativeSchemaError, "checksum"), native.native_session(self.path):
            pass
        with self.assertRaisesRegex(native.NativeSchemaError, "checksum"):
            native.migrate_database(self.path)

    def test_newer_native_schema_is_not_opened_or_downgraded(self):
        future = native.Migration(native.MIGRATIONS[-1].version + 1, "future", ("CREATE TABLE future_example(value TEXT)",))
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS + (future,)):
            native.initialize_database(self.path)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(native.NativeSchemaError, "Unsupported"), native.native_session(self.path):
            pass
        with self.assertRaisesRegex(native.NativeSchemaError, "Unsupported"):
            native.migrate_database(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_user_version_must_match_checked_migration_history(self):
        native.initialize_database(self.path)
        with session(self.path, readonly=False) as connection:
            connection.execute(f"PRAGMA user_version={native.MIGRATIONS[-1].version + 1}")
        with self.assertRaisesRegex(native.NativeSchemaError, "version.*disagree"), native.native_session(self.path):
            pass


class NativeInvariantTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "native.sqlite3"
        native.initialize_database(self.path)
        self.company, self.other_company, self.actor, self.unit, self.product, self.store = [new_id() for _ in range(6)]
        self.debit_account, self.credit_account = new_id(), new_id()
        with native.native_transaction(self.path) as connection:
            for identifier, code in ((self.company, "A"), (self.other_company, "B")):
                connection.execute("INSERT INTO companies(id,code,name) VALUES (?,?,?)", (identifier, code, "Synthetic " + code))
            connection.execute("INSERT INTO users(id,username,display_name,password_hash) VALUES (?,?,?,?)",
                               (self.actor, "synthetic", "Synthetic user", "hash-placeholder"))
            connection.execute("INSERT INTO units(id,code,name) VALUES (?,?,?)", (self.unit, "PC", "Piece"))
            for identifier, code in ((self.debit_account, "001"), (self.credit_account, "002")):
                connection.execute("INSERT INTO accounts(id,code,name) VALUES (?,?,?)", (identifier, code, "Synthetic account " + code))
            connection.execute("INSERT INTO products(id,code,name,small_unit_id) VALUES (?,?,?,?)",
                               (self.product, "0001", "Synthetic product", self.unit))
            connection.execute("INSERT INTO stores(id,code,name) VALUES (?,?,?)", (self.store, "S", "Synthetic store"))
            connection.execute("INSERT INTO company_stores(company_id,store_id) VALUES (?,?)", (self.company, self.store))

    def document(self, connection, *, kind="sale", amount="1", company=None):
        identifier = new_id()
        connection.execute("INSERT INTO documents(id,company_id,kind,document_number,business_date,total_amount,rounding_policy,posting_places,created_by) "
                           "VALUES (?,?,?,?,?,?,?,?,?)", (identifier, company or self.company, kind, identifier, "2026-10-02", amount, "cents", 2, self.actor))
        return identifier

    def item_line(self, connection, document, *, company=None, quantity="1"):
        identifier = new_id()
        connection.execute("INSERT INTO document_lines(id,company_id,document_id,line_number,product_id,store_id,quantity) VALUES (?,?,?,?,?,?,?)",
                           (identifier, company or self.company, document, 1, self.product, self.store, quantity))
        return identifier

    def journal(self, connection, document, *, debit="1", credit="1"):
        entry, first, second = new_id(), new_id(), new_id()
        connection.execute("INSERT INTO journal_entries(id,company_id,document_id) VALUES (?,?,?)", (entry, self.company, document))
        for line, number, account, dr, cr in ((first, 1, self.debit_account, debit, "0"), (second, 2, self.credit_account, "0", credit)):
            connection.execute("INSERT INTO journal_lines(id,company_id,entry_id,line_number,account_id,debit,credit) VALUES (?,?,?,?,?,?,?)",
                               (line, self.company, entry, number, account, dr, cr))
        return entry, first, second

    def post(self, connection, document):
        connection.execute("UPDATE documents SET status='posted',posted_by=?,posted_at=1,revision=revision+1 WHERE id=?", (self.actor, document))

    def test_privilege_defaults_deny_and_grant_foreign_keys_are_enforced(self):
        with native.native_transaction(self.path) as connection:
            user = connection.execute("SELECT is_admin,is_active,failed_attempts FROM users WHERE id=?", (self.actor,)).fetchone()
            self.assertEqual(tuple(user), (0, 0, 0))
            connection.execute("INSERT INTO user_menu_grants(user_id,module) VALUES (?,?)", (self.actor, "sales"))
            row = connection.execute("SELECT can_read,can_create,can_update,can_delete,can_post FROM user_menu_grants").fetchone()
            self.assertEqual(tuple(row), (0, 0, 0, 0, 0))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO user_company_grants(user_id,company_id) VALUES (?,?)", (new_id(), self.company))

    def test_company_boundaries_and_shared_store_memberships(self):
        with native.native_transaction(self.path) as connection:
            document = self.document(connection)
            with self.assertRaises(sqlite3.IntegrityError):
                self.item_line(connection, document, company=self.other_company)
            other = self.document(connection, company=self.other_company)
            with self.assertRaises(sqlite3.IntegrityError):
                self.item_line(connection, other, company=self.other_company)
            connection.execute("INSERT INTO company_stores(company_id,store_id) VALUES (?,?)", (self.other_company, self.store))
            self.item_line(connection, other, company=self.other_company)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO journal_entries(id,company_id,document_id) VALUES (?,?,?)", (new_id(), self.other_company, document))

    def test_canonical_decimals_preserve_large_values_and_reject_invalid_forms(self):
        with native.native_transaction(self.path) as connection:
            document = self.document(connection, amount="9007199254740993.01")
            self.assertEqual(connection.execute("SELECT total_amount FROM documents WHERE id=?", (document,)).fetchone()[0], "9007199254740993.01")
            for value in ("NaN", "Infinity", "1e2", "-1", "+1", "01", "1.0", ".1", "0.01.2", "1.001", " 1", "1 "):
                with self.subTest(value=value), self.assertRaises(sqlite3.IntegrityError):
                    self.document(connection, amount=value)
            for value in ("0", "-1", "1.0000001"):
                with self.subTest(quantity=value), self.assertRaises(sqlite3.IntegrityError):
                    self.item_line(connection, document, quantity=value)

    def test_business_dates_reject_year_zero_invalid_day_and_accept_leap_day(self):
        with native.native_transaction(self.path) as connection:
            document = self.document(connection)
            for value in ("0000-01-01", "2025-02-29", "2026-04-31", "2026-01-01T00:00:00"):
                with self.subTest(value=value), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("UPDATE documents SET business_date=?,revision=revision+1 WHERE id=?", (value, document))
            connection.execute("UPDATE documents SET business_date='2024-02-29',revision=revision+1 WHERE id=?", (document,))

    def test_subtotal_rounding_and_final_posting_places_are_independent_explicit_choices(self):
        with native.native_transaction(self.path) as connection:
            document = self.document(connection)
            connection.execute("UPDATE documents SET rounding_policy='legacy_whole',posting_places=2,revision=revision+1 WHERE id=?", (document,))
            row = connection.execute("SELECT rounding_policy,posting_places FROM documents WHERE id=?", (document,)).fetchone()
            self.assertEqual(tuple(row), ("legacy_whole", 2))
            connection.execute("UPDATE documents SET rounding_policy='cents',posting_places=0,revision=revision+1 WHERE id=?", (document,))
            for places in (None, 1, 3):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("UPDATE documents SET posting_places=?,revision=revision+1 WHERE id=?", (places, document))

    def test_nonzero_financial_posting_requires_exact_balanced_journal(self):
        with native.native_transaction(self.path) as connection:
            document = self.document(connection, amount="9007199254740993.01")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "requires a journal"):
                self.post(connection, document)
            _, _, credit = self.journal(connection, document, debit="9007199254740993.01", credit="9007199254740993")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "balance exactly"):
                self.post(connection, document)
            connection.execute("UPDATE journal_lines SET credit='9007199254740993.01' WHERE id=?", (credit,))
            self.post(connection, document)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM posted_journal_lines").fetchone()[0], 2)

    def test_exact_sum_balances_three_lines_without_binary_float_error(self):
        with native.native_transaction(self.path) as connection:
            document = self.document(connection, amount="0.3")
            entry, _, _ = self.journal(connection, document, debit="0.1", credit="0.3")
            connection.execute("INSERT INTO journal_lines(id,company_id,entry_id,line_number,account_id,debit,credit) VALUES (?,?,?,?,?,?,?)",
                               (new_id(), self.company, entry, 3, self.debit_account, "0.2", "0"))
            self.post(connection, document)

    def test_journal_lines_are_one_sided_and_post_requires_at_least_two(self):
        with native.native_transaction(self.path) as connection:
            document = self.document(connection)
            entry = new_id()
            connection.execute("INSERT INTO journal_entries(id,company_id,document_id) VALUES (?,?,?)", (entry, self.company, document))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "at least two"):
                self.post(connection, document)
            for debit, credit in (("1", "1"), ("0", "0"), ("-1", "0")):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("INSERT INTO journal_lines(id,company_id,entry_id,line_number,account_id,debit,credit) VALUES (?,?,?,?,?,?,?)",
                                       (new_id(), self.company, entry, 1, self.debit_account, debit, credit))

    def test_journal_number_capacity_includes_full_invoice_counterpart_and_adjustments(self):
        with native.native_transaction(self.path) as connection:
            document = self.document(connection)
            _, _, credit = self.journal(connection, document)
            for number in (10001, 20000):
                connection.execute("UPDATE journal_lines SET line_number=? WHERE id=?", (number, credit))
            for number in (0, 20001):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("UPDATE journal_lines SET line_number=? WHERE id=?", (number, credit))
            self.post(connection, document)

    def test_zero_value_goods_and_stock_only_documents_can_post_without_journal(self):
        with native.native_transaction(self.path) as connection:
            for kind, amount in (("sale", "0"), ("purchase", "0"), ("sale_return", "0"), ("delivery_challan", "12"), ("stock_adjustment", "0")):
                document = self.document(connection, kind=kind, amount=amount)
                self.post(connection, document)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0], 0)

    def test_posted_documents_and_all_components_are_immutable_including_replace(self):
        with native.native_transaction(self.path) as connection:
            document = self.document(connection)
            line = self.item_line(connection, document)
            entry, debit, _ = self.journal(connection, document)
            stock = new_id()
            connection.execute("INSERT INTO stock_movements(id,company_id,document_id,document_line_id,product_id,store_id,direction,quantity) VALUES (?,?,?,?,?,?,?,?)",
                               (stock, self.company, document, line, self.product, self.store, "out", "1"))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM posted_stock_movements").fetchone()[0], 0)
            self.post(connection, document)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM posted_stock_movements").fetchone()[0], 1)
            for table, identifier, update in (("documents", document, "notes='changed',revision=revision+1"),
                    ("document_lines", line, "quantity='2'"), ("journal_entries", entry, "description='changed'"),
                    ("journal_lines", debit, "debit='2'"), ("stock_movements", stock, "quantity='2'")):
                with self.subTest(table=table):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(f"UPDATE {table} SET {update} WHERE id=?", (identifier,))
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(f"DELETE FROM {table} WHERE id=?", (identifier,))
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM {table} WHERE id=?", (identifier,))

    def test_posted_parent_rejects_new_child_and_draft_origin_cannot_move(self):
        with native.native_transaction(self.path) as connection:
            document = self.document(connection, amount="0")
            line = self.item_line(connection, document)
            other = self.document(connection, amount="0")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "origin is immutable"):
                connection.execute("UPDATE document_lines SET document_id=? WHERE id=?", (other, line))
            self.post(connection, other)
            with self.assertRaises(sqlite3.IntegrityError):
                self.item_line(connection, other)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO journal_entries(id,company_id,document_id) VALUES (?,?,?)", (new_id(), self.company, other))

    def test_stock_origin_product_and_company_membership_are_enforced(self):
        with native.native_transaction(self.path) as connection:
            document = self.document(connection, amount="0")
            line = self.item_line(connection, document)
            for company, product, origin in ((self.other_company, self.product, line), (self.company, new_id(), line), (self.company, self.product, new_id())):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("INSERT INTO stock_movements(id,company_id,document_id,document_line_id,product_id,store_id,direction,quantity) VALUES (?,?,?,?,?,?,?,?)",
                                       (new_id(), company, document, origin, product, self.store, "in", "1"))

    def test_reversal_origin_is_same_company_posted_and_not_repeated(self):
        with native.native_transaction(self.path) as connection:
            original = self.document(connection, amount="0")
            reversal = self.document(connection, amount="0", kind="reversal")
            connection.execute("UPDATE documents SET reverses_document_id=?,revision=revision+1 WHERE id=?", (original, reversal))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "reference a posted document"):
                self.post(connection, reversal)
            self.post(connection, original)
            self.post(connection, reversal)
            duplicate = self.document(connection, amount="0", kind="reversal")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE documents SET reverses_document_id=?,revision=revision+1 WHERE id=?", (original, duplicate))
            wrong = self.document(connection, company=self.other_company, amount="0", kind="reversal")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE documents SET reverses_document_id=?,revision=revision+1 WHERE id=?", (original, wrong))

    def test_audit_is_append_only_and_plain_backup_still_checks_integrity(self):
        with native.native_transaction(self.path) as connection:
            event = new_id()
            connection.execute("INSERT INTO audit_events(id,actor_id,company_id,action,entity_type) VALUES (?,?,?,?,?)",
                               (event, self.actor, self.company, "synthetic.action", "test"))
            for statement in ("UPDATE audit_events SET action='changed'", "DELETE FROM audit_events", "INSERT OR REPLACE INTO audit_events SELECT * FROM audit_events"):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM users WHERE id=?", (self.actor,))
        backup = Path(self.directory.name) / "backup.sqlite3"
        backup_database(self.path, backup)
        with native.native_session(backup) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0], 1)

    def test_revision_compare_and_transaction_failure_leave_no_partial_posting(self):
        with native.native_transaction(self.path) as connection:
            document = self.document(connection)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "revision"):
                connection.execute("UPDATE documents SET notes='change' WHERE id=?", (document,))
            changed = connection.execute("UPDATE documents SET notes='first',revision=revision+1 WHERE id=? AND revision=0", (document,)).rowcount
            stale = connection.execute("UPDATE documents SET notes='stale',revision=revision+1 WHERE id=? AND revision=0", (document,)).rowcount
            self.assertEqual((changed, stale), (1, 0))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "balance exactly"):
            with native.native_transaction(self.path) as connection:
                self.journal(connection, document, debit="2", credit="1")
                self.post(connection, document)
        with native.native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT status FROM documents WHERE id=?", (document,)).fetchone()[0], "draft")


if __name__ == "__main__":
    unittest.main()
