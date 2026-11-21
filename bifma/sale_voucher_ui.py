"""Inline Sale Voucher entry; authorized persistence remains with its caller."""

from __future__ import annotations

from decimal import Context, Decimal, localcontext
import tkinter as tk
from tkinter import messagebox, ttk

from bifma.calculations import (DiscountKind, InvoiceLine, as_decimal,
                                calculate_invoice, canonical_decimal,
                                convert_quantity, oracle_round)
from bifma.tk_lifecycle import owned_variable


_BLUE = "#184E83"
_STOCK_COLOR = "#8B2635"
_CONTEXT = Context(prec=100)
# Fixed minima keep headings aligned with the independently scrolling rows.
_COLUMNS = (("sr", "Sr #", 36, 0), ("code", "Icode", 62, 0),
            ("product_id", "Item Description", 178, 4), ("find", "", 61, 0),
            ("unit_id", "Unit", 62, 0), ("entered_quantity", "Unit Qty", 68, 0),
            ("factor", "×", 42, 0), ("quantity", "Qty", 63, 1),
            ("rate", "Rate", 73, 1), ("discount_percent", "Dis %", 52, 0),
            ("discount", "Discount", 74, 1), ("value", "Value", 88, 1),
            ("remove", "", 34, 0))
_MIN_TABLE_WIDTH = sum(column[2] for column in _COLUMNS)


def _text(value):
    return "" if value is None else str(value)


def _display(number):
    number = as_decimal(number)
    return format(number, f",.{max(0, -number.as_tuple().exponent)}f")


def _choices(choice, records, selected=None, *, optional=True):
    """Change visible labels without ever deriving a foreign key from a name."""
    choice.records = list(records)
    choice.ids = ([None] if optional else []) + [row["id"] for row in choice.records]
    names = (["— Select —"] if optional else []) + [row.get("name", row.get("code", "")) for row in choice.records]
    choice.widget.configure(values=names)
    if selected in choice.ids:
        choice.widget.current(choice.ids.index(selected))
    elif choice.ids:
        choice.widget.current(0)
    else:
        choice.widget.set("")


