"""Fixed, synthetic native workflow for portable-build verification.

This is deliberately not a test runner or an automation API. It accepts no
business database, script, scenario, credential, or document input. Every record
and password is generated inside one private temporary directory. Real widgets
invoke the normal services; only native file/info/error dialogs are intercepted
with fixed, confined responses so a build cannot wait for operator input.
"""

from contextlib import contextmanager
from decimal import Context, Decimal, localcontext
import gc
import hashlib
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
import weakref

from bifma import __version__

SCENARIO = "native-workflow-v2"
STEPS = ("widget-variable-lifecycle", "create-admin-company", "opening-unit-conversion", "discounted-sale",
    "document-export", "native-reports", "report-export", "breakage-pool",
    "cash-purchase-sale", "cash-linked-returns-reversal", "cash-reports-separation",
    "cash-book-report", "city-balances-report", "backup",
    "restricted-approver-reversal", "balanced-journals", "restore-revokes-sessions")
DATE = "2024-06-15"


class DiagnosticError(RuntimeError):
    """A fixed workflow failed; no success receipt may be issued."""


def _require(condition, message):
    if not condition:
        raise DiagnosticError(message)


@contextmanager
def _unraisable_guard():
    """Keep normal error reporting, but never accept ignored cleanup errors."""
    failures = []
    original = sys.unraisablehook
    def report(info):
        # Keep no exception/object/traceback references: they can resurrect the
        # very widget cycles this diagnostic must prove have been collected.
        failures.append(info.exc_type.__name__)
        original(info)
    try:
        sys.unraisablehook = report
        yield
        _require(not failures, "The diagnostic encountered an unraisable cleanup error.")
    finally:
        sys.unraisablehook = original


def _widget_variable_lifecycle(app):
    """Destroy real widget owners before forced collection on the UI worker."""
    from tkinter import ttk
    from bifma.native_ui import Form, owned_variable
    owner = ttk.Frame(app)
    form = Form(owner, [("text", "Synthetic text", "text"),
                        ("kind", "Synthetic choice", ("one", "two")),
                        ("active", "Synthetic flag", "bool")])
    extra = owned_variable(owner, value="Synthetic standalone variable")
    variables = [*form.inputs.values(), extra]
    names = [str(variable) for variable in variables]
    references = [weakref.ref(form), *(weakref.ref(variable) for variable in variables)]
    trace_threads = []
    trace_command = form.inputs["text"].trace_add("write", lambda *args: trace_threads.append(threading.get_ident()))
    form.inputs["text"].set("Synthetic changed text")
    ui_thread = threading.get_ident()
    _require(trace_threads == [ui_thread], "The widget variable trace ran off the UI thread.")
    # Model delayed callback cycles deterministically, independent of allocation
    # thresholds, screenshots or the collector's current generation counters.
    form._diagnostic_cycle = form
    extra._diagnostic_cycle = extra
    retained = [form, extra]
    del variables, form, extra
    owner.destroy()
    _require(all(not app.tk.call("info", "exists", name) for name in names), "Destroyed widget variables remain in Tcl.")
    _require(not app.tk.call("info", "commands", trace_command), "Destroyed variable trace command remains in Tcl.")
    def collect():
        retained.clear()
        gc.collect()
        return threading.get_ident()
    _require(app.future is None, "The lifecycle check requires an idle application worker.")
    # Blocking here is deliberate: a broken finalizer must not succeed merely
    # because the UI happens to be servicing cross-thread Tcl requests.
    worker_thread = app.worker.submit(collect).result(timeout=10)
    _require(worker_thread != ui_thread, "The lifecycle check did not use the application worker.")
    _require(all(reference() is None for reference in references), "Destroyed widget variables were retained after worker collection.")


class _Dialogs:
    def __init__(self, root):
        self.root = root.resolve(strict=True)
        self.pending = []
        self.errors = []

    def expect(self, operation, title, path):
        path = Path(path).resolve()
        _require(path.parent == self.root, "Diagnostic file must be in its private directory.")
        self.pending.append((operation, title, str(path)))

    def choose(self, operation, **kwargs):
        _require(bool(self.pending), "Unexpected file dialog in the fixed diagnostic.")
        expected, title, path = self.pending.pop(0)
        _require((operation, kwargs.get("title")) == (expected, title), "Unexpected diagnostic file dialog.")
        return path

    @contextmanager
    def installed(self):
        from bifma.native_ui import filedialog, messagebox
        replacements = ((filedialog, "askopenfilename", lambda **kw: self.choose("open", **kw)),
                        (filedialog, "asksaveasfilename", lambda **kw: self.choose("save", **kw)),
                        (messagebox, "showinfo", lambda *args, **kw: "ok"),
                        (messagebox, "showerror", lambda *args, **kw: self.errors.append("UI error")))
        originals = [(module, name, getattr(module, name)) for module, name, _ in replacements]
        try:
            for module, name, value in replacements:
                setattr(module, name, value)
            yield self
            _require(not self.pending, "A required diagnostic file dialog was not used.")
            _require(not self.errors, "The native UI reported an error.")
        finally:
            for module, name, original in originals:
                setattr(module, name, original)


def _widgets(parent):
    pending = list(parent.winfo_children())
    while pending:
        widget = pending.pop(0)
        yield widget
        pending.extend(widget.winfo_children())


