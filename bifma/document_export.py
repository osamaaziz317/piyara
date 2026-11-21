"""Private, standalone HTML copies of authorized saved documents.

Native values and labels come from one TransactionService.get result. Original
copies use the verified DocumentRegister and original company labels. Rendering
does not read current masters, replace saved invoice totals, infer settlements,
or value stock. Sale/BRCV layouts follow the owner's photographs; other layouts
remain native. The original executable print templates are not available.
"""

from decimal import Context, localcontext
from html import escape
import os
from pathlib import Path
import tempfile

from bifma.database import new_destination, publish_new_file, session
from bifma.calculations import as_decimal, canonical_decimal, convert_quantity
from bifma.native_schema import native_transaction
from bifma.transactions import TransactionService


class DocumentExportError(ValueError):
    """The document cannot be represented as a verified posted copy."""


_KINDS = {
    "sale": "Sale Voucher", "purchase": "Purchase invoice",
    "sale_return": "Sales return", "purchase_return": "Purchase return",
    "cash_sale": "Counter sales invoice", "cash_purchase": "Cash purchase invoice",
    "cash_sale_return": "Counter sale return", "cash_purchase_return": "Cash purchase return",
    "breakage_receive": "Breakage receive", "breakage_dispatch": "Breakage dispatch",
    "stock_opening": "Opening stock", "journal": "Journal voucher",
    "opening_balance": "Opening balance voucher", "cash_receipt": "Cash receipt",
    "cash_payment": "Cash payment", "bank_receipt": "Bank receipt",
    "bank_payment": "Bank payment", "reversal": "Reversal",
}
_BREAKAGE = {"breakage_receive", "breakage_dispatch"}
_CASH_TRADE = {"cash_sale", "cash_purchase", "cash_sale_return", "cash_purchase_return"}
_TRADE = {"sale", "purchase", "sale_return", "purchase_return"} | _BREAKAGE | _CASH_TRADE
_DECIMAL_CONTEXT = Context(prec=100)
_CSS = """
@page { size: A4; margin: 12mm; }
* { box-sizing: border-box; }
body { margin: 0; color: #172b38; background: #eef1f4;
       font: 10pt/1.45 "Segoe UI", Arial, sans-serif; }
main { max-width: 210mm; margin: 18px auto; padding: 13mm; background: white; }
header { border-bottom: 2px solid #007e78; padding-bottom: 12px; }
h1 { font-size: 19pt; margin: 5px 0 9px; }
h2 { font-size: 12pt; margin: 22px 0 8px; }
.company { font-size: 15pt; font-weight: 600; }
.meta { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 22px; }
.field { padding: 3px 0; }
.field-title { display: block; color: #52616b; font-size: 9pt; }
.badge { display: inline-block; border: 1px solid #007e78; padding: 2px 8px; }
table { border-collapse: collapse; width: 100%; table-layout: fixed;
        margin: 8px 0 16px; font-size: 9pt; }
th, td { border-bottom: 1px solid #d9e0e4; padding: 7px 5px; text-align: left;
         vertical-align: top; overflow-wrap: anywhere; }
th { background: #eef5f4; font-weight: 600; }
thead { display: table-header-group; }
tr { break-inside: avoid; page-break-inside: avoid; }
.number { text-align: right; font-variant-numeric: tabular-nums; direction: ltr; }
.line-number { width: 5%; }
.item { width: 23%; }
.store { width: 14%; }
.unit { width: 14%; }
.numeric { width: 11%; }
.sale-lines { font-size: 7.5pt; }
.sale-lines th, .sale-lines td { padding: 4px 2px; }
.sale-lines .line-number { width: 4%; }
.sale-lines .code { width: 8%; }
.sale-lines .item { width: 22%; }
.sale-lines .unit { width: 13%; }
.sale-lines .number { width: 8%; white-space: nowrap; }
.account { width: 34%; }
.totals { width: 66%; margin-left: auto; break-inside: avoid; }
.totals th { width: 71%; background: transparent; }
.grand-total { font-size: 12pt; font-weight: 700; border-top: 2px solid #007e78; }
.code { font-size: 8pt; color: #52616b; }
.urdu { display: block; font-family: "Noto Nastaliq Urdu", "Noto Naskh Arabic",
        "Segoe UI", Arial, sans-serif; font-size: 11pt; }
.minor { display: block; color: #52616b; font-size: 8pt; }
.notes { white-space: pre-wrap; overflow-wrap: anywhere; }
bdi { unicode-bidi: isolate; }
.reference { border-left: 3px solid #007e78; padding: 7px 12px; margin: 12px 0; }
footer { border-top: 1px solid #d9e0e4; margin-top: 24px; padding-top: 8px;
         color: #52616b; font-size: 8pt; }
@media print {
  body { background: white; }
  main { max-width: none; margin: 0; padding: 0; }
  h2 { break-after: avoid; page-break-after: avoid; }
}
"""


