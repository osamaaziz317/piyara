"""Complete, live-authorized native reports as private standalone HTML files.

Queries, company labels and totals share one transaction. Financial print
layouts follow the owner's photographed black-and-white report grids. No
browser, printer, external asset or recalculation of report totals is involved.
"""

from decimal import Decimal
from datetime import date
from html import escape
import os
from pathlib import Path
import tempfile

from bifma.database import new_destination, publish_new_file
from bifma.native_reports import NativeReports
from bifma.native_schema import native_transaction


_TITLES = {"trial_balance": "Trial balance", "sub_trial_balance": "Sub-trial balance", "account_ledger": "Account ledger",
           "city_balances": "Account balances by city", "cash_book": "Cash book",
           "profit_loss": "Periodic trading profit",
           "sales_detail": "Sales / returns detail", "purchase_detail": "Purchases / returns detail",
           "stock_quantities": "Stock quantities", "stock_valuation": "Stock valuation", "item_ledger": "Item stock ledger"}
_CSS = """
@page { size: A4 landscape; margin: 12mm; }
* { box-sizing: border-box; }
body { color: #172b38; background: #eef1f4; margin: 0;
       font: 10pt/1.45 "Segoe UI", Arial, sans-serif; }
main { max-width: 297mm; margin: 18px auto; padding: 12mm; background: white; }
header { border-bottom: 2px solid #007e78; padding-bottom: 10px; }
h1 { font-size: 19pt; margin: 8px 0; }
h2 { font-size: 12pt; margin: 18px 0 6px; }
p { margin: 6px 0; }
.company { font-size: 15pt; }
.minor, .code { color: #52616b; font-size: 8pt; }
.urdu { display: block; font: 11pt/1.7 "Noto Nastaliq Urdu", "Noto Naskh Arabic", "Segoe UI", Arial, sans-serif; }
.address { white-space: pre-wrap; overflow-wrap: anywhere; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 8.5pt; margin: 12px 0; }
th, td { text-align: left; vertical-align: top; padding: 6px 4px; border-bottom: 1px solid #d9e0e4; overflow-wrap: anywhere; }
th { background: #eef5f4; }
thead { display: table-header-group; }
tr { break-inside: avoid; page-break-inside: avoid; }
.number { text-align: right; direction: ltr; font-variant-numeric: tabular-nums; }
.totals { width: 55%; margin-left: auto; break-inside: avoid; }
.totals th { background: transparent; width: 60%; }
bdi { unicode-bidi: isolate; }
footer { border-top: 1px solid #d9e0e4; color: #52616b; font-size: 8pt; padding-top: 8px; margin-top: 18px; }
@media print { body { background: white; } main { max-width: none; padding: 0; margin: 0; }
  h2 { break-after: avoid; page-break-after: avoid; } }
"""

