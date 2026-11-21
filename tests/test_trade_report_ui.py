"""Trade report defaults include originals while explicit date input persists."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.test_baseline_import import synthetic_archive
from tests.test_entry_navigation import _EntryApp
from tests.test_preview_lifecycle import ROOT, _check
from tools.smoke_native_ui import button, field


def report_case(source):
    test = _EntryApp(source)
    panel = test.panel
    try:
        test.login()
        test.section("Reports")
        test.menu("Sales / returns detail")
        start = test.entry(panel.main, "From (YYYY-MM-DD)")
        end = test.entry(panel.main, "To (YYYY-MM-DD)")
        _check((start.get(), end.get()) == ("", ""), "Trade defaults hid the original reporting period")
        button(panel.main, "Run report")
        test.drain()
        activity = test.window("Trade activity")
        button(activity, "Choose filters")
        test.drain()
        filters = test.window("Trade detail filters")
        button(filters, "Show trade detail")
        test.drain()
        tree = test.tree(panel.main)
        records = [dict(zip(tree["columns"], tree.item(item, "values"))) for item in tree.get_children()]
        _check(len(records) == 1, "Normal Run report did not include the original invoice")
        _check((records[0]["document_number"], records[0]["record_source"]) == ("260001", "Original"),
               "The original invoice lost its number or provenance")

        field(panel.main, "Report", "Trial balance")
        _check(start.get() == "2026-01-04", "Financial report lost the automatic cutoff opening boundary")
        field(panel.main, "Report", "Purchases / returns detail")
        _check(start.get() == "", "Switching an untouched report retained the financial cutoff default")
        field(panel.main, "From (YYYY-MM-DD)", "2026-01-02")
        field(panel.main, "To (YYYY-MM-DD)", "2026-01-03")
        for report in ("Item ledger", "Sales / returns detail", "Trial balance"):
            field(panel.main, "Report", report)
            _check((start.get(), end.get()) == ("2026-01-02", "2026-01-03"), "Switching report overwrote explicit dates")
        field(panel.main, "From (YYYY-MM-DD)", "")
        field(panel.main, "Report", "Account ledger")
        _check((start.get(), end.get()) == ("", "2026-01-03"), "Switching report overwrote a deliberately cleared date")

        panel.report_screen("Item ledger")
        start = test.entry(panel.main, "From (YYYY-MM-DD)")
        _check(start.get() == "2026-01-04", "A fresh item ledger did not retain its cutoff default")
        field(panel.main, "Report", "Sales / returns detail")
        _check(start.get() == "", "A fresh report form retained the previous form's edited-date state")
    finally:
        test.close()


class TradeReportUITests(unittest.TestCase):
    def test_original_default_run_and_manual_dates_survive_report_changes(self):
        with tempfile.TemporaryDirectory(prefix="bifma-trade-report-ui-") as directory:
            source = Path(directory) / "original.sqlite3"
            synthetic_archive(source)
            before = hashlib.sha256(source.read_bytes()).digest()
            result = subprocess.run([sys.executable, "-B", "-m", "tests.test_trade_report_ui", "workflow", str(source)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(json.loads(result.stdout), {"passed": True})
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "workflow":
        report_case(Path(sys.argv[2]))
        print(json.dumps({"passed": True}))
    else:
        unittest.main()
