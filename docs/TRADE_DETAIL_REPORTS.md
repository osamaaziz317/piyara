# Sales and purchase detail reports

**Sales / returns detail** and **Purchases / returns detail** combine original
archive records with new posted records in the same filters, pages, totals and
printable output. Drafts do not contribute. Original invoices and returns remain
read-only: they are not replayed as new documents or added to a closing snapshot.
The recovered menu identifies these reports, but the original templates and
filters are unavailable. Exact original layout parity remains unverified.

Trade reports initially leave both dates blank so Run report includes original
and new activity. Switching between untouched report choices adjusts the automatic
start date; financial/item reports keep their cutoff-plus-one-day default.
Typing or deliberately clearing a start date preserves that choice across report
changes, and an entered end date is never changed automatically.

Select dates and invoices, returns, or both. Optional filters select a party,
item, store or literal document-number text. Selectors include inactive masters
with matching original or new posted history. Dates are inclusive. New reversals
appear on their own business dates; their reversed originals remain visible.

**Entry type** selects Account and cash, Account entries, or Cash entries. The
service's `payment_mode` defaults to `account`; `cash` and `all` include the
explicit cash kinds. Original `S_TYPE`/`P_TYPE = C` records use cash mode; other
source values use the same account partition as the saved-document browser.
Unclassified original values have review notes. A new generic account invoice
keeps its account-entry classification even if its posting account was cash.

New rows use immutable document labels. Original labels come from the sealed
archive, and the unit comes from each detail's recorded `UOM_ID_SMALL`. Missing
or conflicting units remain explicit. Renaming a current master changes selector
labels, not historical report rows. Missing/inconsistent new-document snapshots
cause an error. Original records carry source table/document/line identities and
archive provenance, without fabricated native document IDs.

The screen pages 100 lines with full-result totals. Original rows precede the
cutoff and new rows follow it; overlapping new activity is rejected. Originals
use stable business-date/source-table/document-ID/line order. New rows retain
business-date/posting-order/posting-time/document/line ordering. Pagination can
span the two sources without losing or repeating a row.

Invoice quantities and amounts have a positive activity sign; returns have a
negative sign. Reversals negate their original activity. These signs describe
trade, not stock direction. Negative original values and out-of-range discounts
remain visible with review notes. They are not repaired during reporting.

**Line amounts exclude header discounts, breakage, freight and document
rounding.** Without item/store filters, document totals count each document once.
Original header amounts follow the preserved party-posting rules also used by
the saved-document browser, including original rounding and header adjustments.
They are recorded party amounts, not inferred invoice totals. An absent or
ambiguous original party amount makes that total contribution Unknown. Original
headers without lines remain counted and are identified in the summary.
Item/store filters omit document totals because there is no approved allocation
of header adjustments or rounding to individual lines.

Original NULL quantity/rate values remain unknown; their line amount and affected
aggregate are Unknown. A separately established original party amount is retained
independently. Original NULL discount percentage uses the source's zero-discount
rule; known zero rates/quantities remain numeric zero. The original ledger may
have applied NVL where inputs were missing, so line and recorded-party totals
can intentionally differ. No unknown amount is silently added as zero.

**Quantities by item** groups separately by item and recorded unit. Unknown unit
identities remain separate from known units. Different items/units are not added
together. Grouped labels use the first historical/saved label in the period;
each detail row retains its own labels. Missing references and source anomalies
are never silently omitted from an unfiltered result.

**Save full report** exports every matching line, filters, provenance, review
notes, amounts and grouped quantities to a new HTML file. One native snapshot
and a verified read-only original snapshot cover the query. The combined
100,000-line limit raises an error instead of truncating output; existing files
are not overwritten.

Access follows every selected activity/mode grant: sales, counter sales,
sales returns, purchases and purchase returns as applicable. No additional ledger,
stock or master-read grant is required. Selectors enforce the same rules. Archive
lineage is verified before the managed native read, and its signature is checked
after the original read. Export verifies the archive again after rendering and
rechecks the source signature and current grants before publication.

`UnifiedReports.trade_detail` and `trade_selectors` implement the combined paths;
`NativeReports` retains its native-only API. `export_snapshot` accepts
`sales_detail` and `purchase_detail`, rendered by the shared `ReportExporter`.
No schema or historical-data change is involved. All matching original headers
and details are read independently of automatic-return eligibility. Breakage,
delivery documents and vouchers are outside this report's scope. Tax allocation,
receivables aging, stock valuation and profit are not calculated here.

`tests/test_unified_trade_reports.py` uses disposable archives/working files to
cover both families and cash modes, original returns, new reversals, combined
paging/totals, historical-only filters, names after renaming, exact decimals,
missing/negative/conflicting source values, empty original headers, full output,
row caps, live grants and source changes before reading or publication.
`tests/test_trade_report_ui.py` verifies a normal menu/Run report shows the original
invoice and that automatic versus explicitly entered/cleared dates survive report
changes, without changing financial/item-ledger defaults or the original archive.
