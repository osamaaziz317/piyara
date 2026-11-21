"""Read-only Tkinter workspace for reviewing recovered historical data.

This development preview is not the operational replacement application.
The repository owns all database access; widgets never execute SQL or import data.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import sqlite3
import sys
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from bifma.legacy_repository import LegacyRepository
from bifma.legacy_stock import LegacyStock
from bifma.tk_lifecycle import owned_variable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "legacy_recovered.sqlite3"
NAVY = "#142D3D"
NAVY_ACTIVE = "#244A5D"
TEAL = "#007E78"
BACKGROUND = "#F2F5F9"
SURFACE = "#FFFFFF"
TEXT = "#162F40"
MUTED = "#667C8D"
BORDER = "#DFE7EF"

SECTIONS = {
    "dashboard": ("Overview", "A clear view of your historical business records."),
    "products": ("Items", "Browse item codes, descriptions, units, and rates shared across companies."),
    "accounts": ("Accounts", "Browse the recovered chart of accounts shared across companies."),
    "stock": ("Stock summary", "Review reconstructed stock quantities and values by item and store."),
    "sales": ("Sales", "Review historical sales and their line items."),
    "purchases": ("Purchases", "Review historical purchases and their line items."),
    "sales_returns": ("Sales returns", "Review historical sales return documents and their line items."),
    "purchase_returns": ("Purchase returns", "Review historical purchase return documents and their line items."),
    "cash_receipts": ("Cash Receipts", "Review cash received vouchers and their account entries."),
    "cash_payments": ("Cash Payments", "Review cash paid vouchers and their account entries."),
    "journals": ("All vouchers", "Review journals, cash vouchers and opening balances from the existing records."),
    "breakage_receive": ("Breakage receipts", "Review received breakage vouchers."),
    "breakage_dispatch": ("Breakage dispatch", "Review dispatched breakage vouchers."),
    "stores": ("Stores", "Browse the original store locations."),
    "units": ("Units", "Browse the original units of measure."),
}
DOCUMENT_SECTIONS = {"sales", "purchases", "sales_returns", "purchase_returns", "journals", "cash_receipts", "cash_payments", "breakage_receive", "breakage_dispatch"}
STOCK_COLUMNS = [("code", "Code"), ("product", "Product"), ("store", "Store"),
                 ("opening", "Opening qty"), ("quantity_in", "In"), ("quantity_out", "Out"),
                 ("closing", "Closing qty"), ("closing_value", "Closing value")]


def display(value):
    if value is None or value == "":
        return "—"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def date_display(value):
    """Readable original date/time; never apply an inferred timezone conversion."""
    if value is None or value == "":
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return display(value)
    if parsed.tzinfo is not None:
        return display(value)
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    result = f"{parsed.day:02d} {months[parsed.month - 1]} {parsed.year:04d}"
    if (parsed.hour, parsed.minute, parsed.second) != (0, 0, 0):
        result += f" · {parsed.hour:02d}:{parsed.minute:02d}:{parsed.second:02d}"
    return result


class PreviewApp(tk.Tk):
    """Modern historical-data browser; all user operations are read-only."""

    def __init__(self, database):
        self.database = Path(database).resolve()
        if not self.database.is_file():
            raise FileNotFoundError(
                "The historical archive was not found. Create the reviewed staging archive "
                "using tools.stage_recovery, then launch with --database PATH. "
                "See docs/DATA_RECOVERY.md. No database has been created."
            )
        self.repository = LegacyRepository(self.database)
        company_rows = self.repository.companies()
        super().__init__()
        try:
            self._initialize_interface(company_rows)
        except BaseException:
            # This root may not be Tk's default root if an embedding caller
            # already owns another one. Release only the interpreter we made.
            self.close()
            if not self._closed:
                self.wait_window(self)
            raise

    def _initialize_interface(self, company_rows):
        self.title("BIFMA TRADERS · Development preview")
        self.geometry("1240x820")
        self.minsize(1050, 680)
        self.configure(bg=BACKGROUND)
        families = set(tkfont.families(self))
        self.family = next((name for name in ("Segoe UI", "Inter", "Noto Sans", "DejaVu Sans")
                            if name in families), "TkDefaultFont")
        self.current_section = "dashboard"
        self.offset = 0
        self.page_size = 100
        self._search_job = None
        self._closing = False
        self._closed = False
        self._close_poll_job = None
        self._stock_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bifma-stock")
        # Main-thread ownership survives screen changes. A superseded read may
        # still be using the archive after its display future is detached.
        self._stock_futures = set()
        self._stock_reap_job = None
        self._stock_generation = 0
        self._stock_future = None
        self._stock_poll_job = None
        self._stock_error = None
        self._stock_results = []
        self._stock_filtered = []
        self._dialogs = []
        self._navigation = {}
        self._company_ids = {}
        used_labels = set()
        for identifier, name in company_rows:
            label = str(name) if name else f"Company {identifier}"
            if label in used_labels:
                label = f"{label} ({identifier})"
            used_labels.add(label)
            self._company_ids[label] = identifier
        self.company_var = owned_variable(self, value=next(iter(self._company_ids), "No companies found"))
        self._configure_style()
        self._build_shell()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Control-f>", self._focus_search)
        self.show_section("dashboard")

    @property
    def company_id(self):
        return self._company_ids.get(self.company_var.get())

    def _configure_style(self):
        style = ttk.Style(self)
        if style.theme_use() != "clam":
            style.theme_use("clam")
        style.configure(".", font=(self.family, 10))
        style.configure("TFrame", background=BACKGROUND)
        style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
        style.configure("TButton", font=(self.family, 10), padding=(13, 8),
                        background=SURFACE, foreground=TEXT, bordercolor=BORDER)
        style.map("TButton", background=[("active", "#EAF1F6"), ("disabled", "#F2F5F8")],
                  foreground=[("disabled", "#9AABB7")])
        style.configure("Accent.TButton", background=TEAL, foreground="white", borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#006A65")])
        style.configure("TEntry", padding=(10, 9), fieldbackground=SURFACE, bordercolor=BORDER)
        style.configure("TCombobox", padding=(9, 8), fieldbackground=SURFACE,
                        background=SURFACE, bordercolor=BORDER, arrowsize=14)
        style.map("TCombobox", fieldbackground=[("readonly", SURFACE)],
                  foreground=[("readonly", TEXT)])
        style.configure("Treeview", font=(self.family, 10), rowheight=35,
                        background=SURFACE, fieldbackground=SURFACE,
                        foreground=TEXT, borderwidth=0)
        style.configure("Treeview.Heading", font=(self.family, 10, "bold"),
                        background="#EAF0F5", foreground=TEXT, padding=(10, 11),
                        relief="flat", borderwidth=0)
        style.map("Treeview", background=[("selected", "#D6EEEB")],
                  foreground=[("selected", "#075D58")])
        style.map("Treeview.Heading", background=[("active", "#E2EAF1")])
        style.configure("TNotebook", background=BACKGROUND, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 11), font=(self.family, 10),
                        background="#E8EEF4", foreground=MUTED)
        style.map("TNotebook.Tab", background=[("selected", SURFACE)],
                  foreground=[("selected", TEAL)])
        style.configure("Vertical.TScrollbar", arrowsize=13, borderwidth=0)
        style.configure("Horizontal.TScrollbar", arrowsize=13, borderwidth=0)
        style.configure("Sidebar.Vertical.TScrollbar", background=NAVY_ACTIVE, troughcolor=NAVY,
                        bordercolor=NAVY, arrowcolor="#88A5B7", lightcolor=NAVY_ACTIVE,
                        darkcolor=NAVY_ACTIVE, arrowsize=10, borderwidth=0)

    def _label(self, parent, text, *, size=10, color=TEXT, bg=SURFACE, bold=False, **kwargs):
        return tk.Label(parent, text=text, font=(self.family, size, "bold" if bold else "normal"),
                        fg=color, bg=bg, **kwargs)

    def _build_shell(self, parent=None):
        parent = self if parent is None else parent
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=1)
        sidebar = tk.Frame(parent, bg=NAVY, width=225)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)
        brand = tk.Frame(sidebar, bg=NAVY)
        brand.grid(row=0, column=0, padx=20, pady=(25, 22), sticky="ew")
        self._label(brand, "BT", size=17, bold=True, color="white", bg=TEAL,
                    padx=9, pady=9).pack(side="left")
        self._label(brand, "BIFMA\nTRADERS", size=12, bold=True, color="white", bg=NAVY,
                    justify="left").pack(side="left", padx=(12, 0))
        nav_host = tk.Frame(sidebar, bg=NAVY)
        nav_host.grid(row=1, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(1, weight=1)
        nav_host.grid_rowconfigure(0, weight=1)
        nav_host.grid_columnconfigure(0, weight=1)
        nav_canvas = tk.Canvas(nav_host, bg=NAVY, highlightthickness=0, width=205)
        nav_scroll = ttk.Scrollbar(nav_host, orient="vertical", command=nav_canvas.yview,
                                   style="Sidebar.Vertical.TScrollbar")
        nav_canvas.configure(yscrollcommand=nav_scroll.set)
        nav_canvas.grid(row=0, column=0, sticky="nsew")
        nav_scroll.grid(row=0, column=1, sticky="ns")
        navigation = tk.Frame(nav_canvas, bg=NAVY)
        navigation.grid_columnconfigure(0, weight=1)
        nav_window = nav_canvas.create_window((0, 0), window=navigation, anchor="nw")
        nav_canvas.bind("<Configure>", lambda event: nav_canvas.itemconfigure(nav_window, width=event.width))
        navigation.bind("<Configure>", lambda _event: nav_canvas.configure(scrollregion=nav_canvas.bbox("all")))

        def scroll_navigation(event):
            if getattr(event, "num", None) == 4:
                direction = -1
            elif getattr(event, "num", None) == 5:
                direction = 1
            else:
                direction = -1 if event.delta > 0 else 1
            nav_canvas.yview_scroll(direction, "units")
            return "break"

        def bind_navigation_scroll(widget):
            widget.bind("<MouseWheel>", scroll_navigation)
            widget.bind("<Button-4>", scroll_navigation)
            widget.bind("<Button-5>", scroll_navigation)

        def show_focused_navigation(event):
            top = nav_canvas.canvasy(0)
            bottom = top + nav_canvas.winfo_height()
            widget_top = event.widget.winfo_y()
            widget_bottom = widget_top + event.widget.winfo_height()
            height = max(1, navigation.winfo_height())
            if widget_top < top:
                nav_canvas.yview_moveto(widget_top / height)
            elif widget_bottom > bottom:
                nav_canvas.yview_moveto((widget_bottom - nav_canvas.winfo_height()) / height)

        bind_navigation_scroll(nav_canvas)
        bind_navigation_scroll(navigation)
        groups = [
            ("WORKSPACE", ["dashboard"]),
            ("BUSINESS", ["products", "accounts", "stock"]),
            ("FINANCIAL", ["cash_receipts", "cash_payments", "journals"]),
            ("TRANSACTIONS", ["sales", "purchases", "sales_returns", "purchase_returns", "breakage_receive", "breakage_dispatch"]),
            ("DIRECTORIES", ["stores", "units"]),
        ]
        row = 0
        for title, sections in groups:
            label = self._label(navigation, title, size=8, bold=True, color="#88A5B7", bg=NAVY, anchor="w")
            label.grid(row=row, column=0, padx=22, pady=(10, 6), sticky="ew")
            bind_navigation_scroll(label)
            row += 1
            for section in sections:
                button = tk.Button(navigation, text=SECTIONS[section][0], anchor="w", bd=0,
                                   relief="flat", highlightthickness=0, padx=15, pady=7,
                                   bg=NAVY, fg="#D8E4ED", activebackground=NAVY_ACTIVE,
                                   activeforeground="white", cursor="hand2", takefocus=True,
                                   font=(self.family, 10),
                                   command=lambda key=section: self.show_section(key))
                button.grid(row=row, column=0, padx=9, pady=1, sticky="ew")
                self._navigation[section] = button
                bind_navigation_scroll(button)
                button.bind("<FocusIn>", show_focused_navigation)
                row += 1
        self._label(sidebar, "EXISTING RECORDS\nOriginal data · Read only", size=8,
                    color="#88A5B7", bg=NAVY, justify="left", anchor="w"
                    ).grid(row=2, column=0, padx=22, pady=20, sticky="ew")

        workspace = tk.Frame(parent, bg=BACKGROUND)
        workspace.grid(row=0, column=1, sticky="nsew")
        workspace.grid_columnconfigure(0, weight=1)
        workspace.grid_rowconfigure(2, weight=1)
        header = tk.Frame(workspace, bg=SURFACE, padx=26, pady=14)
        header.grid(row=0, column=0, sticky="ew")
        self._label(header, "Business workspace", size=12, bold=True).pack(side="left")
        selector = tk.Frame(header, bg=SURFACE)
        selector.pack(side="right")
        self._label(selector, "COMPANY", size=8, bold=True, color=MUTED).pack(side="left", padx=(0, 10))
        self.company_selector = ttk.Combobox(selector, textvariable=self.company_var,
                                             values=list(self._company_ids), state="readonly", width=31)
        self.company_selector.pack(side="left")
        self.company_selector.bind("<<ComboboxSelected>>", self._company_changed)
        notice = tk.Frame(workspace, bg="#FFF4DC", padx=26, pady=9)
        notice.grid(row=1, column=0, sticky="ew")
        self._label(notice, "Existing records · Original values preserved · Read only",
                    size=9, color="#866021", bg="#FFF4DC", anchor="w").pack(fill="x")
        self.content = tk.Frame(workspace, bg=BACKGROUND, padx=26, pady=24)
        self.content.grid(row=2, column=0, sticky="nsew")
        footer = tk.Frame(workspace, bg=BACKGROUND, padx=26, pady=10)
        footer.grid(row=3, column=0, sticky="ew")
        self.status_var = owned_variable(footer, value="Historical archive connected")
        tk.Label(footer, textvariable=self.status_var, bg=BACKGROUND, fg=MUTED,
                 font=(self.family, 8), anchor="w").pack(side="left")
        self._label(footer, "BIFMA TRADERS", size=8, color=MUTED, bg=BACKGROUND).pack(side="right")

    def _company_changed(self, _event=None):
        if self._closing:
            return
        for dialog in list(self._dialogs):
            if dialog.winfo_exists():
                dialog.destroy()
        self._dialogs.clear()
        self.show_section(self.current_section)

    def _cancel_search(self):
        if self._search_job is not None:
            self.after_cancel(self._search_job)
            self._search_job = None

    def show_section(self, section):
        if self._closing:
            return
        if section not in SECTIONS:
            raise ValueError("Unknown preview section")
        self._cancel_search()
        self._invalidate_stock()
        self.current_section = section
        self.offset = 0
        self.active_rows = []
        for widget in self.content.winfo_children():
            widget.destroy()
        for key, button in self._navigation.items():
            button.configure(bg=NAVY_ACTIVE if key == section else NAVY,
                             fg="white" if key == section else "#D8E4ED")
        if not self._company_ids:
            self._label(self.content, "No companies found", size=24, bold=True,
                        bg=BACKGROUND).pack(anchor="w")
            self._label(self.content, "The selected archive has no company records to display.",
                        color=MUTED, bg=BACKGROUND).pack(anchor="w", pady=12)
            return
        if section == "dashboard":
            self._dashboard()
        elif section == "stock":
            self._stock_screen()
        else:
            self._records_screen()

    def _heading(self, title, subtitle):
        frame = tk.Frame(self.content, bg=BACKGROUND)
        frame.pack(fill="x", pady=(0, 22))
        self._label(frame, title, size=23, bold=True, bg=BACKGROUND).pack(anchor="w")
        self._label(frame, subtitle, color=MUTED, bg=BACKGROUND).pack(anchor="w", pady=(6, 0))

    def _dashboard(self):
        self._heading(*SECTIONS["dashboard"])
        summary = self.repository.summary(self.company_id)
        cards = tk.Frame(self.content, bg=BACKGROUND)
        cards.pack(fill="x", pady=(0, 22))
        metrics = [("ITEMS · SHARED", "product_count", "products"), ("ACCOUNTS · SHARED", "account_count", "accounts"),
                   ("SALES DOCUMENTS", "sale_count", "sales"), ("ALL VOUCHERS", "journal_count", "journals")]
        for number, (label, key, section) in enumerate(metrics):
            cards.grid_columnconfigure(number, weight=1, uniform="cards")
            card = tk.Frame(cards, bg=SURFACE, highlightbackground=BORDER,
                            highlightthickness=1, padx=18, pady=17)
            card.grid(row=0, column=number, padx=(0 if number == 0 else 10, 0), sticky="nsew")
            self._label(card, label, size=8, color=MUTED, bold=True).pack(anchor="w")
            value = summary.get(key, 0)
            self._label(card, f"{int(value):,}", size=25, bold=True).pack(anchor="w", pady=(9, 8))
            tk.Button(card, text="View records  →", bg=SURFACE, fg=TEAL, bd=0,
                      activebackground=SURFACE, activeforeground="#005E58", cursor="hand2",
                      font=(self.family, 9), command=lambda name=section: self.show_section(name)
                      ).pack(anchor="w")
        info = tk.Frame(self.content, bg="#E6F2F1", padx=18, pady=15)
        info.pack(fill="x", pady=(0, 22))
        latest = summary.get("latest_sale_date")
        self._label(info, "Historical snapshot", bold=True, color="#14645E", bg="#E6F2F1").pack(anchor="w")
        note = (f"Latest recorded sale: {date_display(latest)}. " if latest else "No sales recorded for this company. ")
        note += "Select a section to explore the recovered records."
        self._label(info, note, size=9, color="#3A716C", bg="#E6F2F1",
                    anchor="w", wraplength=690, justify="left").pack(anchor="w", pady=(5, 0))
        recent_header = tk.Frame(self.content, bg=BACKGROUND)
        recent_header.pack(fill="x", pady=(0, 12))
        self._label(recent_header, "Sales records", size=13, bold=True, bg=BACKGROUND).pack(side="left")
        ttk.Button(recent_header, text="View all sales", command=lambda: self.show_section("sales")).pack(side="right")
        page = self.repository.records("sales", self.company_id, limit=8)
        table_frame, tree = self._make_tree(self.content, page.columns)
        table_frame.pack(fill="both", expand=True)
        rows = page.rows
        self._fill_tree(tree, page.columns, rows)
        tree.bind("<Double-1>", lambda event: self._selected_document("sales", tree, rows, event))
        tree.bind("<Return>", lambda event: self._selected_document("sales", tree, rows))
        if not rows:
            self.status_var.set("No sales records for this company")
        else:
            self.status_var.set("Double-click a sales record to view its details")

    def _make_tree(self, parent, columns):
        container = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        tree = ttk.Treeview(container, columns=[key for key, _ in columns], show="headings", selectmode="browse")
        for key, label in columns:
            lowered = key.lower()
            wide = any(word in lowered for word in ("description", "name", "narration", "account", "customer", "supplier", "product"))
            numeric = any(word in lowered for word in ("amount", "total", "rate", "balance", "debit", "credit", "quantity", "qty", "received", "net", "discount", "opening", "closing", "value"))
            width = 230 if wide else (210 if "date" in lowered else 115)
            tree.heading(key, text=label, anchor="w")
            tree.column(key, width=width, minwidth=90, anchor="e" if numeric else "w", stretch=wide)
        vertical = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(container, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        tree.tag_configure("alternate", background="#F7F9FC")
        return container, tree

    def _fill_tree(self, tree, columns, rows):
        children = tree.get_children()
        if children:
            tree.delete(*children)
        for index, row in enumerate(rows):
            tree.insert("", "end", iid=str(index), values=[
                date_display(row.get(key)) if "date" in key.casefold() else display(row.get(key))
                for key, _ in columns],
                        tags=("alternate",) if index % 2 else ())

    def _records_screen(self):
        self._heading(*SECTIONS[self.current_section])
        toolbar = tk.Frame(self.content, bg=BACKGROUND)
        toolbar.pack(fill="x", pady=(0, 15))
        search_label = "Voucher, date, account or narration" if self.current_section in {"cash_receipts", "cash_payments", "journals"} else "Search records"
        self._label(toolbar, search_label, size=9, color=MUTED, bg=BACKGROUND).pack(side="left", padx=(0, 12))
        self.search_var = owned_variable(toolbar)
        self.search_entry = ttk.Entry(toolbar, textvariable=self.search_var, width=32)
        self.search_entry.pack(side="left")
        self.search_var.trace_add("write", self._schedule_search)
        self.search_entry.bind("<Return>", self._search_now)
        ttk.Button(toolbar, text="Clear", command=lambda: self.search_var.set("")).pack(side="left", padx=(8, 0))
        self.count_var = owned_variable(toolbar)
        tk.Label(toolbar, textvariable=self.count_var, bg=BACKGROUND, fg=MUTED,
                 font=(self.family, 9)).pack(side="right")
        self.table_host = tk.Frame(self.content, bg=BACKGROUND)
        self.table_host.pack(fill="both", expand=True)
        pagination = tk.Frame(self.content, bg=BACKGROUND)
        pagination.pack(fill="x", pady=(14, 0))
        self.page_var = owned_variable(pagination)
        tk.Label(pagination, textvariable=self.page_var, bg=BACKGROUND, fg=MUTED,
                 font=(self.family, 9)).pack(side="left")
        self.next_button = ttk.Button(pagination, text="Next  →", command=lambda: self._change_page(1))
        self.next_button.pack(side="right")
        self.previous_button = ttk.Button(pagination, text="←  Previous", command=lambda: self._change_page(-1))
        self.previous_button.pack(side="right", padx=(0, 8))
        self.record_tree = None
        self._load_page()

    def _schedule_search(self, *_args):
        if self._closing:
            return
        self._cancel_search()
        self._search_job = self.after(300, self._search_now)

    def _search_now(self, _event=None):
        if self._closing:
            return "break"
        self._cancel_search()
        self.offset = 0
        if self.current_section == "stock":
            self._filter_stock()
        else:
            self._load_page()
        return "break"

    def _change_page(self, direction):
        if self._closing:
            return
        self.offset = max(0, self.offset + direction * self.page_size)
        if self.current_section == "stock":
            self._render_stock_page()
        else:
            self._load_page()

    def _load_page(self):
        page = self.repository.records(self.current_section, self.company_id,
                                       search=self.search_var.get().strip(),
                                       offset=self.offset, limit=self.page_size)
        self.active_rows = page.rows
        if self.record_tree is None:
            container, self.record_tree = self._make_tree(self.table_host, page.columns)
            container.pack(fill="both", expand=True)
            self.record_tree.bind("<Double-1>", self._open_active_document)
            self.record_tree.bind("<Return>", self._open_active_document)
        self._fill_tree(self.record_tree, page.columns, page.rows)
        self.count_var.set(f"{page.total:,} records")
        start = self.offset + 1 if page.rows else 0
        end = self.offset + len(page.rows)
        self.page_var.set(f"{start:,}–{end:,} of {page.total:,} records")
        self.previous_button.configure(state="normal" if self.offset > 0 else "disabled")
        self.next_button.configure(state="normal" if end < page.total else "disabled")
        if not page.rows:
            self.status_var.set("No matching records" if self.search_var.get().strip() else "No records in this section")
        elif self.current_section in DOCUMENT_SECTIONS:
            self.status_var.set("Double-click a row or press Enter to view the complete document")
        else:
            self.status_var.set("Historical records · Search with Ctrl+F")

    def _invalidate_stock(self):
        """Detach pending work from its widgets; running reads never touch Tk."""
        self._stock_generation += 1
        if self._stock_poll_job is not None:
            self.after_cancel(self._stock_poll_job)
            self._stock_poll_job = None
        if self._stock_future is not None:
            self._stock_future.cancel()
            self._stock_future = None
        self._prune_stock_reads()

    def _prune_stock_reads(self):
        """Release completed future/results on Tk's thread, without callbacks."""
        self._stock_futures = {future for future in self._stock_futures if not future.done()}

    def _reap_stock_reads(self):
        self._stock_reap_job = None
        self._prune_stock_reads()
        if self._stock_futures and not self._closing:
            self._stock_reap_job = self.after(60, self._reap_stock_reads)

    def _stock_screen(self):
        self._heading(*SECTIONS["stock"])
        notice = tk.Frame(self.content, bg="#FFF4DC", padx=16, pady=12)
        notice.pack(fill="x", pady=(0, 14))
        self._label(notice, "Reconstructed from the backup; original report comparison pending",
                    size=9, color="#866021", bg="#FFF4DC", bold=True, anchor="w").pack(anchor="w")
        self.stock_hint_var = owned_variable(notice)
        tk.Label(notice, textvariable=self.stock_hint_var, bg="#FFF4DC", fg="#866021",
                 font=(self.family, 8), wraplength=690, justify="left", anchor="w"
                 ).pack(anchor="w", pady=(5, 0))
        controls = tk.Frame(self.content, bg=BACKGROUND)
        controls.pack(fill="x", pady=(0, 14))
        self.stock_stream_var = owned_variable(controls, value="Regular stock")
        self.stock_from_var = owned_variable(controls)
        self.stock_to_var = owned_variable(controls)
        self.stock_store_var = owned_variable(controls, value="All stores")
        stores = self.repository.stock_stores(self.company_id)
        self._stock_store_names = {str(identifier): str(name or identifier) for identifier, name in stores}
        self._stock_store_ids = {"All stores": None}
        for identifier, name in stores:
            label = str(name or identifier)
            if label in self._stock_store_ids:
                label = f"{label} ({identifier})"
            self._stock_store_ids[label] = identifier
        specifications = [("Stock type", self.stock_stream_var, ("Regular stock", "Breakage stock"), 14),
                          ("From · YYYY-MM-DD", self.stock_from_var, None, 11),
                          ("To · YYYY-MM-DD", self.stock_to_var, None, 11),
                          ("Store", self.stock_store_var, tuple(self._stock_store_ids), 14)]
        for column, (label, variable, values, width) in enumerate(specifications):
            controls.grid_columnconfigure(column, weight=1)
            field = tk.Frame(controls, bg=BACKGROUND)
            field.grid(row=0, column=column, sticky="ew", padx=(0, 10))
            self._label(field, label, size=8, color=MUTED, bg=BACKGROUND).pack(anchor="w", pady=(0, 5))
            if values is None:
                widget = ttk.Entry(field, textvariable=variable, width=width)
                widget.bind("<Return>", lambda _event: self._begin_stock_load())
            else:
                widget = ttk.Combobox(field, textvariable=variable, values=values,
                                      state="readonly", width=width)
            widget.pack(fill="x")
        ttk.Button(controls, text="Apply", style="Accent.TButton", command=self._begin_stock_load
                   ).grid(row=0, column=4, sticky="s")
        self.stock_stream_var.trace_add("write", lambda *_args: self._update_stock_hint())
        self._update_stock_hint()
        search = tk.Frame(self.content, bg=BACKGROUND)
        search.pack(fill="x", pady=(0, 12))
        self._label(search, "Find item", size=9, color=MUTED, bg=BACKGROUND).pack(side="left", padx=(0, 10))
        self.search_var = owned_variable(search)
        self.search_entry = ttk.Entry(search, textvariable=self.search_var, width=30)
        self.search_entry.pack(side="left")
        self.search_entry.bind("<Return>", self._search_now)
        self.search_var.trace_add("write", self._schedule_search)
        ttk.Button(search, text="Clear", command=lambda: self.search_var.set("")).pack(side="left", padx=(8, 0))
        self.count_var = owned_variable(search, value="Loading…")
        tk.Label(search, textvariable=self.count_var, bg=BACKGROUND, fg=MUTED,
                 font=(self.family, 9)).pack(side="right")
        message = tk.Label(self.content, bg=BACKGROUND, fg=MUTED,
                           font=(self.family, 8), anchor="w")
        self.stock_message_var = owned_variable(message, value="")
        message.configure(textvariable=self.stock_message_var)
        message.pack(fill="x", pady=(0, 9))
        frame, self.record_tree = self._make_tree(self.content, STOCK_COLUMNS)
        frame.pack(fill="both", expand=True)
        pagination = tk.Frame(self.content, bg=BACKGROUND)
        pagination.pack(fill="x", pady=(12, 0))
        self.page_var = owned_variable(pagination)
        tk.Label(pagination, textvariable=self.page_var, bg=BACKGROUND, fg=MUTED,
                 font=(self.family, 9)).pack(side="left")
        self.next_button = ttk.Button(pagination, text="Next  →", command=lambda: self._change_page(1), state="disabled")
        self.next_button.pack(side="right")
        self.previous_button = ttk.Button(pagination, text="←  Previous", command=lambda: self._change_page(-1), state="disabled")
        self.previous_button.pack(side="right", padx=(0, 8))
        self._stock_results = []
        self._stock_filtered = []
        self._begin_stock_load()

    def _update_stock_hint(self):
        if self.stock_stream_var.get() == "Breakage stock":
            note = "The recovered rule counts both breakage receipts and dispatches as incoming stock. This historical behavior is preserved here."
        else:
            note = "The reconstructed report excludes delivery-linked sales. Values follow the original database costing rules and await verification."
        self.stock_hint_var.set(note)

    def _begin_stock_load(self):
        if self._closing or self.current_section != "stock":
            return
        self._invalidate_stock()
        lower, upper = self.stock_from_var.get().strip(), self.stock_to_var.get().strip()
        try:
            for bound in (lower, upper):
                if bound and (len(bound) != 10 or date.fromisoformat(bound).isoformat() != bound):
                    raise ValueError
            if lower and upper and lower > upper:
                self._render_stock_page()
                self.stock_message_var.set("The start date must be on or before the end date.")
                return
        except ValueError:
            self._render_stock_page()
            self.stock_message_var.set("Enter valid dates as YYYY-MM-DD, or leave either date blank.")
            return
        generation = self._stock_generation
        company = self.company_id
        stream = "breakage" if self.stock_stream_var.get() == "Breakage stock" else "regular"
        store = self._stock_store_ids[self.stock_store_var.get()]
        self._stock_error = None
        self._stock_results = []
        self._stock_filtered = []
        self.offset = 0
        self._fill_tree(self.record_tree, STOCK_COLUMNS, [])
        self.previous_button.configure(state="disabled")
        self.next_button.configure(state="disabled")
        self.count_var.set("Loading…")
        self.page_var.set("")
        self.stock_message_var.set("Reconstructing historical stock. You can continue browsing other sections.")
        self.status_var.set("Stock reconstruction in progress")
        # Capture all Tk values above. The worker receives plain immutable filters
        # and creates its own short-lived readonly SQLite connection.
        future = self._stock_executor.submit(
            LegacyStock(self.database).balances, company, date_from=lower or None,
            date_to=upper or None, store_id=store, stream=stream,
        )
        self._stock_future = future
        self._stock_futures.add(future)
        if self._stock_reap_job is None:
            self._stock_reap_job = self.after(60, self._reap_stock_reads)
        self._stock_poll_job = self.after(60, lambda: self._poll_stock(generation, company, future, lower, upper, stream))

    def _poll_stock(self, generation, company, future, lower, upper, stream):
        if self._closing or generation != self._stock_generation or self.current_section != "stock" or company != self.company_id:
            return
        self._stock_poll_job = None
        if not future.done():
            self._stock_poll_job = self.after(60, lambda: self._poll_stock(generation, company, future, lower, upper, stream))
            return
        self._stock_future = None
        try:
            self._stock_results = future.result()
        except Exception as error:
            self._stock_error = error
            self.count_var.set("Unavailable")
            self.stock_message_var.set("This reconstruction could not be completed. Review the archive and its stock compatibility notes.")
            self.status_var.set("Stock report unavailable · Historical archive unchanged")
            return
        period = f"{date_display(lower) if lower else 'All history'} to {date_display(upper) if upper else 'latest available'}"
        self.stock_message_var.set(f"{stream.capitalize()} stock · {period} · Values are historical reconstructions")
        self.status_var.set("Original stock report comparison pending")
        self._filter_stock()

    def _filter_stock(self):
        if self._closing or self._stock_future is not None:
            return
        query = self.search_var.get().strip().casefold()
        self._stock_filtered = [row for row in self._stock_results
                                if not query or query in row.product_code.casefold()
                                or query in row.product_name.casefold()]
        self.offset = 0
        self._render_stock_page()

    def _render_stock_page(self):
        selected = self._stock_filtered[self.offset:self.offset + self.page_size]
        rows = [{"code": row.product_code, "product": row.product_name,
                 "store": self._stock_store_names.get(str(row.store_id), f"Store {row.store_id}" if row.store_id else "Unassigned"),
                 "opening": row.opening_quantity, "quantity_in": row.quantity_in,
                 "quantity_out": row.quantity_out, "closing": row.closing_quantity,
                 "closing_value": row.closing_value} for row in selected]
        self._fill_tree(self.record_tree, STOCK_COLUMNS, rows)
        total = len(self._stock_filtered)
        self.count_var.set(f"{total:,} item / store balances")
        start = self.offset + 1 if rows else 0
        end = self.offset + len(rows)
        self.page_var.set(f"{start:,}–{end:,} of {total:,} balances")
        self.previous_button.configure(state="normal" if self.offset > 0 else "disabled")
        self.next_button.configure(state="normal" if end < total else "disabled")

    def _focus_search(self, _event=None):
        if self._closing:
            return "break"
        if self.current_section != "dashboard" and hasattr(self, "search_entry") and self.search_entry.winfo_exists():
            self.search_entry.focus_set()
            self.search_entry.selection_range(0, "end")
        return "break"

    def _open_active_document(self, event=None):
        return self._selected_document(self.current_section, self.record_tree, self.active_rows, event)

    def _selected_document(self, section, tree, rows, event=None):
        if self._closing or section not in DOCUMENT_SECTIONS:
            return
        if event is not None and getattr(event, "num", None) == 1:
            clicked = tree.identify_row(event.y)
            if not clicked:
                return
            tree.selection_set(clicked)
        selected = tree.selection()
        if selected:
            self.open_document(section, rows[int(selected[0])]["id"])
            return "break"

    def open_document(self, section, record_id):
        if self._closing:
            return
        document = self.repository.document(section, record_id, self.company_id)
        dialog = tk.Toplevel(self)
        self._dialogs.append(dialog)
        dialog.title(f"{document['title']} · BIFMA TRADERS")
        dialog.geometry("1040x700")
        dialog.minsize(850, 580)
        dialog.configure(bg=BACKGROUND)
        dialog.transient(self)
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)
        header = tk.Frame(dialog, bg=SURFACE, padx=24, pady=20)
        header.grid(row=0, column=0, sticky="ew")
        self._label(header, document["title"], size=18, bold=True).pack(anchor="w")
        self._label(header, "Historical document · Changes disabled", size=9, color=MUTED
                    ).pack(anchor="w", pady=(5, 0))
        notebook = ttk.Notebook(dialog)
        dialog.notebook = notebook
        notebook.grid(row=1, column=0, sticky="nsew", padx=20, pady=18)
        overview = tk.Frame(notebook, bg=SURFACE)
        notebook.add(overview, text="Document details")
        overview.grid_rowconfigure(0, weight=1)
        overview.grid_columnconfigure(0, weight=1)
        canvas = tk.Canvas(overview, bg=SURFACE, highlightthickness=0)
        scrollbar = ttk.Scrollbar(overview, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        fields = tk.Frame(canvas, bg=SURFACE, padx=20, pady=20)
        window = canvas.create_window((0, 0), window=fields, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        fields.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        for index in (0, 1):
            fields.grid_columnconfigure(index, weight=1, uniform="fields")
        for number, (label, value) in enumerate(document["fields"]):
            field = tk.Frame(fields, bg=SURFACE, padx=10, pady=11)
            field.grid(row=number // 2, column=number % 2, sticky="nsew")
            self._label(field, label, size=9, color=MUTED).pack(anchor="w")
            shown = date_display(value) if label.casefold() == "date" else display(value)
            self._label(field, shown, size=11, wraplength=370, justify="left",
                        anchor="w").pack(anchor="w", pady=(6, 0))
        if document.get("notes"):
            notes = tk.Frame(fields, bg="#FFF4DC", padx=16, pady=14)
            notes.grid(row=(len(document["fields"]) + 1) // 2, column=0, columnspan=2,
                       sticky="ew", pady=(16, 0))
            self._label(notes, "Record notes", bold=True, bg="#FFF4DC", color="#866021").pack(anchor="w")
            self._label(notes, "\n\n".join(document["notes"]), size=9, color="#866021",
                        bg="#FFF4DC", wraplength=780, justify="left").pack(anchor="w", pady=(6, 0))
        line_items = tk.Frame(notebook, bg=SURFACE, padx=12, pady=12)
        notebook.add(line_items, text=f"Line items ({len(document['rows']):,})")
        if document["columns"]:
            frame, tree = self._make_tree(line_items, document["columns"])
            frame.pack(fill="both", expand=True)
            self._fill_tree(tree, document["columns"], document["rows"])
        else:
            self._label(line_items, "No line-item columns are available for this document.", color=MUTED
                        ).pack(anchor="w", padx=12, pady=16)
        footer = tk.Frame(dialog, bg=BACKGROUND, padx=20, pady=12)
        footer.grid(row=2, column=0, sticky="ew")
        ttk.Button(footer, text="Close", command=dialog.destroy).pack(side="right")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.bind("<Control-w>", lambda _event: dialog.destroy())
        # A real double-click still holds X11's pointer grab while this window
        # is being created. Grabbing an unmapped dialog then fails even though
        # its document loaded successfully. Let Tk finish displaying it first;
        # do not run a nested wait loop or activate a dialog closed before map.
        def activate(event):
            if event.widget is not dialog or self._closing or not dialog.winfo_viewable():
                return
            dialog.unbind("<Visibility>", activation_binding)
            dialog.grab_set()
            dialog.focus_set()
        activation_binding = dialog.bind("<Visibility>", activate, add=True)
        return dialog

    def report_callback_exception(self, exception, value, traceback):
        # Classify the failure without exposing recovered values or exception
        # locals. A widget/window-manager error is not an unreadable archive.
        if issubclass(exception, sqlite3.Error):
            title = "Unable to read this view"
            message = ("The historical records could not be read for this view. "
                       "The archive has not been changed. Reopen the view or review the recovery report.")
        else:
            title = "Interface error"
            message = ("The interface could not complete this action. "
                       "The archive has not been changed. Close and reopen the affected window.")
        self.status_var.set(title)
        messagebox.showerror(title, message, parent=self)

    def smoke_test(self):
        """Exercise all companies/screens/stock streams/documents, then close.

        This method owns the application's lifetime. Instantiate a fresh root
        for any subsequent interactive checks; a closed Tk root is not reused.
        """
        if self._closing:
            raise RuntimeError("The preview is closed; create a new PreviewApp for another smoke test")
        try:
            exists = self.winfo_exists()
        except tk.TclError:
            exists = False
        if not exists:
            raise RuntimeError("The preview root no longer exists")
        if not self._company_ids:
            raise RuntimeError("No company records are available for the preview smoke test")
        screens = 0
        documents = 0
        stock_reports = 0

        def wait_for_stock():
            deadline = time.monotonic() + 90
            while self._stock_future is not None:
                if time.monotonic() > deadline:
                    raise RuntimeError("Stock reconstruction exceeded the smoke-test timeout")
                self.update()
                time.sleep(0.01)
            if self._stock_error is not None:
                raise RuntimeError("Stock reconstruction failed during the smoke test") from self._stock_error

        for company_label in self._company_ids:
            self.company_var.set(company_label)
            for section in SECTIONS:
                self.show_section(section)
                self.update_idletasks()
                screens += 1
                if section == "stock":
                    wait_for_stock()
                    stock_reports += 1
                    self.stock_stream_var.set("Breakage stock")
                    self._begin_stock_load()
                    wait_for_stock()
                    stock_reports += 1
                elif section in DOCUMENT_SECTIONS and self.active_rows:
                    dialog = self.open_document(section, self.active_rows[0]["id"])
                    self.update_idletasks()
                    dialog.notebook.select(1)
                    self.update_idletasks()
                    dialog.destroy()
                    documents += 1
        self.show_section("dashboard")
        self.update_idletasks()
        result = {"companies": len(self._company_ids), "screens": screens,
                  "documents": documents, "stock_reports": stock_reports}
        self.close()
        if not self._closed:
            self.wait_window(self)
        return result

    def close(self):
        if getattr(self, "_closing", False):
            return
        self._closing = True
        if not hasattr(self, "_stock_executor"):
            # Tk may already exist when font/style initialization raises.
            super().destroy()
            self._closed = True
            return
        self._cancel_search()
        self._invalidate_stock()
        if self._stock_reap_job is not None:
            self.after_cancel(self._stock_reap_job)
            self._stock_reap_job = None
        # Keep the interpreter and widgets alive while any superseded read is
        # still running. No completion callback captures Tk from the worker.
        self._stock_executor.shutdown(wait=False, cancel_futures=True)
        if self._stock_futures and hasattr(self, "status_var"):
            self.status_var.set("Finishing the historical stock read before closing…")
        self._wait_for_stock_close()

    def _wait_for_stock_close(self):
        self._close_poll_job = None
        self._prune_stock_reads()
        if self._stock_futures or not self._other_work_finished():
            self._close_poll_job = self.after(60, self._wait_for_stock_close)
            return
        # All submitted work is done or cancelled: joining cannot wait for a
        # long database read. The thread exits before Tcl can be finalized.
        self._stock_executor.shutdown(wait=True, cancel_futures=True)
        super().destroy()
        self._closed = True

    def _other_work_finished(self):
        """The application host can wait for its other workspace before exit."""
        return True

    def destroy(self):
        # Tk callers commonly use destroy() directly, including test cleanup.
        # Keep worker shutdown identical to closing the window through its menu.
        if hasattr(self, "_stock_executor"):
            self.close()
        else:
            super().destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="BIFMA TRADERS historical-data development preview")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE,
                        help="Path to the recovered historical SQLite archive")
    parser.add_argument("--smoke-test", action="store_true",
                        help="Visit all preview screens, open available documents, and exit")
    args = parser.parse_args(argv)
    app = None
    try:
        app = PreviewApp(args.database)
        if args.smoke_test:
            result = app.smoke_test()
            print(f"Preview smoke test passed: {result['companies']} companies, "
                  f"{result['screens']} screens, {result['documents']} documents, "
                  f"{result['stock_reports']} stock reports.")
        else:
            app.mainloop()
    except (OSError, ValueError, RuntimeError, sqlite3.Error, tk.TclError) as error:
        if app is not None:
            app.close()
            if not app._closed:
                app.wait_window(app)
        if isinstance(error, tk.TclError):
            message = "A graphical display with Tk support is required to run the development preview."
        else:
            message = str(error)
        print(f"BIFMA TRADERS preview could not start: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