_FINANCIAL_CSS = """
@page { size: A4 portrait; margin: 12mm; }
* { box-sizing: border-box; }
body { background: #e6e6e6; color: #111; margin: 0;
       font: 9pt/1.35 Arial, "Segoe UI", sans-serif; }
main { width: 210mm; min-height: 297mm; margin: 12px auto; padding: 12mm;
       background: #fff; }
header { position: relative; text-align: center; border-bottom: 1px solid #111;
         padding-bottom: 6px; margin-bottom: 8px; }
.company { font: bold 17pt/1.25 "Times New Roman", serif; }
.company .code { display: none; }
.company br { display: none; }
.company .urdu { display: block; }
h1 { font-size: 13pt; margin: 3px 0; }
h2 { font-size: 10pt; margin: 12px 0 3px; text-align: right; }
.urdu { font-family: "Noto Nastaliq Urdu", "Noto Naskh Arabic", "FreeSerif", "Segoe UI", Arial, sans-serif;
        direction: rtl; unicode-bidi: isolate; }
.urdu-title { display: block; font-size: 17pt; line-height: 1.7; }
.period { text-align: right; margin: 6px 0 2px; }
.account-name { text-align: right; margin: 5px 0; font-size: 10pt; }
.account-name .code { display: inline; margin-inline-end: 6px; }
.account-name br { display: none; }
.account-name .urdu { display: inline; margin-inline-start: 6px; }
.number { direction: ltr; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.code { font-size: 8pt; }
abbr { text-decoration: none; }
bdi { unicode-bidi: isolate; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; }
th, td { border: 1px solid #777; padding: 3px 4px; vertical-align: top; overflow-wrap: anywhere; }
th { font-weight: normal; background: #fff; text-align: center; }
th .urdu { display: block; line-height: 1.7; }
thead { display: table-header-group; }
tr { break-inside: avoid; page-break-inside: avoid; }
.report { font-size: 8pt; }
.report .balance { color: #263e83; font-weight: bold; width: 15%; }
.report .side { width: 5%; text-align: center; }
.report .amount { width: 12%; }
.report .narration { width: 30%; text-align: right; }
.report .date { width: 11%; }
.report .kind { width: 8%; }
.report .voucher { width: 7%; }
.cash-panels { display: flex; gap: 8px; align-items: flex-start; }
.cash-panel { width: calc(50% - 4px); }
.cash-panel h2 { margin: 3px 0 5px; font-size: 11pt; text-align: center; }
.cash-panel h2 .urdu { display: block; line-height: 1.7; }
.cash-panel .amount { width: 22%; }
.cash-panel .voucher { width: 22%; }
.cash-panel .narration { width: 56%; }
.cash-total { display: flex; justify-content: space-between; border-top: 1px solid #111;
              padding-top: 4px; margin-top: 5px; font-weight: bold; }
.totals { width: 58%; margin: 12px 0 0 auto; break-inside: avoid; }
.totals th { text-align: right; width: 66%; }
.totals th, .totals td { border: 0; border-bottom: 1px solid #777; }
.hierarchy-label .minor { display: block; font-size: 7pt; }
.report-notes { margin-top: 14px; font-size: 7pt; line-height: 1.35; color: #444; }
.report-notes p { margin: 3px 0; }
footer { margin-top: 8px; padding-top: 4px; border-top: 1px solid #777;
         color: #444; font-size: 7pt; }
@media print { body { background: white; } main { width: auto; min-height: 0; margin: 0; padding: 0; }
  .cash-panels { display: block; position: relative; }
  .cash-panels::after { content: ""; display: block; clear: both; }
  .cash-panel { width: 49%; float: left; }
  .cash-panel + .cash-panel { float: right; }
}
"""


def _text(value, *, numeric=False):
    if isinstance(value, Decimal):
        # Report aggregates may exceed the per-record 64-digit persistence
        # bounds. Format exactly without normalize(), ambient-context rounding,
        # exponent notation, or reapplying input validation to a computed sum.
        if not value.is_finite():
            raise ValueError("Report decimal values must be finite.")
        value = "0" if value.is_zero() else format(value, "f")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
    if value is None:
        value = ""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError("Report values must be text, integers or exact decimals.")
    return f'<bdi dir="{"ltr" if numeric else "auto"}">{escape(str(value), quote=True)}</bdi>'


def _label(record):
    text = '<span class="code">' + _text(record.get("code")) + "</span><br>" + _text(record.get("name"))
    if record.get("name_urdu"):
        text += '<span class="urdu" lang="ur">' + _text(record["name_urdu"]) + "</span>"
    return text


def _row_label(row, prefix):
    return _label({key: row.get(prefix + key) for key in ("code", "name", "name_urdu")})


def _city_label(record):
    if record.get("id") in {None, "unassigned"}:
        return _text("Unassigned")
    return _label(record)


def _table(columns, rows):
    headings = "".join(f'<th scope="col" class="{style}">{title}</th>' for title, style in columns)
    body = "".join('<tr class="report-row">' + "".join(f'<td class="{columns[index][1]}">{cell}</td>' for index, cell in enumerate(row)) + "</tr>" for row in rows)
    if not body:
        body = f'<tr><td colspan="{len(columns)}">No rows in the selected period.</td></tr>'
    return '<table class="report"><thead><tr>' + headings + "</tr></thead><tbody>" + body + "</tbody></table>"


def _totals(rows, title="Full-period totals"):
    return '<h2>' + escape(title) + '</h2><table class="totals"><tbody>' + "".join(
        f'<tr><th scope="row">{title}</th><td class="number">{_text("Unknown" if value is None else value, numeric=True)}</td></tr>'
        for title, value in rows) + "</tbody></table>"


def _financial_heading(english, urdu):
    return '<span class="urdu" lang="ur">' + urdu + '</span><span class="code">' + english + "</span>"


def _financial_date(value):
    """Present verified ISO business dates in the photographed day-first order."""
    if value:
        value = date.fromisoformat(value).strftime("%d-%m-%Y")
    return _text(value, numeric=True)


def _financial_kind(value):
    # These voucher abbreviations are visible in the supplied report output.
    # Keep the full saved kind available and leave other kinds unchanged.
    abbreviation = {"cash_payment": "CPV", "cash_receipt": "CRV", "journal": "JV", "sale": "SL"}.get(value)
    if abbreviation is None:
        return _text(value)
    return '<abbr title="' + escape(value, quote=True) + '">' + abbreviation + "</abbr>"


