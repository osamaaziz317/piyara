"""Real Tk Find selectors with synthetic in-memory rows, never business data."""

import ctypes
import ctypes.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tkinter as tk
from tkinter import ttk
import unittest

from bifma.native_ui import Choice, NativeApp, NativeWorkspace
from tests.test_preview_documents import _Pointer
from tests.test_preview_lifecycle import _check
from tools.smoke_native_ui import button, widgets


ROOT = Path(__file__).resolve().parents[1]


def _records():
    rows = [{"id": f"row-{index}", "code": f"C{index:03}",
             "name": "Fixture record", "name_urdu": None} for index in range(205)]
    for index, name in enumerate(("Alpha Hardware", "ALPINE Stores", "Beta", "Chosen record")):
        rows[index]["name"] = name
    rows[5]["name"] = "Rate 50%_ [A.*]"
    rows[6]["name"] = "Straße supplier"
    for index in (10, 11):
        rows[index].update(code="DUP", name="Same label")
    rows[203]["name_urdu"] = "کراچی"
    rows[204]["name_urdu"] = "لاہور"
    return rows


def _selector(root, *, enter_select=False):
    holder = ttk.Frame(root)
    holder.pack(fill="x")
    choice = Choice(holder, _records(), selected="row-3", enter_select=enter_select)
    choice.widget.pack(fill="x")
    choice.add_search_button("Account")
    return holder, choice


def _open(root, holder, *, map_window=True):
    button(holder, "Find…")
    dialog = next(widget for widget in widgets(root)
                  if isinstance(widget, tk.Toplevel) and widget.title() == "Find · Account")
    entry = next(widget for widget in widgets(dialog)
                 if isinstance(widget, ttk.Entry) and not isinstance(widget, ttk.Combobox))
    tree = next(widget for widget in widgets(dialog) if isinstance(widget, ttk.Treeview))
    if map_window:
        root.update()
        entry.focus_force()
        root.update()
        _check(root.focus_get() is entry, "Find input did not take focus")
    return dialog, entry, tree


def _text(entry, value):
    entry.delete(0, "end")
    entry.insert(0, value)


def _codes(tree):
    return [tree.item(row, "values")[0] for row in tree.get_children()]


def _control(parent, name):
    return next(widget for widget in widgets(parent)
                if isinstance(widget, ttk.Button) and widget.cget("text") == name)


def _variables(parent):
    result = []
    for widget in (parent, *widgets(parent)):
        for variable in getattr(widget, "_bifma_variables", ()):
            result.append((variable, str(variable), tuple(command for _, command in variable.trace_info())))
    return result


def _released(interpreter, captured):
    for variable, name, commands in captured:
        _check(variable._tk is None and variable._root is None,
               "Closed Find input retained its Tk interpreter")
        _check(not interpreter.getboolean(interpreter.call("info", "exists", name)),
               "Closed Find input retained its Tcl variable")
        for command in commands:
            _check(not interpreter.call("info", "commands", command),
                   "Closed Find input retained its trace command")


