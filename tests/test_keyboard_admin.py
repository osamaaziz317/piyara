"""Administrator dialogs use real focused Enter events on private databases."""

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
from tests.test_entry_navigation import _EntryApp, PASSWORD
from tests.test_preview_lifecycle import ROOT, _check
from tools.smoke_native_ui import widgets


def _type(control, value):
    control.delete(0, "end")
    control.insert(0, value)


def _key(test, control, sequence="<Return>"):
    test.app.update()
    _check(test.app.focus_get() is control,
           f"Expected focus on {control}, got {test.app.focus_get()}")
    control.event_generate(sequence)
    test.drain()


def _start(path):
    test = _EntryApp(path)
    test.login()
    test.app.focus_force()
    test.panel.user_screen()
    test.drain()
    return test


def _create_user_case(path):
    test = _start(path)
    panel = test.panel
    try:
        panel.create_user_dialog()
        dialog = test.window("Create user")
        controls = {name: test.entry(dialog, name) for name in
                    ("Username", "Display name", "Passphrase", "Confirm passphrase")}
        values = ("keyboard_staff", "Keyboard Staff", "Cloud purple 83", "Mismatch")
        for (name, control), value in zip(controls.items(), values):
            _type(control, value)
            _key(test, control)
        admin = next(widget for widget in widgets(dialog) if isinstance(widget, ttk.Checkbutton))
        _check(not admin.instate(["selected"]), "New user silently defaulted to administrator")
        with patch("bifma.native_ui.messagebox.showerror") as error:
            _key(test, admin, "<KP_Enter>")
        _check(error.call_count == 1 and dialog.winfo_exists(), "Mismatch closed the unsaved user dialog")
        _check(test.app.focus_get() is controls["Confirm passphrase"], "Mismatch did not focus confirmation")
        _check(len(panel.security.list_users(panel.actor)) == 1, "Invalid confirmation created a user")
        _type(controls["Confirm passphrase"], values[2])
        _key(test, controls["Confirm passphrase"])
        _key(test, admin)
        _check(not dialog.winfo_exists(), "Final user field did not create and close with one Enter")
        user = next(user for user in panel.security.list_users(panel.actor) if user.username == values[0])
        _check(user.display_name == values[1] and not user.is_admin, "Enter changed submitted user values")
        session = panel.security.authenticate(values[0], values[2])
        panel.security.logout(session)
        _check(isinstance(test.app.focus_get(), ttk.Treeview), "Successful create did not return focus to users")
    finally:
        test.close()


def _password_case(path):
    test = _start(path)
    panel = test.panel
    try:
        panel.password_dialog()
        dialog = test.window("Change passphrase")
        old, new, confirm = (test.entry(dialog, title) for title in
                             ("Current passphrase", "New passphrase", "Confirm new passphrase"))
        _type(old, "Mistyped current password")
        _key(test, old)
        changed_password = "Indigo wind 84"
        _type(new, changed_password)
        _key(test, new)
        _type(confirm, "Not matching")
        with patch("bifma.native_ui.messagebox.showerror") as error:
            _key(test, confirm)
        _check(error.call_count == 1 and dialog.winfo_exists() and test.app.focus_get() is confirm,
               "Password mismatch closed the dialog or lost confirmation focus")
        _type(confirm, changed_password)
        with patch("bifma.native_ui.messagebox.showerror") as error:
            _key(test, confirm, "<KP_Enter>")
        _check(error.call_count == 1 and dialog.winfo_exists() and panel.actor is not None,
               "Wrong current password discarded the form or signed out a valid session")
        _key(test, confirm, "<Shift-Tab>")
        _key(test, new, "<Shift-Tab>")
        _type(old, PASSWORD)
        _key(test, old)
        _key(test, new)
        _key(test, confirm)
        _check(not dialog.winfo_exists(), "Final confirmation did not change password and close")
        session = panel.security.authenticate("owner", changed_password)
        panel.security.logout(session)

        staff = panel.security.create_user(panel.actor, "reset_staff", "Orange moon 18", display_name="Reset Staff")
        panel.reset_password_dialog(staff)
        dialog = test.window("Reset passphrase · Reset Staff")
        password = test.entry(dialog, "New passphrase")
        confirm = test.entry(dialog, "Confirm passphrase")
        _type(password, "")
        _key(test, password)
        with patch("bifma.native_ui.messagebox.showerror") as error:
            _key(test, confirm)
        _check(error.call_count == 1 and dialog.winfo_exists(), "Invalid reset closed the dialog")
        _check(test.app.focus_get() is confirm, "Rejected reset lost its submitting control")
        password.focus_set()
        _type(password, "Violet forest 91")
        _key(test, password)
        _type(confirm, "Violet forest 91")
        _key(test, confirm)
        _check(not dialog.winfo_exists(), "Reset confirmation required an extra Save press")
        session = panel.security.authenticate(staff.username, "Violet forest 91")
        panel.security.logout(session)
    finally:
        test.close()


