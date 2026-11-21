"""Reference-style login and real Enter navigation on private synthetic files."""

import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from bifma.application import Application
from bifma.native_schema import initialize_database, native_session, native_transaction
from bifma.security import SecurityService
from tests.test_preview_lifecycle import ROOT, _check, _pump
from tools.smoke_native_ui import button


class _LoginApp:
    def __init__(self, path):
        self.errors = []
        self.callbacks = []
        self.messages = patch("bifma.native_ui.messagebox.showerror",
                              side_effect=lambda title, text, **kwargs: self.errors.append((title, text)))
        self.messages.start()
        self.app = Application(database=None, transaction_database=path)
        self.panel = self.app.transactions_workspace
        self.app.report_callback_exception = lambda kind, value, tb: self.callbacks.append((kind.__name__, str(value)))
        self.drain()
        self.app.focus_force()  # Supply the window-manager activation on private Xvfb.
        self.form.widgets["username"].focus_force()
        self.app.update()

    @property
    def form(self):
        return self.panel._login_form

    def drain(self):
        self.app.update()
        _pump(self.app, lambda: self.panel.future is None and self.panel._startup_job is None)
        self.app.update()
        _pump(self.app, lambda: self.panel.future is None and self.panel._startup_job is None)
        _check(not self.callbacks, repr(self.callbacks))

    def type(self, key, value):
        control = self.form.widgets[key]
        control.delete(0, "end")
        control.insert(0, value)
        self.app.update()

    def enter(self, key):
        control = self.form.widgets[key] if isinstance(key, str) else key
        self.app.update()
        _check(self.app.focus_get() is control,
               f"Expected actual keyboard focus on {key}, got {self.app.focus_get()}")
        control.event_generate("<Return>")
        self.drain()

    def profile(self, username):
        self.form.widgets["username"].focus_force()
        self.type("username", username)
        self.enter("username")

    def logout(self):
        self.panel.logout()
        self.drain()
        self.form.widgets["username"].focus_force()
        self.app.update()

    def close(self):
        self.app.close()
        if not self.app._closed:
            self.app.wait_window(self.app)
        self.messages.stop()
        gc.collect()


def _database(path, *, companies=2):
    initialize_database(path)
    security = SecurityService(path)
    owner = security.bootstrap_admin("o", "1", display_name="Owner مکمل نام")
    with native_transaction(path) as connection:
        for number in range(companies):
            connection.execute("INSERT INTO companies(id,code,name) VALUES (?,?,?)",
                               (f"00000000-0000-0000-0000-{number:012d}", f"C{number}", "Same company name"))
    return security, owner


def _bootstrap(path):
    initialize_database(path)
    test = _LoginApp(path)
    try:
        _check(test.panel.actor is None and test.form is not None, "First use bypassed owner creation")
        _check(test.app.winfo_width() <= 900 and test.app.winfo_height() <= 650,
               "Owner creation did not use the compact reference login window")
        test.type("username", "u")
        test.enter("username")
        _check(test.form.widgets["display_name"].get() == "u", "First-use Enter did not supply the username as an editable name default")
        _check(not test.form.widgets["display_name"].instate(["readonly"]), "First-use full name cannot be edited")
        _check(test.panel.actor is None, "Username Enter implicitly created an administrator")
        test.type("display_name", "Operator مکمل نام")
        test.enter("display_name")
        test.type("password", "7")
        _check(test.form.widgets["password"].cget("show"), "Password is displayed in plain text")
        test.enter("password")
        test.type("confirm", "7")
        test.enter("confirm")
        _check(test.panel.actor is not None, "Final confirmation Enter did not create the owner")
        _check(test.panel.actor is not None and test.panel.actor.user.username == "u"
               and test.panel.actor.user.display_name == "Operator مکمل نام", "Short credentials or entered full name were not saved")
        _check(not test.errors, repr(test.errors))
        _check(test.app.winfo_width() >= 1000 and test.app.winfo_height() >= 680,
               "Successful owner creation did not restore the application window")
        with native_session(path) as connection:
            _check(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1,
                   "First-use Enter created extra users")
        test.logout()
        _check(all(not test.form.widgets[key].get() for key in ("username", "display_name", "password")),
               "Logout retained credentials or full name")
        test.type("username", "u")
        test.type("password", "7")
        button(test.panel, "OK")  # Mouse submission works without a separate name lookup.
        test.drain()
        _check(test.panel.actor is not None, "Clicking OK without Enter lookup cannot sign in")
    finally:
        test.close()


