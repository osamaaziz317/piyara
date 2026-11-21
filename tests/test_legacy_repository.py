import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from bifma.legacy_repository import LegacyRepository


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "archive.sqlite3"
        schema = json.loads((Path(__file__).resolve().parents[1] / "docs/legacy_schema.json").read_text())
        connection = sqlite3.connect(self.path)
        for table in schema["tables"]:
            definition = ",".join('"' + column["name"] + '" TEXT' for column in table["columns"])
            connection.execute('CREATE TABLE "' + table["name"] + '" (' + definition + ')')
        connection.execute("CREATE TABLE _recovery_metadata (key TEXT PRIMARY KEY,value TEXT)")
        connection.execute("INSERT INTO _recovery_metadata VALUES ('report',?)", (json.dumps({"status": "recovered_unverified"}),))
        connection.executemany("INSERT INTO CONFIG (CMP_ID,CMPNAME) VALUES (?,?)", [("1", "Example"), ("2", "Second")])
        connection.executemany("INSERT INTO PUR_ITEM (ICODE,INAME,SALE_RATE) VALUES (?,?,?)", [
            ("0001", "Plain 100% pack", "9007199254740993.01"), ("0002", "Plain pack", "5")])
        connection.execute("INSERT INTO CHART (CHCODE,CHDESC) VALUES ('00000001','Example customer')")
        connection.execute("INSERT INTO ENTRY_TYPE (TYPE_CODE,DESCRIPTION) VALUES ('8','Sale')")
        connection.executemany("INSERT INTO SMST (TRNID,TRNNO,TRNDATE,BILLNO,CMP_ID,CHCODE,TRN_TYPE) VALUES (?,?,?,?,?,?,?)", [
            ("1", "1", "2026-09-26T12:00:00", "B-1", "1", "00000001", "8"),
            ("2", "2", "2026-09-27T12:00:00", "B-2", "1", "00000001", "8"),
            ("3", "1", "2026-09-27T12:00:00", "OTHER", "2", "00000001", "8")])
        connection.execute("INSERT INTO SDTL (TRNID,VNO,LINE_NUM,ICODE,QTY,RATE,DIS_PER) VALUES ('1','1','1','0001','2.5','12.25','10')")
        connection.commit()
        connection.close()
        self.repo = LegacyRepository(self.path)

    def test_company_scoped_summary_paging_and_detail(self):
        self.assertEqual(self.repo.summary("1")["sale_count"], 2)
        self.assertEqual(self.repo.summary("2")["sale_count"], 1)
        first = self.repo.records("sales", "1", limit=1)
        second = self.repo.records("sales", "1", offset=1, limit=1)
        self.assertEqual(first.total, 2)
        self.assertEqual(first.rows[0]["bill"], "B-2")
        self.assertEqual(second.rows[0]["bill"], "B-1")
        with self.assertRaisesRegex(ValueError, "selected company"):
            self.repo.document("sales", "3", "1")

    def test_search_is_literal_and_parameterized(self):
        self.assertEqual(self.repo.records("products", "1", "%").total, 1)
        self.assertEqual(self.repo.records("products", "1", "_").total, 0)
        self.assertEqual(self.repo.records("products", "1", "' OR 1=1 --").total, 0)
        with self.assertRaises(ValueError):
            self.repo.records("UM_USERS", "1")

    def test_identifiers_and_money_are_not_float_coerced(self):
        row = self.repo.records("products", "1", "0001").rows[0]
        self.assertEqual(row["code"], "0001")
        self.assertEqual(row["sale_rate"], "9007199254740993.01")
        detail = self.repo.document("sales", "1", "1")
        self.assertEqual(detail["rows"][0]["net"], "27.5625")
        self.assertIn(("Line net subtotal (before rounding)", "27.5625"), detail["fields"])

    def test_reads_leave_archive_unchanged(self):
        before = hashlib.sha256(self.path.read_bytes()).digest()
        self.repo.companies()
        self.repo.summary("1")
        for section in ("products", "accounts", "sales", "purchases", "journals", "stores", "units", "breakage_receive", "breakage_dispatch"):
            self.repo.records(section, "1")
        self.repo.document("sales", "1", "1")
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), before)

    def test_paging_limits(self):
        for limit in (0, 501, True, 1.5):
            with self.assertRaises(ValueError):
                self.repo.records("products", "1", limit=limit)
        for offset in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                self.repo.records("products", "1", offset=offset)

    def test_returns_have_separate_lists_titles_and_do_not_inflate_sales(self):
        connection = sqlite3.connect(self.path)
        connection.executemany("INSERT INTO ENTRY_TYPE (TYPE_CODE,DESCRIPTION) VALUES (?,?)", [
            ("9", "Purchase Return Voucher"), ("10", "Sale Return Voucher")])
        connection.execute("INSERT INTO SMST (TRNID,TRNNO,TRNDATE,CMP_ID,TRN_TYPE) VALUES ('4','4','2026-10-01T00:00:00','1','9')")
        connection.execute("INSERT INTO PMST (TRNID,TRNNO,TRNDATE,CMP_ID,TRN_TYPE) VALUES ('1','1','2026-10-01T00:00:00','1','10')")
        connection.commit()
        connection.close()
        self.assertEqual(self.repo.summary("1")["sale_count"], 2)
        self.assertEqual(self.repo.summary("1")["latest_sale_date"], "2026-09-27T12:00:00")
        self.assertEqual(self.repo.records("sales", "1").total, 2)
        self.assertEqual(self.repo.records("purchases", "1").total, 0)
        self.assertEqual(self.repo.records("purchase_returns", "1").total, 1)
        self.assertEqual(self.repo.records("sales_returns", "1").total, 1)
        self.assertEqual(self.repo.document("purchase_returns", "4", "1")["title"], "Purchase Return Voucher")
        self.assertEqual(self.repo.document("sales_returns", "1", "1")["title"], "Sale Return Voucher")
        with self.assertRaises(ValueError):
            self.repo.document("sales", "4", "1")

    def test_historical_store_filter_preserves_cross_company_references(self):
        connection = sqlite3.connect(self.path)
        connection.execute("INSERT INTO STORE_LOC (STORE_ID,STORE_DESC,CMP_ID) VALUES ('1','Original store','1')")
        connection.execute("INSERT INTO SDTL (TRNID,VNO,STORE_ID) VALUES ('3','9','1')")
        connection.commit()
        connection.close()
        self.assertEqual(self.repo.records("stores", "2").total, 0)
        self.assertEqual(self.repo.stock_stores("2"), [("1", "Original store")])


if __name__ == "__main__":
    unittest.main()
