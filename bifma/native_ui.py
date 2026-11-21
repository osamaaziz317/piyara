"""Desktop interface. Business reads and writes use authorized services.

The working database carries preserved opening balances from the original
records and new transactions. Historical documents remain in the archive.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from functools import partial
from pathlib import Path
import sqlite3
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from bifma.calculations import canonical_decimal, convert_quantity
from bifma.native_schema import initialize_database, migrate_database, native_session
from bifma.security import AuthenticationError, AuthorizationError, SecurityService
from bifma.tk_lifecycle import owned_variable
from bifma.keyboard import (focus as keyboard_focus, initial_focus as keyboard_initial_focus,
                            install_window as install_keyboard, uninstall as uninstall_keyboard,
                            unbind_callback)


ROOT = Path(__file__).resolve().parent.parent
NAVY, TEAL, BG, TEXT, MUTED = "#142D3D", "#007E78", "#F2F5F9", "#162F40", "#667C8D"
KINDS = {
    "sale": ("Sale Voucher", "sales"), "purchase": ("Purchases", "purchases"),
    "cash_sale": ("Counter sales", "counter_sales"), "cash_purchase": ("Cash purchases", "purchases"),
    "sale_return": ("Sales returns", "sales_returns"), "purchase_return": ("Purchase returns", "purchase_returns"),
    "cash_sale_return": ("Counter sale returns", "sales_returns"), "cash_purchase_return": ("Cash purchase returns", "purchase_returns"),
    "breakage_receive": ("Breakage receive", "breakage_receive"), "breakage_dispatch": ("Breakage dispatch", "breakage_dispatch"),
    "stock_opening": ("Opening stock", "stock"), "journal": ("Journal Voucher", "journals"),
    "opening_balance": ("Opening Balance", "journals"),
    "cash_receipt": ("Cash Receipt Voucher", "receipts"), "cash_payment": ("Cash Payment Voucher", "payments"),
}
BREAKAGE = {"breakage_receive", "breakage_dispatch"}
CASH_INVOICES = {"cash_sale", "cash_purchase"}
RETURN_ORIGINALS = {"sale_return": "sale", "purchase_return": "purchase", "cash_sale_return": "cash_sale", "cash_purchase_return": "cash_purchase"}
CASH_TRADES = CASH_INVOICES | {"cash_sale_return", "cash_purchase_return"}
TRADE = {"sale", "purchase", "sale_return", "purchase_return"} | CASH_TRADES | BREAKAGE
JOURNALS = {"journal", "opening_balance"}
ENTRY_MODULES = {module for _, module in KINDS.values()}
ACCESS_MODULES = ENTRY_MODULES | {"dashboard", "trial_balance", "ledger", "reports"}
MASTER_LABELS = {"companies": "Companies", "accounts": "Accounts", "cities": "Cities", "units": "Units",
                 "product_groups": "Item groups", "main_item_groups": "Main Item Groups", "sub_item_groups": "Item Sub Groups",
                 "products": "Items", "stores": "Stores", "company_stores": "Company stores"}
GROUP_SECTIONS = {"main_item_groups": "main", "sub_item_groups": "sub"}
MASTER_SECTIONS = {
    "Coding": ("accounts", "products", "cities", "main_item_groups", "sub_item_groups", "units", "stores", "company_stores"),
    "Setup": ("companies",),
}
DOCUMENT_SECTIONS = {
    "Coding": ("stock_opening",),
    "Financial Transactions": ("opening_balance", "cash_payment", "cash_receipt", "journal"),
    "Inventory": ("purchase", "sale", "purchase_return", "sale_return", "cash_sale",
                  "cash_purchase", "cash_sale_return", "cash_purchase_return", "breakage_receive", "breakage_dispatch"),
}
REPORT_SECTIONS = {
    "Financial reports": ("Cash book", "Account ledger", "Trial balance", "Sub-trial balance",
                          "Profit and loss", "Account balances by city"),
    "Inventory reports": ("Stock quantities", "Stock valuation", "Item ledger",
                          "Purchases / returns detail", "Sales / returns detail"),
}
# key, label, choice source (or input type). Services own all validation.
COMMON = [("code", "Code", "text"), ("name", "Name", "text"), ("name_urdu", "Urdu name", "optional")]
CONTACT = [("address", "Address", "optional"), ("phone", "Phone", "optional"),
           ("email", "Email", "optional"), ("website", "Website", "optional"),
           ("sales_tax_number", "Sales tax number", "optional"), ("national_tax_number", "National tax number", "optional")]
MASTER_FIELDS = {
    "companies": COMMON + [("fiscal_start", "Fiscal start (YYYY-MM-DD)", "optional"), ("fiscal_end", "Fiscal end (YYYY-MM-DD)", "optional"), ("cash_account_id", "Cash account", "accounts"), ("is_active", "Active", "bool")] + CONTACT + [("address_urdu", "Urdu address", "optional"), ("fax", "Fax", "optional")],
    "cities": COMMON,
    "accounts": COMMON + [("parent_id", "Parent account", "accounts"), ("city_id", "City", "cities"), ("kind", "Kind", ("general", "group", "customer", "supplier", "both", "cash", "bank")), ("classification", "Classification", ("", "asset", "liability", "equity", "income", "expense")), ("is_postable", "Accept postings", "bool"), ("is_active", "Active", "bool")] + CONTACT + [("mobile", "Mobile", "optional")],
    "units": COMMON + [("is_active", "Active", "bool")],
    "product_groups": COMMON + [("parent_id", "Parent group", "product_groups")],
    "main_item_groups": COMMON,
    "sub_item_groups": COMMON + [("parent_id", "Main item group", "main_item_groups")],
    "stores": COMMON + [("is_active", "Active", "bool")],
    "company_stores": [("company_id", "Company", "companies"), ("store_id", "Store", "stores"), ("is_active", "Active", "bool")],
    "products": [("code", "Item code", "text"), ("name", "Item description", "text"), ("name_urdu", "Urdu description", "optional"),
                 ("group_id", "Group", "product_groups"), ("small_unit_id", "Base unit", "units"),
                 ("large_unit_id", "Large unit", "units"), ("unit_factor", "Base units per large unit", "number"),
                 ("sale_rate", "Sale rate per base unit", "number"), ("purchase_rate", "Purchase rate per base unit", "number"),
                 ("is_active", "Active", "bool")],
}


def label(row):
    return f"{row.get('code', '')} · {row.get('name', row.get('display_name', ''))}".strip(" ·")


def value_text(value):
    return "" if value is None else str(value)


def baseline_details(info):
    """Use the same display values for stored metadata and an import result."""
    if info is None:
        return {}
    if isinstance(info, dict):
        return info
    return {"cutoff_date": info.cutoff_date, "manifest": info.manifest}


def stored_rate(product, kind):
    """An absent source price must never appear as a known zero price."""
    key = kind.removeprefix("cash_") + "_rate"
    return product.get(key) if product.get(key + "_known", 1) else None


def available_reports(permissions):
    """Show reports only when their underlying ledger/activity can be read."""
    reports = (("Trial balance", ("trial_balance",)), ("Sub-trial balance", ("trial_balance",)),
               ("Account balances by city", ("trial_balance",)), ("Stock quantities", ("stock",)), ("Stock valuation", ("stock",)),
               ("Item ledger", ("stock",)), ("Account ledger", ("ledger",)), ("Cash book", ("ledger",)),
               ("Sales / returns detail", ("sales", "counter_sales", "sales_returns")),
               ("Purchases / returns detail", ("purchases", "purchase_returns")))
    available = tuple(title for title, modules in reports
                      if any("read" in permissions.get(module, []) for module in modules))
    if all("read" in permissions.get(module, []) for module in ("reports", "trial_balance", "stock")):
        available += ("Profit and loss",)
    return available


def trade_report_choices(permissions, family):
    """Offer only payment/activity combinations the operator can request.

    The report service rechecks every grant; this controls the visible choices.
    Standard native invoices retain their original account-entry classification.
    """
    if family not in {"sales", "purchases"}:
        raise ValueError("Unknown trade report family")
    regular = "sales" if family == "sales" else "purchases"
    cash = "counter_sales" if family == "sales" else "purchases"
    returns = "sales_returns" if family == "sales" else "purchase_returns"
    can_read = lambda module: "read" in permissions.get(module, [])
    preferred = "all" if can_read(regular) and can_read(cash) else "account" if can_read(regular) else "cash" if can_read(cash) else "all"
    result = {}
    for mode in dict.fromkeys((preferred, "all", "account", "cash")):
        invoice_modules = (regular, cash) if mode == "all" else (cash,) if mode == "cash" else (regular,)
        invoice, returned = all(can_read(module) for module in invoice_modules), can_read(returns)
        choices = {}
        if invoice and returned:
            choices["Invoices and returns"] = "all"
        if invoice:
            choices["Invoices only"] = "invoice"
        if returned:
            choices["Returns only"] = "return"
        if choices:
            result[mode] = choices
    return result


def saved_rows(document):
    """Resolve issued labels from the immutable snapshot, not current masters."""
    snapshot = document.get("snapshot") or {}
    if document.get("kind") == "reversal":
        snapshot = snapshot.get("original_snapshot", {})
    accounts = snapshot.get("accounts", {})
    valuations = {row["movement_id"]: row for row in document.get("stock_valuations", [])}
    origins = {row["line_id"]: row for row in snapshot.get("lines", [])}
    if document.get("kind") == "reversal":
        origins = {line["id"]: origins.get(line.get("source_line_id"), {}) for line in document.get("lines", [])}
    result = {}
    for key in ("lines", "journal_lines", "stock_movements"):
        result[key] = []
        sources = document.get(key, [])
        if document.get("status") == "draft" and key != "lines":
            sources = (document.get("calculation") or {}).get(key, [])
        for source in sources:
            row = dict(source)
            origin = origins.get(row.get("document_line_id", row.get("id")), {})
            product, store = origin.get("product", {}), origin.get("store", {})
            account = accounts.get(row.get("account_id"), {})
            row.update(product_code=product.get("code", ""), product_name=product.get("name", ""),
                       store_name=store.get("name", ""), account_code=account.get("code", ""), account_name=account.get("name", ""))
            if key == "stock_movements":
                valuation = valuations.get(row.get("id"), {})
                for source_key, display_key in (("unit_cost", "unit_cost_display"), ("value", "stock_value_display")):
                    row[display_key] = ("Pending posting" if document.get("status") == "draft" else
                                        "Unknown" if valuation.get(source_key) is None else valuation[source_key])
            result[key].append(row)
    return result


def _prepare_modal(dialog, owner, *, previous_grab=None, focus=None):
    """Acquire modal input only after the window manager displays this dialog."""
    root = owner.winfo_toplevel()
    previous_focus = owner.focus_get()
    install_keyboard(dialog, owner)
    sync_menu = getattr(owner, "_sync_menu_state", None)
    if sync_menu is not None:
        sync_menu()

    def closing():
        return (getattr(owner, "close_pending", False) or getattr(owner, "_closed", False)
                or getattr(root, "_closing", False))

    def activate(event):
        if event.widget is not dialog or closing() or not dialog.winfo_viewable():
            return
        unbind_callback(dialog, "<Visibility>", activation_binding)
        dialog.grab_set()
        initial_focus = focus if focus is not None else getattr(dialog, "_initial_focus", None)
        target = initial_focus if initial_focus is not None and initial_focus.winfo_exists() else keyboard_initial_focus(dialog)
        # Dialogs are opened explicitly by the operator. Merely remembering
        # their field with focus_set can leave
        # keys in the parent while the nested dialog already owns the grab.
        target.focus_force()

    def restore(event):
        if event.widget is dialog and not closing():
            if sync_menu is not None:
                sync_menu(exclude=dialog)
            try:
                if previous_grab is not None and previous_grab.winfo_exists() and previous_grab.winfo_viewable():
                    previous_grab.grab_set()
                if previous_focus is not None and previous_focus.winfo_exists() and previous_focus.winfo_viewable():
                    previous_focus.focus_force()
                elif previous_grab is not None and previous_grab.winfo_exists():
                    keyboard_initial_focus(previous_grab).focus_force()
            except tk.TclError:
                pass

    activation_binding = dialog.bind("<Visibility>", activate, add=True)
    dialog.bind("<Destroy>", restore, add=True)


class Choice:
    """Keeps foreign keys separate from human labels; never parses display text.

    Enter/arrow lookup is enabled by default. Find closes before notifying
    selection handlers, so parent focus can advance without the old popup
    restoring it. Explicit keyboard acceptance also emits ChoiceAccepted.
    """
    def __init__(self, parent, records, *, selected=None, optional=True, width=28, enter_select=True):
        self.records = list(records)
        self._enter_select = enter_select
        self.ids = ([None] if optional else []) + [row["id"] for row in self.records]
        labels = (["— Select —"] if optional else []) + [label(row) for row in self.records]
        self.widget = ttk.Combobox(parent, values=labels, state="readonly", width=width)
        self._search_title, self._search_window, self._search_entry = "Select", None, None
        self._dropdown_list = None
        self.widget.configure(postcommand=self._prepare_dropdown)
        # The selected text is a label, never a foreign key. Typing opens the
        # real search entry instead of silently ignoring letters in a readonly
        # combobox or accepting an unvalidated account/item name as a selection.
        self.widget.bind("<KeyPress>", self._type_search)
        self.widget.bind("<<Paste>>", self._paste_search)
        self.widget.bind("<Button-1>", self._focus_search)
        if enter_select:
            for sequence in ("<Return>", "<KP_Enter>"):
                self.widget.bind(sequence, self._enter_search)
        self.widget.bind("<F4>", lambda event: (self.search(self._search_title), "break")[-1])
        if selected in self.ids:
            self.widget.current(self.ids.index(selected))
        elif self.ids:
            self.widget.current(0)

    def get(self):
        index = self.widget.current()
        return self.ids[index] if 0 <= index < len(self.ids) else None

    def add_search_button(self, title):
        """Search large choices without changing their ID/selection contract."""
        self._search_title = title
        if len(self.records) < 20:
            return
        ttk.Button(self.widget.master, text="Find…", command=lambda: self.search(title)).pack(anchor="e", pady=(4, 0))

    def _type_search(self, event):
        if event.char and event.char.isprintable() and not event.state & (0x4 | 0x8):
            self.search(self._search_title, initial_text=event.char)
            return "break"

    def _enter_search(self, event=None):
        if self.get() is not None:
            self.widget.event_generate("<<ChoiceAccepted>>")
        else:
            self.search(self._search_title)
        return "break"

    def _prepare_dropdown(self):
        # Tk transfers focus to its internal listbox after the arrow opens.
        # Bind only this selector's listbox, retaining native navigation and
        # selection bindings. Register callbacks on the owning widget so its
        # destruction also disposes their Tcl commands.
        popup = str(self.widget.tk.call("ttk::combobox::PopdownWindow", self.widget))
        listbox = popup + ".f.l"
        if self._dropdown_list != listbox:
            self.widget._bind(("bind", listbox), "<KeyPress>", self._type_search, "+")
            self.widget._bind(("bind", listbox), "<<Paste>>", self._paste_search, "+")
            self._dropdown_list = listbox

    def _focus_search(self, event):
        # A click on the text is an entry focus, so the next letters can start
        # a name search. The arrow keeps the standard dropdown behaviour.
        if "downarrow" not in self.widget.identify(event.x, event.y):
            if not self.widget.instate(["disabled"]):
                self.widget.focus_set()
            return "break"

    def _paste_search(self, event=None):
        try:
            text = self.widget.clipboard_get()
        except tk.TclError:
            return "break"
        if text:
            self.search(self._search_title, initial_text=text)
        return "break"

    def search(self, title, *, initial_text=""):
        if self.widget.instate(["disabled"]):
            return
        if self.widget.instate(["pressed"]):
            # Dismiss the native global grab before opening our modal Find.
            # Unpost runs Tk's own saved-grab/focus restoration machinery.
            self.widget.tk.call("ttk::combobox::Unpost", self.widget)
            self.widget.focus_set()
        if self._search_window is not None and self._search_window.winfo_exists():
            if initial_text:
                self._search_entry.insert("end", initial_text)
            self._search_window.lift()
            self._search_entry.focus_set()
            return
        parent = self.widget.winfo_toplevel()
        root = self.widget
        while root.master is not None and not isinstance(root, _NativeInterface):
            root = root.master
        previous_grab = root.grab_current()
        # Nested searches belong to their entry dialog. A selector in the
        # embedded main workspace stays owned by that workspace so its input
        # freeze, menu guard and destruction also cover the Find window.
        dialog = tk.Toplevel(parent if isinstance(parent, tk.Toplevel) else root)
        self._search_window = dialog
        dialog.title("Find · " + title)
        dialog.geometry("720x520")
        dialog.minsize(560, 380)
        dialog.transient(parent)
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Find · " + title, style="Title.TLabel").pack(anchor="w", pady=(0, 12))
        query = Form(body, [("search", "Code or name", "text")],
                     values={"search": initial_text}, columns=1)
        self._search_entry = query.widgets["search"]
        self._search_entry.icursor("end")
        query.frame.pack(fill="x")
        controls = ttk.Frame(body)
        controls.pack(fill="x", pady=(0, 10))
        footer = ttk.Frame(body)
        footer.pack(side="bottom", fill="x", pady=(10, 0))
        tree = NativeApp.tree(body, [("code", "Code"), ("name", "Name"), ("name_urdu", "Urdu name")], [], height=8)
        state = {"rows": [], "matches": [], "offset": 0}
        count = owned_variable(footer)
        ttk.Label(footer, textvariable=count, style="Muted.TLabel").pack(side="left")
        def show(offset=0):
            matches = state["matches"]
            state["offset"], state["rows"] = offset, matches[offset:offset + 100]
            tree.delete(*tree.get_children())
            for index, row in enumerate(state["rows"]):
                tree.insert("", "end", iid=str(index), values=[value_text(row.get(key)) for key in ("code", "name", "name_urdu")])
            if self._enter_select and state["rows"]:
                tree.selection_set("0")
                tree.focus("0")
            count.set(f"{offset + 1 if state['rows'] else 0}–{offset + len(state['rows'])} of {len(matches)}")
            previous.configure(state="normal" if offset else "disabled")
            following.configure(state="normal" if offset + 100 < len(matches) else "disabled")
        def search(event=None):
            term = query.inputs["search"].get().strip().casefold()
            state["matches"] = [row for row in self.records if any(term in str(row.get(key) or "").casefold() for key in ("code", "name", "name_urdu"))]
            show()
            return "break"
        def use(event=None):
            selected = tree.selection()
            if not selected and self._enter_select and state["rows"]:
                selected = ("0",)
            if selected:
                identifier = state["rows"][int(selected[0])]["id"]
                self.widget.current(self.ids.index(identifier))
                if self._enter_select:
                    # Keyboard-driven forms advance to their next field in
                    # this event. Restore the parent modal before notifying
                    # them, otherwise closing Find would undo their focus.
                    dialog.destroy()
                    if self.widget.winfo_exists():
                        self.widget.event_generate("<<ComboboxSelected>>")
                        if event is not None and getattr(event, "keysym", "") in {"Return", "KP_Enter"} and self.widget.winfo_exists():
                            self.widget.event_generate("<<ChoiceAccepted>>")
                else:
                    self.widget.event_generate("<<ComboboxSelected>>")
                    dialog.destroy()
            return "break"
        def move(direction):
            if state["rows"]:
                selected = tree.selection()
                current = state["offset"] + (int(selected[0]) if selected else 0)
                following = max(0, min(len(state["matches"]) - 1, current + direction))
                offset = following // 100 * 100
                if offset != state["offset"]:
                    show(offset)
                identifier = str(following - offset)
                tree.selection_set(identifier)
                tree.focus(identifier)
                tree.see(identifier)
            return "break"
        ttk.Button(controls, text="Search", command=search).pack(side="left")
        ttk.Button(controls, text="Use selected", style="Accent.TButton", command=use).pack(side="right")
        following = ttk.Button(footer, text="Next", command=lambda: show(state["offset"] + 100))
        following.pack(side="right")
        previous = ttk.Button(footer, text="Previous", command=lambda: show(max(0, state["offset"] - 100)))
        previous.pack(side="right", padx=8)
        # The shared Find window filters its loaded choices as text changes,
        # including paste and deletion. Its owner disposes the trace on close.
        query.inputs["search"].trace_add("write", lambda *_: search())
        for sequence in ("<Return>", "<KP_Enter>"):
            query.widgets["search"].bind(sequence, use if self._enter_select else search)
        if self._enter_select:
            for control in (query.widgets["search"], tree):
                control.bind("<Down>", lambda event: move(1))
                control.bind("<Up>", lambda event: move(-1))
        tree.bind("<Double-1>", use)
        tree.bind("<Return>", use)
        tree.bind("<KP_Enter>", use)
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        def cleared(event):
            if event.widget is dialog:
                self._search_window = self._search_entry = None
        dialog.bind("<Destroy>", cleared, add=True)
        search()
        _prepare_modal(dialog, root, previous_grab=previous_grab, focus=query.widgets["search"])


class Form:
    def __init__(self, parent, fields, *, values=None, choices=None, columns=2, enter_navigation=True):
        self.frame = ttk.Frame(parent, padding=12)
        self.inputs, self.kinds, self.widgets = {}, {}, {}
        self._enter_target = None
        self._invoke_enter_target = True
        values, choices = values or {}, choices or {}
        for index, (key, title, kind) in enumerate(fields):
            row, col = divmod(index, columns)
            cell = ttk.Frame(self.frame, padding=(5, 5))
            cell.grid(row=row, column=col, sticky="nsew")
            self.frame.columnconfigure(col, weight=1)
            ttk.Label(cell, text=title, style="Muted.TLabel").pack(anchor="w", pady=(0, 4))
            self.kinds[key] = kind
            if kind == "bool":
                control = owned_variable(self.frame, boolean=True, value=bool(values.get(key, True)))
                widget = ttk.Checkbutton(cell, variable=control)
            elif isinstance(kind, tuple):
                control = owned_variable(self.frame, value=value_text(values.get(key, kind[0])))
                widget = ttk.Combobox(cell, textvariable=control, values=kind, state="readonly", width=28)
            elif kind in choices:
                control = Choice(cell, choices[kind], selected=values.get(key), enter_select=enter_navigation)
                widget = control.widget
            else:
                default = "1" if key in {"quantity", "unit_factor"} else "0" if kind == "number" else ""
                control = owned_variable(self.frame, value=value_text(values.get(key, default)))
                widget = ttk.Entry(cell, textvariable=control, width=30, show="•" if kind == "password" else "")
            widget.pack(fill="x")
            if isinstance(control, Choice):
                control.add_search_button(title)
            self.inputs[key] = control
            self.widgets[key] = widget
            if enter_navigation:
                if isinstance(control, Choice):
                    widget.bind("<<ComboboxSelected>>", lambda event, field=key: (self.advance(field, invoke=False), None)[1], add=True)
                    widget.bind("<<ChoiceAccepted>>", lambda event, field=key: self.advance(field), add=True)
                    for sequence in ("<Return>", "<KP_Enter>"):
                        widget.bind(sequence, lambda event, field=key, choice=control:
                                    self.advance(field) if choice.get() is not None else choice._enter_search(event))
                else:
                    for sequence in ("<Return>", "<KP_Enter>"):
                        widget.bind(sequence, lambda event, field=key: self.advance(field))

    @staticmethod
    def _can_focus(widget):
        # A readonly combobox still supports selecting a value. A generated
        # item code or a disabled company cash account is not an entry stop.
        return (widget.winfo_exists() and not widget.instate(["disabled"])
                and (isinstance(widget, ttk.Combobox) or not widget.instate(["readonly"])))

    def first_widget(self):
        return next((widget for widget in self.widgets.values() if self._can_focus(widget)), None)

    @staticmethod
    def _focus(widget):
        return keyboard_focus(widget)

    def focus_first(self):
        widget = self.first_widget()
        return self._focus(widget) if widget is not None else "break"

    def set_enter_target(self, widget, *, invoke=True):
        """Complete the form through its existing action after the final field."""
        self._enter_target = widget
        self._invoke_enter_target = invoke
        if isinstance(widget, ttk.Button):
            for sequence in ("<Return>", "<KP_Enter>"):
                widget.bind(sequence, lambda event: (widget.invoke(), "break")[-1])

    def advance(self, field, *, invoke=True):
        controls = list(self.widgets)
        for key in controls[controls.index(field) + 1:]:
            widget = self.widgets[key]
            if self._can_focus(widget):
                return self._focus(widget)
        if self._enter_target is not None and self._can_focus(self._enter_target):
            if invoke and self._invoke_enter_target and isinstance(self._enter_target, ttk.Button):
                self._enter_target.invoke()
                return "break"
            return self._focus(self._enter_target)
        return "break"

    def values(self):
        result = {key: control.get() for key, control in self.inputs.items()}
        for key, kind in self.kinds.items():
            if (kind == "optional" or key == "classification") and not result[key]:
                result[key] = None
        return result


class _NativeInterface:
    """One screen/controller implementation for embedded and test workspaces."""

    def _initialize_workspace(self, database=None, *, baseline_source=None, archive_path=None):
        from bifma.print_preview import PreviewFiles
        self._closed = self._finishing_close = False
        self._print_previews = PreviewFiles()
        self.future = self.poll_id = self._startup_job = None
        self.close_pending = False
        self.worker = None
        self.configure(bg=BG)
        self.path, self.actor, self.company_id = None, None, None
        self.baseline_source = Path(baseline_source).resolve() if baseline_source else None
        self.archive_path = Path(archive_path).resolve() if archive_path else self.baseline_source
        self.startup_database = Path(database).resolve() if database else None
        self.baseline = None
        self.security = self.masters = self.transactions = self.reports = self.register = None
        self.document_rows = []
        self.document_tree = None
        self._document_filters = {}
        self._navigation_section = None
        self._record_target = self._records_browser = None
        self._f8_root = self.winfo_toplevel()
        self._f8_binding = self._f8_root.bind("<F8>", self._entry_f8, add="+")
        install_keyboard(self._f8_root, self)
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bifma-ui")
        self.status = owned_variable(self, value="Ready")
        self._styles()
        self.welcome(database)

    def _abort_initialization(self):
        # A failed lazy panel must not leave a worker, idle callback or widget
        # attached to the still-running application root.
        worker = getattr(self, "worker", None)
        if worker is not None:
            worker.shutdown(wait=True, cancel_futures=True)
        self.future = None
        self._finish_close()

    def _styles(self):
        style = ttk.Style(self)
        if style.theme_use() != "clam":
            style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), foreground=TEXT)
        style.configure("TFrame", background="white")
        style.configure("TLabel", background="white")
        style.configure("TLabelframe", background="white", bordercolor="#DEE4E8")
        style.configure("TLabelframe.Label", background="white", foreground=MUTED)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Heading.TLabel", font=("Segoe UI", 23, "bold"))
        style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("TButton", padding=(12, 8))
        style.configure("Accent.TButton", background=TEAL, foreground="white")
        style.map("Accent.TButton", background=[("active", "#006A65")])
        style.configure("Treeview", rowheight=31, borderwidth=0, fieldbackground="white")
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), padding=8)
        style.map("Treeview", background=[("selected", "#D9EFEA")], foreground=[("selected", TEXT)])

    def _clear(self):
        self._detach_menu()
        self._login_form = None
        self._record_target = self._records_browser = None
        if self._startup_job is not None:
            self.after_cancel(self._startup_job)
            self._startup_job = None
        for child in self.winfo_children():
            child.destroy()

    def run(self, task, success=None):
        """One operation at a time; all Tk access remains on the UI thread."""
        if self._closed or self.close_pending:
            return
        if self.future is not None:
            self.status.set("Please wait for the current operation to finish.")
            return
        self.status.set("Working…")
        self.configure(cursor="watch")
        operation_focus = self.focus_get()
        locked = []
        def freeze(parent):
            for widget in parent.winfo_children():
                if isinstance(widget, (ttk.Entry, ttk.Combobox, ttk.Button, ttk.Checkbutton, ttk.Treeview)):
                    disabled = widget.instate(["disabled"])
                    if not disabled:
                        widget.state(["disabled"])
                        locked.append((widget, "ttk"))
                elif isinstance(widget, tk.Button) and widget.cget("state") != "disabled":
                    widget.configure(state="disabled")
                    locked.append((widget, "tk"))
                freeze(widget)
        freeze(self)
        self.future = self.worker.submit(task)
        self._sync_menu_state()
        def poll():
            self.poll_id = None
            if not self.future.done():
                self.poll_id = self.after(50, poll)
                return
            future, self.future = self.future, None
            self.configure(cursor="")
            for widget, family in locked:
                if widget.winfo_exists():
                    widget.state(["!disabled"]) if family == "ttk" else widget.configure(state="normal")
            try:
                result = future.result()
                self.status.set("Ready")
                if success and not self.close_pending:
                    success(result)
            except AuthenticationError as error:
                self.status.set(str(error))
                if self.actor is not None:
                    self.actor = None
                    self.login()
                messagebox.showerror("Sign in", str(error), parent=self)
            except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
                self.status.set("The operation was not completed.")
                messagebox.showerror("Unable to complete", str(error), parent=self.grab_current() or self)
                keyboard_focus(operation_focus)
            finally:
                self._sync_menu_state()
                if self.close_pending:
                    self._finish_close()
        self.poll_id = self.after(50, poll)

    def _panel(self, title, subtitle):
        self._clear()
        panel = ttk.Frame(self, padding=35)
        panel.place(relx=.5, rely=.47, anchor="center", width=660)
        ttk.Label(panel, text="BIFMA TRADERS", foreground=TEAL).pack(anchor="w")
        ttk.Label(panel, text=title, style="Heading.TLabel").pack(anchor="w", pady=(10, 8))
        ttk.Label(panel, text=subtitle, style="Muted.TLabel", wraplength=580).pack(anchor="w", pady=(0, 12))
        ttk.Label(self, textvariable=self.status, padding=10).pack(side="bottom", fill="x")
        return panel

    def welcome(self, database=None):
        if self.baseline_source:
            database = database or self.startup_database
            panel = self._panel("Opening your business", "Loading your companies, items, accounts and transactions.")
            ttk.Button(panel, text="Retry opening", command=lambda: self.welcome(database)).pack(fill="x", pady=5)
            def prepare():
                from bifma.baseline import ensure_baseline
                from bifma.native_schema import startup_verification
                with startup_verification():
                    result = ensure_baseline(self.baseline_source, database, upgrade=True)
                    return self._services(result.path)
            def prepare_initial_database():
                self._startup_job = None
                if not self._closed and not self.close_pending:
                    self.run(prepare, self._services_ready)
            self._startup_job = self.after_idle(prepare_initial_database)
            return
        if database:
            self._panel("Opening your business", "Loading your companies, transactions and balances.")
            def open_initial_database():
                self._startup_job = None
                if not self._closed and not self.close_pending:
                    self.open_database(database)
            self._startup_job = self.after_idle(open_initial_database)
            return
        panel = self._panel("Your business workspace", "Open a working database or restore a backup.")
        ttk.Button(panel, text="Open database", style="Accent.TButton", command=self.open_database).pack(fill="x", pady=5)
        ttk.Button(panel, text="Create new database", command=self.create_database).pack(fill="x", pady=5)
        ttk.Button(panel, text="Restore backup to a new database", command=self.restore_dialog).pack(fill="x", pady=5)
        ttk.Button(panel, text="Upgrade an existing native database", command=self.upgrade_database).pack(fill="x", pady=5)
        ttk.Label(panel, text="The application's default workspace continues from the existing data. "
                  "Creating a new database here starts an independent business.",
                  wraplength=580, style="Muted.TLabel").pack(anchor="w", pady=(15, 0))

    def open_database(self, path=None):
        path = path or filedialog.askopenfilename(parent=self, title="Open native database", filetypes=[("SQLite database", "*.sqlite3 *.db"), ("All files", "*")])
        if path:
            self.run(partial(self._services, Path(path).resolve()), self._services_ready)

    def _services(self, path):
        from bifma.masters import MasterService
        from bifma.transactions import TransactionService
        from bifma.unified_reports import UnifiedReports
        from bifma.baseline import prepare_native_login
        from bifma.document_register import DocumentRegister
        from bifma.native_schema import startup_verification
        with startup_verification():
            baseline = prepare_native_login(path)
            security = SecurityService(path)
            register = DocumentRegister(path, archive_path=self.archive_path if baseline else None, security=security)
            with native_session(path) as connection:
                initial = connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None
            return path, security, MasterService(path, security=security), TransactionService(path, security=security), UnifiedReports(path, security=security, register=register), initial, baseline, register

    def _services_ready(self, result):
        self.path, self.security, self.masters, self.transactions, self.reports, initial, self.baseline, self.register = result
        self.login(initial=initial)

    def create_database(self):
        path = filedialog.asksaveasfilename(parent=self, title="Create a new native database", defaultextension=".sqlite3", initialfile="bifma_native.sqlite3", filetypes=[("SQLite database", "*.sqlite3")], confirmoverwrite=False)
        if path:
            def create():
                initialize_database(path)
                return self._services(Path(path).resolve())
            self.run(create, self._services_ready)

    def upgrade_database(self):
        path = filedialog.askopenfilename(parent=self, title="Select a native database to upgrade", filetypes=[("SQLite database", "*.sqlite3 *.db")])
        if path:
            def upgraded(result):
                messagebox.showinfo("Upgrade complete", f"Database version: {result.version}" + (f"\nBackup: {result.backup_path}" if result.backup_path else "\nThe database was already current."), parent=self)
                self.open_database(path)
            self.run(partial(migrate_database, path), upgraded)

    def restore_dialog(self):
        source = filedialog.askopenfilename(parent=self, title="Select a native backup", filetypes=[("SQLite database", "*.sqlite3 *.db")])
        if not source:
            return
        destination = filedialog.asksaveasfilename(parent=self, title="Restore to a new database file", defaultextension=".sqlite3", initialfile="bifma_restored.sqlite3", filetypes=[("SQLite database", "*.sqlite3")], confirmoverwrite=False)
        if not destination:
            return
        dialog, body = self.dialog("Restore native backup", width=670, height=500)
        ttk.Label(body, text="Sign in with an active administrator account from this backup. The restored database will require a fresh sign-in for every user.", style="Muted.TLabel", wraplength=590).pack(anchor="w", pady=10)
        form = Form(body, [("username", "Backup administrator username", "text"), ("password", "Passphrase", "password")], columns=1)
        form.frame.pack(fill="x")
        def restore():
            from bifma.native_backup import restore_backup
            def restored(result):
                dialog.destroy()
                self.open_database(result.path)
            values = form.values()
            actor = self.actor
            def restore_checked():
                try:
                    return restore_backup(source, destination, **values)
                except AuthenticationError as error:
                    # These credentials belong to the private backup copy;
                    # rejection must not sign out a valid current workspace.
                    # Expired/revoked workspace sessions still require login.
                    if actor is not None:
                        self.security.current_user(actor)
                    raise ValueError(str(error)) from None
            self.run(restore_checked, restored)
        action = ttk.Button(body, text="Restore to new file", style="Accent.TButton", command=restore)
        action.pack(anchor="e", pady=10)
        form.set_enter_target(action)
        dialog._focus_entry = True
        dialog._initial_focus = form.first_widget()
        form.focus_first()

    def create_backup(self):
        from bifma.native_backup import NativeBackupService
        destination = filedialog.asksaveasfilename(parent=self, title="Create verified native backup", defaultextension=".sqlite3", initialfile=f"bifma-backup-{date.today().isoformat()}.sqlite3", filetypes=[("SQLite database", "*.sqlite3")], confirmoverwrite=False)
        if destination:
            service = NativeBackupService(self.path, security=self.security)
            self.run(partial(service.create_backup, self.actor, destination), lambda result: messagebox.showinfo("Backup saved", f"Verified backup saved to:\n{result.path}", parent=self))

    def login(self, *, initial=False):
        self._print_previews.close()
        from bifma.login_ui import LoginForm
        self.actor = None
        self.document_rows, self.document_tree = [], None
        self._document_filters.clear()
        self._navigation_section = None
        self._clear()
        root = self.winfo_toplevel()
        if not getattr(self, "_business_window", None):
            self._business_window = (root.geometry(), root.minsize(), root.title(), root.state())
        root.state("normal")
        root.title("Create your login · BIFMA TRADERS" if initial else "Please enter User and Password · BIFMA TRADERS")
        width, height = 760, 560 if initial else 500
        root.minsize(720, height)
        root.geometry(f"{width}x{height}+{max(0, (root.winfo_screenwidth()-width)//2)}+{max(0, (root.winfo_screenheight()-height)//2)}")
        self.configure(bg="#dde3e3")
        self.status.set("Create your personal administrator login." if initial else "Enter User, then press Enter.")
        ttk.Label(self, textvariable=self.status, padding=10).pack(side="bottom", fill="x")

        def submit():
            if self.future is not None or self.close_pending:
                return
            values = form.values()
            if initial and values["confirm"] != values["password"]:
                messagebox.showerror("Check password", "The passwords do not match.", parent=self)
                form.focus("confirm")
                return
            selected_company = form.company_id()
            def task():
                actor = None
                try:
                    actor = (self.security.bootstrap_admin(values["username"], values["password"], display_name=values["display_name"])
                             if initial else self.security.authenticate(values["username"], values["password"]))
                    if selected_company is not None and selected_company not in {
                            identifier for identifier, _ in self.security.accessible_companies(actor)}:
                        raise AuthorizationError("The selected company is no longer available to this user. Choose the user again.")
                    return actor, None
                except (AuthenticationError, AuthorizationError) as error:
                    if actor is not None:
                        self.security.logout(actor)
                    return None, str(error)
            def signed(result):
                actor, error = result
                if error:
                    form.inputs["password"].set("")
                    self.status.set(error)
                    messagebox.showerror("Sign in", error, parent=self)
                    form.focus("password")
                    return
                if selected_company is not None:
                    self.company_id = selected_company
                self._signed_in(actor)
            self.run(task, signed)

        form = self._login_form = LoginForm(self, initial=initial, submit=submit, cancel=getattr(root, "close", self.close))
        form.place(relx=.5, rely=.47, anchor="center", width=700, height=460 if initial else 400)
        def user_enter():
            if initial:
                if not form.inputs["display_name"].get().strip():
                    form.inputs["display_name"].set(form.inputs["username"].get().strip())
                return form.focus("display_name")
            query = form.inputs["username"].get()
            def show_profile(profile):
                if self._login_form is not form or form.inputs["username"].get() != query:
                    return
                form.set_profile(profile, self.company_id)
                self.status.set("Press Enter for Password." if profile else "Enter your password to sign in, or check the User name.")
                form.focus("display_name" if profile else "password")
            self.run(partial(self.security.login_profile, query), show_profile)
            return "break"

        def password_enter():
            if initial:
                return form.focus("confirm")
            if len(form.companies) > 1:
                return form.focus("company")
            submit()
            return "break"

        actions = {"username": user_enter, "display_name": lambda: form.focus("password"),
                   "password": password_enter,
                   "confirm": lambda: (submit(), "break")[-1],
                   "company": lambda: (submit(), "break")[-1]}
        for key, widget in form.widgets.items():
            def enter(event, action=actions[key]):
                if not event.widget.instate(["disabled"]):
                    action()
                return "break"
            for sequence in ("<Return>", "<KP_Enter>"):
                widget.bind(sequence, enter)
        if not initial:
            form.inputs["username"].trace_add("write", lambda *_: form.clear_profile())
        def focus_user():
            if self._login_form is form and form.winfo_exists() and not self.close_pending:
                form.focus("username")
        focus_job = form.after_idle(focus_user)
        form.bind("<Destroy>", lambda event: form.after_cancel(focus_job) if event.widget is form else None, add=True)

    def _signed_in(self, actor):
        form = getattr(self, "_login_form", None)
        if form is not None:
            for key in ("password", "confirm"):
                if key in form.inputs:
                    form.inputs[key].set("")
        window = getattr(self, "_business_window", None)
        if window:
            root = self.winfo_toplevel()
            geometry, minimum, title, state = window
            root.minsize(*minimum)
            root.geometry(geometry)
            root.title(title)
            root.state(state)
            self._business_window = None
        self.actor = actor
        self.configure(bg=BG)
        self.refresh_shell()

    def refresh_shell(self, *, view=None):
        def fetch():
            companies = self.security.accessible_companies(self.actor)
            selected = self.company_id if self.company_id in [row[0] for row in companies] else companies[0][0] if companies else None
            return companies, self.security.permissions(self.actor, selected) if selected else {}
        self.run(fetch, lambda result: self.shell(*result, view=view))

    def refresh_company_choices(self, done):
        """Refresh edited company access without closing an unfinished form."""
        def fetch():
            companies = self.security.accessible_companies(self.actor)
            selected = self.company_id if self.company_id in [row[0] for row in companies] else companies[0][0] if companies else None
            return companies, selected, self.security.permissions(self.actor, selected) if selected else {}
        def render(result):
            self.companies, self.company_id, self.permissions = result
            self._company_choice.configure(values=[row[1] for row in self.companies])
            if self.company_id is not None:
                self._company_choice.current([row[0] for row in self.companies].index(self.company_id))
            else:
                self._company_choice.set("")
            done()
        self.run(fetch, render)

    def _detach_menu(self):
        for sequence, identifier in getattr(self, "_menu_bindings", []):
            unbind_callback(self.winfo_toplevel(), sequence, identifier)
        self._menu_bindings = []
        bar = getattr(self, "_menu_bar", None)
        if bar is not None and str(self.winfo_toplevel().cget("menu")) == str(bar):
            self.winfo_toplevel().configure(menu="")
        self._menu_bar = None
        self._menu_widgets = []

    def _menu_blocked(self, exclude=None):
        if self.future is not None or self.close_pending:
            return True
        def modal(parent):
            for child in parent.winfo_children():
                if child is exclude:
                    continue
                if isinstance(child, tk.Toplevel) or modal(child):
                    return True
            return False
        return modal(self)

    def _sync_menu_state(self, exclude=None):
        state = "disabled" if self._menu_blocked(exclude) else "normal"
        for menu in getattr(self, "_menu_widgets", []):
            if menu.winfo_exists() and menu.index("end") is not None:
                for index in range(menu.index("end") + 1):
                    if menu.type(index) != "separator":
                        menu.entryconfigure(index, state=state)

    def _show_navigation(self, title):
        self._navigation_section = title

    def _build_menu(self):
        self._menu_widgets = []
        def menu():
            result = tk.Menu(self, tearoff=False)
            self._menu_widgets.append(result)
            return result
        def command(parent, title, action):
            def invoke():
                if self._menu_blocked():
                    self.status.set("Finish or close the current form before changing sections.")
                    return
                action()
            parent.add_command(label=title, command=invoke)
        bar = self._menu_bar = menu()
        file = menu()
        if "read" in self.permissions.get("dashboard", []):
            command(file, "Overview", self.dashboard)
            file.add_separator()
        command(file, "Change passphrase", self.password_dialog)
        command(file, "Sign out", self.logout)
        command(file, "Exit", self.winfo_toplevel().close)
        bar.add_cascade(label="File", menu=file, underline=0)
        coding = menu()
        if self.actor.user.is_admin:
            financial = menu()
            command(financial, "Accounts", partial(self.master_entry, "accounts"))
            coding.add_cascade(label="Financial Coding", menu=financial, underline=0)
        inventory = menu()
        if self.actor.user.is_admin:
            for entity in ("main_item_groups", "sub_item_groups", "products", "units", "stores", "company_stores"):
                command(inventory, MASTER_LABELS[entity], partial(self.master_entry, entity))
        if "read" in self.permissions.get("stock", []):
            command(inventory, KINDS["stock_opening"][0], partial(self.transaction_entry, "stock_opening"))
        if inventory.index("end") is not None:
            coding.add_cascade(label="Inventory Coding", menu=inventory, underline=0)
        if self.actor.user.is_admin:
            command(coding, "Cities", partial(self.master_entry, "cities"))
        if coding.index("end") is not None:
            bar.add_cascade(label="Coding", menu=coding, underline=0)
        for title in ("Financial Transactions", "Inventory"):
            section = menu()
            for kind in DOCUMENT_SECTIONS[title]:
                if "read" in self.permissions.get(KINDS[kind][1], []):
                    command(section, KINDS[kind][0], partial(self.transaction_entry, kind))
            if section.index("end") is not None:
                bar.add_cascade(label=title, menu=section, underline=title.index("T") if title == "Financial Transactions" else 0)
        reports = menu()
        if any("read" in self.permissions.get(module, []) for _, module in KINDS.values()):
            command(reports, "Voucher Printing", self.voucher_printing)
        available = available_reports(self.permissions)
        for title, names in REPORT_SECTIONS.items():
            section = menu()
            for report in names:
                if report in available:
                    command(section, report, partial(self.report_screen, report))
            if section.index("end") is not None:
                reports.add_cascade(label=title, menu=section)
        if reports.index("end") is not None:
            bar.add_cascade(label="Reports", menu=reports, underline=0)
        if self.actor.user.is_admin:
            setup = menu()
            for title, action in (("Companies", partial(self.master_entry, "companies")),
                                  ("Users & access", self.user_screen), ("Audit log", self.audit_screen),
                                  ("Create backup", self.create_backup), ("Restore backup", self.restore_dialog)):
                command(setup, title, action)
            bar.add_cascade(label="Setup", menu=setup, underline=0)
        self.winfo_toplevel().configure(menu=bar)
        root = self.winfo_toplevel()
        def menu_key(event, letter=None):
            if not self._menu_blocked():
                if letter is None:
                    root.tk.call("tk::FirstMenu", root)
                else:
                    root.tk.call("tk::TraverseToMenu", root, letter)
            return "break"
        self._menu_bindings = []
        for letter in "fctirs":
            for key in (letter, letter.upper()):
                sequence = f"<Alt-KeyPress-{key}>"
                self._menu_bindings.append((sequence, root.bind(sequence, lambda event, value=letter: menu_key(event, value), add=True)))
        self._menu_bindings.append(("<F10>", root.bind("<F10>", menu_key, add=True)))
        def company_key(event):
            if not self._menu_blocked():
                keyboard_focus(self._company_choice)
            return "break"
        for sequence in ("<Control-l>", "<Control-L>"):
            self._menu_bindings.append((sequence, root.bind(sequence, company_key, add=True)))
        self._sync_menu_state()

    def shell(self, companies, permissions=None, *, view=None):
        self._clear()
        self.companies = companies
        self.permissions = permissions or {}
        if self.company_id not in [row[0] for row in companies]:
            self.company_id = companies[0][0] if companies else None
        self._build_menu()
        area = ttk.Frame(self)
        area.pack(fill="both", expand=True)
        top = ttk.Frame(area, padding=(22, 14))
        top.pack(fill="x")
        ttk.Label(top, text="Company", style="Muted.TLabel").pack(side="left", padx=(0, 8))
        choice = ttk.Combobox(top, values=[row[1] for row in companies], state="readonly", width=32)
        self._company_choice = choice
        choice.pack(side="left")
        if self.company_id:
            choice.current([row[0] for row in companies].index(self.company_id))
        def switch(event):
            if self.future is not None:
                if self.company_id is not None:
                    choice.current([row[0] for row in self.companies].index(self.company_id))
                return
            if choice.current() < 0:
                return
            self.company_id = self.companies[choice.current()][0]
            self.refresh_shell()
        choice.bind("<<ComboboxSelected>>", switch)
        ttk.Label(top, text=self.actor.user.display_name, style="Muted.TLabel").pack(side="right")
        ttk.Separator(area).pack(fill="x")
        ttk.Label(area, textvariable=self.status, style="Muted.TLabel", padding=(22, 10)).pack(side="bottom", fill="x")
        self.main = ttk.Frame(area, padding=22)
        self.main.pack(fill="both", expand=True)
        if view is not None:
            view()
        elif companies and "read" in self.permissions.get("dashboard", []):
            self.dashboard()
        elif companies:
            self.heading("Your company workspace", "Select an available module from the top menu. An administrator manages your company and module access.")
        else:
            self.heading("Set up your company", "An administrator can create a company, chart of accounts, units, stores and items.")
            if self.actor.user.is_admin:
                ttk.Button(self.main, text="Open setup", style="Accent.TButton", command=lambda: self.master_entry("companies")).pack(anchor="w", pady=20)

    def heading(self, title, hint=""):
        self._record_target = None
        self.document_rows, self.document_tree = [], None
        for child in self.main.winfo_children():
            child.destroy()
        ttk.Label(self.main, text=title, style="Heading.TLabel").pack(anchor="w")
        ttk.Label(self.main, text=hint, wraplength=min(820, max(500, self.winfo_width() - 60)), style="Muted.TLabel").pack(anchor="w", pady=(7, 18))

    def dashboard(self):
        if not self.company_id:
            return
        def render(data):
            baseline = data.get("baseline") or baseline_details(self.baseline)
            cutoff = baseline.get("cutoff_date")
            hint = (f"Balances continue from your existing records through {cutoff}. New transactions update these balances."
                    if cutoff else "New transactions · Drafts can be reviewed before posting.")
            self.heading(data["company"]["name"], hint)
            cards = ttk.Frame(self.main)
            cards.pack(fill="x", pady=(6, 10))
            original_count = data.get("document_counts", {}).get("original", 0)
            for title, number in (("Saved documents", data["posted"] + original_count), ("Draft documents", data["drafts"])):
                box = ttk.LabelFrame(cards, text=title, padding=18)
                box.pack(side="left", fill="x", expand=True, padx=(0, 16))
                ttk.Label(box, text=str(number), style="Heading.TLabel").pack(anchor="w")
            ttk.Label(self.main, text="Use the top menu to create documents, review stock and account balances, or maintain your company setup.", wraplength=700).pack(anchor="w", pady=(12, 16))
            if baseline:
                wrap = min(800, max(480, self.winfo_width() - 100))
                details = ttk.LabelFrame(self.main, text="Starting balances", padding=16)
                details.pack(fill="x")
                if "account_count" in baseline and "stock_count" in baseline:
                    ttk.Label(details, text=f"{baseline['account_count']:,} account balances and {baseline['stock_count']:,} stock balances carried into this company.").pack(anchor="w", pady=(0, 8))
                ttk.Label(details, text=f"Open sales, purchases or vouchers from the top menu to find existing records and add new entries. New entries must be dated after {cutoff}.",
                          style="Muted.TLabel", wraplength=wrap).pack(anchor="w")
                anomalies = baseline.get("manifest", {}).get("anomalies", {})
                notes = []
                negative = anomalies.get("negative_stock_groups", 0)
                if negative:
                    notes.append(f"{negative:,} negative stock balances")
                mismatch = anomalies.get("unit_mismatch_rows", 0)
                if mismatch:
                    notes.append(f"{mismatch:,} stock rows with differing units")
                if anomalies.get("unknown_sale_rates", 0) or anomalies.get("unknown_purchase_rates", 0):
                    notes.append("items with no saved price")
                if notes:
                    ttk.Label(details, text="Original data to review across all companies: " + "; ".join(notes) + ". Original quantities and missing prices are preserved.",
                              wraplength=wrap).pack(anchor="w", pady=(8, 0))
                ttk.Button(details, text="View starting balance details", command=self.baseline_dialog).pack(anchor="w", pady=(12, 0))
        def fetch():
            data = self.reports.dashboard(self.actor, self.company_id)
            data["document_counts"] = self.register.document_counts(self.actor, self.company_id)
            return data
        self.run(fetch, render)

    def baseline_dialog(self):
        info = baseline_details(self.baseline)
        if not info:
            return
        dialog, body = self.dialog("Starting balances", width=820, height=650)
        manifest = info.get("manifest", {})
        ttk.Label(body, text=f"Existing records included through {info['cutoff_date']}. New documents must be dated later.",
                  wraplength=730).pack(anchor="w", pady=10)
        for policy in manifest.get("policies", []):
            ttk.Label(body, text=policy, style="Muted.TLabel", wraplength=730).pack(anchor="w", pady=6)
        issues = manifest.get("anomalies", {})
        titles = (("negative_stock_groups", "Negative stock balances"),
                  ("unit_mismatch_rows", "Original stock rows with different units"),
                  ("unit_mismatch_products", "Items affected by differing units"),
                  ("unknown_sale_rates", "Items with no saved sale price"),
                  ("unknown_purchase_rates", "Items with no saved purchase price"),
                  ("inactive_observed_store_memberships", "Historical stores requiring review before use"),
                  ("unknown_account_postability", "Accounts whose posting status needs review"))
        for key, title in titles:
            if key in issues:
                ttk.Label(body, text=f"{title}: {issues[key]:,}").pack(anchor="w", pady=5)
        ttk.Button(body, text="Close", command=dialog.destroy).pack(anchor="e", pady=15)

    def home(self):
        if "read" in self.permissions.get("dashboard", []):
            self.dashboard()
            return
        for kind, (_, module) in KINDS.items():
            if "read" in self.permissions.get(module, []):
                self.transaction_entry(kind)
                return
        self.heading("Your company workspace", "Select an available module from the top menu.")

    @staticmethod
    def tree(parent, columns, rows, *, height=14):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=[key for key, _ in columns], show="headings", height=height, selectmode="browse")
        vertical = ttk.Scrollbar(frame, command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        heading_font = tkfont.Font(root=parent, font=ttk.Style(parent).lookup("Treeview.Heading", "font"))
        for key, title in columns:
            tree.heading(key, text=title)
            minimum = max(95, heading_font.measure(title) + 28)
            tree.column(key, width=max(150, minimum), minwidth=minimum, anchor="w")
        for index, row in enumerate(rows):
            tree.insert("", "end", iid=str(index), values=[value_text(row.get(key)) for key, _ in columns])
        return tree

    def _entry_f8(self, event=None):
        # Bind on this application's root, never bind_all: child dialogs have
        # their own toplevel binding and other Tk applications are unrelated.
        if self._record_target is not None and self.grab_current() is None:
            self._show_saved_records(self._record_target)
        return "break"

    def _show_saved_records(self, target, *, owner=None):
        if self.future is not None or self.close_pending or self.actor is None:
            return "break"
        if self._records_browser is not None and self._records_browser[0].winfo_exists():
            return "break"
        if self.grab_current() not in (None, owner):
            return "break"
        family, key = target
        title = MASTER_LABELS[key] if family == "master" else KINDS[key][0]
        focus = self.focus_get()
        dialog, outer = self.dialog("Saved records · " + title, width=1050, height=750)
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        browser = dialog, body
        self._records_browser = browser
        def back():
            if self.future is not None:
                self.status.set("Finish the current operation before closing this window.")
                return "break"
            dialog.destroy()
            if focus is not None and focus.winfo_exists():
                focus.focus_force()
            elif owner is not None and owner.winfo_exists():
                keyboard_initial_focus(owner).focus_force()
            return "break"
        def destroyed(event):
            if event.widget is dialog and self._records_browser == browser:
                self._records_browser = None
                self.document_rows, self.document_tree = [], None
        dialog.bind("<Destroy>", destroyed, add="+")
        dialog.bind("<F8>", lambda event: "break")
        dialog.bind("<Escape>", lambda event: back())
        dialog.protocol("WM_DELETE_WINDOW", back)
        footer = ttk.Frame(dialog, padding=(15, 8))
        footer.pack(side="bottom", fill="x", before=dialog.winfo_children()[0])
        ttk.Button(footer, text="Back to entry", command=back).pack(side="right")
        if family == "master":
            self.master_screen(key, browser=browser)
        else:
            self.document_list(key, browser=browser)
        return "break"

    def _entry_controls(self, dialog, body, target):
        command = partial(self._show_saved_records, target, owner=dialog)
        dialog.bind("<F8>", lambda event: command())
        ttk.Button(body, text="Saved records (F8)", command=command).pack(anchor="e", padx=15, pady=(0, 8))

    def _entry_page(self, title, section, target, create=None):
        self._show_navigation(section)
        self.heading(title, "New entry · Press F8 to view saved records.")
        self._record_target = target
        bar = ttk.Frame(self.main)
        bar.pack(fill="x", pady=8)
        if create is not None:
            ttk.Button(bar, text="New entry", style="Accent.TButton", command=create).pack(side="left")
        else:
            ttk.Label(self.main, text="You can view saved records. New entry requires creation permission.",
                      style="Muted.TLabel").pack(anchor="w", pady=10)
        saved_button = ttk.Button(bar, text="Saved records (F8)", command=partial(self._show_saved_records, target))
        saved_button.pack(side="right")
        if create is not None:
            create()
        else:
            keyboard_focus(saved_button)

    def master_entry(self, entity):
        if self.future is not None or self.actor is None:
            return
        section = next(title for title, entities in MASTER_SECTIONS.items() if entity in entities)
        create = partial(self.master_dialog, entity, entry=True) if self.actor.user.is_admin else None
        self._entry_page(MASTER_LABELS[entity], section, ("master", entity), create)

    def transaction_entry(self, kind):
        if kind not in KINDS:
            raise ValueError("This document type is not available for entry.")
        if self.future is not None or self.actor is None or not self.company_id:
            return
        title, module = KINDS[kind]
        section = next(name for name, kinds in DOCUMENT_SECTIONS.items() if kind in kinds)
        create = (partial(self.document_editor, kind, entry=True, return_to=partial(self.transaction_entry, kind))
                  if "create" in self.permissions.get(module, []) else None)
        self._entry_page(title, section, ("document", kind), create)

    def master_screen(self, entity="companies", search="", offset=0, *, section=None, browser=None, focus_results=False):
        section = section or next(title for title, entities in MASTER_SECTIONS.items() if entity in entities)
        entities = MASTER_SECTIONS[section]
        if entity not in entities:
            raise ValueError("This record belongs to a different menu section.")
        show = partial(self.master_screen, entity, section=section, browser=browser)
        def render(rows):
            if browser is not None:
                if not browser[0].winfo_exists():
                    return
                target = browser[1]
                for widget in target.winfo_children():
                    widget.destroy()
            else:
                self._show_navigation(section)
                self.heading(MASTER_LABELS[entity], "Search existing records, add new records or open a record to update its details.")
                target = self.main
            bar = ttk.Frame(target)
            bar.pack(fill="x", pady=(0, 12))
            if browser is None:
                entity_box = ttk.Combobox(bar, values=[MASTER_LABELS[key] for key in entities], state="readonly", width=20)
                entity_box.current(entities.index(entity))
                entity_box.pack(side="left", padx=(0, 8))
                entity_box.bind("<<ComboboxSelected>>", lambda e: self.master_entry(entities[entity_box.current()]))
            else:
                ttk.Label(bar, text="Search", style="Muted.TLabel").pack(side="left", padx=(0, 8))
            query = owned_variable(bar, value=search)
            query_field = ttk.Entry(bar, textvariable=query, width=20)
            query_field.pack(side="left")
            def search_records(event=None):
                show(search=query.get(), focus_results=True)
                return "break"
            query_field.bind("<Return>", search_records)
            query_field.bind("<KP_Enter>", search_records)
            ttk.Button(bar, text="Search", command=search_records).pack(side="left", padx=6)
            if browser is None:
                ttk.Button(bar, text="New", style="Accent.TButton", command=lambda: self.master_dialog(entity)).pack(side="right")
            columns = [("code", "Code"), ("name", "Name"), ("is_active", "Active")]
            if entity == "company_stores":
                columns = [("company_name", "Company"), ("store_name", "Store"), ("is_active", "Active")]
            elif entity in GROUP_SECTIONS:
                columns = [("code", "Code"), ("name", "Name"), ("name_urdu", "Urdu name")]
                if entity == "sub_item_groups":
                    columns.append(("parent_name", "Main item group"))
            elif entity == "products":
                columns += [("sale_price_display", "Sale price"), ("purchase_price_display", "Purchase price"),
                            ("unit_review", "Original units")]
            bottom = ttk.Frame(target)
            bottom.pack(side="bottom", fill="x", pady=10)
            tree = self.tree(target, columns, rows, height=12 if browser else 14)
            def edit(event=None):
                selected = tree.selection()
                if selected:
                    self.master_dialog(entity, rows[int(selected[0])],
                                       on_saved=(lambda result: show(search=search, offset=offset, focus_results=True)) if browser else None)
                return "break"
            tree.bind("<Double-1>", edit)
            tree.bind("<Return>", edit)
            tree.bind("<KP_Enter>", edit)
            ttk.Button(bottom, text="Edit selected", command=edit).pack(side="left")
            ttk.Button(bottom, text="Previous", state="normal" if offset else "disabled", command=lambda: show(search=search, offset=max(0, offset - 100), focus_results=True)).pack(side="right")
            ttk.Button(bottom, text="Next", state="normal" if len(rows) == 100 else "disabled", command=lambda: show(search=search, offset=offset + 100, focus_results=True)).pack(side="right", padx=8)
            if focus_results and rows:
                tree.selection_set("0")
                tree.focus("0")
                keyboard_focus(tree)
            else:
                keyboard_focus(query_field)
        def fetch():
            storage = "product_groups" if entity in GROUP_SECTIONS else entity
            options = {"group_section": GROUP_SECTIONS[entity]} if entity in GROUP_SECTIONS else {}
            rows = self.masters.list_records(self.actor, storage, search=search, limit=100, offset=offset, **options)
            if entity == "company_stores":
                companies = {row["id"]: label(row) for row in self._all_masters("companies")}
                stores = {row["id"]: label(row) for row in self._all_masters("stores")}
                rows = [dict(row, company_name=companies.get(row["company_id"], ""), store_name=stores.get(row["store_id"], "")) for row in rows]
            elif entity == "products":
                issues = baseline_details(self.baseline).get("manifest", {}).get("product_issues", {})
                rows = [dict(row,
                             sale_price_display=value_text(stored_rate(row, "sale")) if stored_rate(row, "sale") is not None else "Not recorded",
                             purchase_price_display=value_text(stored_rate(row, "purchase")) if stored_rate(row, "purchase") is not None else "Not recorded",
                             unit_review="Review differing units" if row["id"] in issues else "") for row in rows]
            elif entity == "sub_item_groups":
                parents = {row["id"]: label(row) for row in self._all_masters("product_groups")}
                rows = [dict(row, parent_name=parents.get(row["parent_id"], "")) for row in rows]
            return rows
        self.run(fetch, render)

    def master_dialog(self, entity, record=None, *, entry=False, on_saved=None):
        fields = MASTER_FIELDS[entity]
        sources = {kind for _, _, kind in fields if isinstance(kind, str) and kind in MASTER_LABELS}
        def fetch():
            self.security.require_admin(self.actor)
            choices = {}
            for source in sources:
                choices[source] = self._all_masters(source)
            if entity == "products" and record is None:
                choices["suggested_item_code"] = self.masters.next_product_code(self.actor)
            return choices
        def render(choices):
            dialog, body = self.dialog(("Edit " if record else "New ") + MASTER_LABELS[entity], width=770, height=min(780, 220 + ((len(fields)+1)//2) * 78))
            dialog._focus_entry = True
            if entry:
                self._entry_controls(dialog, body, ("master", entity))
            initial = dict(record or {})
            if entity == "sub_item_groups":
                if not choices["main_item_groups"]:
                    ttk.Label(body, text="Create a Main Item Group before saving a sub group.",
                              style="Muted.TLabel", wraplength=690).pack(anchor="w", padx=15, pady=8)
                elif record and record["parent_id"] not in {row["id"] for row in choices["main_item_groups"]}:
                    ttk.Label(body, text="This saved group's parent is not a main item group. Review its hierarchy before saving changes.",
                              style="Muted.TLabel", wraplength=690).pack(anchor="w", padx=15, pady=8)
            if entity == "products":
                if record is None:
                    initial["code"] = choices["suggested_item_code"]
                    ttk.Label(body, text="Item code is assigned automatically when you save.",
                              style="Muted.TLabel", wraplength=690).pack(anchor="w", padx=15, pady=8)
                for key in ("sale_rate", "purchase_rate"):
                    if not record or not record.get(key + "_known", 1):
                        initial[key] = ""
                issue = baseline_details(self.baseline).get("manifest", {}).get("product_issues", {}).get(initial.get("id"))
                if issue:
                    ttk.Label(body, text=f"{issue['unit_mismatch_rows']:,} original stock rows use units different from this item's current base unit. Starting quantities preserve the original report; review the item's earlier records before changing its setup.",
                              wraplength=690).pack(anchor="w", padx=15, pady=8)
                ttk.Label(body, text="Leave a price blank when it has not been recorded. Zero is an actual price of zero.",
                          style="Muted.TLabel", wraplength=690).pack(anchor="w", padx=15, pady=8)
            form = Form(body, fields, values=initial, choices=choices, enter_navigation=True)
            form.frame.pack(fill="x")
            if entity == "products":
                form.widgets["code"].configure(state="readonly")
            dialog._initial_focus = form.first_widget()
            def save():
                values = form.values()
                storage = "product_groups" if entity in GROUP_SECTIONS else entity
                options = {"group_section": GROUP_SECTIONS[entity]} if entity in GROUP_SECTIONS else {}
                if entity == "products":
                    values.pop("code")
                if record:
                    identifier = (record["company_id"], record["store_id"]) if entity == "company_stores" else record["id"]
                    task = partial(self.masters.update, self.actor, storage, identifier, values, record["version"], **options)
                elif entity == "products":
                    task = partial(self.masters.create_product, self.actor, values)
                else:
                    task = partial(self.masters.create, self.actor, storage, values, **options)
                def done(result):
                    dialog.destroy()
                    if on_saved is not None and entity == "companies":
                        self.refresh_company_choices(partial(on_saved, result))
                    elif on_saved is not None:
                        on_saved(result)
                    elif entry and entity == "companies":
                        self.refresh_shell(view=partial(self.master_entry, entity))
                    elif entry:
                        self.master_entry(entity)
                    elif entity == "companies":
                        self.refresh_shell()
                    elif entity == "products":
                        self.master_screen(entity, search=result["code"])
                    else:
                        self.master_screen(entity)
                self.run(task, done)
            save_button = ttk.Button(body, text="Save changes", style="Accent.TButton", command=save)
            save_button.pack(anchor="e", padx=17, pady=12)
            form.set_enter_target(save_button)
        self.run(fetch, render)

    def _all_masters(self, entity):
        result = []
        storage = "product_groups" if entity in GROUP_SECTIONS else entity
        options = {"group_section": GROUP_SECTIONS[entity]} if entity in GROUP_SECTIONS else {}
        while True:
            page = self.masters.list_records(self.actor, storage, limit=100, offset=len(result), **options)
            result.extend(page)
            if len(page) < 100:
                return result

    def dialog(self, title, *, width=850, height=700, scrollable=True):
        previous_grab = self.grab_current()
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.geometry(f"{width}x{height}")
        dialog.minsize(min(width, 640), min(height, 420))
        dialog.transient(self.winfo_toplevel())
        def dismiss():
            if self.future is None:
                dialog.destroy()
            else:
                self.status.set("Finish the current operation before closing this dialog.")
        dialog.protocol("WM_DELETE_WINDOW", dismiss)
        frame = ttk.Frame(dialog, padding=15)
        frame.pack(fill="both", expand=True)
        if not scrollable:
            _prepare_modal(dialog, self, previous_grab=previous_grab)
            return dialog, frame
        ttk.Label(frame, text=title, style="Title.TLabel").pack(anchor="w", pady=(0, 10))
        canvas = tk.Canvas(frame, bg="white", highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        body = ttk.Frame(canvas)
        canvas._keyboard_body = body
        window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        _prepare_modal(dialog, self, previous_grab=previous_grab)
        return dialog, body

    def document_list(self, kind, search=None, offset=None, *, date_from=None, date_to=None, browser=None,
                      focus_results=False):
        if kind not in KINDS:
            raise ValueError("This document type is not available in the application menu.")
        if not self.company_id:
            return
        filter_key = (self.company_id, kind)
        previous = self._document_filters.get(filter_key, {})
        automatic_dates = (browser is not None and kind == "sale" and (
            previous.get("_auto_dates", False) or (not previous and search is None
            and offset is None and date_from is None and date_to is None)))
        if search is None and offset is None and date_from is None and date_to is None:
            search, offset = previous.get("search", ""), previous.get("offset", 0)
            date_from, date_to = previous.get("date_from"), previous.get("date_to")
        search, offset = search or "", offset or 0
        filters = {"search": search, "offset": offset, "date_from": date_from or None, "date_to": date_to or None}
        show = partial(self.document_list, kind, browser=browser, focus_results=True)
        def render(rows):
            if browser is not None:
                if not browser[0].winfo_exists():
                    return
                target = browser[1]
                for widget in target.winfo_children():
                    widget.destroy()
            else:
                self._show_navigation(next(title for title, kinds in DOCUMENT_SECTIONS.items() if kind in kinds))
                self.heading(KINDS[kind][0], "Save a draft, review its lines and totals, then post. Posted documents keep their original details.")
                target = self.main
            self._document_filters[filter_key] = {**filters, "_auto_dates": automatic_dates}
            bar = ttk.Frame(target)
            bar.pack(fill="x", pady=(0, 12))
            query = owned_variable(bar, value=search)
            ttk.Label(bar, text="Search", style="Muted.TLabel").pack(side="left", padx=(0, 6))
            entry = ttk.Entry(bar, textvariable=query, width=28)
            entry.pack(side="left")
            def apply_filters(event=None):
                self._document_filters[filter_key]["_auto_dates"] = False
                show(query.get(), 0, date_from=lower.get().strip(), date_to=upper.get().strip())
                return "break"
            def clear_filters():
                self._document_filters[filter_key]["_auto_dates"] = False
                show("", 0)
            ttk.Button(bar, text="Search", command=apply_filters).pack(side="left", padx=8)
            for sequence in ("<Return>", "<KP_Enter>"):
                entry.bind(sequence, apply_filters)
            if browser is None and "create" in self.permissions.get(KINDS[kind][1], []):
                ttk.Button(bar, text="New document", style="Accent.TButton", command=lambda: self.document_editor(kind)).pack(side="right")
            dates = ttk.Frame(target)
            dates.pack(fill="x", pady=(0, 12))
            lower = owned_variable(dates, value=date_from or "")
            upper = owned_variable(dates, value=date_to or "")
            date_controls = []
            for title, variable in (("Date From" if kind == "sale" else "From", lower),
                                    ("Date To" if kind == "sale" else "To", upper)):
                ttk.Label(dates, text=title, style="Muted.TLabel").pack(side="left", padx=(0, 6))
                control = ttk.Entry(dates, textvariable=variable, width=13)
                control.pack(side="left", padx=(0, 12))
                date_controls.append(control)
            for sequence in ("<Return>", "<KP_Enter>"):
                date_controls[0].bind(sequence, lambda event: Form._focus(date_controls[1]))
                date_controls[1].bind(sequence, apply_filters)
            ttk.Label(dates, text="YYYY-MM-DD · Blank = all dates", style="Muted.TLabel").pack(side="left")
            ttk.Button(dates, text="Clear filters", command=clear_filters).pack(side="right")
            bottom = ttk.Frame(target)
            bottom.pack(side="bottom", fill="x", pady=10)
            statuses = {"draft": "Draft", "posted": "Posted", "original": "Recorded", "historical": "Recorded",
                        "recorded": "Recorded", "cancelled": "Cancelled", "reversed": "Reversed"}
            shown = [{**row, "status_display": row.get("status_display") or statuses.get(row.get("status"), value_text(row.get("status")))} for row in rows]
            tree = self.tree(target, [("document_number", "Number"), ("business_date", "Date"), ("account", "Account / customer"), ("status_display", "Status"), ("total_amount", "Total")], shown, height=12 if browser else 14)
            self.document_rows, self.document_tree = rows, tree
            def open_selected(event=None):
                selection = tree.selection()
                if selection:
                    row = rows[int(selection[0])]
                    self.open_document(row["id"], source=row.get("source", "current"), kind=kind,
                                       return_to=partial(show, **filters) if browser else None)
                return "break"
            tree.bind("<Double-1>", open_selected)
            tree.bind("<Return>", open_selected)
            tree.bind("<KP_Enter>", open_selected)
            ttk.Button(bottom, text="Open selected", command=open_selected).pack(side="left")
            ttk.Label(bottom, text=f"{offset + 1 if rows else 0}–{offset + len(rows)}", style="Muted.TLabel").pack(side="left", padx=12)
            ttk.Button(bottom, text="Next", state="normal" if len(rows) == 100 else "disabled", command=lambda: show(search, offset+100, date_from=date_from, date_to=date_to)).pack(side="right")
            ttk.Button(bottom, text="Previous", state="normal" if offset else "disabled", command=lambda: show(search, max(0, offset-100), date_from=date_from, date_to=date_to)).pack(side="right", padx=8)
            if rows:
                tree.selection_set("0")
                tree.focus("0")
            focus = tree if focus_results and rows else entry
            if browser is not None:
                # The browser may map while its first read is still running;
                # its Back button must not retain focus after fields arrive.
                browser[0]._initial_focus = focus
            Form._focus(focus)
        def fetch():
            nonlocal date_from, date_to, filters
            if automatic_dates:
                # Refresh suggested bounds after a new voucher is saved;
                # ranges explicitly searched or cleared remain the user's choice.
                bounds = self.register.date_bounds(self.actor, self.company_id, kind)
                date_from, date_to = bounds["date_from"], bounds["date_to"]
                filters = {**filters, **bounds}
            return self.register.list_documents(self.actor, self.company_id, kind, limit=100, **filters)
        self.run(fetch, render)

    def open_document(self, identifier, *, source="current", kind=None, return_to=None):
        list_kind = kind
        def render(document):
            if document.get("source") == "original":
                self.original_document_dialog(document)
                return
            document["_list_kind"] = list_kind or document["kind"]
            if document["status"] == "draft":
                self.review_draft(document, return_to=return_to)
            else:
                self.posted_dialog(document, return_to=return_to)
        fetch = (partial(self.register.get, self.actor, self.company_id, kind, identifier, source=source)
                 if kind is not None else partial(self.transactions.get, self.actor, self.company_id, identifier))
        self.run(fetch, render)

    def voucher_printing(self):
        self._show_navigation("Reports")
        self.heading("Voucher Printing", "Choose a section, find a saved voucher, then open it and select Save printable copy.")
        if not self.company_id:
            return
        choices = [(kind, title) for kind, (title, module) in KINDS.items()
                   if "read" in self.permissions.get(module, [])]
        if not choices:
            ttk.Label(self.main, text="No document sections are available for this account.").pack(anchor="w")
            return
        bar = ttk.Frame(self.main)
        bar.pack(fill="x", pady=12)
        ttk.Label(bar, text="Document section").pack(side="left", padx=(0, 10))
        selected = ttk.Combobox(bar, values=[title for _, title in choices], state="readonly", width=32)
        selected.current(0)
        selected.pack(side="left", padx=(0, 10))
        def set_section(event=None):
            if selected.current() >= 0:
                self._record_target = ("document", choices[selected.current()][0])
        def browse(event=None):
            set_section()
            if self._record_target is not None:
                self._show_saved_records(self._record_target)
            return "break"
        selected.bind("<<ComboboxSelected>>", set_section)
        selected.bind("<Return>", browse)
        selected.bind("<KP_Enter>", browse)
        ttk.Button(bar, text="Find vouchers (F8)", command=browse).pack(side="left")
        ttk.Label(self.main, text="Original recorded vouchers and new posted vouchers can be printed. Drafts must be posted first.",
                  style="Muted.TLabel", wraplength=780).pack(anchor="w", pady=8)
        set_section()
        selected.focus_set()

    def original_document_dialog(self, document):
        dialog, body = self.dialog(document["title"], width=1000, height=740)
        ttk.Label(body, text="Saved document · Values as originally recorded.", style="Muted.TLabel").pack(anchor="w", pady=(0, 12))
        notebook = ttk.Notebook(body)
        notebook.pack(fill="both", expand=True)
        overview = ttk.Frame(notebook, padding=14)
        notebook.add(overview, text="Document details")
        for column in range(2):
            overview.columnconfigure(column, weight=1, uniform="details")
        for index, (title, value) in enumerate(document["fields"]):
            field = ttk.Frame(overview, padding=(6, 8))
            field.grid(row=index // 2, column=index % 2, sticky="nsew")
            ttk.Label(field, text=title, style="Muted.TLabel").pack(anchor="w")
            ttk.Label(field, text=value_text(value) or "—", wraplength=390, justify="left").pack(anchor="w", pady=(4, 0))
        if document.get("notes"):
            ttk.Label(overview, text="\n".join(document["notes"]), style="Muted.TLabel", wraplength=810,
                      justify="left").grid(row=(len(document["fields"]) + 1) // 2, column=0, columnspan=2, sticky="w", pady=12)
        lines = ttk.Frame(notebook, padding=10)
        notebook.add(lines, text=f"Line items ({len(document['rows']):,})")
        self.tree(lines, document["columns"], document["rows"], height=12)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=14)
        ttk.Button(actions, text="Close", command=dialog.destroy).pack(side="left")
        ttk.Button(actions, text="Save printable copy", command=lambda: self.export_document(document)).pack(side="right")
        ttk.Button(actions, text="Bill / print preview", command=lambda: self.preview_document(document)).pack(side="right", padx=8)
        self._bind_document_print(dialog, document)

    def document_editor(self, kind, document=None, *, entry=False, return_to=None, print_after_post=False):
        if kind not in KINDS:
            raise ValueError("This document type is not available for entry.")
        company, actor = self.company_id, self.actor
        def fetch():
            choices = self.masters.lookup(actor, company, KINDS[kind][1], action="update" if document else "create")
            if kind in {"cash_receipt", "cash_payment", "sale"}:
                numbering = self.transactions.document_numbering(actor, company, kind, action="update" if document else "create")
                choices["suggested_document_number"] = numbering["next"]
                choices["last_document_number"] = numbering["last"]
            elif document is None:
                choices["suggested_document_number"] = self.transactions.next_document_number(actor, company, kind)
            if kind == "sale" and "read" in self.permissions.get("stock", []):
                cutoff = baseline_details(self.baseline).get("cutoff_date")
                stock_date = max(date.today(), date.fromisoformat(cutoff)) if cutoff else date.today()
                try:
                    choices["stock_balances"] = self.transactions.stock_balance(actor, company, date_to=stock_date.isoformat())
                    choices["stock_date"] = stock_date.isoformat()
                except AuthorizationError:
                    # Stock is optional display data; losing this separate
                    # read grant does not remove an authorized sale form.
                    pass
            return choices
        def render(choices):
            self._editor(kind, company, actor, choices, document, entry=entry, return_to=return_to,
                         print_after_post=print_after_post)
        self.run(fetch, render)

    def _cash_editor(self, kind, company, actor, choices, document, values, *, entry=False,
                     return_to=None, print_after_post=False):
        from bifma.cash_voucher_ui import CashVoucherForm
        dialog, body = self.dialog(("Edit draft · " if document else "New · ") + KINDS[kind][0],
                                   width=1040, height=650, scrollable=False)
        dialog.minsize(790, 540)
        dialog._focus_entry = True
        target = ("document", kind)
        records = partial(self._show_saved_records, target, owner=dialog)
        dialog.bind("<F8>", lambda event: records())
        company_record = next((row for row in choices.get("companies", []) if row["id"] == company), {})
        def raw_values():
            return ({key: value.get() for key, value in form.inputs.items()},
                    [tuple(control.get() for control in row.inputs.values()) for row in form.rows
                     if any(control.get() for control in row.inputs.values())])
        def discard():
            if self.future is not None:
                return False
            return raw_values() == original or messagebox.askyesno(
                "Unsaved voucher", "Discard the changes in this form? Saved drafts remain available through F8.", parent=dialog)
        def close():
            if discard():
                dialog.destroy()
        def new():
            if discard():
                dialog.destroy()
                self.document_editor(kind, entry=entry, return_to=return_to)
        def save(payload, printing=False):
            options = {"document_id": document["id"], "expected_revision": document["revision"]} if document else {}
            def saved(result):
                dialog.destroy()
                self.review_draft(result, return_to=return_to, entry=entry,
                                  print_after_post=printing or print_after_post)
            self.run(partial(self.transactions.save_draft, actor, company, kind, payload, **options), saved)
        form = CashVoucherForm(body, kind=kind, accounts=choices.get("accounts", []),
                               company=company_record, values=values, choice_factory=Choice,
                               last_number=choices.get("last_document_number", ""),
                               on_save=save, on_print=lambda payload: save(payload, printing=True),
                               on_new=new, on_exit=close, on_records=records)
        form.pack(fill="both", expand=True)
        dialog.cash_form = form
        original = raw_values()
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog._initial_focus = form.widgets["document_number"]
        for sequence in ("<Control-s>", "<Control-S>"):
            dialog.bind(sequence, form.save_from_keyboard)

    def _sale_editor(self, company, actor, choices, document, values, *, entry=False,
                     return_to=None, print_after_post=False):
        from bifma.sale_voucher_ui import SaleVoucherForm
        dialog, body = self.dialog(("Edit draft · " if document else "New · ") + KINDS["sale"][0],
                                   width=1180, height=700, scrollable=False)
        dialog.minsize(850, 610)
        dialog._focus_entry = True
        target = ("document", "sale")
        records = partial(self._show_saved_records, target, owner=dialog)
        dialog.bind("<F8>", lambda event: records())
        company_record = next((row for row in choices.get("companies", []) if row["id"] == company), {})
        stock = {(row["product_id"], row["store_id"]): row["quantity"]
                 for row in choices.get("stock_balances", [])}
        def show_stock(row):
            product, store = row.inputs["product_id"].get(), row.inputs["store_id"].get()
            if "stock_date" in choices and product is not None and store:
                form.set_stock(stock.get((product, store), "0"), caption="Stock · " + choices["stock_date"])
            else:
                form.set_stock("—", caption="Stock")
        def discard():
            if self.future is not None:
                return False
            return form.raw_values() == original or messagebox.askyesno(
                "Unsaved sale voucher", "Discard the changes in this form? Saved drafts remain available through F8.", parent=dialog)
        def close():
            if discard():
                dialog.destroy()
        def new():
            if discard():
                dialog.destroy()
                self.document_editor("sale", entry=entry, return_to=return_to)
        def save(payload, printing=False):
            options = {"document_id": document["id"], "expected_revision": document["revision"]} if document else {}
            def saved(result):
                dialog.destroy()
                self.review_draft(result, return_to=return_to, entry=entry,
                                  print_after_post=printing or print_after_post)
            self.run(partial(self.transactions.save_draft, actor, company, "sale", payload, **options), saved)
        form = SaleVoucherForm(body, choices=choices, company=company_record, values=values,
                               choice_factory=Choice, on_save=save,
                               last_number=choices.get("last_document_number", ""),
                               on_print=lambda payload: save(payload, printing=True),
                               on_new=new, on_exit=close, on_records=records,
                               on_item_focus=show_stock,
                               product_issues=baseline_details(self.baseline).get("manifest", {}).get("product_issues", {}))
        form.pack(fill="both", expand=True)
        dialog.sale_form = form
        original = form.raw_values()
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog._initial_focus = form.widgets["document_number"]
        for sequence in ("<Control-s>", "<Control-S>"):
            dialog.bind(sequence, form.save_from_keyboard)

    def _editor(self, kind, company, actor, choices, document, *, entry=False, return_to=None, print_after_post=False):
        title = KINDS[kind][0]
        cutoff = baseline_details(self.baseline).get("cutoff_date")
        business_date = max(date.today(), date.fromisoformat(cutoff) + timedelta(days=1)) if cutoff else date.today()
        values = dict(document["values"]) if document else {"business_date": business_date.isoformat(),
                 "document_number": choices.get("suggested_document_number", ""),
                 "rounding_policy": "legacy_whole", "posting_places": "0"}
        configured_cash = next((row.get("cash_account_id") for row in choices.get("companies", []) if row["id"] == company), None)
        if kind in CASH_INVOICES | {"cash_receipt", "cash_payment"} and document is None:
            values["account_id"] = configured_cash
        if kind in {"cash_receipt", "cash_payment"}:
            if document is None:
                # Cash amounts accept whole units or two decimal places. The
                # voucher service validates precision without rounding lines.
                values["posting_places"] = "2"
            self._cash_editor(kind, company, actor, choices, document, values, entry=entry,
                              return_to=return_to, print_after_post=print_after_post)
            return
        if kind == "sale":
            self._sale_editor(company, actor, choices, document, values, entry=entry,
                              return_to=return_to, print_after_post=print_after_post)
            return
        dialog, body = self.dialog(("Edit draft · " if document else "New · ") + title, width=990, height=780)
        dialog._focus_entry = True
        if entry:
            self._entry_controls(dialog, body, ("document", kind))
        fields = [("document_number", "Document number", "text"), ("business_date", "Date (YYYY-MM-DD)", "text"),
                  ("rounding_policy", "Subtotal rounding", ("legacy_whole", "cents")), ("posting_places", "Final decimal places", ("0", "2"))]
        if kind not in JOURNALS:
            fields += [("contra_account_id" if kind == "stock_opening" else "account_id", "Opening balance counterpart" if kind == "stock_opening" else "Cash account" if kind in CASH_TRADES else "Party account" if kind in TRADE else "Cash account", "accounts")]
        if kind in CASH_INVOICES:
            party_label = "Customer" if kind == "cash_sale" else "Supplier"
            fields += [("counterparty_name", party_label + " name (optional)", "text"), ("counterparty_mobile", party_label + " mobile (optional)", "text")]
        fields += [("notes", "Notes", "text")]
        if kind in TRADE - BREAKAGE:
            fields += [("discount", "Header discount", "number"), ("breakage", "Breakage allowance", "number"), ("freight", "Freight", "number")]
        elif kind == "breakage_receive":
            fields += [("breakage", "Breakage allowance", "number")]
        header = Form(body, fields, values=values, choices=choices, columns=3, enter_navigation=True)
        header.frame.pack(fill="x")
        dialog._initial_focus = header.first_widget()
        if cutoff:
            ttk.Label(body, text=f"Starting balances include records through {cutoff}. This document must use a later date.",
                      style="Muted.TLabel", wraplength=850).pack(anchor="w", padx=15, pady=(0, 8))
        if kind in RETURN_ORIGINALS:
            ttk.Label(body, text="Enter the discount, breakage allowance and freight for this return. Refunds across linked returns cannot exceed the original invoice total.",
                      style="Muted.TLabel", wraplength=850).pack(anchor="w", padx=15, pady=(0, 8))
        if kind in CASH_TRADES:
            header.widgets["account_id"].configure(state="disabled")
            if kind in CASH_INVOICES:
                def use_company_cash():
                    selected = header.inputs["account_id"]
                    account = next((row for row in choices.get("accounts", []) if row["id"] == configured_cash and row.get("kind") == "cash"), None)
                    if account is None or configured_cash not in selected.ids:
                        messagebox.showerror("Cash account unavailable", "Configure an active cash account in the company setup, then reopen this draft for editing.", parent=dialog)
                        return
                    selected.widget.current(selected.ids.index(configured_cash))
                ttk.Button(body, text="Use company cash account", command=use_company_cash).pack(anchor="w", padx=15, pady=(0, 8))
            ttk.Label(body, text="The full invoice total posts directly to cash. Cash and account invoices have separate document numbers." if kind in CASH_INVOICES else "Choose a cash invoice line. The refund uses that invoice's saved cash account.", style="Muted.TLabel", wraplength=850).pack(anchor="w", padx=15, pady=(0, 8))
        lines = [dict(line) for line in values.get("lines", [])]
        ttk.Label(body, text="Item quantities and rates use the product’s base unit." if kind in TRADE | {"stock_opening"} else "Enter the accounts and amounts for this voucher.", style="Muted.TLabel").pack(anchor="w", padx=15, pady=8)
        if kind in BREAKAGE:
            ttk.Label(body, text="This standalone entry affects breakage stock. Freight and header discounts are unavailable for these entries.", style="Muted.TLabel", wraplength=850).pack(anchor="w", padx=15, pady=(0, 8))
            if kind == "breakage_receive":
                ttk.Label(body, text="Use the same receiving store on every line.", style="Muted.TLabel").pack(anchor="w", padx=15, pady=(0, 8))
        if kind in TRADE:
            columns = [("product", "Item"), ("store", "Store"), ("quantity", "Qty"), ("rate", "Rate"), ("discount_percent", "Discount %")]
        elif kind == "stock_opening":
            columns = [("product", "Item"), ("store", "Store"), ("quantity", "Qty"), ("value", "Opening value"), ("pool", "Stock pool")]
        else:
            columns = [("account", "Account"), ("debit", "Debit"), ("credit", "Credit")] if kind in JOURNALS else [("account", "Account"), ("amount", "Amount"), ("narration", "Narration")]
        linebox = ttk.Frame(body, height=245)
        linebox.pack(fill="both", expand=True, padx=15)
        tree = self.tree(linebox, columns, [], height=6)
        def refresh():
            tree.delete(*tree.get_children())
            lookup = {row["id"]: label(row) for key in ("products", "stores", "accounts") for row in choices.get(key, [])}
            for index, line in enumerate(lines):
                row = dict(line)
                for key in ("product", "store", "account"):
                    row[key] = lookup.get(row.get(key+"_id"), "")
                tree.insert("", "end", iid=str(index), values=[value_text(row.get(key)) for key, _ in columns])
        def line_dialog(index=None):
            if self.future is not None:
                return
            current = lines[index] if index is not None else {"rate": ""} if kind in TRADE else {}
            if kind in TRADE:
                line_fields = [("product_id", "Item", "products"), ("store_id", "Store", "stores"), ("quantity", "Quantity in base units", "number"), ("rate", "Rate per base unit", "number"), ("discount_percent", "Line discount %", "number")]
            elif kind == "stock_opening":
                line_fields = [("product_id", "Item", "products"), ("store_id", "Store", "stores"), ("quantity", "Quantity in base units", "number"), ("value", "Opening value", "number"), ("pool", "Stock pool", ("regular", "breakage"))]
            elif kind in JOURNALS:
                line_fields = [("account_id", "Account", "accounts"), ("debit", "Debit", "number"), ("credit", "Credit", "number"), ("narration", "Narration", "text")]
            else:
                line_fields = [("account_id", "Account", "accounts"), ("amount", "Amount", "number"), ("narration", "Narration", "text")]
            child, frame = self.dialog("Edit line" if index is not None else "Add line", width=710, height=550)
            child._focus_entry = True
            form = Form(frame, line_fields, values=current, choices=choices, enter_navigation=True)
            form.frame.pack(fill="x")
            child._initial_focus = form.first_widget()
            if kind in TRADE | {"stock_opening"}:
                unit_note = owned_variable(frame, value="")
                def update_unit_note(event=None):
                    issue = baseline_details(self.baseline).get("manifest", {}).get("product_issues", {}).get(form.inputs["product_id"].get())
                    unit_note.set("Original stock for this item includes differing units. Review its history before adding this line." if issue else "")
                form.widgets["product_id"].bind("<<ComboboxSelected>>", update_unit_note, add=True)
                ttk.Label(frame, textvariable=unit_note, wraplength=600).pack(anchor="w", padx=15)
                update_unit_note()
                ttk.Button(frame, text="Convert large / base quantities", command=lambda: self.quantity_converter(form, choices)).pack(anchor="w", padx=15, pady=8)
            if kind in {"sale", "purchase"} | CASH_INVOICES:
                def use_stored_rate():
                    product = next((row for row in choices["products"] if row["id"] == form.inputs["product_id"].get()), None)
                    if product is None:
                        messagebox.showerror("Choose item", "Select an item before using its stored rate.", parent=child)
                        return
                    rate = stored_rate(product, kind)
                    if rate is None:
                        messagebox.showinfo("No saved price", "This item's price was not recorded. Enter the agreed price for this line.", parent=child)
                        return
                    form.inputs["rate"].set(value_text(rate))
                ttk.Button(frame, text="Use stored rate", command=use_stored_rate).pack(anchor="w", padx=15, pady=(0, 8))
            source = dict(current)
            source_label = owned_variable(frame, value="Original line selected" if current.get("source_line_id") else "Select the original posted line for this return.")
            if kind.endswith("_return"):
                ttk.Label(frame, textvariable=source_label, style="Muted.TLabel", wraplength=600).pack(padx=15, anchor="w")
                def selected_original(line, original):
                    for key in ("product_id", "store_id"):
                        if line[key] not in form.inputs[key].ids:
                            messagebox.showerror("Original item unavailable", "The original item or store is inactive. Review its setup before entering a return.", parent=child)
                            return False
                    if original["account_id"] not in header.inputs["account_id"].ids:
                        messagebox.showerror("Original account unavailable", "The original party account is inactive. Review its setup before entering a return.", parent=child)
                        return False
                    source["source_line_id"] = line["id"]
                    for key in ("product_id", "store_id", "rate", "discount_percent"):
                        if key not in line:
                            continue
                        control = form.inputs[key]
                        if isinstance(control, Choice):
                            control.widget.current(control.ids.index(line[key]))
                        else:
                            control.set(value_text(line[key]))
                    header.inputs["account_id"].widget.current(header.inputs["account_id"].ids.index(original["account_id"]))
                    for key in ("rounding_policy", "posting_places"):
                        header.inputs[key].set(value_text(original[key]))
                    source_label.set("Original line selected. Enter the quantity being returned.")
                    return True
                original_button = ttk.Button(frame, text="Choose original line", command=lambda: self.select_original_line(
                    company, kind, selected_original, selected_focus=form.widgets["quantity"]))
                original_button.pack(anchor="w", padx=15, pady=10)
                for sequence in ("<Return>", "<KP_Enter>"):
                    original_button.bind(sequence, lambda event: (original_button.invoke(), "break")[-1])
                if not current.get("source_line_id"):
                    child._initial_focus = original_button
            def add():
                row = form.values()
                if kind.endswith("_return"):
                    row["source_line_id"] = source.get("source_line_id")
                # Use the same pure calculation primitives as the service so a
                # mistyped line stays open. The service remains authoritative
                # for fresh permissions, stock, links and document balancing.
                from bifma.calculations import InvoiceLine, DiscountKind, as_decimal
                from bifma.posting_rules import JournalEntry
                try:
                    for key in ("product_id", "store_id") if kind in TRADE | {"stock_opening"} else ("account_id",):
                        if row.get(key) is None:
                            form._focus(form.widgets[key])
                            title = {"product_id": "an item", "store_id": "a store", "account_id": "an account"}[key]
                            raise ValueError("Choose " + title + ".")
                    if kind in TRADE | {"stock_opening"}:
                        InvoiceLine(row["quantity"], row.get("rate", "0"), row.get("discount_percent", "0"),
                                    discount_kind=DiscountKind.PERCENT)
                    if kind == "stock_opening" and as_decimal(row["value"]) < 0:
                        raise ValueError("Opening value cannot be negative.")
                    if kind in JOURNALS:
                        JournalEntry(row["account_id"], as_decimal(row["debit"]), as_decimal(row["credit"]))
                    if kind.endswith("_return") and not row.get("source_line_id"):
                        original_button.focus_set()
                        raise ValueError("Choose the original invoice line before using this return line.")
                except ValueError as error:
                    messagebox.showerror("Check line", str(error), parent=child)
                    return
                if index is None:
                    lines.append(row)
                else:
                    lines[index] = row
                child.destroy()
                refresh()
                selected = str(len(lines) - 1 if index is None else index)
                tree.selection_set(selected)
                tree.focus(selected)
                tree.see(selected)
                add_button.focus_set()
            use_button = ttk.Button(frame, text="Use line", style="Accent.TButton", command=add)
            use_button.pack(anchor="e", padx=15, pady=12)
            form.set_enter_target(use_button)
        def selected_index():
            selection = tree.selection()
            return int(selection[0]) if selection else None
        def edit_line(event=None):
            if self.future is not None:
                return "break"
            index = selected_index()
            if index is not None:
                line_dialog(index)
            return "break"
        def remove_line(event=None):
            if self.future is not None:
                return "break"
            index = selected_index()
            if index is not None:
                lines.pop(index)
                refresh()
                if lines:
                    selected = str(min(index, len(lines) - 1))
                    tree.selection_set(selected)
                    tree.focus(selected)
                tree.focus_set()
            return "break"
        tree.bind("<Double-1>", edit_line)
        tree.bind("<Return>", edit_line)
        tree.bind("<KP_Enter>", edit_line)
        tree.bind("<Delete>", remove_line)
        controls = ttk.Frame(body, padding=15)
        controls.pack(fill="x")
        add_button = ttk.Button(controls, text="Add line", command=line_dialog)
        add_button.pack(side="left")
        header.set_enter_target(add_button)
        ttk.Button(controls, text="Edit", command=edit_line).pack(side="left", padx=8)
        ttk.Button(controls, text="Remove", command=remove_line).pack(side="left")
        def save():
            if self.future is not None or self.grab_current() not in (None, dialog):
                return
            payload = header.values()
            payload["posting_places"] = int(payload["posting_places"])
            payload["lines"] = [dict(line) for line in lines]
            options = {"document_id": document["id"], "expected_revision": document["revision"]} if document else {}
            def saved(result):
                dialog.destroy()
                self.review_draft(result, return_to=return_to, entry=entry)
            self.run(partial(self.transactions.save_draft, actor, company, kind, payload, **options), saved)
        save_button = ttk.Button(controls, text="Save draft & review", style="Accent.TButton", command=save)
        save_button.pack(side="right")
        for sequence in ("<Control-s>", "<Control-S>"):
            dialog.bind(sequence, lambda event: (save(), "break")[-1])
        for sequence in ("<Insert>", "<Control-plus>"):
            tree.bind(sequence, lambda event: (line_dialog(), "break")[-1])
        original_values = (header.values(), [dict(line) for line in lines])
        def close():
            if self.future is not None:
                return
            unchanged = (header.values(), lines) == original_values
            if unchanged or messagebox.askyesno("Unsaved document", "Discard the changes in this form? Saved drafts remain available through F8.", parent=dialog):
                dialog.destroy()
        dialog.protocol("WM_DELETE_WINDOW", close)
        ttk.Label(body, text="Enter: next field / use line  ·  Ctrl+S: save and review  ·  Table: Enter edits, Insert adds, Delete removes",
                  style="Muted.TLabel", wraplength=850).pack(anchor="w", padx=15, pady=(0, 8))
        if kind in CASH_TRADES:
            explanation = "This cash document already records its cash movement. Do not enter a receipt or payment voucher for the same movement."
        else:
            explanation = "Payments on account invoices are entered as separate receipt or payment vouchers."
        ttk.Label(body, text=explanation, style="Muted.TLabel", wraplength=850).pack(anchor="w", padx=15, pady=(0, 12))
        refresh()

    def quantity_converter(self, line_form, choices):
        product_id = line_form.inputs["product_id"].get()
        product = next((row for row in choices["products"] if row["id"] == product_id), None)
        if product is None or not product.get("large_unit_id"):
            messagebox.showerror("Large unit unavailable", "Select an item with a configured large unit and conversion factor.", parent=self)
            return
        units = {row["id"]: row for row in choices["units"]}
        large, small = units[product["large_unit_id"]], units[product["small_unit_id"]]
        dialog, body = self.dialog("Convert quantity", width=690, height=430)
        dialog._focus_entry = True
        ttk.Label(body, text=f"{label(product)}\n1 {large['name']} = {product['unit_factor']} {small['name']}", style="Title.TLabel", wraplength=620).pack(anchor="w", pady=12)
        form = Form(body, [("large", f"Large quantity ({large['name']})", "number"), ("small", f"Base quantity ({small['name']})", "number")])
        form.frame.pack(fill="x")
        dialog._initial_focus = form.first_widget()
        ttk.Label(body, text="The converted total replaces this line’s base quantity. The rate remains per base unit.", style="Muted.TLabel", wraplength=600).pack(anchor="w", padx=15, pady=10)
        def apply():
            try:
                total = convert_quantity(**form.values(), factor=product["unit_factor"])
                if total <= 0:
                    raise ValueError("Enter a quantity greater than zero.")
            except ValueError as error:
                messagebox.showerror("Check quantity", str(error), parent=dialog)
                return
            line_form.inputs["quantity"].set(canonical_decimal(total))
            dialog.destroy()
            line_form._focus(line_form.widgets["quantity"])
        apply_button = ttk.Button(body, text="Use base quantity", style="Accent.TButton", command=apply)
        apply_button.pack(anchor="e", padx=15, pady=12)
        form.set_enter_target(apply_button)

    def select_original_line(self, company, return_kind, callback, *, selected_focus=None):
        dialog, body = self.dialog("Choose the original document", width=840, height=650)
        dialog._focus_entry = True
        ttk.Label(body, text="Select a new posted invoice or an eligible invoice from the original records. Existing returns are included when checking the remaining quantity.",
                  style="Muted.TLabel", wraplength=750).pack(anchor="w", pady=(0, 10))
        query = owned_variable(body)
        bar = ttk.Frame(body)
        bar.pack(fill="x")
        query_entry = ttk.Entry(bar, textvariable=query)
        query_entry.pack(side="left", fill="x", expand=True)
        dialog._initial_focus = query_entry
        results = ttk.Frame(body)
        results.pack(fill="both", expand=True)
        def show(rows, term, offset, *, focus_results=False):
            for child in results.winfo_children():
                child.destroy()
            controls = ttk.Frame(results)
            controls.pack(side="bottom", fill="x", pady=8)
            shown = [{**row, "source_label": "Original history" if row.get("source") == "history" else "New document"} for row in rows]
            tree = self.tree(results, [("document_number", "Document"), ("business_date", "Date"), ("total_amount", "Total"), ("source_label", "Source")], shown, height=9)
            def choose_document(event=None):
                if self.future is not None:
                    return "break"
                selected = tree.selection()
                if selected:
                    self.run(partial(self.transactions.get_return_source, self.actor, company, return_kind, rows[int(selected[0])]["id"]), lambda document: show_lines(document, term, offset))
                return "break"
            tree.bind("<Double-1>", choose_document)
            tree.bind("<Return>", choose_document)
            tree.bind("<KP_Enter>", choose_document)
            if rows:
                tree.selection_set("0")
                tree.focus("0")
                if focus_results:
                    tree.focus_set()
            ttk.Button(controls, text="Show lines", command=choose_document).pack(side="left")
            ttk.Label(controls, text=f"{offset + 1 if rows else 0}–{offset + len(rows)}", style="Muted.TLabel").pack(side="left", padx=12)
            ttk.Button(controls, text="Next", state="normal" if len(rows) == 100 else "disabled", command=lambda: search(offset + 100, term)).pack(side="right")
            ttk.Button(controls, text="Previous", state="normal" if offset else "disabled", command=lambda: search(max(0, offset - 100), term)).pack(side="right", padx=8)
        def show_lines(document, term, offset):
            for child in results.winfo_children():
                child.destroy()
            controls = ttk.Frame(results)
            controls.pack(side="bottom", fill="x", pady=8)
            rows = [{**row, "return_note": row.get("exclusion_reason") or ""} for row in saved_rows(document)["lines"]]
            if document.get("source") == "history":
                ttk.Label(results, text="This invoice belongs to the original history. Its details remain unchanged when you post a new return.",
                          style="Muted.TLabel", wraplength=750).pack(anchor="w", pady=8)
            tree = self.tree(results, [("line_number", "Line"), ("product_code", "Item"), ("product_name", "Description"), ("store_name", "Store"), ("quantity", "Original quantity"), ("rate", "Rate"), ("discount_percent", "Discount %"), ("return_note", "Return eligibility")], rows, height=9)
            def choose(event=None):
                if self.future is not None:
                    return
                selected = tree.selection()
                if selected:
                    line = rows[int(selected[0])]
                    if not line.get("return_eligible", True):
                        messagebox.showinfo("Original line unavailable", line.get("exclusion_reason") or "This original line needs review before it can be returned.", parent=dialog)
                        return "break"
                    if callback(line, document) is False:
                        return "break"
                    dialog.destroy()
                    if selected_focus is not None and selected_focus.winfo_exists():
                        selected_focus.focus_set()
                        selected_focus.selection_range(0, "end")
                return "break"
            ttk.Button(controls, text="Back to documents", command=lambda: search(offset, term)).pack(side="left")
            ttk.Button(controls, text="Use selected line", style="Accent.TButton", command=choose).pack(side="right")
            tree.bind("<Double-1>", choose)
            tree.bind("<Return>", choose)
            tree.bind("<KP_Enter>", choose)
            if rows:
                tree.selection_set("0")
                tree.focus("0")
                tree.focus_set()
        def search(offset=0, term=None, *, focus_results=False):
            term = query.get() if term is None else term
            self.run(partial(self.transactions.list_return_sources, self.actor, company, return_kind, search=term, offset=offset, limit=100), lambda rows: show(rows, term, offset, focus_results=focus_results))
        ttk.Button(bar, text="Search", command=search).pack(side="left", padx=8)
        for sequence in ("<Return>", "<KP_Enter>"):
            query_entry.bind(sequence, lambda event: (search(focus_results=True), "break")[-1])
        search()

    def _close_document_view(self, dialog, callback):
        if self.future is not None:
            self.status.set("Finish the current operation before closing this document.")
            return
        dialog.destroy()
        callback()

    def review_draft(self, document, *, return_to=None, entry=False, print_after_post=False):
        dialog, body = self.dialog("Review draft · " + document["document_number"], width=870, height=630)
        dialog._focus_entry = True
        self.document_summary(body, document)
        ttk.Label(body, text="Review the saved draft. Posting applies its stock and accounting entries together.", style="Muted.TLabel", wraplength=750).pack(anchor="w", pady=8)
        self.document_tabs(body, document, height=7)
        bar = ttk.Frame(body, padding=(0, 15))
        bar.pack(fill="x")
        def edit():
            dialog.destroy()
            self.document_editor(document["kind"], document, return_to=return_to, entry=entry,
                                 print_after_post=print_after_post)
        allowed = self.permissions.get(KINDS.get(document["kind"], ("", ""))[1], [])
        if "update" in allowed:
            ttk.Button(bar, text="Edit draft", command=edit).pack(side="left")
        def post():
            def posted(result):
                dialog.destroy()
                self.posted_dialog(result, return_to=return_to, print_after_open=print_after_post)
            self.run(partial(self.transactions.post, self.actor, document["company_id"], document["id"], document["revision"]), posted)
        if "post" in allowed:
            ttk.Button(bar, text="Post and print" if print_after_post else "Post document", style="Accent.TButton", command=post).pack(side="right")
        back = return_to or (partial(self.document_list, document["kind"]) if document["kind"] in KINDS else self.home)
        dismiss = partial(self._close_document_view, dialog, back)
        keep_button = ttk.Button(bar, text="Keep as draft", command=dismiss)
        keep_button.pack(side="right", padx=8)
        # Arriving here after entry must never turn another Enter into a post.
        dialog._initial_focus = keep_button
        dialog.protocol("WM_DELETE_WINDOW", dismiss)

    def posted_dialog(self, document, *, return_to=None, print_after_open=False):
        dialog, body = self.dialog("Posted · " + document["document_number"], width=900, height=700)
        dialog._focus_entry = True
        self.document_summary(body, document)
        ttk.Label(body, text="Posted document · Its saved details cannot be edited.", style="Muted.TLabel").pack(anchor="w", pady=8)
        self.document_tabs(body, document)
        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=15)
        ttk.Button(bar, text="Save printable copy", command=lambda: self.export_document(document)).pack(side="right", padx=(8, 0))
        ttk.Button(bar, text="Bill / print preview", command=lambda: self.preview_document(document)).pack(side="right", padx=(8, 0))
        if document.get("reversal"):
            ttk.Label(bar, text="Reversed by " + document["reversal"]["document_number"], style="Muted.TLabel").pack(side="left")
        back_to = document.get("_list_kind", document["kind"])
        back = return_to or (partial(self.document_list, back_to) if back_to in KINDS else self.home)
        dismiss = partial(self._close_document_view, dialog, back)
        close_button = ttk.Button(bar, text="Close", command=dismiss)
        close_button.pack(side="right")
        dialog._initial_focus = close_button
        dialog.protocol("WM_DELETE_WINDOW", dismiss)
        self._bind_document_print(dialog, document)

        if print_after_open:
            def print_visible(event):
                if event.widget is not dialog or not dialog.winfo_viewable():
                    return
                unbind_callback(dialog, "<Visibility>", print_binding)
                if not self.close_pending:
                    self.preview_document(document)
            # The modal's visibility binding runs first. Only then may the
            # printable page may open from this posted form.
            print_binding = dialog.bind("<Visibility>", print_visible, add="+")

    def export_document(self, document):
        from bifma.document_export import DocumentExporter, OriginalDocumentExporter
        destination = filedialog.asksaveasfilename(parent=self.grab_current() or self, title="Save printable document", defaultextension=".html", initialfile="bifma-document.html", filetypes=[("Printable HTML", "*.html")], confirmoverwrite=False)
        if destination:
            if document.get("source") == "original":
                exporter = OriginalDocumentExporter(self.path, register=self.register)
                task = partial(exporter.export_html, self.actor, document["company_id"], document["kind"], document["id"], destination)
            else:
                exporter = DocumentExporter(self.path, security=self.security)
                task = partial(exporter.export_html, self.actor, document["company_id"], document["id"], destination)
            self.run(task,
                     lambda path: messagebox.showinfo("Printable copy saved", f"Saved to:\n{path}\n\nOpen the file in your browser and choose Print or Save as PDF.", parent=self))

    def _open_print_preview(self, path):
        import webbrowser
        try:
            opened = webbrowser.open(Path(path).as_uri(), new=2)
        except (OSError, webbrowser.Error):
            opened = False
        if opened:
            self.status.set("Print preview opened · Ctrl+P prints or saves PDF. Return here to continue.")
        else:
            messagebox.showinfo("Print preview", f"Open this printable page in a browser:\n{path}\n\nCtrl+P prints or saves PDF. The page remains available until sign-out or closing the application.", parent=self.grab_current() or self)

    def _bind_document_print(self, dialog, document):
        def preview(event=None):
            if self.grab_current() is dialog and self.future is None and not self.close_pending:
                self.preview_document(document)
            return "break"
        dialog.bind("<Control-p>", preview)
        dialog.bind("<Control-P>", preview)

    def preview_document(self, document):
        """Use the same fresh authorization/export checks as a saved copy."""
        from bifma.document_export import DocumentExporter, OriginalDocumentExporter
        if self.future is not None or self.close_pending or self._closed:
            return
        destination = self._print_previews.destination()
        if document.get("source") == "original":
            exporter = OriginalDocumentExporter(self.path, register=self.register)
            task = partial(exporter.export_html, self.actor, document["company_id"], document["kind"], document["id"], destination)
        else:
            exporter = DocumentExporter(self.path, security=self.security)
            task = partial(exporter.export_html, self.actor, document["company_id"], document["id"], destination)
        self.run(task, self._open_print_preview)

    def document_summary(self, body, document):
        snapshot = document.get("snapshot") or {}
        values = document.get("values") or {}
        company = snapshot.get("company", {})
        account_id = document.get("account_id")
        account = snapshot.get("accounts", {}).get(account_id, {})
        ttk.Label(body, text=f"{document['business_date']}    Total: {document['total_amount']}", style="Title.TLabel").pack(anchor="w", pady=10)
        ttk.Label(body, text=company.get("name", "") + (" · " + label(account) if account else ""), wraplength=760).pack(anchor="w", pady=3)
        if snapshot.get("bill_number"):
            ttk.Label(body, text="Bill #: " + snapshot["bill_number"]).pack(anchor="w", pady=3)
        cash = snapshot.get("cash_settlement")
        if isinstance(cash, dict):
            label_text = "Cash correction" if document["kind"] == "reversal" else "Cash refund" if document["kind"] in RETURN_ORIGINALS else "Cash in full"
            contact = " · ".join(text for text in (cash.get("counterparty_name", ""), cash.get("counterparty_mobile", "")) if text)
            ttk.Label(body, text=label_text + (" · " + contact if contact else ""), wraplength=760).pack(anchor="w", pady=3)
        rounding = "whole units" if document["rounding_policy"] == "legacy_whole" else "cents"
        text = f"Subtotal: {rounding} · Final decimals: {document['posting_places']}"
        if document["kind"] in BREAKAGE:
            text += f" · Breakage stock · Allowance: {values.get('breakage', '0')}"
        elif document["kind"] in TRADE:
            text += f" · Discount: {values.get('discount', '0')} · Breakage: {values.get('breakage', '0')} · Freight: {values.get('freight', '0')}"
        ttk.Label(body, text=text, style="Muted.TLabel", wraplength=760).pack(anchor="w", pady=3)

    def document_tabs(self, body, document, *, height=10):
        notebook = ttk.Notebook(body)
        notebook.pack(fill="both", expand=True)
        sections = saved_rows(document)
        for title, key, columns in (
            ("Items", "lines", [("product_code", "Item"), ("product_name", "Description"), ("store_name", "Store"), ("quantity", "Quantity"), ("rate", "Rate"), ("discount_percent", "Discount %"), ("net_amount", "Amount")]),
            ("Accounting", "journal_lines", [("account_code", "Account"), ("account_name", "Name"), ("debit", "Debit"), ("credit", "Credit")]),
            ("Stock", "stock_movements", [("product_code", "Item"), ("store_name", "Store"), ("pool", "Stock pool"), ("direction", "Direction"), ("quantity", "Quantity"), ("unit_cost_display", "Saved unit cost"), ("stock_value_display", "Saved stock value")]),
        ):
            if not sections[key]:
                continue
            frame = ttk.Frame(notebook)
            notebook.add(frame, text=title)
            self.tree(frame, columns, sections[key], height=height)

    def _ledger_report_filter(self, report):
        from bifma.report_ui import open_financial_report
        self._show_navigation("Reports")
        open_financial_report(self, report)

    def report_screen(self, report=None):
        if not self.company_id:
            return
        if report in {"Cash book", "Account ledger", "Sub-trial balance"}:
            return self._ledger_report_filter(report)
        self._show_navigation("Reports")
        cutoff = baseline_details(self.baseline).get("cutoff_date")
        self.heading("Reports", (f"Trade detail and dated stock reports include original activity. Financial and item ledgers carry balances from {cutoff}, followed by new postings."
                                 if cutoff else "Posted transactions. Enter dates to narrow the reporting period."))
        available = available_reports(self.permissions)
        if not available:
            ttk.Label(self.main, text="No report permissions are currently available.", style="Muted.TLabel").pack(anchor="w")
            return
        fields = [("report", "Report", available), ("date_from", "From (YYYY-MM-DD)", "optional"), ("date_to", "To (YYYY-MM-DD)", "optional"), ("pool", "Stock pool", ("regular", "breakage"))]
        defaults = {"date_from": (date.fromisoformat(cutoff) + timedelta(days=1)).isoformat()} if cutoff else {}
        if report in available:
            defaults["report"] = report
        form = Form(self.main, fields, values=defaults, columns=2)
        form.frame.pack(fill="x")
        automatic_start_date, setting_start_date = True, False
        def start_date_edited(*_):
            nonlocal automatic_start_date
            if not setting_start_date:
                automatic_start_date = False
        # Form owns this variable and removes its trace on destruction.
        form.inputs["date_from"].trace_add("write", start_date_edited)
        def report_controls(event=None):
            nonlocal setting_start_date
            selected_report = form.inputs["report"].get()
            stock_report = selected_report in {"Stock quantities", "Stock valuation", "Item ledger"}
            form.widgets["pool"].configure(state="readonly" if stock_report else "disabled")
            if automatic_start_date:
                setting_start_date = True
                try:
                    form.inputs["date_from"].set("" if selected_report in {"Sales / returns detail", "Purchases / returns detail"}
                                                 else defaults.get("date_from", ""))
                finally:
                    setting_start_date = False
        form.widgets["report"].bind("<<ComboboxSelected>>", report_controls)
        report_controls()
        controls = ttk.Frame(self.main)
        controls.pack(fill="x", pady=8)
        result_box = ttk.Frame(self.main)
        result_box.pack(fill="both", expand=True, pady=12)
        def filter_action(dialog, body, form, title, command):
            action = ttk.Button(body, text=title, style="Accent.TButton", command=command)
            action.pack(anchor="e", pady=12)
            form.set_enter_target(action)
            dialog._focus_entry = True
            dialog._initial_focus = form.first_widget()
            form.focus_first()

        def filter_result(dialog, render):
            # Keep values and the focused field available if the worker rejects
            # dates or access. Close the chooser only after a successful read.
            def ready(result):
                if dialog.winfo_exists():
                    dialog.destroy()
                render(result)
            return ready

        def show(result, columns, summary="", footer=None, export=None):
            for child in result_box.winfo_children():
                child.destroy()
            if summary:
                main_summary, _, explanation = summary.partition("\n")
                ttk.Label(result_box, text=main_summary, style="Title.TLabel", wraplength=750).pack(anchor="w", pady=(5, 8))
                if explanation:
                    ttk.Label(result_box, text=explanation, style="Muted.TLabel", wraplength=750).pack(anchor="w", pady=(0, 8))
            if footer or export:
                bar = ttk.Frame(result_box)
                bar.pack(side="bottom", fill="x", pady=8)
                if export:
                    ttk.Button(bar, text="Save full report", command=export).pack(side="left", padx=(0, 12))
                if footer:
                    footer(bar)
            tree = self.tree(result_box, columns, result)
            if tree.get_children():
                first = tree.get_children()[0]
                tree.selection_set(first)
                tree.focus(first)
            tree.focus_set()
        def generate():
            values = form.values()
            args = {key: values[key] for key in ("date_from", "date_to")}
            if values["report"] == "Trial balance":
                def render(result):
                    totals = result["totals"]
                    show(result["rows"], [("code", "Code"), ("name", "Account"), ("opening", "Opening Dr / −Cr"), ("debit", "Debit"), ("credit", "Credit"), ("closing", "Closing Dr / −Cr")], f"Period debits: {totals['debit']}    Credits: {totals['credit']}", export=partial(self.export_report, "trial_balance", args))
                self.run(partial(self.reports.trial_balance, self.actor, self.company_id, **args), render)
            elif values["report"] == "Sub-trial balance":
                def render(result):
                    rows = [{**row, "hierarchy_name": "  " * min(row["depth"], 12) + row["name"], "level": row["depth"] + 1} for row in result["rows"]]
                    totals = result["totals"]
                    show(rows, [("code", "Code"), ("hierarchy_name", "Account hierarchy"), ("level", "Level"), ("opening", "Opening Dr / −Cr"), ("debit", "Debit"), ("credit", "Credit"), ("closing", "Closing Dr / −Cr")], f"Debits: {totals['debit']}    Credits: {totals['credit']} · Group rows include their descendants.", export=partial(self.export_report, "sub_trial_balance", args))
                self.run(partial(self.reports.sub_trial_balance, self.actor, self.company_id, **args), render)
            elif values["report"] == "Profit and loss":
                def render(result):
                    totals = result["totals"]
                    profit = totals["profit"]
                    ready = result["status"] == "ready"
                    summary = (f"Periodic trading profit: {profit}" if ready and profit is not None
                               else "Periodic trading profit: Review required")
                    summary += "\nTrading result includes classified income, expenses and the change in inventory value."
                    needs = []
                    for key, title in (("unclassified_count", "accounts need a classification"),
                                       ("mapping_issue_count", "account mappings need review"),
                                       ("missing_valuation_count", "movements have no recorded cost")):
                        if totals.get(key):
                            needs.append(f"{totals[key]:,} {title}")
                    if needs:
                        summary += " " + "; ".join(needs) + ". Profit remains unknown until these are resolved."
                    rows = [{**row, "classification_display": row.get("classification") or "Not assigned",
                             "contribution_display": "Unknown" if row.get("contribution") is None else row["contribution"]}
                            for row in result["rows"]]
                    def details():
                        dialog, body = self.dialog("Periodic trading profit details", width=820, height=640)
                        for key, title in (("income", "Income"), ("expense", "Expenses"),
                                           ("inventory_opening", "Opening inventory value"),
                                           ("inventory_closing", "Closing inventory value"),
                                           ("opening_injections", "Additional opening stock value"),
                                           ("inventory_adjustment", "Inventory value adjustment"),
                                           ("classified_result", "Classified account result"), ("profit", "Trading profit")):
                            value = totals.get(key)
                            ttk.Label(body, text=f"{title}: {value if value is not None else 'Unknown'}").pack(anchor="w", pady=5)
                        for issue in result.get("mapping_issues", []):
                            target = (f"Item {issue.get('code', '')} · {issue.get('name', '')}" if issue.get("product_id")
                                      else f"Document {issue.get('document_number', '')} · account {issue.get('code', '')}")
                            reason = issue["issue"].replace("purchase_account_id", "Purchase account").replace("sale_account_id", "Sales account")
                            ttk.Label(body, text=target + ": " + reason, wraplength=720, style="Muted.TLabel").pack(anchor="w", pady=5)
                        ttk.Button(body, text="Close", command=dialog.destroy).pack(anchor="e", pady=12)
                    def footer(bar):
                        ttk.Button(bar, text="Calculation details", command=details).pack(side="left")
                        if not ready and self.actor.user.is_admin:
                            ttk.Button(bar, text="Review account classifications", command=lambda: self.master_screen("accounts")).pack(side="left", padx=8)
                    show(rows, [("code", "Code"), ("name", "Account"), ("classification_display", "Classification"),
                                ("debit", "Period debit"), ("credit", "Period credit"),
                                ("contribution_display", "Profit contribution")], summary, footer,
                         partial(self.export_report, "profit_loss", args))
                self.run(partial(self.reports.profit_loss, self.actor, self.company_id, **args), render)
            elif values["report"] == "Account balances by city":
                def choose(selectors):
                    dialog, body = self.dialog("City balance filters", width=760, height=470)
                    ttk.Label(body, text="Group posted balances by current account cities and kinds. These are account balances, not unpaid-invoice or due-date amounts.", style="Muted.TLabel", wraplength=680).pack(anchor="w", pady=8)
                    kinds = {"All kinds": None, "Customer": "customer", "Supplier": "supplier", "Customer and supplier": "both",
                             "Cash": "cash", "Bank": "bank", "General": "general", "Group": "group"}
                    selection = Form(body, [("city_id", "City (blank for all)", "cities"), ("account_kind", "Account kind", tuple(kinds)),
                                           ("search", "Account code or name", "text"), ("include_zero", "Include zero closing balances", "bool")], choices=selectors)
                    selection.frame.pack(fill="x")
                    def selected():
                        filters = {**selection.values(), **args}
                        filters["account_kind"] = kinds[filters["account_kind"]]
                        def render(result):
                            offset, count = result["offset"], len(result["rows"])
                            totals = result["totals"]
                            city = result["city"]["name"] if result["city"] else "All cities"
                            kind = next(title for title, key in kinds.items() if key == filters["account_kind"])
                            details = city + " · " + kind + " · " + ("Zero closings included" if filters["include_zero"] else "Zero closings excluded")
                            if filters["search"]:
                                details += " · Search: " + filters["search"]
                            def grouped():
                                detail, content = self.dialog("Totals by city", width=1050, height=580)
                                ttk.Label(content, text="Totals for every filtered account, across all pages. Current cities and account kinds are used; debit and credit closings stay separate.", style="Muted.TLabel", wraplength=950).pack(anchor="w", pady=10)
                                ttk.Button(content, text="Close", command=detail.destroy).pack(side="bottom", anchor="e", pady=10)
                                self.tree(content, [("name", "City"), ("account_count", "Accounts"), ("opening", "Opening Dr / −Cr"),
                                                   ("debit", "Period debit"), ("credit", "Period credit"), ("closing_debit", "Closing debit"),
                                                   ("closing_credit", "Closing credit"), ("closing", "Net Dr / −Cr")], result["groups"])
                            def footer(bar):
                                ttk.Button(bar, text="Totals by city", command=grouped).pack(side="left")
                                ttk.Label(bar, text=f"{offset + 1 if count else 0}–{offset + count} of {result['total']} accounts", style="Muted.TLabel").pack(side="left", padx=10)
                                ttk.Button(bar, text="Next", state="normal" if offset + count < result["total"] else "disabled", command=lambda: page(offset + 100)).pack(side="right")
                                ttk.Button(bar, text="Previous", state="normal" if offset else "disabled", command=lambda: page(max(0, offset - 100))).pack(side="right", padx=8)
                            rows = [{**row, "city_label": row["city_name"] or "Unassigned"} for row in result["rows"]]
                            summary = f"Closing debit: {totals['closing_debit']}    Credit: {totals['closing_credit']}\nCurrent cities and account kinds. " + details
                            show(rows, [("city_label", "City"), ("code", "Code"), ("name", "Account"), ("kind", "Current kind"),
                                        ("opening", "Opening Dr / −Cr"), ("debit", "Debit"), ("credit", "Credit"),
                                        ("closing_debit", "Closing debit"), ("closing_credit", "Closing credit")], summary, footer,
                                 partial(self.export_report, "city_balances", filters))
                        def page(offset=0):
                            self.run(partial(self.reports.city_balances, self.actor, self.company_id, offset=offset, limit=100, **filters), filter_result(dialog, render))
                        page()
                    filter_action(dialog, body, selection, "Show city balances", selected)
                self.run(partial(self.reports.city_selectors, self.actor, self.company_id), choose)
            elif values["report"] in {"Stock quantities", "Stock valuation"}:
                valued = values["report"] == "Stock valuation"
                def choose(stores):
                    dialog, body = self.dialog("Stock valuation filters" if valued else "Stock quantity filters", width=720, height=410)
                    choice = Form(body, [("store_id", "Store (blank for all)", "stores"), ("search", "Item code or name", "text")], choices={"stores": stores})
                    choice.frame.pack(fill="x")
                    def selected():
                        filters = {"pool": values["pool"], **args, **choice.values()}
                        if valued:
                            def valuation(result):
                                keys = ("value_opening", "value_in", "value_out", "value_closing")
                                rows = [{**row, **{key: "Unknown" if row.get(key) is None else row[key] for key in keys}}
                                        for row in result["rows"]]
                                total = result["totals"]["value_closing"]
                                missing = result["totals"]["missing_valuation_count"]
                                summary = f"Closing stock value: {total if total is not None else 'Unknown'}\nNew movements use saved last-purchase costs. Original starting values remain unchanged."
                                if missing:
                                    summary += f" {missing:,} movements have no recorded cost; their value remains unknown."
                                show(rows, [("code", "Code"), ("name", "Item"), ("store", "Store"),
                                            ("opening", "Opening quantity"), ("closing", "Closing quantity"),
                                            ("value_opening", "Opening value"), ("value_in", "Value in"),
                                            ("value_out", "Value out"), ("value_closing", "Closing value")], summary,
                                     export=partial(self.export_report, "stock_valuation", filters))
                            self.run(partial(self.reports.stock_valuation, self.actor, self.company_id, **filters), filter_result(dialog, valuation))
                        else:
                            self.run(partial(self.reports.stock, self.actor, self.company_id, **filters), filter_result(dialog, lambda result: show(result, [("code", "Code"), ("name", "Item"), ("store", "Store"), ("opening", "Opening"), ("in", "In"), ("out", "Out"), ("closing", "Closing")], export=partial(self.export_report, "stock_quantities", filters))))
                    filter_action(dialog, body, choice, "Show stock valuation" if valued else "Show stock quantities", selected)
                self.run(partial(self.reports.stock_stores, self.actor, self.company_id), choose)
            elif values["report"] == "Item ledger":
                def choose(data):
                    dialog, body = self.dialog("Choose item and store", width=720, height=410)
                    choice = Form(body, [("product_id", "Item", "products"), ("store_id", "Store (blank for all)", "stores")], choices=data)
                    choice.frame.pack(fill="x")
                    def selected():
                        selected_values = choice.values()
                        if not selected_values["product_id"]:
                            messagebox.showerror("Choose item", "Select the item to report.", parent=dialog)
                            return
                        def render(result):
                            offset, count = result["offset"], len(result["rows"])
                            def footer(bar):
                                ttk.Label(bar, text=f"{offset + 1 if count else 0}–{offset + count} of {result['total']} entries", style="Muted.TLabel").pack(side="left")
                                ttk.Button(bar, text="Next", state="normal" if offset + count < result["total"] else "disabled", command=lambda: page(offset + 100)).pack(side="right")
                                ttk.Button(bar, text="Previous", state="normal" if offset else "disabled", command=lambda: page(max(0, offset - 100))).pack(side="right", padx=8)
                            show(result["rows"], [("business_date", "Date"), ("document_number", "Document"), ("store_name", "Store"), ("in", "In"), ("out", "Out"), ("balance", "Running quantity")], f"{result['product']['code']} · {result['pool']} · Opening: {result['opening']}    In: {result['in']}    Out: {result['out']}    Closing: {result['closing']}", footer, partial(self.export_report, "item_ledger", {"pool": values["pool"], **selected_values, **args}))
                        def page(offset=0):
                            self.run(partial(self.reports.stock_ledger, self.actor, self.company_id, pool=values["pool"], limit=100, offset=offset, **selected_values, **args), filter_result(dialog, render))
                        page()
                    filter_action(dialog, body, choice, "Show item ledger", selected)
                def selectors():
                    return {"products": self.reports.stock_products(self.actor, self.company_id), "stores": self.reports.stock_stores(self.actor, self.company_id)}
                self.run(selectors, choose)
            elif values["report"] in {"Sales / returns detail", "Purchases / returns detail"}:
                family = "sales" if values["report"] == "Sales / returns detail" else "purchases"
                choices = trade_report_choices(self.permissions, family)
                mode_labels = {"all": "Account and cash", "account": "Account entries", "cash": "Cash entries"}
                def choose_filters(kind, payment_mode, previous_dialog=None):
                    def choose(selectors):
                        if previous_dialog is not None and previous_dialog.winfo_exists():
                            previous_dialog.destroy()
                        dialog, body = self.dialog("Trade detail filters", width=760, height=500)
                        ttk.Label(body, text=f"{family.title()} · {kind} · {mode_labels[payment_mode]} · Reversals of the selected activity are included.", style="Muted.TLabel", wraplength=650).pack(anchor="w", pady=8)
                        selection = Form(body, [("account_id", "Party (blank for all)", "accounts"), ("product_id", "Item (blank for all)", "products"),
                                                ("store_id", "Store (blank for all)", "stores"), ("search", "Document number contains", "text")], choices=selectors)
                        selection.frame.pack(fill="x")
                        def selected():
                            filters = {**selection.values(), **args, "kind": kind, "payment_mode": payment_mode}
                            def render(result):
                                offset, count = result["offset"], len(result["rows"])
                                def quantities():
                                    detail, content = self.dialog("Trade quantities", width=950, height=580)
                                    ttk.Label(content, text="Full-period quantities by item and saved base unit. Invoices are positive, returns negative; reversals have the opposite sign.", style="Muted.TLabel", wraplength=850).pack(anchor="w", pady=10)
                                    ttk.Button(content, text="Close", command=detail.destroy).pack(side="bottom", anchor="e", pady=10)
                                    quantity_rows = [{**row, **{key: "Unknown" if row[key] is None else row[key]
                                                      for key in ("invoice", "return", "reversal", "net")}} for row in result["quantity_totals"]]
                                    self.tree(content, [("product_code", "Item code"), ("product_name", "Item"), ("unit_code", "Recorded base unit"), ("invoice", "Invoices"), ("return", "Returns"), ("reversal", "Reversals"), ("net", "Net quantity")], quantity_rows)
                                def footer(bar):
                                    ttk.Button(bar, text="Quantities by item", command=quantities).pack(side="left")
                                    ttk.Label(bar, text=f"{offset + 1 if count else 0}–{offset + count} of {result['total']} lines", style="Muted.TLabel").pack(side="left", padx=10)
                                    ttk.Button(bar, text="Next", state="normal" if offset + count < result["total"] else "disabled", command=lambda: page(offset + 100)).pack(side="right")
                                    ttk.Button(bar, text="Previous", state="normal" if offset else "disabled", command=lambda: page(max(0, offset - 100))).pack(side="right", padx=8)
                                rows = [{**row, "activity": ("Reverse " + row["source_kind"] if row["kind"] == "reversal" else row["kind"]).replace("_", " "),
                                         "record_source": "Original" if row.get("source") == "original" else "New posted",
                                         "review_notes": "; ".join(row.get("issues", [])),
                                         **{key: "Unknown" if row[key] is None else row[key] for key in
                                            ("signed_quantity", "rate", "discount_percent", "signed_net_amount")}} for row in result["rows"]]
                                amount_text = lambda value: "Unknown" if value is None else str(value)
                                final = (f"Recorded / final document amount: {amount_text(result['document_totals']['net'])}" if result["document_totals"] is not None else "Header amounts omitted for item/store filters")
                                summary = f"Line amount: {amount_text(result['totals']['net'])} · {final}\n{mode_labels[payment_mode]}. Line amounts exclude header adjustments and rounding."
                                if result.get("original_trade_activity"):
                                    summary += f" Original documents: {result['original_document_count']}; new posted: {result['current_document_count']}."
                                    if result.get("original_issue_count") or result.get("original_empty_document_count"):
                                        summary += f" Review notes: {result['original_issue_count']} original lines; {result['original_empty_document_count']} original documents have no lines."
                                report = "sales_detail" if family == "sales" else "purchase_detail"
                                show(rows, [("business_date", "Date"), ("document_number", "Document"), ("record_source", "Record source"), ("activity", "Activity"), ("reverses_document_number", "Reverses"),
                                            ("account_name", "Party"), ("product_code", "Item code"), ("product_name", "Item"), ("store_name", "Store"),
                                            ("unit_code", "Recorded base unit"), ("signed_quantity", "Signed quantity"), ("rate", "Rate"), ("discount_percent", "Discount %"), ("signed_net_amount", "Signed line amount"), ("review_notes", "Review notes")],
                                     summary, footer, partial(self.export_report, report, filters))
                            def page(offset=0):
                                self.run(partial(self.reports.trade_detail, self.actor, self.company_id, family, offset=offset, limit=100, **filters), filter_result(dialog, render))
                            page()
                        filter_action(dialog, body, selection, "Show trade detail", selected)
                    self.run(partial(self.reports.trade_selectors, self.actor, self.company_id, family, kind=kind, payment_mode=payment_mode), choose)
                if len(choices) == 1 and len(next(iter(choices.values()))) == 1:
                    payment_mode, activities = next(iter(choices.items()))
                    choose_filters(next(iter(activities.values())), payment_mode)
                else:
                    dialog, body = self.dialog("Trade activity", width=650, height=420)
                    first_mode = next(iter(choices))
                    activity = Form(body, [("payment_mode", "Entry type", tuple(mode_labels[mode] for mode in choices)), ("kind", "Include", tuple(choices[first_mode]))], columns=1)
                    activity.frame.pack(fill="x")
                    labels_to_mode = {mode_labels[mode]: mode for mode in choices}
                    def change_mode(event=None):
                        mode = labels_to_mode[activity.inputs["payment_mode"].get()]
                        options = choices[mode]
                        activity.widgets["kind"].configure(values=tuple(options))
                        if activity.inputs["kind"].get() not in options:
                            activity.inputs["kind"].set(next(iter(options)))
                    activity.widgets["payment_mode"].bind("<<ComboboxSelected>>", change_mode)
                    def select_activity():
                        payment_mode = labels_to_mode[activity.inputs["payment_mode"].get()]
                        kind = choices[payment_mode][activity.inputs["kind"].get()]
                        choose_filters(kind, payment_mode, previous_dialog=dialog)
                    filter_action(dialog, body, activity, "Choose filters", select_activity)
            else:
                cash_book = values["report"] == "Cash book"
                def choose(data):
                    accounts = data["accounts"] if cash_book else data
                    selected_account = data["default_account_id"] if cash_book else None
                    dialog, body = self.dialog("Choose cash account" if cash_book else "Choose account", width=700, height=430 if cash_book else 360)
                    if cash_book:
                        text = "Shows every posting to the selected cash account, including journals, openings, trades, vouchers and reversals."
                        if not selected_account:
                            text += " Select an account; no eligible company cash account is configured."
                        ttk.Label(body, text=text, style="Muted.TLabel", wraplength=610).pack(anchor="w", pady=8)
                    choice = Choice(body, accounts, selected=selected_account, enter_select=True)
                    choice.widget.pack(fill="x", pady=15)
                    choice.add_search_button("Cash account" if cash_book else "Account")
                    def selected():
                        identifier = choice.get()
                        if identifier is None:
                            messagebox.showerror("Choose account", "Select the account to report.", parent=dialog)
                            return
                        def render(result):
                            offset, count = result["offset"], len(result["rows"])
                            def footer(bar):
                                ttk.Label(bar, text=f"{offset + 1 if count else 0}–{offset + count} of {result['total']} entries", style="Muted.TLabel").pack(side="left")
                                ttk.Button(bar, text="Next", state="normal" if offset + count < result["total"] else "disabled", command=lambda: page(offset + 100)).pack(side="right")
                                ttk.Button(bar, text="Previous", state="normal" if offset else "disabled", command=lambda: page(max(0, offset - 100))).pack(side="right", padx=8)
                            summary = f"Opening: {result['opening']}    Closing: {result['closing']}"
                            columns = [("business_date", "Date"), ("document_number", "Document"), ("debit", "Debit"), ("credit", "Credit"), ("balance", "Balance Dr / −Cr")]
                            if cash_book:
                                role = {"saved_snapshot": "Saved cash role", "baseline_snapshot": "Cash account in original records"}.get(result["cash_role_source"], "Current cash account")
                                summary += f"\n{label(result['account'])} · {role}. Cash in: {result['debit']} · Cash out: {result['credit']} · All posted activity."
                                columns = [("business_date", "Date"), ("document_number", "Document"), ("kind", "Kind"), ("narration", "Narration"),
                                           ("debit", "Cash in"), ("credit", "Cash out"), ("balance", "Balance Dr / −Cr")]
                            show(result["rows"], columns, summary, footer,
                                 partial(self.export_report, "cash_book" if cash_book else "account_ledger", {"account_id": identifier, **args}))
                        def page(offset=0):
                            report = self.reports.cash_book if cash_book else self.reports.ledger
                            self.run(partial(report, self.actor, self.company_id, identifier, limit=100, offset=offset, **args), filter_result(dialog, render))
                        page()
                    action = ttk.Button(body, text="Show cash book" if cash_book else "Show ledger", style="Accent.TButton", command=selected)
                    action.pack(anchor="e")
                    def accept_account(event=None):
                        if choice.get() is None:
                            return choice._enter_search(event)
                        selected()
                        return "break"
                    for sequence in ("<Return>", "<KP_Enter>"):
                        choice.widget.bind(sequence, accept_account)
                    choice.widget.bind("<<ChoiceAccepted>>", accept_account)
                    choice.widget.bind("<<ComboboxSelected>>", lambda event: action.focus_set())
                    dialog._focus_entry = True
                    dialog._initial_focus = choice.widget
                self.run(partial(self.reports.cash_accounts if cash_book else self.reports.accounts, self.actor, self.company_id), choose)
        run_report = ttk.Button(controls, text="Run report", style="Accent.TButton", command=generate)
        run_report.pack(side="right")
        form.set_enter_target(run_report)
        form.focus_first()

    def export_report(self, report, filters, *, company_id=None):
        from bifma.report_export import ReportExporter
        destination = filedialog.asksaveasfilename(parent=self, title="Save full printable report", defaultextension=".html", initialfile="bifma-report.html", filetypes=[("Printable HTML", "*.html")], confirmoverwrite=False)
        if destination:
            exporter = ReportExporter(self.path, security=self.security, reports=self.reports)
            self.run(partial(exporter.export_html, self.actor, company_id or self.company_id, report, destination, **filters),
                     lambda path: messagebox.showinfo("Report saved", f"Saved the full report with these filters to:\n{path}\n\nOpen the file in your browser and choose Print or Save as PDF.", parent=self))

    def preview_report(self, report, filters, *, company_id=None):
        from bifma.report_export import ReportExporter
        if self.future is not None or self.close_pending or self._closed:
            return
        destination = self._print_previews.destination()
        exporter = ReportExporter(self.path, security=self.security, reports=self.reports)
        self.run(partial(exporter.export_html, self.actor, company_id or self.company_id, report, destination, **filters),
                 self._open_print_preview)

    def audit_screen(self):
        from bifma.audit import AuditService
        service = AuditService(self.path, security=self.security)
        def render(selectors):
            self.heading("Audit log", "Activity across the native database. Dates and times are UTC; names show their current labels.")
            form = Form(self.main, [("company_id", "Company (blank for all)", "companies"), ("action", "Action", tuple([""] + selectors["actions"])),
                                    ("date_from", "From date (UTC)", "optional"), ("date_to", "To date (UTC)", "optional"),
                                    ("search", "Search actions, users or record IDs", "text")], choices=selectors, columns=2)
            form.frame.pack(fill="x")
            controls = ttk.Frame(self.main)
            controls.pack(fill="x", pady=8)
            result_box = ttk.Frame(self.main)
            result_box.pack(fill="both", expand=True)
            def show(result, filters, focus_result):
                for child in result_box.winfo_children():
                    child.destroy()
                bar = ttk.Frame(result_box)
                bar.pack(side="bottom", fill="x", pady=10)
                tree = self.tree(result_box, [("occurred_at_utc", "Time (UTC)"), ("actor_label", "User"), ("company_label", "Company"), ("action", "Action"), ("subject_label", "Record")], result["rows"])
                def details():
                    selected = tree.selection()
                    if not selected:
                        return
                    row = result["rows"][int(selected[0])]
                    dialog, body = self.dialog("Audit event", width=820, height=570)
                    ttk.Label(body, text=row["action"], style="Title.TLabel").pack(anchor="w", pady=8)
                    ttk.Label(body, text=row["occurred_at_utc"] + " · " + row["subject_label"], wraplength=740).pack(anchor="w", pady=5)
                    ttk.Button(body, text="Close", command=dialog.destroy).pack(side="bottom", anchor="e", pady=12)
                    content = tk.Text(body, wrap="word", height=16, font=("Consolas", 11))
                    content.pack(fill="both", expand=True)
                    content.insert("1.0", json.dumps(row["details"], ensure_ascii=False, indent=2))
                    content.configure(state="disabled")
                for sequence in ("<Return>", "<KP_Enter>", "<Double-1>"):
                    tree.bind(sequence, lambda event: (details(), "break")[-1])
                if tree.get_children():
                    first = tree.get_children()[0]
                    tree.selection_set(first)
                    tree.focus(first)
                if focus_result:
                    tree.focus_set()
                else:
                    form.focus_first()
                ttk.Button(bar, text="View event", command=details).pack(side="left")
                offset, count = result["offset"], len(result["rows"])
                ttk.Label(bar, text=f"{offset + 1 if count else 0}–{offset + count} of {result['total']} events", style="Muted.TLabel").pack(side="left", padx=15)
                ttk.Button(bar, text="Next", state="normal" if offset + count < result["total"] else "disabled", command=lambda: page(offset + 100, filters)).pack(side="right")
                ttk.Button(bar, text="Previous", state="normal" if offset else "disabled", command=lambda: page(max(0, offset - 100), filters)).pack(side="right", padx=8)
            def page(offset=0, filters=None, *, focus_result=True):
                filters = form.values() if filters is None else filters
                self.run(partial(service.list_events, self.actor, offset=offset, limit=100, **filters), lambda result: show(result, filters, focus_result))
            apply_filters = ttk.Button(controls, text="Apply filters", style="Accent.TButton", command=page)
            apply_filters.pack(side="right")
            form.set_enter_target(apply_filters)
            page(focus_result=False)
        self.run(partial(service.selectors, self.actor), render)

    def user_screen(self):
        def render(users):
            self.heading("Users & access", "Users need both company access and permission for each module. Administrators manage shared setup.")
            rows = [{"id": user.id, "username": user.username, "display_name": user.display_name, "is_admin": user.is_admin, "is_active": user.is_active} for user in users]
            bar = ttk.Frame(self.main)
            bar.pack(side="bottom", fill="x", pady=12)
            tree = self.tree(self.main, [("username", "Username"), ("display_name", "Name"), ("is_admin", "Administrator"), ("is_active", "Active")], rows)
            ttk.Button(bar, text="Add user", style="Accent.TButton", command=self.create_user_dialog).pack(side="left")
            def selected(action):
                selection = tree.selection()
                if selection:
                    action(users[int(selection[0])])
            ttk.Button(bar, text="Edit access", command=lambda: selected(self.access_dialog)).pack(side="left", padx=8)
            ttk.Button(bar, text="Administrator role", command=lambda: selected(self.admin_role_dialog)).pack(side="left", padx=(0, 8))
            ttk.Button(bar, text="Enable / disable", command=lambda: selected(lambda user: self.run(partial(self.security.set_active, self.actor, user.id, not user.is_active), lambda result: self.user_screen()))).pack(side="left")
            ttk.Button(bar, text="Reset passphrase", command=lambda: selected(self.reset_password_dialog)).pack(side="left", padx=8)
            for sequence in ("<Return>", "<KP_Enter>", "<Double-1>"):
                tree.bind(sequence, lambda event: (selected(self.access_dialog), "break")[-1])
            if tree.get_children():
                first = tree.get_children()[0]
                tree.selection_set(first)
                tree.focus(first)
            tree.focus_set()
        self.run(partial(self.security.list_users, self.actor), render)

    def admin_role_dialog(self, user):
        dialog, body = self.dialog("Administrator role · " + user.display_name, width=650, height=370)
        form = Form(body, [("is_admin", "Administrator", "bool")], values={"is_admin": user.is_admin}, columns=1)
        form.frame.pack(fill="x")
        ttk.Label(body, text="Administrators have full company access and manage users, setup, audit and backups. Changing this role signs the user out of every session.", wraplength=570, style="Muted.TLabel").pack(anchor="w", padx=15, pady=12)
        def save():
            value = form.values()["is_admin"]
            if value == user.is_admin:
                dialog.destroy()
                return
            own_role = user.id == self.actor.user.id
            def done(result):
                dialog.destroy()
                if own_role:
                    self.login()
                    self.status.set("Administrator role changed. Sign in again.")
                else:
                    self.user_screen()
            self.run(partial(self.security.set_admin, self.actor, user.id, value), done)
        action = ttk.Button(body, text="Save administrator role", style="Accent.TButton", command=save)
        action.pack(anchor="e", padx=15, pady=12)
        form.set_enter_target(action)
        dialog._focus_entry = True
        dialog._initial_focus = form.first_widget()
        form.focus_first()

    def create_user_dialog(self):
        dialog, body = self.dialog("Create user", width=720, height=570)
        form = Form(body, [("username", "Username", "text"), ("display_name", "Display name", "text"), ("password", "Passphrase", "password"), ("confirm", "Confirm passphrase", "password"), ("is_admin", "Administrator", "bool")], values={"is_admin": False})
        form.frame.pack(fill="x")
        def save():
            values = form.values()
            if values.pop("confirm") != values["password"]:
                messagebox.showerror("Check passphrase", "The passphrases do not match.", parent=dialog)
                Form._focus(form.widgets["confirm"])
                return
            self.run(partial(self.security.create_user, self.actor, **values), lambda user: (dialog.destroy(), self.user_screen()))
        action = ttk.Button(body, text="Create user", style="Accent.TButton", command=save)
        action.pack(anchor="e", pady=12)
        form.set_enter_target(action)
        dialog._focus_entry = True
        dialog._initial_focus = form.first_widget()
        form.focus_first()

    def access_dialog(self, user):
        def render(data):
            companies, access = data
            dialog, body = self.dialog("Access · " + user.display_name, width=760, height=690)
            ttk.Label(body, text="Set company access", style="Title.TLabel").pack(anchor="w", pady=8)
            toggles, company_checks = [], []
            for company in companies:
                selected = owned_variable(body, boolean=True, value=company["id"] in access["company_ids"])
                toggles.append((company["id"], selected))
                inactive = not company["is_active"]
                check = ttk.Checkbutton(body, text=label(company) + (" (inactive)" if inactive else ""), variable=selected,
                                        state="disabled" if inactive and not selected.get() else "normal")
                check.pack(anchor="w")
                company_checks.append(check)
            ttk.Label(body, text="Saving replaces this user's company access with the checked companies.", style="Muted.TLabel", wraplength=660).pack(anchor="w", pady=10)
            def company_access_saved(result):
                self.status.set("Company access saved. Choose the module permissions next.")
                Form._focus(module_box)
            save_companies = ttk.Button(body, text="Save company access", command=lambda: self.run(partial(self.security.set_company_access, self.actor, user.id, [identifier for identifier, selected in toggles if selected.get()]), company_access_saved))
            save_companies.pack(anchor="w")
            ttk.Separator(body).pack(fill="x", pady=18)
            module = owned_variable(body, value="sales")
            ttk.Label(body, text="Set module permissions", style="Title.TLabel").pack(anchor="w", pady=8)
            module_box = ttk.Combobox(body, textvariable=module, state="readonly", values=sorted(ACCESS_MODULES), width=30)
            module_box.pack(anchor="w")
            flags = {action: owned_variable(body, boolean=True, value=False) for action in ("read", "create", "update", "post")}
            checks = {}
            for action, flag in flags.items():
                checks[action] = ttk.Checkbutton(body, text=action.title(), variable=flag)
                checks[action].pack(anchor="w")
            def load_permissions(event=None):
                selected = access["permissions"].get(module.get(), [])
                for action, flag in flags.items():
                    applicable = action == "read" or module.get() in ENTRY_MODULES
                    flag.set(applicable and action in selected)
                    checks[action].configure(state="normal" if applicable else "disabled")
            module_box.bind("<<ComboboxSelected>>", load_permissions)
            load_permissions()
            ttk.Label(body, text="Saving replaces permissions for the selected module.", style="Muted.TLabel").pack(anchor="w", pady=8)
            ttk.Label(body, text="Shared setup, users, audit and backup require administrator access.", style="Muted.TLabel", wraplength=660).pack(anchor="w", pady=(0, 8))
            def save_permissions():
                selected_module = module.get()
                selected = [action for action, flag in flags.items() if flag.get()]
                def done(result):
                    access["permissions"][selected_module] = selected
                    self.status.set("Module permissions saved.")
                    dialog.destroy()
                self.run(partial(self.security.set_permissions, self.actor, user.id, selected_module, selected), done)
            save_module = ttk.Button(body, text="Save module permissions", command=save_permissions)
            save_module.pack(anchor="w")
            ttk.Button(body, text="Close", command=dialog.destroy).pack(anchor="e", pady=12)

            def advance(control, controls, target):
                for candidate in controls[controls.index(control) + 1:]:
                    if not candidate.instate(["disabled"]):
                        return Form._focus(candidate)
                # Space changes the checkbox; Enter accepts the displayed
                # selections and completes this section through its Save action.
                target.invoke()
                return "break"
            permission_controls = [module_box, *checks.values()]
            for controls, target in ((company_checks, save_companies), (permission_controls, save_module)):
                for control in controls:
                    for sequence in ("<Return>", "<KP_Enter>"):
                        control.bind(sequence, lambda event, control=control, controls=controls, target=target: advance(control, controls, target))
            dialog._focus_entry = True
            dialog._initial_focus = next((check for check in company_checks if not check.instate(["disabled"])), module_box)
        self.run(lambda: (self._all_masters("companies"), self.security.get_access(self.actor, user.id)), render)

    def password_dialog(self):
        dialog, body = self.dialog("Change passphrase", width=640, height=570)
        form = Form(body, [("current_password", "Current passphrase", "password"), ("new_password", "New passphrase", "password"), ("confirm", "Confirm new passphrase", "password")], columns=1)
        form.frame.pack(fill="x")
        def save():
            values = form.values()
            if values.pop("confirm") != values["new_password"]:
                messagebox.showerror("Check passphrase", "The passphrases do not match.", parent=dialog)
                Form._focus(form.widgets["confirm"])
                return
            def done(actor):
                self.actor = actor
                dialog.destroy()
                self.status.set("Passphrase changed. Other sessions were signed out.")
            actor = self.actor
            def change_checked():
                try:
                    return self.security.change_password(actor, **values)
                except AuthenticationError as error:
                    # A mistyped current password is a form error. A revoked
                    # or expired session must still use the normal sign-in path.
                    self.security.current_user(actor)
                    raise ValueError(str(error)) from None
            self.run(change_checked, done)
        action = ttk.Button(body, text="Change passphrase", style="Accent.TButton", command=save)
        action.pack(anchor="e", pady=12)
        form.set_enter_target(action)
        dialog._focus_entry = True
        dialog._initial_focus = form.first_widget()
        form.focus_first()

    def reset_password_dialog(self, user):
        dialog, body = self.dialog("Reset passphrase · " + user.display_name, width=640, height=470)
        form = Form(body, [("password", "New passphrase", "password"), ("confirm", "Confirm passphrase", "password")], columns=1)
        form.frame.pack(fill="x")
        def save():
            values = form.values()
            if values["password"] != values["confirm"]:
                messagebox.showerror("Check passphrase", "The passphrases do not match.", parent=dialog)
                Form._focus(form.widgets["confirm"])
                return
            self.run(partial(self.security.reset_password, self.actor, user.id, values["password"]), lambda result: (dialog.destroy(), self.user_screen()))
        action = ttk.Button(body, text="Reset passphrase", style="Accent.TButton", command=save)
        action.pack(anchor="e", pady=12)
        form.set_enter_target(action)
        dialog._focus_entry = True
        dialog._initial_focus = form.first_widget()
        form.focus_first()

    def logout(self):
        self.run(partial(self.security.logout, self.actor), lambda result: self.login())

    def close(self):
        if self._closed or self._finishing_close:
            return
        self.close_pending = True
        if self.future is not None:
            self.status.set("Finishing the current operation before closing…")
            return
        self._finish_close()

    def _finish_close(self):
        if getattr(self, "_closed", False) or getattr(self, "_finishing_close", False):
            return
        self._finishing_close = True
        self.close_pending = True
        binding = getattr(self, "_f8_binding", None)
        if binding is not None:
            unbind_callback(self._f8_root, "<F8>", binding)
            self._f8_binding = None
        for name in ("poll_id", "_startup_job"):
            pending = getattr(self, name, None)
            if pending is not None:
                self.after_cancel(pending)
                setattr(self, name, None)
        worker = getattr(self, "worker", None)
        if worker is not None:
            worker.shutdown(wait=True, cancel_futures=True)
        self.future = None
        previews = getattr(self, "_print_previews", None)
        if previews is not None:
            previews.close()
        self._detach_menu()
        uninstall_keyboard(self)
        super().destroy()
        self._closed = True

    def destroy(self):
        # A direct widget destruction request has the same worker lifetime as
        # Close. The application owner waits for _closed before destroying Tk.
        self.close()

    def report_callback_exception(self, exception, value, traceback):
        self.status.set("An unexpected interface error occurred.")
        messagebox.showerror("Interface error", "This screen could not complete the action. Reopen the screen and verify the document status before retrying.", parent=self)


class NativeWorkspace(_NativeInterface, tk.Frame):
    """Native entry screens embedded in the application's existing Tk root."""

    def __init__(self, master, database=None, *, baseline_source=None, archive_path=None):
        super().__init__(master)
        try:
            self._initialize_workspace(database, baseline_source=baseline_source, archive_path=archive_path)
        except BaseException:
            self._abort_initialization()
            raise


class NativeApp(_NativeInterface, tk.Tk):
    """Thin standalone host used by native workflow tests and diagnostics."""

    def __init__(self, database=None):
        super().__init__()
        try:
            self.title("BIFMA TRADERS · Workspace")
            self.geometry("1280x840")
            self.minsize(1050, 680)
            self._initialize_workspace(database)
            self.protocol("WM_DELETE_WINDOW", self.close)
        except BaseException:
            self._abort_initialization()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="BIFMA TRADERS native workspace (development build)")
    parser.add_argument("--database", type=Path, help="Open an existing native SQLite database")
    args = parser.parse_args(argv)
    app = NativeApp(args.database)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
