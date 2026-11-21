"""Real Tk and synthetic archives in bounded subprocesses, never business data."""

from contextlib import closing, redirect_stderr
import gc
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import weakref


ROOT = Path(__file__).resolve().parents[1]


def _archive(path):
    schema = json.loads((ROOT / "docs/legacy_schema.json").read_text(encoding="utf-8"))
    with closing(sqlite3.connect(path)) as connection, connection:
        for table in schema["tables"]:
            columns = ",".join('"' + column["name"] + '" TEXT' for column in table["columns"])
            connection.execute('CREATE TABLE "' + table["name"] + '" (' + columns + ')')
        connection.execute("CREATE TABLE _recovery_metadata (key TEXT PRIMARY KEY,value TEXT)")
        connection.execute("INSERT INTO _recovery_metadata VALUES ('report',?)",
                           (json.dumps({"status": "recovered_unverified"}),))
        connection.execute("INSERT INTO CONFIG (CMP_ID,CMPNAME) VALUES ('1','Synthetic lifecycle company')")
        connection.execute("INSERT INTO PUR_ITEM (ICODE,INAME) VALUES ('A','Synthetic lifecycle item')")
        connection.execute("INSERT INTO STORE_LOC (STORE_ID,STORE_DESC,CMP_ID) VALUES ('1','Synthetic store','1')")
        connection.execute("""INSERT INTO OPEN_STOCK
            (TRNID,ICODE,OPEN_QTY,OPEN_RATE,OPEN_DATE,STORE_ID,CMP_ID)
            VALUES ('1','A','10','2','2026-01-01T00:00:00','1','1')""")


def _check(condition, message):
    if not condition:
        raise AssertionError(message)


