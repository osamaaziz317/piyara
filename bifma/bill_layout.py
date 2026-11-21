"""Print layouts transcribed from the owner's Sale/BRCV bill photographs.

The caller supplies verified saved facts. This module does no database reads,
settlement inference, rounding or invoice calculation.
"""

from datetime import date
from html import escape


CSS = """
@page { size: A4; margin: 10mm; }
* { box-sizing: border-box; }
body { margin: 0; color: #000; background: #ddd;
       font: 10pt/1.45 Arial, "Noto Naskh Arabic", "FreeSerif", sans-serif; }
main { width: 190mm; min-height: 260mm; padding: 8mm; margin: 14px auto; background: white; }
.bt { text-align: center; font: bold italic 36pt/1 Georgia, serif; letter-spacing: -5px; }
.company { text-align: center; font-size: 10pt; margin: 3px 0; }
.bill-title { border: 1px solid #000; background: #e5e5e5; width: 50%; margin: 7px auto 13px;
              text-align: center; font-size: 20pt; line-height: 1.6; }
.bill-title small { display: block; font: bold 10pt Arial, sans-serif; margin: -2px 0 5px; }
.bill-meta { display: grid; grid-template-columns: 1fr 1fr; gap: 7px 22px; direction: rtl; }
.bill-field { display: flex; gap: 10px; align-items: baseline; border-bottom: 1px solid #aaa; min-height: 25px; }
.bill-field .label { white-space: nowrap; font-weight: bold; }
.bill-field .value { flex: 1; }
.bill-field .english { text-align: right; }
.wide { grid-column: 1 / -1; }
.label small { font: 7pt Arial, sans-serif; display: block; }
.urdu { font-family: "Noto Nastaliq Urdu", "Noto Naskh Arabic", "FreeSerif", "Segoe UI", Arial, sans-serif;
        direction: rtl; unicode-bidi: isolate; }
.english { display: block; font: 7.5pt/1.4 Arial, sans-serif; direction: ltr; unicode-bidi: isolate; }
small[lang="en"] { direction: ltr; unicode-bidi: isolate; }
.code { font: 8pt/1.4 Arial, sans-serif; }
bdi { unicode-bidi: isolate; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; margin-top: 13px; direction: rtl; }
thead { display: table-header-group; }
th, td { border: 1px solid #111; padding: 5px 4px; vertical-align: top; overflow-wrap: anywhere; }
th { background: #e5e5e5; font-size: 11pt; }
th small { display: block; font: 7pt/1.5 Arial, sans-serif; }
.sr { width: 6%; text-align: center; }
.dc { width: 8%; text-align: center; }
.description { width: 33%; text-align: right; }
.packing { width: 12%; text-align: center; }
.qty { width: 8%; }
.rate { width: 11%; }
.percent { width: 8%; }
.amount { width: 15%; }
.number { text-align: right; font-variant-numeric: tabular-nums; }
.packing small { display: block; font-size: 7pt; }
tr { break-inside: avoid; page-break-inside: avoid; }
.totals { width: 56%; margin-right: auto; font-size: 10pt; }
.totals th { width: 65%; background: white; text-align: right; font-size: 11pt; }
.totals .grand th, .totals .grand td { border-top: 2px solid black; font-weight: bold; background: #eee; }
.status { font: bold 9pt Arial, sans-serif; text-align: center; margin: 5px 0; }
.notes { white-space: pre-wrap; }
.record-details { margin-top: 22px; border-top: 1px solid #888; padding-top: 8px; }
.record-details summary { cursor: pointer; font: 9pt Arial, sans-serif; }
.record-details .meta { display: grid; grid-template-columns: 1fr 1fr; }
.record-details .field { padding: 5px; }
.record-details .field-title { display: block; font-weight: bold; }
.record-details h1, .record-details h2 { font-size: 12pt; }
.record-details table { direction: ltr; font-size: 8pt; }
footer { clear: both; border-top: 1px solid black; margin-top: 22px; padding-top: 5px; font: 8pt Arial, sans-serif; }
@media print {
  body { background: white; }
  main { width: auto; min-height: 0; padding: 0; margin: 0; }
  .bill-lines th, .bill-lines td { padding: 3px 4px; }
  .bill-lines .english, .bill-lines .code, .packing small,
  .bill-field .value .english, .company .english, .company .code { display: none; }
  .record-details { display: none; }
  .totals { break-inside: avoid; page-break-inside: avoid; }
}
"""


def text(value, *, numeric=False):
    if value is None:
        value = "—"
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError("Bill text must be a saved string or exact decimal value.")
    return '<bdi dir="' + ("ltr" if numeric else "auto") + '">' + escape(str(value), quote=True) + "</bdi>"


def label(record):
    name, urdu = record.get("name", ""), record.get("name_urdu")
    if urdu:
        result = '<span class="urdu" lang="ur">' + text(urdu) + '</span><span class="english">' + text(name) + "</span>"
    else:
        result = text(name)
    if record.get("code"):
        result += '<span class="code"> ' + text(record["code"], numeric=True) + "</span>"
    return result