def _field(parent, title, value):
    from tkinter import ttk
    form = getattr(parent, "_login_form", None)
    key = {"User": "username", "User Name": "display_name", "Password": "password",
           "Confirm password": "confirm"}.get(title)
    if form is not None and key in form.widgets and form.widgets[key].winfo_exists():
        control = form.widgets[key]
        control.delete(0, "end")
        control.insert(0, value)
        return
    for widget in _widgets(parent):
        if isinstance(widget, ttk.Label) and widget.cget("text") == title:
            for control in widget.master.winfo_children():
                if isinstance(control, ttk.Checkbutton):
                    _require(type(value) is bool, "Checkbox workflow value must be boolean.")
                    if control.instate(["selected"]) != value:
                        control.invoke()
                    return
                if isinstance(control, ttk.Combobox):
                    control.current(list(control.cget("values")).index(str(value)))
                    control.event_generate("<<ComboboxSelected>>")
                    return
                if isinstance(control, ttk.Entry):
                    control.delete(0, "end")
                    control.insert(0, value)
                    return
    raise DiagnosticError("Missing workflow field: " + title)


def _button(parent, title):
    from tkinter import ttk
    for widget in _widgets(parent):
        if isinstance(widget, ttk.Button) and widget.cget("text") == title:
            _require(not widget.instate(["disabled"]), "Workflow button is disabled: " + title)
            widget.invoke()
            return
    raise DiagnosticError("Missing workflow button: " + title)


class _Workflow:
    def __init__(self, app, dialogs):
        self.app, self.dialogs = app, dialogs
        self.steps = []

    def drain(self):
        end = time.monotonic() + 30
        while True:
            self.app.update()
            if self.app.future is None:
                self.app.update()
                if self.app.future is None:
                    break
            _require(time.monotonic() < end, "The diagnostic UI worker did not finish.")
            time.sleep(.01)
        _require(not self.dialogs.errors, "The native UI reported an error during the workflow.")

    def dialog(self, title):
        import tkinter as tk
        self.drain()
        values = [w for w in self.app.winfo_children() if isinstance(w, tk.Toplevel) and w.title() == title]
        _require(len(values) == 1, "Missing workflow dialog: " + title)
        return values[0]

    def complete(self, step):
        _require(step == STEPS[len(self.steps)], "Unexpected workflow step order.")
        self.steps.append({"step": step, "passed": True})

    def post_editor(self, editor, number):
        _button(editor, "Save" if hasattr(editor, "sale_form") else "Save draft & review")
        _button(self.dialog("Review draft · " + number), "Post document")
        return self.dialog("Posted · " + number)

    def close_posted(self, posted):
        _button(posted, "Close")
        self.drain()

    def trade(self, kind, title, number, quantity, rate, *, header=None):
        self.app.document_editor(kind)
        editor = self.dialog("New · " + title)
        if kind == "sale":
            form = editor.sale_form
            form.inputs["document_number"].set(number)
            form.inputs["business_date"].set(DATE)
            form.inputs["buyer_code"].set("CUSTOMER")
            for label, value in (header or {}).items():
                form.inputs[{"Header discount": "discount", "Breakage allowance": "breakage", "Freight": "freight"}[label]].set(value)
            line = form.rows[0]
            line.inputs["code"].set("EXAMPLE")
            line.inputs["entered_quantity"].set(quantity)
            if rate == "stored":
                _require(line.inputs["rate"].get() == "20", "Stored sale rate did not fill the inline row.")
                line.inputs["discount_percent"].set("10")
            else:
                line.inputs["rate"].set(rate)
            return self.post_editor(editor, number)
        _field(editor, "Document number", number)
        _field(editor, "Date (YYYY-MM-DD)", DATE)
        _field(editor, "Party account", "CUSTOMER · Customer")
        for label, value in (header or {}).items():
            _field(editor, label, value)
        _button(editor, "Add line")
        line = self.dialog("Add line")
        _field(line, "Item", "EXAMPLE · Example item")
        _field(line, "Store", "SHOP · Shop")
        _field(line, "Quantity in base units", quantity)
        if rate == "stored":
            _button(line, "Use stored rate")
            _field(line, "Line discount %", "10")
        else:
            _field(line, "Rate per base unit", rate)
        _button(line, "Use line")
        return self.post_editor(editor, number)


def _balanced(path):
    from bifma.native_schema import native_session
    with native_session(path) as connection:
        rows = connection.execute("SELECT entry_id,debit,credit FROM journal_lines ORDER BY entry_id,line_number").fetchall()
    totals = {}
    with localcontext(Context(prec=256)):
        for row in rows:
            debit, credit = totals.get(row[0], (Decimal(0), Decimal(0)))
            totals[row[0]] = (debit + Decimal(row[1]), credit + Decimal(row[2]))
    _require(bool(totals) and all(debit == credit for debit, credit in totals.values()), "Posted journals are not balanced.")
    return 0


