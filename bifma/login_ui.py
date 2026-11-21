"""Photo-based login card with code-drawn artwork and native editable fields."""

from collections import Counter
import tkinter as tk
from tkinter import ttk

from bifma.tk_lifecycle import owned_variable


class LoginForm(ttk.Frame):
    def __init__(self, parent, *, initial=False, submit, cancel):
        super().__init__(parent, style="LoginCard.TFrame")
        self.initial = initial
        self.inputs, self.widgets, self.companies = {}, {}, []
        self.frame = self
        style = ttk.Style(self)
        style.configure("LoginCard.TFrame", background="#efefec")
        style.configure("Login.TLabel", background="#efefec", foreground="#263540",
                        font=("Segoe UI", 10, "bold"))
        style.configure("Login.TEntry", padding=(5, 4), font=("Segoe UI", 11))
        style.configure("Login.TCombobox", padding=(5, 4), font=("Segoe UI", 11))
        self.canvas = tk.Canvas(self, width=700, height=460 if initial else 400,
                                background="#efefec", highlightthickness=1,
                                highlightbackground="#b8bdbc")
        self.canvas.pack(fill="both", expand=True)
        self._artwork()
        fields = ttk.Frame(self.canvas, style="LoginCard.TFrame")
        self.canvas.create_window(258, 182, window=fields, anchor="nw", width=414)
        definitions = [("username", "User"), ("display_name", "User Name"),
                       ("password", "Password")]
        definitions += [("confirm", "Confirm password")] if initial else [("company", "Company")]
        for key, title in definitions:
            row = ttk.Frame(fields, style="LoginCard.TFrame")
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=title, style="Login.TLabel", width=16 if initial else 11,
                      anchor="e").pack(side="left", padx=(0, 8))
            value = owned_variable(row, value="")
            if key == "company":
                widget = ttk.Combobox(row, textvariable=value, state="disabled", style="Login.TCombobox")
            else:
                widget = ttk.Entry(row, textvariable=value, style="Login.TEntry",
                                   show="•" if key in {"password", "confirm"} else "")
                if key == "display_name" and not initial:
                    widget.state(["readonly"])
            widget.pack(side="left", fill="x", expand=True)
            self.inputs[key], self.widgets[key] = value, widget
        actions = ttk.Frame(fields, style="LoginCard.TFrame")
        actions.pack(fill="x", pady=(13, 0))
        self.submit_button = ttk.Button(actions, text="Create administrator" if initial else "OK", command=submit)
        self.submit_button.pack(side="left", padx=(0 if initial else 99, 0))
        self.cancel_button = ttk.Button(actions, text="Cancel", command=cancel)
        self.cancel_button.pack(side="right")
        for button in (self.submit_button, self.cancel_button):
            for sequence in ("<Return>", "<KP_Enter>"):
                button.bind(sequence, lambda event, control=button: (control.invoke(), "break")[-1])
        if initial:
            self.canvas.create_text(350, 418, text="Create your personal login once. Enter your full name and password.",
                                    fill="#53616a", font=("Segoe UI", 10), width=620)

    def _artwork(self):
        c = self.canvas
        # Native canvas artwork scales cleanly without bundled image dependencies.
        for x, y, letter in ((-30, 75, "F"), (80, 40, "M"), (-40, 305, "C"), (89, 330, "S")):
            points = (x, y + 25, x + 87, y, x + 125, y + 93, x + 31, y + 122)
            shadow = [value + (4 if i % 2 else 2) for i, value in enumerate(points)]
            c.create_polygon(*shadow, fill="#c1c5c3", outline="")
            c.create_polygon(*points, fill="#fafaf8", outline="#d5d8d4", width=2)
            c.create_text(x + 62, y + 59, text=letter, fill="#d8dcda", font=("Segoe UI", 33))
        c.create_rectangle(151, 22, 672, 56, fill="#1e2b34", outline="#adb8ba")
        c.create_text(411, 39, text="Welcome to Financial Management System", fill="white",
                      font=("Segoe UI", 12, "bold"))
        c.create_text(458, 111, text="BIFMA", fill="#c82432", font=("Segoe UI", 49, "bold"))
        c.create_text(458, 155, text="T R A D E R S", fill="#374551", font=("Segoe UI", 15, "bold"))
        c.create_polygon(38, 191, 167, 154, 219, 281, 81, 325, fill="#a7192b", outline="#991726", width=2)
        c.create_polygon(33, 182, 164, 145, 211, 269, 76, 313, fill="#d12438", outline="#b81d2c", width=2)
        # A shopping cart on the red key, matching the reference's visual motif.
        c.create_line(81, 203, 92, 203, 105, 255, 153, 255, fill="white", width=4)
        c.create_line(96, 215, 161, 205, 153, 243, 102, 248, fill="white", width=3)
        for x in (114, 134, 151):
            c.create_line(x, 214, x - 2, 244, fill="white", width=2)
        c.create_line(100, 230, 158, 222, fill="white", width=2)
        for x in (113, 149):
            c.create_oval(x - 5, 265, x + 5, 275, fill="white", outline="white")

    def values(self):
        return {key: value.get() for key, value in self.inputs.items()}

    def focus(self, key):
        widget = self.widgets[key]
        widget.focus_set()
        if isinstance(widget, ttk.Entry) and not isinstance(widget, ttk.Combobox):
            widget.selection_range(0, "end")
        return "break"

    def clear_profile(self):
        self.inputs["display_name"].set("")
        self.inputs["password"].set("")
        self.companies = []
        self.widgets["company"].configure(values=(), state="disabled")
        self.inputs["company"].set("")

    def set_profile(self, profile, preferred_company=None):
        self.inputs["display_name"].set(profile["display_name"] if profile else "")
        self.companies = list(profile["companies"]) if profile else []
        widget = self.widgets["company"]
        counts = Counter(name for _, name in self.companies)
        names = [name if counts[name] == 1 else f"{name} ({index + 1})"
                 for index, (_, name) in enumerate(self.companies)]
        # Duplicate names need distinct displayed values for Tcl's current index.
        widget.configure(values=names,
                         state="readonly" if self.companies else "disabled")
        if self.companies:
            selected = next((index for index, (identifier, _) in enumerate(self.companies)
                             if identifier == preferred_company), None)
            if selected is None:
                selected = next((index for index, (_, name) in enumerate(self.companies)
                                 if name.strip().casefold() == "bifma traders"), 0)
            widget.current(selected)
        else:
            self.inputs["company"].set("")

    def company_id(self):
        if self.initial:
            return None
        index = self.widgets["company"].current()
        return self.companies[index][0] if 0 <= index < len(self.companies) else None
