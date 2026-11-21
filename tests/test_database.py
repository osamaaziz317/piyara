import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bifma import database
from bifma.database import (
    backup_database, check_integrity, publish_new_file, session, transaction,
)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "store.sqlite3"
        connection = sqlite3.connect(self.path)
        connection.executescript("""
            CREATE TABLE documents (id INTEGER PRIMARY KEY, total TEXT NOT NULL);
            CREATE TABLE lines (id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL REFERENCES documents(id), qty TEXT NOT NULL);
        """)
        connection.close()

    def test_failure_rolls_back_header_and_lines(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.path) as connection:
                connection.execute("INSERT INTO documents VALUES (1, '120.25')")
                connection.execute("INSERT INTO lines VALUES (1, 99, '2')")
        with session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)

    def test_success_commits_together_and_readonly_rejects_writes(self):
        with transaction(self.path) as connection:
            connection.execute("INSERT INTO documents VALUES (1, '120.25')")
            connection.execute("INSERT INTO lines VALUES (1, 1, '2')")
        with session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM lines").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM documents")
        self.assertTrue(check_integrity(self.path)["ok"])

    def test_readonly_missing_database_is_not_created(self):
        missing = self.path.parent / "missing.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            with session(missing):
                pass
        self.assertFalse(missing.exists())

    def test_backup_contains_committed_wal_and_refuses_overwrite(self):
        active = sqlite3.connect(self.path)
        self.addCleanup(active.close)
        active.execute("PRAGMA journal_mode=WAL")
        active.execute("INSERT INTO documents VALUES (1, '9007199254740993.01')")
        active.commit()
        destination = self.path.parent / "backup.sqlite3"
        backup_database(self.path, destination)
        with session(destination) as connection:
            self.assertEqual(connection.execute("SELECT total FROM documents").fetchone()[0],
                             "9007199254740993.01")
        with self.assertRaises(FileExistsError):
            backup_database(self.path, destination)
        with self.assertRaises(FileExistsError):
            backup_database(self.path, self.path)

    def test_failed_backup_leaves_no_partial_destination(self):
        destination = self.path.parent / "backup.sqlite3"
        self.path.write_bytes(b"invalid SQLite")
        with self.assertRaises(sqlite3.DatabaseError):
            backup_database(self.path, destination)
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.path.parent.glob(".bifma-backup-*")))

    def test_backup_rejects_dangling_symlink(self):
        target = self.path.parent / "unexpected.sqlite3"
        link = self.path.parent / "backup.sqlite3"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(FileExistsError):
            backup_database(self.path, link)
        self.assertFalse(target.exists())

    def test_publication_flushes_writable_stage_before_link_without_changing_bytes(self):
        staged = self.path.parent / "staged.bin"
        destination = self.path.parent / "published.bin"
        content = b"\x00Synthetic exact bytes\r\n\xff\x00"
        staged.write_bytes(content)
        real_fsync, real_link = os.fsync, os.link
        events = []

        def writable_flush(descriptor):
            if stat.S_ISREG(os.fstat(descriptor).st_mode):
                self.assertFalse(destination.exists())
                # Emulate the Windows flush requirement on POSIX as well:
                # a zero-byte write requires write access, without changing
                # content or extending the file. A readonly stage fails here.
                self.assertEqual(os.write(descriptor, b""), 0)
                real_fsync(descriptor)
                events.append("file_synced")
            else:
                real_fsync(descriptor)

        def publish_link(source, target):
            self.assertEqual(events, ["file_synced"])
            self.assertEqual(staged.read_bytes(), content)
            real_link(source, target)
            events.append("linked")

        with patch.object(database.os, "fsync", side_effect=writable_flush), patch.object(database.os, "link", side_effect=publish_link):
            publish_new_file(staged, destination)
        self.assertEqual(events, ["file_synced", "linked"])
        self.assertEqual(destination.read_bytes(), content)
        self.assertFalse(staged.exists())

    def test_publication_refuses_competing_destination_and_preserves_stage(self):
        staged = self.path.parent / "staged.bin"
        destination = self.path.parent / "published.bin"
        staged.write_bytes(b"Prepared content")
        destination.write_bytes(b"Another writer's content")
        with self.assertRaises(FileExistsError):
            publish_new_file(staged, destination)
        self.assertEqual(staged.read_bytes(), b"Prepared content")
        self.assertEqual(destination.read_bytes(), b"Another writer's content")

    def test_failed_publication_flush_never_creates_destination(self):
        staged = self.path.parent / "staged.bin"
        destination = self.path.parent / "published.bin"
        staged.write_bytes(b"Prepared content")
        with patch.object(database.os, "fsync", side_effect=OSError("Synthetic flush failure")), patch.object(database.os, "link") as link:
            with self.assertRaisesRegex(OSError, "Synthetic flush failure"):
                publish_new_file(staged, destination)
        link.assert_not_called()
        self.assertFalse(destination.exists())
        self.assertEqual(staged.read_bytes(), b"Prepared content")


if __name__ == "__main__":
    unittest.main()