def _verify_printed_totals(text, expected):
    """Check the actual printable totals, independently of service values."""
    from html.parser import HTMLParser
    class Totals(HTMLParser):
        def __init__(self):
            super().__init__()
            self.active, self.rows, self.row, self.cell = False, [], [], None
        def handle_starttag(self, tag, attrs):
            if tag == "table":
                self.active = "totals" in dict(attrs).get("class", "").split()
            if self.active:
                if tag == "tr":
                    self.row = []
                elif tag in {"th", "td"}:
                    self.cell = []
        def handle_data(self, data):
            if self.active and self.cell is not None:
                self.cell.append(data)
        def handle_endtag(self, tag):
            if self.active:
                if tag in {"th", "td"} and self.cell is not None:
                    self.row.append("".join(self.cell).strip())
                    self.cell = None
                elif tag == "tr":
                    self.rows.append(tuple(self.row))
                elif tag == "table":
                    self.active = False
    parser = Totals()
    parser.feed(text)
    parser.close()
    _require(parser.rows == list(expected.items()), "Printable report totals differ from the exact expected values.")


def _verify_city_balances(report, trial):
    """Cross-check current-city grouping against independent direct balances."""
    fields = ("opening", "debit", "credit", "closing", "closing_debit", "closing_credit")
    with localcontext(Context(prec=256)):
        expected = {key: trial["totals"][key] for key in fields[:4]}
        expected["closing_debit"] = sum((max(row["closing"], Decimal(0)) for row in trial["rows"]), Decimal(0))
        expected["closing_credit"] = sum((max(-row["closing"], Decimal(0)) for row in trial["rows"]), Decimal(0))
        _require(len(report["rows"]) == report["total"] == report["totals"]["account_count"], "City report did not return every account.")
        _require(sum(group["account_count"] for group in report["groups"]) == report["total"], "City account groups do not cover the full report.")
        for key in fields:
            total = report["totals"][key]
            _require(total == expected[key], "City balances differ from direct trial balance: " + key)
            _require(sum((group[key] for group in report["groups"]), Decimal(0)) == total, "City group totals differ: " + key)
            _require(sum((row[key] for row in report["rows"]), Decimal(0)) == total, "City account rows differ: " + key)


