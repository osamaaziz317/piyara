"""Printable city balances and complete cash books use synthetic data only."""

from decimal import Context, Decimal, localcontext
from html.parser import HTMLParser
import os
import stat
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma import native_reports, report_export
from bifma.native_schema import native_transaction
from bifma.report_export import ReportExporter
from bifma.security import AuthorizationError
from tests import test_native_reports as report_helpers
from tests import test_report_export as export_helpers
from tests.test_cash_reports import CashReportFixture


class ReportTables(HTMLParser):
    """Read rendered table cells so totals are checked in their actual columns."""

    def __init__(self, content):
        super().__init__()
        self.tables, self.table, self.row, self.cell = [], None, None, None
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.table = []
        elif tag == "tr":
            self.row = []
        elif tag in {"td", "th"}:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append("\n")

    def handle_data(self, value):
        if self.cell is not None:
            self.cell.append(value)

    def handle_endtag(self, tag):
        if tag in {"td", "th"}:
            self.row.append("".join(self.cell))
            self.cell = None
        elif tag == "tr":
            self.table.append(self.row)
            self.row = None
        elif tag == "table":
            self.tables.append(self.table)
            self.table = None


class CityCashExportTests(unittest.TestCase):
    # Use fixture helpers only; do not inherit another class's test methods.
    document = report_helpers.NativeReportTests.document
    many_journal_lines = export_helpers.ReportExportTests.many_journal_lines
    reader = export_helpers.ReportExportTests.reader
    export = export_helpers.ReportExportTests.export

    def setUp(self):
        report_helpers.NativeReportTests.setUp(self)
        self.directory = self.path.parent
        self.exporter = ReportExporter(self.path, self.security)
        self.city, self.empty_city, self.zero_account = [str(uuid4()) for _ in range(3)]
        with native_transaction(self.path) as connection:
            connection.executemany("INSERT INTO cities(id,code,name,name_urdu) VALUES (?,?,?,?)", [
                (self.city, "NORTH", "Current North", "شمال"),
                (self.empty_city, "EMPTY", "Empty city", None)])
            connection.execute("UPDATE accounts SET city_id=? WHERE id IN (?,?)", (self.city, self.debit, self.credit))
            connection.execute("UPDATE accounts SET kind='cash' WHERE id=?", (self.debit,))
            connection.execute("INSERT INTO accounts(id,code,name) VALUES (?,'ZERO','Zero activity account')", (self.zero_account,))

    def zero_activity(self):
        debit, credit = self.debit, self.credit
        try:
            self.debit = self.credit = self.zero_account
            self.document("ZERO-CLOSING", "2026-10-01", "1")
        finally:
            self.debit, self.credit = debit, credit

    def test_city_details_groups_and_grand_totals_keep_both_closing_sides_exact(self):
        self.document("OPEN", "2026-09-30", "9007199254740993.01")
        self.document("PERIOD", "2026-10-01", "0.3")
        self.document("FUTURE", "2026-10-03", "99")
        self.document("DRAFT", "2026-10-01", "88", posted=False)
        self.document("OTHER", "2026-10-01", "77", company=self.other)
        self.zero_activity()
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET is_active=0,is_postable=0 WHERE id=?", (self.credit,))
        with localcontext(Context(prec=3)):
            content = self.export("city_balances", date_from="2026-10-01", date_to="2026-10-02")
        detail, groups, totals = ReportTables(content).tables
        self.assertEqual((len(detail) - 1, len(groups) - 1), (3, 2))
        debit = next(row for row in detail[1:] if "DEBIT" in row[0])
        credit = next(row for row in detail[1:] if "CREDIT" in row[0])
        self.assertEqual(debit[-6:], ["9007199254740993.01", "0.3", "0", "9007199254740993.31", "9007199254740993.31", "0"])
        self.assertEqual(credit[-3:], ["-9007199254740993.31", "0", "9007199254740993.31"])
        self.assertIn("Inactive · Not postable", credit[0])
        north = next(row for row in groups[1:] if "NORTH" in row[0])
        self.assertEqual(north[-3:], ["0", "9007199254740993.31", "9007199254740993.31"])
        unassigned = next(row for row in groups[1:] if row[0] == "Unassigned")
        self.assertEqual(unassigned[1:], ["1", "0", "1", "1", "0", "0", "0"])
        self.assertEqual(dict(totals), {"Accounts": "3", "Net opening Dr / −Cr": "0", "Period debits": "1.3",
                                     "Period credits": "1.3", "Net closing Dr / −Cr": "0",
                                     "Closing debit balances": "9007199254740993.31", "Closing credit balances": "9007199254740993.31"})
        self.assertIn("Account balances by city", content)
        self.assertIn("Current city summary", content)
        self.assertIn("Zero closing balances: <bdi dir=\"auto\">Retained", content)
        self.assertNotIn("No inventory valuation", content)

    def test_city_filters_are_explicit_literal_and_bilingual_with_safe_markup(self):
        self.document("ONE", "2026-10-01", "12.34")
        attack = '10% <script>alert("x")</script> & text'
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET name=?,name_urdu='کھاتہ' WHERE id=?", (attack, self.debit))
            connection.execute("UPDATE cities SET name=? WHERE id=?", (attack, self.city))
        content = self.export("city_balances", city_id=self.city, account_kind="cash", search="%", include_zero=False,
                              date_from="2026-10-01", date_to="2026-10-02")
        detail, groups, totals = ReportTables(content).tables
        self.assertEqual((len(detail) - 1, len(groups) - 1), (1, 1))
        self.assertIn(attack, detail[1][0])
        self.assertEqual(detail[1][2], "cash")
        self.assertEqual(dict(totals)["Closing debit balances"], "12.34")
        for phrase in ("City filter (current assignment)", "Account kind (current master)", "Account search (literal)",
                       "Zero closing balances: <bdi dir=\"auto\">Excluded", "2026-10-01", "2026-10-02", "شمال", "کھاتہ"):
            self.assertIn(phrase, content)
        parsed = export_helpers.ParsedReport(content)
        for tag, attrs in parsed.elements:
            self.assertNotIn(tag, {"script", "img", "iframe", "object", "embed", "link", "form", "svg", "a"})
            self.assertFalse(any(key.startswith("on") or key in {"src", "href"} for key in attrs))
        self.assertIn("Content-Security-Policy", content)
        self.assertIn("&lt;script&gt;", content)
        self.assertIn('lang="ur"', content)

    def test_unassigned_and_zero_exclusion_and_empty_selected_city_are_distinct(self):
        self.zero_activity()
        retained = self.export("city_balances", city_id="unassigned")
        self.assertIn("Zero activity account", retained)
        self.assertEqual(dict(ReportTables(retained).tables[-1])["Accounts"], "1")
        excluded = self.export("city_balances", filename="excluded.html", city_id="unassigned", include_zero=False)
        self.assertNotIn("Zero activity account", excluded)
        self.assertIn("No rows in the selected period", excluded)
        empty = self.export("city_balances", filename="empty.html", city_id=self.empty_city)
        self.assertIn("Empty city", empty)
        self.assertNotIn("All cities, including unassigned", empty)
        self.assertEqual(dict(ReportTables(empty).tables[-1])["Accounts"], "0")

    def test_current_master_labels_are_captured_once_and_later_exports_follow_reassignment(self):
        self.document("HISTORY", "2026-10-01", "7")
        render = report_export._render

        def change_after_snapshot(snapshot):
            with native_transaction(self.path) as connection:
                connection.execute("UPDATE accounts SET city_id=?,kind='supplier',name='Updated account' WHERE id=?", (self.empty_city, self.debit))
                connection.execute("UPDATE cities SET name='Renamed North' WHERE id=?", (self.city,))
            return render(snapshot)

        with patch.object(report_export, "_render", side_effect=change_after_snapshot):
            before = self.export("city_balances", account_kind="cash")
        row = ReportTables(before).tables[0][1]
        self.assertIn("Debit account", row[0])
        self.assertIn("Current North", row[1])
        self.assertEqual(row[2], "cash")
        after = self.export("city_balances", filename="after.html", city_id=self.empty_city, account_kind="supplier")
        row = ReportTables(after).tables[0][1]
        self.assertIn("Updated account", row[0])
        self.assertIn("Empty city", row[1])
        self.assertEqual((row[2], row[-3]), ("supplier", "7"))
        self.assertIn("A later master change can move the same historical balance", after)

    def test_cash_book_exports_beyond_one_page_with_exact_opening_period_and_closing_values(self):
        self.document("OPEN", "2026-09-30", "9007199254740993.01")
        self.many_journal_lines()
        with localcontext(Context(prec=3)):
            content = self.export("cash_book", account_id=self.debit, date_from="2026-10-01", date_to="2026-10-01")
        payments, receipts, totals = ReportTables(content).tables
        self.assertEqual(payments[1:], [["No rows in the selected period."]])
        # Every receipt retains its exact amount, voucher/type and narration in
        # posting order. The photographed cash grid has no running-balance
        # column; opening and closing remain explicit full-period totals.
        self.assertEqual(receipts[1:], [["0.1", "MANY\nJV", f"Row {index}"] for index in range(1, 502)])
        self.assertEqual(content.count('<tr class="report-row">'), 501)
        self.assertEqual(dict(totals), {"Opening Dr / −Cr": "9007199254740993.01", "Period cash in / debits": "50.1",
                                      "Period cash out / credits": "0", "Closing Dr / −Cr": "9007199254741043.11"})
        self.assertIn("Cash Book", content)
        self.assertIn("Current account master", content)
        self.assertIn("all posted journal entries for this account", content)
        self.assertIn("Period cash in / debits", content)
        self.assertIn("Cash payments", content)
        self.assertIn("Cash receipts", content)
        self.assertNotIn("No inventory valuation", content)

    def test_caps_fail_before_either_report_publishes_partial_output(self):
        self.many_journal_lines(3)
        for report, filters in (("city_balances", {}), ("cash_book", {"account_id": self.debit})):
            with self.subTest(report=report), patch.object(native_reports, "MAX_EXPORT_ROWS", 1), self.assertRaisesRegex(ValueError, "exceeds 1 rows"):
                self.export(report, **filters)
        self.assertFalse((self.directory / "report.html").exists())
        self.assertEqual(list(self.directory.glob(".bifma-report-*")), [])

    def test_permissions_remain_report_specific_and_company_scoped(self):
        user, actor = self.reader("ledger")
        self.export("cash_book", actor=actor, account_id=self.debit)
        with self.assertRaises(AuthorizationError):
            self.export("city_balances", actor=actor, filename="denied.html")
        with self.assertRaises(AuthorizationError):
            self.export("cash_book", actor=actor, company=self.other, account_id=self.debit, filename="denied.html")
        self.security.set_permissions(self.admin, user.id, "ledger", [])
        self.security.set_permissions(self.admin, user.id, "trial_balance", ["read"])
        self.export("city_balances", actor=actor, filename="city.html")
        with self.assertRaises(AuthorizationError):
            self.export("cash_book", actor=actor, account_id=self.debit, filename="denied.html")
        self.assertFalse((self.directory / "denied.html").exists())

    def test_both_reports_recheck_module_and_company_access_after_render(self):
        user, actor = self.reader("ledger")
        self.security.set_permissions(self.admin, user.id, "trial_balance", ["read"])
        render = report_export._render
        for report, module, filters in (("city_balances", "trial_balance", {}), ("cash_book", "ledger", {"account_id": self.debit})):
            for revoke_company in (False, True):
                with self.subTest(report=report, revoke_company=revoke_company):
                    self.security.set_company_access(self.admin, user.id, [self.company])
                    self.security.set_permissions(self.admin, user.id, module, ["read"])

                    def revoke(snapshot):
                        content = render(snapshot)
                        if revoke_company:
                            self.security.set_company_access(self.admin, user.id, [])
                        else:
                            self.security.set_permissions(self.admin, user.id, module, [])
                        return content

                    with patch.object(report_export, "_render", side_effect=revoke), self.assertRaises(AuthorizationError):
                        self.export(report, actor=actor, **filters)
                    self.assertFalse((self.directory / "report.html").exists())
                    self.assertEqual(list(self.directory.glob(".bifma-report-*")), [])

    def test_new_reports_keep_private_publication_and_never_overwrite(self):
        self.document("SYNTHETIC", "2026-10-01", "1")
        for report, filters in (("city_balances", {}), ("cash_book", {"account_id": self.debit})):
            filename = report + ".html"
            content = self.export(report, filename=filename, **filters)
            destination = self.directory / filename
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                self.export(report, filename=filename, **filters)
            self.assertEqual(destination.read_text(encoding="utf-8"), content)
        self.assertEqual(list(self.directory.glob(".bifma-report-*")), [])


