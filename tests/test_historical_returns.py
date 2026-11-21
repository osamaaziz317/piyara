"""Returns reference real source invoices without replaying them as new sales."""

from decimal import Decimal
import hashlib
from pathlib import Path
import tempfile
import unittest

from bifma.baseline import ensure_baseline
from bifma.database import session
from bifma.document_export import DocumentExporter
from bifma.native_reports import NativeReports
from bifma.native_schema import native_session
from bifma.security import AuthorizationError, SecurityService
from bifma.transactions import TransactionError, TransactionService
from tests.test_baseline_import import synthetic_archive


class HistoricalReturnTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / "original.sqlite3"
        self.path = Path(self.directory.name) / "live.sqlite3"
        synthetic_archive(self.source)
        self.counter = 0

    def prepare(self):
        self.before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        ensure_baseline(self.source, self.path)
        self.security = SecurityService(self.path)
        self.actor = self.security.bootstrap_admin("owner", "Green rivers sparkle beneath moonlight 84")
        self.service = TransactionService(self.path, self.security)
        self.reports = NativeReports(self.path, security=self.security)
        with native_session(self.path) as connection:
            self.company = connection.execute("SELECT id FROM companies WHERE code='1'").fetchone()[0]
            self.product = connection.execute("SELECT id FROM products WHERE code='A'").fetchone()[0]
            self.store = connection.execute("SELECT id FROM stores WHERE code='1'").fetchone()[0]
            self.party = connection.execute("SELECT id FROM accounts WHERE code='0002'").fetchone()[0]
            self.original_id = connection.execute("SELECT id FROM baseline_trade_documents").fetchone()[0]
            self.original_line_id = connection.execute("SELECT id FROM baseline_trade_lines").fetchone()[0]

    def values(self, quantity="1", **headers):
        self.counter += 1
        return {"document_number": f"R-{self.counter}", "business_date": "2026-01-04",
                "rounding_policy": "legacy_whole", "posting_places": 0, "account_id": self.party,
                "lines": [{"product_id": self.product, "store_id": self.store, "quantity": quantity,
                           "rate": "10", "discount_percent": "0", "source_line_id": self.original_line_id}], **headers}

    def post(self, values):
        draft = self.service.save_draft(self.actor, self.company, "sale_return", values)
        return self.service.post(self.actor, self.company, draft["id"], draft["revision"])

    def quantity(self):
        return Decimal(self.service.stock_balance(self.actor, self.company)[0]["quantity"])

    def test_original_line_return_continues_stock_and_account_without_forged_invoice(self):
        self.prepare()
        rows = self.service.list_return_sources(self.actor, self.company, "sale_return", search="260001")
        self.assertEqual([(row["id"], row["source"]) for row in rows], [(self.original_id, "history")])
        original = self.service.get_return_source(self.actor, self.company, "sale_return", self.original_id)
        self.assertEqual((original["total_amount"], original["lines"][0]["quantity"]), ("30", "3"))
        returned = self.post(self.values(discount="1"))
        self.assertEqual(returned["total_amount"], "9")
        self.assertEqual(returned["lines"][0]["historical_source_line_id"], self.original_line_id)
        self.assertIsNone(returned["lines"][0]["source_line_id"])
        self.assertEqual(returned["snapshot"]["return_of"]["source"], "history")
        self.assertEqual(self.quantity(), 8)
        self.assertEqual(self.reports.ledger(self.actor, self.company, self.party)["closing"], Decimal(11))
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents WHERE kind='sale'").fetchone()[0], 0)
        output = self.path.parent / "return.html"
        DocumentExporter(self.path, self.security).export_html(self.actor, self.company, returned["id"], output)
        self.assertIn("from original history", output.read_text())
        self.assertIn("260001", output.read_text())
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.before)

    def test_quantity_and_staff_adjusted_money_caps_release_on_reversal(self):
        self.prepare()
        first = self.post(self.values(discount="1"))
        with self.assertRaisesRegex(TransactionError, "original total"):
            self.post(self.values(quantity="2", freight="2"))
        second = self.post(self.values(quantity="2", freight="1"))
        self.assertEqual(Decimal(first["total_amount"]) + Decimal(second["total_amount"]), 30)
        with self.assertRaisesRegex(TransactionError, "quantity exceeds"):
            self.post(self.values())
        self.service.reverse(self.actor, self.company, first["id"], first["revision"],
                             document_number="REV-1", business_date="2026-01-04", reason="Wrong return")
        self.assertEqual(self.post(self.values(discount="1"))["total_amount"], "9")
        self.assertEqual(self.quantity(), 10)

    def add_old_return(self, *, linked=True):
        with session(self.source, readonly=False) as connection:
            connection.execute("INSERT INTO PMST(TRNID,TRNNO,TRNDATE,TRN_TYPE,CMP_ID,CHCODE,P_TYPE) "
                               "VALUES('2','2','2026-01-03T00:00:00','10','1','0002','R')")
            connection.execute("INSERT INTO PDTL(TRNID,VNO,LINE_NUM,STORE_ID,ICODE,QTY,RATE,UOM_ID_SMALL,VNO_SDTL) "
                               "VALUES('2','2','1','1','A','1','10','1',?)", ("1" if linked else None,))

    def test_explicit_prior_historical_return_consumes_both_caps(self):
        self.add_old_return()
        self.prepare()
        original = self.service.get_return_source(self.actor, self.company, "sale_return", self.original_id)
        self.assertEqual(original["historical_returned_amount"], "10")
        self.assertEqual(original["lines"][0]["historical_returned_quantity"], "1")
        with self.assertRaisesRegex(TransactionError, "original total"):
            self.post(self.values(quantity="2", freight="1"))
        self.post(self.values(quantity="2"))
        self.assertEqual(self.quantity(), 10)
        with self.assertRaisesRegex(TransactionError, "quantity exceeds"):
            self.post(self.values())

    def test_unlinked_prior_returns_do_not_invent_available_capacity(self):
        self.add_old_return(linked=False)
        self.prepare()
        self.assertEqual(self.service.list_return_sources(self.actor, self.company, "sale_return"), [])
        with self.assertRaisesRegex(TransactionError, "review"):
            self.post(self.values())
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.before)

    def test_wrong_original_kind_and_unit_conflict_cannot_be_returned(self):
        with session(self.source, readonly=False) as connection:
            connection.execute("UPDATE SDTL SET UOM_ID_SMALL='2'")
        self.prepare()
        self.assertEqual(self.service.list_return_sources(self.actor, self.company, "sale_return"), [])
        with self.assertRaisesRegex(TransactionError, "review"):
            self.post(self.values())
        with self.assertRaises(TransactionError):
            self.service.get_return_source(self.actor, self.company, "purchase_return", self.original_id)

    def test_return_source_chooser_rechecks_current_original_and_return_permissions(self):
        self.prepare()
        user = self.security.create_user(self.actor, "returns", "Quiet golden lanterns frame the hills 53")
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.set_permissions(self.actor, user.id, "sales_returns", ["create"])
        staff = self.security.authenticate("returns", "Quiet golden lanterns frame the hills 53")
        with self.assertRaises(AuthorizationError):
            self.service.list_return_sources(staff, self.company, "sale_return")
        with self.assertRaises(AuthorizationError):
            self.service.save_draft(staff, self.company, "sale_return", self.values())
        self.security.set_permissions(self.actor, user.id, "sales", ["read"])
        self.assertEqual(len(self.service.list_return_sources(staff, self.company, "sale_return")), 1)
        draft = self.service.save_draft(staff, self.company, "sale_return", self.values())
        self.security.set_permissions(self.actor, user.id, "sales_returns", ["create", "post"])
        self.security.set_permissions(self.actor, user.id, "sales", [])
        with self.assertRaises(AuthorizationError):
            self.service.post(staff, self.company, draft["id"], draft["revision"])
        self.security.set_permissions(self.actor, user.id, "sales_returns", [])
        with self.assertRaises(AuthorizationError):
            self.service.get_return_source(staff, self.company, "sale_return", self.original_id)


if __name__ == "__main__":
    unittest.main()
