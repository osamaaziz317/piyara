"""Exercise document dialogs through real Tk events using synthetic records."""

from contextlib import closing
import ctypes
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.test_preview_lifecycle import ROOT, _archive, _check, _pump


def _documents(path):
    _archive(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("INSERT INTO ENTRY_TYPE (TYPE_CODE,DESCRIPTION) VALUES ('8','Synthetic sale')")
        connection.execute("INSERT INTO SMST (TRNID,TRNNO,TRNDATE,CMP_ID,TRN_TYPE) "
                           "VALUES ('1','TEST-1','2026-10-02T00:00:00','1','8')")
        connection.executemany("INSERT INTO SDTL (TRNID,VNO,LINE_NUM,ICODE,QTY,RATE,DIS_PER) "
                               "VALUES ('1',?,?,'A','1','2','0')",
                               [(str(i), str(i)) for i in range(1, 22)])
        connection.executemany("INSERT INTO ENTRY_TYPE (TYPE_CODE,DESCRIPTION) VALUES (?,?)",
                               [('2', 'Cash Payment Voucher'), ('3', 'Cash Receipt Voucher')])
        connection.execute("INSERT INTO CHART (CHCODE,CHDESC) VALUES ('0001','Synthetic cash party')")
        connection.executemany("INSERT INTO JMST (TRNID,TRNNO,VTYPE,TRNDATE,CMP_ID) "
                               "VALUES (?, ?, ?, '2026-10-02T00:00:00','1')",
                               [('P', 'PAY-1', '2'), ('R', 'REC-1', '3')])
        connection.executemany("INSERT INTO JDTL (TRNID,DTL_ID,SR,CHCODE,NARRATION,DR,CR) "
                               "VALUES (?, ?, '1','0001', ?, ?, ?)",
                               [('P', 'P1', 'Synthetic paid', '10.125', '0'),
                                ('R', 'R1', 'Synthetic received', '0', '19.755')])


class _Pointer:
    """Real X11 server input: event_generate does not hold the pointer grab."""

    def __init__(self):
        self.x11 = ctypes.CDLL(ctypes.util.find_library('X11'))
        self.xtst = ctypes.CDLL(ctypes.util.find_library('Xtst'))
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.x11.XFlush.argtypes = [ctypes.c_void_p]
        self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                 ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        self.xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                                 ctypes.c_int, ctypes.c_ulong]
        self.display = self.x11.XOpenDisplay(None)
        _check(bool(self.display), 'Could not open the test display')

    def move(self, x, y):
        self.xtst.XTestFakeMotionEvent(self.display, -1, x, y, 0)
        self.x11.XFlush(self.display)

    def button(self, pressed):
        self.xtst.XTestFakeButtonEvent(self.display, 1, pressed, 0)
        self.x11.XFlush(self.display)

    def close(self):
        self.button(False)
        self.x11.XCloseDisplay(self.display)


