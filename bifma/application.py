"""The single BIFMA TRADERS window and its command-line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys
import tkinter as tk
from tkinter import messagebox


DEFAULT_DATABASE = Path(__file__).resolve().parent.parent / "data" / "legacy_recovered.sqlite3"


class Application(tk.Tk):
    """Own one module shell and keep Tk alive until its database worker exits."""

    def __init__(self, database=DEFAULT_DATABASE, *, transaction_database=None):
        self.transactions_workspace = None
        self._closing = self._closed = False
        self._close_poll_job = None
        self.archive_path = Path(database).resolve() if database is not None else None
        self.baseline_source = self.archive_path if transaction_database is None else None
        self.transaction_database = (Path(transaction_database).resolve() if transaction_database is not None
                                     else self.baseline_source.parent / "bifma.sqlite3" if self.baseline_source else None)
        super().__init__()
        try:
            from bifma.native_ui import NativeWorkspace

            self.title("BIFMA TRADERS")
            self.geometry("1280x860")
            self.minsize(1050, 720)
            self.protocol("WM_DELETE_WINDOW", self.close)
            self.transactions_workspace = NativeWorkspace(
                self, self.transaction_database, baseline_source=self.baseline_source,
                archive_path=self.archive_path)
            self.transactions_workspace.pack(fill="both", expand=True)
        except BaseException:
            self.close()
            if not self._closed:
                self.wait_window(self)
            raise

    def report_callback_exception(self, exception, value, traceback):
        panel = self.transactions_workspace
        if self._closing:
            return
        if panel is not None and not panel._closed:
            panel.report_callback_exception(exception, value, traceback)
        else:
            messagebox.showerror("Interface error", "The application could not complete this action. Reopen the application and try again.", parent=self)

    def close(self):
        if self._closing:
            return
        self._closing = True
        panel = self.transactions_workspace
        if panel is not None and not panel._closed:
            panel.close()
        self._wait_for_close()

    def _wait_for_close(self):
        self._close_poll_job = None
        panel = self.transactions_workspace
        if panel is not None and not panel._closed:
            self._close_poll_job = self.after(50, self._wait_for_close)
            return
        # Tk can still have a queued theme notification from widget startup.
        # Let earlier idle callbacks drain while this interpreter exists. This
        # also makes failed startup/retry safe alongside an unrelated Tk root.
        self._close_poll_job = self.after_idle(self._finish_close)

    def _finish_close(self):
        self._close_poll_job = None
        # The frame has joined its worker before destroying its widgets. Only
        # now can its one owning interpreter be destroyed on this Tk thread.
        super().destroy()
        self._closed = True

    def destroy(self):
        self.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="BIFMA TRADERS — records, transactions and reports")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE,
                        help="Original data (defaults to the project's existing data folder)")
    parser.add_argument("--transaction-database", type=Path,
                        help="Open an existing working database instead of preparing the default baseline")
    parser.add_argument("--transactions", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-test", action="store_true", help="Run read-only original-data display diagnostics and exit")
    args = parser.parse_args(argv)
    app = None
    try:
        if args.smoke_test:
            # The archive browser is an internal read-only diagnostic, never a
            # second product shell or a route around the application's login.
            from bifma.preview_ui import PreviewApp

            app = PreviewApp(args.database)
            result = app.smoke_test()
            print(f"Existing-record smoke test passed: {result['companies']} companies, "
                  f"{result['screens']} screens, {result['documents']} documents, "
                  f"{result['stock_reports']} stock reports.")
        else:
            app = Application(args.database, transaction_database=args.transaction_database)
            app.mainloop()
    except (OSError, ValueError, RuntimeError, sqlite3.Error, tk.TclError) as error:
        if app is not None:
            app.close()
            if not app._closed:
                app.wait_window(app)
        message = ("A graphical display with Tk support is required."
                   if isinstance(error, tk.TclError) else str(error))
        print(f"BIFMA TRADERS could not start: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