def _name_and_company(path):
    security, owner = _database(path)
    test = _LoginApp(path)
    try:
        test.profile("o")
        _check(test.form.widgets["display_name"].get() == "Owner مکمل نام", "Saved full name was not completed on User Enter")
        _check(test.form.widgets["display_name"].instate(["readonly"]), "Saved full name can be edited on login")
        test.form.widgets["display_name"].insert(0, "changed")
        _check(test.form.widgets["display_name"].get() == "Owner مکمل نام", "Readonly name accepted an edit")
        test.enter("display_name")
        test.type("password", "1")
        test.enter("password")
        _check(test.panel.actor is None and test.app.focus_get() is test.form.widgets["company"],
               "Password Enter skipped Company when multiple companies are available")
        company = test.form.widgets["company"]
        _check(company.instate(["readonly"]) and len(company.cget("values")) == 2,
               "Company choices are not a readonly selector")
        _check(len(set(name for _, name in test.form.companies)) == 1,
               "Duplicate-name fixture did not preserve duplicate names")
        chosen = test.form.companies[1][0]
        company.current(1)
        company.event_generate("<<ComboboxSelected>>")
        test.enter("company")
        _check(test.panel.actor is not None and test.panel.company_id == chosen,
               "Duplicate company labels lost the selected stable company ID")
        _check(not test.errors, repr(test.errors))
        test.logout()
        _check(test.app.winfo_width() <= 900 and test.app.winfo_height() <= 650,
               "Logout did not restore compact login geometry")
        _check(not test.form.widgets["company"].get() and not test.form.companies,
               "Logout retained previous company choices")
        _check(test.form.widgets["password"].get() == "", "Logout retained the password")
        button(test.panel, "Cancel")
        if not test.app._closed:
            test.app.wait_window(test.app)
        _check(test.app._closed, "Login Cancel did not close the owning application")
    finally:
        security.logout(owner)
        test.close()


def _clear_and_errors(path):
    security, owner = _database(path, companies=1)
    inactive = security.create_user(owner, "inactive", "2", display_name="Inactive name")
    security.set_active(owner, inactive.id, False)
    test = _LoginApp(path)
    try:
        test.profile("o")
        test.enter("display_name")
        test.type("password", "temporary")
        test.type("username", "missing")
        _check(not test.form.widgets["display_name"].get() and not test.form.widgets["password"].get()
               and not test.form.widgets["company"].get(), "Changing username retained another user's profile or password")
        for username in ("missing", "inactive"):
            test.profile(username)
            _check(not test.form.widgets["display_name"].get() and not test.form.companies,
                   "Unknown or inactive user completed a saved profile")
            _check(test.app.focus_get() is test.form.widgets["password"],
                   "Unknown profile did not leave password entry available")
            test.type("password", "2")
            test.enter("password")
            _check(test.panel.actor is None and test.form is not None, "Invalid credentials opened the company shell")
            _check(len(test.errors) >= 1, "Failed login did not show a generic authentication error")
        _check(test.errors[-1][1] == test.errors[-2][1], "Missing and inactive users show different authentication failures")
        test.profile("o")
        test.enter("display_name")
        test.type("password", "wrong")
        test.enter("password")
        _check(test.panel.actor is None and test.form is not None, "Wrong password left the login form")
        _check(test.app.focus_get() is test.form.widgets["password"], "Failed authentication did not restore password focus")
        test.type("password", "1")
        test.enter("password")
        _check(test.panel.actor is not None and test.panel.company_id == "00000000-0000-0000-0000-000000000000", "Single-company Enter retry did not sign in")
    finally:
        test.close()


def _stale_profile(path):
    security, owner = _database(path)
    test = _LoginApp(path)
    entered, release = threading.Event(), threading.Event()
    original = test.panel.security.login_profile
    def delayed(username):
        value = original(username)
        entered.set()
        _check(release.wait(10), "Stale-profile worker was not released")
        return value
    try:
        with patch.object(test.panel.security, "login_profile", side_effect=delayed):
            test.type("username", "o")
            test.form.widgets["username"].event_generate("<Return>")
            _pump(test.app, entered.is_set)
            # A pending callback must not populate fields after identity changes,
            # even if the worker has already obtained a valid profile snapshot.
            test.form.inputs["username"].set("missing")
            release.set()
            test.drain()
        _check(test.form.widgets["username"].get() == "missing"
               and not test.form.widgets["display_name"].get()
               and not test.form.widgets["company"].get(),
               "An obsolete async lookup replaced the new user's empty profile")
        _check(test.panel.actor is None and not test.errors, "Profile lookup authenticated or failed unexpectedly")
    finally:
        release.set()
        test.close()


