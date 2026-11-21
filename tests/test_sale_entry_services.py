"""Sale reference and browser date defaults, using disposable databases only."""

import hashlib
from pathlib import Path
import tempfile
import unittest

from bifma.baseline import BaselineError, ensure_baseline
from bifma.document_export import DocumentExporter
from bifma.document_register import DocumentRegister
from bifma.masters import MasterService
from bifma.security import AuthorizationError, SecurityService
from bifma.transactions import TransactionError, TransactionService
from tests.test_baseline_import import synthetic_archive
from tests.test_transactions import TransactionFixture


class SaleReferenceTests(TransactionFixture, unittest.TestCase):
    def test_bill_reference_survives_review_post_restart_and_print_without_changing_amounts(self):
        self.opening()
        reference = '00012<&"'
        draft = self.save("sale", self.trade_values(bill_number=reference))
        self.assertEqual(draft["values"]["bill_number"], reference)
        self.assertEqual(draft["snapshot"]["bill_number"], reference)
        posted = self.post(draft)
        restarted = TransactionService(self.path, security=self.security).get(self.actor, self.company, posted["id"])
        self.assertEqual(restarted["snapshot"]["bill_number"], reference)
        self.assertEqual(restarted["total_amount"], "10")
        self.assertEqual(self.quantity(), 9)
        output = Path(self.directory.name) / "sale.html"
        DocumentExporter(self.path, security=self.security).export_html(self.actor, self.company, posted["id"], output)
        html = output.read_text()
        self.assertIn("Bill number", html)
        self.assertIn("00012&lt;&amp;&quot;", html)

    def test_clearing_bill_reference_and_old_drafts_keep_existing_posting_contract(self):
        self.opening()
        draft = self.save("sale", self.trade_values(bill_number="0007"))
        values = {**draft["values"], "bill_number": ""}
        edited = self.service.save_draft(self.actor, self.company, "sale", values,
                                        document_id=draft["id"], expected_revision=draft["revision"])
        self.assertNotIn("bill_number", edited["values"])
        self.assertNotIn("bill_number", edited["snapshot"])
        self.assertEqual(self.post(edited)["total_amount"], "10")
        old = self.save("sale", self.trade_values())
        self.assertNotIn("bill_number", self.post(old)["snapshot"])

    def test_invalid_references_and_unrelated_document_kinds_are_rejected(self):
        for reference in (12, None, "x" * 81, "invalid\x00reference"):
            with self.subTest(reference=reference), self.assertRaises(TransactionError):
                self.save("sale", self.trade_values(bill_number=reference))
        with self.assertRaises(TransactionError):
            self.save("purchase", self.trade_values("purchase", bill_number="0001"))


class SaleDateBoundsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.source = Path(folder.name) / "original.sqlite3"
        self.path = Path(folder.name) / "working.sqlite3"
        synthetic_archive(self.source)
        ensure_baseline(self.source, self.path)
        self.security = SecurityService(self.path)
        self.actor = self.security.bootstrap_admin("owner", "Silver lanterns orbit quietly 72")
        self.company = self.security.accessible_companies(self.actor)[0][0]
        self.register = DocumentRegister(self.path, self.source, security=self.security)

    def test_date_defaults_cover_original_and_new_drafts_without_mutating_archive(self):
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.assertEqual(self.register.date_bounds(self.actor, self.company, "sale"),
                         {"date_from": "2026-01-02", "date_to": "2026-01-02"})
        choices = MasterService(self.path, security=self.security).lookup(self.actor, self.company, "sales", "create")
        party = next(row for row in choices["accounts"] if row["code"] == "0002")
        values = {"document_number": "DATE-1", "business_date": "2026-02-02", "rounding_policy": "legacy_whole",
                  "posting_places": 0, "account_id": party["id"],
                  "lines": [{"product_id": choices["products"][0]["id"], "store_id": choices["stores"][0]["id"],
                             "quantity": "1", "rate": "10"}]}
        TransactionService(self.path, security=self.security).save_draft(self.actor, self.company, "sale", values)
        bounds = self.register.date_bounds(self.actor, self.company, "sale")
        self.assertEqual(bounds, {"date_from": "2026-01-02", "date_to": "2026-02-02"})
        self.assertEqual(len(self.register.list_documents(self.actor, self.company, "sale", **bounds)), 2)
        self.assertEqual(self.register.date_bounds(self.actor, self.company, "cash_sale"),
                         {"date_from": None, "date_to": None})
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), digest)

    def test_date_defaults_require_fresh_section_grants_and_unchanged_originals(self):
        user = self.security.create_user(self.actor, "reader", "Copper orchard 42 window violet")
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.set_permissions(self.actor, user.id, "sales", ["read"])
        reader = self.security.authenticate("reader", "Copper orchard 42 window violet")
        self.assertEqual(self.register.date_bounds(reader, self.company, "sale")["date_from"], "2026-01-02")
        self.security.set_permissions(self.actor, user.id, "sales", [])
        with self.assertRaises(AuthorizationError):
            self.register.date_bounds(reader, self.company, "sale")
        with self.source.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaises(BaselineError):
            self.register.date_bounds(self.actor, self.company, "sale")
