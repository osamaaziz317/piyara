"""Keyboard-only public UI paths on disposable companies and a private display."""

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
from tests.test_business_keyboard import _type
from tests.test_entry_navigation import _EntryApp
from tests.test_preview_lifecycle import ROOT, _check
from tools.smoke_native_ui import widgets


def _press(test, expected, sequence="<Return>"):
    test.app.update()
    _check(test.app.focus_get() is expected,
           f"Expected keyboard focus on {expected}, got {test.app.focus_get()}")
    expected.event_generate(sequence)
    test.drain()


def _tab_to(test, target, *, backwards=False):
    """Reach a visible control through native traversal, without assigning focus."""
    for _ in range(80):
        current = test.app.focus_get()
        if current is target:
            return
        _check(current is not None, "Keyboard traversal lost focus")
        current.event_generate("<Shift-Tab>" if backwards else "<Tab>")
        test.drain()
    raise AssertionError(f"Cannot reach {target} with keyboard traversal")


def _find(test, selector, title, query, *, reopen=False):
    _press(test, selector, "<F4>" if reopen else "<Return>")
    dialog = next(widget for widget in widgets(test.panel)
                  if isinstance(widget, tk.Toplevel) and widget.title() == "Find · " + title)
    entry = test.entry(dialog, "Code or name")
    _check(test.app.focus_get() is entry, "Find did not activate its editable input")
    _type(entry, query)
    _press(test, entry, "<KP_Enter>")
    _check(not dialog.winfo_exists(), "Keypad Enter did not accept the Find result")


def _master_case(path):
    test = _EntryApp(path)
    panel = test.panel
    try:
        test.login()
        test.app.focus_force()  # Simulate only the window manager's initial activation.
        panel.master_entry("cities")
        dialog = test.window("New Cities")
        code, name, urdu = (test.entry(dialog, title) for title in ("Code", "Name", "Urdu name"))
        _type(code, "KBD-CITY")
        _press(test, code)
        _press(test, name)
        with patch("bifma.native_ui.messagebox.showerror") as error:
            _press(test, urdu)
        _check(error.call_count == 1 and dialog.winfo_exists() and code.get() == "KBD-CITY",
               "Invalid final-field save closed the dialog or discarded the entered code")
        _tab_to(test, name, backwards=True)
        _type(name, "Keyboard city")
        _press(test, name)
        _press(test, urdu, "<KP_Enter>")
        _check(not dialog.winfo_exists(), "Completed master did not close on final keypad Enter")
        fresh = test.window("New Cities")
        fresh_code = test.entry(fresh, "Code")
        _check(test.app.focus_get() is fresh_code, "Fresh entry did not restore Code focus")
        _type(fresh_code, "UNSAVED")
        _press(test, fresh_code, "<F8>")
        browser = test.window("Saved records · Cities")
        query = next(widget for widget in widgets(browser) if isinstance(widget, ttk.Entry))
        _check(test.app.focus_get() is query, "F8 browser did not focus its search input")
        _type(query, "KBD-CITY")
        _press(test, query)
        table = test.tree(browser)
        _check(test.app.focus_get() is table and len(table.selection()) == 1,
               "Search Enter did not leave the first result ready for keyboard opening")
        _press(test, table, "<KP_Enter>")
        edit = test.window("Edit Cities")
        _press(test, test.entry(edit, "Code"))
        _type(test.entry(edit, "Name"), "Keyboard city edited")
        _press(test, test.entry(edit, "Name"))
        _press(test, test.entry(edit, "Urdu name"))
        _check(not edit.winfo_exists(), "Edited master did not save and close on final Enter")
        _press(test, test.app.focus_get(), "<Escape>")
        _check(not browser.winfo_exists() and fresh.winfo_exists()
               and fresh_code.get() == "UNSAVED" and test.app.focus_get() is fresh_code,
               "Escape from Saved records lost the original entry or restored the wrong focus: "
               f"browser={browser.winfo_exists()}, entry={fresh.winfo_exists()}, "
               f"code={fresh_code.get()!r}, focus={test.app.focus_get()}, expected={fresh_code}")
        saved = panel.masters.list_records(panel.actor, "cities", search="KBD-CITY")
        _check(len(saved) == 1 and saved[0]["name"] == "Keyboard city edited",
               "Keyboard save/edit did not preserve exactly one correct record")
        _press(test, fresh_code, "<Escape>")
        _check(not fresh.winfo_exists(), "Escape could not close the master entry")
        _check(all(window.winfo_exists() for window, bindings in panel._keyboard_windows),
               "Closing entry/browser/edit dialogs retained destroyed keyboard windows")
    finally:
        test.close()