def _ledger_report_cycle(flow, root, actor, masters, company, cash):
    from tkinter import ttk
    app = flow.app
    documents = {"CASH-P-1", "SALE-1", "CASH-SR-1", "CASH-PR-1", "CASH-REV-1"}
    book = app.reports.cash_book(actor, company, cash["id"], date_from=DATE, date_to=DATE)
    _require(book["account"]["id"] == cash["id"] and book["cash_role_source"] == "current_master", "Cash book did not retain the selected current cash account.")
    _require(book["total"] == len(book["rows"]) == 5 and book["opening"] == 0 and book["debit"] == 36 and book["credit"] == 70 and book["closing"] == -34, "Complete cash book totals differ.")
    _field(app, "Report", "Cash book")
    _field(app, "From (YYYY-MM-DD)", DATE)
    _field(app, "To (YYYY-MM-DD)", DATE)
    def show_book():
        _button(app, "Run report")
        chooser = flow.dialog("Choose cash account")
        control = next(w for w in _widgets(chooser) if isinstance(w, ttk.Combobox))
        _require(control.get() == "CASH · Synthetic cash", "Cash book did not default to the configured company cash account.")
        _button(chooser, "Show cash book")
        flow.drain()
    show_book()
    tree = next(w for w in _widgets(app) if isinstance(w, ttk.Treeview))
    values = [tree.item(row, "values") for row in tree.get_children()]
    _require(len(values) == 5 and all(any(number in row for row in values) for number in documents), "Cash book widgets omitted posted activity.")
    _require(any(isinstance(w, ttk.Label) and "CASH · Synthetic cash" in w.cget("text") and "Cash in: 36 · Cash out: 70" in w.cget("text") for w in _widgets(app)), "Cash book did not display its selected account and complete movements.")
    output = root / "cash-book.html"
    flow.dialogs.expect("save", "Save full printable report", output)
    _button(app, "Save full report")
    flow.drain()
    text = output.read_text(encoding="utf-8")
    _require(all(value in text for value in documents | {"Cash book", "Synthetic cash", DATE}), "Complete cash book export lost documents or filter context.")
    _verify_printed_totals(text, {"Opening Dr / −Cr": "0", "Period cash in / debits": "36",
        "Period cash out / credits": "70", "Closing Dr / −Cr": "-34"})
    next_day = "2024-06-16"
    _field(app, "From (YYYY-MM-DD)", next_day)
    _field(app, "To (YYYY-MM-DD)", next_day)
    show_book()
    tree = next(w for w in _widgets(app) if isinstance(w, ttk.Treeview))
    _require(not tree.get_children(), "Cash book date filter included out-of-period entries.")
    _require(any(isinstance(w, ttk.Label) and w.cget("text") == "Opening: -34    Closing: -34" for w in _widgets(app)), "Filtered cash book lost its opening carry-forward.")
    output = root / "cash-book-filtered.html"
    flow.dialogs.expect("save", "Save full printable report", output)
    _button(app, "Save full report")
    flow.drain()
    text = output.read_text(encoding="utf-8")
    _require(next_day in text and all(number not in text for number in documents), "Cash book export ignored its date filter.")
    _verify_printed_totals(text, {"Opening Dr / −Cr": "-34", "Period cash in / debits": "0",
        "Period cash out / credits": "0", "Closing Dr / −Cr": "-34"})
    flow.complete("cash-book-report")
    city = masters.create(actor, "cities", {"code": "REPORTCITY", "name": "Diagnostic city"})
    current = masters.get(actor, "accounts", cash["id"])
    masters.update(actor, "accounts", cash["id"], {"city_id": city["id"]}, current["version"])
    report = app.reports.city_balances(actor, company, date_from=DATE, date_to=DATE, include_zero=False)
    trial = app.reports.trial_balance(actor, company, date_from=DATE, date_to=DATE)
    _verify_city_balances(report, trial)
    totals = report["totals"]
    _require(report["total"] == 3 and len(report["groups"]) == 2 and totals["debit"] == totals["credit"] == 106 and totals["closing_debit"] == totals["closing_credit"] == 54, "City fixture totals or separate closing sides differ.")
    _field(app, "Report", "Account balances by city")
    _field(app, "From (YYYY-MM-DD)", DATE)
    _field(app, "To (YYYY-MM-DD)", DATE)
    def show_city(filtered=False):
        _button(app, "Run report")
        choices = flow.dialog("City balance filters")
        if filtered:
            _field(choices, "City (blank for all)", "REPORTCITY · Diagnostic city")
            _field(choices, "Account kind", "Cash")
            _field(choices, "Account code or name", "CASH")
        _field(choices, "Include zero closing balances", False)
        _button(choices, "Show city balances")
        flow.drain()
    show_city()
    tree = next(w for w in _widgets(app) if isinstance(w, ttk.Treeview))
    _require(len(tree.get_children()) == 3, "City report widgets lost account rows.")
    _require(any(isinstance(w, ttk.Label) and w.cget("text") == "Closing debit: 54    Credit: 54" for w in _widgets(app)), "City report netted away separate debit and credit balances.")
    _button(app, "Totals by city")
    grouped = flow.dialog("Totals by city")
    tree = next(w for w in _widgets(grouped) if isinstance(w, ttk.Treeview))
    _require(len(tree.get_children()) == 2, "City totals dialog lost assigned or unassigned balances.")
    grouped.destroy()
    output = root / "city-balances.html"
    flow.dialogs.expect("save", "Save full printable report", output)
    _button(app, "Save full report")
    flow.drain()
    text = output.read_text(encoding="utf-8")
    _require(all(value in text for value in ("Diagnostic city", "Unassigned", "CASH", "PURCHASE", "REVENUE", DATE)), "Full city export lost current groups, accounts or filters.")
    _verify_printed_totals(text, {"Accounts": "3", "Net opening Dr / −Cr": "0", "Period debits": "106",
        "Period credits": "106", "Net closing Dr / −Cr": "0", "Closing debit balances": "54", "Closing credit balances": "54"})
    filtered = app.reports.city_balances(actor, company, date_from=DATE, date_to=DATE, city_id=city["id"], account_kind="cash", search="CASH", include_zero=False)
    _require(filtered["total"] == 1 and filtered["totals"]["closing_debit"] == 0 and filtered["totals"]["closing_credit"] == 34, "City/kind/account filters differ.")
    show_city(filtered=True)
    tree = next(w for w in _widgets(app) if isinstance(w, ttk.Treeview))
    _require(len(tree.get_children()) == 1, "City/kind/account filters did not reach the report widgets.")
    output = root / "city-balances-filtered.html"
    flow.dialogs.expect("save", "Save full printable report", output)
    _button(app, "Save full report")
    flow.drain()
    text = output.read_text(encoding="utf-8")
    _require("Diagnostic city" in text and "CASH" in text and "PURCHASE" not in text and "REVENUE" not in text, "City export ignored its selected filters.")
    _verify_printed_totals(text, {"Accounts": "1", "Net opening Dr / −Cr": "0", "Period debits": "36",
        "Period credits": "70", "Net closing Dr / −Cr": "-34", "Closing debit balances": "0", "Closing credit balances": "34"})
    flow.complete("city-balances-report")
    return {"cash_book_rows": book["total"], "cash_book_debit": str(book["debit"]), "cash_book_credit": str(book["credit"]), "cash_book_closing": str(book["closing"]),
        "configured_cash_selected": True, "city_balance_accounts": report["total"], "city_balance_groups": len(report["groups"]),
        "city_debit": str(totals["debit"]), "city_credit": str(totals["credit"]), "city_closing_debit": str(totals["closing_debit"]),
        "city_closing_credit": str(totals["closing_credit"]), "city_filtered_accounts": filtered["total"]}