def _text(value, *, numeric=False):
    """Escape a scalar and isolate its direction from neighboring fields."""
    if value is None:
        value = ""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise DocumentExportError("Saved document text or exact decimal value is invalid")
    direction = "ltr" if numeric else "auto"
    return f'<bdi dir="{direction}">{escape(str(value), quote=True)}</bdi>'


def _label(record, context):
    if not isinstance(record, dict) or not isinstance(record.get("name"), str) or not record["name"] or not isinstance(record.get("code"), str):
        raise DocumentExportError(f"Saved {context} labels are unavailable; a verified snapshot is required")
    result = f'<span class="code">{_text(record["code"])}</span><br>{_text(record["name"])}'
    if record.get("name_urdu"):
        result += f'<span class="urdu" lang="ur">{_text(record["name_urdu"])}</span>'
    return result


def _field(title, content):
    return f'<div class="field"><span class="field-title">{title}</span>{content}</div>'


def _contact(record):
    """Optional details from this issued snapshot, never current master lookup."""
    fields = []
    for key, title in (("address", "Address"), ("address_urdu", "پتہ"), ("phone", "Phone"),
                       ("mobile", "Mobile"), ("fax", "Fax"), ("email", "Email"), ("website", "Website"),
                       ("sales_tax_number", "Sales tax number"), ("national_tax_number", "National tax number")):
        if record.get(key):
            text = _text(record[key])
            if key == "address_urdu":
                text = '<span class="urdu" lang="ur">' + text + "</span>"
            fields.append(_field(title, text))
    return '<div class="meta">' + "".join(fields) + "</div>" if fields else ""


def _table(title, columns, rows, *, table_class=None):
    heads = "".join(f'<th scope="col" class="{style}">{heading}</th>' for heading, style in columns)
    body = "".join("<tr>" + "".join(f'<td class="{columns[index][1]}">{cell}</td>' for index, cell in enumerate(row)) + "</tr>" for row in rows)
    css_class = f' class="{table_class}"' if table_class else ""
    return f'<section><h2>{title}</h2><table{css_class}><thead><tr>{heads}</tr></thead><tbody>{body}</tbody></table></section>'


def _unit(product):
    small, large = product.get("small_unit"), product.get("large_unit")
    result = _label(small, "base unit") if small is not None else _text("—")
    if large is not None:
        result += '<span class="minor">Large unit</span>' + _label(large, "large unit")
        result += '<span class="minor">Base units per large unit: ' + _text(product["unit_factor"], numeric=True) + "</span>"
    return result


def _remove_owned_publication(destination, identity):
    """Match native backup failure cleanup without deleting another writer's file."""
    try:
        current = destination.stat(follow_symlinks=False)
        if identity is not None and (current.st_dev, current.st_ino) == identity:
            destination.unlink()
    except FileNotFoundError:
        pass


def _cash_details(document, snapshot, calculation):
    """Validate issued cash facts without consulting current setup or allocating money."""
    kind = document["kind"]
    if kind == "reversal":
        reference = snapshot.get("reversal_of")
        kind = reference.get("kind") if isinstance(reference, dict) else None
        original = snapshot.get("original_snapshot")
        has_cash_facts = "cash_settlement" in snapshot or (
            isinstance(original, dict) and "cash_settlement" in original)
        if has_cash_facts and kind not in _CASH_TRADE:
            raise DocumentExportError("Saved cash reversal kind is unavailable or inconsistent")
    if kind not in _CASH_TRADE:
        return None
    saved = snapshot.get("cash_settlement")
    cash_id = document.get("account_id")
    account = snapshot.get("accounts", {}).get(cash_id)
    if (not isinstance(saved, dict) or saved.get("mode") != "cash"
            or saved.get("cash_account_id") != cash_id or not cash_id
            or not isinstance(account, dict) or account.get("id") != cash_id or account.get("kind") != "cash"
            or not isinstance(calculation.get("total"), str) or calculation["total"] != document.get("total_amount")):
        raise DocumentExportError("Saved full cash settlement is unavailable or inconsistent")
    for field, maximum in (("counterparty_name", 150), ("counterparty_mobile", 80)):
        value = saved.get(field)
        if not isinstance(value, str) or len(value) > maximum:
            raise DocumentExportError("Saved cash counterparty details are unavailable or inconsistent")
    if document["kind"] == "reversal":
        if not isinstance(original, dict) or original.get("cash_settlement") != saved:
            raise DocumentExportError("Saved cash reversal facts do not match its original")
    return {**saved, "kind": kind, "amount": calculation["total"]}


