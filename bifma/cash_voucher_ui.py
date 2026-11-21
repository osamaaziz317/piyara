"""Inline cash-voucher entry matching the original application's form layout.

This component owns presentation only. Its caller performs all authorized reads,
draft validation, posting and printable exports through the existing services.
"""

from __future__ import annotations

from decimal import Context, Decimal, localcontext
import tkinter as tk
from tkinter import messagebox, ttk

from bifma.calculations import as_decimal
from bifma.tk_lifecycle import owned_variable


_TOTAL_CONTEXT = Context(prec=100)
_PAYMENT_COLOR = "#184E83"
_RECEIPT_COLOR = "#942A3B"
_COLUMNS = ((0, 0, 104), (1, 3, 175), (2, 0, 80),
            (3, 4, 190), (4, 1, 108), (5, 0, 40))


class _CashRow:
    """One inline line; display names never substitute for account identifiers."""

    def __init__(self, voucher, initial):
        self.voucher = voucher
        self.frame = ttk.Frame(voucher.line_body)
        self.inputs, self.widgets = {}, {}
        self._changing_account = False
        self.number = 0
        for column, weight, minimum in _COLUMNS:
            self.frame.columnconfigure(column, weight=weight, minsize=minimum)
        account = voucher.choice_factory(self.frame, voucher.accounts,
                                         selected=initial.get("account_id"), width=1, enter_select=True)
        account._search_title = "Account"
        selected_id = account.get()
        account.widget.configure(values=["— Select account —"] +
                                 [row.get("name", "") for row in account.records])
        account.widget.current(account.ids.index(selected_id))
        self.inputs["account_id"] = account
        self.widgets["account_id"] = account.widget
        selected = voucher.accounts_by_id.get(account.get(), {})
        code = owned_variable(self.frame, value=selected.get("code", ""))
        self.inputs["code"] = code
        self.widgets["code"] = ttk.Entry(self.frame, textvariable=code, width=1)
        self.widgets["code"].grid(row=0, column=0, sticky="ew", padx=(0, 5), pady=3)
        account.widget.grid(row=0, column=1, sticky="ew", padx=(0, 5), pady=3)
        self.find_button = ttk.Button(self.frame, text="Find…", width=6,
                                      command=lambda: account.search("Account"))
        self.find_button.grid(row=0, column=2, sticky="ew", padx=(0, 8), pady=3)
        for key, column in (("narration", 3), ("amount", 4)):
            control = owned_variable(self.frame, value=str(initial.get(key) or ""))
            widget = ttk.Entry(self.frame, textvariable=control, width=1,
                               justify="right" if key == "amount" else "left")
            self.inputs[key], self.widgets[key] = control, widget
            widget.grid(row=0, column=column, sticky="ew", padx=(0, 5), pady=3)
        self.remove_button = ttk.Button(self.frame, text="×", width=2,
                                        command=lambda: voucher.remove_line(self))
        self.remove_button.grid(row=0, column=5, sticky="ew", pady=3)
        code.trace_add("write", self._code_changed)
        account.widget.bind("<<ComboboxSelected>>", self._account_changed, add=True)
        self.inputs["amount"].trace_add("write", lambda *_: voucher.update_total())
        for sequence in ("<Return>", "<KP_Enter>"):
            self.widgets["code"].bind(sequence, self._code_enter)
            account.widget.bind(sequence, lambda event: self.focus("narration") if account.get() is not None else self._find())
            self.widgets["narration"].bind(sequence, lambda event: self.focus("amount"))
            self.widgets["amount"].bind(sequence, self._next_line)
        self.widgets["code"].bind("<F4>", lambda event: self._find())
        account.widget.bind("<F4>", lambda event: self._find())
        for widget in (self.frame, self.widgets["code"], self.widgets["narration"], self.widgets["amount"]):
            voucher.bind_line_scroll(widget)
        for widget in self.widgets.values():
            widget.bind("<FocusIn>", lambda event: voucher.reveal_line(self), add=True)
            voucher.bind_save_shortcut(widget)
            widget.bind("<Control-Delete>", lambda event: (self.remove_button.invoke(), "break")[-1])

    def _find(self):
        if self.widgets["account_id"].instate(["disabled"]):
            return "break"
        self.inputs["account_id"].search("Account", initial_text=self.inputs["code"].get()
                                          if self.inputs["account_id"].get() is None else "")
        return "break"

    def _code_enter(self, event=None):
        if self.widgets["code"].instate(["disabled"]):
            return "break"
        if self.is_blank() and any(not row.is_blank() for row in self.voucher.rows if row is not self):
            return self.voucher.save_from_keyboard()
        if self.inputs["account_id"].get() is None:
            return self._find()
        return self.focus("narration")

    def focus(self, key):
        widget = self.widgets[key]
        widget.focus_set()
        if isinstance(widget, ttk.Entry) and not isinstance(widget, ttk.Combobox):
            widget.selection_range(0, "end")
        self.voucher.reveal_line(self)
        return "break"

    def _code_changed(self, *_):
        if self._changing_account:
            return
        text = self.inputs["code"].get().strip().casefold()
        matches = self.voucher.accounts_by_code.get(text, [])
        account = self.inputs["account_id"]
        identifier = matches[0]["id"] if len(matches) == 1 else None
        account.widget.current(account.ids.index(identifier))

    def _account_changed(self, event=None):
        self._changing_account = True
        try:
            row = self.voucher.accounts_by_id.get(self.inputs["account_id"].get(), {})
            self.inputs["code"].set(row.get("code", ""))
        finally:
            self._changing_account = False
        if row:
            self.focus("narration")

    def _next_line(self, event=None):
        if self.widgets["amount"].instate(["disabled"]):
            return "break"
        if not self.is_blank():
            try:
                self.value()
            except ValueError as error:
                messagebox.showerror("Check voucher", str(error), parent=self.voucher.winfo_toplevel())
                return "break"
        index = self.voucher.rows.index(self)
        if index + 1 == len(self.voucher.rows):
            self.voucher.add_line(focus=True)
        else:
            following = self.voucher.rows[index + 1]
            following.widgets["code"].focus_set()
            self.voucher.reveal_line(following)
        return "break"

    def is_blank(self):
        return (self.inputs["account_id"].get() is None
                and not any(self.inputs[key].get().strip() for key in ("code", "amount", "narration")))

    def value(self):
        account = self.inputs["account_id"].get()
        code = self.inputs["code"].get().strip()
        amount = self.inputs["amount"].get().strip()
        narration = self.inputs["narration"].get()
        if account is None and not code and not amount and not narration.strip():
            return None
        if account is None:
            self.widgets["code"].focus_set()
            raise ValueError(f"Line {self.number}: select an account or enter its complete account code.")
        try:
            if as_decimal(amount) <= 0:
                raise ValueError("Enter an amount greater than zero.")
        except ValueError as error:
            self.focus("amount")
            raise ValueError(f"Line {self.number}: {error}") from None
        return {"account_id": account, "amount": amount, "narration": narration}


