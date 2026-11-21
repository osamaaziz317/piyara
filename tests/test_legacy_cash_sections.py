"""Cash voucher browsing preserves the original rows and exact source amounts."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from bifma.legacy_repository import LegacyRepository


class LegacyCashSectionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="bifma-cash-sections-")
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "synthetic.sqlite3"
        schema_path = Path(__file__).resolve().parents[1] / "docs" / "legacy_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        with closing(sqlite3.connect(self.path)) as connection, connection:
            for table in schema["tables"]:
                columns = ",".join('"' + column["name"] + '" TEXT' for column in table["columns"])
                connection.execute('CREATE TABLE "' + table["name"] + '" (' + columns + ')')
            connection.execute("CREATE TABLE _recovery_metadata (key TEXT PRIMARY KEY,value TEXT)")
            connection.execute("INSERT INTO _recovery_metadata VALUES ('report',?)",
                               (json.dumps({"status": "recovered_unverified"}),))
            connection.executemany("INSERT INTO CONFIG (CMP_ID,CMPNAME,CHCODE_CASH) VALUES (?,?,?)", [
                ("1", "Synthetic first company", "CASH0001"),
                ("2", "Synthetic second company", "CASH0002"),
            ])
            connection.executemany("INSERT INTO CHART (CHCODE,CHDESC) VALUES (?,?)", [
                ("00000001", "Cash client 100% exact_under \\ path"),
                ("00000002", "Different counterparty"),
                ("CASH0001", "Synthetic cash one"),
                ("CASH0002", "Synthetic cash two"),
            ])
            connection.executemany("INSERT INTO ENTRY_TYPE (TYPE_CODE,DESCRIPTION) VALUES (?,?)", [
                ("1", "Journal Voucher"), ("2", "Cash Payment Voucher"),
                ("3", "Cash Receipt Voucher"), ("4", "Bank Payment Voucher"),
                ("5", "Bank Receipt Voucher"), ("6", "Opening Balance"),
            ])
            connection.executemany(
                "INSERT INTO JMST (TRNID,TRNNO,TRNDATE,VTYPE,CMP_ID,CANCEL) VALUES (?,?,?,?,?,?)", [
                    ("1", "2", "2026-10-01T00:00:00", "3", "1", None),
                    ("2", "10", "2026-10-01T00:00:00", "3", "1", None),
                    ("3", "3", "2026-09-30T00:00:00", "3", "1", ""),
                    ("4", "4", "2026-09-29T00:00:00", "3", "1", "0"),
                    ("5", "5", "2026-09-28T00:00:00", "3", "1", None),
                    ("6", "2", "2026-10-01T00:00:00", "3", "2", None),
                    ("7", "2", "2026-10-01T00:00:00", "2", "1", None),
                    ("8", "10", "2026-10-01T00:00:00", "2", "1", None),
                    ("9", "2", "2026-10-01T00:00:00", "2", "2", None),
                    ("10", "20", "2026-10-01T00:00:00", "1", "1", None),
                    ("11", "21", "2026-10-01T00:00:00", "4", "1", None),
                    ("12", "22", "2026-10-01T00:00:00", "5", "1", None),
                    ("13", "23", "2026-10-01T00:00:00", "6", "1", None),
                ],
            )
            # Opposite-side values and a signed historical line are deliberate:
            # browsing must preserve them, not rebalance or normalize history.
            details = [
                ("1", "1", "00000001", "Repeated match first", "7.125", "9007199254740993.0101"),
                ("1", "2", "00000001", "Repeated match second", None, "0.0009"),
                ("1", "3", "00000002", "Unmatched amount remains in voucher total", "0", "0.002"),
                ("1", "10", "00000002", None, None, None),
                ("2", "1", "00000002", "Repeated match third", "0", "1.2"),
                ("2", "2", "00000002", "Unmatched fractional amount", "0", "0.3"),
                ("3", "1", "00000002", "Empty cancellation marker", "0", "3"),
                ("4", "1", "00000002", "Zero cancellation marker", "0", "4"),
                ("6", "1", "00000001", "Repeated match other company", "0", "999"),
                ("7", "1", "00000001", "Payment first", "9007199254740993.0000001", "8.50"),
                ("7", "2", "00000001", "Payment second", "0.0000002", None),
                ("7", "3", "00000002", None, None, None),
                ("8", "1", "00000002", "Signed source line", "-1.20", "0"),
                ("8", "2", "00000002", "Second source line", "3.20", "0"),
                ("9", "1", "00000001", "Payment other company", "999", "0"),
                ("10", "1", "00000002", "Ordinary journal", "1", "1"),
                ("11", "1", "00000002", "Bank-only payment", "2", "0"),
                ("12", "1", "00000002", "Bank-only receipt", "0", "2"),
                ("13", "1", "00000002", "Opening", "4", "4"),
            ]
            connection.executemany(
                "INSERT INTO JDTL (DTL_ID,TRNID,SR,CHCODE,NARRATION,DR,CR) VALUES (?,?,?,?,?,?,?)",
                [(str(index),) + row for index, row in enumerate(details, 1)],
            )
        self.before = hashlib.sha256(self.path.read_bytes()).digest()
        self.repo = LegacyRepository(self.path)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), self.before,
                         "Historical browsing changed the synthetic archive")

    def test_cash_lists_are_company_and_type_scoped_with_paging(self):
        receipts = self.repo.records("cash_receipts", "1")
        payments = self.repo.records("cash_payments", "1")
        self.assertEqual(receipts.total, 5)
        self.assertEqual([row["id"] for row in receipts.rows], ["2", "1", "3", "4", "5"])
        self.assertEqual(payments.total, 2)
        self.assertEqual([row["id"] for row in payments.rows], ["8", "7"])
        self.assertIn(("amount", "Received"), receipts.columns)
        self.assertIn(("amount", "Paid"), payments.columns)
        first = self.repo.records("cash_receipts", "1", limit=1)
        second = self.repo.records("cash_receipts", "1", offset=1, limit=1)
        self.assertEqual((first.total, second.total), (5, 5))
        self.assertEqual((first.rows[0]["id"], second.rows[0]["id"]), ("2", "1"))
        self.assertEqual([row["id"] for row in self.repo.records("cash_receipts", "2").rows], ["6"])
        self.assertEqual([row["id"] for row in self.repo.records("cash_payments", "2").rows], ["9"])
        self.assertEqual(self.repo.records("cash_receipts", "absent").total, 0)

    def test_receipt_and_payment_amounts_are_exact_unrounded_source_side_sums(self):
        receipts = {row["id"]: row["amount"] for row in self.repo.records("cash_receipts", "1").rows}
        payments = {row["id"]: row["amount"] for row in self.repo.records("cash_payments", "1").rows}
        self.assertEqual(receipts["1"], "9007199254740993.013")
        self.assertEqual(receipts["2"], "1.5")
        self.assertEqual(receipts["5"], "0")
        self.assertEqual(payments["7"], "9007199254740993.0000003")
        self.assertEqual(payments["8"], "2")
        self.assertTrue(all(isinstance(amount, str) for amount in (*receipts.values(), *payments.values())))

    def test_detail_search_keeps_distinct_headers_and_complete_amounts(self):
        first = self.repo.records("cash_receipts", "1", "Repeated match", limit=1)
        second = self.repo.records("cash_receipts", "1", "Repeated match", offset=1, limit=1)
        beyond = self.repo.records("cash_receipts", "1", "Repeated match", offset=2, limit=1)
        self.assertEqual((first.total, second.total, beyond.total), (2, 2, 2))
        self.assertEqual((first.rows[0]["id"], second.rows[0]["id"]), ("2", "1"))
        self.assertEqual(first.rows[0]["amount"], "1.5")
        self.assertEqual(second.rows[0]["amount"], "9007199254740993.013")
        self.assertEqual(beyond.rows, [])

    def test_search_matches_detail_accounts_and_treats_wildcards_literally(self):
        for query in ("00000001", "Cash client", "%", "_", "\\"):
            with self.subTest(query=query):
                page = self.repo.records("cash_receipts", "1", query)
                self.assertEqual(page.total, 1)
                self.assertEqual([row["id"] for row in page.rows], ["1"])
        self.assertEqual(self.repo.records("cash_receipts", "1", "' OR 1=1 --").total, 0)
        self.assertEqual(self.repo.records("cash_receipts", "1", "Bank-only").total, 0)
        self.assertEqual(self.repo.records("cash_receipts", "1", "Cash Receipt Voucher").total, 5)
        self.assertEqual(self.repo.records("cash_payments", "1", "Payment second").rows[0]["id"], "7")

    def test_document_rejects_wrong_cash_type_company_and_bank_type(self):
        for section, identifier, company in (
            ("cash_receipts", "7", "1"), ("cash_payments", "1", "1"),
            ("cash_receipts", "6", "1"), ("cash_payments", "9", "1"),
            ("cash_receipts", "12", "1"), ("cash_payments", "11", "1"),
            ("cash_receipts", "absent", "1"),
        ):
            with self.subTest(section=section, identifier=identifier, company=company):
                with self.assertRaises(ValueError):
                    self.repo.document(section, identifier, company)

    def test_document_preserves_detail_rows_and_explains_separate_cash_counterpart(self):
        receipt = self.repo.document("cash_receipts", "1", "1")
        fields = dict(receipt["fields"])
        self.assertEqual(receipt["title"], "Cash Receipt Voucher")
        self.assertEqual(fields["Cash received"], "9007199254740993.013")
        self.assertEqual(fields["Detail debit"], "7.125")
        self.assertEqual(fields["Detail credit"], "9007199254740993.013")
        self.assertEqual(fields["Ledger inclusion"], "Included")
        self.assertEqual(len(receipt["rows"]), 4)
        self.assertEqual(receipt["rows"][0]["credit"], "9007199254740993.0101")
        self.assertIsNone(receipt["rows"][-1]["credit"])
        self.assertIsNone(receipt["rows"][-1]["debit"])
        self.assertNotIn("CASH0001", {row["code"] for row in receipt["rows"]})
        self.assertTrue(any("cash" in note.casefold() and "counterpart" in note.casefold()
                            for note in receipt["notes"]))
        payment = self.repo.document("cash_payments", "7", "1")
        payment_fields = dict(payment["fields"])
        self.assertEqual(payment["title"], "Cash Payment Voucher")
        self.assertEqual(payment_fields["Cash paid"], "9007199254740993.0000003")
        self.assertEqual(payment_fields["Detail credit"], "8.5")
        self.assertEqual(len(payment["rows"]), 3)
        self.assertEqual(payment["rows"][0]["credit"], "8.50")
        signed = self.repo.document("cash_payments", "8", "1")
        self.assertEqual(signed["rows"][0]["debit"], "-1.20")

    def test_falsey_non_null_cancellation_markers_stay_visible_but_excluded(self):
        rows = {row["id"]: row for row in self.repo.records("cash_receipts", "1").rows}
        for identifier, marker in (("3", ""), ("4", "0")):
            with self.subTest(marker=marker):
                self.assertEqual(rows[identifier]["cancelled"], marker)
                document = self.repo.document("cash_receipts", identifier, "1")
                fields = dict(document["fields"])
                self.assertEqual(fields["Cancellation marker"], marker)
                self.assertEqual(fields["Ledger inclusion"], "Excluded (cancellation marker)")
                self.assertEqual(len(document["rows"]), 1)

    def test_all_vouchers_keeps_journals_cash_bank_and_openings_accessible(self):
        page = self.repo.records("journals", "1")
        self.assertEqual(page.total, 11)
        self.assertEqual({row["id"] for row in page.rows},
                         {"1", "2", "3", "4", "5", "7", "8", "10", "11", "12", "13"})
        self.assertEqual(self.repo.document("journals", "1", "1")["title"], "Cash Receipt Voucher")
        self.assertEqual(self.repo.document("journals", "7", "1")["title"], "Cash Payment Voucher")
        self.assertEqual(self.repo.document("journals", "10", "1")["title"], "Journal Voucher")
        self.assertEqual(self.repo.document("journals", "11", "1")["title"], "Bank Payment Voucher")
        self.assertEqual(self.repo.document("journals", "12", "1")["title"], "Bank Receipt Voucher")
        self.assertEqual(self.repo.document("journals", "13", "1")["title"], "Opening Balance")


if __name__ == "__main__":
    unittest.main()