def _form_case(path):
    from bifma.native_ui import Form
    test = _EntryApp(path)
    panel = test.panel
    try:
        test.login()
        test.app.focus_force()
        dialog, body = panel.dialog("Keyboard controls", width=740, height=620)
        records = [{"id": "alpha", "code": "A", "name": "Alpha"},
                   {"id": "beta", "code": "B", "name": "Beta"}]
        form = Form(body, [("generated", "Generated", "text"), ("name", "Name", "text"),
                           ("disabled", "Disabled", "text"), ("password", "Password", "password"),
                           ("number", "Number", "number"), ("mode", "Mode", ("First", "Second")),
                           ("choice", "Account", "accounts"), ("active", "Active", "bool")],
                    values={"generated": "AUTO", "choice": "alpha"}, choices={"accounts": records})
        form.frame.pack(fill="x")
        form.widgets["generated"].state(["readonly"])
        form.widgets["disabled"].state(["disabled"])
        calls = []
        action = ttk.Button(body, text="Accept", command=lambda: calls.append(form.values()))
        action.pack()
        form.set_enter_target(action)
        dialog._initial_focus = form.first_widget()
        test.drain()
        _check(test.app.focus_get() is form.widgets["name"], "Default Form focus did not skip the generated code")
        _type(form.widgets["name"], "Keyboard name")
        _press(test, form.widgets["name"], "<KP_Enter>")
        _check(test.app.focus_get() is form.widgets["password"], "Enter did not skip the disabled field")
        _type(form.widgets["password"], "private")
        _press(test, form.widgets["password"])
        _type(form.widgets["number"], "3.25")
        _press(test, form.widgets["number"])
        _press(test, form.widgets["mode"])
        _press(test, form.widgets["choice"])
        _check(test.app.focus_get() is form.widgets["active"]
               and not any(widget.title().startswith("Find · ") for widget in test.windows()),
               "Enter on an already selected Choice unexpectedly reopened Find")
        _press(test, form.widgets["active"], "<Shift-Tab>")
        _press(test, form.widgets["choice"], "<F4>")
        missing = next(widget for widget in widgets(panel)
                       if isinstance(widget, tk.Toplevel) and widget.title() == "Find · Account")
        query = test.entry(missing, "Code or name")
        _type(query, "no such account")
        _press(test, query, "<KP_Enter>")
        _check(missing.winfo_exists() and query.get() == "no such account"
               and form.inputs["choice"].get() == "alpha" and not calls,
               "Enter with no Find result changed the selected ID or submitted its parent")
        _press(test, query, "<Escape>")
        _check(not missing.winfo_exists() and test.app.focus_get() is form.widgets["choice"],
               "Find Escape did not restore the selected field")
        _find(test, form.widgets["choice"], "Account", "beta", reopen=True)
        _check(form.inputs["choice"].get() == "beta" and test.app.focus_get() is form.widgets["active"],
               "Find did not retain the selected ID or advance after keypad Enter")
        _press(test, form.widgets["active"], "<space>")
        _check(not form.values()["active"] and not calls, "Space did not toggle independently of form submission")
        _press(test, form.widgets["active"], "<KP_Enter>")
        _check(len(calls) == 1 and calls[0]["number"] == "3.25" and not calls[0]["active"],
               "Final keypad Enter did not invoke exactly once with current field values")
        _tab_to(test, action)
        _press(test, action)
        _check(len(calls) == 2, "Focused button did not invoke exactly once on Enter")
        action.state(["disabled"])
        _press(test, action)
        _check(len(calls) == 2, "Enter invoked a disabled action")
        action.state(["!disabled"])
        _press(test, action, "<Escape>")
        _check(not dialog.winfo_exists(), "Dialog Escape did not follow its close action")

        # A selector is also a possible final field: its selection notification
        # must not submit once before and again after Find restores the parent.
        dialog, body = panel.dialog("Final selector", width=700, height=430)
        final = Form(body, [("account", "Account", "accounts")], choices={"accounts": records})
        final.frame.pack(fill="x")
        accepted = []
        def save():
            accepted.append(final.values()["account"])
            dialog.destroy()
        save_button = ttk.Button(body, text="Save selected account", command=save)
        save_button.pack()
        final.set_enter_target(save_button)
        dialog._initial_focus = final.first_widget()
        test.drain()
        choice = final.widgets["account"]
        choice.current(1)
        choice.event_generate("<<ComboboxSelected>>")
        test.drain()
        _check(not accepted and test.app.focus_get() is save_button,
               "Ordinary selector notification implicitly submitted the dialog")
        _press(test, save_button, "<Shift-Tab>")
        _find(test, choice, "Account", "beta", reopen=True)
        _check(not dialog.winfo_exists() and accepted == ["beta"],
               "Final Find keyboard selection did not save and close exactly once")
    finally:
        test.close()