def _filter_case():
    root = tk.Tk()
    errors, changes = [], []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    try:
        holder, choice = _selector(root)
        choice.widget.bind("<<ComboboxSelected>>", lambda event: changes.append(choice.get()))
        dialog, entry, tree = _open(root, holder)
        _check(len(tree.get_children()) == 100, "Initial choices are not paged")
        button(dialog, "Next")
        button(dialog, "Next")
        _check(_codes(tree) == [f"C{index}" for index in range(200, 205)], "Final page is incorrect")

        entry.event_generate("<KeyPress>", keysym="a")
        _check(entry.get() == "a" and len(tree.get_children()) == 7,
               "Typing the first letter did not immediately filter all pages")
        _check(_control(dialog, "Previous").instate(["disabled"]), "Typing did not reset the page")
        entry.event_generate("<KeyPress>", keysym="l")
        _check(_codes(tree) == ["C000", "C001"], "Typing did not narrow names ignoring case")
        entry.event_generate("<KeyPress>", keysym="p")
        entry.event_generate("<KeyPress>", keysym="h")
        _check(_codes(tree) == ["C000"], "Further typing did not narrow names")
        entry.event_generate("<KeyPress>", keysym="BackSpace")
        _check(_codes(tree) == ["C000", "C001"], "Backspace did not expand results")

        root.clipboard_clear()
        root.clipboard_append("کراچی")
        entry.delete(0, "end")
        entry.event_generate("<<Paste>>")
        _check(entry.get() == "کراچی" and _codes(tree) == ["C203"],
               "Pasting Urdu did not immediately find the account")
        entry.selection_range(0, "end")
        entry.event_generate("<KeyPress>", keysym="BackSpace")
        _check(entry.get() == "" and len(tree.get_children()) == 100
               and _control(dialog, "Next").instate(["!disabled"]),
               "Clearing the query did not restore the first full page")

        for term, expected in (("c204", ["C204"]), ("لاہور", ["C204"]),
                               (" STRASSE ", ["C006"]), ("%", ["C005"]),
                               ("_", ["C005"]), ("[A.*]", ["C005"])):
            _text(entry, term)
            _check(_codes(tree) == expected, f"Live literal/code/Unicode search failed for {term!r}")
        button(dialog, "Search")
        entry.event_generate("<Return>")
        _check(_codes(tree) == ["C005"] and dialog.winfo_exists(),
               "Search/Enter stopped retaining their explicit search behavior")
        _check(choice.get() == "row-3" and not changes, "Filtering committed an account selection")

        _text(entry, "Same label")
        _check(_codes(tree) == ["DUP", "DUP"], "Duplicate labels were merged")
        tree.selection_set("1")
        _check(choice.get() == "row-3" and not changes, "Highlighting committed the account")
        _text(entry, "no matching record")
        _check(not tree.get_children(), "No-match search retained stale rows")
        button(dialog, "Use selected")
        _check(dialog.winfo_exists() and choice.get() == "row-3" and not changes,
               "No-match search committed an empty or stale selection")
        _text(entry, "Same label")
        tree.selection_set("1")
        button(dialog, "Use selected")
        root.update()
        _check(not dialog.winfo_exists() and choice.get() == "row-11" and changes == ["row-11"],
               "Explicit selection did not retain the duplicate row's stable ID")
        _check(not errors, repr(errors))
    finally:
        root.destroy()


def _cleanup_case():
    root = tk.Tk()
    other = None
    errors = []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    try:
        holder, choice = _selector(root)
        dialog, entry, tree = _open(root, holder)
        captured = _variables(dialog)
        _text(entry, "Alpha")
        _check(_codes(tree) == ["C000"], "Live search was not active before Escape")
        entry.event_generate("<Escape>")
        root.update()
        _check(not dialog.winfo_exists() and choice.get() == "row-3",
               "Escape changed the selected account or left Find open")
        _released(root.tk, captured)

        embedded = NativeWorkspace(root)
        embedded.pack(fill="both", expand=True)
        embedded_holder, _ = _selector(embedded)
        dialog, entry, tree = _open(root, embedded_holder)
        captured = _variables(dialog)
        _check(dialog.master is embedded, "A main-workspace Find escaped its lifecycle owner")
        embedded.destroy()
        root.update()
        _check(not dialog.winfo_exists(), "Closing the workspace orphaned its Find window")
        _released(root.tk, captured)

        other = tk.Tk()
        other.report_callback_exception = root.report_callback_exception
        other_holder, other_choice = _selector(other)
        other.update()
        dialog, entry, tree = _open(root, holder, map_window=False)
        captured = _variables(dialog)
        _text(entry, "C204")
        _check(_codes(tree) == ["C204"], "Immediate input failed before Find mapped")
        root.destroy()
        _released(root.tk, captured)

        heartbeat = []
        other.after_idle(lambda: heartbeat.append(True))
        other_dialog, other_entry, other_tree = _open(other, other_holder)
        _text(other_entry, "C204")
        other_tree.selection_set("0")
        button(other_dialog, "Use selected")
        other.update()
        _check(heartbeat and other_choice.get() == "row-204",
               "Closing a Find parent broke an unrelated Tk root")
        _check(not errors, repr(errors))
    finally:
        for window in (root, other):
            if window is not None:
                try:
                    window.destroy()
                except tk.TclError:
                    pass