def _render(document):
    if document.get("status") != "posted":
        raise DocumentExportError("Only posted documents can be exported")
    kind = document.get("kind")
    if kind not in _KINDS:
        raise DocumentExportError("This document kind has no supported output layout")
    snapshot, values, calculation = (document.get(key) for key in ("snapshot", "values", "calculation"))
    if not all(isinstance(value, dict) for value in (snapshot, values, calculation)):
        raise DocumentExportError("Posted document has no verified saved snapshot")
    accounts = snapshot.get("accounts")
    origins = snapshot.get("lines")
    if not isinstance(accounts, dict) or not isinstance(origins, list):
        raise DocumentExportError("Posted document has incomplete saved labels")
    cash = _cash_details(document, snapshot, calculation)
    origins = {row["line_id"]: row for row in origins}
    company = _label(snapshot.get("company"), "company")
    parts = ["<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">",
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             '<meta http-equiv="Content-Security-Policy" content="default-src &#x27;none&#x27;; style-src &#x27;unsafe-inline&#x27;; base-uri &#x27;none&#x27;; form-action &#x27;none&#x27;">',
             '<title>BIFMA TRADERS · Posted document</title><style>', _CSS, "</style></head><body><main>",
             f'<header><div class="company">{company}</div>', _contact(snapshot["company"]),
             f'<h1>{_KINDS[kind]}</h1><div class="meta">',
             _field("Sale #" if kind == "sale" else "Document number", _text(document["document_number"])),
             _field("Document date", _text(document["business_date"], numeric=True)),
             _field("Posting status", '<span class="badge">Posted</span>')]
    if snapshot.get("bill_number"):
        parts.append(_field("Bill number", _text(snapshot["bill_number"])))
    if document.get("account_id"):
        title = "Buyer" if kind == "sale" else "Cash account" if cash else "Opening balance counterpart" if kind == "stock_opening" else "Cash / bank account" if kind in {"cash_receipt", "cash_payment", "bank_receipt", "bank_payment"} else "Account"
        parts.append(_field(title, _label(accounts.get(document["account_id"]), "header account")))
    if cash:
        parts.append(_field("Payment mode", _text("Cash reversal" if kind == "reversal" else "Full cash settlement")))
        counterparty = "Customer" if cash["kind"] in {"cash_sale", "cash_sale_return"} else "Supplier"
        if cash["counterparty_name"]:
            parts.append(_field(counterparty, _text(cash["counterparty_name"])))
        if cash["counterparty_mobile"]:
            parts.append(_field(counterparty + " mobile", _text(cash["counterparty_mobile"])))
    rounding = {"legacy_whole": "Whole units", "cents": "Two decimal places"}.get(document.get("rounding_policy"))
    if rounding is None or document.get("posting_places") not in (0, 2):
        raise DocumentExportError("Saved rounding policy is unavailable")
    if kind in _TRADE:
        parts.append(_field("Subtotal rounding", _text(rounding)))
    parts.extend([_field("Final decimal places", _text(document["posting_places"], numeric=True)), "</div></header>"])
    if document.get("account_id"):
        details = _contact(accounts[document["account_id"]])
        if details:
            parts.append("<section><h2>Account details</h2>" + details + "</section>")
    if kind == "reversal":
        original = snapshot.get("reversal_of")
        if not isinstance(original, dict) or not original.get("document_number") or not original.get("business_date"):
            raise DocumentExportError("Saved reversal reference is unavailable")
        parts.append('<div class="reference">Reverses document ' + _text(original["document_number"]) + " dated " + _text(original["business_date"], numeric=True) + "</div>")
        parts.append('<section><h2>Reversal reason</h2><div class="notes">' + _text(values.get("reason", document.get("notes", ""))) + "</div></section>")
    if document.get("reversal"):
        reference = document["reversal"]
        parts.append('<div class="reference">Reversed by document ' + _text(reference["document_number"]) + " dated " + _text(reference["business_date"], numeric=True) + "</div>")
    if kind in {"cash_sale_return", "cash_purchase_return"} or (kind in {"sale_return", "purchase_return"} and snapshot.get("return_of")):
        reference = snapshot.get("return_of")
        original_kind = kind.removesuffix("_return")
        if (not isinstance(reference, dict) or reference.get("kind") != original_kind
                or not reference.get("id") or not reference.get("document_number") or not reference.get("business_date")):
            raise DocumentExportError("Saved return reference is unavailable or inconsistent")
        origin = " from original history" if reference.get("source") == "history" else ""
        parts.append('<div class="reference">Returns document ' + _text(reference["document_number"]) + " dated " + _text(reference["business_date"], numeric=True) + origin + "</div>")

    movements = document.get("stock_movements", [])
    movement_lookup = {row["document_line_id"]: row for row in movements}
    items = []
    bill_lines = []
    input_lines = values.get("lines", [])
    for line in document.get("lines", []):
        origin = origins.get(line["id"])
        if not isinstance(origin, dict):
            raise DocumentExportError("Saved item labels are unavailable")
        product = origin.get("product")
        if kind == "sale":
            input_index = line["line_number"] - 1
            if (not isinstance(input_lines, list) or input_index < 0 or input_index >= len(input_lines)
                    or not isinstance(input_lines[input_index], dict)):
                raise DocumentExportError("Saved sale entry quantities are unavailable")
            entered = input_lines[input_index]
            if entered.get("product_id") != line["product_id"] or entered.get("quantity") != line["quantity"]:
                raise DocumentExportError("Saved sale item and quantity details do not reconcile")
            unit_id = entered.get("unit_id")
            entered_quantity = entered.get("entered_quantity")
            if unit_id is None and entered_quantity is None:
                unit_id = product.get("small_unit", {}).get("id") if product.get("small_unit") else None
                entered_quantity = line["quantity"]
                factor = "1"
            elif unit_id is None or entered_quantity is None:
                raise DocumentExportError("Saved sale entry unit and quantity do not reconcile")
            elif product.get("small_unit") and unit_id == product["small_unit"].get("id"):
                factor = "1"
            elif product.get("large_unit") and unit_id == product["large_unit"].get("id") and product.get("unit_factor") is not None:
                factor = product["unit_factor"]
            else:
                raise DocumentExportError("Saved sale unit is missing from its verified item snapshot")
            try:
                with localcontext(_DECIMAL_CONTEXT):
                    converted = convert_quantity(large=entered_quantity, small="0", factor=factor)
                    gross = as_decimal(line["quantity"]) * as_decimal(line["rate"])
                    discount_amount = gross - as_decimal(line["net_amount"])
            except ValueError as error:
                raise DocumentExportError(str(error)) from None
            if converted != as_decimal(line["quantity"]) or discount_amount < 0:
                raise DocumentExportError("Saved sale quantities or line amounts do not reconcile")
            unit = next((unit for unit in (product.get("small_unit"), product.get("large_unit"))
                         if isinstance(unit, dict) and unit.get("id") == unit_id), None)
            if unit is None:
                raise DocumentExportError("Saved sale unit label is unavailable")
            description = _text(product.get("name", ""))
            if product.get("name_urdu"):
                description += '<span class="urdu" lang="ur">' + _text(product["name_urdu"]) + "</span>"
            cells = [_text(line["line_number"], numeric=True), _text(product.get("code", "")),
                     description, _label(unit, "sale unit"),
                     _text(entered_quantity, numeric=True), _text(factor, numeric=True),
                     _text(line["quantity"], numeric=True), _text(line["rate"], numeric=True),
                     _text(line["discount_percent"], numeric=True), _text(canonical_decimal(discount_amount), numeric=True),
                     _text(line["net_amount"], numeric=True)]
        else:
            cells = [_text(line["line_number"], numeric=True), _label(product, "product"), _label(origin.get("store"), "store"), _unit(product), _text(line["quantity"], numeric=True)]
        if kind == "sale":
            items.append(cells)
        elif kind == "stock_opening":
            
            movement = movement_lookup.get(line["id"])
            if movement is None or movement.get("valuation_basis") != "opening_explicit":
                raise DocumentExportError("Verified opening stock details are unavailable")
            cells += [_text(movement["pool"]), _text(line["net_amount"], numeric=True)]
            items.append(cells)
        else:
            cells += [_text(line["rate"], numeric=True), _text(line["discount_percent"], numeric=True), _text(line["net_amount"], numeric=True)]
            items.append(cells)
        if kind in {"sale", "breakage_receive"}:
            bill_lines.append({
                "number": line["line_number"], "product": product,
                "unit": unit if kind == "sale" else product.get("small_unit"),
                "entered_quantity": entered_quantity if kind == "sale" else line["quantity"],
                "factor": factor if kind == "sale" else "1", "quantity": line["quantity"],
                "rate": line["rate"], "discount_percent": line["discount_percent"], "amount": line["net_amount"],
            })
    if items:
        if kind == "sale":
            columns = [("#", "line-number"), ("Item code", "code"), ("Item description", "item"),
                       ("Unit", "unit"), ("Unit qty", "number numeric"), ("×", "number numeric"),
                       ("Qty", "number numeric"), ("Rate", "number numeric"), ("Dis %", "number numeric"),
                       ("Discount", "number numeric"), ("Value", "number numeric")]
        else:
            columns = [("#", "line-number"), ("Item", "item"), ("Store", "store"), ("Base unit", "unit"), ("Quantity", "number numeric")]
        if kind == "stock_opening":
            columns += [("Stock pool", "numeric"), ("Opening value", "number numeric")]
        elif kind != "sale":
            columns += [("Rate", "number numeric"), ("Discount %", "number numeric"), ("Original line amount" if kind == "reversal" else "Line amount", "number numeric")]
        parts.append(_table("Sale items" if kind == "sale" else "Original items" if kind == "reversal" else "Items",
                            columns, items, table_class="sale-lines" if kind == "sale" else None))

    total_rows = []
    if kind == "sale":
        raw = calculation.get("raw_totals")
        if not isinstance(raw, dict) or any(not isinstance(raw.get(key), str) for key in ("subtotal", "rounding_adjustment", "final_rounding_adjustment")):
            raise DocumentExportError("Saved sale subtotal is unavailable")
        total_rows += [("Subtotal", raw["subtotal"]), ("Discount", values["discount"]),
                       ("Breakage", values["breakage"]), ("Freight", values["freight"])]
    elif kind in _TRADE:
        raw = calculation.get("raw_totals")
        fields = [("Raw line subtotal", "raw_subtotal"), ("Subtotal rounding adjustment", "rounding_adjustment"), ("Rounded subtotal", "subtotal")]
        if not isinstance(raw, dict) or any(key not in raw for _, key in fields) or any(key not in raw for key in ("header_total", "final_rounding_adjustment")):
            raise DocumentExportError("Saved invoice totals are unavailable")
        total_rows += [(name, raw[key]) for name, key in fields]
        if kind not in _BREAKAGE:
            total_rows += [("Less header discount", values["discount"]), ("Less breakage allowance", values["breakage"]), ("Add freight", values["freight"])]
        elif kind == "breakage_receive":
            total_rows.append(("Less breakage allowance", values["breakage"]))
        total_rows += [("Total before final rounding", raw["header_total"]), ("Final rounding adjustment", raw["final_rounding_adjustment"])]
    parts.append('<table class="totals"><tbody>')
    parts.extend(f'<tr><th scope="row">{name}</th><td class="number">{_text(value, numeric=True)}</td></tr>' for name, value in total_rows)
    parts.append('<tr class="grand-total"><th scope="row">Document total</th><td class="number">' + _text(document["total_amount"], numeric=True) + "</td></tr></tbody></table>")
    if cash:
        parts.append('<section><h2>' + ("Reversed cash amount" if kind == "reversal" else "Full cash amount")
                     + '</h2><p class="number">' + _text(cash["amount"], numeric=True) + "</p></section>")

    journal = []
    for line in ([] if kind == "sale" else document.get("journal_lines", [])):
        journal.append([_label(accounts.get(line["account_id"]), "posting account"), _text(line.get("narration", "")), _text(line["debit"], numeric=True), _text(line["credit"], numeric=True)])
    if journal:
        parts.append(_table("Posted accounting", [("Account", "account"), ("Narration", ""), ("Debit", "number"), ("Credit", "number")], journal))
    elif kind != "sale":
        parts.append("<section><h2>Posted accounting</h2><p>No accounting entries.</p></section>")

    stock = []
    for movement in ([] if kind == "sale" else movements):
        origin = origins.get(movement["document_line_id"])
        if not isinstance(origin, dict) or movement.get("pool") not in ("regular", "breakage") or movement.get("direction") not in ("in", "out"):
            raise DocumentExportError("Verified stock movement labels or directions are unavailable")
        stock.append([_label(origin.get("product"), "stock product"), _label(origin.get("store"), "stock store"), _text(movement["pool"]),
                      _text("In" if movement["direction"] == "in" else "Out"), _text(movement["quantity"], numeric=True)])
    if stock:
        parts.append(_table("Posted stock quantities", [("Item", "item"), ("Store", "store"), ("Stock pool", ""), ("Direction", ""), ("Quantity", "number")], stock))
    if kind != "reversal" and document.get("notes"):
        parts.append('<section><h2>' + ("Remarks" if kind == "sale" else "Notes") + '</h2><div class="notes">' + _text(document["notes"]) + "</div></section>")
    parts.append("<footer>Posted document copy · Saved labels and amounts</footer></main></body></html>\n")
    content = "".join(parts)
    if kind in {"sale", "breakage_receive"}:
        from bifma.bill_layout import render_bill
        with localcontext(_DECIMAL_CONTEXT):
            gross = sum((as_decimal(row["quantity"]) * as_decimal(row["rate"]) for row in bill_lines), as_decimal("0"))
            line_discount = gross - sum((as_decimal(row["amount"]) for row in bill_lines), as_decimal("0"))
        totals = [("کل رقم", "Gross line value", canonical_decimal(gross), False)]
        if line_discount:
            totals.append(("اشیاء کی رعایت", "Line discount", canonical_decimal(line_discount), False))
        raw = calculation["raw_totals"]
        if as_decimal(raw["rounding_adjustment"]):
            totals.append(("اعشاری فرق", "Subtotal rounding adjustment", raw["rounding_adjustment"], False))
        totals.append(("خالص رقم", "Subtotal", raw["subtotal"], False))
        if kind == "sale":
            totals.append(("رعایت", "Discount", values["discount"], False))
        totals.append(("بریکج", "Less breakage allowance", values["breakage"], False))
        if kind == "sale":
            totals.append(("کرایہ", "Freight", values["freight"], False))
        if as_decimal(raw["final_rounding_adjustment"]):
            totals.append(("آخری اعشاری فرق", "Final rounding adjustment", raw["final_rounding_adjustment"], False))
        totals.append(("بل رقم", "Document total", document["total_amount"], True))
        model = {"kind": kind, "company": snapshot["company"], "party": accounts[document["account_id"]],
                 "number": document["document_number"], "date": document["business_date"],
                 "bill_number": snapshot.get("bill_number"), "remarks": document.get("notes", ""),
                 "status": "Posted" + (" · Reversed by document " + document["reversal"]["document_number"] if document.get("reversal") else ""),
                 "lines": bill_lines, "totals": totals,
                 "show_discount": any(as_decimal(row["discount_percent"]) for row in bill_lines),
                 "footer": "Posted document copy · Saved labels and amounts"}
        return render_bill(model, details_html=content.split("<main>", 1)[1].rsplit("</main>", 1)[0])
    return content


