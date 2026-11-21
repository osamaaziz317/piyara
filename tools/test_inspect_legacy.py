"""Read-only schema extraction checks; no business records are decoded."""

import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

try:
    from .inspect_legacy import SchemaError, artifact_texts, extract_schema, main, read_chunks
except ImportError:
    from inspect_legacy import SchemaError, artifact_texts, extract_schema, main, read_chunks


ROOT = Path(__file__).resolve().parents[1]


def chunked(text):
    raw = text.encode("ascii")
    return struct.pack("<H", len(raw)) + raw + b"\0\0"


class FramingTests(unittest.TestCase):
    def test_multichunk_source_preserves_text(self):
        data = b"\xfd\xff" + struct.pack("<H", 7) + b"CREATE " + chunked("FUNCTION EXAMPLE RETURN NUMBER IS\nBEGIN RETURN 1; END;\n")
        source, end = read_chunks(data, 0, format71=True)
        self.assertEqual(source, "CREATE FUNCTION EXAMPLE RETURN NUMBER IS\nBEGIN RETURN 1; END;\n")
        self.assertEqual(end, len(data))

    def test_malformed_sources_fail_without_echoing_payload(self):
        for data in (b"\x05", b"\xff\x7fsecret", b"\x02\x00\xff\x00\x00\x00"):
            with self.subTest(data_length=len(data)):
                with self.assertRaises(SchemaError) as raised:
                    read_chunks(data, 0)
                self.assertNotIn("secret", str(raised.exception))

    def test_binary_row_data_is_not_extracted(self):
        data = (b'\x03\x03gEXPORT:V10.02.01\nTABLE "EXAMPLE"\n'
                b'CREATE TABLE "EXAMPLE" ("ID" NUMBER(5, 0) NOT NULL ENABLE, "VALUE" VARCHAR2(80)) PCTFREE 10\n'
                b'INSERT INTO "EXAMPLE" ("ID", "VALUE") VALUES (:1, :2)\n'
                b'\x00\x01private_customer_and_password_value\xff\x00')
        result = extract_schema(data)
        self.assertEqual(result["tables"][0]["columns"][0]["oracle_type"], "NUMBER(5, 0)")
        self.assertNotIn("private_customer", "".join(artifact_texts(result).values()))
        self.assertNotIn("INSERT INTO", "".join(artifact_texts(result).values()))

    def test_executable_is_rejected(self):
        with self.assertRaises(SchemaError):
            extract_schema(b"MZ" + b"\0" * 100)


class SuppliedBackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / "BT-270926.EXE"
        cls.data = cls.path.read_bytes()
        cls.schema = extract_schema(cls.data)

    def test_inventory_and_relationships(self):
        summary = self.schema["summary"]
        for key, value in {"tables": 30, "views": 6, "functions": 24, "defaults": 25,
                           "primary_keys": 27, "unique_constraints": 23, "foreign_keys": 79,
                           "indexes": 0, "sequences": 0}.items():
            self.assertEqual(summary[key], value, key)
        tables = {table["name"]: table for table in self.schema["tables"]}
        self.assertIn({"columns": ["VNO_DCDTL"], "references_table": "DCDTL", "references_columns": ["VNO"]}, tables["SDTL"]["foreign_keys"])
        defaults = {col["name"]: col.get("default") for col in tables["UM_USER_RIGHTS"]["columns"]}
        self.assertEqual(defaults["QUE_ALW"], "'N'")

    def test_recovered_business_formulas_are_preserved(self):
        functions = {entry["name"]: entry["sql"] for entry in self.schema["functions"]}
        self.assertIn("SELECT MAX(B.VNO)", functions["GET_PUR_RATE"])
        self.assertIn("PM.TRN_TYPE = 10", functions["GET_PUR_RATE"])
        views = {entry["name"]: entry["sql"] for entry in self.schema["views"]}
        self.assertIn("AND    SD.VNO_DCDTL IS NULL", views["STOCK"])
        self.assertIn("/*\nUNION ALL", views["STOCK"])
        self.assertIn("SD.RATE/100*NVL(SD.DIS_PER,0)", views["SALE_VW_DTL"])
        self.assertIn("- NVL(BM.FREIGHT,0) CR", views["PUR_VW_DTL"])
        self.assertIn("+ NVL(BM.FREIGHT,0) DR", views["PUR_VW_DTL"])

    def test_repeatable_cli_preserves_original(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            arguments = [str(self.path), "--output-dir", directory]
            self.assertEqual(main(arguments), 0)
            self.assertEqual(main(arguments + ["--check"]), 0)
            sql = (Path(directory) / "legacy_schema.sql").read_text()
            self.assertNotIn("INSERT INTO", sql)
            self.assertNotIn("DBMS_STATS", sql)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
