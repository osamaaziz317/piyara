"""Cash-voucher entry checks using synthetic choices and no business database."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import ttk
import unittest
from decimal import Decimal, localcontext
from unittest.mock import patch

from bifma.cash_voucher_ui import CashVoucherForm
from bifma.native_ui import Choice
from tools.smoke_native_ui import widgets


ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS = [
    {"id": "cash", "code": "0001", "name": "Cash in hand", "kind": "cash"},
    {"id": "party-a", "code": "0301001", "name": "Shared account title", "kind": "supplier"},
    {"id": "party-b", "code": "0301002", "name": "Shared account title", "kind": "customer"},
]


def _check(condition, message):
    if not condition:
        raise AssertionError(message)


def _form(root, kind="cash_payment", **options):
    saved, printed, navigation = [], [], []
    defaults = {"document_number": "528", "business_date": "2026-10-03",
                "account_id": "cash", "notes": "Unfinished voucher",
                "rounding_policy": "legacy_whole", "posting_places": 0}
    defaults.update(options.pop("values", {}))
    form = CashVoucherForm(
        root, kind=kind, accounts=ACCOUNTS,
        company={"code": "1", "name": "Fixture Company", "cash_account_id": "cash"},
        values=defaults, last_number="527", choice_factory=Choice,
        on_save=saved.append, on_print=printed.append,
        on_new=lambda: navigation.append("new"), on_exit=lambda: navigation.append("exit"),
        on_records=lambda: navigation.append("records"), **options)
    form.pack(fill="both", expand=True)
    return form, saved, printed, navigation


def _entry_case():
    root = tk.Tk()
    root.geometry("1000x650")
    errors = []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    try:
        form, saved, printed, navigation = _form(root)
        root.update()
        _check(form.payload()["lines"] == [], "A fresh voucher contains old or fabricated entries")
        first, second = form.rows[:2]
        first.widgets["code"].focus_force()
        for digit in "0301001":
            first.widgets["code"].event_generate("<KeyPress>", keysym=digit)
        _check(first.inputs["account_id"].get() == "party-a",
               "Typed account code did not resolve its exact stable identifier")
        first.inputs["narration"].set("Payment narration")
        with localcontext() as context:
            context.prec = 2
            first.inputs["amount"].set("9007199254740993.01")
            second.inputs["code"].set("0301002")
            second.inputs["amount"].set("0.02")
        _check(form.total_value == Decimal("9007199254740993.03"),
               "Live total lost precision or used the caller's Decimal context")
        _check(form.total.get() == "9,007,199,254,740,993.03", "Voucher total formatting is incorrect")
        form.buttons["Save"].invoke()
        payload = saved[-1]
        _check(payload["lines"] == [
            {"account_id": "party-a", "amount": "9007199254740993.01", "narration": "Payment narration"},
            {"account_id": "party-b", "amount": "0.02", "narration": ""}],
            "Save did not preserve account identity, entered text or skip untouched rows")
        _check(payload["account_id"] == "cash" and payload["posting_places"] == 0
               and payload["notes"] == "Unfinished voucher", "Voucher header semantics changed")
        _check(form.last_number.get() == "527", "Last voucher metadata changed")
        form.buttons["Print"].invoke()
        _check(printed == [payload], "Print did not retain the same entered voucher")

        first.inputs["code"].set("0301")
        _check(first.inputs["account_id"].get() is None,
               "Partially replacing a code retained the previous account")
        try:
            form.payload()
        except ValueError as error:
            _check("Line 1" in str(error), "Unresolved account error lacks its line")
        else:
            raise AssertionError("An unresolved account code was silently saved")
        first.inputs["code"].set("0301001")
        account = first.inputs["account_id"]
        account.widget.current(account.ids.index("party-b"))
        account.widget.event_generate("<<ComboboxSelected>>")
        _check(first.inputs["code"].get() == "0301002" and first.value()["account_id"] == "party-b",
               "Duplicate title selection did not update its precise account code")
        second.inputs["amount"].set("NaN")
        _check(form.total_value is None and form.total.get() == "Check amount",
               "Invalid input displayed a plausible numeric total")
        second.inputs["amount"].set("0.02")
        form.remove_line(second)
        _check(form.total_value == Decimal("9007199254740993.01"), "Removing a line did not update the total")
        final = form.rows[-1]
        final.widgets["amount"].focus_force()
        final.widgets["amount"].event_generate("<Return>")
        root.update()
        _check(len(form.rows) == 5 and root.focus_get() is form.rows[-1].widgets["code"],
               "Enter on the last line did not add and focus a new inline row")
        for key in ("Save", "Print", "New", "Exit"):
            button = form.buttons[key]
            _check(button.winfo_rooty() >= root.winfo_rooty()
                   and button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty() + root.winfo_height(),
                   f"{key} is outside the supported window size")
        form.buttons["New"].invoke()
        form.buttons["Exit"].invoke()
        _check(navigation == ["new", "exit"], "New/Exit navigation callbacks were not used")
        _check(not errors, repr(errors))
    finally:
        root.destroy()


def _receipt_cleanup_case():
    root = tk.Tk()
    errors = []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    try:
        form, saved, _, _ = _form(root, "cash_receipt", values={
            "document_number": "Existing draft", "posting_places": 2,
            "rounding_policy": "cents", "lines": [
                {"account_id": "party-b", "amount": "17.20", "narration": "Saved narration"}]})
        root.update()
        _check(form.rows[0].inputs["code"].get() == "0301002", "Draft account code was not restored")
        form.rows[0].inputs["amount"].set("17.25")
        form.buttons["Save"].invoke()
        _check(saved[-1]["lines"][0]["amount"] == "17.25"
               and saved[-1]["posting_places"] == 2 and saved[-1]["rounding_policy"] == "cents",
               "Editing a receipt changed its established posting precision")
        captured = list(form._bifma_variables)
        for row in form.rows:
            captured.extend(row.frame._bifma_variables)
        names = [str(variable) for variable in captured]
        form.destroy()
        _check(all(variable._tk is None and variable._root is None for variable in captured),
               "Voucher shutdown retained a variable's Tcl interpreter")
        _check(all(not root.tk.getboolean(root.tk.call("info", "exists", name)) for name in names),
               "Voucher shutdown retained a Tcl variable")
        _check(not errors, repr(errors))
    finally:
        root.destroy()


def _keyboard_case():
    root = tk.Tk()
    root.geometry("1000x650")
    errors = []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    def enter(expected):
        _check(root.focus_get() is expected, "Cash keyboard flow lost its actual focused control")
        expected.event_generate("<Return>")
        root.update()
    try:
        form, saved, _, _ = _form(root, values={"posting_places": 2})
        root.update()
        root.focus_force()
        form.focus_first()
        root.update()
        enter(form.widgets["document_number"])
        enter(form.widgets["business_date"])
        first = form.rows[0]
        popup = first.inputs["account_id"]._search_window
        query = first.inputs["account_id"]._search_entry
        _check(root.focus_get() is query, "Date Enter did not open focused Account Find")
        for letter in "shared":
            query.event_generate("<KeyPress>", keysym=letter)
        root.update()
        tree = next(widget for widget in widgets(popup) if isinstance(widget, ttk.Treeview))
        _check(len(tree.get_children()) == 2 and query.get() == "shared",
               "Typed account title did not filter both duplicate-name accounts")
        query.event_generate("<Down>")
        enter(query)
        _check(not popup.winfo_exists() and first.inputs["account_id"].get() == "party-b"
               and first.inputs["code"].get() == "0301002",
               "Account Enter lost the highlighted stable ID or leading zeros")
        _check(root.focus_get() is first.widgets["narration"], "Find close stole Narration focus")
        first.inputs["narration"].set("Receipt by keyboard")
        enter(first.widgets["narration"])
        first.inputs["amount"].set("12.35")
        enter(first.widgets["amount"])
        second = form.rows[1]
        _check(root.focus_get() is second.widgets["code"], "Amount Enter did not start the next row")
        for digit in "0301001":
            second.widgets["code"].event_generate("<KeyPress>", keysym=digit)
        enter(second.widgets["code"])
        _check(second.inputs["account_id"]._search_window is None,
               "An exact account code unnecessarily reopened Find")
        enter(second.widgets["narration"])
        second.inputs["amount"].set("1.70")
        form.buttons["Save"].invoke()
        _check(saved[-1]["lines"] == [
            {"account_id": "party-b", "amount": "12.35", "narration": "Receipt by keyboard"},
            {"account_id": "party-a", "amount": "1.70", "narration": ""}],
            "Keyboard entry changed its exact cash payload")
        _check(form.total_value == Decimal("14.05") and saved[-1]["posting_places"] == 2,
               "Keyboard navigation changed totals or cash precision")
        _check(not errors, repr(errors))
    finally:
        root.destroy()


def _finish_case():
    root = tk.Tk()
    errors = []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    try:
        form, saved, _, _ = _form(root, blank_rows=2)
        root.update()
        first, blank = form.rows
        first.inputs["code"].set("0301001")
        first.inputs["amount"].set("18.25")
        first.widgets["account_id"].focus_force()
        first.widgets["account_id"].event_generate("<KP_Enter>")
        root.update()
        _check(root.focus_get() is first.widgets["narration"], "Selected account Enter reopened Find instead of advancing")
        first.widgets["narration"].event_generate("<KP_Enter>")
        root.update()
        _check(root.focus_get() is first.widgets["amount"], "Keypad Enter did not advance narration")
        first.widgets["amount"].event_generate("<KP_Enter>")
        root.update()
        _check(root.focus_get() is blank.widgets["code"], "Amount Enter did not reach the blank finishing row")
        blank.widgets["code"].event_generate("<Return>")
        root.update()
        _check(len(saved) == 1 and len(saved[0]["lines"]) == 1 and saved[0]["lines"][0]["amount"] == "18.25",
               "Blank-code Enter did not save exactly the populated voucher")
        form.widgets["notes"].focus_set()
        form.widgets["notes"].event_generate("<Control-s>")
        root.update()
        _check(len(saved) == 2, "Ctrl+S from the header did not save the same voucher")
        first.inputs["amount"].set("invalid")
        first.widgets["amount"].focus_set()
        with patch("bifma.cash_voucher_ui.messagebox.showerror") as message:
            first.widgets["amount"].event_generate("<Return>")
            root.update()
        _check(message.call_count == 1 and root.focus_get() is first.widgets["amount"] and len(saved) == 2,
               "Invalid amount advanced or saved instead of retaining its field")
        first.widgets["amount"].event_generate("<Control-Delete>")
        root.update()
        _check(form.rows == [blank] and root.focus_get() is blank.widgets["code"],
               "Keyboard row removal lost the neighboring row or keyboard focus")
        _check(not errors, repr(errors))
    finally:
        root.destroy()


@unittest.skipIf(os.name == "posix" and not os.environ.get("DISPLAY"), "Cash voucher checks require a graphical display")
class CashVoucherUITests(unittest.TestCase):
    def _run(self, case):
        result = subprocess.run([sys.executable, "-B", "-m", "tests.test_cash_voucher_ui", case],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {"case": case, "passed": True})

    def test_inline_accounts_exact_totals_payload_and_keyboard_rows(self):
        self._run("entry")

    def test_receipt_draft_settings_and_widget_cleanup(self):
        self._run("receipt_cleanup")

    def test_cash_header_find_narration_amount_and_next_row_use_focused_enter(self):
        self._run("keyboard")

    def test_blank_row_finishes_save_shortcut_keypad_and_validation(self):
        self._run("finish")


if __name__ == "__main__":
    cases = {"entry": _entry_case, "receipt_cleanup": _receipt_cleanup_case, "keyboard": _keyboard_case, "finish": _finish_case}
    if len(sys.argv) == 2 and sys.argv[1] in cases:
        cases[sys.argv[1]]()
        print(json.dumps({"case": sys.argv[1], "passed": True}))
    else:
        unittest.main()
