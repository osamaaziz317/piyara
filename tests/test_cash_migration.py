"""Version-four generalized table rebuild against populated synthetic history."""

from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma import native_schema as native
from bifma.database import session
from bifma.transactions import TransactionService, _json
from tests.test_transactions import TransactionFixture


BASELINE_CHECKSUMS = (
    "4fe764da2b83db268aafaad4d191f5bbc2840bc7bd141f24f64205d96b96c462",
    "ec12d8a891bf47ad17e910144f5bff74c64a43b9dd900869b5abf7f3ed60245a",
    "aefeaaf2c5f74d5cb25bb0d2fe787be7d125b60fd6ebd443b39c5fa9566d6d95",
)


class CashMigrationTests(TransactionFixture, unittest.TestCase):
    def setUp(self):
        # Create and populate the preceding schema through its real checked
        # services; no downgrade, catalog edit, or copied production data. The
        # current service also queries new baseline/valuation components. Empty
        # TEMP stand-ins model their absence while creating this old-format test
        # fixture, without adding anything to the version-three main schema.
        prepare = native.prepare_connection
        prepare_document = TransactionService._prepare
        def old_prepare_document(service, *args, **kwargs):
            result = prepare_document(service, *args, **kwargs)
            for line in result["lines"]:
                self.assertIsNone(line.pop("historical_source_line_id", None))
            return result
        def old_write_metadata(connection, company, identifier, prepared):
            connection.execute("INSERT INTO document_metadata(document_id,company_id,input_json,snapshot_json,calculation_json) VALUES(?,?,?,?,?)",
                               (identifier, company, _json(prepared["values"]), _json(prepared["snapshot"]), _json(prepared["calculation"])))
            for line in prepared["lines"]:
                connection.execute("INSERT INTO document_lines(id,company_id,document_id,line_number,product_id,store_id,quantity,rate,discount_percent,net_amount,source_line_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                   (line["id"], company, identifier, line["line_number"], line["product_id"], line["store_id"], line["quantity"], line["rate"], line["discount_percent"], line["net_amount"], line["source_line_id"]))
        def old_fixture_connection(connection):
            prepare(connection)
            connection.execute("CREATE TEMP TABLE IF NOT EXISTS baseline_imports(singleton,cutoff_date)")
            connection.execute("CREATE TEMP TABLE IF NOT EXISTS baseline_document_numbers(company_id,kind,document_number)")
            connection.execute("CREATE TEMP TABLE IF NOT EXISTS baseline_stock_balances(company_id,store_id,product_id,pool,quantity)")
            connection.execute("CREATE TEMP TABLE IF NOT EXISTS baseline_purchase_rates(company_id,product_id,rate,rate_source,source_id,source_json)")
            connection.execute("CREATE TEMP TABLE IF NOT EXISTS stock_valuation_snapshots(movement_id,unit_cost,value,source_json)")
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS[:3]), patch.object(native, "prepare_connection", old_fixture_connection), \
                patch.object(TransactionService, "_prepare", old_prepare_document), \
                patch.object(TransactionService, "_write_metadata", staticmethod(old_write_metadata)):
            super().setUp()
            self.opening()
            self.sale = self.post(self.save("sale", self.trade_values(quantity="2")))
            values = self.trade_values("sale_return", source=self.sale["lines"][0]["id"])
            self.returned = self.post(self.save("sale_return", values))
            purchase = self.post(self.save("purchase", self.trade_values("purchase")))
            self.reversed = self.reverse(purchase)
            self.draft = self.save("sale", self.trade_values())
            self.before = self.content()

    def content(self, path=None):
        with session(path or self.path) as connection:
            if not hasattr(self, "original_columns"):
                tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'schema_migrations' ORDER BY name")]
                self.original_columns = {table: [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')] for table in tables}
            return {table: [tuple(row) for row in connection.execute(f'SELECT rowid,{",".join(columns)} FROM "{table}" ORDER BY rowid')]
                    for table, columns in self.original_columns.items()}

    def test_v1_v2_v3_checksums_are_frozen_and_public_kind_set_expands_only_v4(self):
        self.assertEqual(tuple(m.checksum for m in native.MIGRATIONS[:3]), BASELINE_CHECKSUMS)
        self.assertEqual(native.MIGRATIONS[3].name, "distinct_cash_trade_document_namespaces")
        self.assertIn("cash_sale", native.DOCUMENT_KINDS)
        self.assertFalse(any("cash_sale" in statement for statement in native.MIGRATIONS[0].statements))
        for version in (1, 2):
            path = self.path.parent / f"old-{version}.sqlite3"
            with patch.object(native, "MIGRATIONS", native.MIGRATIONS[:version]):
                native.initialize_database(path)
            upgraded = native.migrate_database(path)
            self.assertEqual(upgraded.applied_versions, tuple(range(version + 1, native.MIGRATIONS[-1].version + 1)))
            with native.native_session(path) as connection:
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_populated_upgrade_preserves_all_rows_snapshots_rowids_and_preupgrade_backup(self):
        with self.assertRaisesRegex(native.NativeSchemaError, "upgrade is required"), native.native_session(self.path):
            pass
        result = native.migrate_database(self.path)
        self.assertEqual((result.version, result.applied_versions), (native.MIGRATIONS[-1].version, tuple(m.version for m in native.MIGRATIONS[3:])))
        self.assertEqual(self.content(), self.before)
        self.assertEqual(self.content(result.backup_path), self.before)
        with session(result.backup_path) as connection:
            native.prepare_connection(connection)
            self.assertEqual(native.require_native(connection, require_current=False), 3)
        with native.native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM baseline_imports").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM stock_valuation_snapshots").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT DISTINCT sale_rate_known,purchase_rate_known FROM products").fetchone()[:], (1, 1))
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM posted_stock_movements").fetchone()[0], 5)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM posted_journal_lines").fetchone()[0], 10)
            self.assertEqual(tuple(row[2] for row in connection.execute("PRAGMA foreign_key_list(documents)")), ("documents", "documents", "users", "users", "accounts", "companies"))

    def test_rebuilt_table_keeps_foreign_keys_posted_guards_and_reversal_links(self):
        native.migrate_database(self.path)
        with native.native_transaction(self.path) as connection:
            for table, key, identifier in (("documents", "id", self.sale["id"]),
                    ("document_lines", "id", self.sale["lines"][0]["id"]),
                    ("document_metadata", "document_id", self.sale["id"]),
                    ("journal_lines", "id", self.sale["journal_lines"][0]["id"]),
                    ("stock_movements", "id", self.sale["stock_movements"][0]["id"])):
                for sql in (f"DELETE FROM {table} WHERE {key}=?", f"INSERT OR REPLACE INTO {table} SELECT * FROM {table} WHERE {key}=?"):
                    with self.subTest(table=table, sql=sql), self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(sql, (identifier,))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE documents SET account_id=?,revision=revision+1 WHERE id=?", (str(uuid4()), self.draft["id"]))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE document_metadata SET company_id=? WHERE document_id=?", (self.other_company, self.draft["id"]))
            self.assertEqual(connection.execute("SELECT reverses_document_id FROM documents WHERE id=?", (self.reversed["id"],)).fetchone()[0], self.reversed["reverses_document_id"])
        # A draft created under v3 can still be posted without reclassification.
        posted = self.post(self.draft)
        self.assertEqual(posted["kind"], "sale")
        self.assertNotIn("cash_settlement", posted["snapshot"])

    def test_rebuild_holds_write_lock_and_existing_number_namespace_remains(self):
        backup = native.backup_database
        observed = []

        def locked_backup(source, destination):
            with closing(sqlite3.connect(source, isolation_level=None, timeout=0)) as competitor:
                with self.assertRaises(sqlite3.OperationalError):
                    competitor.execute("BEGIN IMMEDIATE")
            observed.append(True)
            return backup(source, destination)

        with patch.object(native, "backup_database", locked_backup):
            native.migrate_database(self.path)
        self.assertEqual(observed, [True])
        with native.native_transaction(self.path) as connection:
            for kind in ("cash_sale", "cash_purchase", "cash_sale_return", "cash_purchase_return"):
                parameters = (str(uuid4()), self.company, kind, self.sale["document_number"], "2026-01-02", self.actor.user.id)
                connection.execute("INSERT INTO documents(id,company_id,kind,document_number,business_date,created_by,rounding_policy,posting_places) VALUES(?,?,?,?,?,?,'cents',2)", parameters)
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("INSERT INTO documents(id,company_id,kind,document_number,business_date,created_by,rounding_policy,posting_places) VALUES(?,?,?,?,?,?,'cents',2)", (str(uuid4()), *parameters[1:]))

    def test_later_failure_rolls_back_entire_rebuild_and_preserves_verified_backup(self):
        before_bytes = self.path.read_bytes()
        broken = native.Migration(native.MIGRATIONS[-1].version + 1, "synthetic_failure", ("INSERT INTO absent_table VALUES ('failure')",))
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS + (broken,)), self.assertRaises(sqlite3.OperationalError):
            native.migrate_database(self.path)
        self.assertEqual(self.path.read_bytes(), before_bytes)
        self.assertEqual(self.content(), self.before)
        backups = list(self.path.parent.glob("*.preupgrade-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.content(backups[0]), self.before)
        self.assertEqual(native.open_native_database(self.path, require_current=False), 3)

    def test_foreign_key_check_before_commit_rejects_bad_rebuild_effects(self):
        before = self.path.read_bytes()
        orphan = str(uuid4())
        bad = native.Migration(native.MIGRATIONS[-1].version + 1, "synthetic_orphan", (f"UPDATE documents SET account_id='{orphan}',revision=revision+1 WHERE id='{self.draft['id']}'",))
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS + (bad,)), self.assertRaisesRegex(native.NativeSchemaError, "integrity"):
            native.migrate_database(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.content(), self.before)

    def test_fk_suspension_is_private_and_restored_after_failure(self):
        with session(self.path, readonly=False) as connection:
            native.prepare_connection(connection)
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                with native._migration_transaction(connection, native.MIGRATIONS[3:]):
                    self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 0)
                    with self.assertRaisesRegex(native.NativeSchemaError, "enforce foreign keys"):
                        native.require_native(connection, require_current=False)
                    raise RuntimeError("synthetic migration failure")
            self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(native.require_native(connection, require_current=False), 3)

    def test_actual_process_crash_during_rebuild_recovers_v3_and_can_upgrade(self):
        child = r'''
import os, sys
from bifma import native_schema as native
path = sys.argv[1]
def interrupted(connection, migrations):
    connection.execute("PRAGMA cache_size=1")
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    for migration in migrations:
        for statement in migration.statements:
            connection.execute(statement)
            if statement == "DROP TABLE documents":
                assert connection.in_transaction
                assert connection.execute("SELECT COUNT(*) FROM documents_cash_v4").fetchone()[0] > 0
                with open(path + "-journal", "rb") as journal:
                    assert any(journal.read(8)), "fault must leave a real hot journal"
                os._exit(74)
    raise AssertionError("did not reach rebuild interruption")
native._apply = interrupted
native.migrate_database(path)
'''
        result = subprocess.run([sys.executable, "-B", "-c", child, str(self.path)], cwd=Path(__file__).resolve().parent.parent,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        self.assertEqual(result.returncode, 74, result.stderr)
        self.assertTrue(Path(str(self.path) + "-journal").exists())
        self.assertEqual(native.open_native_database(self.path, require_current=False), 3)
        self.assertEqual(self.content(), self.before)
        backups = list(self.path.parent.glob("*.preupgrade-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.content(backups[0]), self.before)
        self.assertEqual(native.migrate_database(self.path).applied_versions, tuple(m.version for m in native.MIGRATIONS[3:]))
        self.assertEqual(self.content(), self.before)


if __name__ == "__main__":
    unittest.main()
