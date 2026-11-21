# Posted native document output

`bifma.document_export.DocumentExporter` creates a standalone, UTF-8 HTML copy
of one posted native document. Open the resulting file in a local browser to
print or save it as PDF. The file includes A4 print CSS and repeats table headings
across pages. Export itself does not launch a browser, send a print job or contact
any network service.

```python
from bifma.document_export import DocumentExporter

exporter = DocumentExporter(native_database_path, security=security_service)
output_path = exporter.export_html(
    actor, company_id, posted_document_id, "/chosen/directory/invoice-001.html"
)
```

The returned value is a `pathlib.Path`. The destination directory must exist and
the filename must be new. Existing files, directories and final symlinks are
rejected. A temporary file is created in the same directory with mode `0600`,
written completely and synced before atomic publication using the common
`database.publish_new_file` helper. A concurrent writer cannot be overwritten.
Failed preparation/publication cleans up the temporary file. If a directory sync
fails after linking the complete destination, cleanup removes that destination
only if its device/inode still matches this export; a competing replacement is
preserved. Filesystems must support same-directory hard links; permission
enforcement depends on the host filesystem. The export leaves business records
unchanged; its live authorization check may refresh the session heartbeat.

## What the copy contains

Every call reads through `TransactionService.get`, which checks the current
session, company grant and document module's `read` permission in the same
database transaction as the document snapshot. Reversals require the original
document module's read permission. The exporter does not accept a previously
cached document in place of that authorization. Drafts and posted documents
without verified saved metadata are rejected before any output file is created.

Company, party/posting accounts, product, store, base/large unit labels and unit
conversion factors come from the saved document snapshot. Subsequent master
renames do not rewrite an issued copy. English and Urdu names are included where
saved; user text is HTML escaped and enclosed in directional isolation. Urdu
shaping uses fonts installed on the printing computer; no fonts or other assets
are downloaded. All styles are embedded, with no scripts, external assets or
network links. A restrictive content security policy is included.

New document snapshots also retain configured company/account addresses, contact
fields and tax registration identifiers. They print as escaped text, including
leading zeros; email/website fields are not active links. Existing documents
without those saved fields remain unchanged and do not borrow later master
details. Registration identifiers do not imply tax has been calculated.

Amounts and quantities retain their exact saved decimal strings. No binary
floating-point conversion, rounding recalculation or currency symbol is added.
Trade copies include percentage discounts, raw line subtotal, subtotal rounding,
header discount/breakage/freight, total before final rounding, final adjustment
and posted document total. Zero-value goods still show their item quantities and
stock effect; an empty journal is described as having no accounting entries.
Explicit cash trades show their saved cash account, full cash amount and optional
receipt contacts. Cash returns/reversals preserve their original binding and
references. These facts are validated from immutable snapshots; see
[cash trade output](CASH_TRADES.md).
Breakage receive/dispatch copies show their own document titles and breakage
stock directions. Receive includes its supported header breakage deduction;
unsupported breakage freight/header discounts do not appear as editable charges.

Vouchers show every posted debit/credit and its saved account label and narration.
Opening stock shows the supplied opening value and regular/breakage pool. Other
stock sections show quantity and direction; unverified inventory values or COGS
are not presented. Paid, due, tax and settlement claims are not inferred.

Reversal copies reference the original document number/date and state the saved
reason. Their item table is explicitly labeled as original items/line amounts;
the posted accounting and stock sections show the actual reversed effects.
Original document copies show a subsequently posted reversal's number/date.
These are document references, not links to nonexistent exported files.

## Qualification limits

This is a **native print layout**, not a reproduction of an original report
template. The original software's forms and print templates remain unavailable.
Unit tests cover authorization, frozen labels, exact numbers, bilingual text,
escaping, vouchers/reversals, zero-value goods and nonoverwriting publication.
They do not establish original print parity, printer-specific pagination or
Windows/browser/font qualification. Historical archive documents and report
exports are outside this service's scope. Production acceptance remains open.

Run the focused checks with:

```sh
python3 -m unittest discover -s tests -p 'test_document_export.py'
```
