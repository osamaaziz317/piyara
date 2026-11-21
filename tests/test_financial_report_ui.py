"""Dedicated report filters and previews on a disposable baseline only."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma.report_ui import date_display, report_date
from bifma.native_schema import native_transaction
from tests.test_baseline_import import synthetic_archive
from tests.test_entry_navigation import _EntryApp
from tests.test_preview_lifecycle import ROOT, _check
from tools.smoke_native_ui import button


def workflow(source):
    test = _EntryApp(source)
    panel = test.panel
    try:
        test.login()
        other_company = str(uuid4())
        with native_transaction(panel.path) as connection:
            connection.execute("INSERT INTO companies(id,code,name,fiscal_start,fiscal_end) VALUES(?,?,?,?,?)",
                               (other_company, "REPORT-OTHER", "Other report company", "2026-01-01", "2026-12-31"))
        panel.report_screen("Account ledger")
        test.drain()
        filters = test.window("Party Ledger Report")
        form = filters.report_filter
        _check(form.inputs["date_from"].get() == "04-01-26", "Ledger lost the cutoff opening default")
        form.inputs["date_from"].set("")
        form.inputs["date_to"].set("20-11-26")
        form.inputs["account_code"].set("0002")
        _check(form.account_choice.get() is not None, "Exact party code did not resolve its stable ID")
        company = form.widgets["company_id"]
        company.focus_force()
        test.app.update()
        company.event_generate("<Return>")
        test.drain()
        preview = test.window("Party Ledger Report · Preview")
        _check(test.tree(preview).get_children(), "Ledger preview omitted the explicit opening snapshot")
        _check(filters.winfo_exists() and form.inputs["account_code"].get() == "0002", "View replaced the retained filter")
        button(preview, "Close")
        test.drain()
        _check(filters.winfo_exists() and test.app.grab_current() is filters, "Closing preview failed to restore the filter grab")
        _check(form.inputs["date_from"].get() == "" and form.inputs["account_code"].get() == "0002", "Closing preview lost entered filters")
        errors = []
        form.inputs["date_to"].set("31-02-26")
        with patch("tkinter.messagebox.showerror", side_effect=lambda *args, **kwargs: errors.append(args)):
            form.view_button.invoke()
            test.drain()
        _check(errors and filters.winfo_exists() and form.inputs["date_to"].get() == "31-02-26", "Bad date destroyed the filter or its input")
        form.inputs["date_to"].set("20-11-26")
        form.widgets["company_id"].current(form.company_ids.index(other_company))
        form.widgets["company_id"].event_generate("<<ComboboxSelected>>")
        test.drain()
        _check(form.account_choice.get() is None and form.inputs["account_code"].get() == "", "Company switch retained a stale selected account")
        form.inputs["account_code"].set("0002")
        calls = []
        with patch.object(panel, "preview_report", side_effect=lambda *args, **kwargs: calls.append((args, kwargs))):
            form.view_button.invoke()
            test.drain()
            preview = test.window("Party Ledger Report · Preview")
            button(preview, "Print / page preview")
        _check(calls[0][1]["company_id"] == other_company and panel.company_id != other_company,
               "Printing used the main company's ID instead of the report selection")
        preview.destroy()
        filters.destroy()
        test.drain()

        panel.report_screen("Cash book")
        test.drain()
        filters = test.window("Cash Book")
        form = filters.report_filter
        _check(form.default_cash_id is not None and form.cash_choice is None, "Configured cash account was not selected automatically")
        form.inputs["date_to"].set("04-01-26")
        calls = []
        with patch.object(panel, "preview_report", side_effect=lambda *args, **kwargs: calls.append((args, kwargs))):
            button(filters, "View")
            test.drain()
            preview = test.window("Cash Book · Preview")
            button(preview, "Print / page preview")
        _check(calls[0][0][0] == "cash_book" and calls[0][1]["company_id"] == panel.company_id,
               "Printing lost the captured report company")
        _check(calls[0][0][1]["date_from"] == "2026-01-04" == calls[0][0][1]["date_to"], "Cash Book did not use one daily date")
        preview.destroy()
        filters.destroy()
        test.drain()

        panel.report_screen("Sub-trial balance")
        test.drain()
        filters = test.window("Sub Trial Balance")
        filters.report_filter.inputs["date_to"].set("2026-11-20")
        button(filters, "View")
        test.drain()
        preview = test.window("Sub Trial Balance · Preview")
        _check(test.tree(preview).get_children(), "Sub-trial preview has no baseline accounts")
        _check(filters.winfo_exists(), "Sub-trial View discarded its filter")
        preview.destroy()
        filters.destroy()
        test.drain()
    finally:
        test.close()


class ReportDateTests(unittest.TestCase):
    def test_original_and_iso_dates_normalize_without_invalid_calendar_dates(self):
        self.assertEqual(report_date("20-11-26"), "2026-11-20")
        self.assertEqual(report_date("20-11-2026"), "2026-11-20")
        self.assertEqual(report_date("2026-11-20"), "2026-11-20")
        self.assertEqual(date_display("2026-11-20"), "20-11-26")
        self.assertIsNone(report_date(" "))
        for value in ("31-02-26", "invalid", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                report_date(value, required=True)


@unittest.skipIf(os.name == "posix" and not os.environ.get("DISPLAY"), "Requires a display")
class FinancialReportUITests(unittest.TestCase):
    def test_keyboard_retained_filters_daily_cash_and_captured_print_company(self):
        with tempfile.TemporaryDirectory(prefix="bifma-financial-ui-") as directory:
            source = Path(directory) / "original.sqlite3"
            synthetic_archive(source)
            result = subprocess.run([sys.executable, "-B", "-m", "tests.test_financial_report_ui", "workflow", str(source)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(json.loads(result.stdout), {"passed": True})


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "workflow":
        workflow(Path(sys.argv[2]))
        print(json.dumps({"passed": True}))
    else:
        unittest.main()
