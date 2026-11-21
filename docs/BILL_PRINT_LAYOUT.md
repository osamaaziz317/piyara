# Sale and breakage bill pages — 2026-11-20

`bifma/bill_layout.py` renders the owner's Sale/BRCV reference arrangement:
centered BT, boxed Urdu title, party/code, number/date/reference/remarks,
right-to-left item table, packing, quantity, rate, discount and totals. English
labels and saved English names accompany Urdu for readable fallback. There are
no new mandatory libraries, external fonts, scripts or network assets.

Native bills use the issued company/party/product snapshots. Sale packing is
validated against the saved unit and base quantity by the existing document
renderer. Large-unit rates remain rates per base unit, as in entry/posting.
Gross and line discount display use exact Decimal arithmetic; subtotal and
rounding adjustments come from the saved calculation, and final amount comes
from the posted document. This fixes a prior conditional that omitted the sale
buyer from the printable header. The original full document detail is collapsed
below the page and excluded from printing, so breakage accounting/stock facts
remain inspectable without appearing on the customer bill.

Original bill access still goes through `DocumentRegister`. The exporter then
reads original Urdu names, packing components, and any actual linked DC number
inside its managed authorized archive read. It validates those lines against
the verified register detail. Missing packing is left unknown; no default is
substituted for original values. The register's recorded party amount supplies
the original final amount. The preserved archive and working data are not
updated by rendering. A changed archive or revoked permission prevents file
publication under the existing exporter checks.

The supplied November sale print has an independent line-rounding discrepancy:
its rounded line values sum to 20,733, while the recovered original aggregate
ledger rule gives 20,732 for those inputs. The later invoice is absent from the
September backup. We retain exact line figures and the recorded original party
amount; we do not silently change postings or claim byte-identical original
print calculations. See `VIDEO_WORKFLOW_EVIDENCE.md`.

Bill/print preview uses the same exporter as Save printable copy and opens the
result directly in the system browser. Ctrl+P opens that browser's print/PDF
dialog. `PreviewFiles` stores unique HTML pages privately until logout/clean
shutdown, when it removes them. A saved export remains at the chosen path.
Drafts still require explicit review/posting before a posted bill is issued.

Focused regression tests check saved buyer labels after master renaming,
large-unit packing, exact discounts and totals, HTML escaping, no Store/DC
invention, original read-only labels and missing-value handling, fresh grants,
archive changes, and safe output publication. Browser-rendered synthetic pages
were visually reviewed locally; physical printer qualification remains separate.

A temporary working database prepared from the actual supplied archive verified
both companies and exported three available original Sale/Breakage Receive
bills through the real register/exporter. The archive SHA-256 stayed unchanged
and the temporary working database was removed. A synthetic two-line sale PDF
also retained each line amount and final total exactly once on one page, with
the expandable accounting/detail appendix excluded from printing.