def _case(mode, path):
    import tkinter as tk
    from tkinter import ttk
    from bifma.preview_ui import PreviewApp

    errors = []
    app = PreviewApp(path)
    app.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    app.update()
    try:
        if mode == 'cash-sections':
            for section, title, field_name, amount in (
                ('cash_receipts', 'Cash Receipt Voucher', 'Cash received', '19.755'),
                ('cash_payments', 'Cash Payment Voucher', 'Cash paid', '10.125'),
            ):
                app._navigation[section].invoke()
                app.update()
                _check(app.current_section == section, 'Cash sidebar opened the wrong section')
                _check(len(app.active_rows) == 1, 'Cash section mixed voucher types')
                _check(app.active_rows[0]['amount'] == amount, 'Cash amount was not preserved')
                app.search_var.set('Synthetic cash party')
                app.search_entry.focus_force()
                app.search_entry.event_generate('<Return>')
                app.update()
                _check(len(app.active_rows) == 1, 'Account search lost the cash voucher')
                app.record_tree.selection_set('0')
                app.record_tree.focus_force()
                app.record_tree.event_generate('<Return>')
                _pump(app, lambda: app.grab_current() is not None)
                dialog = app.grab_current()
                _check(dialog.title().startswith(title), 'Wrong cash document title')
                def labels(parent):
                    result = []
                    for child in parent.winfo_children():
                        if isinstance(child, tk.Label):
                            result.append(child.cget('text'))
                        result.extend(labels(child))
                    return result
                _check(field_name in labels(dialog) and amount in labels(dialog), 'Cash detail total is missing')
                dialog.notebook.select(1)
                app.update()
                dialog.focus_force()
                dialog.event_generate('<Escape>')
                app.update()
                _check(app.grab_current() is None, 'Cash voucher kept input after closing')
        elif mode == 'pointer':
            app.show_section('sales')
            app.update()
            tree = app.record_tree
            x, y, width, height = tree.bbox('0')
            pointer = _Pointer()
            try:
                pointer.move(tree.winfo_rootx() + x + 20, tree.winfo_rooty() + y + height // 2)
                # Keep the second button press down while Tk opens the dialog.
                # This fails with the original immediate grab_set() implementation.
                for _ in range(2):
                    pointer.button(True)
                    for _ in range(5):
                        app.update()
                        time.sleep(.005)
                    pointer.button(False)
                    app.update()
                _pump(app, lambda: bool(app._dialogs) or bool(errors))
                _check(not errors, repr(errors))
                _check(len(app._dialogs) == 1, 'Double-click opened duplicate dialogs')
                dialog = app._dialogs[0]
                _pump(app, lambda: app.grab_current() is dialog)
                _check(dialog.winfo_viewable(), 'Modal voucher must be visible')
                dialog.notebook.select(1)
                app.update()
                def descendants(parent):
                    for child in parent.winfo_children():
                        yield child
                        yield from descendants(child)
                line_tree = next(w for w in descendants(dialog) if isinstance(w, ttk.Treeview))
                _check(len(line_tree.get_children()) == 21, 'Voucher lines did not render')
                dialog.geometry('900x600')
                dialog.notebook.select(0)
                app.update()
                dialog.focus_force()
                dialog.event_generate('<Escape>')
                app.update()
                _check(not dialog.winfo_exists(), 'Escape did not close the voucher')
                _check(app.grab_current() is None, 'Closed voucher retained the grab')
                # Keyboard activation must still work after the mouse-opened
                # dialog has closed, without a second class-binding action.
                tree.focus_force()
                tree.selection_set('0')
                tree.event_generate('<Return>')
                _pump(app, lambda: app.grab_current() is not None)
                dialog = app.grab_current()
                close = next(w for w in descendants(dialog)
                             if isinstance(w, ttk.Button) and w.cget('text') == 'Close')
                close.invoke()
                app.update()
                _check(app.grab_current() is None, 'Close button retained the grab')
            finally:
                pointer.close()
        else:
            top = tk.Toplevel
            def hidden_dialog(*args, **kwargs):
                dialog = top(*args, **kwargs)
                dialog.withdraw()
                return dialog
            with patch('bifma.preview_ui.tk.Toplevel', side_effect=hidden_dialog):
                dialog = app.open_document('sales', '1')
            app.update()
            _check(not dialog.winfo_viewable(), 'Delayed-map fixture became visible')
            _check(app.grab_current() is None, 'Hidden voucher captured input')
            heartbeat = []
            app.after_idle(lambda: heartbeat.append(True))
            _pump(app, lambda: bool(heartbeat))
            if mode == 'delayed':
                dialog.deiconify()
                _pump(app, lambda: app.grab_current() is dialog)
                _check(dialog.winfo_viewable(), 'Shown voucher did not become viewable')
                dialog.destroy()
                app.update()
                _check(app.grab_current() is None, 'Destroyed voucher retained input')
            elif mode == 'close-before-map':
                dialog.destroy()
                app.update()
                _check(app.grab_current() is None, 'Closed hidden voucher captured input')
                with patch('bifma.preview_ui.tk.Toplevel', side_effect=hidden_dialog):
                    app.open_document('sales', '1')
                app.close()
                _pump(app, lambda: app._closed)
        _check(not errors, repr(errors))
    finally:
        app.close()
        if not app._closed:
            app.wait_window(app)


@unittest.skipIf(os.name == 'posix' and not os.environ.get('DISPLAY'),
                 'Document interaction tests require a graphical display')
class DocumentInteractionTests(unittest.TestCase):
    def _run(self, mode):
        with tempfile.TemporaryDirectory(prefix='bifma-document-ui-') as folder:
            path = Path(folder) / 'synthetic.sqlite3'
            _documents(path)
            before = hashlib.sha256(path.read_bytes()).digest()
            result = subprocess.run([sys.executable, '-B', '-m', 'tests.test_preview_documents', mode, str(path)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertEqual(json.loads(result.stdout), {'case': mode, 'passed': True})
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    @unittest.skipUnless(sys.platform.startswith('linux') and ctypes.util.find_library('X11')
                         and ctypes.util.find_library('Xtst'), 'Real pointer regression requires X11/XTest')
    def test_real_double_click_then_keyboard_open_and_close(self):
        self._run('pointer')

    def test_delayed_visibility_keeps_parent_responsive_without_grabbing_input(self):
        self._run('delayed')

    def test_close_before_map_and_parent_shutdown_leave_no_dialog_callback(self):
        self._run('close-before-map')

    def test_cash_sidebar_search_and_voucher_details(self):
        self._run('cash-sections')


class DocumentErrorMessageTests(unittest.TestCase):
    def test_widget_failures_are_not_reported_as_archive_read_failures(self):
        import tkinter as tk
        from bifma.preview_ui import PreviewApp
        for kind, title in ((tk.TclError, 'Interface error'), (ValueError, 'Interface error'),
                            (sqlite3.DatabaseError, 'Unable to read this view')):
            with self.subTest(kind=kind.__name__):
                statuses = []
                app = SimpleNamespace(status_var=SimpleNamespace(set=statuses.append))
                with patch('bifma.preview_ui.messagebox.showerror') as show:
                    PreviewApp.report_callback_exception(app, kind, kind('private record text'), None)
                self.assertEqual(statuses, [title])
                self.assertEqual(show.call_args.args[0], title)
                self.assertNotIn('private record text', show.call_args.args[1])
                if kind is tk.TclError:
                    self.assertNotIn('could not be read', show.call_args.args[1])


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] in {'pointer', 'delayed', 'close-before-map', 'cash-sections'}:
        _case(sys.argv[1], Path(sys.argv[2]))
        print(json.dumps({'case': sys.argv[1], 'passed': True}))
    else:
        unittest.main()
