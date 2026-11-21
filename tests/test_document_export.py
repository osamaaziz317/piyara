"""Posted HTML output uses synthetic native data and new temporary files only."""

from html.parser import HTMLParser
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma.database import publish_new_file
from bifma.document_export import DocumentExporter, DocumentExportError
from bifma.native_schema import initialize_database, native_transaction
from bifma.security import AuthorizationError, SecurityService
from bifma.transactions import TransactionService


class ParsedDocument(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.elements, self.text = [], []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, text):
        self.text.append(text)


class DocumentExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.directory = Path(self.directory.name)
        self.path = self.directory / "native.sqlite3"
        initialize_database(self.path)
        self.security = SecurityService(self.path)
        self.actor = self.security.bootstrap_admin("owner", "Copper mornings surround meadow 92")
        self.service = TransactionService(self.path, self.security)
        self.exporter = DocumentExporter(self.path, self.security)
        self.company, self.store, self.product, self.unit, self.large_unit = [str(uuid4()) for _ in range(5)]
        self.accounts = {key: str(uuid4()) for key in ("customer", "sales", "purchases", "inventory", "equity", "cash", "bank")}
        self.number = 0
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO companies(id,code,name,name_urdu,fiscal_start,fiscal_end) VALUES (?,'CO','Issued company','بفما ٹریڈرز','2026-01-01','2026-12-31')", (self.company,))
            for key, identifier in self.accounts.items():
                kind = key if key in {"customer", "cash", "bank"} else "general"
                connection.execute("INSERT INTO accounts(id,code,name,name_urdu,kind) VALUES (?,?,?,?,?)", (identifier, key.upper(), "Issued " + key, "کھاتہ", kind))
            connection.execute("INSERT INTO stores(id,code,name,name_urdu) VALUES (?,'S1','Issued store','گودام')", (self.store,))
            connection.execute("INSERT INTO company_stores(company_id,store_id) VALUES (?,?)", (self.company, self.store))
            connection.execute("INSERT INTO units(id,code,name,name_urdu) VALUES (?,'PC','Issued piece','عدد')", (self.unit,))
            connection.execute("INSERT INTO units(id,code,name,name_urdu) VALUES (?,'CTN','Issued carton','ڈبہ')", (self.large_unit,))
            connection.execute("INSERT INTO products(id,code,name,name_urdu,small_unit_id,large_unit_id,unit_factor,sale_account_id,purchase_account_id,inventory_account_id) VALUES (?,'P1','Issued item','پلاسٹک کا سامان',?,?,'12',?,?,?)", (self.product, self.unit, self.large_unit, self.accounts["sales"], self.accounts["purchases"], self.accounts["inventory"]))

    def common(self, *, whole=False):
        self.number += 1
        return {"document_number": "N-" + str(self.number), "business_date": "2026-10-01",
                "rounding_policy": "legacy_whole" if whole else "cents", "posting_places": 0 if whole else 2, "lines": []}

    def posted(self, kind, values):
        draft = self.service.save_draft(self.actor, self.company, kind, values)
        return self.service.post(self.actor, self.company, draft["id"], draft["revision"])

    def trade(self, *, rate="17.125", quantity="1.25", whole=False, **fields):
        values = self.common(whole=whole)
        values.update(account_id=self.accounts["customer"], discount="0.1", breakage="0.02", freight="0.03",
                      lines=[{"product_id": self.product, "store_id": self.store, "quantity": quantity, "rate": rate, "discount_percent": "10"}])
        values.update(fields)
        return values

    def export(self, document, filename="document.html", *, actor=None):
        destination = self.directory / filename
        result = self.exporter.export_html(actor or self.actor, self.company, document["id"], destination)
        self.assertEqual(result, destination)
        return destination.read_text(encoding="utf-8")

    def test_bilingual_snapshot_labels_exact_rounding_and_private_standalone_layout(self):
        purchase = self.posted("purchase", self.trade(whole=True))
        with native_transaction(self.path) as connection:
            for table in ("companies", "accounts", "products", "stores", "units"):
                connection.execute(f"UPDATE {table} SET name='Later renamed master',name_urdu='بعد کا نام'")
        content = self.export(purchase)
        for expected in ("Issued company", "Issued customer", "Issued purchases", "Issued item", "Issued store", "Issued piece", "Issued carton", "بفما ٹریڈرز", "کھاتہ", "پلاسٹک کا سامان", "عدد", "ڈبہ"):
            self.assertIn(expected, content)
        self.assertNotIn("Later renamed master", content)
        self.assertNotIn("بعد کا نام", content)
        for amount in ("1.25", "17.125", "19.265625", "-0.265625", "18.91", "0.09", "19"):
            self.assertIn(f'>{amount}</bdi>', content)
        self.assertIn("Less header discount", content)
        self.assertIn("Less breakage allowance", content)
        self.assertIn("Add freight", content)
        self.assertIn("Final rounding adjustment", content)
        self.assertIn("Base units per large unit:", content)
        self.assertIn('lang="ur"', content)
        self.assertIn('dir="auto"', content)
        self.assertIn('charset="utf-8"', content)
        self.assertIn("size: A4", content)
        self.assertIn("@media print", content)
        self.assertIn("Content-Security-Policy", content)
        self.assertNotIn("Paid", content)
        self.assertNotIn("Due", content)
        self.assertNotIn("Tax", content)
        self.assertNotIn("unit_cost", content)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE((self.directory / "document.html").stat().st_mode), 0o600)
        self.assertEqual(list(self.directory.glob(".bifma-document-*")), [])

    def test_all_user_content_is_text_not_html_or_network_activity(self):
        malicious = '<script>alert("x")</script><img src="https://invalid.example/a" onerror="bad()"> & \'quote\''
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET name=? WHERE id=?", (malicious, self.company))
            connection.execute("UPDATE products SET name_urdu=? WHERE id=?", (malicious, self.product))
            connection.execute("UPDATE accounts SET name=? WHERE id=?", (malicious, self.accounts["customer"]))
        values = self.trade(notes=malicious)
        values["document_number"] = 'INV-<svg onload="bad()">'
        document = self.posted("purchase", values)
        content = self.export(document)
        parsed = ParsedDocument(content)
        self.assertIn(malicious, "".join(parsed.text))
        self.assertIn('INV-&lt;svg onload=&quot;bad()&quot;&gt;', content)
        self.assertIn("&amp; &#x27;quote&#x27;", content)
        for tag, attributes in parsed.elements:
            self.assertNotIn(tag, {"script", "img", "iframe", "object", "embed", "link", "form", "svg"})
            self.assertFalse(any(key.startswith("on") or key in {"src", "href", "action"} for key in attributes))

    def test_contact_details_are_frozen_escaped_and_never_loaded_from_current_masters(self):
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET address=?,address_urdu=?,phone=?,sales_tax_number=?,national_tax_number=? WHERE id=?",
                               ('Issued <warehouse> & street', 'اصل پتہ', '001-555-0100', '00017-A', '00018-B', self.company))
            connection.execute("UPDATE accounts SET address=?,email=?,website=?,mobile=? WHERE id=?",
                               ('Issued party address', '<script>@example.invalid', 'https://example.invalid/?x=<x>', '001-555-0200', self.accounts['customer']))
        document = self.posted("purchase", self.trade())
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET address='Later address',address_urdu='بعد کا پتہ',phone='Later phone',sales_tax_number='Later tax',national_tax_number='Later national'")
            connection.execute("UPDATE accounts SET address='Later party',email='Later email',website='Later website',mobile='Later mobile'")
        content = self.export(document)
        for expected in ('Issued &lt;warehouse&gt; &amp; street', 'اصل پتہ', '001-555-0100', '00017-A', '00018-B',
                         'Issued party address', '&lt;script&gt;@example.invalid', 'https://example.invalid/?x=&lt;x&gt;', '001-555-0200'):
            self.assertIn(expected, content)
        self.assertNotIn('Later ', content)
        for tag, attrs in ParsedDocument(content).elements:
            self.assertNotIn(tag, {'script', 'a', 'img'})
            self.assertFalse(any(key in {'src', 'href'} or key.startswith('on') for key in attrs))

    def test_free_goods_keep_zero_amount_and_stock_without_invented_accounting(self):
        values = self.trade(rate="0", quantity="2", discount="0", breakage="0", freight="0", notes="")
        document = self.posted("purchase", values)
        self.assertEqual(document["journal_lines"], [])
        content = self.export(document)
        self.assertIn("No accounting entries.", content)
        self.assertIn("Posted stock quantities", content)
        self.assertIn('>0</bdi>', content)
        self.assertIn('>2</bdi>', content)
        self.assertNotIn("<h2>Notes</h2>", content)

    def test_sale_bill_has_snapshot_buyer_rtl_packing_exact_values_and_safe_remarks(self):
        self.posted("purchase", self.trade(quantity="24", rate="5"))
        values = self.trade(quantity="24", rate="10.125", bill_number='00014<&"',
                            notes='<img src="https://invalid.example"> اصل تفصیل')
        values["lines"][0].update(unit_id=self.large_unit, entered_quantity="2")
        document = self.posted("sale", values)
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET name='Changed party',name_urdu='بدلا نام' WHERE id=?", (self.accounts["customer"],))
        content = self.export(document)
        printable = content.split('<details class="record-details">', 1)[0]
        for expected in ("BT", "سیل بل", "Sale Voucher", "Buyer", "Issued customer", "Issued item", "پلاسٹک کا سامان", "Issued carton", "00014&lt;&amp;&quot;", "218.61"):
            self.assertIn(expected, printable)
        for amount in ("2", "12", "24", "10.125", "218.7", "24.3"):
            self.assertIn('>' + amount + '</bdi>', printable)
        self.assertIn('direction: rtl', printable)
        self.assertIn('class="bill-lines"', printable)
        self.assertIn("01-OCT-26", printable)
        self.assertNotIn("Issued store", printable)
        self.assertNotIn("Changed party", content)
        self.assertNotIn("Posted accounting", printable)
        self.assertNotIn("DC #", printable)
        self.assertNotIn("Balance due", content)
        for tag, attrs in ParsedDocument(content).elements:
            self.assertNotIn(tag, {"script", "img", "iframe", "svg"})
            self.assertFalse(any(key in {"src", "href"} or key.startswith("on") for key in attrs))

    def test_breakage_bill_keeps_accounting_details_outside_the_printed_page(self):
        document = self.posted("breakage_receive", self.trade(rate="10", quantity="2", discount="0", freight="0", breakage="1"))
        content = self.export(document)
        printable, details = content.split('<details class="record-details">', 1)
        self.assertIn("Breakage receive", printable)
        self.assertIn("Issued customer", printable)
        self.assertIn('>17</bdi>', printable)
        self.assertIn("Less breakage allowance", printable)
        self.assertNotIn("Store</", printable)
        self.assertNotIn("Posted accounting", printable)
        self.assertIn("Posted accounting", details)
        self.assertIn(".record-details { display: none; }", content)

    def test_breakage_prints_saved_directions_accounts_and_only_supported_adjustments(self):
        receive = self.posted("breakage_receive", self.trade(rate="10", quantity="2", discount="0", freight="0", breakage="1"))
        dispatch = self.posted("breakage_dispatch", self.trade(rate="5", quantity="1", discount="0", freight="0", breakage="0"))
        for kind, document, direction, account, total in (("receive", receive, "in", "sales", "17"), ("dispatch", dispatch, "out", "purchases", "4.5")):
            with self.subTest(kind=kind):
                content = self.export(document, kind + ".html")
                self.assertIn("Breakage " + kind, content)
                self.assertIn("Issued " + account, content)
                self.assertIn('>' + total + '</bdi>', content)
                self.assertIn('>' + direction.title() + '</bdi>', content)
                self.assertIn('>breakage</bdi>', content)
                self.assertNotIn("Add freight", content)
                self.assertNotIn("Less header discount", content)
                self.assertEqual("Less breakage allowance" in content, kind == "receive")

    def test_all_cash_bank_and_journal_entries_and_large_exact_amount_are_rendered(self):
        cases = [("cash_receipt", "cash"), ("cash_payment", "cash"), ("bank_receipt", "bank"), ("bank_payment", "bank"), ("journal", None), ("opening_balance", None)]
        for kind, account in cases:
            with self.subTest(kind=kind):
                values = self.common()
                amount = "9007199254740993.01"
                if account:
                    values.update(account_id=self.accounts[account], lines=[{"account_id": self.accounts["customer"], "amount": amount, "narration": "Exact voucher amount"}])
                else:
                    values["lines"] = [{"account_id": self.accounts["cash"], "debit": amount, "credit": "0", "narration": "Exact voucher amount"}, {"account_id": self.accounts["customer"], "debit": "0", "credit": amount}]
                document = self.posted(kind, values)
                content = self.export(document, kind + ".html")
                self.assertIn("Issued customer", content)
                self.assertIn("Issued " + (account or "cash"), content)
                self.assertEqual(content.count('>' + amount + '</bdi>'), 3)
                self.assertIn("Exact voucher amount", content)
                self.assertNotIn("<h2>Items</h2>", content)
                self.assertNotIn("Raw line subtotal", content)
                self.assertNotIn("Posted stock quantities", content)

    def test_opening_stock_has_explicit_value_pool_and_contra_account(self):
        values = self.common()
        values.update(contra_account_id=self.accounts["equity"], lines=[{"product_id": self.product, "store_id": self.store, "quantity": "2.125", "value": "31.25", "pool": "breakage"}])
        document = self.posted("stock_opening", values)
        content = self.export(document)
        self.assertIn("Opening value", content)
        self.assertIn("Opening balance counterpart", content)
        self.assertIn("Issued equity", content)
        self.assertIn("Issued inventory", content)
        self.assertIn('>breakage</bdi>', content)
        self.assertIn('>2.125</bdi>', content)
        self.assertIn('>31.25</bdi>', content)
        self.assertNotIn("Raw line subtotal", content)

    def test_reversal_and_reversed_original_keep_labels_references_and_reason(self):
        original = self.posted("purchase", self.trade())
        reversal = self.service.reverse(self.actor, self.company, original["id"], original["revision"], document_number="REV-001", business_date="2026-10-02", reason="Wrong entry <corrected>\nوجہ")
        content = self.export(reversal, "reversal.html")
        self.assertIn("Reverses document", content)
        self.assertIn('>' + original["document_number"] + '</bdi>', content)
        self.assertIn('>REV-001</bdi>', content)
        self.assertIn("2026-10-01", content)
        self.assertIn("2026-10-02", content)
        self.assertIn("Wrong entry &lt;corrected&gt;\nوجہ", content)
        self.assertIn("Issued item", content)
        self.assertIn("Issued customer", content)
        self.assertIn("Original line amount", content)
        self.assertIn('>Out</bdi>', content)
        source_copy = self.export(original, "original.html")
        self.assertIn("Reversed by document", source_copy)
        self.assertIn('>REV-001</bdi>', source_copy)

    def test_draft_and_missing_snapshot_are_rejected_without_creating_files(self):
        draft = self.service.save_draft(self.actor, self.company, "purchase", self.trade())
        destination = self.directory / "draft.html"
        with self.assertRaisesRegex(DocumentExportError, "Only posted"):
            self.exporter.export_html(self.actor, self.company, draft["id"], destination)
        self.assertFalse(destination.exists())
        unverified = dict(draft, status="posted", snapshot=None)
        with patch.object(self.exporter.transactions, "get", return_value=unverified), self.assertRaisesRegex(DocumentExportError, "saved snapshot"):
            self.exporter.export_html(self.actor, self.company, draft["id"], destination)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.directory.glob(".bifma-document-*")), [])

    def test_live_document_permission_and_company_access_are_required_for_every_export(self):
        document = self.posted("purchase", self.trade())
        user = self.security.create_user(self.actor, "reader", "Sunny orchards circle meadow 84")
        reader = self.security.authenticate("reader", "Sunny orchards circle meadow 84")
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.set_permissions(self.actor, user.id, "purchases", ["read"])
        self.export(document, "allowed.html", actor=reader)
        self.security.set_permissions(self.actor, user.id, "purchases", [])
        with self.assertRaises(AuthorizationError):
            self.export(document, "revoked.html", actor=reader)
        self.assertFalse((self.directory / "revoked.html").exists())
        self.security.set_permissions(self.actor, user.id, "purchases", ["read"])
        self.security.set_company_access(self.actor, user.id, [])
        with self.assertRaises(AuthorizationError):
            self.export(document, "wrong-company.html", actor=reader)
        self.assertFalse((self.directory / "wrong-company.html").exists())

    def test_existing_file_and_dangling_symlink_are_never_overwritten(self):
        document = self.posted("purchase", self.trade())
        target = self.directory / "existing.html"
        target.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.exporter.export_html(self.actor, self.company, document["id"], target)
        self.assertEqual(target.read_text(), "keep")
        link = self.directory / "link.html"
        try:
            link.symlink_to(self.directory / "missing.html")
        except OSError:
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(FileExistsError):
            self.exporter.export_html(self.actor, self.company, document["id"], link)
        self.assertFalse((self.directory / "missing.html").exists())

    def test_publication_failure_removes_stage_and_never_exposes_partial_destination(self):
        document = self.posted("purchase", self.trade())
        destination = self.directory / "failed.html"

        def fail(staged, target):
            self.assertEqual(target, destination)
            self.assertTrue(staged.read_text(encoding="utf-8").endswith("</html>\n"))
            self.assertFalse(target.exists())
            raise OSError("Synthetic publication failure")

        with patch("bifma.document_export.publish_new_file", side_effect=fail), self.assertRaisesRegex(OSError, "publication"):
            self.exporter.export_html(self.actor, self.company, document["id"], destination)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.directory.glob(".bifma-document-*")), [])

    def test_destination_created_during_export_wins_without_data_loss(self):
        document = self.posted("purchase", self.trade())
        destination = self.directory / "race.html"

        def race(staged, target):
            target.write_text("Another writer", encoding="utf-8")
            publish_new_file(staged, target)

        with patch("bifma.document_export.publish_new_file", side_effect=race), self.assertRaises(FileExistsError):
            self.exporter.export_html(self.actor, self.company, document["id"], destination)
        self.assertEqual(destination.read_text(encoding="utf-8"), "Another writer")
        self.assertEqual(list(self.directory.glob(".bifma-document-*")), [])

    def test_failure_after_link_publication_removes_own_complete_destination(self):
        document = self.posted("purchase", self.trade())
        destination = self.directory / "linked.html"

        def fail_after_link(staged, target):
            publish_new_file(staged, target)
            self.assertTrue(target.read_text(encoding="utf-8").endswith("</html>\n"))
            raise OSError("Synthetic directory sync failure")

        with patch("bifma.document_export.publish_new_file", side_effect=fail_after_link), self.assertRaisesRegex(OSError, "sync failure"):
            self.exporter.export_html(self.actor, self.company, document["id"], destination)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.directory.glob(".bifma-document-*")), [])

    def test_failed_publication_cleanup_preserves_competing_replacement(self):
        document = self.posted("purchase", self.trade())
        destination = self.directory / "replaced.html"

        def replace_after_link(staged, target):
            publish_new_file(staged, target)
            replacement = self.directory / "other-writer.html"
            replacement.write_text("Keep the replacement", encoding="utf-8")
            os.replace(replacement, target)
            raise OSError("Synthetic post-link failure")

        with patch("bifma.document_export.publish_new_file", side_effect=replace_after_link), self.assertRaisesRegex(OSError, "post-link"):
            self.exporter.export_html(self.actor, self.company, document["id"], destination)
        self.assertEqual(destination.read_text(encoding="utf-8"), "Keep the replacement")
        self.assertEqual(list(self.directory.glob(".bifma-document-*")), [])


if __name__ == "__main__":
    unittest.main()