def _report_case(path):
    test = _EntryApp(path)
    panel = test.panel
    try:
        test.login()
        test.app.focus_force()
        panel.report_screen("Trial balance")
        test.drain()
        report = test.entry(panel.main, "Report")
        _check(test.app.focus_get() is report, "Report did not initially focus its selector")
        _press(test, report)
        _press(test, test.entry(panel.main, "From (YYYY-MM-DD)"))
        _press(test, test.entry(panel.main, "To (YYYY-MM-DD)"), "<KP_Enter>")
        _check(test.tree(panel.main).get_children(), "Final report date Enter did not Run report")

        panel.report_screen("Stock quantities")
        test.drain()
        for title in ("Report", "From (YYYY-MM-DD)", "To (YYYY-MM-DD)", "Stock pool"):
            _press(test, test.entry(panel.main, title))
        filters = test.window("Stock quantity filters")
        store = test.entry(filters, "Store (blank for all)")
        _find(test, store, "Store (blank for all)", "main")
        search = test.entry(filters, "Item code or name")
        _type(search, "synthetic")
        _press(test, search, "<KP_Enter>")
        _check(not filters.winfo_exists() and test.tree(panel.main).get_children(),
               "Completed stock filter did not close and show its report on final Enter")

        # A failed asynchronous read must leave its completed filter available for correction/retry.
        panel.report_screen("Stock quantities")
        test.drain()
        _press(test, test.entry(panel.main, "Report"))
        start = test.entry(panel.main, "From (YYYY-MM-DD)")
        _type(start, "not-a-date")
        _press(test, start)
        _press(test, test.entry(panel.main, "To (YYYY-MM-DD)"))
        _press(test, test.entry(panel.main, "Stock pool"))
        filters = test.window("Stock quantity filters")
        _tab_to(test, test.entry(filters, "Item code or name"))
        search = test.entry(filters, "Item code or name")
        _type(search, "KEEP QUERY")
        with patch("bifma.native_ui.messagebox.showerror") as error:
            _press(test, search)
        _check(error.call_count == 1 and filters.winfo_exists() and search.get() == "KEEP QUERY",
               "Failed report Enter discarded its filter instead of allowing correction")
        _press(test, test.app.focus_get(), "<Escape>")
        _check(not filters.winfo_exists(), "Report filter could not be cancelled with Escape")
    finally:
        test.close()