def bill_date(value):
    """Match the photographed bill date; full source text stays in its details."""
    if isinstance(value, str):
        try:
            day = date.fromisoformat(value[:10])
        except ValueError:
            return value
        months = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
        return f"{day.day:02d}-{months[day.month - 1]}-{day.year % 100:02d}"
    return value


def field(urdu, english, value, *, wide=False, html=False):
    return ('<div class="bill-field' + (" wide" if wide else "") + '"><span class="label urdu" lang="ur">'
            + urdu + '<small lang="en">' + english + '</small></span><span class="value">'
            + (value if html else text(value)) + "</span></div>")


def render_bill(model, *, details_html=""):
    """Return one standalone RTL bill; details are expandable, off the printout."""
    sale = model["kind"] == "sale"
    heading = "سیل بل" if sale else "وصولی بریکج واپسی"
    english = "Sale Voucher" if sale else "Breakage receive"
    parts = ['<!doctype html><html lang="ur"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             '<meta http-equiv="Content-Security-Policy" content="default-src &#x27;none&#x27;; style-src &#x27;unsafe-inline&#x27;; base-uri &#x27;none&#x27;; form-action &#x27;none&#x27;">',
             '<title>' + english + ' · ' + escape(str(model["number"]), quote=True) + '</title><style>',
             CSS, '</style></head><body><main><header><div class="bt">BT</div>',
             '<div class="company">' + label(model["company"]) + '</div>',
             '<div class="bill-title urdu" lang="ur">' + heading + '<small lang="en">' + english + '</small></div>']
    if model.get("status"):
        parts.append('<div class="status">' + text(model["status"]) + '</div>')
    parts.append('<div class="bill-meta">')
    parts.append(field("بل نمبر" if sale else "رسید نمبر", "Sale #" if sale else "Receipt #", model["number"]))
    parts.append(field("تاریخ", "Date", text(bill_date(model["date"]), numeric=True), html=True))
    parts.append(field("نام", "Buyer" if sale else "Party", label(model["party"]), wide=True, html=True))
    if model.get("serial") is not None:
        parts.append(field("سیریل نمبر", "SR", model["serial"]))
    if model.get("bill_number"):
        parts.append(field("حوالہ بل نمبر", "Bill number", model["bill_number"]))
    if model.get("remarks"):
        parts.append(field("تفصیل", "Remarks", '<span class="notes">' + text(model["remarks"]) + '</span>', wide=True, html=True))
    parts.append('</div></header><table class="bill-lines"><thead><tr>')
    headings = [("نمبر", "Sr #", "sr")]
    if model.get("show_dc"):
        headings.append(("ڈی سی", "DC #", "dc"))
    headings.extend([("نام اشیاء", "Item description", "description"), ("پیکنگ", "Packing", "packing"),
                     ("تعداد", "Qty", "qty number"), ("ریٹ", "Rate", "rate number")])
    if sale or model.get("show_discount"):
        headings.append(("رعایت", "Dis %", "percent number"))
    headings.append(("رقم", "Value", "amount number"))
    parts.extend('<th scope="col" class="' + cls + '"><span class="urdu" lang="ur">' + urdu + '</span><small lang="en">' + en + '</small></th>' for urdu, en, cls in headings)
    parts.append('</tr></thead><tbody>')
    for row in model["lines"]:
        cells = [text(row["number"], numeric=True)]
        if model.get("show_dc"):
            cells.append(text(row.get("dc_number"), numeric=True))
        cells.append(label(row["product"]))
        packing = ('<bdi dir="ltr">' + text(row.get("entered_quantity"), numeric=True) + " × " + text(row.get("factor"), numeric=True) + '</bdi>'
                   if row.get("entered_quantity") is not None and row.get("factor") is not None else text(None))
        if row.get("unit"):
            packing += '<small>' + label(row["unit"]) + '</small>'
        cells.extend([packing, text(row["quantity"], numeric=True), text(row["rate"], numeric=True)])
        if sale or model.get("show_discount"):
            cells.append(text(row["discount_percent"], numeric=True))
        cells.append(text(row["amount"], numeric=True))
        parts.append('<tr>' + ''.join('<td class="' + headings[index][2] + '">' + value + '</td>' for index, value in enumerate(cells)) + '</tr>')
    parts.append('</tbody></table><table class="totals"><tbody>')
    for urdu, en, value, grand in model["totals"]:
        parts.append('<tr' + (' class="grand"' if grand else '') + '><th scope="row" class="urdu">' + urdu
                     + '<span class="english">' + en + '</span></th><td class="number">' + text(value, numeric=True) + '</td></tr>')
    parts.append('</tbody></table><footer>' + text(model["footer"]) + '</footer>')
    if details_html:
        parts.append('<details class="record-details"><summary>Saved document details</summary>' + details_html + '</details>')
    parts.append('</main></body></html>\n')
    return ''.join(parts)
