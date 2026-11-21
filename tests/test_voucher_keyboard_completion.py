"""Complete entry/review keyboard journeys use only disposable company files."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from bifma.native_schema import native_session
from tests.test_baseline_import import synthetic_archive
from tests.test_business_keyboard import _control, _enter, _find, _type
from tests.test_entry_navigation import _EntryApp
from tests.test_preview_lifecycle import ROOT, _check


def _generic_case(path):
    test = _EntryApp(path)
    panel = test.panel
    try:
        test.login()
        test.app.focus_force()
        panel.transaction_entry("journal")
        dialog = test.window("New · Journal Voucher")
        for title in ("Document number", "Date (YYYY-MM-DD)", "Subtotal rounding", "Final decimal places", "Notes"):
            _enter(test, test.entry(dialog, title))
        line = test.window("Add line")
        _find(test, test.entry(line, "Account"), "Account", "party")
        debit = test.entry(line, "Debit")
        _type(debit, "invalid")
        _enter(test, debit)
        _enter(test, test.entry(line, "Credit"))
        narration = test.entry(line, "Narration")
        _type(narration, "Keyboard balancing line")
        with patch("bifma.native_ui.messagebox.showerror") as error:
            _enter(test, narration)
        _check(error.call_count == 1 and line.winfo_exists() and not test.tree(dialog).get_children(),
               "An invalid final-field Enter closed or applied the line")
        _type(debit, "5")
        _enter(test, narration)
        _check(not line.winfo_exists() and test.app.focus_get() is _control(dialog, "Add line"),
               "Successful final-field Enter did not close the line and restore next-line focus")
        _enter(test, _control(dialog, "Add line"))
        line = test.window("Add line")
        _find(test, test.entry(line, "Account"), "Account", "cash")
        _enter(test, test.entry(line, "Debit"))
        credit = test.entry(line, "Credit")
        _type(credit, "5")
        _enter(test, credit)
        _enter(test, test.entry(line, "Narration"))
        _control(dialog, "Add line").event_generate("<Control-s>")
        review = test.titled("Review draft · ")
        _check(not dialog.winfo_exists() and test.app.focus_get() is _control(review, "Keep as draft"),
               "Save shortcut did not close entry and focus safe review dismissal")
        with native_session(test.path) as connection:
            document = dict(connection.execute("SELECT id,status FROM documents").fetchone())
            _check(document["status"] == "draft" and connection.execute("SELECT count(*) FROM journal_lines").fetchone()[0] == 0,
                   "Completing voucher fields implicitly posted accounting entries")
        _enter(test, _control(review, "Keep as draft"))
        fresh = test.window("New · Journal Voucher")
        _check(not test.tree(fresh).get_children() and test.app.focus_get() is test.entry(fresh, "Document number"),
               "Review Enter did not close and restore the fresh entry page")
        fresh.event_generate("<Escape>")
        test.drain()
        _check(not fresh.winfo_exists(), "Escape did not close unchanged fresh voucher")
        panel.review_draft(panel.transactions.get(panel.actor, panel.company_id, document["id"]), return_to=lambda: None)
        review = test.titled("Review draft · ")
        post = _control(review, "Post document")
        for _ in range(25):
            if test.app.focus_get() is post:
                break
            test.app.focus_get().event_generate("<Tab>")
            test.drain()
        _enter(test, post)
        posted = test.titled("Posted · ")
        _check(test.app.focus_get() is _control(posted, "Close"), "Posted voucher did not focus Close")
        _enter(test, _control(posted, "Close"))
        _check(not posted.winfo_exists(), "Enter could not dismiss the posted voucher")
        with native_session(test.path) as connection:
            _check(connection.execute("SELECT status FROM documents WHERE id=?", (document["id"],)).fetchone()[0] == "posted",
                   "Explicit keyboard Post did not post the reviewed document")
    finally:
        test.close()


def _converter_case(path):
    test = _EntryApp(path)
    panel = test.panel
    try:
        test.login()
        test.app.focus_force()
        panel.transaction_entry("purchase")
        dialog = test.window("New · Purchases")
        add = _control(dialog, "Add line")
        add.focus_set()
        _enter(test, add)
        line = test.window("Add line")
        _find(test, test.entry(line, "Item"), "Item", "synthetic")
        converter = _control(line, "Convert large / base quantities")
        # Traverse by Tab to exercise reachability of the secondary action.
        for _ in range(25):
            if test.app.focus_get() is converter:
                break
            test.app.focus_get().event_generate("<Tab>")
            test.drain()
        _enter(test, converter)
        convert = test.window("Convert quantity")
        large = test.entry(convert, "Large quantity (Pack)")
        small = test.entry(convert, "Base quantity (Piece)")
        _check(test.app.focus_get() is large, "Quantity conversion did not focus its first field")
        _type(large, "2")
        _enter(test, large)
        _type(small, "1")
        _enter(test, small)
        _check(not convert.winfo_exists() and test.entry(line, "Quantity in base units").get() == "25"
               and test.app.focus_get() is test.entry(line, "Quantity in base units"),
               "Final quantity-converter Enter did not apply and restore line quantity focus")
        line.destroy()
        test.drain()
        notes = test.entry(dialog, "Notes")
        _type(notes, "Keep unsaved text")
        with patch("bifma.native_ui.messagebox.askyesno", return_value=False) as question:
            notes.focus_set()
            notes.event_generate("<Escape>")
            test.drain()
        _check(question.call_count == 1 and dialog.winfo_exists() and notes.get() == "Keep unsaved text",
               "Escape discarded an unfinished document without respecting its guard")
        with patch("bifma.native_ui.messagebox.askyesno", return_value=True):
            notes.event_generate("<Escape>")
            test.drain()
        _check(not dialog.winfo_exists(), "Confirmed keyboard close did not dismiss the unfinished document")
    finally:
        test.close()


def _busy_case(path):
    test = _EntryApp(path)
    panel = test.panel
    gate = None
    try:
        test.login()
        test.app.focus_force()
        for kind, title in (("cash_receipt", "Cash Receipt Voucher"), ("sale", "Sale Voucher"), ("journal", "Journal Voucher")):
            panel.transaction_entry(kind)
            dialog = test.window("New · " + title)
            if kind == "journal":
                add = _control(dialog, "Add line")
                add.focus_set()
                _enter(test, add)
                line = test.window("Add line")
                _find(test, test.entry(line, "Account"), "Account", "party")
                _type(test.entry(line, "Debit"), "5")
                _enter(test, test.entry(line, "Debit"))
                _enter(test, test.entry(line, "Credit"))
                _enter(test, test.entry(line, "Narration"))
                tree = test.tree(dialog)
                tree.selection_set("0")
                first_control = tree
                events = [(tree, "<Delete>"), (tree, "<Insert>"), (tree, "<Return>"), (tree, "<Control-s>")]
                snapshot = lambda: [tree.item(item, "values") for item in tree.get_children()]
            else:
                form = dialog.cash_form if kind == "cash_receipt" else dialog.sale_form
                row = form.rows[0]
                if kind == "cash_receipt":
                    row.inputs["code"].set("0002")
                    row.inputs["amount"].set("invalid")
                    first_control = row.widgets["amount"]
                    enter_control = form.rows[-1].widgets["amount"]
                else:
                    form.inputs["buyer_code"].set("0002")
                    row.inputs["code"].set("A")
                    first_control = row.widgets["rate"]
                    enter_control = form.rows[-1].widgets["discount_percent"]
                events = [(first_control, "<Control-Delete>"), (enter_control, "<Return>"),
                          (form.rows[-1].widgets["code"], "<Return>"), (form.widgets["notes"], "<Control-s>")]
                if kind == "sale":
                    events.append((form.widgets["freight"], "<KP_Enter>"))
                snapshot = lambda: [tuple(control.get() for control in item.inputs.values()) for item in form.rows]
            before = snapshot()
            windows = set(test.windows())
            first_control.focus_set()
            gate = Event()
            panel.run(lambda: gate.wait(15))
            _check(first_control.instate(["disabled"]), "Worker did not freeze voucher controls")
            with patch("bifma.cash_voucher_ui.messagebox.showerror") as cash_error, patch("bifma.sale_voucher_ui.messagebox.showerror") as sale_error:
                for control, sequence in events:
                    control.focus_force()
                    control.event_generate(sequence)
                    test.app.update()
                _check(cash_error.call_count == sale_error.call_count == 0,
                       "A disabled Save shortcut still attempted validation during a worker operation")
            _check(snapshot() == before and set(test.windows()) == windows,
                   "Keyboard shortcuts changed the pending voucher or opened another dialog while frozen")
            gate.set()
            test.drain()
            gate = None
            _check(not first_control.instate(["disabled"]), "Worker completion did not reenable entry")
            first_control.focus_force()
            first_control.event_generate("<Delete>" if kind == "journal" else "<Control-Delete>")
            test.drain()
            _check(len(snapshot()) == len(before) - 1, "The enabled removal shortcut stopped working after the worker")
            dialog.destroy()
            test.drain()
        with native_session(test.path) as connection:
            _check(connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 0,
                   "Blocked keyboard actions saved an unfinished voucher")
    finally:
        if gate is not None:
            gate.set()
        test.close()


CASES = {"generic": _generic_case, "converter": _converter_case, "busy": _busy_case}


@unittest.skipIf(os.name == "posix" and not os.environ.get("DISPLAY"), "Keyboard completion checks require a graphical display")
class VoucherKeyboardCompletionTests(unittest.TestCase):
    def _run(self, case):
        with tempfile.TemporaryDirectory(prefix="bifma-voucher-keyboard-") as folder:
            path = Path(folder) / "synthetic-history.sqlite3"
            synthetic_archive(path)
            before = hashlib.sha256(path.read_bytes()).digest()
            result = subprocess.run([sys.executable, "-B", "-m", "tests.test_voucher_keyboard_completion", case, str(path)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(json.loads(result.stdout), {"case": case, "passed": True})
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_final_line_save_review_and_explicit_post_all_by_keyboard(self):
        self._run("generic")

    def test_quantity_converter_completes_and_escape_preserves_unsaved_entry(self):
        self._run("converter")

    def test_worker_freeze_blocks_keyboard_save_delete_and_row_creation(self):
        self._run("busy")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] in CASES:
        CASES[sys.argv[1]](Path(sys.argv[2]))
        print(json.dumps({"case": sys.argv[1], "passed": True}))
    else:
        unittest.main()
