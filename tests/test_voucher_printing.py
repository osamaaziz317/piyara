"""Voucher Printing routes original and posted records through live authorization."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from bifma.database import session
from bifma.native_schema import native_session
from tests.test_document_register import all_original_modules
from tests.test_entry_navigation import _EntryApp
from tests.test_preview_lifecycle import ROOT, _check
from tools.smoke_native_ui import button, widgets


def _workflow():
    with tempfile.TemporaryDirectory(prefix="bifma-printing-ui-") as folder:
        source = Path(folder) / "source.sqlite3"
        all_original_modules(source)
        with session(source, readonly=False) as connection:
            connection.execute("UPDATE SMST SET BILLNO='000012' WHERE TRNID='1'")
        test = _EntryApp(source)
        target = Path(folder) / "printed-original.html"
        notices = []
        try:
            test.login()
            with native_session(test.path) as connection:
                test.panel.company_id = connection.execute("SELECT id FROM companies WHERE code='1'").fetchone()[0]
            test.panel.refresh_shell()
            test.drain()
            test.section("Reports")
            with patch.object(test.panel.register, "list_documents", wraps=test.panel.register.list_documents) as reads:
                test.menu("Voucher Printing")
                _check(reads.call_count == 0 and not test.windows(), "Printing loaded documents before choosing Find/F8")
                section = next(widget for widget in widgets(test.panel.main) if isinstance(widget, ttk.Combobox))
                _check(section.get() == "Sale Voucher", "Printing did not use an available document section")
                test.f8(section)
                browser = test.window("Saved records · Sale Voucher")
                row_index = next(index for index, row in enumerate(test.panel.document_rows)
                                 if row["source"] == "original" and row["id"] == "1")
                test.panel.document_tree.selection_set(str(row_index))
                button(browser, "Open selected")
                test.drain()
                document = next(window for window in test.windows() if window is not browser)
                with patch("bifma.native_ui.filedialog.asksaveasfilename", return_value=str(target)), \
                     patch("bifma.native_ui.messagebox.showinfo", side_effect=lambda *args, **kwargs: notices.append(args)):
                    button(document, "Save printable copy")
                    test.drain()
                content = target.read_text()
                _check("000012" in content and "Original recorded document" in content,
                       "Original print lost its reference or provenance")
                _check("Synthetic item" in content and "Recorded party amount" in content,
                       "Original print lost recorded line/header amounts")
                _check(bool(notices) and document.winfo_exists() and browser.winfo_exists(),
                       "Saving the print lost the current document/browser")
                button(document, "Close")
                button(browser, "Back to entry")
                test.drain()
                _check(not test.windows(), "Printing browser did not return to its section chooser")
            # The same print entry respects the signed-in user's available sections.
            owner = test.panel.actor
            staff = test.panel.security.create_user(owner, "cashreader", "Violet morning rivers sparkle 92")
            test.panel.security.set_company_access(owner, staff.id, [test.panel.company_id])
            test.panel.security.set_permissions(owner, staff.id, "receipts", ["read"])
            test.panel.logout()
            test.drain()
            test.login("cashreader", "Violet morning rivers sparkle 92")
            test.section("Reports")
            test.menu("Voucher Printing")
            section = next(widget for widget in widgets(test.panel.main) if isinstance(widget, ttk.Combobox))
            _check(tuple(section.cget("values")) == ("Cash Receipt Voucher",),
                   "Printing exposed unavailable document sections")
            _check(not test.errors, repr(test.errors))
        finally:
            test.close()


@unittest.skipIf(os.name == "posix" and not os.environ.get("DISPLAY"), "Voucher printing UI needs a graphical display")
class VoucherPrintingTests(unittest.TestCase):
    def test_original_voucher_export_from_the_permission_filtered_printing_menu(self):
        result = subprocess.run([sys.executable, "-B", "-m", "tests.test_voucher_printing", "workflow"],
                                cwd=ROOT, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {"passed": True})


if __name__ == "__main__":
    if sys.argv[1:] == ["workflow"]:
        _workflow()
        print(json.dumps({"passed": True}))
    else:
        unittest.main()
