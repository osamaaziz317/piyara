"""Preserve populated version-five files while enabling short login names."""

from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma import native_schema as native
from bifma.baseline import BaselineError, baseline_info, ensure_baseline, prepare_native_login
from bifma.database import session
from bifma.security import SecurityService
from bifma.transactions import TransactionService
from tests.test_baseline_import import synthetic_archive


PREVIOUS_CHECKSUMS = (
    "4fe764da2b83db268aafaad4d191f5bbc2840bc7bd141f24f64205d96b96c462",
    "ec12d8a891bf47ad17e910144f5bff74c64a43b9dd900869b5abf7f3ed60245a",
    "aefeaaf2c5f74d5cb25bb0d2fe787be7d125b60fd6ebd443b39c5fa9566d6d95",
    "880846a7358f0ba267eb7b80a700c3bc92c34619c95cd682ac1ce433f5677b4a",
    "2d1317e605cade9c8580ade9ab5cc23c3b28942c0e6c26e6cf3ed99670c33b41",
)


class UsernameMigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "working.sqlite3"
        self.source = self.root / "source.sqlite3"
        synthetic_archive(self.source)
        self.clock = lambda: 1_800_000_000
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS[:5]):
            self.baseline = ensure_baseline(self.source, self.path)
            security = SecurityService(self.path, clock=self.clock)
            self.owner = security.bootstrap_admin("administrator", "Existing private password 99", display_name="Existing Owner")
            self.staff = security.create_user(self.owner, "existing.staff", "Existing staff password 98", display_name="Existing Staff")
            with native.native_session(self.path) as connection:
                self.company = connection.execute("SELECT id FROM companies").fetchone()[0]
                accounts = dict(connection.execute("SELECT code,id FROM accounts"))
            security.set_company_access(self.owner, self.staff.id, [self.company])
            security.set_permissions(self.owner, self.staff.id, "receipts", ["read", "create"])
            self.staff_actor = security.authenticate("existing.staff", "Existing staff password 98")
            transactions = TransactionService(self.path, security=security, clock=self.clock)
            values = {"document_number": "000027", "business_date": "2026-01-04",
                      "rounding_policy": "cents", "posting_places": 2,
                      "account_id": accounts["0001"], "notes": "Preserve exact saved values",
                      "lines": [{"account_id": accounts["0002"], "amount": "12.25", "narration": "اصل"}]}
            draft = transactions.save_draft(self.owner, self.company, "cash_receipt", values)
            self.posted = transactions.post(self.owner, self.company, draft["id"], draft["revision"])
            inactive = security.create_user(self.owner, "locked.staff", "Saved locked account password 97")
            with native.native_transaction(self.path) as connection:
                connection.execute("UPDATE users SET is_active=0,failed_attempts=4,locked_until=? WHERE id=?",
                                   (self.clock() + 900, inactive.id))
            self.before = self.content()
            self.catalog = self.other_catalog()

    def content(self, path=None):
        with session(path or self.path) as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name<>'schema_migrations' ORDER BY name")]
            return {table: [tuple(row) for row in connection.execute(f'SELECT rowid,* FROM "{table}" ORDER BY rowid')]
                    for table in tables}

    def other_catalog(self):
        with session(self.path) as connection:
            return [tuple(row) for row in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE tbl_name<>'users' ORDER BY type,name")]

    def test_previous_specs_frozen_and_new_constraints_only_lower_minimum(self):
        self.assertEqual(tuple(migration.checksum for migration in native.MIGRATIONS[:5]), PREVIOUS_CHECKSUMS)
        result = native.migrate_database(self.path)
        self.assertEqual((result.version, result.applied_versions), (6, (6,)))
        with native.native_transaction(self.path) as connection:
            for username in ("a", "ab", "u" * 80):
                connection.execute("INSERT INTO users(id,username,display_name,password_hash) VALUES (?,?,'Name','hash')",
                                   (str(uuid4()), username))
            for username in ("", "x" * 81, "Uppercase", "has space", "م"):
                with self.subTest(username=username), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("INSERT INTO users(id,username,display_name,password_hash) VALUES (?,?,'Name','hash')",
                                       (str(uuid4()), username))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO users(id,username,display_name,password_hash) VALUES (?,'a','Name','hash')",
                                   (str(uuid4()),))

    def test_populated_upgrade_preserves_rows_references_hashes_seals_and_backup(self):
        source_bytes = self.source.read_bytes()
        result = native.migrate_database(self.path)
        self.assertEqual(self.content(), self.before)
        self.assertEqual(self.content(result.backup_path), self.before)
        self.assertEqual(self.other_catalog(), self.catalog)
        self.assertEqual(baseline_info(self.path).manifest, self.baseline.manifest)
        self.assertEqual(self.source.read_bytes(), source_bytes)
        with session(result.backup_path) as connection:
            native.prepare_connection(connection)
            self.assertEqual(native.require_native(connection, require_current=False), 5)
        with native.native_transaction(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            for table in ("user_company_grants", "user_menu_grants", "auth_sessions", "documents", "audit_events"):
                self.assertIn("users", {row[2] for row in connection.execute(f"PRAGMA foreign_key_list({table})")})
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM users WHERE id=?", (self.owner.user.id,))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE documents SET notes='Changed' WHERE id=?", (self.posted["id"],))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM baseline_stock_balances")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM audit_events")
        security = SecurityService(self.path, clock=self.clock)
        self.assertEqual(security.current_user(self.staff_actor).id, self.staff.id)
        self.assertTrue(security.can(self.staff_actor, self.company, "receipts", "read"))
        self.assertEqual(security.authenticate("administrator", "Existing private password 99").user.id, self.owner.user.id)

    def test_failure_rolls_back_entire_rebuild_with_unchanged_preupgrade_backup(self):
        before = self.path.read_bytes()
        failure = native.Migration(7, "synthetic_failure", ("INSERT INTO missing_table VALUES (1)",))
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS + (failure,)), self.assertRaises(sqlite3.OperationalError):
            native.migrate_database(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.content(), self.before)
        backups = list(self.root.glob("*.preupgrade-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.content(backups[0]), self.before)
        self.assertEqual(native.open_native_database(self.path, require_current=False), 5)

    def test_fk_suspension_restored_and_orphaned_sessions_prevent_commit(self):
        with session(self.path, readonly=False) as connection:
            native.prepare_connection(connection)
            with self.assertRaisesRegex(RuntimeError, "Abort synthetic"):
                with native._migration_transaction(connection, native.MIGRATIONS[5:]):
                    self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 0)
                    raise RuntimeError("Abort synthetic rebuild")
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertFalse(connection.in_transaction)
        before = self.path.read_bytes()
        orphan = native.Migration(7, "synthetic_orphan", (f"UPDATE auth_sessions SET user_id='{uuid4()}'",))
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS + (orphan,)), self.assertRaisesRegex(native.NativeSchemaError, "integrity"):
            native.migrate_database(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.content(), self.before)

    def test_default_startup_opt_in_upgrades_matching_baseline_once(self):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(native.NativeSchemaError, "upgrade is required"):
            ensure_baseline(self.source, self.path)
        self.assertEqual(self.path.read_bytes(), before)
        result = ensure_baseline(self.source, self.path, upgrade=True)
        self.assertEqual(result.manifest, self.baseline.manifest)
        self.assertFalse(result.created)
        self.assertEqual(self.content(), self.before)
        self.assertEqual(len(list(self.root.glob("*.preupgrade-*.sqlite3"))), 1)
        upgraded = self.path.read_bytes()
        ensure_baseline(self.source, self.path, upgrade=True)
        self.assertEqual(self.path.read_bytes(), upgraded)
        self.assertEqual(len(list(self.root.glob("*.preupgrade-*.sqlite3"))), 1)

    def test_competing_upgrade_between_initial_check_and_fallback_reuses_current_file(self):
        from bifma import baseline
        opener = baseline.open_native_database
        completed = []

        def competing_upgrade(path, *, require_current=True):
            if not require_current and not completed:
                completed.append(native.migrate_database(path))
            return opener(path, require_current=require_current)

        with patch.object(baseline, "open_native_database", competing_upgrade):
            result = ensure_baseline(self.source, self.path, upgrade=True)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].applied_versions, (6,))
        self.assertEqual(result.manifest, self.baseline.manifest)
        self.assertFalse(result.created)
        self.assertEqual(self.content(), self.before)
        self.assertEqual(len(list(self.root.glob("*.preupgrade-*.sqlite3"))), 1)

    def test_wrong_source_unrelated_native_and_older_schema_refused_before_upgrade(self):
        before = self.path.read_bytes()
        other_source = self.root / "other-source.sqlite3"
        synthetic_archive(other_source)
        with session(other_source, readonly=False) as connection:
            connection.execute("UPDATE CONFIG SET CMPNAME='Other company'")
        with self.assertRaisesRegex(BaselineError, "does not belong"):
            ensure_baseline(other_source, self.path, upgrade=True)
        self.assertEqual(self.path.read_bytes(), before)
        for version in (4, 5):
            unrelated = self.root / f"unrelated-{version}.sqlite3"
            with patch.object(native, "MIGRATIONS", native.MIGRATIONS[:version]):
                native.initialize_database(unrelated)
            untouched = unrelated.read_bytes()
            expected = native.NativeSchemaError if version == 4 else BaselineError
            with self.assertRaises(expected):
                ensure_baseline(self.source, unrelated, upgrade=True)
            self.assertEqual(unrelated.read_bytes(), untouched)
        self.assertEqual(list(self.root.glob("*.preupgrade-*.sqlite3")), [])

    def test_baseline_change_before_write_lock_is_rechecked_before_backup(self):
        migrate = native.migrate_database
        damaged = []
        def change_then_migrate(path, **kwargs):
            with session(path, readonly=False) as connection:
                connection.execute("DROP TRIGGER baseline_stock_balances_update_guard")
                connection.execute("UPDATE baseline_stock_balances SET quantity='999'")
            damaged.append(Path(path).read_bytes())
            return migrate(path, **kwargs)
        with patch.object(native, "migrate_database", change_then_migrate), self.assertRaisesRegex(BaselineError, "no longer matches"):
            ensure_baseline(self.source, self.path, upgrade=True)
        self.assertEqual(self.path.read_bytes(), damaged[0])
        self.assertEqual(native.open_native_database(self.path, require_current=False), 5)
        self.assertEqual(list(self.root.glob("*.preupgrade-*.sqlite3")), [])

    def test_direct_database_service_startup_upgrades_native_and_checks_existing_seal(self):
        from bifma.native_ui import NativeApp
        app = SimpleNamespace(archive_path=self.source)
        result = NativeApp._services(app, self.path)
        self.assertEqual(result[0], self.path)
        self.assertEqual(result[6].manifest, self.baseline.manifest)
        self.assertEqual(self.content(), self.before)
        plain = self.root / "native-only.sqlite3"
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS[:5]):
            native.initialize_database(plain)
        self.assertIsNone(prepare_native_login(plain))
        self.assertEqual(native.open_native_database(plain), 6)
        self.assertEqual(len(list(self.root.glob("native-only.preupgrade-*.sqlite3"))), 1)
        with native.native_transaction(self.path) as connection:
            connection.execute("DROP TRIGGER baseline_stock_balances_update_guard")
            connection.execute("UPDATE baseline_stock_balances SET quantity='999'")
        damaged = self.path.read_bytes()
        with self.assertRaisesRegex(BaselineError, "no longer matches"):
            NativeApp._services(app, self.path)
        self.assertEqual(self.path.read_bytes(), damaged)


if __name__ == "__main__":
    unittest.main()