class DocumentExporter:
    """Export one live-authorized posted document to a new private HTML file."""

    def __init__(self, path, security=None):
        self.transactions = TransactionService(path, security=security)

    def export_html(self, actor, company_id, document_id, destination) -> Path:
        """Return the created path; never replace a file or follow its symlink.

        Authorization and document reading share TransactionService's database
        transaction. Publication is atomic on the destination filesystem.
        Business records remain unchanged; authorization may refresh the session
        heartbeat. No browser or print job is launched.
        """
        document = self.transactions.get(actor, company_id, document_id)
        content = _render(document)
        destination = new_destination(destination)
        descriptor, name = tempfile.mkstemp(prefix=".bifma-document-", suffix=".html", dir=destination.parent)
        staged = Path(name)
        identity = None
        try:
            status = os.fstat(descriptor)
            identity = status.st_dev, status.st_ino
            # mkstemp creates mode 0600; make the intended permission explicit.
            os.chmod(staged, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                descriptor = None
                stream.write(content)
                stream.flush()
            publish_new_file(staged, destination)
        except BaseException:
            _remove_owned_publication(destination, identity)
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
            staged.unlink(missing_ok=True)
        return destination


def _render_original(document, company):
    """Copy the verified original detail view without reposting or recalculating."""
    if document.get("source") != "original":
        raise DocumentExportError("Choose a recorded original document.")
    title = str(document["title"])
    status = "Cancelled in original records" if document.get("status") == "cancelled" else "Original recorded document"
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
             "<title>" + escape(title, quote=True) + " · Original record</title><style>",
             _CSS, "</style></head><body><main><header>",
             '<div class="company">' + _label(company, "original company") + "</div>",
             "<h1>" + _text(title) + "</h1>", '<span class="badge">' + _text(status) + "</span>",
             '<div class="meta">']
    for label, value in document["fields"]:
        parts.append(_field(escape(str(label), quote=True), _text("—" if value is None else value)))
    parts.append("</div></header>")
    columns = [(escape(str(label), quote=True), "") for _, label in document["columns"]]
    if columns:
        rows = [[_text("—" if row.get(key) is None else row[key]) for key, _ in document["columns"]]
                for row in document["rows"]]
        parts.append(_table("Recorded line items", columns, rows))
    if document.get("notes"):
        parts.append('<section><h2>Record notes</h2><div class="notes">'
                     + _text("\n".join(document["notes"])) + "</div></section>")
    parts.append("<footer>Original record copy · Values from the preserved archive. "
                 "An em dash means no recorded value.</footer></main></body></html>\n")
    content = "".join(parts)
    if document.get("bill_print"):
        from bifma.bill_layout import render_bill
        return render_bill(document["bill_print"], details_html=content.split("<main>", 1)[1].rsplit("</main>", 1)[0])
    return content


