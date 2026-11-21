# Working from the original records

Updated 2026-10-03. This is the current operational integration; earlier
readiness reports describe the investigation before it was implemented.

Run `python3 BIFMA_TRADERS_V3.py`. The application opens one module menu with
the original companies, items, accounts, stock and account balances. Each
sales, purchase, return, voucher and opening section lists original and new
documents together, with one New document action. Original detail dialogs retain
the saved values. Separate Workspace/History navigation is no longer used.

## Automatic preparation and persistence

`bifma.baseline.ensure_baseline` reads `data/legacy_recovered.sqlite3` without
writing to it. It initializes schema 6 in a private temporary file, imports
masters and independent closing balances, reconciles their exact contents,
then publishes `data/bifma.sqlite3` without overwriting an existing file.
Interruption before publication cannot expose a partly prepared live database.
Concurrent starters can reuse only a complete file matching the same source.

Subsequent launches reopen that working file; they do not reimport balances or
replace new transactions. A SHA-256 of the archive and sealed source-to-UUID
mappings identify its origin. Original master codes remain text, including
leading zeros. Working labels use the existing trim/NFC normalization contract;
normalization is recorded and the original source text remains untouched.
Sealed balance, rate, invoice-reference and mapping rows have an immutable
content digest and SQL guards. Ordinary operational master edits remain audited.

The owner creates a personal administrator login once. No original passwords,
sessions or guessed permissions are imported. Native backup/restore includes
all sealed balances, source references, new documents and valuation snapshots.
Keep the original archive with the application for original document details
and stock activity before the cutoff. `DocumentRegister` verifies its association
with the working file and requires current company/module read permissions;
search and date filters apply before the two document streams are paginated.

## Closing date and quantities

The closing date is the latest source business date. In the supplied archive it
is **2026-09-27**. Account balances and both regular/breakage quantities and
historical stock values use the original verified ledger/stock calculations
through that date. New document dates must be later and inside the company's
configured fiscal interval. Services and schema guards enforce the boundary.
Account and item-ledger reports use the carried closing snapshot and new
postings; they explicitly identify the original closing row and do not invent
earlier turnover. Earlier documents open in their own entry section. Stock
quantities and Stock valuation accept earlier and cross-cutoff date ranges,
reading original stock movements followed by new movements without adding the
carried snapshot a second time. Their screens and full exports use the same
dates, store and pool filters.

Stock and account closings are imported independently. No opening-stock
inventory journal is added, so the original account balances are not counted
twice. Do not re-enter these closings as native opening documents.

Negative original quantities are preserved exactly. New incoming goods can
reduce a shortage; a new outward movement must leave enough stock at its date
and every later posted event. Company, store and regular/breakage pools remain
separate. Posting locks prevent concurrent sales from using the same stock.

Current original product units retain the interpretation used by the original
stock view. Stored historical unit conflicts are listed; no conversion is
invented. Imported stock freezes the product's base unit just as native posted
history does. Only source-declared store memberships are active; additionally
observed historical company/store pairs are retained inactive for review.

## Original invoice returns and numbering

Original invoices and lines are stored as sealed references with raw source
JSON, separate identities, labels and the exact original party-side total.
They are not inserted as new posted invoices, do not generate journals and do
not move the carried stock a second time.

The return picker searches eligible original history and new posted invoices.
`list_return_sources` / `get_return_source` require the current return-create
and original-read grants. Service posting validates company, party, item, store,
rate, units, original quantity and original amount under the write lock.
Physical native lines distinguish their native source from
`historical_source_line_id`.

The owner selected **staff-entered** discount, breakage and freight amounts on
new partial returns. No proportional adjustment is guessed. Aggregate refunded
amount and quantities, including verified historical linked returns, cannot
exceed the original invoice. Reversing a new return releases its capacity and
reverses its exact accounting and quantity/value effects.

Invalid/nonpositive or unsupported original line values, conflicting units,
ambiguous party totals, unsupported delivery/breakage links and uncertain
unlinked historical returns make automatic return capacity unavailable.
Reasons remain visible; source facts are not changed to make a return pass.
Historical cash invoices retain the cash account evidenced by their original
ledger, even when that differs from the company's current cash configuration.

Suggested document numbers advance from the greatest numeric original/native
number in the company and document kind, including saved drafts. Saving still
checks uniqueness atomically; suggestions are not reservations.

## Prices and valuation

Missing source sale/purchase master prices are marked unknown. The entry screen
leaves them blank and requires an explicit rate. An intentionally entered zero
is distinct from an absent price. Editing unrelated master fields does not
turn an unknown price into a known zero.

The owner selected the original `GET_PUR_RATE` method. Original cost candidates
retain its highest eligible numeric-ID choice, primary opening/purchase source,
sales-return fallback and provenance. New candidates use monotonic posting order
and line number, restricted by company, item and business date, across stores.
Discounts and invoice header charges do not change this raw inventory rate.

Each new stock movement receives an exact, immutable valuation snapshot inside
its posting transaction. Later purchases never rewrite saved values. A dated
reversal stops its original from supplying rates from that date onward, while
its movement value reverses the saved original exactly. Breakage uses the
original detail-rate method with the corrected outward dispatch direction.
Explicit opening values remain exact even if value/quantity has no finite
decimal representation. Missing or invalid candidate costs remain unknown.

Stock valuation adds these signed new movement values to the exact historical
value. It does **not** multiply all closing quantity by the latest price.
Unknown values prevent a complete value total instead of contributing false
zeros. No extra COGS journal is silently added to existing double-entry postings.

Profit and loss uses explicitly reviewed account classifications and discloses
its inventory adjustment method. Unclassified accounts, incompatible purchase
account treatment and missing costs produce **Review required**, not an asserted
profit. Original classifications are absent; they are not guessed from names.
See the report's explanations before interpreting a total as business profit.

## Source review still required

The supplied archive carries 603 account closings and 6,894 stock closings
across two companies. Both carried trial balances sum to zero. It includes
4,315 negative stock groups, 102 rows with unit conflicts affecting 28 products,
32 accounts without an original posting permission and one additional observed
store membership. Missing prices/costs remain reported as such. These are
business-data issues, not repaired historical transactions.

Original form code and print templates are unavailable. The owner requires the
original feature scope, so field names alone do not authorize new labour/tax,
delivery, invoice-allocation or unrelated workflows. Exact original UI/print
parity and business acceptance remain unverified. Passing application tests
does not establish production readiness. Packaging remains owner-managed.

## Earlier baseline-integration verification — 2026-10-03

The full application test suite passed 575 tests with no skips, including the
combined-window GUI flows on a private virtual display. The public launcher's
read-only smoke check covered 2 companies, 30 screens, 11 documents and 4 stock
reports. Fifteen source-manifest checks passed; no platform packages were built.

A separate temporary working database imported from the actual archive passed
new purchase, sale, cash receipt, cash payment, sale reversal and historical
sale-return operations. Independent checks reconciled account deltas, balanced
journals and stock quantity/value deltas, then verified three full HTML exports,
restart without duplicate import and backup/restore with old-session revocation.
Original source, V2 database, export and archive hashes remained unchanged.
The temporary business data was removed; tests did not populate the default
working database. These checks verify the implemented contracts, not unavailable
original form/print parity or business acceptance of source anomalies.

After unifying the module screens, an additional private temporary import
reconciled both companies' document counts with the source tables, opened 14
original module details and listed a new posted cash receipt alongside original
receipts. Search/date filters, restart, earlier/cutoff stock compared with the
source engine and a full earlier-stock HTML export passed. All four original
file hashes stayed unchanged and the temporary working data was removed.
