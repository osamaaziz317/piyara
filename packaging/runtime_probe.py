"""Data-free runtime verification shared by the build and frozen history app."""

import platform
import sqlite3
import struct
import sys
import tkinter as tk

from bifma import __version__


def check_runtime():
    if sys.version_info < (3, 10):
        raise RuntimeError("BIFMA requires Python 3.10 or newer.")
    if sqlite3.sqlite_version_info < (3, 37):
        raise RuntimeError("BIFMA requires SQLite 3.37 or newer.")
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE runtime_check(value TEXT) STRICT")
        if connection.execute("SELECT json_valid('{}')").fetchone()[0] != 1:
            raise RuntimeError("SQLite JSON support is required.")
    window = tk.Tk()
    try:
        window.withdraw()
        window.update_idletasks()
        tk_version = str(window.tk.call("package", "require", "Tk"))
        if tuple(int(part) for part in tk_version.split(".")[:2]) < (8, 6):
            raise RuntimeError("The portable build requires Tcl/Tk 8.6 or newer.")
        return {
            "application_version": __version__,
            "python_version": platform.python_version(),
            "sqlite_version": sqlite3.sqlite_version,
            "tk_version": tk_version,
            "platform": sys.platform,
            "machine": platform.machine(),
            "bits": struct.calcsize("P") * 8,
        }
    finally:
        window.destroy()