def _role_case(path):
    test = _start(path)
    panel = test.panel
    try:
        owner = panel.actor.user
        panel.admin_role_dialog(owner)
        dialog = test.window("Administrator role · " + owner.display_name)
        check = next(widget for widget in widgets(dialog) if isinstance(widget, ttk.Checkbutton))
        _key(test, check, "<space>")
        _check(not check.instate(["selected"]), "Space did not toggle role")
        with patch("bifma.native_ui.messagebox.showerror") as error:
            _key(test, check)
        _check(error.call_count == 1 and dialog.winfo_exists(), "Last-admin guard closed the role dialog")
        _check(panel.security.list_users(panel.actor)[0].is_admin, "Enter bypassed the last-admin guard")
        _key(test, check, "<space>")
        _key(test, check)
        _check(not dialog.winfo_exists(), "Unchanged accepted role did not close")

        staff = panel.security.create_user(panel.actor, "role_staff", "Yellow branch 16", display_name="Role Staff")
        panel.admin_role_dialog(staff)
        dialog = test.window("Administrator role · Role Staff")
        check = next(widget for widget in widgets(dialog) if isinstance(widget, ttk.Checkbutton))
        _key(test, check, "<space>")
        _key(test, check, "<KP_Enter>")
        _check(not dialog.winfo_exists(), "Final role checkbox did not save and close")
        changed = next(user for user in panel.security.list_users(panel.actor) if user.id == staff.id)
        _check(changed.is_admin, "Keyboard role change was not persisted")
    finally:
        test.close()


def _restore_case(path):
    from bifma.native_backup import NativeBackupService
    test = _start(path)
    panel = test.panel
    try:
        backup = path.parent / "private-backup.sqlite3"
        destination = path.parent / "restored.sqlite3"
        NativeBackupService(test.path, security=panel.security).create_backup(panel.actor, backup)
        with patch("bifma.native_ui.filedialog.askopenfilename", return_value=str(backup)), \
             patch("bifma.native_ui.filedialog.asksaveasfilename", return_value=str(destination)):
            panel.restore_dialog()
        dialog = test.window("Restore native backup")
        user = test.entry(dialog, "Backup administrator username")
        password = test.entry(dialog, "Passphrase")
        _type(user, "owner")
        _key(test, user)
        _type(password, "Wrong backup password")
        with patch("bifma.native_ui.messagebox.showerror") as error:
            _key(test, password)
        _check(error.call_count == 1 and dialog.winfo_exists() and panel.actor is not None
               and not destination.exists(),
               "Rejected backup credentials closed the form, signed out its owner or published a restore")
        _type(password, PASSWORD)
        _key(test, password, "<KP_Enter>")
        _check(not dialog.winfo_exists() and panel.path == destination,
               "Restore last-field Enter did not complete restore and close its dialog")
        _check(panel.actor is None, "Restore bypassed fresh sign-in")
        with native_session(destination) as connection:
            _check(connection.execute("SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL").fetchone()[0] == 0,
                   "Restored file retained active authentication sessions")
    finally:
        test.close()


def _expired_session_case(path):
    from bifma.native_backup import NativeBackupService
    test = _start(path)
    panel = test.panel
    try:
        backup = path.parent / "private-backup.sqlite3"
        destination = path.parent / "not-restored.sqlite3"
        NativeBackupService(test.path, security=panel.security).create_backup(panel.actor, backup)
        with patch("bifma.native_ui.filedialog.askopenfilename", return_value=str(backup)), \
             patch("bifma.native_ui.filedialog.asksaveasfilename", return_value=str(destination)):
            panel.restore_dialog()
        dialog = test.window("Restore native backup")
        user = test.entry(dialog, "Backup administrator username")
        password = test.entry(dialog, "Passphrase")
        _type(user, "owner")
        _key(test, user)
        _type(password, "Wrong backup password")
        panel.security.logout(panel.actor)
        with patch("bifma.native_ui.messagebox.showerror") as error:
            _key(test, password)
        _check(error.call_count == 1 and panel.actor is None and not dialog.winfo_exists()
               and not destination.exists(), "Restore retry preserved an invalid workspace session")
        test.login()
        panel.password_dialog()
        dialog = test.window("Change passphrase")
        for title, value in (("Current passphrase", PASSWORD), ("New passphrase", "Orange valley 16")):
            control = test.entry(dialog, title)
            _type(control, value)
            _key(test, control)
        confirm = test.entry(dialog, "Confirm new passphrase")
        _type(confirm, "Orange valley 16")
        panel.security.logout(panel.actor)
        with patch("bifma.native_ui.messagebox.showerror") as error:
            _key(test, confirm)
        _check(error.call_count == 1 and panel.actor is None and not dialog.winfo_exists(),
               "Password retry bypassed revoked-session sign-in")
        session = panel.security.authenticate("owner", PASSWORD)
        panel.security.logout(session)
    finally:
        test.close()


