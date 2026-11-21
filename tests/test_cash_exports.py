"""Saved full-cash document copies and authorized cash/account report exports."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from bifma import document_export, report_export
from bifma.document_export import DocumentExportError, DocumentExporter
from bifma.native_schema import native_transaction
from bifma.report_export import ReportExporter
from bifma.security import AuthorizationError
from tests.test_cash_reports import CashReportFixture


class CashExportTests(CashReportFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.documents = DocumentExporter(self.path, self.security)
        self.exports = ReportExporter(self.path, self.security)

    def document(self, saved, filename="document.html", actor=None):
        destination = self.path.parent / filename
        self.documents.export_html(actor or self.actor, self.company, saved["id"], destination)
        return destination.read_text(encoding="utf-8")

    def report_html(self, report="sales_detail", filename="report.html", actor=None, **filters):
        destination = self.path.parent / filename
        self.exports.export_html(actor or self.actor, self.company, report, destination, **filters)
        return destination.read_text(encoding="utf-8")

    def test_cash_titles_full_amount_contacts_and_original_references_are_saved(self):
        self.opening(quantity="100")
        contact = 'Synthetic <customer> & "supplier"'
        documents = []
        for kind, title, returned, return_title, contact_title in (
            ("cash_sale", "Counter sales invoice", "cash_sale_return", "Counter sale return", "Customer"),
            ("cash_purchase", "Cash purchase invoice", "cash_purchase_return", "Cash purchase return", "Supplier"),
        ):
            original = self.invoice(kind, quantity="3", rate="7.125", counterparty_name=contact, counterparty_mobile="000-demo-123")
            credit = self.invoice(returned, quantity="1", rate="7.125", source=original["lines"][0]["id"], when="2026-01-03")
            correction = self.reverse(credit, "2026-01-04")
            documents.extend(((original, title, contact_title, None), (credit, return_title, contact_title, original), (correction, "Reversal", contact_title, None)))
        with native_transaction(self.path) as connection:
            for table in ("companies", "accounts", "products", "stores", "units"):
                connection.execute(f"UPDATE {table} SET name='Later renamed label'")
        for index, (saved, title, contact_title, source) in enumerate(documents):
            with self.subTest(kind=saved["kind"]):
                html = self.document(saved, f"cash-{index}.html")
                self.assertIn(f"<h1>{title}</h1>", html)
                self.assertIn("Cash account", html)
                self.assertIn(contact_title + " mobile", html)
                self.assertIn("Synthetic &lt;customer&gt; &amp; &quot;supplier&quot;", html)
                self.assertIn("000-demo-123", html)
                self.assertNotIn(contact, html)
                self.assertNotIn("Later renamed label", html)
                self.assertIn(">Cash</bdi>", html)
                self.assertIn("Reversed cash amount" if saved["kind"] == "reversal" else "Full cash amount", html)
                self.assertIn(">" + saved["calculation"]["total"] + "</bdi>", html)
                self.assertNotIn("Amount due", html)
                self.assertNotIn("Tendered", html)
                if source:
                    self.assertIn("Returns document", html)
                    self.assertIn(source["document_number"], html)

    def test_cash_zero_amount_still_prints_stock_and_no_accounting(self):
        original = self.invoice("cash_purchase", rate="0", quantity="2")
        html = self.document(original)
        self.assertIn("Full cash amount", html)
        self.assertIn("No accounting entries.", html)
        self.assertIn("Posted stock quantities", html)
        self.assertIn(">In</bdi>", html)

    def test_old_standard_document_with_cash_account_has_no_cash_settlement_label(self):
        original = self.post(self.save("purchase", self.trade_values("purchase", account_id=self.accounts["cash"])))
        html = self.document(original)
        self.assertIn("<h1>Purchase invoice</h1>", html)
        self.assertNotIn("Full cash settlement", html)
        self.assertNotIn("Full cash amount", html)

    def test_missing_inconsistent_cash_snapshots_fail_before_publication(self):
        original = self.invoice("cash_purchase", quantity="2")
        returned = self.invoice("cash_purchase_return", source=original["lines"][0]["id"])
        reversed_return = self.reverse(returned)
        cases = []
        for field, value in (("mode", "account"), ("cash_account_id", self.accounts["bank"]),
                             ("counterparty_name", None), ("counterparty_mobile", "x" * 81)):
            altered = deepcopy(original)
            altered["snapshot"]["cash_settlement"][field] = value
            cases.append(altered)
        altered = deepcopy(original)
        altered["snapshot"].pop("cash_settlement")
        cases.append(altered)
        altered = deepcopy(original)
        altered["calculation"]["total"] = "999"
        cases.append(altered)
        altered = deepcopy(returned)
        altered["snapshot"]["return_of"]["kind"] = "purchase"
        cases.append(altered)
        altered = deepcopy(reversed_return)
        altered["snapshot"]["cash_settlement"]["counterparty_name"] = "Changed reversal identity"
        cases.append(altered)
        altered = deepcopy(reversed_return)
        altered["snapshot"]["reversal_of"].pop("kind")
        cases.append(altered)
        for index, altered in enumerate(cases):
            destination = self.path.parent / f"invalid-{index}.html"
            with self.subTest(index=index), patch.object(self.documents.transactions, "get", return_value=altered), self.assertRaises(DocumentExportError):
                self.documents.export_html(self.actor, self.company, original["id"], destination)
            self.assertFalse(destination.exists())
        self.assertEqual(list(self.path.parent.glob(".bifma-document-*")), [])

    def test_cash_document_and_report_reads_use_counter_grant_not_sales_grant(self):
        self.opening()
        cash = self.invoice("cash_sale")
        account = self.invoice("sale")
        user, actor = self.reader(["counter_sales"])
        self.assertIn("Counter sales invoice", self.document(cash, "allowed.html", actor))
        with self.assertRaises(AuthorizationError):
            self.document(account, "denied-account.html", actor)
        html = self.report_html(actor=actor, payment_mode="cash", kind="invoice")
        self.assertIn("Payment mode: <bdi", html)
        self.assertIn(cash["document_number"], html)
        self.assertNotIn(account["document_number"] + "</bdi>", html)
        self.security.set_permissions(self.actor, user.id, "counter_sales", [])
        self.security.set_permissions(self.actor, user.id, "sales", ["read"])
        with self.assertRaises(AuthorizationError):
            self.document(cash, "denied-cash.html", actor)
        with self.assertRaises(AuthorizationError):
            self.report_html(filename="denied-report.html", actor=actor, payment_mode="cash", kind="invoice")

    def test_combined_export_rechecks_every_required_grant_after_render(self):
        self.opening()
        self.invoice("cash_sale")
        self.invoice("sale")
        modules = ("sales", "counter_sales", "sales_returns")
        user, actor = self.reader(modules)
        real_render = report_export._render
        for module in modules:
            with self.subTest(module=module):
                def revoke(snapshot):
                    html = real_render(snapshot)
                    self.security.set_permissions(self.actor, user.id, module, [])
                    return html
                destination = self.path.parent / (module + ".html")
                with patch.object(report_export, "_render", side_effect=revoke), self.assertRaises(AuthorizationError):
                    self.exports.export_html(actor, self.company, "sales_detail", destination, payment_mode="all")
                self.assertFalse(destination.exists())
                self.assertEqual(list(self.path.parent.glob(".bifma-report-*")), [])
                self.security.set_permissions(self.actor, user.id, module, ["read"])

    def test_report_mode_filter_saved_labels_and_header_filter_omission(self):
        cash = self.invoice("cash_purchase", quantity="2", rate="17", discount="1", document_number="CASH-INVOICE")
        account = self.invoice("purchase", rate="3", document_number="ACCOUNT-INVOICE")
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET name='Current renamed cash' WHERE id=?", (self.accounts["cash"],))
        html = self.report_html("purchase_detail", payment_mode="cash")
        self.assertIn("Payment mode: ", html)
        self.assertIn(">Cash</bdi>", html)
        self.assertIn(cash["document_number"], html)
        self.assertNotIn(account["document_number"], html)
        self.assertNotIn("Current renamed cash", html)
        self.assertIn("Full-period quantities by item and saved base unit", html)
        self.assertIn("Final document totals (each document once)", html)
        self.assertIn(">33</bdi>", html)
        combined = self.report_html("purchase_detail", "all.html", payment_mode="all")
        self.assertIn("Account and cash", combined)
        self.assertIn(account["document_number"], combined)
        filtered = self.report_html("purchase_detail", "filtered.html", payment_mode="cash", product_id=self.product)
        self.assertIn("Final document totals are omitted", filtered)
        default = self.report_html("purchase_detail", "account.html")
        self.assertNotIn(cash["document_number"], default)


if __name__ == "__main__":
    unittest.main()