def _financial_notes(snapshot):
    """Keep provenance explicit without crowding the printed report heading."""
    report, baseline = snapshot["report"], snapshot.get("baseline")
    parts = ['<section class="report-notes">']
    if baseline:
        parts.append("<p>Existing balances and posted transactions · Existing balances as at " + _text(baseline["cutoff_date"])
                     + ". These preserved closing balances are carried forward once, followed by new posted entries. "
                     "A range including that date shows the closing snapshot in the period columns. "
                     "This report does not reconstruct historical turnover before that closing snapshot.</p>")
        parts.append("<p>Original archive SHA-256: " + _text(baseline["source_sha256"]) + "</p>")
    if report == "cash_book":
        role = {"current_master": "Current account master", "saved_snapshot": "Saved cash classification in a posted document",
                "baseline_snapshot": "Original company's configured cash account in the preserved baseline"}[snapshot["result"]["cash_role_source"]]
        parts.append("<p>Cash account eligibility: " + _text(role) + ". Includes all posted journal entries for this account, including reversals.</p>")
    if report == "sub_trial_balance":
        parts.append("<p>Each account row includes its own and descendant postings under the current parent links. "
                     "Do not add these overlapping subtotals together. Grand totals count direct postings once. "
                     "Indentation is limited for deep charts; the displayed level retains the full depth.</p>")
    parts.append("<p>Positive opening, running and closing balances are debit; negative balances are credit. "
                 "Period debit and credit columns are unsigned amounts. Entries are ordered by business date and posting order. "
                 "Opening includes earlier dates; running balances include every preceding entry.</p>")
    parts.append("</section><footer>Operational report · Company and report headings use current labels · Generated at "
                 + _text(snapshot["generated_at"]) + " (UTC)</footer>")
    return "".join(parts)