def _cash_cycle(flow, root, actor, masters, primary_company, accounts, product, store):
    """A second company keeps the primary 34/8/2 assertions independent."""
    from tkinter import ttk
    from bifma.native_schema import native_session
    app = flow.app
    cash = masters.create(actor, "accounts", {"code": "CASH", "name": "Synthetic cash", "kind": "cash", "classification": "asset"})
    company = masters.create(actor, "companies", {"code": "CASHCO", "name": "Synthetic cash company", "cash_account_id": cash["id"], "fiscal_start": "2024-01-01", "fiscal_end": "2024-12-31"})["id"]
    masters.create(actor, "company_stores", {"company_id": company, "store_id": store["id"]})
    app.company_id = company
    app.refresh_shell()
    flow.drain()
    # An ordinary draft with the same company/document number must coexist;
    # numbering must not change an account invoice into a cash invoice.
    draft = app.transactions.save_draft(actor, company, "sale", {"document_number": "SALE-1", "business_date": DATE,
        "rounding_policy": "legacy_whole", "posting_places": 0, "account_id": accounts["CUSTOMER"]["id"],
        "lines": [{"product_id": product["id"], "store_id": store["id"], "quantity": "1", "rate": "3"}]})
    def post(kind, title, number, quantity, rate=None):
        app.document_editor(kind)
        editor = flow.dialog("New · " + title)
        _field(editor, "Document number", number)
        _field(editor, "Date (YYYY-MM-DD)", DATE)
        if not kind.endswith("_return"):
            _field(editor, "Customer name (optional)" if kind == "cash_sale" else "Supplier name (optional)", "Synthetic counterparty")
            _field(editor, "Customer mobile (optional)" if kind == "cash_sale" else "Supplier mobile (optional)", "000-demo-123")
        _button(editor, "Add line")
        line = flow.dialog("Add line")
        if kind.endswith("_return"):
            _button(line, "Choose original line")
            picker = flow.dialog("Choose the original document")
            tree = next(w for w in _widgets(picker) if isinstance(w, ttk.Treeview))
            _require(len(tree.get_children()) == 1, "Cash return must select its one corresponding cash invoice.")
            tree.selection_set(tree.get_children()[0])
            _button(picker, "Show lines")
            flow.drain()
            tree = next(w for w in _widgets(picker) if isinstance(w, ttk.Treeview))
            tree.selection_set(tree.get_children()[0])
            _button(picker, "Use selected line")
        else:
            _field(line, "Item", "EXAMPLE · Example item")
            _field(line, "Store", "SHOP · Shop")
            _field(line, "Rate per base unit", rate)
        _field(line, "Quantity in base units", quantity)
        _button(line, "Use line")
        return flow.post_editor(editor, number)
    flow.close_posted(post("cash_purchase", "Cash purchases", "CASH-P-1", "10", "6"))
    sale_dialog = post("cash_sale", "Counter sales", "SALE-1", "2", "10")
    output = root / "counter-sale.html"
    flow.dialogs.expect("save", "Save printable document", output)
    _button(sale_dialog, "Save printable copy")
    flow.drain()
    _require(all(value in output.read_text(encoding="utf-8") for value in ("Counter sales invoice", "Full cash amount", "Synthetic counterparty")), "Cash invoice output lacks its saved settlement.")
    flow.close_posted(sale_dialog)
    sale = app.transactions.list_documents(actor, company, "cash_sale", status="posted")[0]
    saved = app.transactions.get(actor, company, sale["id"])
    _require(saved["total_amount"] == "20" and saved["snapshot"]["cash_settlement"]["cash_account_id"] == cash["id"], "Counter sale did not save full cash settlement.")
    _require(app.transactions.get(actor, company, draft["id"])["kind"] == "sale", "Overlapping document number changed ordinary invoice identity.")
    flow.complete("cash-purchase-sale")
    flow.close_posted(post("cash_sale_return", "Counter sale returns", "CASH-SR-1", "1"))
    flow.close_posted(post("cash_purchase_return", "Cash purchase returns", "CASH-PR-1", "1"))
    returned = app.transactions.list_documents(actor, company, "cash_sale_return", status="posted")[0]
    # Seed a previously saved correction through the retained service. The
    # owner removed reversal entry from the application, while old documents
    # and their accounting effects must remain readable.
    returned = app.transactions.get(actor, company, returned["id"])
    reversal = app.transactions.reverse(actor, company, returned["id"], returned["revision"],
        document_number="CASH-REV-1", business_date=DATE, reason="Synthetic saved correction compatibility")
    app.open_document(reversal["id"])
    flow.close_posted(flow.dialog("Posted · CASH-REV-1"))
    quantity = app.transactions.stock_balance(actor, company, product_id=product["id"])[0]["quantity"]
    closing = app.reports.ledger(actor, company, cash["id"])["closing"]
    with native_session(app.path) as connection:
        cash_documents = connection.execute("SELECT COUNT(*) FROM documents WHERE company_id=? AND status='posted'", (company,)).fetchone()[0]
        extra_vouchers = connection.execute("SELECT COUNT(*) FROM documents WHERE company_id=? AND kind IN ('cash_receipt','cash_payment','bank_receipt','bank_payment')", (company,)).fetchone()[0]
    _require(quantity == "7" and closing == Decimal("-34") and cash_documents == 5 and extra_vouchers == 0, "Cash stock, journal or voucher effects differ.")
    flow.complete("cash-linked-returns-reversal")
    _require(app.reports.trade_detail(actor, company, "sales")["total"] == 0, "Account report included cash entries.")
    report = app.reports.trade_detail(actor, company, "sales", payment_mode="cash")
    _require(report["total"] == 3 and report["totals"]["net"] == Decimal("20"), "Cash sale/return/reversal report differs.")
    app.report_screen()
    flow.drain()
    _field(app, "Report", "Sales / returns detail")
    _button(app, "Run report")
    activity = flow.dialog("Trade activity")
    _field(activity, "Entry type", "Cash entries")
    _button(activity, "Choose filters")
    _button(flow.dialog("Trade detail filters"), "Show trade detail")
    flow.drain()
    tree = next(w for w in _widgets(app) if isinstance(w, ttk.Treeview))
    _require(len(tree.get_children()) == 3, "Cash report widgets lost sale/return/reversal rows.")
    output = root / "cash-sales-detail.html"
    flow.dialogs.expect("save", "Save full printable report", output)
    _button(app, "Save full report")
    flow.drain()
    _require(all(value in output.read_text(encoding="utf-8") for value in ("SALE-1", "CASH-SR-1", "CASH-REV-1")), "Cash report export lost transactions.")
    flow.complete("cash-reports-separation")
    ledger_effects = _ledger_report_cycle(flow, root, actor, masters, company, cash)
    app.company_id = primary_company
    app.refresh_shell()
    flow.drain()
    return {"cash_posted_documents": cash_documents, "cash_regular_quantity": quantity,
        "cash_account_closing": str(closing), "cash_extra_vouchers": extra_vouchers,
        "cash_sales_report_rows": report["total"], "cash_sales_report_net": str(report["totals"]["net"]), **ledger_effects}