def _access_audit_case(path):
    test = _EntryApp(path)
    panel = test.panel
    try:
        test.login()
        test.app.focus_force()
        staff = panel.security.create_user(panel.actor, "keyboard_staff", "8", display_name="Keyboard staff")
        panel.user_screen()
        test.drain()
        table = test.tree(panel.main)
        _check(table.item(table.selection()[0], "values")[0] == "keyboard_staff",
               "Users did not initially select the first available row")
        _press(test, table, "<KP_Enter>")
        access = test.window("Access · Keyboard staff")
        checks = [widget for widget in widgets(access) if isinstance(widget, ttk.Checkbutton)]
        companies = [widget for widget in checks if widget.cget("text") not in {"Read", "Create", "Update", "Post"}]
        _check(companies and test.app.focus_get() is companies[0], "Company access did not focus the first checkbox")
        _press(test, companies[0], "<space>")
        for control in companies:
            if not control.instate(["disabled"]):
                _press(test, control)
        module = next(widget for widget in widgets(access) if isinstance(widget, ttk.Combobox))
        _check(access.winfo_exists() and test.app.focus_get() is module,
               "Final company checkbox Enter did not save that section and focus Module")
        _press(test, module)
        read = next(widget for widget in checks if widget.cget("text") == "Read")
        _press(test, read, "<space>")
        for title in ("Read", "Create", "Update", "Post"):
            _press(test, next(widget for widget in checks if widget.cget("text") == title), "<KP_Enter>")
        _check(not access.winfo_exists(), "Final permission Enter did not save and close Access")
        granted = panel.security.get_access(panel.actor, staff.id)
        _check(len(granted["company_ids"]) == 1 and granted["permissions"]["sales"] == ["read"],
               "Enter navigation changed access flags instead of accepting the Space selections")

        panel.audit_screen()
        test.drain()
        company = test.entry(panel.main, "Company (blank for all)")
        _check(test.app.focus_get() is company, "Audit initial load stole focus from its filters")
        # Leave the optional company filter empty through ordinary Tab traversal.
        _tab_to(test, test.entry(panel.main, "Action"))
        for title in ("Action", "From date (UTC)", "To date (UTC)"):
            _press(test, test.entry(panel.main, title))
        search = test.entry(panel.main, "Search actions, users or record IDs")
        _type(search, "security.user_created")
        _press(test, search, "<KP_Enter>")
        table = test.tree(panel.main)
        _check(test.app.focus_get() is table and table.selection(),
               "Audit filter Enter did not prepare a result for keyboard opening")
        _press(test, table)
        detail = test.window("Audit event")
        _check(any(isinstance(widget, tk.Text) and widget.cget("state") == "disabled"
                   for widget in widgets(detail)), "Audit detail did not remain read-only")
        _press(test, test.app.focus_get(), "<Escape>")
        _check(not detail.winfo_exists() and test.app.focus_get() is table,
               "Audit detail Escape did not restore the selected event")
        with native_session(test.path) as connection:
            _check(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0,
                   "Keyboard administration created a business document")
    finally:
        test.close()