class CashVoucherForm(ttk.Frame):
    """A cash payment/receipt header, inline lines and original-style actions.

    ``choice_factory`` is the application's shared Choice class, injected to
    reuse its live Find dialog without introducing a circular module import.
    Save and Print callbacks receive a service-shaped payload. New/Exit/F8
    callbacks own navigation; they can leave this widget alive while browsing.
    """

    def __init__(self, parent, *, kind, accounts, company, values,
                 choice_factory, on_save, on_new, on_exit, on_print,
                 on_records, last_number="", blank_rows=5):
        if kind not in {"cash_payment", "cash_receipt"}:
            raise ValueError("CashVoucherForm requires a cash payment or receipt kind")
        super().__init__(parent, padding=(14, 8))
        self.kind, self.choice_factory = kind, choice_factory
        self.accounts = list(accounts)
        self.accounts_by_id = {row["id"]: row for row in self.accounts}
        self.accounts_by_code = {}
        for account in self.accounts:
            self.accounts_by_code.setdefault(str(account.get("code", "")).casefold(), []).append(account)
        self.rows, self.inputs, self.widgets = [], {}, {}
        self.on_save, self.on_print = on_save, on_print
        title = "Cash Payment Voucher" if kind == "cash_payment" else "Cash Receipt Voucher"
        color = _PAYMENT_COLOR if kind == "cash_payment" else _RECEIPT_COLOR
        ttk.Label(self, text=title, font=("TkDefaultFont", 25, "bold"),
                  foreground=color, anchor="center").pack(fill="x", pady=(0, 13))

        header = ttk.Frame(self, padding=(12, 10), relief="groove", borderwidth=1)
        header.pack(fill="x")
        for col in (1, 3, 5):
            header.columnconfigure(col, weight=1)
        for key, text, col, width in (("document_number", "Voucher #", 0, 14),
                                      ("business_date", "Date (YYYY-MM-DD)", 2, 14)):
            ttk.Label(header, text=text, foreground=color).grid(row=0, column=col, sticky="w", padx=(0, 7), pady=3)
            control = owned_variable(self, value=str(values.get(key) or ""))
            widget = ttk.Entry(header, textvariable=control, width=width)
            widget.grid(row=0, column=col + 1, sticky="ew", padx=(0, 16), pady=3)
            self.inputs[key], self.widgets[key] = control, widget
        ttk.Label(header, text="Last voucher #", foreground=color).grid(row=0, column=4, sticky="w", padx=(0, 7), pady=3)
        self.last_number = owned_variable(self, value=str(last_number or "—"))
        ttk.Entry(header, textvariable=self.last_number, state="readonly", width=12).grid(row=0, column=5, sticky="ew", pady=3)

        ttk.Label(header, text="Company", foreground=color).grid(row=1, column=0, sticky="w", padx=(0, 7), pady=(8, 3))
        company_box = ttk.Frame(header)
        company_box.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(0, 16), pady=(8, 3))
        company_code = owned_variable(self, value=str(company.get("code") or ""))
        company_name = owned_variable(self, value=str(company.get("name") or ""))
        ttk.Entry(company_box, textvariable=company_code, state="readonly", width=7).pack(side="left", padx=(0, 5))
        ttk.Entry(company_box, textvariable=company_name, state="readonly").pack(side="left", fill="x", expand=True)
        cash_id = values.get("account_id", company.get("cash_account_id"))
        cash = self.accounts_by_id.get(cash_id, {})
        cash_text = (str(cash.get("code", "")) + " · " + str(cash.get("name", ""))).strip(" ·")
        ttk.Label(header, text="Cash account", foreground=color).grid(row=1, column=4, sticky="w", padx=(0, 7), pady=(8, 3))
        ttk.Label(header, text=cash_text or "Review company setup", wraplength=190).grid(row=1, column=5, sticky="w", pady=(8, 3))
        # These are established posting settings, preserved when editing a draft.
        # Routine voucher entry does not need to expose accounting implementation.
        for key, value in (("account_id", cash_id or ""),
                            ("rounding_policy", values.get("rounding_policy", "legacy_whole")),
                            ("posting_places", values.get("posting_places", 0))):
            self.inputs[key] = owned_variable(self, value=value)
        note_row = ttk.Frame(self)
        note_row.pack(fill="x", pady=(9, 12))
        ttk.Label(note_row, text="Notes", foreground=color).pack(side="left", padx=(0, 12))
        self.inputs["notes"] = owned_variable(self, value=str(values.get("notes") or ""))
        self.widgets["notes"] = ttk.Entry(note_row, textvariable=self.inputs["notes"])
        self.widgets["notes"].pack(side="left", fill="x", expand=True)
        for sequence in ("<Return>", "<KP_Enter>"):
            self.widgets["document_number"].bind(sequence, lambda event: self.focus_date())
            self.widgets["business_date"].bind(sequence, lambda event: self.find_first_account())
            self.widgets["notes"].bind(sequence, lambda event: self.find_first_account())

        # Reserve the action/total area before the expanding entry grid so every
        # action remains reachable in the minimum supported application window.
        footer = ttk.Frame(self)
        footer.pack(side="bottom", fill="x", pady=(10, 0))
        totals = ttk.Frame(footer)
        totals.pack(fill="x", pady=(0, 10))
        ttk.Button(totals, text="Add line", command=lambda: self.add_line(focus=True)).pack(side="left")
        ttk.Label(totals, text="Enter: next / blank code saves · Ctrl+S: Save · F4: Find", style="Muted.TLabel").pack(side="left", padx=12)
        self.total = owned_variable(self, value="0.00")
        self.total_value = Decimal(0)
        ttk.Label(totals, textvariable=self.total, font=("TkDefaultFont", 14, "bold"), foreground=color).pack(side="right")
        ttk.Label(totals, text="Total", font=("TkDefaultFont", 12, "bold"), foreground=color).pack(side="right", padx=18)
        actions = ttk.Frame(footer)
        actions.pack(fill="x")
        ttk.Button(actions, text="Saved records (F8)", command=on_records).pack(side="left")
        self.buttons = {}
        for text, action in (("Exit", on_exit), ("New", on_new),
                             ("Save", lambda: self._submit(self.on_save)),
                             ("Print", lambda: self._submit(self.on_print))):
            button = ttk.Button(actions, text=text, command=action,
                                style="Accent.TButton" if text == "Save" else "TButton", width=9)
            button.pack(side="right", padx=(6, 0))
            self.buttons[text] = button
            self.bind_save_shortcut(button)
        for widget in self.widgets.values():
            self.bind_save_shortcut(widget)

        headings = ttk.Frame(self)
        headings.pack(fill="x", padx=(0, 17))
        for col, weight, minimum in _COLUMNS:
            headings.columnconfigure(col, weight=weight, minsize=minimum)
        for text, column in (("Acct. Code", 0), ("Account Title", 1),
                             ("Narration", 3),
                             ("Payment" if kind == "cash_payment" else "Receipt", 4)):
            ttk.Label(headings, text=text, foreground=color,
                      font=("TkDefaultFont", 10, "bold")).grid(row=0, column=column, sticky="w", pady=(0, 6))
        ttk.Separator(self).pack(fill="x")
        line_container = ttk.Frame(self)
        line_container.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(line_container, highlightthickness=0, borderwidth=0,
                                background=ttk.Style(self).lookup("TFrame", "background") or "#F2F5F9", height=200)
        scrollbar = ttk.Scrollbar(line_container, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.line_body = ttk.Frame(self.canvas)
        self.line_window = self.canvas.create_window((0, 0), window=self.line_body, anchor="nw")
        self.line_body.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.line_window, width=event.width))
        self.bind_line_scroll(self.canvas)
        self.bind_line_scroll(self.line_body)
        for line in values.get("lines", []):
            self.add_line(line)
        while len(self.rows) < max(1, blank_rows):
            self.add_line()
        self.update_total()

    def add_line(self, values=None, *, focus=False):
        row = _CashRow(self, values or {})
        row.frame.pack(fill="x")
        self.rows.append(row)
        row.number = len(self.rows)
        self.update_total()
        if focus:
            self.update_idletasks()
            self.canvas.yview_moveto(1)
            row.widgets["code"].focus_set()
        return row

    def remove_line(self, row):
        removed_index = self.rows.index(row)
        self.rows.remove(row)
        row.frame.destroy()
        for index, item in enumerate(self.rows, 1):
            item.number = index
        if not self.rows:
            self.add_line()
        self.update_total()
        self.rows[min(removed_index, len(self.rows) - 1)].focus("code")

    def save_from_keyboard(self, event=None):
        self.buttons["Save"].invoke()
        return "break"

    def bind_save_shortcut(self, widget):
        for sequence in ("<Control-s>", "<Control-S>"):
            widget.bind(sequence, self.save_from_keyboard)

    def focus_first(self):
        self.widgets["document_number"].focus_set()
        self.widgets["document_number"].selection_range(0, "end")

    def focus_date(self):
        self.widgets["business_date"].focus_set()
        self.widgets["business_date"].selection_range(0, "end")
        return "break"

    def find_first_account(self):
        self.reveal_line(self.rows[0])
        return self.rows[0]._find()

    def reveal_line(self, row):
        """Keep keyboard entry visible while tabbing through a long voucher."""
        self.update_idletasks()
        height = self.line_body.winfo_height()
        if height <= self.canvas.winfo_height():
            return
        top = row.frame.winfo_y()
        bottom = top + row.frame.winfo_height()
        visible_top = self.canvas.canvasy(0)
        visible_bottom = visible_top + self.canvas.winfo_height()
        if top < visible_top:
            self.canvas.yview_moveto(top / height)
        elif bottom > visible_bottom:
            self.canvas.yview_moveto((bottom - self.canvas.winfo_height()) / height)

    def bind_line_scroll(self, widget):
        def scroll(event):
            steps = (-1 if event.num == 4 else 1) if event.num in (4, 5) else (-1 if event.delta > 0 else 1)
            self.canvas.yview_scroll(steps, "units")
            return "break"
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(event, scroll, add=True)

    def update_total(self):
        try:
            with localcontext(_TOTAL_CONTEXT):
                total = sum((as_decimal(row.inputs["amount"].get()) for row in self.rows
                             if row.inputs["amount"].get().strip()), Decimal(0))
            places = max(2, -total.as_tuple().exponent)
            text = format(total, f",.{places}f")
        except ValueError:
            total, text = None, "Check amount"
        self.total_value = total
        self.total.set(text)

    def values(self):
        result = {key: control.get() for key, control in self.inputs.items()}
        result["posting_places"] = int(result["posting_places"])
        result["account_id"] = result["account_id"] or None
        return result

    def payload(self):
        result = self.values()
        result["lines"] = [value for row in self.rows if (value := row.value()) is not None]
        return result

    def _submit(self, callback):
        try:
            payload = self.payload()
        except ValueError as error:
            messagebox.showerror("Check voucher", str(error), parent=self.winfo_toplevel())
            return
        callback(payload)
