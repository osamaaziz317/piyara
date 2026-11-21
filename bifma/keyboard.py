"""Window-scoped keyboard support for the maintained Tk application."""

import tkinter as tk
from tkinter import ttk


def available(widget):
    try:
        return widget.winfo_exists() and (not isinstance(widget, ttk.Widget) or not widget.instate(["disabled"]))
    except (AttributeError, tk.TclError):
        return False


def reveal(widget):
    """Keep a focused field visible inside the generic scrolling dialogs."""
    # Native combobox/menu popups may report a raw Tcl widget path.
    if not isinstance(widget, tk.Misc):
        return
    parent = widget.master
    while parent is not None:
        if isinstance(parent, tk.Canvas) and getattr(parent, "_keyboard_body", None) is not None:
            bounds = parent.bbox("all")
            if bounds and parent.winfo_height() > 1:
                top = parent.canvasy(0)
                y = top + widget.winfo_rooty() - parent.winfo_rooty()
                bottom = y + widget.winfo_height()
                height = parent.winfo_height()
                target = y - 8 if y < top else bottom - height + 8 if bottom > top + height else None
                if target is not None:
                    parent.yview_moveto(max(0, target) / max(1, bounds[3]))
        parent = parent.master


def focus(widget):
    if available(widget):
        widget.focus_set()
        if isinstance(widget, ttk.Entry) and not isinstance(widget, ttk.Combobox):
            widget.selection_range(0, "end")
        reveal(widget)
    return "break"


def invoke(widget):
    if available(widget):
        widget.invoke()
    return "break"


def unbind_callback(widget, sequence, identifier):
    """Remove one Python handler without erasing other handlers on older Tkinter.

    Python 3.10's ``unbind(sequence, identifier)`` clears the whole sequence.
    Every Tkinter-registered handler is its own Tcl script line, identified by
    its unique command name. Preserve the remaining scripts and their commands.
    """
    if not identifier:
        return
    script = widget.bind(sequence)
    prefix = 'if {"[' + identifier + ' '
    remaining = "\n".join(line for line in script.splitlines() if not line.startswith(prefix))
    if script.endswith("\n") and remaining:
        remaining += "\n"
    widget.tk.call("bind", widget._w, sequence, remaining)
    if widget.tk.call("info", "commands", identifier):
        widget.deletecommand(identifier)


def descendants(parent):
    for widget in parent.winfo_children():
        if isinstance(widget, tk.Toplevel):
            continue
        yield widget
        yield from descendants(widget)


def initial_focus(window):
    explicit = getattr(window, "_initial_focus", None)
    if explicit is not None and available(explicit):
        return explicit
    widgets = [widget for widget in descendants(window) if available(widget) and widget.winfo_viewable()]
    for widget in widgets:
        if isinstance(widget, ttk.Combobox) or isinstance(widget, ttk.Entry) and not widget.instate(["readonly"]):
            return widget
    for widget in widgets:
        if isinstance(widget, ttk.Treeview):
            return widget
    for title in ("Close", "Back to entry", "Cancel"):
        for widget in widgets:
            if isinstance(widget, ttk.Button) and widget.cget("text") == title:
                return widget
    return next((widget for widget in widgets if isinstance(widget, (ttk.Button, ttk.Checkbutton))), window)


def install_window(window, owner):
    """Wire new controls as they map, without changing global class bindings."""
    if getattr(window, "_keyboard_owner", None) is owner:
        return
    window._keyboard_owner = owner
    bindings = []

    def mapped(event):
        widget = event.widget
        if not isinstance(widget, tk.Widget) or widget.winfo_toplevel() is not window:
            return
        if isinstance(widget, ttk.Button):
            for sequence in ("<Return>", "<KP_Enter>"):
                if not widget.bind(sequence):
                    widget.bind(sequence, lambda event, control=widget: invoke(control))
        elif isinstance(widget, (ttk.Entry, ttk.Checkbutton)):
            for sequence in ("<Return>", "<KP_Enter>"):
                if not widget.bind(sequence):
                    widget.bind(sequence, lambda event, control=widget: focus(control.tk_focusNext()) if available(control) else "break")
        elif isinstance(widget, ttk.Notebook):
            if not getattr(widget, "_keyboard_traversal", False):
                widget.enable_traversal()
                widget._keyboard_traversal = True
        if isinstance(widget, ttk.Treeview) and not widget.bind("<FocusIn>"):
            def select_first(event):
                if not widget.selection() and widget.get_children():
                    first = widget.get_children()[0]
                    widget.selection_set(first)
                    widget.focus(first)
            widget.bind("<FocusIn>", select_first)

    def escape(event):
        grab = window.grab_current()
        if grab is not None and grab is not window:
            return "break"
        callback = window.protocol("WM_DELETE_WINDOW")
        if callback:
            window.tk.eval(callback)
        else:
            window.destroy()
        return "break"

    bindings.append(("<Map>", window.bind("<Map>", mapped, add=True)))
    bindings.append(("<FocusIn>", window.bind("<FocusIn>", lambda event: reveal(event.widget), add=True)))
    if isinstance(window, tk.Toplevel):
        bindings.append(("<Escape>", window.bind("<Escape>", escape, add=True)))
    def destroyed(event):
        if event.widget is window:
            owner._keyboard_windows = [(other, ids) for other, ids in getattr(owner, "_keyboard_windows", [])
                                       if other is not window]
            if getattr(window, "_keyboard_owner", None) is owner:
                del window._keyboard_owner
    bindings.append(("<Destroy>", window.bind("<Destroy>", destroyed, add=True)))
    owner._keyboard_windows = getattr(owner, "_keyboard_windows", []) + [(window, bindings)]


def uninstall(owner):
    for window, bindings in getattr(owner, "_keyboard_windows", []):
        if not available(window):
            continue
        for sequence, identifier in bindings:
            if identifier:
                unbind_callback(window, sequence, identifier)
        if getattr(window, "_keyboard_owner", None) is owner:
            del window._keyboard_owner
    owner._keyboard_windows = []
