"""Full report exports use synthetic native databases and private temp files."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Context, Decimal, localcontext
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import stat
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma import native_reports, report_export
from bifma.database import publish_new_file
from bifma.native_reports import MAX_EXPORT_ROWS, NativeReports
from bifma.native_schema import native_session, native_transaction
from bifma.report_export import ReportExporter
from bifma.security import AuthenticationError, AuthorizationError
from bifma.transactions import TransactionService
from tests import test_native_reports as report_test_helpers


class ParsedReport(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.elements, self.text = [], []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, value):
        self.text.append(value)


class FinancialPrintLayoutTests(unittest.TestCase):
    """Print layouts consume the verified snapshot without deriving new amounts."""

    def snapshot(self, report, result):
        return {"report": report, "company": {"code": "BT", "name": "Synthetic traders"},
                "filters": {"date_from": "2026-10-01", "date_to": "2026-10-02"},
                "generated_at": "2026-10-03T12:00:00+00:00", "result": result}

    def test_cash_book_has_payment_left_receipt_right_and_preserves_each_saved_row(self):
        result = {"account": {"code": "CASH", "name": "Synthetic cash"},
                  "opening": Decimal("9007199254740993.01"),
                  "debit": Decimal("12.123456789"), "credit": Decimal("5.000000001"),
                  "closing": Decimal("9007199254741000.133456788"),
                  "cash_role_source": "current_master", "rows": [
                      {"document_number": "RECEIPT-1", "kind": "cash_receipt", "business_date": "2026-10-01",
                       "narration": "Saved receipt narration", "debit": Decimal("12.123456789"), "credit": Decimal(0)},
                      {"document_number": "PAYMENT-1", "kind": "cash_payment", "business_date": "2026-10-02",
                       "narration": "Saved payment narration", "debit": Decimal(0), "credit": Decimal("5.000000001")}]}
        with localcontext(Context(prec=3)):
            content = report_export._render(self.snapshot("cash_book", result))
        payments, receipts = content.split('<section class="cash-panel">')[1:]
        self.assertIn("Cash payments", payments)
        self.assertIn("PAYMENT-1", payments)
        self.assertNotIn("RECEIPT-1", payments)
        self.assertIn("Cash receipts", receipts)
        self.assertIn("RECEIPT-1", receipts)
        self.assertNotIn("PAYMENT-1", receipts)
        self.assertEqual(content.count('<tr class="report-row">'), 2)
        for amount in ("9007199254740993.01", "12.123456789", "5.000000001", "9007199254741000.133456788"):
            self.assertIn(f'>{amount}</bdi>', content)
        self.assertIn(">01-10-2026</bdi>", content)
        self.assertIn(">02-10-2026</bdi>", content)
        self.assertIn("روزنامچہ", content)
        self.assertIn("size: A4 portrait", content)

    def test_party_ledger_column_order_signs_and_untrusted_text_remain_exact(self):
        attack = '<img src="https://invalid.example/" onerror="bad()"> & saved'
        result = {"account": {"code": "PARTY", "name": attack}, "opening": Decimal(0),
                  "debit": Decimal(0), "credit": Decimal("14.0001"), "closing": Decimal("-14.0001"),
                  "rows": [{"document_number": attack, "kind": "cash_receipt", "business_date": "2026-10-01",
                            "narration": attack, "debit": Decimal(0), "credit": Decimal("14.0001"),
                            "balance": Decimal("-14.0001")} ]}
        content = report_export._render(self.snapshot("account_ledger", result))
        table = content.split('<table class="report">')[1].split("</table>")[0]
        # These are the left-to-right physical cells: balance at the left and
        # voucher number/date at the right, as in the Oracle report output.
        headings = ["Balance", "Dr / Cr", "Credit", "Debit", "Narration", "Date", "Type", "Voucher"]
        self.assertEqual(sorted(headings, key=table.index), headings)
        self.assertIn('<td class="number balance"><bdi dir="ltr">-14.0001</bdi>', table)
        self.assertIn('<td class="side"><bdi dir="auto">Cr</bdi>', table)
        parsed = ParsedReport(content)
        self.assertIn(attack, "".join(parsed.text))
        self.assertFalse(any(tag in {"img", "script", "iframe", "a", "form"} for tag, attrs in parsed.elements))
        self.assertIn("کھاتہ نامہ", content)
        self.assertIn("negative balances are credit", content)

    def test_cash_empty_side_and_subtrial_grand_totals_are_explicit(self):
        amounts = {key: Decimal(0) for key in ("opening", "debit", "credit", "closing")}
        cash = {"account": {"name": "Empty cash"}, "cash_role_source": "saved_snapshot", "rows": [], **amounts}
        content = report_export._render(self.snapshot("cash_book", cash))
        self.assertEqual(content.count("No rows in the selected period."), 2)
        self.assertIn("Saved cash classification in a posted document", content)
        # Hierarchy subtotals overlap, so renderer must use snapshot grand totals
        # and never add the parent and child rows to invent a doubled total.
        row = {"code": "A", "name": "Synthetic account", "depth": 0,
               "opening": Decimal(0), "debit": Decimal("7.1234"), "credit": Decimal(0), "closing": Decimal("7.1234")}
        trial = {"rows": [row, {**row, "depth": 1, "name": "Synthetic child"}], "totals": {**amounts, "debit": Decimal("7.1234"), "closing": Decimal("7.1234")}}
        content = report_export._render(self.snapshot("sub_trial_balance", trial))
        self.assertEqual(content.count('<tr class="report-row">'), 2)
        self.assertIn("Grand totals (direct postings once)", content)
        self.assertIn("Do not add these overlapping subtotals together", content)
        self.assertNotIn("14.2468", content)


class ReportExportTests(unittest.TestCase):
    # Share only synthetic fixture helpers, without inheriting its test methods.
    document = report_test_helpers.NativeReportTests.document
    stock_document = report_test_helpers.NativeReportTests.stock_document
    stock_item = report_test_helpers.NativeReportTests.stock_item
    stock_store = report_test_helpers.NativeReportTests.stock_store

    def setUp(self):
        report_test_helpers.NativeReportTests.setUp(self)
        self.directory = self.path.parent
        self.exporter = ReportExporter(self.path, self.security)

    def export(self, report, filename="report.html", actor=None, company=None, **filters):
        destination = self.directory / filename
        result = self.exporter.export_html(actor or self.admin, company or self.company, report, destination, **filters)
        self.assertEqual(result, destination)
        return destination.read_text(encoding="utf-8")

    def reader(self, module):
        user = self.security.create_user(self.admin, "reader", "Silver orchard 63 green river")
        actor = self.security.authenticate("reader", "Silver orchard 63 green river")
        self.security.set_company_access(self.admin, user.id, [self.company])
        self.security.set_permissions(self.admin, user.id, module, ["read"])
        return user, actor

    def many_journal_lines(self, count=501):
        identifier, journal = str(uuid4()), str(uuid4())
        total = str(Decimal(count) / 10)
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO documents(id,company_id,kind,document_number,business_date,total_amount,rounding_policy,posting_places,created_by) VALUES (?,?,'journal','MANY','2026-10-01',?,'cents',2,?)", (identifier, self.company, total, self.admin.user.id))
            connection.execute("INSERT INTO journal_entries(id,company_id,document_id) VALUES (?,?,?)", (journal, self.company, identifier))
            connection.executemany("INSERT INTO journal_lines(id,company_id,entry_id,line_number,account_id,debit,credit,narration) VALUES (?,?,?,?,?,?,?,?)",
                                   [(str(uuid4()), self.company, journal, index, self.debit, "0.1", "0", f"Row {index}") for index in range(1, count + 1)]
                                   + [(str(uuid4()), self.company, journal, count + 1, self.credit, "0", total, "Counterpart")])
            connection.execute("UPDATE documents SET status='posted',revision=1,posted_by=?,posted_at=1,posting_sequence=1 WHERE id=?", (self.admin.user.id, identifier))

    def many_stock_documents(self, count=501):
        with native_transaction(self.path) as connection:
            for index in range(1, count + 1):
                identifier, line = str(uuid4()), str(uuid4())
                connection.execute("INSERT INTO documents(id,company_id,kind,document_number,business_date,total_amount,rounding_policy,posting_places,created_by) VALUES (?,?,'stock_opening',?,'2026-10-01','0','cents',2,?)", (identifier, self.company, f"ITEM-{index:04}", self.admin.user.id))
                connection.execute("INSERT INTO document_lines(id,company_id,document_id,line_number,product_id,store_id,quantity) VALUES (?,?,?,1,?,?,'0.125')", (line, self.company, identifier, self.product, self.store))
                connection.execute("INSERT INTO stock_movements(id,company_id,document_id,document_line_id,product_id,store_id,direction,quantity,pool) VALUES (?,?,?,?,?,?,'in','0.125','regular')", (str(uuid4()), self.company, identifier, line, self.product, self.store))
                snapshot = {"lines": [{"line_id": line, "product": {"id": self.product, "code": "OLD", "name": "Original item"}, "store": {"id": self.store, "code": "OLDSTORE", "name": "Original store"}}]}
                connection.execute("INSERT INTO document_metadata(document_id,company_id,input_json,snapshot_json,calculation_json) VALUES (?,?, '{}',?,'{}')", (identifier, self.company, json.dumps(snapshot)))
                connection.execute("UPDATE documents SET status='posted',revision=1,posted_by=?,posted_at=1,posting_sequence=? WHERE id=?", (self.admin.user.id, index, identifier))

    def test_account_ledger_exports_more_than_a_page_with_exact_opening_and_full_totals(self):
        self.document("OPEN", "2026-09-30", "9007199254740993.01")
        self.many_journal_lines()
        with localcontext(Context(prec=3)):
            content = self.export("account_ledger", account_id=self.debit, date_from="2026-10-01")
        self.assertEqual(content.count('<tr class="report-row">'), 501)
        for amount in ("9007199254740993.01", "9007199254740993.11", "9007199254741043.11", "50.1"):
            self.assertIn(f'>{amount}</bdi>', content)
        self.assertIn("Row 501", content)
        self.assertNotIn("Counterpart", content)
        self.assertIn("negative balances are credit", content)
        page = self.reports.ledger(self.admin, self.company, self.debit, date_from="2026-10-01", offset=500, limit=1)
        self.assertIn(f'>{page["rows"][0]["balance"]}</bdi>', content)

    def test_item_ledger_exports_all_snapshot_batches_in_order_with_saved_labels(self):
        self.stock_document("OPEN", "2026-09-30", [{"quantity": "9007199254740993.123456"}])
        self.many_stock_documents()
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE products SET name='Current item' WHERE id=?", (self.product,))
        content = self.export("item_ledger", product_id=self.product, date_from="2026-10-01")
        self.assertEqual(content.count('<tr class="report-row">'), 501)
        self.assertIn("Original item", content)
        self.assertIn("Current item", content)
        self.assertIn("Original store", content)
        self.assertIn('>62.625</bdi>', content)
        self.assertIn('>9007199254741055.748456</bdi>', content)
        self.assertLess(content.index("ITEM-0001"), content.index("ITEM-0501"))

    def test_trial_balance_bilingual_current_company_labels_and_company_isolation(self):
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET name='Current company',name_urdu='بفما ٹریڈرز',address='Factory road',address_urdu='کارخانہ روڈ' WHERE id=?", (self.company,))
            connection.execute("UPDATE accounts SET name_urdu='کھاتہ' WHERE id=?", (self.debit,))
        self.document("OPEN", "2026-09-30", "7.25")
        self.document("PERIOD", "2026-10-01", "2.1")
        self.document("OTHER COMPANY", "2026-10-01", "888", company=self.other)
        self.document("DRAFT", "2026-10-01", "999", posted=False)
        content = self.export("trial_balance", date_from="2026-10-01", date_to="2026-10-01")
        for value in ("Current company", "بفما ٹریڈرز", "کھاتہ", "Factory road", "کارخانہ روڈ", ">7.25</bdi>", ">-7.25</bdi>", ">9.35</bdi>", ">2.1</bdi>"):
            self.assertIn(value, content)
        self.assertNotIn(">888</bdi>", content)
        self.assertNotIn(">999</bdi>", content)
        self.assertEqual(content.count('<tr class="report-row">'), 2)

    def test_exact_aggregates_can_exceed_individual_record_magnitude_limits(self):
        integer_part = "9" * 64
        amount = integer_part + ".99"
        self.document("LARGE ONE", "2026-10-01", amount)
        self.document("LARGE TWO", "2026-10-02", amount)
        # Two .99 fractions carry a whole unit into the 65-digit integer sum.
        expected = str(2 * int(integer_part) + 1) + ".98"
        self.assertEqual(len(expected.split(".")[0]), 65)
        with localcontext(Context(prec=3)):
            content = self.export("trial_balance")
            account = self.export("account_ledger", filename="large-ledger.html", account_id=self.debit)
        self.assertIn(f'>{expected}</bdi>', content)
        self.assertIn(f'>-{expected}</bdi>', content)
        self.assertIn(f'>{expected}</bdi>', account)

    def test_stock_quantities_filters_and_different_items_have_no_grand_quantity(self):
        other_item = self.stock_item("ANOTHER")
        self.stock_document("QUANTITIES", "2026-10-01", [{"quantity": "7"}, {"quantity": "12", "product_id": other_item}, {"quantity": "3", "pool": "breakage"}])
        content = self.export("stock_quantities", pool="regular", store_id=self.store)
        self.assertEqual(content.count('<tr class="report-row">'), 2)
        self.assertNotIn("Full-period totals", content)
        self.assertNotIn(">19</bdi>", content)
        self.assertIn("No inventory valuation", content)
        selected = self.export("stock_quantities", filename="selected.html", search="another")
        self.assertEqual(selected.count('<tr class="report-row">'), 1)
        self.assertIn("ANOTHER", selected)
        breakage = self.export("stock_quantities", filename="breakage.html", pool="breakage")
        self.assertEqual(breakage.count('<tr class="report-row">'), 1)
        self.assertIn(">3</bdi>", breakage)

    def test_item_ledger_keeps_reversal_reference_and_exact_cancelling_quantities(self):
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET fiscal_start='2026-01-01',fiscal_end='2026-12-31' WHERE id=?", (self.company,))
        original = self.stock_document("ORIGINAL", "2026-10-01", [{"quantity": "2.125"}], snapshot=True, sequence=1)
        service = TransactionService(self.path, security=self.security)
        service.reverse(self.admin, self.company, original, 1, document_number="REVERSAL", business_date="2026-10-02", reason="Synthetic correction")
        content = self.export("item_ledger", product_id=self.product)
        self.assertEqual(content.count('<tr class="report-row">'), 2)
        self.assertIn("REVERSAL", content)
        self.assertIn('Reverses <bdi dir="auto">ORIGINAL</bdi>', content)
        self.assertIn(">2.125</bdi>", content)
        self.assertIn('<th scope="row">Closing quantity</th><td class="number"><bdi dir="ltr">0</bdi>', content)

    def test_escaped_injection_and_no_active_or_external_content(self):
        attack = '</title><script>alert("x")</script><img src="https://invalid.example/" onerror="bad()"> & \'quote\''
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET name=?,address=? WHERE id=?", (attack, attack, self.company))
            connection.execute("UPDATE products SET name=?,name_urdu=? WHERE id=?", (attack, attack, self.product))
        self.stock_document('<script>alert("x")</script>', "2026-10-01", [{"quantity": "1"}], snapshot=True)
        content = self.export("item_ledger", product_id=self.product)
        parsed = ParsedReport(content)
        self.assertIn(attack, "".join(parsed.text))
        for tag, attrs in parsed.elements:
            self.assertNotIn(tag, {"script", "img", "iframe", "object", "embed", "link", "form", "svg", "a"})
            self.assertFalse(any(key.startswith("on") or key in {"src", "href"} for key in attrs))
        for expected in ('charset="utf-8"', "Content-Security-Policy", "size: A4 landscape", "@media print", 'dir="auto"', 'dir="ltr"', 'lang="ur"'):
            self.assertIn(expected, content)
        self.assertIn("&amp; &#x27;quote&#x27;", content)

    def test_permissions_are_live_company_scoped_and_module_specific(self):
        user, actor = self.reader("ledger")
        self.export("account_ledger", actor=actor, account_id=self.debit)
        for report, filters in (("trial_balance", {}), ("stock_quantities", {}), ("item_ledger", {"product_id": self.product})):
            with self.subTest(report=report), self.assertRaises(AuthorizationError):
                self.export(report, filename="denied.html", actor=actor, **filters)
        with self.assertRaises(AuthorizationError):
            self.export("account_ledger", filename="denied.html", actor=actor, company=self.other, account_id=self.debit)
        self.security.set_permissions(self.admin, user.id, "ledger", [])
        with self.assertRaises(AuthorizationError):
            self.export("account_ledger", filename="denied.html", actor=actor, account_id=self.debit)
        self.assertFalse((self.directory / "denied.html").exists())

    def test_expired_session_creates_no_output(self):
        with patch.object(self.security, "clock", return_value=self.admin.expires_at + 1), self.assertRaises(AuthenticationError):
            self.export("trial_balance")
        self.assertFalse((self.directory / "report.html").exists())

    def test_permission_revocation_during_render_prevents_publication(self):
        user, actor = self.reader("trial_balance")
        render = report_export._render
        def revoke(snapshot):
            content = render(snapshot)
            self.security.set_permissions(self.admin, user.id, "trial_balance", [])
            return content
        with patch.object(report_export, "_render", side_effect=revoke), self.assertRaises(AuthorizationError):
            self.export("trial_balance", actor=actor)
        self.assertFalse((self.directory / "report.html").exists())
        self.assertEqual(list(self.directory.glob(".bifma-report-*")), [])

    def test_company_grant_revocation_during_render_prevents_publication(self):
        user, actor = self.reader("stock")
        render = report_export._render
        def revoke(snapshot):
            content = render(snapshot)
            self.security.set_company_access(self.admin, user.id, [])
            return content
        with patch.object(report_export, "_render", side_effect=revoke), self.assertRaises(AuthorizationError):
            self.export("item_ledger", actor=actor, product_id=self.product)
        self.assertFalse((self.directory / "report.html").exists())
        self.assertEqual(list(self.directory.glob(".bifma-report-*")), [])

    def test_complete_snapshot_blocks_concurrent_writer_until_labels_and_rows_are_read(self):
        self.document("ORIGINAL", "2026-10-01", "1")
        started, completed = Event(), Event()
        executor = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(executor.shutdown)
        query, render = NativeReports._query_trial_balance, report_export._render
        pending = []
        def writer():
            started.set()
            with native_transaction(self.path) as connection:
                connection.execute("UPDATE companies SET name='Changed company' WHERE id=?", (self.company,))
            self.document("AFTER SNAPSHOT", "2026-10-01", "99")
            completed.set()
        def read(connection, company, **options):
            self.assertTrue(connection.in_transaction)
            pending.append(executor.submit(writer))
            self.assertTrue(started.wait(2))
            self.assertFalse(completed.is_set())
            return query(connection, company, **options)
        def after_snapshot(snapshot):
            pending[0].result(timeout=10)
            return render(snapshot)
        with patch.object(NativeReports, "_query_trial_balance", side_effect=read), patch.object(report_export, "_render", side_effect=after_snapshot):
            content = self.export("trial_balance")
        self.assertIn("Company A", content)
        self.assertNotIn("Changed company", content)
        self.assertIn(">1</bdi>", content)
        self.assertNotIn(">100</bdi>", content)

    def test_row_limit_fails_explicitly_and_never_publishes_truncated_output(self):
        self.assertEqual(MAX_EXPORT_ROWS, 100_000)
        native_reports._row_limit(100_000, MAX_EXPORT_ROWS)
        with self.assertRaisesRegex(ValueError, "100,000 rows"):
            native_reports._row_limit(100_001, MAX_EXPORT_ROWS)
        self.many_journal_lines(3)
        with patch.object(native_reports, "MAX_EXPORT_ROWS", 2), self.assertRaisesRegex(ValueError, "exceeds 2 rows"):
            self.export("account_ledger", account_id=self.debit)
        self.assertFalse((self.directory / "report.html").exists())

    def test_row_cap_applies_to_account_groups_stock_groups_and_item_movements(self):
        self.document("MONEY", "2026-10-01", "1")
        other_item = self.stock_item("ANOTHER")
        self.stock_document("STOCK", "2026-10-01", [{"quantity": "1"}, {"quantity": "1"}, {"quantity": "1", "product_id": other_item}])
        for report, filters in (("trial_balance", {}), ("stock_quantities", {}), ("item_ledger", {"product_id": self.product})):
            with self.subTest(report=report), patch.object(native_reports, "MAX_EXPORT_ROWS", 1), self.assertRaisesRegex(ValueError, "exceeds 1 rows"):
                self.export(report, **filters)
        self.assertFalse((self.directory / "report.html").exists())

    def test_invalid_filters_missing_ids_and_unclassified_stock_fail(self):
        invalid = [("unknown", {}), ("account_ledger", {}), ("account_ledger", {"account_id": []}),
                   ("item_ledger", {}), ("trial_balance", {"limit": 100}), ("stock_quantities", {"offset": 0}),
                   ("account_ledger", {"account_id": self.debit, "pool": "regular"}),
                   ("stock_quantities", {"pool": []}), ("stock_quantities", {"search": 0}),
                   ("item_ledger", {"product_id": self.product, "store_id": []}),
                   ("trial_balance", {"date_to": "2026-02-30"}),
                   ("trial_balance", {"date_from": "2026-10-03", "date_to": "2026-10-01"}),
                   ("item_ledger", {"product_id": str(uuid4())}), ("account_ledger", {"account_id": str(uuid4())})]
        for report, filters in invalid:
            with self.subTest(report=report, filters=filters), self.assertRaises(ValueError):
                self.export(report, **filters)
        self.stock_document("UNKNOWN", "2026-09-30", [{"quantity": "2", "pool": None}])
        for report, filters in (("stock_quantities", {}), ("item_ledger", {"product_id": self.product})):
            with self.subTest(report=report), self.assertRaisesRegex(ValueError, "no verified stock pool"):
                self.export(report, date_from="2026-10-01", **filters)
        self.assertFalse((self.directory / "report.html").exists())

    def test_empty_report_is_explicit_and_missing_old_metadata_uses_current_labels(self):
        empty = self.export("trial_balance")
        self.assertIn("No rows in the selected period", empty)
        self.stock_document("OLD", "2026-10-01", [{"quantity": "1"}])
        content = self.export("item_ledger", filename="old.html", product_id=self.product)
        self.assertIn("Item", content)
        self.assertIn("Older entries may show their current names", content)

    def test_new_private_file_never_overwrites_file_or_database(self):
        self.export("trial_balance")
        destination = self.directory / "report.html"
        original = destination.read_bytes()
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        for existing in (destination, self.path):
            with self.assertRaises(FileExistsError):
                self.exporter.export_html(self.admin, self.company, "trial_balance", existing)
        self.assertEqual(destination.read_bytes(), original)

    def test_existing_and_dangling_symlinks_are_never_overwritten(self):
        self.export("trial_balance")
        destination = self.directory / "report.html"
        original = destination.read_bytes()
        alias = self.directory / "link.html"
        broken = self.directory / "broken.html"
        missing = self.directory / "missing"
        try:
            alias.symlink_to(destination)
            broken.symlink_to(missing)
        except OSError:
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(FileExistsError):
            self.exporter.export_html(self.admin, self.company, "trial_balance", alias)
        with self.assertRaises(FileExistsError):
            self.exporter.export_html(self.admin, self.company, "trial_balance", broken)
        self.assertEqual(destination.read_bytes(), original)
        self.assertFalse(missing.exists())

    def test_failure_before_publication_removes_staging_file(self):
        with patch.object(report_export, "publish_new_file", side_effect=OSError("Synthetic failure")), self.assertRaises(OSError):
            self.export("trial_balance")
        self.assertFalse((self.directory / "report.html").exists())
        self.assertEqual(list(self.directory.glob(".bifma-report-*")), [])

    def test_failure_after_publication_removes_only_owned_file(self):
        def fail(staged, destination):
            publish_new_file(staged, destination)
            raise OSError("Synthetic directory sync failure")
        with patch.object(report_export, "publish_new_file", side_effect=fail), self.assertRaises(OSError):
            self.export("trial_balance")
        self.assertFalse((self.directory / "report.html").exists())
        self.assertEqual(list(self.directory.glob(".bifma-report-*")), [])

    def test_competing_destination_is_preserved_during_failure_cleanup(self):
        def compete(staged, destination):
            destination.write_text("Another writer's file", encoding="utf-8")
            publish_new_file(staged, destination)
        with patch.object(report_export, "publish_new_file", side_effect=compete), self.assertRaises(FileExistsError):
            self.export("trial_balance")
        self.assertEqual((self.directory / "report.html").read_text(), "Another writer's file")
        self.assertEqual(list(self.directory.glob(".bifma-report-*")), [])

    def test_report_exports_do_not_mutate_business_records(self):
        self.document("ORIGINAL", "2026-10-01", "1", quantity="2")
        tables = ("companies", "accounts", "products", "stores", "documents", "document_lines", "document_metadata", "journal_entries", "journal_lines", "stock_movements", "audit_events")
        def business_state():
            with native_session(self.path) as connection:
                return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")] for table in tables}
        before = business_state()
        for report, filters in (("trial_balance", {}), ("account_ledger", {"account_id": self.debit}), ("stock_quantities", {}), ("item_ledger", {"product_id": self.product})):
            self.export(report, filename=report + ".html", **filters)
        self.assertEqual(business_state(), before)


if __name__ == "__main__":
    unittest.main()