def _render_financial(snapshot):
    """Dense monochrome financial reports, using the authorized snapshot only."""
    report, company = snapshot["report"], snapshot["company"]
    filters, result = snapshot["filters"], snapshot["result"]
    english, urdu = {
        "cash_book": ("Cash Book", "روزنامچہ"),
        "account_ledger": ("Party Ledger", "کھاتہ نامہ"),
        "sub_trial_balance": ("Sub-trial balance", "چٹھہ"),
    }[report]
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
             "<title>" + escape(english + " · " + company["name"], quote=True) + "</title><style>" + _FINANCIAL_CSS,
             '</style></head><body><main><header><div class="company">' + _label(company) + "</div>",
             '<h1><span class="urdu urdu-title" lang="ur">' + urdu + '</span><span class="code">' + english + "</span></h1>"]
    if filters["date_from"] == filters["date_to"] and filters["date_to"]:
        period = 'Date / تاریخ: ' + _financial_date(filters["date_to"])
    else:
        period = 'Date From / از: ' + (_financial_date(filters["date_from"]) if filters["date_from"] else _text("Beginning"))
        period += ' · Date To / تا: ' + (_financial_date(filters["date_to"]) if filters["date_to"] else _text("No end date"))
    parts.append('<p class="period">' + period + "</p>")
    if company.get("address"):
        parts.append('<p class="code">' + _text(company["address"]) + "</p>")
    if company.get("address_urdu"):
        parts.append('<p class="urdu" lang="ur">' + _text(company["address_urdu"]) + "</p>")
    parts.append("</header>")
    if report in {"cash_book", "account_ledger"}:
        parts.append('<div class="account-name">Account (current label): ' + _label(result["account"]) + "</div>")
    if report == "cash_book":
        # Cash-in is the cash account's debit and cash-out its credit. Keep
        # each row's saved narration/reference; do not guess the other account.
        parts.append('<div class="cash-panels">')
        for side, title, urdu_title in (("credit", "Cash payments", "جمع / ادائیگی"),
                                        ("debit", "Cash receipts", "نام / وصولی")):
            parts.append('<section class="cash-panel"><h2>' + _financial_heading(title, urdu_title) + "</h2>")
            rows = []
            for row in result["rows"]:
                if row[side] == 0:
                    continue
                reference = _text(row.get("document_number"))
                reference += '<br><span class="code">' + _financial_kind(row.get("kind")) + "</span>"
                if filters["date_from"] != filters["date_to"]:
                    reference += '<br><span class="code">' + _financial_date(row["business_date"]) + "</span>"
                rows.append([_text(row[side], numeric=True), reference, _text(row.get("narration"))])
            parts.append(_table([(_financial_heading("Amount", "رقم"), "number amount"),
                                 (_financial_heading("Voucher", "واؤچر"), "voucher"),
                                 (_financial_heading("Narration", "تفصیل"), "narration")], rows))
            parts.append('<div class="cash-total"><span class="number">' + _text(result[side], numeric=True)
                         + '</span><span class="urdu" lang="ur">ٹوٹل رقم</span></div></section>')
        parts.append("</div>")
        totals = [("Opening Dr / −Cr", result["opening"]), ("Period cash in / debits", result["debit"]),
                  ("Period cash out / credits", result["credit"]), ("Closing Dr / −Cr", result["closing"])]
    elif report == "account_ledger":
        columns = [(_financial_heading("Balance", "بقایہ"), "number balance"), ("Dr / Cr", "side"),
                   (_financial_heading("Credit", "جمع"), "number amount"),
                   (_financial_heading("Debit", "نام"), "number amount"),
                   (_financial_heading("Narration", "تفصیل"), "narration"),
                   (_financial_heading("Date", "تاریخ"), "date"),
                   (_financial_heading("Type", "قسم"), "kind"),
                   (_financial_heading("Voucher", "نمبر"), "voucher")]
        rows = [[_text(row["balance"], numeric=True), _text("Dr" if row["balance"] > 0 else "Cr" if row["balance"] < 0 else ""),
                 _text(row["credit"], numeric=True), _text(row["debit"], numeric=True), _text(row.get("narration")),
                 _financial_date(row["business_date"]), _financial_kind(row.get("kind")), _text(row.get("document_number"))]
                for row in result["rows"]]
        parts.append(_table(columns, rows))
        totals = [("Opening Dr / −Cr", result["opening"]), ("Period debits", result["debit"]),
                  ("Period credits", result["credit"]), ("Closing Dr / −Cr", result["closing"])]
    else:
        columns = [(_financial_heading("Closing Dr / −Cr", "بقایہ"), "number"),
                   (_financial_heading("Credit", "جمع"), "number"),
                   (_financial_heading("Debit", "نام"), "number"),
                   (_financial_heading("Opening Dr / −Cr", "سابقہ بقایہ"), "number"),
                   (_financial_heading("Account", "کھاتہ"), "narration")]
        rows = []
        for row in result["rows"]:
            label = (f'<div class="hierarchy-label" style="margin-inline-start:{min(row["depth"], 8)}em">'
                     + '<span class="minor">Level ' + _text(row["depth"] + 1) + "</span>" + _label(row) + "</div>")
            rows.append([_text(row[key], numeric=True) for key in ("closing", "credit", "debit", "opening")] + [label])
        parts.append(_table(columns, rows))
        totals = [("Net opening Dr / −Cr", result["totals"]["opening"]), ("Period debits", result["totals"]["debit"]),
                  ("Period credits", result["totals"]["credit"]), ("Net closing Dr / −Cr", result["totals"]["closing"])]
    parts.append(_totals(totals, "Grand totals (direct postings once)" if report == "sub_trial_balance" else "Full-period totals"))
    parts.append(_financial_notes(snapshot))
    parts.append("</main></body></html>\n")
    return "".join(parts)