def _keyboard_lifecycle_case(path):
    from bifma.keyboard import uninstall, unbind_callback
    test = _start(path)
    panel = test.panel
    try:
        dialog, body = panel.dialog("Keyboard tab lifecycle", width=700, height=470)
        notebook = ttk.Notebook(body)
        notebook.pack(fill="both", expand=True)
        for title in ("First", "Second"):
            page = ttk.Frame(notebook)
            notebook.add(page, text=title)
            ttk.Entry(page).pack()
        test.drain()
        variable = f"ttk::notebook::TLNotebooks({dialog})"
        for _ in range(3):
            registered = test.app.tk.splitlist(test.app.tk.call("set", variable))
            _check(registered.count(str(notebook)) == 1, "Remapping duplicated notebook keyboard registration")
            notebook.pack_forget()
            test.drain()
            notebook.pack(fill="both", expand=True)
            test.drain()
        old_path = str(notebook)
        notebook.destroy()
        test.drain()
        _check(old_path not in test.app.tk.splitlist(test.app.tk.call("set", variable)),
               "Destroyed notebook retained an Alt/Ctrl-Tab traversal reference")
        replacement = ttk.Notebook(body)
        replacement.pack(fill="both", expand=True)
        first = ttk.Frame(replacement)
        second = ttk.Frame(replacement)
        replacement.add(first, text="New first")
        replacement.add(second, text="New second")
        entry = ttk.Entry(first)
        entry.pack()
        test.drain()
        entry.focus_set()
        _key(test, entry, "<Control-Tab>")
        _check(replacement.select() == str(second), "Fresh notebook traversal failed after old tab cleanup")
        dialog.destroy()
        test.drain()
        _check(not test.app.tk.getboolean(test.app.tk.call("info", "exists", variable)),
               "Destroyed dialog retained notebook traversal state")
        _check(all(window is not dialog for window, _ in panel._keyboard_windows),
               "Destroyed modal retained owner keyboard callbacks")

        menu_calls = []
        menu_sentinels = [(sequence, test.app.bind(sequence, lambda event: menu_calls.append(event.keysym), add=True))
                          for sequence in ("<F10>", "<Alt-KeyPress-f>")]
        menu_callbacks = list(panel._menu_bindings)
        panel._detach_menu()
        for sequence, identifier in menu_sentinels:
            _check(identifier in test.app.bind(sequence), "Menu cleanup erased an unrelated keyboard handler")
        for sequence, identifier in menu_callbacks:
            _check(identifier not in test.app.bind(sequence)
                   and not test.app.tk.call("info", "commands", identifier),
                   "Menu cleanup retained a dead owner callback")
        test.app.event_generate("<F10>")
        test.drain()
        _check(menu_calls == ["F10"], "Unrelated menu handler stopped working after menu cleanup")
        for sequence, identifier in menu_sentinels:
            unbind_callback(test.app, sequence, identifier)

        sentinel = test.app.bind("<FocusIn>", lambda event: None, add=True)
        owned_bindings = [(window, sequence, identifier) for window, bindings in panel._keyboard_windows
                          for sequence, identifier in bindings]
        f8_before = test.app.bind("<F8>")
        uninstall(panel)
        _check(sentinel in test.app.bind("<FocusIn>") and test.app.bind("<F8>") == f8_before,
               "Keyboard uninstall removed unrelated root/F8 bindings: "
               + repr((sentinel, test.app.bind("<FocusIn>"), f8_before, test.app.bind("<F8>"))))
        for window, sequence, identifier in owned_bindings:
            _check(identifier not in window.bind(sequence)
                   and not test.app.tk.call("info", "commands", identifier),
                   "Keyboard uninstall left a bound owner callback alive")
        _check(not panel._keyboard_windows, "Keyboard uninstall retained windows")
        unbind_callback(test.app, "<FocusIn>", sentinel)
    finally:
        test.close()


CASES = {"create": _create_user_case, "password": _password_case,
         "role": _role_case, "restore": _restore_case, "expired": _expired_session_case,
         "lifecycle": _keyboard_lifecycle_case}


@unittest.skipIf(os.name == "posix" and not os.environ.get("DISPLAY"),
                 "Administrator keyboard checks require a graphical display")
class AdministratorKeyboardTests(unittest.TestCase):
    def _run(self, case):
        with tempfile.TemporaryDirectory(prefix="bifma-keyboard-admin-") as folder:
            path = Path(folder) / "synthetic-history.sqlite3"
            synthetic_archive(path)
            before = hashlib.sha256(path.read_bytes()).digest()
            result = subprocess.run([sys.executable, "-B", "-m", "tests.test_keyboard_admin", case, str(path)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(json.loads(result.stdout), {"case": case, "passed": True})
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_create_user_final_enter_saves_and_validation_keeps_values(self):
        self._run("create")

    def test_change_and_reset_password_final_enter_closes_only_on_success(self):
        self._run("password")

    def test_role_space_and_enter_preserve_last_administrator_guard(self):
        self._run("role")

    def test_restore_final_enter_keeps_verified_restore_and_fresh_login(self):
        self._run("restore")

    def test_password_and_restore_retry_still_reject_revoked_workspace_sessions(self):
        self._run("expired")

    def test_notebook_remap_cleanup_and_window_binding_uninstall_are_scoped(self):
        self._run("lifecycle")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] in CASES:
        CASES[sys.argv[1]](Path(sys.argv[2]))
        print(json.dumps({"case": sys.argv[1], "passed": True}))
    else:
        unittest.main()