def _close_app(app):
    try:
        app.close()
    finally:
        app.worker.shutdown(wait=True, cancel_futures=True)
        if app.future is not None:
            app._finish_close()


def _exercise(root):
    from tkinter import ttk
    from bifma.native_schema import native_session
    from bifma.native_ui import NativeApp
    dialogs = _Dialogs(root)
    password, approver_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    path, backup, restored = (root / name for name in ("synthetic.sqlite3", "backup.sqlite3", "restored.sqlite3"))
    app = None
    with dialogs.installed():
        try:
            app = NativeApp()
            flow = _Workflow(app, dialogs)
            _widget_variable_lifecycle(app)
            flow.complete("widget-variable-lifecycle")
            dialogs.expect("save", "Create a new native database", path)
            _button(app, "Create new database")
            flow.drain()
            for title, value in (("User", "owner"), ("Password", password), ("User Name", "Synthetic owner"), ("Confirm password", password)):
                _field(app, title, value)
            _button(app, "Create administrator")
            flow.drain()
            app.master_dialog("companies")
            company_form = flow.dialog("New Companies")
            for title, value in (("Code", "DEMO"), ("Name", "Synthetic example company"),
                ("Fiscal start (YYYY-MM-DD)", "2024-01-01"), ("Fiscal end (YYYY-MM-DD)", "2024-12-31"),
                ("Address", "Synthetic warehouse street"), ("Sales tax number", "0001-DEMO")):
                _field(company_form, title, value)
            _button(company_form, "Save changes")
            flow.drain()
            _require(bool(app.company_id), "Company setup did not finish.")
            actor, masters, company = app.actor, app.masters, app.company_id
            parent = masters.create(actor, "accounts", {"code": "000", "name": "Accounts", "kind": "group"})
            accounts = {}
            for code, kind, classification in (("CUSTOMER", "customer", "asset"), ("REVENUE", "general", "income"), ("PURCHASE", "general", "expense"), ("INVENTORY", "general", "asset"), ("CAPITAL", "general", "equity")):
                accounts[code] = masters.create(actor, "accounts", {"code": code, "name": code.title(), "kind": kind, "classification": classification, "parent_id": parent["id"]})
            unit = masters.create(actor, "units", {"code": "PCS", "name": "Pieces"})
            large = masters.create(actor, "units", {"code": "PACK", "name": "Packs"})
            store = masters.create(actor, "stores", {"code": "SHOP", "name": "Shop"})
            masters.create(actor, "company_stores", {"company_id": company, "store_id": store["id"]})
            product = masters.create(actor, "products", {"code": "EXAMPLE", "name": "Example item", "small_unit_id": unit["id"], "large_unit_id": large["id"], "unit_factor": "6", "sale_rate": "20", "purchase_rate": "12", "sale_account_id": accounts["REVENUE"]["id"], "purchase_account_id": accounts["PURCHASE"]["id"], "inventory_account_id": accounts["INVENTORY"]["id"]})
            flow.complete("create-admin-company")
            app.document_editor("stock_opening")
            editor = flow.dialog("New · Opening stock")
            _field(editor, "Document number", "OPEN-1")
            _field(editor, "Date (YYYY-MM-DD)", DATE)
            _field(editor, "Opening balance counterpart", "CAPITAL · Capital")
            _button(editor, "Add line")
            line = flow.dialog("Add line")
            _field(line, "Item", "EXAMPLE · Example item")
            _field(line, "Store", "SHOP · Shop")
            _button(line, "Convert large / base quantities")
            conversion = flow.dialog("Convert quantity")
            _field(conversion, "Large quantity (Packs)", "1")
            _field(conversion, "Base quantity (Pieces)", "4")
            _button(conversion, "Use base quantity")
            _field(line, "Opening value", "100")
            _button(line, "Use line")
            flow.close_posted(flow.post_editor(editor, "OPEN-1"))
            _require(app.transactions.stock_balance(actor, company, product_id=product["id"])[0]["quantity"] == "10", "Opening conversion differs.")
            flow.complete("opening-unit-conversion")
            posted = flow.trade("sale", "Sale Voucher", "SALE-1", "2", "stored", header={"Header discount": "2"})
            with native_session(path) as connection:
                total = connection.execute("SELECT total_amount FROM documents WHERE company_id=? AND document_number='SALE-1' AND status='posted'", (company,)).fetchone()[0]
            _require(total == "34", "Discounted sale total differs.")
            flow.complete("discounted-sale")
            output = root / "sale.html"
            dialogs.expect("save", "Save printable document", output)
            _button(posted, "Save printable copy")
            flow.drain()
            text = output.read_text(encoding="utf-8")
            _require(all(label in text for label in ("Sales invoice", "SALE-1", "Synthetic warehouse street", "0001-DEMO")), "Issued document output differs.")
            flow.close_posted(posted)
            flow.complete("document-export")
            app.report_screen()
            flow.drain()
            _button(app, "Run report")
            flow.drain()
            _field(app, "Report", "Sub-trial balance")
            _button(app, "Run report")
            flow.drain()
            hierarchy = next(w for w in _widgets(app) if isinstance(w, ttk.Treeview))
            _require(any("Accounts" in hierarchy.item(row, "values") for row in hierarchy.get_children()), "Hierarchy report lost its parent.")
            _field(app, "Report", "Account ledger")
            _button(app, "Run report")
            chooser = flow.dialog("Choose account")
            control = next(w for w in _widgets(chooser) if isinstance(w, ttk.Combobox))
            control.current(list(control.cget("values")).index("CUSTOMER · Customer"))
            _button(chooser, "Show ledger")
            flow.drain()
            _require(any(isinstance(w, ttk.Label) and w.cget("text") == "Opening: 0    Closing: 34" for w in _widgets(app)), "Account ledger closing differs.")
            _field(app, "Report", "Item ledger")
            _button(app, "Run report")
            chooser = flow.dialog("Choose item and store")
            _field(chooser, "Item", "EXAMPLE · Example item")
            _field(chooser, "Store (blank for all)", "SHOP · Shop")
            _button(chooser, "Show item ledger")
            flow.drain()
            _require(any(isinstance(w, ttk.Label) and "Opening: 0    In: 10    Out: 2    Closing: 8" in w.cget("text") for w in _widgets(app)), "Item ledger totals differ.")
            flow.complete("native-reports")
            output = root / "item-ledger.html"
            dialogs.expect("save", "Save full printable report", output)
            _button(app, "Save full report")
            flow.drain()
            _require(all(value in output.read_text(encoding="utf-8") for value in ("OPEN-1", "SALE-1")), "Full item report lost documents.")
            _field(app, "Report", "Sales / returns detail")
            _button(app, "Run report")
            _button(flow.dialog("Trade activity"), "Choose filters")
            _button(flow.dialog("Trade detail filters"), "Show trade detail")
            flow.drain()
            _require(any(isinstance(w, ttk.Label) and "Line amount: 36 · Recorded / final document amount: 34" in w.cget("text") for w in _widgets(app)), "Trade report totals differ.")
            output = root / "sales-detail.html"
            dialogs.expect("save", "Save full printable report", output)
            _button(app, "Save full report")
            flow.drain()
            _require("SALE-1" in output.read_text(encoding="utf-8"), "Full sales report lost its document.")
            flow.complete("report-export")
            flow.close_posted(flow.trade("breakage_receive", "Breakage receive", "BR-1", "3", "5", header={"Breakage allowance": "1"}))
            flow.close_posted(flow.trade("breakage_dispatch", "Breakage dispatch", "BD-1", "1", "5"))
            _require(app.transactions.stock_balance(actor, company, product_id=product["id"], pool="breakage")[0]["quantity"] == "2", "Breakage pool differs.")
            _require(app.transactions.stock_balance(actor, company, product_id=product["id"])[0]["quantity"] == "8", "Regular pool was contaminated.")
            flow.complete("breakage-pool")
            cash_effects = _cash_cycle(flow, root, actor, masters, company, accounts, product, store)
            dialogs.expect("save", "Create verified native backup", backup)
            app.create_backup()
            flow.drain()
            _require(backup.is_file(), "Verified backup was not created.")
            backup_digest = hashlib.sha256(backup.read_bytes()).hexdigest()
            flow.complete("backup")
            approver = app.security.create_user(actor, "approver", approver_password)
            app.security.set_company_access(actor, approver.id, [company])
            app.security.set_permissions(actor, approver.id, "sales", ["read", "post"])
            draft = app.transactions.save_draft(actor, company, "sale", {"document_number": "APPROVE-1", "business_date": DATE, "rounding_policy": "legacy_whole", "posting_places": 0, "account_id": accounts["CUSTOMER"]["id"], "lines": [{"product_id": product["id"], "store_id": store["id"], "quantity": "1", "rate": "20"}]})
            app.logout()
            flow.drain()
            _field(app, "User", "approver")
            _field(app, "Password", approver_password)
            _button(app, "OK")
            flow.drain()
            app.open_document(draft["id"])
            review = flow.dialog("Review draft · APPROVE-1")
            _require(not any(isinstance(w, ttk.Button) and w.cget("text") == "Edit draft" for w in _widgets(review)), "Post-only approver can edit a draft.")
            _button(review, "Post document")
            issued = flow.dialog("Posted · APPROVE-1")
            _require(not any(isinstance(w, ttk.Button) and w.cget("text") == "Reverse document" for w in _widgets(issued)),
                     "Removed reversal action remains available.")
            flow.close_posted(issued)
            # Compatibility fixture only: the restricted user's saved reversal
            # remains readable after the UI entry action has been removed.
            saved = app.transactions.get(app.actor, company, draft["id"])
            reversal = app.transactions.reverse(app.actor, company, saved["id"], saved["revision"],
                document_number="REV-1", business_date=DATE, reason="Synthetic saved correction compatibility")
            app.open_document(reversal["id"])
            flow.close_posted(flow.dialog("Posted · REV-1"))
            verifier = app.security.authenticate("owner", password)
            regular = app.transactions.stock_balance(verifier, company, product_id=product["id"])[0]["quantity"]
            app.security.logout(verifier)
            _require(regular == "8", "Reversal did not restore regular stock.")
            flow.complete("restricted-approver-reversal")
            imbalances = _balanced(path)
            with native_session(path) as connection:
                posted_count = connection.execute("SELECT COUNT(*) FROM documents WHERE company_id=? AND status='posted'", (company,)).fetchone()[0]
                all_posted_count = connection.execute("SELECT COUNT(*) FROM documents WHERE status='posted'").fetchone()[0]
            _require(posted_count == 6 and all_posted_count == 11, "Posted document count differs.")
            flow.complete("balanced-journals")
            app.logout()
            flow.drain()
            app.welcome()
            dialogs.expect("open", "Select a native backup", backup)
            dialogs.expect("save", "Restore to a new database file", restored)
            app.restore_dialog()
            restore = flow.dialog("Restore native backup")
            _field(restore, "Backup administrator username", "owner")
            _field(restore, "Passphrase", password)
            _button(restore, "Restore to new file")
            flow.drain()
            _require(app.path == restored and app.actor is None, "Restore did not require fresh sign-in.")
            with native_session(restored) as connection:
                active = connection.execute("SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL").fetchone()[0]
                restored_count = connection.execute("SELECT COUNT(*) FROM documents WHERE status='posted'").fetchone()[0]
            _require(active == 0 and restored_count == 9, "Restore sessions or snapshot document count differs.")
            # Revocation is checked before fresh authentication, so a legitimate
            # new session cannot be mistaken for a copied session.
            _field(app, "User", "owner")
            _field(app, "Password", password)
            _button(app, "OK")
            flow.drain()
            _require(app.actor is not None, "The restored administrator could not sign in.")
            with native_session(restored) as connection:
                restored_cash_company = connection.execute("SELECT id FROM companies WHERE code='CASHCO'").fetchone()[0]
                restored_cash_account = connection.execute("SELECT id FROM accounts WHERE code='CASH'").fetchone()[0]
                restored_cash_rows = connection.execute("SELECT id FROM documents WHERE company_id=? AND status='posted'", (restored_cash_company,)).fetchall()
            restored_quantity = app.transactions.stock_balance(app.actor, restored_cash_company, product_id=product["id"])[0]["quantity"]
            restored_closing = app.reports.ledger(app.actor, restored_cash_company, restored_cash_account)["closing"]
            _require(len(restored_cash_rows) == 5 and restored_quantity == "7" and restored_closing == Decimal("-34"), "Restored cash stock, ledger or document count differs.")
            expected_binding = {"mode": "cash", "cash_account_id": restored_cash_account,
                                "counterparty_name": "Synthetic counterparty", "counterparty_mobile": "000-demo-123"}
            for row in restored_cash_rows:
                saved = app.transactions.get(app.actor, restored_cash_company, row[0])
                _require(saved["account_id"] == restored_cash_account and saved["snapshot"].get("cash_settlement") == expected_binding,
                         "Restored cash invoice/refund/reversal lost its saved binding or contact.")
            _require(hashlib.sha256(backup.read_bytes()).hexdigest() == backup_digest, "Restore changed its source backup.")
            flow.complete("restore-revokes-sessions")
            effects = {"widget_variable_lifecycle": True, "sale_total": total, "regular_quantity": regular, "breakage_quantity": "2", "primary_posted_documents": posted_count, "posted_documents": all_posted_count,
                "restored_documents": restored_count, "restored_active_sessions_before_login": active, "journal_imbalances": imbalances,
                "backup_scope": "primary-and-cash-before-approver", "restored_cash_documents": len(restored_cash_rows),
                "restored_cash_regular_quantity": restored_quantity, "restored_cash_account_closing": str(restored_closing),
                "restored_cash_settlement_preserved": True}
            effects.update(cash_effects)
            return flow.steps, effects
        finally:
            if app is not None:
                # close_pending suppresses callbacks after a failed step; waiting
                # here closes worker-owned DB handles before temporary cleanup.
                _close_app(app)


def run_workflow():
    """Run only the fixed scenario and return evidence after complete cleanup."""
    with _unraisable_guard():
        with tempfile.TemporaryDirectory(prefix="bifma-workflow-") as directory:
            root = Path(directory)
            steps, effects = _exercise(root)
        # Finish any delayed finalizers while the error guard still covers the
        # run, after workers and widget owners have already been shut down.
        gc.collect()
        _require(not root.exists(), "Diagnostic temporary data was not removed.")
        _require(steps == [{"step": step, "passed": True} for step in STEPS], "Diagnostic did not complete every step.")
    return {"schema_version": 1, "scenario": SCENARIO, "application_version": __version__,
        "frozen": getattr(sys, "frozen", False) is True, "passed": True, "steps": steps,
        "effects": effects, "temporary_data_removed": True}