def _render(snapshot):
    report, company = snapshot["report"], snapshot["company"]
    if report in {"account_ledger", "cash_book", "sub_trial_balance"}:
        return _render_financial(snapshot)
    filters, result = snapshot["filters"], snapshot["result"]
    baseline = snapshot.get("baseline")
    includes_baseline = baseline is not None and report not in {"sales_detail", "purchase_detail"}
    historical_activity = bool(snapshot.get("historical_activity"))
    original_trades = bool(snapshot.get("original_trade_activity"))
    title = _TITLES[report]
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
             "<title>" + escape(title + " · " + company["name"], quote=True) + "</title><style>" + _CSS,
             '</style></head><body><main><header><div class="company">' + _label(company) + "</div>"]
    if company.get("address"):
        parts.append('<p class="address">' + _text(company["address"]) + "</p>")
    if company.get("address_urdu"):
        parts.append('<p class="address urdu" lang="ur">' + _text(company["address_urdu"]) + "</p>")
    parts += ["<h1>" + title + "</h1><p>From: " + _text(filters["date_from"] or "Beginning")
              + " · To: " + _text(filters["date_to"] or "No end date") + "</p>",
              "<p>" + ("Original records and new posted trade activity" if original_trades else
                         "Original and new stock activity" if historical_activity else
                         "Existing balances and posted transactions" if includes_baseline else "Posted native transactions only")
              + " · Full report · " + _text(len(result["rows"])) + " rows</p>"]
    if historical_activity:
        parts.append("<p>Original stock movements through " + _text(baseline["cutoff_date"])
                     + " and new posted movements afterward use the selected dates. Opening columns contain earlier activity; preserved closing balances are not added a second time.</p>")
    elif includes_baseline:
        parts.append("<p>Existing balances as at " + _text(baseline["cutoff_date"])
                     + ". These preserved closing balances are carried forward once, followed by new posted entries.</p>")
        if report == "profit_loss":
            parts.append("<p>Income and expenses cover new transactions after the baseline. Existing stock values supply the opening inventory. This report does not reconstruct historical period turnover.</p>")
        else:
            parts.append("<p>A range including that date shows the baseline opening in the period columns; it does not reconstruct historical turnover. Earlier documents are available in their sales, purchase or voucher section. Dated stock reports include original stock activity.</p>")
    if report == "city_balances":
        city = _city_label(result["city"]) if result["city"] is not None else _text("All cities, including unassigned")
        parts.append("<p>City filter (current assignment): " + city + "</p>")
        parts.append("<p>Account kind (current master): " + _text(filters["account_kind"] or "All kinds") + "</p>")
        parts.append("<p>Account search (literal): " + _text(filters["search"] or "No search filter") + "</p>")
        parts.append("<p>Zero closing balances: " + _text("Retained" if filters["include_zero"] else "Excluded") + "</p>")
    if report in {"sales_detail", "purchase_detail"}:
        if original_trades:
            parts.append("<p>Original archive records and new posted entries are counted once. Original party amounts follow the preserved source posting rules; new document amounts follow their saved posting snapshots. An unavailable amount is Unknown, not zero.</p>")
            parts.append("<p>Original documents: " + _text(result.get("original_document_count", 0))
                         + " · New posted documents: " + _text(result.get("current_document_count", 0))
                         + " · Original lines with review notes: " + _text(result.get("original_issue_count", 0))
                         + " · Original documents without lines: " + _text(result.get("original_empty_document_count", 0)) + "</p>")
        parts.append("<p>Include: " + _text(filters["kind"]) + " · Documents: " + _text(result["document_count"]) + "</p>")
        parts.append("<p>Payment mode: " + _text({"account": "Account", "cash": "Cash", "all": "Account and cash"}[filters["payment_mode"]]) + "</p>")
        for key, title in (("account", "Party"), ("product", "Item"), ("store", "Store")):
            if result.get(key):
                parts.append("<p>" + title + " filter (current label): " + _label(result[key]) + "</p>")
        if filters["search"]:
            parts.append("<p>Document search: " + _text(filters["search"]) + "</p>")
    if report in {"item_ledger", "stock_quantities", "stock_valuation"}:
        parts.append("<p>Stock pool: " + _text(filters["pool"]) + " · Store: "
                     + (_label(result["store"]) if result.get("store") else _text("All company stores")) + "</p>")
        if report == "item_ledger":
            parts.append("<p>Item (current label): " + _label(result["product"]) + "</p>")
        elif filters["search"]:
            parts.append("<p>Item search: " + _text(filters["search"]) + "</p>")
    parts.append("</header>")
    rows = []
    if report == "profit_loss":
        parts.append("<p>Status: " + _text("Ready for review" if result["status"] == "ready" else "Review required — result unavailable") + "</p>")
        columns = [("Account (current label)", ""), ("Classification", ""), ("Opening Dr / −Cr", "number"),
                   ("Period debit", "number"), ("Period credit", "number"), ("Period contribution", "number")]
        for row in result["rows"]:
            rows.append([_label(row), _text(row["classification"] or "Unclassified")]
                        + [_text("Unknown" if row[key] is None else row[key], numeric=True)
                           for key in ("opening", "debit", "credit", "contribution")])
        totals = [("Classified income", result["totals"]["income"]), ("Classified expenses", result["totals"]["expense"]),
                  ("Income less expenses before inventory adjustment", result["totals"]["classified_result"]),
                  ("Opening inventory value (both pools)", result["totals"]["inventory_opening"]),
                  ("Closing inventory value (both pools)", result["totals"]["inventory_closing"]),
                  ("Net external stock openings", result["totals"]["opening_injections"]),
                  ("Inventory adjustment", result["totals"]["inventory_adjustment"]),
                  ("Periodic trading profit / −loss", result["totals"]["profit"]),
                  ("Accounts needing classification", result["totals"]["unclassified_count"]),
                  ("Mapping issues", result["totals"]["mapping_issue_count"]),
                  ("Movements with unknown cost", result["totals"]["missing_valuation_count"])]
    elif report == "trial_balance":
        columns = [("Account (current label)", ""), ("Opening Dr / −Cr", "number"), ("Period debit", "number"), ("Period credit", "number"), ("Closing Dr / −Cr", "number")]
        for row in result["rows"]:
            label = _label(row)
            rows.append([label] + [_text(row[key], numeric=True) for key in ("opening", "debit", "credit", "closing")])
        totals = [("Net opening Dr / −Cr", result["totals"]["opening"]), ("Period debits", result["totals"]["debit"]),
                  ("Period credits", result["totals"]["credit"]), ("Net closing Dr / −Cr", result["totals"]["closing"])]
    elif report == "city_balances":
        amounts = ("opening", "debit", "credit", "closing", "closing_debit", "closing_credit")
        amount_columns = [("Opening Dr / −Cr", "number"), ("Period debit", "number"), ("Period credit", "number"),
                          ("Closing Dr / −Cr", "number"), ("Closing debit", "number"), ("Closing credit", "number")]
        columns = [("Account (current label)", ""), ("City (current assignment)", ""), ("Kind (current master)", "")] + amount_columns
        for row in result["rows"]:
            city = {"id": row["city_id"], **{key: row["city_" + key] for key in ("code", "name", "name_urdu")}}
            label = _label(row)
            if not row["is_active"] or not row["is_postable"]:
                status = [name for key, name in (("is_active", "Inactive"), ("is_postable", "Not postable")) if not row[key]]
                label += '<br><span class="minor">' + _text(" · ".join(status)) + "</span>"
            rows.append([label, _city_label(city), _text(row["kind"])] + [_text(row[key], numeric=True) for key in amounts])
        totals = [("Accounts", result["totals"]["account_count"]), ("Net opening Dr / −Cr", result["totals"]["opening"]),
                  ("Period debits", result["totals"]["debit"]), ("Period credits", result["totals"]["credit"]),
                  ("Net closing Dr / −Cr", result["totals"]["closing"]), ("Closing debit balances", result["totals"]["closing_debit"]),
                  ("Closing credit balances", result["totals"]["closing_credit"])]
        parts.append("<h2>Account detail</h2>")
    elif report in {"stock_quantities", "stock_valuation"}:
        columns = [("Item (current label)", ""), ("Store (current label)", ""), ("Opening", "number"), ("In", "number"), ("Out", "number"), ("Closing", "number")]
        if report == "stock_valuation":
            columns += [("Opening value", "number"), ("Value in", "number"), ("Value out", "number"), ("Closing value", "number")]
        for row in result["rows"]:
            cells = [_label(row), _label({"code": row["store_code"], "name": row["store"], "name_urdu": row["store_name_urdu"]})]
            cells += [_text(row[key], numeric=True) for key in ("opening", "in", "out", "closing")]
            if report == "stock_valuation":
                cells += [_text("Unknown" if row[key] is None else row[key], numeric=True)
                          for key in ("value_opening", "value_in", "value_out", "value_closing")]
            rows.append(cells)
        totals = []
        if report == "stock_valuation":
            totals = [("Opening value", result["totals"]["value_opening"]), ("Period value in", result["totals"]["value_in"]),
                      ("Period value out", result["totals"]["value_out"]), ("Closing value", result["totals"]["value_closing"]),
                      ("Movements with unknown cost", result["totals"]["missing_valuation_count"])]
    elif report in {"sales_detail", "purchase_detail"}:
        columns = [("Date", ""), ("Document / type", ""), ("Party / cash account", ""), ("Item / base unit", ""),
                   ("Store", ""), ("Signed quantity", "number"), ("Rate", "number"),
                   ("Discount %", "number"), ("Signed line amount", "number")]
        for row in result["rows"]:
            reference = _text(row["document_number"]) + '<br><span class="minor">' + _text(row["kind"].replace("_", " ")) + "</span>"
            reference += '<br><span class="minor">Payment: ' + _text(row["payment_mode"]) + "</span>"
            if original_trades:
                reference += '<br><span class="minor">' + _text("Original record" if row.get("source") == "original" else "New posted record") + "</span>"
                if row.get("issues"):
                    reference += '<br><span class="minor">' + _text("; ".join(row["issues"])) + "</span>"
            if row.get("reverses_document_id"):
                reference += '<br><span class="minor">Reverses ' + _text(row["reverses_document_number"]) + "</span>"
            item = _row_label(row, "product_") + '<br><span class="minor">Base unit: ' + _row_label(row, "unit_") + "</span>"
            rows.append([_text(row["business_date"]), reference, _row_label(row, "account_"), item, _row_label(row, "store_")]
                        + [_text("Unknown" if row[key] is None else row[key], numeric=True) for key in ("signed_quantity", "rate", "discount_percent", "signed_net_amount")])
        totals = [("Invoice line amounts", result["totals"]["invoice"]), ("Return line amounts", result["totals"]["return"]),
                  ("Reversal line amounts", result["totals"]["reversal"]), ("Net line amount", result["totals"]["net"])]
    else:
        columns = [("Date", ""), ("Document / reversal", ""), ("Item", ""), ("Store", ""), ("In", "number"), ("Out", "number"), ("Running quantity", "number")]
        columns += [("Value in", "number"), ("Value out", "number"), ("Running value", "number")]
        for row in result["rows"]:
            reference = _text(row["document_number"]) + '<br><span class="minor">' + _text(row["kind"]) + "</span>"
            if row.get("reverses_document_id"):
                reference += '<br><span class="minor">Reverses ' + _text(row.get("reverses_document_number") or row["reverses_document_id"]) + "</span>"
            rows.append([_text(row["business_date"]), reference, _row_label(row, "product_"), _row_label(row, "store_")]
                        + [_text(row[key], numeric=True) for key in ("in", "out", "balance")]
                        + [_text("Unknown" if row[key] is None else row[key], numeric=True) for key in ("value_in", "value_out", "value_balance")])
        totals = [("Opening quantity", result["opening"]), ("Period quantity in", result["in"]),
                  ("Period quantity out", result["out"]), ("Closing quantity", result["closing"]),
                  ("Opening value", result["value_opening"]), ("Period value in", result["value_in"]),
                  ("Period value out", result["value_out"]), ("Closing value", result["value_closing"]),
                  ("Movements with unknown cost", result["missing_valuation_count"])]
    parts.append(_table(columns, rows))
    if report == "city_balances":
        parts.append("<h2>Current city summary</h2>")
        group_rows = [[_city_label({"id": row["city_id"], **{key: row[key] for key in ("code", "name", "name_urdu")}}),
                       _text(row["account_count"], numeric=True)] + [_text(row[key], numeric=True) for key in amounts]
                      for row in result["groups"]]
        parts.append(_table([("City (current assignment)", ""), ("Accounts", "number")] + amount_columns, group_rows))
    if totals:
        totals_title = "Grand totals (selected accounts)" if report == "city_balances" else "Full-period totals"
        parts.append(_totals(totals, totals_title))
    if report == "profit_loss":
        if result["mapping_issues"]:
            parts.append("<h2>Classification and inventory review</h2>")
            parts.append(_table([("Item / account", ""), ("Document", ""), ("Required review", "")],
                                [[_text(row.get("code")), _text(row.get("document_number")), _text(row["issue"])]
                                 for row in result["mapping_issues"]]))
        parts.append("<p>Method: classified income less classified expenses, plus closing inventory minus opening inventory, less net externally introduced stock openings. Both regular and breakage inventory are included. Opening and reversal entries do not create trading profit.</p>")
        parts.append("<p>This periodic method requires purchase postings classified as expenses and sale postings as income, with no separate cost-of-sales inventory postings. Missing classifications, incompatible mappings or unknown costs make the result unavailable. Account classifications and product mappings are current; saved movement costs and baseline values remain unchanged.</p>")
    if report in {"sales_detail", "purchase_detail"}:
        document_totals = result["document_totals"]
        if document_totals is not None:
            parts.append(_totals([("Invoices", document_totals["invoice"]), ("Returns", document_totals["return"]),
                                  ("Reversals", document_totals["reversal"]), ("Net document amount", document_totals["net"])],
                                 "Recorded party / final document totals (each document once)" if original_trades else
                                 "Final document totals (each document once)"))
        else:
            parts.append("<p>Final document totals are omitted for item/store filters because header adjustments and rounding have no assigned per-item amount.</p>")
        parts.append("<h2>Full-period quantities by item and saved base unit</h2>")
        quantity_rows = [[_row_label(row, "product_"), _row_label(row, "unit_")]
                         + [_text("Unknown" if row[key] is None else row[key], numeric=True) for key in ("invoice", "return", "reversal", "net")]
                         for row in result["quantity_totals"]]
        parts.append(_table([("Item", ""), ("Base unit", ""), ("Invoices", "number"), ("Returns", "number"),
                             ("Reversals", "number"), ("Net quantity", "number")], quantity_rows))
    if report == "city_balances":
        parts.append("<p>City, account kind, labels and status use the current account master. A later master change can move the same historical balance to another group. Unassigned accounts remain a separate group. Each selected account contributes once to the summary and grand totals.</p>")
        parts.append("<p>Closing debit and closing credit totals keep the two sides separate; the signed net closing is their difference. This is an account balance report, with no invoice aging or receipt allocation.</p>")
    if report in {"trial_balance", "city_balances"}:
        parts.append("<p>Positive opening, running and closing balances are debit; negative balances are credit. Period debit and credit columns are unsigned amounts.</p>")
    elif report in {"sales_detail", "purchase_detail"}:
        parts.append("<p>Line amounts exclude header discounts, breakage, freight and document rounding. Invoices are positive and returns negative; reversals have the opposite sign of their original document. These signs describe trade activity, not stock direction. Quantities for different items or units are not added together.</p>")
        if original_trades:
            parts.append("<p>Original labels and recorded units come from the sealed archive; new labels come from saved document snapshots. Missing or conflicting original units remain explicit and are not replaced by a current item unit. Original rows precede the cutoff; new rows follow it. Each group has stable date/document/line ordering.</p>")
        else:
            parts.append("<p>Party, item, store and base-unit labels come from the saved document. Entries follow business date and posting order; originals remain visible when later reversed.</p>")
    elif report == "profit_loss":
        parts.append("<p>Historical source report layouts and tax treatment are not inferred. The income/expense detail is separate from preserved opening balances; a displayed zero is never substituted for an unavailable profit result.</p>")
    elif report == "stock_quantities":
        parts.append("<p>Quantities use each item's base unit. No inventory valuation is included. Quantities for different items are not added together.</p>")
    else:
        parts.append("<p>Historical movement values are preserved exactly. New regular movements use the last eligible purchase rate by posting sequence and line, across stores; values are fixed when posted. Breakage uses its entered rate and reversals cancel their original values. Discounts and header charges do not change this inventory method.</p>")
        parts.append("<p>Unknown costs prevent a complete value total; they are not treated as zero. Quantities use each item's base unit and are not added across different items. A closing value is the sum of movement values, not closing quantity multiplied by today's rate.</p>")
    if report == "item_ledger":
        parts.append("<p>Entries are ordered by business date and posting order. Opening includes earlier dates; running balances include every preceding entry.</p>")
    if report == "item_ledger":
        parts.append("<p>Item and store names follow the saved document where available. Older entries may show their current names.</p>")
    if includes_baseline or original_trades:
        parts.append("<p>Original archive SHA-256: " + _text(baseline["source_sha256"]) + "</p>")
    parts.append("<footer>Operational report · Company and report headings use current labels · Generated at "
                 + _text(snapshot["generated_at"]) + " (UTC)</footer></main></body></html>\n")
    return "".join(parts)


