"""Native backup/restore verification with synthetic temporary databases."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma import native_backup as backup
from bifma import native_schema as native
from bifma.database import DatabaseError, require_integrity, session
from bifma.security import SecurityService, AuthenticationError, AuthorizationError


PASSWORD = "Silver rivers flow beyond Saturn 57!"
STAFF_PASSWORD = "Quiet copper lanterns near Jupiter 92!"


class Clock:
    def __init__(self):
        self.now = 1_800_000_000

    def __call__(self):
        return self.now


class NativeBackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "native.sqlite3"
        self.destination = self.root / "result.sqlite3"
        native.initialize_database(self.path)
        self.clock = Clock()
        self.security = SecurityService(self.path, clock=self.clock)
        self.admin = self.security.bootstrap_admin("administrator", PASSWORD)
        self.service = backup.NativeBackupService(self.path, security=self.security, clock=self.clock)
        self.company, self.unit, self.product = (str(uuid4()) for _ in range(3))
        with native.native_transaction(self.path) as connection:
            connection.execute("INSERT INTO companies(id,code,name) VALUES(?, 'A', 'Synthetic company')", (self.company,))
            connection.execute("INSERT INTO units(id,code,name) VALUES(?, 'PC', 'Piece')", (self.unit,))
            connection.execute("INSERT INTO products(id,code,name,small_unit_id,sale_rate) VALUES(?, 'P', 'Synthetic product', ?, ?)",
                               (self.product, self.unit, "9007199254740993.01"))

    def restore(self, *, source=None, username="administrator", password=PASSWORD):
        return backup.restore_backup(source or self.path, self.destination, username, password, clock=self.clock)

    def fingerprint(self, path=None):
        path = path or self.path
        return {
            suffix: hashlib.sha256(candidate.read_bytes()).hexdigest()
            for suffix in ("", "-wal", "-shm")
            if (candidate := Path(str(path) + suffix)).exists()
        }

    def rowset(self, table, path=None):
        with native.native_session(path or self.path) as connection:
            return [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]

    def events(self, action, path=None):
        with native.native_session(path or self.path) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM audit_events WHERE action=?", (action,))]

    def assert_no_stage(self):
        self.assertFalse(list(self.root.glob(".bifma-*")))

    def staff(self):
        user = self.security.create_user(self.admin, "operator", STAFF_PASSWORD)
        return user, self.security.authenticate("operator", STAFF_PASSWORD)

    def test_backup_preserves_full_committed_snapshot_exact_text_and_safe_source_audit(self):
        before = {table: self.rowset(table) for table in ("companies", "units", "products", "users", "audit_events", "auth_sessions")}
        self.clock.now += 10
        result = self.service.create_backup(self.admin, self.destination)
        self.assertEqual(result.path, self.destination)
        self.assertEqual(result.version, native.MIGRATIONS[-1].version)
        for table, rows in before.items():
            self.assertEqual(self.rowset(table, self.destination), rows, table)
        for table in ("companies", "units", "products", "users"):
            self.assertEqual(self.rowset(table), before[table], table)
        event = self.events("backup.created")[0]
        self.assertEqual(event["actor_id"], self.admin.user.id)
        self.assertEqual(json.loads(event["details_json"]), {"schema_version": result.version})
        self.assertNotIn(str(self.destination), str(event))
        self.assertNotIn(self.admin.token, str(event))
        self.assertEqual(self.events("backup.created", self.destination), [])
        self.assert_no_stage()
        require_integrity(self.destination)
        if os.name == "posix":
            self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)

    def test_backup_authorizes_under_write_lock_and_includes_committed_wal(self):
        active = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(active.close)
        active.execute("PRAGMA journal_mode=WAL")
        active.execute("UPDATE products SET purchase_rate='0.000001' WHERE id=?", (self.product,))
        self.assertTrue(Path(str(self.path) + "-wal").exists())
        actual = backup.backup_database
        observed = []

        def checked_copy(source, destination):
            with closing(sqlite3.connect(source, isolation_level=None, timeout=0)) as competitor, competitor:
                with self.assertRaises(sqlite3.OperationalError):
                    competitor.execute("BEGIN IMMEDIATE")
            observed.append(True)
            return actual(source, destination)

        self.clock.now += 10
        with patch.object(backup, "backup_database", checked_copy):
            self.service.create_backup(self.admin, self.destination)
        self.assertEqual(observed, [True])
        with native.native_session(self.destination) as connection:
            self.assertEqual(connection.execute("SELECT purchase_rate FROM products").fetchone()[0], "0.000001")
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertFalse(Path(str(self.destination) + "-wal").exists())

    def test_backup_requires_current_real_active_admin(self):
        _, staff = self.staff()
        self.clock.now += 5
        for actor, error in ((staff, AuthorizationError), (self.admin.user, AuthenticationError), ("x" * 43, AuthenticationError)):
            with self.subTest(actor_type=type(actor).__name__), self.assertRaises(error):
                self.service.create_backup(actor, self.destination)
            self.assertFalse(self.destination.exists())
            self.assert_no_stage()
        self.security.logout(self.admin)
        with self.assertRaises(AuthenticationError):
            self.service.create_backup(self.admin, self.destination)
        self.assertEqual(self.events("backup.created"), [])

    def test_backup_failure_rolls_back_authorization_audit_and_cleans_files(self):
        before = self.fingerprint()
        self.clock.now += 10
        for method in ("backup_database", "_validate", "_audit", "publish_new_file"):
            with self.subTest(method=method), patch.object(backup, method, side_effect=RuntimeError("synthetic failure")):
                with self.assertRaises(RuntimeError):
                    self.service.create_backup(self.admin, self.destination)
            self.assertFalse(self.destination.exists())
            self.assertEqual(self.fingerprint(), before)
            self.assert_no_stage()

    def test_backup_commit_failure_removes_own_published_inode(self):
        # A deferred FK failure appears only when native_transaction commits.
        actual_audit = backup._audit

        def bad_commit(connection, *args):
            actual_audit(connection, *args)
            connection.execute("PRAGMA defer_foreign_keys=ON")
            connection.execute("INSERT INTO user_company_grants(user_id,company_id) VALUES(?,?)", (self.admin.user.id, str(uuid4())))

        before = self.fingerprint()
        with patch.object(backup, "_audit", bad_commit), self.assertRaises(sqlite3.IntegrityError):
            self.service.create_backup(self.admin, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.fingerprint(), before)
        self.assert_no_stage()

    def test_restore_authenticates_private_copy_and_revokes_all_sessions(self):
        _, staff = self.staff()
        self.security.authenticate("administrator", PASSWORD)
        with native.native_transaction(self.path) as connection:
            future = self.clock.now + 100
            connection.execute("INSERT INTO auth_sessions(token_hash,user_id,issued_at,expires_at,revoked_at,last_seen_at) VALUES(?,?,?,?,?,?)",
                               ("a" * 64, self.admin.user.id, future, future + 100, None, future))
        before = self.fingerprint()
        business = {table: self.rowset(table) for table in ("companies", "units", "products")}
        session_count = len(self.rowset("auth_sessions"))
        actual = SecurityService.authenticate
        observed = []

        def checked_authentication(security, username, password):
            staged = Path(security.path)
            self.assertNotEqual(staged, self.path)
            self.assertFalse(self.destination.exists())
            if os.name == "posix":
                self.assertEqual(staged.stat().st_mode & 0o777, 0o600)
                self.assertEqual(staged.parent.stat().st_mode & 0o777, 0o700)
            observed.append(True)
            return actual(security, username, password)

        with patch.object(SecurityService, "authenticate", checked_authentication):
            result = self.restore()
        self.assertEqual(observed, [True])
        self.assertEqual(result.path, self.destination)
        self.assertEqual(result.version, native.MIGRATIONS[-1].version)
        self.assertEqual(result.revoked_sessions, session_count + 1)
        self.assertFalse(hasattr(result, "token"))
        self.assertEqual(self.fingerprint(), before)
        for table, rows in business.items():
            self.assertEqual(self.rowset(table, self.destination), rows)
        with native.native_session(self.destination) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM auth_sessions WHERE revoked_at<issued_at").fetchone()[0], 0)
        restored_security = SecurityService(self.destination, clock=self.clock)
        for actor in (self.admin, staff):
            with self.assertRaises(AuthenticationError):
                restored_security.current_user(actor)
        restored_security.authenticate("administrator", PASSWORD)
        event = self.events("backup.restored", self.destination)[0]
        self.assertEqual(event["actor_id"], self.admin.user.id)
        self.assertEqual(json.loads(event["details_json"]), {"schema_version": result.version, "revoked_sessions": session_count + 1})
        self.assertEqual(self.events("backup.restored"), [])
        self.assertNotIn(PASSWORD, str(event))
        self.assertNotIn(self.admin.token, str(event))
        self.assert_no_stage()
        if os.name == "posix":
            self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)

    def test_restore_preserves_source_database_and_wal_bytes(self):
        active = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(active.close)
        active.execute("PRAGMA journal_mode=WAL")
        active.execute("PRAGMA wal_autocheckpoint=0")
        active.execute("UPDATE products SET purchase_rate='100.000001' WHERE id=?", (self.product,))
        before = {key: value for key, value in self.fingerprint().items() if key != "-shm"}
        self.assertIn("-wal", before)
        self.restore()
        # SQLite readers may update shared-memory read marks; database pages and
        # committed WAL frames must remain byte-for-byte unchanged.
        self.assertEqual({key: value for key, value in self.fingerprint().items() if key != "-shm"}, before)
        with native.native_session(self.destination) as connection:
            self.assertEqual(connection.execute("SELECT purchase_rate FROM products").fetchone()[0], "100.000001")
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertFalse(Path(str(self.destination) + "-wal").exists())

    def test_failed_restore_credentials_do_not_change_source_counters_or_publish(self):
        before = self.fingerprint()
        for username, password in (("administrator", "wrong password"), ("unknown", PASSWORD), (None, None)):
            with self.subTest(username=username), self.assertRaises(AuthenticationError):
                self.restore(username=username, password=password)
            self.assertEqual(self.fingerprint(), before)
            self.assertFalse(self.destination.exists())
            self.assert_no_stage()

    def test_restore_requires_active_unlocked_admin_not_ordinary_user(self):
        user, _ = self.staff()
        with self.assertRaises(AuthorizationError):
            self.restore(username="operator", password=STAFF_PASSWORD)
        for column, value in (("is_active", 0), ("locked_until", self.clock.now + 300), ("is_admin", 0)):
            with self.subTest(column=column):
                with native.native_transaction(self.path) as connection:
                    connection.execute(f"UPDATE users SET {column}=? WHERE id=?", (value, self.admin.user.id))
                before = self.fingerprint()
                expected = AuthorizationError if column == "is_admin" else AuthenticationError
                with self.assertRaises(expected):
                    self.restore()
                self.assertEqual(self.fingerprint(), before)
                self.assertFalse(self.destination.exists())
                self.assert_no_stage()
                with native.native_transaction(self.path) as connection:
                    connection.execute("UPDATE users SET is_active=1,is_admin=1,locked_until=NULL WHERE id=?", (self.admin.user.id,))

    def test_restore_failure_after_authentication_cleans_everything_except_original(self):
        before = self.fingerprint()
        for method in ("_audit", "_single_file", "publish_new_file"):
            with self.subTest(method=method), patch.object(backup, method, side_effect=RuntimeError("synthetic failure")):
                with self.assertRaises(RuntimeError):
                    self.restore()
            self.assertEqual(self.fingerprint(), before)
            self.assertFalse(self.destination.exists())
            self.assert_no_stage()

    def test_both_operations_never_overwrite_source_or_existing_destinations(self):
        self.destination.write_bytes(b"existing file")
        for destination in (self.path, self.destination):
            before = destination.read_bytes()
            with self.subTest(destination=destination.name):
                with self.assertRaises(FileExistsError):
                    self.service.create_backup(self.admin, destination)
                with self.assertRaises(FileExistsError):
                    backup.restore_backup(self.path, destination, "administrator", PASSWORD, clock=self.clock)
            self.assertEqual(destination.read_bytes(), before)
        self.assert_no_stage()

    def test_both_operations_reject_dangling_destination_symlink(self):
        unexpected = self.root / "unexpected.sqlite3"
        try:
            self.destination.symlink_to(unexpected)
        except OSError:
            self.skipTest("Symlinks are unavailable")
        with self.assertRaises(FileExistsError):
            self.service.create_backup(self.admin, self.destination)
        with self.assertRaises(FileExistsError):
            self.restore()
        self.assertFalse(unexpected.exists())
        self.assertTrue(self.destination.is_symlink())

    def test_publication_race_preserves_competing_destination_and_cleans_staging(self):
        actual = backup.publish_new_file

        def competing_publish(staged, destination):
            destination.write_bytes(b"competing file")
            return actual(staged, destination)

        for operation in (lambda: self.service.create_backup(self.admin, self.destination), self.restore):
            before = self.fingerprint()
            with patch.object(backup, "publish_new_file", competing_publish), self.assertRaises(FileExistsError):
                operation()
            self.assertEqual(self.destination.read_bytes(), b"competing file")
            self.assertEqual(self.fingerprint(), before)
            self.assert_no_stage()
            self.destination.unlink()

    def test_failure_after_publication_removes_only_owned_destination(self):
        actual = backup.publish_new_file

        def failed_publication(staged, destination):
            actual(staged, destination)
            raise OSError("synthetic directory sync failure")

        for operation in (lambda: self.service.create_backup(self.admin, self.destination), self.restore):
            before = self.fingerprint()
            with patch.object(backup, "publish_new_file", failed_publication), self.assertRaises(OSError):
                operation()
            self.assertFalse(self.destination.exists())
            self.assertEqual(self.fingerprint(), before)
            self.assert_no_stage()

    def test_restore_rejects_old_future_and_tampered_migration_lineage(self):
        future = native.Migration(native.MIGRATIONS[-1].version + 1, "future", ("CREATE TABLE future_example(value TEXT)",))
        before = self.fingerprint()
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS + (future,)):
            with self.assertRaisesRegex(native.NativeSchemaError, "separate copy.*migrate_database"):
                self.restore()
        self.assertEqual(self.fingerprint(), before)
        self.assertFalse(list(self.root.glob("*.preupgrade-*")))
        future_path = self.root / "future.sqlite3"
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS + (future,)):
            native.initialize_database(future_path)
        with self.assertRaises(native.NativeSchemaError):
            self.restore(source=future_path)
        with session(self.path, readonly=False) as connection:
            connection.execute("DROP TRIGGER schema_migrations_immutable_update")
            connection.execute("UPDATE schema_migrations SET checksum=?", ("0" * 64,))
        before = self.fingerprint()
        with self.assertRaisesRegex(native.NativeSchemaError, "checksum"):
            self.restore()
        self.assertEqual(self.fingerprint(), before)
        self.assertFalse(self.destination.exists())
        self.assert_no_stage()

    def test_restore_refuses_invalid_data_foreign_keys_and_non_native_files(self):
        other = self.root / "other.sqlite3"
        with closing(sqlite3.connect(other)) as connection, connection:
            connection.execute("CREATE TABLE original_data(id TEXT)")
        before = other.read_bytes()
        with self.assertRaises(native.NativeSchemaError):
            self.restore(source=other)
        self.assertEqual(other.read_bytes(), before)
        other.write_bytes(b"not a SQLite database")
        with self.assertRaises(sqlite3.DatabaseError):
            self.restore(source=other)
        with session(self.path, readonly=False) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("UPDATE products SET small_unit_id=?", (str(uuid4()),))
        before = self.fingerprint()
        with self.assertRaises(DatabaseError):
            self.restore()
        with self.assertRaises(DatabaseError):
            self.service.create_backup(self.admin, self.destination)
        self.assertEqual(self.fingerprint(), before)
        self.assertFalse(self.destination.exists())
        self.assert_no_stage()

    def test_missing_source_and_uninitialized_native_cannot_be_restored(self):
        missing = self.root / "missing.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            self.restore(source=missing)
        self.assertFalse(missing.exists())
        empty = self.root / "empty-native.sqlite3"
        native.initialize_database(empty)
        before = empty.read_bytes()
        with self.assertRaises(AuthenticationError):
            self.restore(source=empty)
        self.assertEqual(empty.read_bytes(), before)
        self.assertFalse(self.destination.exists())
        self.assert_no_stage()


if __name__ == "__main__":
    unittest.main()