def _original_bill(original, document, company, source_company):
    """Read the photographed bill's labels/packing inside the authorized source read.

    Header total is the register's recorded party amount. It is deliberately not
    recomputed from today's corrected posting rules or rounded line displays.
    """
    kind = document["kind"]
    master, detail = ("SMST", "SDTL") if kind == "sale" else ("BRMST", "BRDTL")
    header = original.execute(
        f"SELECT m.*,a.CHDESC,a.CHDESC_URDU FROM {master} m LEFT JOIN CHART a ON a.CHCODE=m.CHCODE "
        "WHERE m.TRNID=? AND m.CMP_ID=?", (document["id"], source_company)).fetchone()
    if header is None:
        raise DocumentExportError("The recorded bill is unavailable in this company.")
    fields = dict(document["fields"])
    rows = []
    # Packing fields are copied as recorded; absent components stay absent.
    for index, row in enumerate(original.execute(
            f"SELECT d.*,p.INAME,p.INAME_URDU,u.UOM_DESC,u.UOM_DESC_URDU FROM {detail} d "
            "LEFT JOIN PUR_ITEM p ON p.ICODE=d.ICODE LEFT JOIN UOM u ON u.UOM_ID=d.UOM_ID_SMALL "
            "WHERE d.TRNID=? ORDER BY CAST(d.LINE_NUM AS INTEGER),d.VNO", (document["id"],))):
        if index >= len(document["rows"]):
            raise DocumentExportError("The recorded bill lines changed during reading.")
        verified = document["rows"][index]
        if row["ICODE"] != verified["code"] or row["QTY"] != verified["quantity"] or row["RATE"] != verified["rate"]:
            raise DocumentExportError("The recorded bill lines do not match their verified details.")
        unit = {"name": row["UOM_DESC"] or "", "name_urdu": row["UOM_DESC_URDU"]}
        bill_row = {"number": row["LINE_NUM"], "product": {"code": row["ICODE"], "name": row["INAME"] or "", "name_urdu": row["INAME_URDU"]},
                    "unit": unit, "entered_quantity": row["QTY_LARGE_UOM"], "factor": row["QTY_SMALL_UOM"],
                    "quantity": row["QTY"], "rate": row["RATE"], "discount_percent": verified["discount"], "amount": verified["net"]}
        if kind == "sale" and row["VNO_DCDTL"] is not None:
            dispatch = original.execute(
                "SELECT m.DCNO FROM DCDTL d JOIN DCMST m ON m.TRNID=d.TRNID WHERE d.VNO=? AND m.CMP_ID=?",
                (row["VNO_DCDTL"], source_company)).fetchall()
            if len(dispatch) == 1:
                bill_row["dc_number"] = dispatch[0]["DCNO"]
        rows.append(bill_row)
    if len(rows) != len(document["rows"]):
        raise DocumentExportError("The recorded bill line count does not match its verified details.")
    totals = [("خالص رقم", "Line net subtotal (before rounding)", fields.get("Line net subtotal (before rounding)"), False)]
    if kind == "sale":
        totals.append(("رعایت", "Header discount", header["DISCOUNT"], False))
    totals.append(("بریکج", "Breakage", header["BREAKAGE"], False))
    if header["FREIGHT"] is not None:
        totals.append(("کرایہ", "Freight", header["FREIGHT"], False))
    totals.append(("بل رقم", "Recorded party amount", document.get("total_amount"), True))
    return {"kind": kind, "company": company,
            "party": {"code": header["CHCODE"], "name": header["CHDESC"] or header["CUSTOMER"] or "", "name_urdu": header["CHDESC_URDU"]},
            "number": header["TRNNO"], "serial": header["TRNID"], "date": header["TRNDATE"],
            "bill_number": header["BILLNO"], "remarks": header["REMARKS"],
            "status": "Cancelled in original records" if document.get("status") == "cancelled" else "Original recorded document",
            "show_dc": any("dc_number" in row for row in rows),
            "show_discount": any(as_decimal(row["discount_percent"]) for row in rows),
            "lines": rows, "totals": totals,
            "footer": "Original record copy · Exact archive values; — means no recorded value."}