def _fresh_access(path):
    security, owner = _database(path)
    staff = security.create_user(owner, "s", "2", display_name="Saved staff name")
    security.set_company_access(owner, staff.id, ["00000000-0000-0000-0000-000000000000", "00000000-0000-0000-0000-000000000001"])
    security.set_permissions(owner, staff.id, "sales", ["read"])
    test = _LoginApp(path)
    try:
        test.profile("s")
        test.enter("display_name")
        test.type("password", "2")
        test.enter("password")
        choices = test.form.companies
        index = next(index for index, (identifier, _) in enumerate(choices) if identifier == "00000000-0000-0000-0000-000000000001")
        test.form.widgets["company"].current(index)
        test.form.widgets["company"].event_generate("<<ComboboxSelected>>")
        security.set_company_access(owner, staff.id, ["00000000-0000-0000-0000-000000000000"])
        test.enter("company")
        _check(test.panel.actor is None and test.form is not None,
               "Login accepted a company grant that was revoked after name lookup")
        _check(test.errors, "Revoked company selection did not explain why login remained open")
        with native_session(path) as connection:
            active = connection.execute("SELECT COUNT(*) FROM auth_sessions WHERE user_id=? AND revoked_at IS NULL", (staff.id,)).fetchone()[0]
            _check(active == 0, "Rejected company login left an authenticated session active")
        test.profile("s")
        _check([identifier for identifier, _ in test.form.companies] == ["00000000-0000-0000-0000-000000000000"],
               "Retry did not refresh revoked company choices")
        test.enter("display_name")
        test.type("password", "2")
        test.enter("password")
        _check(test.panel.actor is not None and test.panel.company_id == "00000000-0000-0000-0000-000000000000",
               "Fresh allowed company could not be selected after a grant change")
    finally:
        test.close()


def _cancel_pending(path):
    _database(path)
    test = _LoginApp(path)
    entered, release = threading.Event(), threading.Event()
    original = test.panel.security.login_profile
    def delayed(username):
        profile = original(username)
        entered.set()
        _check(release.wait(10), "Cancelled profile worker was not released")
        return profile
    try:
        with patch.object(test.panel.security, "login_profile", side_effect=delayed):
            test.type("username", "o")
            test.form.widgets["username"].event_generate("<Return>")
            _pump(test.app, entered.is_set)
            # Closing the login while a read is outstanding drains the worker,
            # then discards its profile instead of touching destroyed widgets.
            test.app.after(100, release.set)
            test.app.close()
            if not test.app._closed:
                test.app.wait_window(test.app)
        _check(test.app._closed and test.panel._closed,
               "Closing during profile lookup did not drain both application and workspace")
        _check(not test.errors and not test.callbacks, "Late profile callback accessed a closed login")
        with native_session(path) as connection:
            _check(connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 1,
                   "Cancelled profile lookup issued an authentication session")
    finally:
        release.set()
        test.close()


CASES = {"bootstrap": _bootstrap, "name-and-company": _name_and_company,
         "clear-and-errors": _clear_and_errors, "stale-profile": _stale_profile,
         "fresh-access": _fresh_access, "cancel-pending": _cancel_pending}


@unittest.skipIf(os.name == "posix" and not os.environ.get("DISPLAY"),
                 "Login keyboard checks require a graphical display")
class LoginUITests(unittest.TestCase):
    def _run(self, case):
        with tempfile.TemporaryDirectory(prefix="bifma-login-ui-") as folder:
            path = Path(folder) / "synthetic.sqlite3"
            result = subprocess.run([sys.executable, "-B", "-m", "tests.test_login_ui", case, str(path)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(json.loads(result.stdout), {"case": case, "passed": True})

    def test_first_owner_short_credentials_enter_sequence_and_click_login(self):
        self._run("bootstrap")

    def test_saved_name_readonly_enter_and_duplicate_company_ids(self):
        self._run("name-and-company")

    def test_username_change_unknown_inactive_wrong_password_and_retry(self):
        self._run("clear-and-errors")

    def test_obsolete_async_profile_does_not_fill_changed_username(self):
        self._run("stale-profile")

    def test_company_access_is_checked_again_after_password_authentication(self):
        self._run("fresh-access")

    def test_close_during_profile_lookup_drains_worker_without_authentication(self):
        self._run("cancel-pending")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] in CASES:
        CASES[sys.argv[1]](Path(sys.argv[2]))
        print(json.dumps({"case": sys.argv[1], "passed": True}))
    else:
        unittest.main()
