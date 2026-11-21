"""Focused business-entry keyboard checks on disposable synthetic companies."""

import hashlib
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

from bifma.native_schema import native_session
from tests.test_baseline_import import synthetic_archive
from tests.test_entry_navigation import _EntryApp
from tests.test_preview_lifecycle import ROOT, _check
from tools.smoke_native_ui import button, field, widgets


def _control(parent, title):
    return next(widget for widget in widgets(parent)
                if isinstance(widget, ttk.Button) and widget.cget("text") == title)


def _enter(test, expected):
    test.app.update()
    _check(test.app.focus_get() is expected,
           f"Expected actual keyboard focus on {expected}, got {test.app.focus_get()}")
    expected.event_generate("<Return>")
    test.drain()


def _type(control, text):
    control.delete(0, "end")
    control.insert(0, text)


def _find(test, selector, title, query):
    _enter(test, selector)
    popup = next(widget for widget in widgets(test.panel)
                 if isinstance(widget, tk.Toplevel) and widget.title() == "Find · " + title)
    entry = test.entry(popup, "Code or name")
    _check(test.app.focus_get() is entry, "Find did not focus its editable query")
    for letter in query:
        entry.event_generate("<KeyPress>", keysym="space" if letter == " " else letter)
    test.drain()
    _check(entry.get() == query, "Typed query did not reach Find")
    _enter(test, entry)
    _check(not popup.winfo_exists(), "Enter did not accept the highlighted Find result")


def _master_case(path):
    test = _EntryApp(path)
    panel = test.panel
    try:
        test.login()
        test.app.focus_force()  # Supply only the initial WM activation on Xvfb.
        panel.master_entry("cities")
        dialog = test.window("New Cities")
        code, name, urdu = (test.entry(dialog, key) for key in ("Code", "Name", "Urdu name"))
        _check(test.app.focus_get() is code, "New City did not initially focus Code")
        _type(code, "KEYBOARD")
        _enter(test, code)
        _type(name, "Keyboard city")
        _enter(test, name)
        _enter(test, urdu)
        _check(not dialog.winfo_exists(), "Last city field Enter did not save and close its completed form")
        fresh = test.window("New Cities")
        _check(fresh is not dialog and test.app.focus_get() is test.entry(fresh, "Code")
               and not test.entry(fresh, "Code").get(), "Saved City did not return to focused blank entry")
        _check(panel.masters.list_records(panel.actor, "cities", search="KEYBOARD")[0]["name"] == "Keyboard city",
               "Final-field Enter did not retain entered master values")
        fresh.destroy()
        test.drain()

        panel.master_entry("products")
        item = test.window("New Items")
        code = test.entry(item, "Item code")
        description = test.entry(item, "Item description")
        _check(code.instate(["readonly"]) and test.app.focus_get() is description,
               "Item entry did not skip its generated readonly code")
        _type(description, "Keyboard item")
        _enter(test, description)
        _enter(test, test.entry(item, "Urdu description"))
        _find(test, test.entry(item, "Group"), "Group", "sub")
        _find(test, test.entry(item, "Base unit"), "Base unit", "piece")
        # Optional large unit can stay empty by Tab, preserving native keyboard behavior.
        large = test.entry(item, "Large unit")
        _check(test.app.focus_get() is large, "Base-unit Find did not advance to Large unit")
        large.event_generate("<Tab>")
        test.drain()
        factor = test.entry(item, "Base units per large unit")
        _check(test.app.focus_get() is factor, "Tab could not leave an optional unselected unit")
        for title in ("Base units per large unit", "Sale rate per base unit", "Purchase rate per base unit"):
            _enter(test, test.entry(item, title))
        active = next(widget for widget in widgets(item) if isinstance(widget, ttk.Checkbutton))
        before = active.instate(["selected"])
        _enter(test, active)
        saved = panel.masters.list_records(panel.actor, "products", search="Keyboard item")
        _check(not item.winfo_exists() and len(saved) == 1 and bool(saved[0]["is_active"]) == before,
               "Final Enter failed to save once or changed the Active checkbox")
        test.window("New Items").destroy()
        test.drain()

        panel.master_entry("accounts")
        account = test.window("New Accounts")
        _check(test.app.focus_get() is test.entry(account, "Code"), "Account entry did not focus Code")
        account.destroy()
        test.drain()
    finally:
        test.close()