class CashBookPostedExportTests(CashReportFixture, unittest.TestCase):
    def export_book(self, filename="cash.html", **filters):
        destination = self.path.parent / filename
        ReportExporter(self.path, self.security).export_html(self.actor, self.company, "cash_book", destination,
                                                            account_id=self.accounts["cash"], **filters)
        return destination.read_text(encoding="utf-8")

    def test_cash_book_includes_standard_cash_account_trades_journal_receipt_and_reversal(self):
        self.opening(quantity="10")
        values = self.common("2026-01-01")
        values["lines"] = [{"account_id": self.accounts["cash"], "debit": "100", "credit": "0"},
                           {"account_id": self.accounts["equity"], "debit": "0", "credit": "100"}]
        self.post(self.save("journal", values))
        ordinary = self.invoice("sale", account_id=self.accounts["cash"], rate="7")
        purchase = self.invoice("cash_purchase", rate="3")
        values = self.common("2026-01-03")
        values.update(account_id=self.accounts["cash"], lines=[{"account_id": self.accounts["customer"], "amount": "2"}])
        receipt = self.post(self.save("cash_receipt", values))
        reversal = self.reverse(purchase, "2026-01-04")
        content = self.export_book(date_from="2026-01-02", date_to="2026-01-04")
        payments, receipts, totals = ReportTables(content).tables
        payment_facts = [(row[0], row[1].splitlines()) for row in payments[1:]]
        receipt_facts = [(row[0], row[1].splitlines()) for row in receipts[1:]]
        self.assertEqual(payment_facts, [("3", [purchase["document_number"], "cash_purchase", "02-01-2026"])])
        self.assertEqual(receipt_facts, [
            ("7", [ordinary["document_number"], "SL", "02-01-2026"]),
            ("2", [receipt["document_number"], "CRV", "03-01-2026"]),
            ("3", [reversal["document_number"], "reversal", "04-01-2026"])])
        self.assertEqual(content.count('<tr class="report-row">'), 4)
        self.assertEqual(dict(totals), {"Opening Dr / −Cr": "100", "Period cash in / debits": "12", "Period cash out / credits": "3", "Closing Dr / −Cr": "109"})

    def test_former_cash_account_uses_verified_saved_role_and_current_labels(self):
        self.invoice("cash_purchase", rate="3")
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET cash_account_id=NULL WHERE id=?", (self.company,))
            connection.execute("UPDATE accounts SET kind='general',is_active=0,is_postable=0,name='Former counter',name_urdu='نقد' WHERE id=?", (self.accounts["cash"],))
        content = self.export_book()
        self.assertIn("Saved cash classification in a posted document", content)
        self.assertIn("Former counter", content)
        self.assertIn("نقد", content)
        self.assertEqual(dict(ReportTables(content).tables[-1])["Closing Dr / −Cr"], "-3")


if __name__ == "__main__":
    unittest.main()