class _SaleRow:
    def __init__(self, voucher, initial):
        self.voucher = voucher
        self.frame = ttk.Frame(voucher.line_body)
        self.inputs, self.widgets = {}, {}
        # Stock location is recorded internally, not selected during sale entry.
        # In particular, reopening a draft must keep each line's original ID.
        self.inputs["store_id"] = owned_variable(self.frame, value=initial.get("store_id", voucher.default_store) or "")
        self._changing = False
        self._product_id = None
        self.number = 0
        for column, (_, _, minimum, weight) in enumerate(_COLUMNS):
            self.frame.columnconfigure(column, minsize=minimum, weight=weight)
        self.serial = owned_variable(self.frame)
        ttk.Label(self.frame, textvariable=self.serial, anchor="center").grid(row=0, column=0, sticky="ew")
        for key in ("code", "entered_quantity", "factor", "quantity", "rate", "discount_percent", "discount", "value"):
            value = initial.get("entered_quantity", initial.get("quantity", "1")) if key == "entered_quantity" else initial.get(key, "0" if key == "discount_percent" else "")
            control = owned_variable(self.frame, value=_text(value))
            widget = ttk.Entry(self.frame, textvariable=control, width=1,
                               justify="left" if key == "code" else "right",
                               takefocus=key not in {"factor", "quantity", "discount", "value"},
                               state="readonly" if key in {"factor", "quantity", "discount", "value"} else "normal")
            self.inputs[key], self.widgets[key] = control, widget
            column = next(index for index, field in enumerate(_COLUMNS) if field[0] == key)
            widget.grid(row=0, column=column, sticky="ew", padx=(0, 4), pady=3)
        product = voucher.products_by_id.get(initial.get("product_id"), {})
        saved_units = [voucher.units_by_id[key]
                       for key in dict.fromkeys((product.get("small_unit_id"), product.get("large_unit_id")))
                       if key in voucher.units_by_id]
        for key, records, selected, optional, enter_select in (
                ("product_id", voucher.products, initial.get("product_id"), True, True),
                # Populate before applying the saved selection: an empty Choice
                # would discard a carton/large-unit ID before configuration.
                ("unit_id", saved_units, initial.get("unit_id"), False, True)):
            choice = voucher.choice_factory(self.frame, records, selected=selected,
                                             optional=optional, width=1, enter_select=enter_select)
            choice._search_title = {"product_id": "Item", "unit_id": "Unit"}[key]
            _choices(choice, records, selected, optional=optional)
            self.inputs[key], self.widgets[key] = choice, choice.widget
            column = next(index for index, field in enumerate(_COLUMNS) if field[0] == key)
            choice.widget.grid(row=0, column=column, sticky="ew", padx=(0, 4), pady=3)
        self.find_button = ttk.Button(self.frame, text="Find…", width=5, style="SaleLine.TButton", command=self.find_item)
        self.find_button.grid(row=0, column=3, sticky="ew", padx=(0, 4), pady=3)
        self.remove_button = ttk.Button(self.frame, text="×", width=2, style="SaleLine.TButton", command=lambda: voucher.remove_line(self))
        self.remove_button.grid(row=0, column=len(_COLUMNS) - 1, sticky="ew", pady=3)
        self._configure_product(reset=False)
        self.inputs["code"].trace_add("write", self._code_changed)
        for key in ("entered_quantity", "rate", "discount_percent"):
            self.inputs[key].trace_add("write", lambda *_: self.recalculate())
        self.widgets["product_id"].bind("<<ComboboxSelected>>", self._item_selected, add=True)
        self.widgets["unit_id"].bind("<<ComboboxSelected>>", lambda event: self.recalculate(), add=True)
        self.widgets["unit_id"].bind("<<ChoiceAccepted>>", lambda event: self.focus("entered_quantity"), add=True)
        self.widgets["code"].bind("<F4>", lambda event: self.find_item())
        for sequence in ("<Return>", "<KP_Enter>"):
            self.widgets["code"].bind(sequence, self._code_enter)
            self.widgets["product_id"].bind(sequence, lambda event: self.focus("unit_id") if self.inputs["product_id"].get() is not None else self.find_item())
            for key, following in (("unit_id", "entered_quantity"), ("entered_quantity", "rate"), ("rate", "discount_percent")):
                self.widgets[key].bind(sequence, lambda event, target=following: self.focus(target))
            # Computed amounts can receive focus through a click. They finish
            # this row too, instead of cycling back to its item selector.
            for key in ("discount_percent", "discount", "value"):
                self.widgets[key].bind(sequence, lambda event: voucher.next_item(self))
        for widget in self.widgets.values():
            widget.bind("<FocusIn>", lambda event: voucher.item_focus(self), add=True)
            voucher.bind_scroll(widget)
            voucher.bind_save_shortcut(widget)
            widget.bind("<Control-Delete>", lambda event: (self.remove_button.invoke(), "break")[-1])
        voucher.bind_scroll(self.frame)
        self.recalculate()

    def _configure_product(self, *, reset):
        identifier = self.inputs["product_id"].get()
        product = self.voucher.products_by_id.get(identifier)
        self._changing = True
        try:
            self._product_id = identifier
            if product is None:
                _choices(self.inputs["unit_id"], [], optional=False)
                self.inputs["factor"].set("")
                self.inputs["quantity"].set("")
                return
            self.inputs["code"].set(product.get("code", ""))
            units = [self.voucher.units_by_id[key] for key in dict.fromkeys((product.get("small_unit_id"), product.get("large_unit_id")))
                     if key in self.voucher.units_by_id]
            selected_unit = None if reset else self.inputs["unit_id"].get()
            if selected_unit not in {unit["id"] for unit in units}:
                selected_unit = product.get("small_unit_id")
            _choices(self.inputs["unit_id"], units, selected_unit, optional=False)
            if reset:
                self.inputs["entered_quantity"].set("1")
                self.inputs["discount_percent"].set("0")
                self.inputs["rate"].set(_text(product.get("sale_rate")) if product.get("sale_rate_known", 1) else "")
        finally:
            self._changing = False

    def _code_changed(self, *_):
        if self._changing:
            return
        term = self.inputs["code"].get().strip().casefold()
        matches = self.voucher.products_by_code.get(term, [])
        identifier = matches[0]["id"] if len(matches) == 1 else None
        choice = self.inputs["product_id"]
        choice.widget.current(choice.ids.index(identifier))
        if identifier != self._product_id:
            self._configure_product(reset=True)
        self.recalculate()
        self.voucher.item_focus(self)

    def _item_selected(self, event=None):
        if self.inputs["product_id"].get() != self._product_id:
            self._configure_product(reset=True)
        self.recalculate()
        self.focus("unit_id")

    def _code_enter(self, event=None):
        if self.widgets["code"].instate(["disabled"]):
            return "break"
        if self.is_blank() and any(not row.is_blank() for row in self.voucher.rows if row is not self):
            return self.voucher.focus_adjustment("discount")
        if self.inputs["product_id"].get() is None:
            return self.find_item()
        return self.focus("unit_id")

    def find_item(self):
        if self.widgets["product_id"].instate(["disabled"]):
            return "break"
        self.voucher.reveal_line(self, self.widgets["product_id"])
        self.inputs["product_id"].search("Item", initial_text=self.inputs["code"].get() if self.inputs["product_id"].get() is None else "")
        return "break"

    def focus(self, key):
        widget = self.widgets[key]
        # Newly appended rows must be laid out before Find captures the field
        # to restore on cancel. An unmapped entry cannot take focus yet.
        self.voucher.reveal_line(self, widget)
        widget.focus_set()
        if isinstance(widget, ttk.Entry) and not isinstance(widget, ttk.Combobox):
            widget.selection_range(0, "end")
        self.voucher.item_focus(self)
        return "break"

    def is_blank(self):
        return (self.inputs["product_id"].get() is None and not self.inputs["code"].get().strip()
                and self.inputs["entered_quantity"].get().strip() in ("", "1")
                and not self.inputs["rate"].get().strip()
                and self.inputs["discount_percent"].get().strip() in ("", "0"))

    def base_quantity(self):
        product = self.voucher.products_by_id.get(self.inputs["product_id"].get())
        if product is None:
            raise ValueError(f"Line {self.number}: choose an item or enter its complete code.")
        unit = self.inputs["unit_id"].get()
        if unit == product.get("small_unit_id") and unit is not None:
            factor = "1"
        elif unit == product.get("large_unit_id") and unit is not None:
            factor = product["unit_factor"]
        else:
            raise ValueError(f"Line {self.number}: choose an available item unit.")
        self.inputs["factor"].set(_text(factor))
        return convert_quantity(large=self.inputs["entered_quantity"].get(), small="0", factor=factor)

    def invoice_line(self):
        return InvoiceLine(self.base_quantity(), self.inputs["rate"].get(),
                           self.inputs["discount_percent"].get() or "0", discount_kind=DiscountKind.PERCENT)

    def recalculate(self):
        if self._changing:
            return
        if self.is_blank():
            for key in ("quantity", "discount", "value"):
                self.inputs[key].set("")
        else:
            try:
                quantity = self.base_quantity()
                self.inputs["quantity"].set(canonical_decimal(quantity))
            except ValueError:
                self.inputs["quantity"].set("—")
            try:
                amount = self.invoice_line().calculate()
                self.inputs["discount"].set(_display(amount.discount))
                self.inputs["value"].set(_display(amount.net))
            except ValueError:
                self.inputs["discount"].set("—")
                self.inputs["value"].set("—")
        self.voucher.update_totals()

    def value(self):
        if self.is_blank():
            return None
        line = self.invoice_line()
        store = self.inputs["store_id"].get()
        if not store:
            raise ValueError("This company's stock posting setup needs administrator review before saving a sale.")
        return {"product_id": self.inputs["product_id"].get(), "store_id": store,
                "quantity": canonical_decimal(line.quantity), "rate": self.inputs["rate"].get(),
                "discount_percent": self.inputs["discount_percent"].get() or "0",
                "unit_id": self.inputs["unit_id"].get(),
                "entered_quantity": self.inputs["entered_quantity"].get()}