class OriginalDocumentExporter:
    """Print original vouchers through the same verified, authorized register."""

    def __init__(self, path, *, register=None, archive_path=None, security=None):
        from bifma.document_register import DocumentRegister
        if register is not None and Path(register.path).resolve() != Path(path).resolve():
            raise DocumentExportError("The document register belongs to a different working database.")
        self.register = register or DocumentRegister(path, archive_path, security=security)

    def export_html(self, actor, company_id, kind, document_id, destination) -> Path:
        register = self.register
        document = register.get(actor, company_id, kind, document_id, source="original")
        # Company labels also come from the preserved source, not edited masters.
        with native_transaction(register.path) as connection:
            register._authorize(connection, actor, company_id, kind)
            source_company = register.source_company(connection, company_id)
            with session(register.archive_path) as original:
                original.execute("BEGIN")
                row = original.execute("SELECT CMP_ID,CMPNAME,CMPNAME_URDU FROM CONFIG WHERE CMP_ID=?", (source_company,)).fetchone()
                if row is None:
                    raise DocumentExportError("The recorded company is unavailable.")
                company = {"code": row["CMP_ID"], "name": row["CMPNAME"], "name_urdu": row["CMPNAME_URDU"]}
                if kind in {"sale", "breakage_receive"}:
                    document["bill_print"] = _original_bill(original, document, company, source_company)
            register.assert_archive_unchanged()
        content = _render_original(document, company)
        destination = new_destination(destination)
        descriptor, name = tempfile.mkstemp(prefix=".bifma-original-", suffix=".html", dir=destination.parent)
        staged, identity = Path(name), None
        try:
            status = os.fstat(descriptor)
            identity = status.st_dev, status.st_ino
            os.chmod(staged, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                descriptor = None
                stream.write(content)
                stream.flush()
            register.verify_archive()
            with native_transaction(register.path) as connection:
                register._authorize(connection, actor, company_id, kind)
                if register.source_company(connection, company_id) != source_company:
                    raise DocumentExportError("The original company association changed during printing.")
                register.assert_archive_unchanged()
                publish_new_file(staged, destination)
                register.assert_archive_unchanged()
        except BaseException:
            _remove_owned_publication(destination, identity)
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
            staged.unlink(missing_ok=True)
        return destination