def _menu_case(path):
    test = _EntryApp(path)
    panel = test.panel
    try:
        test.login()
        test.app.focus_force()
        test.app.event_generate("<Alt-KeyPress-c>")
        test.drain()
        focus_path = str(test.app.tk.call("focus"))
        _check(focus_path and str(test.app.tk.call("winfo", "class", focus_path)) == "Menu",
               "Alt+C did not open Coding for keyboard traversal")
        # A preceding real-pointer test can leave the pointer above a menu
        # entry. Navigate by the visible highlight rather than assuming Tk
        # initially highlights the first item beneath that resting pointer.
        for _ in range(5):
            focus_path = str(test.app.tk.call("focus"))
            active = str(test.app.tk.call(focus_path, "index", "active"))
            if active != "none" and str(test.app.tk.call(focus_path, "entrycget", active, "-label")) == "Cities":
                break
            test.app.tk.call("event", "generate", focus_path, "<Down>")
            test.drain()
        else:
            raise AssertionError("Arrow keys could not highlight Coding → Cities")
        test.app.tk.call("event", "generate", focus_path, "<Return>")
        test.drain()
        dialog = test.window("New Cities")
        code = test.entry(dialog, "Code")
        _check(test.app.focus_get() is code, "Menu selection did not activate its new entry")
        _press(test, code, "<Alt-KeyPress-r>")
        _check(dialog.winfo_exists() and test.app.focus_get() is code,
               "A menu hotkey escaped the active entry modal")
        _press(test, code, "<Escape>")
        test.app.event_generate("<Control-l>")
        test.drain()
        _check(test.app.focus_get() is panel._company_choice,
               "Ctrl+L did not reach the company selector from the keyboard")
        _press(test, panel._company_choice, "<F10>")
        focus_path = str(test.app.tk.call("focus"))
        _check(focus_path and str(test.app.tk.call("winfo", "class", focus_path)) == "Menu",
               "F10 did not activate the main menu")
        test.app.tk.call("event", "generate", focus_path, "<Escape>")
        test.drain()

        regular_dialog = panel.dialog
        def compact_dialog(title, **options):
            return regular_dialog(title, **{**options, "height": 440})
        with patch.object(panel, "dialog", side_effect=compact_dialog):
            panel.master_entry("companies")
            dialog = test.window("New Companies")
        fax = test.entry(dialog, "Fax")
        _tab_to(test, fax)
        canvas = next(widget for widget in widgets(dialog) if isinstance(widget, tk.Canvas))
        top, bottom = canvas.winfo_rooty(), canvas.winfo_rooty() + canvas.winfo_height()
        _check(top <= fax.winfo_rooty() and fax.winfo_rooty() + fax.winfo_height() <= bottom,
               "Keyboard focus did not scroll a lower company field into view")
        _press(test, fax, "<Escape>")
        _check(not dialog.winfo_exists(), "Escape did not close the scrolled company form")

        panel.voucher_printing()
        test.drain()
        section = next(widget for widget in widgets(panel.main) if isinstance(widget, ttk.Combobox))
        _press(test, section)
        browser = test.window("Saved records · Sale Voucher")
        search = next(widget for widget in widgets(browser) if isinstance(widget, ttk.Entry))
        _type(search, "260001")
        _press(test, search)
        _press(test, test.tree(browser))
        original = next(window for window in test.windows() if window is not browser)
        notebook = next(widget for widget in widgets(original) if isinstance(widget, ttk.Notebook))
        _tab_to(test, notebook)
        _check(notebook.index(notebook.select()) == 0, "Original voucher initially hid its details")
        _press(test, notebook, "<Control-Tab>")
        _check(notebook.index(notebook.select()) == 1, "Ctrl+Tab did not expose original line items")
        _press(test, test.app.focus_get(), "<Escape>")
        _check(not original.winfo_exists() and browser.winfo_exists(),
               "Original detail Escape closed its parent browser")
        _press(test, test.app.focus_get(), "<Escape>")
        _check(not browser.winfo_exists() and test.app.focus_get() is section,
               "Saved-voucher Escape did not return to Voucher Printing")
    finally:
        test.close()


CASES = {"masters": _master_case, "form": _form_case, "reports": _report_case,
         "access_audit": _access_audit_case, "menu": _menu_case}


@unittest.skipIf(os.name == "posix" and not os.environ.get("DISPLAY"),
                 "Keyboard UI checks require a graphical display")
class KeyboardUITests(unittest.TestCase):
    def _run(self, case):
        with tempfile.TemporaryDirectory(prefix="bifma-keyboard-ui-") as folder:
            path = Path(folder) / "synthetic-history.sqlite3"
            synthetic_archive(path)
            before = hashlib.sha256(path.read_bytes()).digest()
            result = subprocess.run([sys.executable, "-B", "-m", "tests.test_keyboard_ui", case, str(path)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(json.loads(result.stdout), {"case": case, "passed": True})
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_final_master_enter_validates_saves_edits_and_restores_f8_entry(self):
        self._run("masters")

    def test_shared_fields_choices_buttons_keypad_enter_and_reverse_traversal(self):
        self._run("form")

    def test_report_enter_runs_and_closes_filters_only_after_success(self):
        self._run("reports")

    def test_access_space_selection_final_enter_and_audit_detail_escape(self):
        self._run("access_audit")

    def test_top_menu_shortcuts_modal_guard_and_scrolled_field_keyboard_reachability(self):
        self._run("menu")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] in CASES:
        CASES[sys.argv[1]](Path(sys.argv[2]))
        print(json.dumps({"case": sys.argv[1], "passed": True}))
    else:
        unittest.main()