class _Input(_Pointer):
    """X server keyboard input, so focus/grab failures cannot be bypassed."""

    def __init__(self, root):
        super().__init__()
        self.root = root
        self.x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x11.XKeysymToKeycode.restype = ctypes.c_uint
        self.x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        self.x11.XStringToKeysym.restype = ctypes.c_ulong
        self.x11.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        self.xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]

    def pump(self):
        for _ in range(12):
            self.root.update()
            time.sleep(.005)

    def focus_window(self, window):
        # Xvfb has no window manager. Give the application window the same
        # initial activation a WM supplies; every field uses real clicks/keys.
        self.x11.XSetInputFocus(self.display, window.winfo_id(), 1, 0)
        self.x11.XFlush(self.display)
        self.pump()

    def click(self, widget):
        self.click_at(widget.winfo_rootx() + 20, widget.winfo_rooty() + widget.winfo_height() // 2)

    def click_at(self, x, y):
        self.move(x, y)
        self.button(True)
        self.pump()
        self.button(False)
        self.pump()

    def key(self, name, *, control=False):
        code = self.x11.XKeysymToKeycode(self.display, self.x11.XStringToKeysym(name.encode("ascii")))
        modifier = self.x11.XKeysymToKeycode(self.display, self.x11.XStringToKeysym(b"Control_L"))
        if control:
            self.xtst.XTestFakeKeyEvent(self.display, modifier, True, 0)
        self.xtst.XTestFakeKeyEvent(self.display, code, True, 0)
        self.xtst.XTestFakeKeyEvent(self.display, code, False, 0)
        if control:
            self.xtst.XTestFakeKeyEvent(self.display, modifier, False, 0)
        self.x11.XFlush(self.display)
        self.pump()


def _pointer_case():
    root = NativeApp()
    root.geometry("1000x750+0+0")
    errors, changes = [], []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    pointer = _Input(root)
    try:
        entry_window, body = root.dialog("Synthetic new entry")
        entry_window.geometry("850x700+30+30")
        holder, choice = _selector(body)
        choice.widget.bind("<<ComboboxSelected>>", lambda event: changes.append(choice.get()))
        pointer.pump()
        pointer.focus_window(entry_window)

        # The normal Find button must accept actual keys, without focus_force
        # or sending synthetic events to the desired input widget.
        pointer.click(_control(holder, "Find…"))
        dialog = choice._search_window
        entry = choice._search_entry
        tree = next(widget for widget in widgets(dialog) if isinstance(widget, ttk.Treeview))
        _check(dialog.master is entry_window, "Nested Find is not owned by the entry window")
        _check(root.grab_current() is dialog, "Find did not take its nested input grab")
        pointer.key("a")
        pointer.key("l")
        _check(entry.get() == "al" and _codes(tree) == ["C000", "C001"],
               f"Actual keyboard input did not reach the nested Find search: {entry.get()!r}, focus {root.focus_get()}")
        pointer.click(entry)
        pointer.key("p")
        _check(entry.get() == "alp", "Clicking the search field prevented keyboard editing")
        pointer.key("Escape")
        _check(choice._search_window is None and root.grab_current() is entry_window,
               "Closing Find did not restore the entry modal")

        # Clicking the selector's text and typing is also a name search. It
        # must not silently swallow letters in its readonly display field.
        pointer.click(choice.widget)
        pointer.key("a")
        pointer.key("l")
        pointer.key("p")
        pointer.key("h")
        dialog, entry = choice._search_window, choice._search_entry
        _check(dialog is not None, "Typing in the selector did not open Find")
        tree = next(widget for widget in widgets(dialog) if isinstance(widget, ttk.Treeview))
        _check(entry.get() == "alph" and _codes(tree) == ["C000"] and choice.get() == "row-3",
               "Selector typing lost characters, did not filter, or changed the selected ID")
        pointer.key("BackSpace")
        _check(_codes(tree) == ["C000", "C001"], "Real deletion did not expand name matches")
        pointer.key("Escape")
        _check(root.focus_get() is choice.widget, "Find did not restore keyboard focus to the selector")

        root.clipboard_clear()
        root.clipboard_append("کراچی")
        pointer.key("v", control=True)
        dialog, entry = choice._search_window, choice._search_entry
        tree = next(widget for widget in widgets(dialog) if isinstance(widget, ttk.Treeview))
        _check(entry.get() == "کراچی" and _codes(tree) == ["C203"],
               "Pasting into the selector did not open a Unicode name search")
        pointer.key("a", control=True)
        # Ttk Entry select-all bindings vary with Tk/platform; a real mouse
        # triple-click is unnecessary for identity checks already covered above.
        _text(entry, "Same label")
        tree.selection_set("1")
        pointer.click(_control(dialog, "Use selected"))
        _check(choice.get() == "row-11" and changes == ["row-11"],
               "Pointer selection lost the duplicate label's stable ID")
        _check(root.grab_current() is entry_window and root.focus_get() is choice.widget,
               "Committing a selection left the entry form without input")
        _check(not errors, repr(errors))
    finally:
        pointer.close()
        root.destroy()


def _dropdown_case():
    root = NativeApp()
    root.geometry("1000x750+0+0")
    errors, changes = [], []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    pointer = _Input(root)
    try:
        entry_window, body = root.dialog("Synthetic dropdown entry")
        entry_window.geometry("850x700+30+30")
        holder, choice = _selector(body)
        choice.widget.bind("<<ComboboxSelected>>", lambda event: changes.append(choice.get()))
        pointer.pump()
        pointer.focus_window(entry_window)
        pointer.click(choice.widget)
        middle = choice.widget.winfo_height() // 2
        arrow_x = next(x for x in range(choice.widget.winfo_width() - 1, 0, -1)
                       if "downarrow" in choice.widget.identify(x, middle))
        def open_arrow():
            pointer.click_at(choice.widget.winfo_rootx() + arrow_x,
                             choice.widget.winfo_rooty() + middle)
            popdown = str(root.tk.call("ttk::combobox::PopdownWindow", choice.widget))
            _check(root.tk.getboolean(root.tk.call("winfo", "viewable", popdown)),
                   "Arrow no longer opens the ordinary dropdown")
            return popdown

        popdown = open_arrow()
        pointer.key("a")
        pointer.key("l")
        _check(choice._search_window is not None, "Typing after opening the dropdown did not open Find")
        dialog, entry = choice._search_window, choice._search_entry
        tree = next(widget for widget in widgets(dialog) if isinstance(widget, ttk.Treeview))
        _check(entry.get() == "al" and _codes(tree) == ["C000", "C001"],
               "Dropdown typing lost characters or did not filter account names")
        _check(not root.tk.getboolean(root.tk.call("winfo", "viewable", popdown)),
               "Find opened before the native dropdown closed")
        _check(root.grab_current() is dialog and choice.get() == "row-3" and not changes,
               "Dropdown-to-Find changed the selected ID or retained a stale grab")
        pointer.key("Escape")
        _check(root.grab_current() is entry_window and root.focus_get() is choice.widget,
               "Closing dropdown-initiated Find did not restore the entry form")

        root.clipboard_clear()
        root.clipboard_append("کراچی")
        open_arrow()
        pointer.key("v", control=True)
        dialog, entry = choice._search_window, choice._search_entry
        _check(dialog is not None and entry.get() == "کراچی", "Dropdown Unicode paste did not open Find")
        tree = next(widget for widget in widgets(dialog) if isinstance(widget, ttk.Treeview))
        _check(_codes(tree) == ["C203"], "Dropdown paste did not filter the Unicode name")
        pointer.key("Escape")

        # Standard dropdown navigation remains available until typing begins.
        open_arrow()
        pointer.key("Down")
        pointer.key("Up")
        pointer.key("Return")
        _check(choice.get() == "row-3" and changes == ["row-3"],
               "Up/Down/Return no longer select through the native dropdown")
        open_arrow()
        pointer.key("Escape")
        _check(choice._search_window is None and choice.get() == "row-3" and len(changes) == 1,
               "Escape committed a dropdown row or started a search")
        popdown = open_arrow()
        listbox = popdown + ".f.l"
        root.tk.call(listbox, "see", choice.ids.index("row-2"))
        pointer.pump()
        box = tuple(map(int, root.tk.call(listbox, "bbox", choice.ids.index("row-2"))))
        pointer.click_at(int(root.tk.call("winfo", "rootx", listbox)) + box[0] + 15,
                         int(root.tk.call("winfo", "rooty", listbox)) + box[1] + box[3] // 2)
        _check(choice.get() == "row-2" and changes[-1] == "row-2", "Dropdown mouse selection changed IDs")
        commands = tuple(choice.widget._tclCommands or ())
        entry_window.destroy()
        pointer.pump()
        _check(all(not root.tk.call("info", "commands", command) for command in commands),
               "Destroyed selector retained a popup callback")
        _check(not errors, repr(errors))
    finally:
        pointer.close()
        root.destroy()


def _enter_select_case():
    root = NativeApp()
    root.geometry("1000x750+0+0")
    errors, changes = [], []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    pointer = _Input(root)
    try:
        entry_window, body = root.dialog("Synthetic keyboard voucher")
        entry_window.geometry("850x700+30+30")
        _, choice = _selector(body, enter_select=True)
        _, item = _selector(body, enter_select=True)
        item._search_title = "Item"
        next_field = ttk.Entry(body)
        next_field.pack(fill="x")
        chained = [False]
        def selected(event):
            changes.append(choice.get())
            _check(choice._search_window is None and root.grab_current() is entry_window,
                   "Keyboard selection notified the form before restoring its modal")
            if chained[0]:
                item.search("Item")
            else:
                next_field.focus_set()
        choice.widget.bind("<<ComboboxSelected>>", selected)
        item.widget.bind("<<ComboboxSelected>>", lambda event: next_field.focus_set())
        pointer.pump()
        pointer.focus_window(entry_window)
        # With no WM, Tk can still regard the application as inactive after
        # XSetInputFocus. Activate only the outer entry window once; every
        # selector, Find query and following input then uses actual clicks/keys.
        entry_window.focus_force()
        pointer.pump()

        def open_find():
            pointer.click(choice.widget)
            # The selector already has a saved ID. Enter now accepts that ID
            # for the next field; F4 explicitly reopens its live Find window.
            pointer.key("F4")
            dialog, entry = choice._search_window, choice._search_entry
            _check(dialog is not None and root.focus_get() is entry,
                   f"F4 on an enabled keyboard selector did not open editable Find: {dialog}, focus {root.focus_get()}")
            tree = next(widget for widget in widgets(dialog) if isinstance(widget, ttk.Treeview))
            return dialog, entry, tree
        def type_name(text):
            for character in text:
                pointer.key(character)

        dialog, entry, tree = open_find()
        type_name("al")
        _check(_codes(tree) == ["C000", "C001"] and tree.selection() == ("0",),
               "Keyboard Find did not filter and highlight the first match")
        pointer.key("Down")
        _check(tree.selection() == ("1",) and root.focus_get() is entry,
               "Down did not move the result highlight while keeping the query editable")
        pointer.key("Up")
        _check(tree.selection() == ("0",), "Up did not restore the previous result highlight")
        pointer.key("Down")
        pointer.key("Return")
        _check(choice.get() == "row-1" and changes == ["row-1"],
               "Enter did not choose the highlighted account ID")
        pointer.key("7")
        _check(next_field.get() == "7" and root.focus_get() is next_field,
               "Closing Find stole focus back after the form advanced to its next field")

        dialog, entry, tree = open_find()
        type_name("zzzz")
        pointer.key("Return")
        _check(dialog.winfo_exists() and not tree.get_children() and choice.get() == "row-1",
               "Enter with no matching result committed a stale account")
        pointer.key("Escape")
        _check(choice.get() == "row-1" and changes == ["row-1"] and root.focus_get() is choice.widget,
               "Escape changed a previous ID or failed to return to its field")

        dialog, entry, tree = open_find()
        pointer.click(_control(dialog, "Next"))
        pointer.click(entry)
        pointer.key("Up")
        _check(_codes(tree)[-1] == "C099" and tree.selection() == ("99",),
               "Keyboard navigation did not reach the previous results page")
        pointer.key("Down")
        _check(_codes(tree)[0] == "C100" and tree.selection() == ("0",),
               "Keyboard navigation did not reach the next results page")
        pointer.key("Escape")

        dialog, entry, tree = open_find()
        type_name("same")
        pointer.key("Down")
        pointer.key("Return")
        _check(choice.get() == "row-11" and changes[-1] == "row-11",
               "Keyboard selection merged duplicate labels instead of retaining IDs")

        chained[0] = True
        dialog, entry, tree = open_find()
        type_name("beta")
        # Enter may also use the first result if no row is highlighted.
        tree.selection_remove(*tree.selection())
        pointer.key("Return")
        _check(choice.get() == "row-2" and item._search_window is not None
               and root.grab_current() is item._search_window,
               "After-close selection callback could not open the next item Find")
        type_name("alph")
        pointer.key("Return")
        _check(item.get() == "row-0" and root.grab_current() is entry_window
               and root.focus_get() is next_field,
               "Chained account/item keyboard selection did not return to voucher entry")
        _check(not errors, repr(errors))
    finally:
        pointer.close()
        root.destroy()


@unittest.skipIf(os.name == "posix" and not os.environ.get("DISPLAY"),
                 "Find selector checks require a graphical display")
class ChoiceSearchTests(unittest.TestCase):
    def _run(self, case):
        result = subprocess.run([sys.executable, "-B", "-m", "tests.test_choice_search", case],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {"case": case, "passed": True})

    def test_typing_paste_delete_paging_and_explicit_stable_id_selection(self):
        self._run("filter")

    def test_escape_and_parent_shutdown_release_search_without_affecting_another_root(self):
        self._run("cleanup")

    @unittest.skipUnless(sys.platform.startswith("linux") and ctypes.util.find_library("X11")
                         and ctypes.util.find_library("Xtst"), "Real input regression requires X11/XTest")
    def test_nested_find_and_selector_typing_accept_real_pointer_keyboard_and_paste(self):
        self._run("pointer")

    @unittest.skipUnless(sys.platform.startswith("linux") and ctypes.util.find_library("X11")
                         and ctypes.util.find_library("Xtst"), "Real input regression requires X11/XTest")
    def test_open_dropdown_typing_paste_navigation_and_callback_cleanup(self):
        self._run("dropdown")

    @unittest.skipUnless(sys.platform.startswith("linux") and ctypes.util.find_library("X11")
                         and ctypes.util.find_library("Xtst"), "Real input regression requires X11/XTest")
    def test_opt_in_enter_selection_arrows_cancel_paging_and_next_field_focus(self):
        self._run("enter-select")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] in {"filter", "cleanup", "pointer", "dropdown", "enter-select"}:
        {"filter": _filter_case, "cleanup": _cleanup_case, "pointer": _pointer_case,
         "dropdown": _dropdown_case, "enter-select": _enter_select_case}[sys.argv[1]]()
        print(json.dumps({"case": sys.argv[1], "passed": True}))
    else:
        unittest.main()