def _remove_owned_publication(destination, identity):
    try:
        current = destination.stat(follow_symlinks=False)
        if identity is not None and (current.st_dev, current.st_ino) == identity:
            destination.unlink()
    except FileNotFoundError:
        pass


class ReportExporter:
    def __init__(self, path, security=None, *, reports=None):
        if reports is not None and Path(reports.path).resolve() != Path(path).resolve():
            raise ValueError("The report service belongs to a different working database.")
        self.reports = reports if reports is not None else NativeReports(path, security=security)

    def export_html(self, actor, company_id, report, destination, **filters) -> Path:
        """Publish all filtered rows to a new mode-0600 UTF-8 HTML file.

        Authorization is checked within the report snapshot and again while
        publishing, so a session revoked or expired during rendering fails.
        Business records are unchanged; live authorization may update heartbeat.
        """
        snapshot = self.reports.export_snapshot(actor, company_id, report, **filters)
        content = _render(snapshot)
        destination = new_destination(destination)
        descriptor, name = tempfile.mkstemp(prefix=".bifma-report-", suffix=".html", dir=destination.parent)
        staged, identity = Path(name), None
        try:
            status = os.fstat(descriptor)
            identity = status.st_dev, status.st_ino
            os.chmod(staged, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                descriptor = None
                stream.write(content)
                stream.flush()
            verify_archive = getattr(self.reports, "verify_export_archive", None)
            if verify_archive is not None:
                verify_archive(snapshot)
            with native_transaction(self.reports.path) as connection:
                for module in snapshot.get("required_modules", (snapshot["module"],)):
                    self.reports.security.require(actor, company_id, module, "read", connection=connection)
                assert_archive = getattr(self.reports, "assert_export_archive_unchanged", None)
                if assert_archive is not None:
                    assert_archive(snapshot)
                publish_new_file(staged, destination)
        except BaseException:
            _remove_owned_publication(destination, identity)
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
            staged.unlink(missing_ok=True)
        return destination
