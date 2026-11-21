"""Compact report filters observed in the owner's Oracle walkthrough.

Widgets only: callers supply authorized company/account choices and execute the
existing report services. Dates are normalized before they reach those services.
"""

from datetime import date, datetime, timedelta
import tkinter as tk
from tkinter import ttk

from bifma.tk_lifecycle import owned_variable


def report_date(value, *, required=False):
    text = value.strip()
    if not text:
        if required:
            raise ValueError("Enter the report date.")
        return None
    for pattern in ("%Y-%m-%d", "%d-%m-%Y", "%d-%m-%y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    raise ValueError("Enter a valid date as DD-MM-YY or YYYY-MM-DD.")


def date_display(value):
    return date.fromisoformat(value).strftime("%d-%m-%y") if value else ""


class FinancialReportFilter(ttk.Frame):
    """Retained Date/Party/Company form with the original View and Exit flow."""

    def __init__(self, parent, *, report, companies, company_id, choice_factory,
                 date_from=None, date_to=None, on_view, on_exit, on_company):
        super().__init__(parent, padding=9)
        self.report = report
        self.choice_factory = choice_factory
        self.company_ids = [row[0] for row in companies]
        self._changing = False
        self.account_choice = None
        self.account_records = []
        self.on_company = on_company
        self.inputs, self.widgets = {}, {}
        title = {"Cash book": "روزنامچہ", "Account ledger": "کھاتہ نام",
                 "Sub-trial balance": "چٹھہ"}[report]
        tk.Label(self, text=title, foreground="#123972", background="#e5e6bb",
                 font=("Noto Naskh Arabic", 25, "bold"), pady=5).pack(fill="x", pady=(0, 10))
        body = ttk.Frame(self, padding=(10, 10))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        row = 0
        dates = (("date_from", "Date From", date_from), ("date_to", "Date To", date_to)) if report == "Account ledger" else (("date_to", "Date", date_to),)
        for key, label, value in dates:
            ttk.Label(body, text=label + ":").grid(row=row, column=0, sticky="e", padx=(0, 9), pady=7)
            variable = owned_variable(self, value=date_display(value))
            widget = ttk.Entry(body, textvariable=variable, width=18)
            widget.grid(row=row, column=1, sticky="w", pady=7)
            self.inputs[key], self.widgets[key] = variable, widget
            row += 1
        if report == "Account ledger":
            ttk.Label(body, text="Party / account:").grid(row=row, column=0, sticky="e", padx=(0, 9), pady=7)
            account = ttk.Frame(body)
            account.grid(row=row, column=1, sticky="ew", pady=7)
            self.inputs["account_code"] = owned_variable(self)
            self.widgets["account_code"] = ttk.Entry(account, textvariable=self.inputs["account_code"], width=12)
            self.widgets["account_code"].pack(side="left", padx=(0, 5))
            self.account_choice = choice_factory(account, [], enter_select=True, width=25)
            self.account_choice._search_title = "Party / account"
            self.account_choice.widget.pack(side="left", fill="x", expand=True)
            ttk.Button(account, text="Find…", command=lambda: self.account_choice.search("Party / account"), width=6).pack(side="left", padx=(5, 0))
            self.widgets["account_id"] = self.account_choice.widget
            self.account_choice.widget.bind("<<ComboboxSelected>>", self._account_selected, add=True)
            self.account_choice.widget.bind("<<ChoiceAccepted>>", self._account_selected, add=True)
            self.inputs["account_code"].trace_add("write", self._account_code_changed)
            row += 1
        ttk.Label(body, text="Company:").grid(row=row, column=0, sticky="e", padx=(0, 9), pady=7)
        company = ttk.Combobox(body, state="readonly", values=[record[1] for record in companies], width=32)
        company.grid(row=row, column=1, sticky="ew", pady=7)
        if company_id in self.company_ids:
            company.current(self.company_ids.index(company_id))
        self.widgets["company_id"] = company
        company.bind("<<ComboboxSelected>>", lambda event: on_company(self.company_id()))
        row += 1
        self.cash_holder = ttk.Frame(body)
        self.cash_holder.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        self.notice = owned_variable(self)
        ttk.Label(body, textvariable=self.notice, style="Muted.TLabel", wraplength=475).grid(
            row=row + 1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        controls = ttk.Frame(self, padding=(10, 6))
        controls.pack(fill="x", side="bottom")
        self.view_button = ttk.Button(controls, text="View", command=on_view, width=10)
        self.view_button.pack(side="left")
        self.exit_button = ttk.Button(controls, text="Exit", command=on_exit, width=10)
        self.exit_button.pack(side="right")
        self._bind_navigation()

    def company_id(self):
        current = self.widgets["company_id"].current()
        return self.company_ids[current] if 0 <= current < len(self.company_ids) else None

    def _bind_navigation(self):
        ordered = [key for key in ("date_from", "date_to", "account_code", "account_id", "company_id") if key in self.widgets]
        for index, key in enumerate(ordered):
            target = self.widgets[ordered[index + 1]] if index + 1 < len(ordered) else self.view_button
            if key == "account_code":
                callback = self._code_enter
            elif key == "account_id":
                callback = lambda event: self._account_enter()
            elif key == "company_id":
                callback = lambda event: self._company_enter()
            else:
                callback = lambda event, destination=target: self._advance(destination)
            for sequence in ("<Return>", "<KP_Enter>"):
                self.widgets[key].bind(sequence, callback)

    def _advance(self, widget):
        if widget is self.view_button:
            self.view_button.invoke()
        else:
            widget.focus_set()
            if isinstance(widget, ttk.Entry) and not isinstance(widget, ttk.Combobox):
                widget.selection_range(0, "end")
        return "break"

    def _code_enter(self, event=None):
        if self.account_choice.get() is None:
            self.account_choice.search("Party / account", initial_text=self.inputs["account_code"].get())
            return "break"
        return self._advance(self.widgets["company_id"])

    def _account_enter(self):
        if self.account_choice.get() is None:
            self.account_choice.search("Party / account")
            return "break"
        return self._advance(self.widgets["company_id"])

    def _company_enter(self):
        choice = getattr(self, "cash_choice", None)
        if choice is not None and choice.get() is None:
            choice.search("Cash account")
            return "break"
        return self._advance(self.view_button)

    def _account_selected(self, event=None):
        record = next((row for row in self.account_records if row["id"] == self.account_choice.get()), {})
        self._changing = True
        try:
            self.inputs["account_code"].set(record.get("code", ""))
        finally:
            self._changing = False
        return self._advance(self.widgets["company_id"])

    def _account_code_changed(self, *_):
        if self._changing:
            return
        term = self.inputs["account_code"].get().strip().casefold()
        matches = [row["id"] for row in self.account_records if str(row.get("code", "")).casefold() == term]
        identifier = matches[0] if len(matches) == 1 else None
        self.account_choice.widget.current(self.account_choice.ids.index(identifier))

    def set_accounts(self, records, default_id=None):
        self.account_records = list(records)
        if self.report == "Cash book":
            for child in self.cash_holder.winfo_children():
                child.destroy()
            selected = next((row for row in records if row["id"] == default_id), None)
            self.default_cash_id = selected["id"] if selected else None
            self.cash_choice = None
            if selected:
                self.notice.set("")
            else:
                self.notice.set("Select the cash account; no configured company cash account is available.")
                # Imported saved cash roles remain explicitly selectable when
                # configuration is missing; a location/account is never guessed.
                self.cash_choice = self.choice_factory(self.cash_holder, records, enter_select=True)
                self.cash_choice._search_title = "Cash account"
                self.cash_choice.widget.pack(fill="x")
                self.cash_choice.widget.bind("<<ChoiceAccepted>>", lambda event: self._advance(self.view_button), add=True)
            return
        if self.account_choice is not None:
            self._changing = True
            try:
                self.account_choice.records = list(records)
                self.account_choice.ids = [None] + [row["id"] for row in records]
                self.account_choice.widget.configure(values=["— Select —"] + [row["name"] for row in records])
                self.account_choice.widget.current(0)
                self.inputs["account_code"].set("")
            finally:
                self._changing = False

    def values(self):
        result = {"company_id": self.company_id()}
        if self.report == "Account ledger":
            result.update(date_from=report_date(self.inputs["date_from"].get()),
                          date_to=report_date(self.inputs["date_to"].get()),
                          account_id=self.account_choice.get())
        else:
            end = report_date(self.inputs["date_to"].get(), required=True)
            result.update(date_from=end if self.report == "Cash book" else None, date_to=end)
            if self.report == "Cash book":
                result["account_id"] = self.cash_choice.get() if self.cash_choice is not None else self.default_cash_id
        if result.get("date_from") and result.get("date_to") and result["date_from"] > result["date_to"]:
            raise ValueError("Date From must be on or before Date To.")
        return result

    def first_widget(self):
        return self.widgets.get("date_from", self.widgets["date_to"])


def open_financial_report(workspace, report):
    """Retain the filter while each authorized report opens in its own preview."""
    from functools import partial
    from tkinter import messagebox
    from bifma.native_ui import Choice, baseline_details

    cash_book = report == "Cash book"
    ledger = report == "Account ledger"
    report_key = "cash_book" if cash_book else "account_ledger" if ledger else "sub_trial_balance"
    titles = {"Cash book": "Cash Book", "Account ledger": "Party Ledger Report",
              "Sub-trial balance": "Sub Trial Balance"}
    actor = workspace.actor
    original_company = workspace.company_id

    def company_accounts(company):
        if cash_book:
            return workspace.reports.cash_accounts(actor, company)
        if ledger:
            return {"accounts": workspace.reports.accounts(actor, company), "default_account_id": None}
        permissions = workspace.security.permissions(actor, company)
        if "read" not in permissions.get("trial_balance", []):
            raise ValueError("Sub-trial balance access is unavailable for this company.")
        return {"accounts": [], "default_account_id": None}

    def fetch():
        companies = workspace.security.accessible_companies(actor)
        available_ids = {row[0] for row in companies}
        company = original_company if original_company in available_ids else companies[0][0] if companies else None
        return companies, company, company_accounts(company) if company else None

    def create_filter(result):
        companies, company, selectors = result
        if company is None:
            messagebox.showinfo("No companies", "No companies are available for this report.", parent=workspace)
            return
        dialog, body = workspace.dialog(titles[report], width=650 if ledger else 450,
                                        height=430 if ledger else 340, scrollable=False)
        dialog._focus_entry = True
        dialog.minsize(520 if ledger else 420, 410 if ledger else 320)
        state = {"company": company, "preview": None}
        cutoff = baseline_details(workspace.baseline).get("cutoff_date")
        start = (date.fromisoformat(cutoff) + timedelta(days=1)).isoformat() if cutoff else None

        def close_filter():
            active_preview = state["preview"]
            if active_preview is not None and active_preview.winfo_exists():
                active_preview.lift()
            elif workspace.future is None:
                dialog.destroy()

        def accounts_ready(selected_company, data):
            if not dialog.winfo_exists() or form.company_id() != selected_company:
                return
            form.set_accounts(data["accounts"], data.get("default_account_id"))
            state["company"] = selected_company

        def change_company(selected_company):
            state["company"] = None
            form.set_accounts([])
            if selected_company:
                workspace.run(partial(company_accounts, selected_company), partial(accounts_ready, selected_company))

        def preview(result, company_id, arguments):
            company_name = next((name for identifier, name in companies if identifier == company_id), "")
            window, page = workspace.dialog(titles[report] + " · Preview", width=1120, height=730, scrollable=False)
            window.transient(dialog)
            window._focus_entry = True
            state["preview"] = window
            toolbar = ttk.Frame(page)
            toolbar.pack(fill="x", pady=(0, 10))
            open_page = partial(workspace.preview_report, report_key, arguments, company_id=company_id)
            save_copy = partial(workspace.export_report, report_key, arguments, company_id=company_id)
            ttk.Button(toolbar, text="Print / page preview", command=open_page).pack(side="left")
            ttk.Button(toolbar, text="Save printable copy", command=save_copy).pack(side="left", padx=7)
            def shortcut(action):
                if workspace.grab_current() is window and workspace.future is None and not workspace.close_pending:
                    action()
                return "break"
            for sequence, action in (("<Control-p>", open_page), ("<Control-P>", open_page),
                                     ("<Control-s>", save_copy), ("<Control-S>", save_copy)):
                window.bind(sequence, lambda event, action=action: shortcut(action))
            close = ttk.Button(toolbar, text="Close", command=window.destroy)
            close.pack(side="right")
            ttk.Label(page, text="BT", font=("TkDefaultFont", 25, "bold"), anchor="center").pack(fill="x")
            ttk.Label(page, text=company_name, font=("TkDefaultFont", 13, "bold"), anchor="center").pack(fill="x")
            urdu = {"Cash book": "روزنامچہ", "Account ledger": "کھاتہ نام", "Sub-trial balance": "چٹھہ"}[report]
            ttk.Label(page, text=titles[report] + "  ·  " + urdu, style="Title.TLabel", anchor="center").pack(fill="x", pady=(0, 7))
            period = date_display(arguments["date_to"]) if not ledger else ((date_display(arguments["date_from"]) or "Beginning") + "  —  " + (date_display(arguments["date_to"]) or "Present"))
            ttk.Label(page, text=period, anchor="center").pack(fill="x", pady=(0, 8))
            summary = owned_variable(page)
            ttk.Label(page, textvariable=summary, wraplength=1020).pack(anchor="w", pady=(0, 10))
            if cash_book:
                columns = [("business_date", "Date / تاریخ"), ("document_number", "Voucher #"),
                           ("narration", "Particulars / تفصیل"), ("debit", "Receipt / وصول"),
                           ("credit", "Payment / ادائیگی"), ("balance", "Balance / بقایا")]
            elif ledger:
                columns = [("business_date", "Date / تاریخ"), ("document_number", "Voucher #"),
                           ("kind", "Type"), ("narration", "Particulars / تفصیل"),
                           ("debit", "Debit / بنام"), ("credit", "Credit / جمع"), ("balance", "Balance / بقایا")]
            else:
                columns = [("code", "Code"), ("hierarchy_name", "Account / کھاتہ"),
                           ("opening", "Opening"), ("debit", "Debit / بنام"),
                           ("credit", "Credit / جمع"), ("closing", "Balance / بقایا")]
            footer = ttk.Frame(page)
            footer.pack(side="bottom", fill="x", pady=(8, 0))
            count = owned_variable(footer)
            ttk.Label(footer, textvariable=count).pack(side="left")
            tree = workspace.tree(page, columns, [], height=15)
            for key, _ in columns:
                tree.column(key, anchor="e" if key in {"debit", "credit", "balance", "opening", "closing"} else "w",
                            width=230 if key in {"narration", "hierarchy_name"} else 120)

            def show(data):
                if not window.winfo_exists():
                    return
                rows = data["rows"]
                tree.delete(*tree.get_children())
                for index, source in enumerate(rows):
                    row = dict(source)
                    row["business_date"] = date_display(row["business_date"]) if row.get("business_date") else ""
                    row["hierarchy_name"] = "  " * min(row.get("depth", 0), 12) + row.get("name", "")
                    tree.insert("", "end", iid=str(index), values=[str(row.get(key, "")) for key, _ in columns])
                offset = data.get("offset", 0)
                total = data.get("total", len(rows))
                count.set(f"{offset + 1 if rows else 0}–{offset + len(rows)} of {total}")
                if ledger or cash_book:
                    account = data["account"]
                    summary.set(f"{account['code']} · {account['name']}\nOpening: {data['opening']}    Debit: {data['debit']}    Credit: {data['credit']}    Closing: {data['closing']}")
                else:
                    totals = data["totals"]
                    summary.set(f"Opening: {totals['opening']}    Debit: {totals['debit']}    Credit: {totals['credit']}    Closing: {totals['closing']}")
                before.configure(state="normal" if offset else "disabled", command=lambda: load(max(0, offset - 100)))
                after.configure(state="normal" if offset + len(rows) < total else "disabled", command=lambda: load(offset + 100))
                if rows:
                    tree.selection_set("0")
                    tree.focus("0")
                    tree.focus_set()

            def load(offset):
                if ledger or cash_book:
                    operation = workspace.reports.cash_book if cash_book else workspace.reports.ledger
                    workspace.run(partial(operation, actor, company_id, limit=100, offset=offset, **arguments), show)

            after = ttk.Button(footer, text="Next", width=9)
            after.pack(side="right")
            before = ttk.Button(footer, text="Previous", width=9)
            before.pack(side="right", padx=7)
            show(result)
            window._initial_focus = tree if result["rows"] else close

        def view():
            if state["preview"] is not None and state["preview"].winfo_exists():
                state["preview"].lift()
                return
            try:
                chosen = form.values()
                selected_company = chosen.pop("company_id")
                if not selected_company or selected_company != state["company"]:
                    raise ValueError("Choose an available company and wait for its accounts to load.")
                if (ledger or cash_book) and not chosen.get("account_id"):
                    raise ValueError("Select the account for this report.")
            except ValueError as error:
                messagebox.showerror(titles[report], str(error), parent=dialog)
                return
            operation = workspace.reports.cash_book if cash_book else workspace.reports.ledger if ledger else workspace.reports.sub_trial_balance
            workspace.run(partial(operation, actor, selected_company, **chosen),
                          lambda data: preview(data, selected_company, chosen))

        form = FinancialReportFilter(body, report=report, companies=companies, company_id=company,
                                     choice_factory=Choice, date_from=start if ledger else None,
                                     date_to=date.today().isoformat(), on_view=view,
                                     on_exit=close_filter, on_company=change_company)
        form.pack(fill="both", expand=True)
        form.set_accounts(selectors["accounts"], selectors.get("default_account_id"))
        dialog.report_filter = form
        dialog._initial_focus = form.first_widget()
        dialog.protocol("WM_DELETE_WINDOW", close_filter)

    workspace.run(fetch, create_filter)