def _pump(app, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        _check(time.monotonic() < deadline, "Tk lifecycle condition timed out")
        app.update()
        time.sleep(0.005)


def _variable_state(app, *, screen_only=False):
    names, commands, references = [], [], []
    for key, variable in vars(app).items():
        if not key.endswith("_var") or (screen_only and key in {"company_var", "status_var"}):
            continue
        if variable._tk is None:
            continue
        names.append(str(variable))
        commands.extend(command for _mode, command in variable.trace_info())
        references.append(weakref.ref(variable))
        # Delay Python reclamation until an explicitly scheduled collection.
        variable._synthetic_cycle = variable
    return names, commands, references


def _released(interpreter, state):
    names, commands, references = state
    for name in names:
        _check(not interpreter.getboolean(interpreter.call("info", "exists", name)),
               "A destroyed widget retained its Tcl variable")
    for command in commands:
        _check(not interpreter.call("info", "commands", command),
               "A destroyed widget retained its Tcl trace command")
    for reference in references:
        variable = reference()
        _check(variable is None or (variable._tk is None and variable._root is None),
               "A destroyed widget retained its interpreter or root")


def _close_case(path, method):
    from bifma.legacy_stock import LegacyStock
    from bifma.preview_ui import PreviewApp

    started, collect, collected, release = (threading.Event() for _ in range(4))
    worker_threads, callback_errors, unraisables, destroyed, heartbeats = [], [], [], [], []
    main_thread = threading.get_ident()
    original_balance, original_hook = LegacyStock.balances, sys.unraisablehook
    app = None

    def held_balance(service, *args, **kwargs):
        # This closure captures no root, widgets, variables or application.
        worker_threads.append(threading.get_ident())
        started.set()
        _check(collect.wait(15), "Synthetic collection gate timed out")
        gc.collect()
        collected.set()
        _check(release.wait(15), "Synthetic stock read release timed out")
        return original_balance(service, *args, **kwargs)

    def unraisable(info):
        unraisables.append((info.exc_type.__name__, str(info.exc_value)))
        original_hook(info)

    def callback_error(kind, value, _traceback):
        callback_errors.append((kind.__name__, str(value)))

    sys.unraisablehook = unraisable
    try:
        with patch.object(LegacyStock, "balances", held_balance):
            app = PreviewApp(path)
            app.report_callback_exception = callback_error
            interpreter = app.tk
            root_reference = weakref.ref(app)
            app.bind("<Destroy>", lambda event: destroyed.append(threading.get_ident())
                     if event.widget is app else None, add=True)
            app.show_section("stock")
            _pump(app, started.is_set)
            running = app._stock_future
            old_variables = _variable_state(app, screen_only=True)
            _check(len(old_variables[0]) == 9 and len(old_variables[1]) == 2,
                   "The probe did not observe all stock-screen variables and traces")
            app.show_section("products")
            _released(interpreter, old_variables)
            record_variables = _variable_state(app, screen_only=True)
            app.show_section("accounts")
            _released(interpreter, record_variables)
            app.show_section("stores")
            app.show_section("units")
            app.show_section("stock")
            queued = app._stock_future
            _check(queued is not running and not queued.running(), "A second read was not queued")
            app.show_section("dashboard")
            _check(queued.cancelled() and app._stock_future is None,
                   "Navigation did not cancel and detach the queued read")
            _check(running in app._stock_futures, "A superseded running read was forgotten")
            final_variables = _variable_state(app)
            _check(len(final_variables[0]) == 2, "Screen changes retained live screen variables")

            getattr(app, method)()
            app.close()  # A repeated request must preserve the shutdown poll.
            _check(not app._closed and app.winfo_exists(), "Tk closed before a running read finished")
            _check(app._stock_future is None, "The test did not exercise a detached read")
            app.after(0, lambda: heartbeats.append(threading.get_ident()))
            collect.set()
            _pump(app, lambda: collected.is_set() and bool(heartbeats))
            _check(not app._closed and not running.done(), "Close did not wait for the held read")
            _check(all(reference() is None for reference in old_variables[2] + record_variables[2]),
                   "Superseded variables survived worker collection")
            _check(heartbeats == [main_thread] and len(worker_threads) == 1
                   and worker_threads[0] != main_thread, "Tk did not remain responsive during worker collection")
            # Closing controls must not start another query or use detached variables.
            app.show_section("stock")
            app._company_changed()
            app._schedule_search()
            app._search_now()
            app._change_page(1)
            app._focus_search()
            app.open_document("sales", "does-not-exist")
            _check(app.current_section == "dashboard" and app._search_job is None,
                   "Closing allowed a new screen or delayed search")
            release.set()
            _pump(app, lambda: app._closed)
            _check(destroyed == [main_thread], "Root destruction was not exactly once on the Tk thread")
            _check(queued.cancelled() and len(worker_threads) == 1, "A cancelled queued read executed")
            _check(len(running.result()) == 1 and running.result()[0].closing_quantity == 10,
                   "The real synthetic stock read did not finish")
            _check(not app._stock_futures and app._stock_poll_job is None
                   and app._stock_reap_job is None and app._close_poll_job is None,
                   "A future or Tk shutdown callback remained registered")
            _released(interpreter, final_variables)
            app = None
            del interpreter
            gc.collect()
            _check(root_reference() is None, "The closed root was retained")
            _check(all(reference() is None for reference in final_variables[2]),
                   "The closed root's variables were retained")
            _check(not callback_errors and not unraisables, "Tk raised a callback/finalizer exception")
    finally:
        collect.set()
        release.set()
        if app is not None:
            app.close()
            _pump(app, lambda: app._closed)
        sys.unraisablehook = original_hook


def _startup_case(path):
    import tkinter as tk
    from bifma import preview_ui

    previous = tk.Tk()
    previous.withdraw()
    constructed = []
    initialize = preview_ui.PreviewApp._initialize_interface

    def initialize_and_record(app, company_rows):
        constructed.append(weakref.ref(app))
        return initialize(app, company_rows)

    try:
        with patch.object(preview_ui.PreviewApp, "_initialize_interface", initialize_and_record), \
                patch.object(preview_ui.tkfont, "families", side_effect=tk.TclError("Synthetic font failure")):
            output = io.StringIO()
            with redirect_stderr(output):
                result = preview_ui.main(["--database", str(path)])
        gc.collect()
        _check(output.getvalue() == "BIFMA TRADERS preview could not start: "
               "A graphical display with Tk support is required to run the development preview.\n",
               "Unexpected output during partial-startup cleanup")
        _check(result == 1 and len(constructed) == 1 and constructed[0]() is None,
               "A partially constructed preview root was retained")
        _check(tk._default_root is previous and previous.winfo_exists(),
               "Startup cleanup destroyed an unrelated root")
    finally:
        previous.destroy()


def _smoke_error_case(path):
    import tkinter as tk
    from bifma import preview_ui
    from bifma.legacy_stock import LegacyStock

    previous = tk.Tk()
    previous.withdraw()
    started, release = threading.Event(), threading.Event()
    roots, reads, heartbeats = [], [], []
    original_balance = LegacyStock.balances

    def held_balance(service, *args, **kwargs):
        started.set()
        _check(release.wait(15), "Synthetic error-path read release timed out")
        return original_balance(service, *args, **kwargs)

    def failing_smoke(app):
        roots.append(weakref.ref(app))
        app.show_section("stock")
        _pump(app, started.is_set)
        reads.append(app._stock_future)
        app.show_section("dashboard")
        _check(app._stock_future is None and not reads[0].done(),
               "Smoke error did not leave a superseded running read")

        def unblock():
            heartbeats.append(not app._closed)
            release.set()

        app.after(30, unblock)
        raise RuntimeError("Synthetic smoke failure")

    try:
        with patch.object(LegacyStock, "balances", held_balance), \
                patch.object(preview_ui.PreviewApp, "smoke_test", failing_smoke):
            output = io.StringIO()
            with redirect_stderr(output):
                result = preview_ui.main(["--database", str(path), "--smoke-test"])
        gc.collect()
        _check(result == 1 and output.getvalue()
               == "BIFMA TRADERS preview could not start: Synthetic smoke failure\n",
               "The CLI did not report the smoke failure")
        _check(heartbeats == [True] and reads[0].done()
               and reads[0].result()[0].closing_quantity == 10,
               "The CLI returned before its pending read and responsive close finished")
        _check(len(roots) == 1 and roots[0]() is None,
               "The failed smoke run retained its preview root")
        _check(tk._default_root is previous and previous.winfo_exists(),
               "Error-path cleanup destroyed an unrelated root")
    finally:
        release.set()
        for reference in roots:
            app = reference()
            if app is not None:
                app.close()
                _pump(app, lambda: app._closed)
        previous.destroy()


@unittest.skipIf(os.name == "posix" and not os.environ.get("DISPLAY"),
                 "Real Tk lifecycle tests require an X11 DISPLAY on POSIX")
class PreviewLifecycleTests(unittest.TestCase):
    def _run(self, mode):
        with tempfile.TemporaryDirectory(prefix="bifma-preview-lifecycle-") as folder:
            path = Path(folder) / "synthetic.sqlite3"
            _archive(path)
            before = hashlib.sha256(path.read_bytes()).digest()
            result = subprocess.run(
                [sys.executable, "-B", "-m", "tests.test_preview_lifecycle", mode, str(path)],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=45, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(json.loads(result.stdout), {"case": mode, "passed": True})
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_close_drains_superseded_reads_responsively_and_releases_tk(self):
        self._run("close")

    def test_direct_destroy_drains_superseded_reads_and_cancels_queued_work(self):
        self._run("destroy")

    def test_partial_startup_releases_only_its_own_root(self):
        self._run("startup")

    def test_cli_smoke_error_drains_its_own_window_with_another_root_alive(self):
        self._run("smoke-error")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] in {"close", "destroy", "startup", "smoke-error"}:
        case, database = sys.argv[1], Path(sys.argv[2])
        if case == "startup":
            _startup_case(database)
        elif case == "smoke-error":
            _smoke_error_case(database)
        else:
            _close_case(database, case)
        print(json.dumps({"case": case, "passed": True}))
    else:
        unittest.main()