class SaleVoucherForm(ttk.Frame):
    """Keyboard-first sale entry with exact previews and service-shaped payloads."""

    def __init__(self, parent, *, choices, company, values, choice_factory,
                 on_save, on_new, on_exit, on_print, on_records,
                 last_number="", blank_rows=5, on_item_focus=None, product_issues=None):
        super().__init__(parent, padding=(10, 8))
        ttk.Style(self).configure("SaleLine.TButton", padding=(3, 3))
        self.choice_factory = choice_factory
        self.products, self.stores = list(choices.get("products", [])), list(choices.get("stores", []))
        self.accounts = list(choices.get("accounts", []))
        self.products_by_id = {row["id"]: row for row in self.products}
        self.accounts_by_id = {row["id"]: row for row in self.accounts}
        self.units_by_id = {row["id"]: row for row in choices.get("units", [])}
        self.products_by_code, self.accounts_by_code = {}, {}
        for records, index in ((self.products, self.products_by_code), (self.accounts, self.accounts_by_code)):
            for row in records:
                index.setdefault(str(row.get("code", "")).casefold(), []).append(row)
        original_stores = {line.get("store_id") for line in values.get("lines", [])}
        active_stores = {store["id"] for store in self.stores}
        if len(original_stores) == 1 and original_stores <= active_stores:
            self.default_store = next(iter(original_stores))
        elif "default_sale_store_id" in choices:
            assigned = choices["default_sale_store_id"]
            self.default_store = assigned if assigned in active_stores else None
        else:
            # Component callers without a service resolution can only use an
            # unambiguous sole location, never an arbitrary sorted first row.
            self.default_store = self.stores[0]["id"] if len(self.stores) == 1 else None
        self.inputs, self.widgets, self.rows, self.buttons = {}, {}, [], {}
        self.on_save, self.on_print, self.on_item_focus = on_save, on_print, on_item_focus
        self.product_issues, self._changing_buyer = product_issues or {}, False
        self._active_row = None
        self._after_jobs = set()
        self.bind("<Destroy>", self._destroyed, add=True)
        heading = ttk.Frame(self)
        heading.pack(fill="x", pady=(0, 10))
        ttk.Label(heading, text="Sale Voucher", font=("TkDefaultFont", 25, "bold"), foreground=_BLUE).pack(side="left")
        self.stock = owned_variable(self, value="—")
        self.stock_caption = owned_variable(self, value="Stock")
        stock_display = ttk.Frame(heading)
        stock_display.pack(side="right", padx=(12, 0))
        ttk.Label(stock_display, textvariable=self.stock_caption, foreground=_STOCK_COLOR,
                  font=("TkDefaultFont", 12, "bold")).pack()
        ttk.Label(stock_display, textvariable=self.stock, font=("TkDefaultFont", 16, "bold"),
                  foreground=_STOCK_COLOR).pack()
        actions = ttk.Frame(heading)
        actions.pack(side="right", padx=(12, 0))
        for title, callback in (("New", on_new), ("Bill", lambda: self._submit(on_print)), ("Exit", on_exit)):
            button = ttk.Button(actions, text=title, command=callback, width=6)
            button.pack(side="left", padx=(4, 0))
            self.buttons[title] = button
            self.bind_save_shortcut(button)
        # Existing callers use Print to enable/disable the print action. Bill is
        # its visible name in the original Sale Voucher; both refer to one button.
        self.buttons["Print"] = self.buttons["Bill"]
        header = ttk.Frame(self, padding=(10, 8), relief="groove", borderwidth=1)
        header.pack(fill="x")
        for column in (1, 3, 5):
            header.columnconfigure(column, weight=1)
        for key, title, row, column, default in (
                ("document_number", "Sale #", 0, 0, ""), ("business_date", "Date (YYYY-MM-DD)", 0, 4, ""),
                ("bill_number", "Bill #", 1, 0, "")):
            ttk.Label(header, text=title, foreground=_BLUE).grid(row=row, column=column, sticky="w", padx=(0, 8), pady=3)
            control = owned_variable(self, value=_text(values.get(key, default)))
            widget = ttk.Entry(header, textvariable=control, width=15)
            widget.grid(row=row, column=column + 1, sticky="ew", padx=(0, 12), pady=3)
            self.inputs[key], self.widgets[key] = control, widget
        ttk.Label(header, text=_text(company.get("name")), foreground=_BLUE,
                  font=("TkDefaultFont", 11, "bold")).grid(row=0, column=2, columnspan=2, sticky="ew", padx=8)
        self.last_number = owned_variable(self, value=_text(last_number) or "—")
        ttk.Label(header, text="Last sale #", foreground=_BLUE).grid(row=1, column=4, sticky="w", padx=(0, 8))
        ttk.Label(header, textvariable=self.last_number).grid(row=1, column=5, sticky="w")
        buyer = ttk.Frame(header)
        buyer.grid(row=2, column=1, columnspan=5, sticky="ew", pady=3)
        ttk.Label(header, text="Buyer", foreground=_BLUE).grid(row=2, column=0, sticky="w", padx=(0, 8))
        account = self.choice_factory(buyer, self.accounts, selected=values.get("account_id"), enter_select=True)
        account._search_title = "Buyer"
        _choices(account, self.accounts, values.get("account_id"))
        selected = self.accounts_by_id.get(account.get(), {})
        self.inputs["buyer_code"] = owned_variable(self, value=_text(selected.get("code")))
        self.widgets["buyer_code"] = ttk.Entry(buyer, textvariable=self.inputs["buyer_code"], width=14)
        self.widgets["buyer_code"].pack(side="left", padx=(0, 5))
        account.widget.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.inputs["account_id"], self.widgets["account_id"] = account, account.widget
        ttk.Button(buyer, text="Find…", command=lambda: account.search("Buyer")).pack(side="right")
        ttk.Label(header, text="Remarks", foreground=_BLUE).grid(row=3, column=0, sticky="w", padx=(0, 8), pady=3)
        self.inputs["notes"] = owned_variable(self, value=_text(values.get("notes")))
        self.widgets["notes"] = ttk.Entry(header, textvariable=self.inputs["notes"])
        self.widgets["notes"].grid(row=3, column=1, columnspan=5, sticky="ew", pady=3)
        for key, default in (("rounding_policy", "legacy_whole"), ("posting_places", 0)):
            self.inputs[key] = owned_variable(self, value=values.get(key, default))
        self.inputs["buyer_code"].trace_add("write", self._buyer_code_changed)
        account.widget.bind("<<ComboboxSelected>>", self._buyer_selected, add=True)
        for sequence in ("<Return>", "<KP_Enter>"):
            self.widgets["document_number"].bind(sequence, lambda event: self.focus_date())
            self.widgets["business_date"].bind(sequence, lambda event: self.find_buyer())
            self.widgets["buyer_code"].bind(sequence, lambda event: self.first_item() if account.get() else self.find_buyer())
            self.widgets["notes"].bind(sequence, lambda event: self.first_item())
            self.widgets["bill_number"].bind(sequence, lambda event: self.focus_date())
            account.widget.bind(sequence, lambda event: self.first_item() if account.get() is not None else self.find_buyer())

        footer = ttk.Frame(self)
        footer.pack(side="bottom", fill="x", pady=(6, 0))
        self.notice = owned_variable(self)
        ttk.Label(footer, textvariable=self.notice, style="Muted.TLabel", wraplength=980).pack(fill="x", pady=(0, 4))
        totals = ttk.Frame(footer)
        totals.pack(fill="x")
        self.total_quantity, self.line_discount, self.subtotal, self.total = [owned_variable(self, value="0") for _ in range(4)]
        self.total_value = Decimal(0)
        for title, variable in (("Qty", self.total_quantity), ("Line discount", self.line_discount), ("Subtotal", self.subtotal)):
            ttk.Label(totals, text=title, foreground=_BLUE).pack(side="left", padx=(0, 5))
            ttk.Label(totals, textvariable=variable).pack(side="left", padx=(0, 20))
        ttk.Label(totals, textvariable=self.total, font=("TkDefaultFont", 14, "bold"), foreground=_BLUE).pack(side="right")
        ttk.Label(totals, text="Balance Value", font=("TkDefaultFont", 11, "bold"), foreground=_BLUE).pack(side="right", padx=12)
        adjustments = ttk.Frame(footer)
        adjustments.pack(fill="x", pady=7)
        for key, title in (("discount", "Header discount"), ("breakage", "Breakage"), ("freight", "Freight")):
            ttk.Label(adjustments, text=title, foreground=_BLUE).pack(side="left", padx=(0, 6))
            self.inputs[key] = owned_variable(self, value=_text(values.get(key, "0")))
            self.widgets[key] = ttk.Entry(adjustments, textvariable=self.inputs[key], width=12, justify="right")
            self.widgets[key].pack(side="left", padx=(0, 20))
            self.inputs[key].trace_add("write", lambda *_: self.update_totals())
        for sequence in ("<Return>", "<KP_Enter>"):
            self.widgets["discount"].bind(sequence, lambda event: self.focus_adjustment("breakage"))
            self.widgets["breakage"].bind(sequence, lambda event: self.focus_adjustment("freight"))
            self.widgets["freight"].bind(sequence, self.save_from_keyboard)
        controls = ttk.Frame(footer)
        controls.pack(fill="x")
        ttk.Button(controls, text="Saved records (F8)", command=on_records).pack(side="left")
        ttk.Button(controls, text="Add item", command=self.first_item).pack(side="left", padx=8)
        save = ttk.Button(controls, text="Save", command=lambda: self._submit(on_save),
                          width=8, style="Accent.TButton")
        save.pack(side="right", padx=(6, 0))
        self.buttons["Save"] = save
        self.bind_save_shortcut(save)
        for widget in self.widgets.values():
            self.bind_save_shortcut(widget)
        ttk.Label(self, text="Rate is per base unit. Enter: next field; blank item code: totals. Ctrl+S: Save. F4: Find. Ctrl+Delete: remove row.",
                  style="Muted.TLabel", wraplength=980).pack(anchor="w", pady=(7, 5))
        table = ttk.Frame(self)
        table.pack(fill="both", expand=True)
        background = ttk.Style(self).lookup("TFrame", "background") or "#F2F5F9"
        self.heading_canvas = tk.Canvas(table, height=30, borderwidth=0, highlightthickness=0, background=background)
        self.heading_canvas.grid(row=0, column=0, sticky="ew")
        self.canvas = tk.Canvas(table, height=190, borderwidth=0, highlightthickness=0, background=background)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.canvas.yview)
        vertical.grid(row=1, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self._xview)
        horizontal.grid(row=2, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(1, weight=1)
        headings = ttk.Frame(self.heading_canvas)
        self.heading_window = self.heading_canvas.create_window((0, 0), window=headings, anchor="nw")
        for column, (_, title, minimum, weight) in enumerate(_COLUMNS):
            headings.columnconfigure(column, minsize=minimum, weight=weight)
            ttk.Label(headings, text=title, foreground=_BLUE,
                      font=("TkDefaultFont", 9, "bold")).grid(row=0, column=column, sticky="w", pady=5)
        self.line_body = ttk.Frame(self.canvas)
        self.line_window = self.canvas.create_window((0, 0), window=self.line_body, anchor="nw")
        self.line_body.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._resize_table)
        self.bind_scroll(self.canvas)
        self.bind_scroll(self.line_body)
        for line in values.get("lines", []):
            self.add_line(line)
        while len(self.rows) < max(1, blank_rows):
            self.add_line()
        self.update_totals()
        if self.default_store is None:
            self.notice.set("This company's stock posting setup needs administrator review before saving a sale.")

    def _defer(self, callback):
        job = None
        def invoke():
            self._after_jobs.discard(job)
            if self.winfo_exists():
                callback()
        job = self.after_idle(invoke)
        self._after_jobs.add(job)

    def _destroyed(self, event):
        if event.widget is self:
            for job in self._after_jobs:
                self.after_cancel(job)
            self._after_jobs.clear()

    def _buyer_code_changed(self, *_):
        if self._changing_buyer:
            return
        matches = self.accounts_by_code.get(self.inputs["buyer_code"].get().strip().casefold(), [])
        choice = self.inputs["account_id"]
        choice.widget.current(choice.ids.index(matches[0]["id"] if len(matches) == 1 else None))

    def _buyer_selected(self, event=None):
        self._changing_buyer = True
        try:
            account = self.accounts_by_id.get(self.inputs["account_id"].get(), {})
            self.inputs["buyer_code"].set(account.get("code", ""))
        finally:
            self._changing_buyer = False
        if account:
            self._defer(self.first_item)

    def find_buyer(self):
        self.inputs["account_id"].search("Buyer", initial_text=self.inputs["buyer_code"].get()
                                         if self.inputs["account_id"].get() is None else "")
        return "break"

    def first_item(self):
        if self.buttons["Save"].instate(["disabled"]):
            return "break"
        row = next((line for line in self.rows if line.is_blank()), None)
        if row is None:
            row = self.add_line()
        row.focus("code")
        row.find_item()
        return "break"

    def next_item(self, row):
        if row.widgets["discount_percent"].instate(["disabled"]):
            return "break"
        try:
            row.value()
        except ValueError as error:
            self.notice.set(str(error))
            return "break"
        index = self.rows.index(row)
        following = self.rows[index + 1] if index + 1 < len(self.rows) else self.add_line()
        following.focus("code")
        following.find_item()
        return "break"

    def focus_adjustment(self, key):
        self.widgets[key].focus_set()
        self.widgets[key].selection_range(0, "end")
        return "break"

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

    def item_focus(self, row):
        self._active_row = row
        self.reveal_line(row)
        product = self.products_by_id.get(row.inputs["product_id"].get())
        note = []
        if not row.inputs["store_id"].get():
            note.append("This company's stock posting setup needs administrator review before saving a sale.")
        if product is not None:
            if not product.get("sale_rate_known", 1) or product.get("sale_rate") is None:
                note.append("No saved sale price. Enter the agreed rate per base unit.")
            if self.product_issues.get(product["id"]):
                note.append("Original stock includes differing units. Review the item's history before posting.")
        self.notice.set(" ".join(note))
        if self.on_item_focus is not None:
            self.on_item_focus(row)

    def set_stock(self, text, *, caption=None):
        self.stock.set(_text(text) or "—")
        if caption is not None:
            self.stock_caption.set(caption)

    def add_line(self, values=None, *, focus=False):
        row = _SaleRow(self, values or {})
        row.number = len(self.rows) + 1
        row.serial.set(str(row.number))
        row.frame.pack(fill="x")
        self.rows.append(row)
        self.update_totals()
        if focus:
            row.find_item()
        return row

    def remove_line(self, row):
        removed_index = self.rows.index(row)
        self.rows.remove(row)
        row.frame.destroy()
        if self._active_row is row:
            self._active_row = None
            self.set_stock(None)
        if not self.rows:
            self.add_line()
        for index, item in enumerate(self.rows, 1):
            item.number = index
            item.serial.set(str(index))
        self.update_totals()
        self.rows[min(removed_index, len(self.rows) - 1)].focus("code")

    def update_totals(self):
        try:
            lines = [row.invoice_line() for row in self.rows if not row.is_blank()]
            if not lines:
                self.total_value = Decimal(0)
                for variable in (self.total_quantity, self.line_discount, self.subtotal, self.total):
                    variable.set("0")
                return
            result = calculate_invoice(lines, rounding=self.inputs["rounding_policy"].get(),
                                       **{key: self.inputs[key].get() or "0" for key in ("discount", "breakage", "freight")})
            total = oracle_round(result.total, int(self.inputs["posting_places"].get()))
            with localcontext(_CONTEXT):
                quantity = sum((line.quantity for line in lines), Decimal(0))
                discount = sum((amount.discount for amount in result.line_amounts), Decimal(0))
            self.total_quantity.set(_display(quantity))
            self.line_discount.set(_display(discount))
            self.subtotal.set(_display(result.subtotal))
            self.total.set(_display(total))
            self.total_value = total
        except ValueError:
            self.total_value = None
            self.total.set("Check entries")
            for variable in (self.total_quantity, self.line_discount, self.subtotal):
                variable.set("—")

    def values(self):
        result = {key: control.get() for key, control in self.inputs.items() if key != "buyer_code"}
        result["posting_places"] = int(result["posting_places"])
        return result

    def payload(self):
        result = self.values()
        if result["account_id"] is None:
            raise ValueError("Choose a buyer or enter its complete account code.")
        result["lines"] = [line for row in self.rows if (line := row.value()) is not None]
        return result

    def raw_values(self):
        """Include incomplete input for New/Exit confirmation without validating."""
        return (tuple((key, control.get()) for key, control in self.inputs.items()),
                tuple(tuple((key, control.get()) for key, control in row.inputs.items()) for row in self.rows))

    def _submit(self, callback):
        try:
            payload = self.payload()
        except ValueError as error:
            messagebox.showerror("Check sale voucher", str(error), parent=self.winfo_toplevel())
            return
        callback(payload)

    def _resize_table(self, event):
        width = max(_MIN_TABLE_WIDTH, event.width)
        self.canvas.itemconfigure(self.line_window, width=width)
        self.heading_canvas.itemconfigure(self.heading_window, width=width)
        self.heading_canvas.configure(scrollregion=(0, 0, width, 30))

    def _xview(self, *arguments):
        self.canvas.xview(*arguments)
        self.heading_canvas.xview(*arguments)

    def reveal_line(self, row, widget=None):
        if not hasattr(self, "canvas"):
            return
        self.update_idletasks()
        height = self.line_body.winfo_height()
        top, bottom = row.frame.winfo_y(), row.frame.winfo_y() + row.frame.winfo_height()
        start, visible = self.canvas.canvasy(0), self.canvas.winfo_height()
        if height > visible:
            if top < start:
                self.canvas.yview_moveto(top / height)
            elif bottom > start + visible:
                self.canvas.yview_moveto((bottom - visible) / height)
        widget = widget or self.focus_get()
        if widget in row.widgets.values():
            width = self.line_body.winfo_width()
            left, right = widget.winfo_x(), widget.winfo_x() + widget.winfo_width()
            start, visible = self.canvas.canvasx(0), self.canvas.winfo_width()
            if width > visible:
                if left < start:
                    self._xview("moveto", left / width)
                elif right > start + visible:
                    self._xview("moveto", (right - visible) / width)

    def bind_scroll(self, widget):
        def scroll(event):
            steps = (-1 if event.num == 4 else 1) if event.num in (4, 5) else (-1 if event.delta > 0 else 1)
            self.canvas.yview_scroll(steps, "units")
            return "break"
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(sequence, scroll, add=True)