def _generic_case(path):
    test = _EntryApp(path)
    panel = test.panel
    try:
        test.login()
        test.app.focus_force()
        # Journal and opening-balance use the same header/line editor.
        for kind, title in (("opening_balance", "Opening Balance"), ("journal", "Journal Voucher")):
            panel.transaction_entry(kind)
            dialog = test.window("New · " + title)
            for name in ("Document number", "Date (YYYY-MM-DD)", "Subtotal rounding", "Final decimal places", "Notes"):
                _enter(test, test.entry(dialog, name))
            _check(not test.tree(dialog).get_children(), "Header traversal created a line before line entry")
            line = test.window("Add line")
            _find(test, test.entry(line, "Account"), "Account", "party")
            debit = test.entry(line, "Debit")
            _type(debit, "5")
            _enter(test, debit)
            _enter(test, test.entry(line, "Credit"))
            narration = test.entry(line, "Narration")
            _type(narration, "Keyboard journal")
            _enter(test, narration)
            _check(not line.winfo_exists(), "Final narration Enter did not accept and close the completed line")
            tree = test.tree(dialog)
            _check(len(tree.get_children()) == 1 and "5" in tree.item("0", "values"),
                   "Final-field Enter lost entered journal debit")
            tree.selection_set("0")
            tree.focus_set()
            _enter(test, tree)
            edit = test.window("Edit line")
            _check(test.entry(edit, "Debit").get() == "5"
                   and test.entry(edit, "Narration").get() == "Keyboard journal",
                   "Table Enter did not reopen the same line without changing it")
            edit.destroy()
            dialog.destroy()
            test.drain()

        panel.transaction_entry("purchase")
        dialog = test.window("New · Purchases")
        button(dialog, "Add line")
        line = test.window("Add line")
        _find(test, test.entry(line, "Item"), "Item", "synthetic")
        _find(test, test.entry(line, "Store"), "Store", "main")
        quantity = test.entry(line, "Quantity in base units")
        _check(quantity.get() == "1", "New inventory line lost its quantity-one default")
        _type(quantity, "2")
        _enter(test, quantity)
        rate = test.entry(line, "Rate per base unit")
        _check(rate.get() == "", "Unknown original purchase rate was fabricated")
        _type(rate, "4.25")
        _enter(test, rate)
        _enter(test, test.entry(line, "Line discount %"))
        _check(not line.winfo_exists(), "Final inventory field did not close the accepted line")
        tree = test.tree(dialog)
        _check("2" in tree.item("0", "values") and "4.25" in tree.item("0", "values"),
               "Inventory keyboard entry changed the entered quantity or exact rate")
        dialog.destroy()
        test.drain()
        with native_session(test.path) as connection:
            _check(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0,
                   "Navigating unsaved journal/purchase fields created financial documents")
    finally:
        test.close()


def _return_case(path):
    test = _EntryApp(path)
    panel = test.panel
    try:
        test.login()
        test.app.focus_force()
        product = panel.masters.list_records(panel.actor, "products")[0]
        changed = panel.masters.update(panel.actor, "products", product["id"], {"is_active": False}, product["version"])
        panel.transaction_entry("sale_return")
        dialog = test.window("New · Sales returns")
        button(dialog, "Add line")
        line = test.window("Add line")
        _enter(test, _control(line, "Choose original line"))
        picker = test.window("Choose the original document")
        query = next(widget for widget in widgets(picker) if isinstance(widget, ttk.Entry))
        _type(query, "260001")
        _enter(test, query)
        tree = test.tree(picker)
        _enter(test, tree)
        tree = test.tree(picker)
        shown = tree.item("0", "values")
        with patch("bifma.native_ui.messagebox.showerror") as error:
            _enter(test, tree)
        _check(error.call_count == 1 and error.call_args.args[0] == "Original item unavailable",
               "The unavailable original item was not rejected clearly")
        _check(picker.winfo_exists() and query.get() == "260001" and tree.selection() == ("0",)
               and tree.item("0", "values") == shown,
               "Rejected original line closed the chooser or lost its query/selection")
        picker.destroy()
        line.destroy()
        dialog.destroy()
        test.drain()
        panel.masters.update(panel.actor, "products", product["id"], {"is_active": True}, changed["version"])
        panel.transaction_entry("sale_return")
        dialog = test.window("New · Sales returns")
        button(dialog, "Add line")
        line = test.window("Add line")
        _enter(test, _control(line, "Choose original line"))
        picker = test.window("Choose the original document")
        query = next(widget for widget in widgets(picker) if isinstance(widget, ttk.Entry))
        _enter(test, query)
        _enter(test, test.tree(picker))
        _enter(test, test.tree(picker))
        quantity = test.entry(line, "Quantity in base units")
        _check(not picker.winfo_exists() and test.app.focus_get() is quantity,
               "Accepted return source did not close Find and focus quantity")
        _check(test.entry(line, "Rate per base unit").get() == "10", "Original line price changed during keyboard selection")
        with native_session(test.path) as connection:
            _check(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0,
                   "Browsing/rejecting a historical invoice created a return")
    finally:
        test.close()


CASES = {"masters": _master_case, "generic": _generic_case, "returns": _return_case}


@unittest.skipIf(os.name == "posix" and not os.environ.get("DISPLAY"),
                 "Business keyboard checks require a graphical display")
class BusinessKeyboardTests(unittest.TestCase):
    def _run(self, case):
        with tempfile.TemporaryDirectory(prefix="bifma-business-keyboard-") as folder:
            path = Path(folder) / "synthetic-history.sqlite3"
            synthetic_archive(path)
            before = hashlib.sha256(path.read_bytes()).digest()
            result = subprocess.run([sys.executable, "-B", "-m", "tests.test_business_keyboard", case, str(path)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(json.loads(result.stdout), {"case": case, "passed": True})
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_city_item_and_account_focus_traversal_and_final_field_save(self):
        self._run("masters")

    def test_journal_opening_balance_and_inventory_line_keyboard_flow(self):
        self._run("generic")

    def test_historical_return_enter_selection_retains_rejected_original(self):
        self._run("returns")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] in CASES:
        CASES[sys.argv[1]](Path(sys.argv[2]))
        print(json.dumps({"case": sys.argv[1], "passed": True}))
    else:
        unittest.main()
