from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bifma.database import session
from tools.stage_recovery import check_source_constraints, stage_dump


def table(name, columns, rows, *, primary_key=None, foreign_keys=None, unique=None):
    schema = {"name": name, "columns": [
        {"name": n, "oracle_type": typ, "nullable": nullable}
        for n, typ, nullable in columns],
        "primary_key": primary_key or [], "foreign_keys": foreign_keys or [],
        "unique_constraints": unique or []}
    source = SimpleNamespace(name=name,
        columns=[SimpleNamespace(**column) for column in schema["columns"]],
        iter_rows=lambda: iter(rows))
    return schema, source


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "recovered.sqlite3"

    def source(self, *tables):
        schema = {"source": {"sha256": "test-source"}, "tables": [t[0] for t in tables]}
        dump = SimpleNamespace(sha256="test-source", tables={t[1].name: t[1] for t in tables})
        return dump, schema

    def test_exact_values_nulls_spaces_and_relationships(self):
        parent = table("PARENT", [("ID", "NUMBER", False), ("AMOUNT", "NUMBER", True),
                                 ("NOTE", "VARCHAR2(80)", True), ("ON_DATE", "DATE", True)],
                       [{"ID": Decimal("1.00"), "AMOUNT": Decimal("123456789012345678901234567890.12345678"),
                         "NOTE": "اردو  ", "ON_DATE": "2026-09-27T15:28:47"},
                        {"ID": Decimal("2"), "AMOUNT": None, "NOTE": None, "ON_DATE": None}], primary_key=["ID"])
        child = table("CHILD", [("PARENT_ID", "NUMBER", True)],
                      [{"PARENT_ID": Decimal("1")}, {"PARENT_ID": None}],
                      foreign_keys=[{"columns": ["PARENT_ID"], "references_table": "PARENT",
                                     "references_columns": ["ID"]}])
        report = stage_dump(*self.source(parent, child), self.path)
        self.assertEqual(report["total_rows"], 4)
        self.assertEqual(report["constraint_violations"], 0)
        self.assertFalse(report["oracle_reconciled"])
        with session(self.path) as connection:
            row = connection.execute("SELECT * FROM PARENT WHERE ID='1'").fetchone()
            self.assertEqual(tuple(row), ("1", "123456789012345678901234567890.12345678",
                                         "اردو  ", "2026-09-27T15:28:47"))
            manifest = json.loads(connection.execute(
                "SELECT value FROM _recovery_metadata WHERE key='report'").fetchone()[0])
            self.assertEqual(manifest, report)
        with self.assertRaises(FileExistsError):
            stage_dump(*self.source(parent, child), self.path)

    def test_conflicts_preserved_and_reported(self):
        parent = table("PARENT", [("ID", "NUMBER", False)],
                       [{"ID": Decimal(1)}, {"ID": Decimal(1)}], primary_key=["ID"])
        child = table("CHILD", [("PARENT_ID", "NUMBER", True)],
                      [{"PARENT_ID": Decimal(9)}], foreign_keys=[
                          {"columns": ["PARENT_ID"], "references_table": "PARENT", "references_columns": ["ID"]}])
        report = stage_dump(*self.source(parent, child), self.path)
        self.assertEqual(report["total_rows"], 3)
        self.assertEqual(report["constraint_violations"], 2)

    def test_decode_failure_never_publishes_partial_database(self):
        source_table = table("ITEMS", [("ID", "NUMBER", False)], [], primary_key=["ID"])

        def broken_rows():
            yield {"ID": Decimal(1)}
            raise ValueError("corrupt source field")

        source_table[1].iter_rows = broken_rows
        with self.assertRaisesRegex(ValueError, "corrupt source"):
            stage_dump(*self.source(source_table), self.path)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_fingerprint_mismatch_refused_before_write(self):
        dump, schema = self.source(table("ITEMS", [("ID", "NUMBER", False)], []))
        dump.sha256 = "different-source"
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            stage_dump(dump, schema, self.path)
        self.assertFalse(self.path.exists())

    def test_oracle_partial_null_unique_semantics(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE SAMPLE (A TEXT, B TEXT)")
        connection.executemany("INSERT INTO SAMPLE VALUES (?,?)", [
            (None, None), (None, None), ("1", None), ("1", None), ("1", "2"), ("1", "3")])
        schema, _ = table("SAMPLE", [("A", "NUMBER", True), ("B", "NUMBER", True)], [], unique=[["A", "B"]])
        report = check_source_constraints(connection, {"tables": [schema]})
        self.assertEqual(report[0]["violations"], 1)

    def test_staging_rejects_dangling_symlink(self):
        target = self.path.parent / "unexpected.sqlite3"
        try:
            self.path.symlink_to(target)
        except OSError:
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(FileExistsError):
            stage_dump(*self.source(table("ITEMS", [("ID", "NUMBER", False)], [])), self.path)
        self.assertFalse(target.exists())

    def test_open_failure_cleans_staging_file(self):
        with patch("tools.stage_recovery.sqlite3.connect", side_effect=sqlite3.OperationalError("open failed")):
            with self.assertRaisesRegex(sqlite3.OperationalError, "open failed"):
                stage_dump(*self.source(table("ITEMS", [("ID", "NUMBER", False)], [])), self.path)
        self.assertEqual(list(self.path.parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
