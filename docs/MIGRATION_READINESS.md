# Historical-to-native migration readiness

**2026-10-03 update:** automatic operational baseline integration is now
implemented. See [BASELINE_INTEGRATION.md](BASELINE_INTEGRATION.md) for the
current import, cutoff, preserved anomalies, return bridge and valuation rules.
This document retains the earlier investigation and source-data findings;
statements below that no importer exists describe that earlier state.

Reviewed 2026-10-02 against the verified archive and native schema version 3.
**Master mapping is largely available; an operational cutover is not ready.**
No migration was performed. All archive inspection used SQLite `mode=ro`;
the archive SHA-256 remained
`d5883f726438192e9e5331cb1b70ae2d4b081d8dcf72a8b2770cb692a92d83be`.
No native database was created or changed.

The [Oracle reconciliation](ORACLE_RECONCILIATION.md) verifies 30 tables and
93,110 rows and the reconstructed historical stock/ledger results. That proves
recovery fidelity, not that source balances satisfy new posting rules. Counts
below are actual recovered rows, not the dump's stale optimizer statistics.
Closing-balance checks use **all available records**, without choosing a cutover
date. They must be repeated at the owner's accepted cutoff.

## Supported master mappings

Native IDs are UUIDs. Preserve source codes as text and retain a durable mapping
of `(source hash, table, source key) → native UUID`; do not reuse numeric source
IDs as native IDs. Master provenance/import tracking is not implemented yet.

| Source → native | Rows | Directly supported mapping / remaining boundary |
| --- | ---: | --- |
| `CONFIG` → companies | 2 | `CMP_ID` as preserved code; names, bilingual addresses, contacts/tax identifiers and `CHCODE_CASH`. `SDATE`/`EDATE` supply candidate fiscal dates. Confirm the new operating period and active state. |
| `CITY` → cities | 70 | Preserved ID/code and bilingual names. |
| `CHART` → accounts | 703 | `CHCODE`, bilingual descriptions, city, address/contact/tax fields. Explicit hierarchy, kind, classification, active/postable status still need a reviewed mapping. |
| `UOM` → units | 7 | Preserved ID/code and bilingual names; future active status is a setup decision. |
| Group master/detail → product groups | 1 + 43 | Both name sets and explicit `MAIN_CODE` parent relationship. One raw code overlaps between the two source tables, so a reversible namespace is required in the single native code column. |
| `PUR_ITEM` → products | 6,207 | Code/names, subgroup, small/large units, factor; `PUR_CODE` → purchase posting account and `EXP_CODE` → sale posting account. These roles are evidenced by the original views. There is no source inventory-account mapping. |
| `STORE_LOC` → stores / company memberships | 2 | Names/code and 2 declared company/store memberships. Historical usage requires separate review below. |

All checked master references resolve: city, group parent, product group/units,
product sale/purchase accounts, company cash accounts and store companies.
The recovered constraint audit reports zero key/foreign-key violations.
The 702 account codes beginning with zero must remain text.

Mapped text fits current native limits and has no rejected control characters.
Native trim/NFC normalization would change 20 account rows, 1 city row and
68 product rows; no normalized code duplicates occur within a source table.
Keep original labels in the immutable archive and record accepted normalization
in the migration manifest rather than claiming byte-identical master copies.
See [master validation](MASTER_SERVICES.md) for the actual service contract.

## Concrete gaps

**Accounts and rates.** All 703 `ACCTYPE` values are NULL. `CHLEVEL` has three
levels, but there is no explicit parent-account field; 32 `ALLOWED` values are
NULL. Neither a parent tree nor native asset/liability/equity/income/expense
classification can be inferred safely from codes or names. `CONFIG` identifies
the cash-account reference, but it does not classify the rest of the chart.
Native classification is optional and can remain unset; postability and required
operational account roles still need an explicit mapping.
All 6,207 `LPUR_RATE` values and 6,087 `SALE_RATE` values are NULL; only 120 sale
rates are present. All large sale rates are NULL. Present master rates and all
positive conversion factors fit native precision and unit rules. Native rates
are non-null with zero defaults, so an importer must not silently turn unknown
rates into factual zero prices. An approved unknown-rate handling rule or schema
support is needed. Last purchase price is also not an inventory-cost policy.

**Units and stores.** There are 102 rows whose stored small unit differs from
the current product master: 93 sales-family details, 5 breakage-receive details
and 4 opening-stock rows. They affect 28 products; 26 products have more than one
historical small unit. The source views use current item units, so even matching
Oracle output does not establish a physically consistent native base unit.
Choose authoritative base units/conversions before native posting freezes them.
Do not recompute stored quantities from similarly named pack/loose fields without
original entry semantics. Separately, 96 sales-family details use a store whose
declared company differs from the header company. There is one extra observed
membership beyond the declared set; their union has 3 pairs. Decide whether that
membership is legitimate for future work; do not transfer source rows between
companies or silently grant future store access.

**Opening stock.** All-time reconstructed balances are:

| Pool | Company/store/product groups | Positive quantity | Zero quantity | Negative quantity |
| --- | ---: | ---: | ---: | ---: |
| Regular | 5,438 | 903 | 220 | 4,315 |
| Breakage | 1,456 | 1,456 | 0 | 0 |

Both companies have negative regular-stock groups. Native opening stock accepts
positive quantities only and prevents later negative availability. No quantity
exceeds its 18-integer/6-fraction limit, but 322 regular-stock value groups exceed
2 fractional places, including 296 of the 903 positive-quantity groups. There
are 313 negative regular value groups; 8 positive breakage groups have zero
historical value. Also, 269 raw opening rates exceed the native rate precision.
These are source observations, not corrections to apply automatically.

Regular-stock valuation uses `GET_PUR_RATE`, including its highest-ID selection
rule, and is not an accepted inventory-cost/COGS policy. Native openings require
explicit pool, inventory account, value and counterpart account. Approve
cutover quantities against stock records/counts, value/rounding policy and
account mappings; retain the historical calculations unchanged. Do not drop
negative groups, round values or substitute zero to make validation pass.

**Opening accounts.** Both all-time historical trial balances balance exactly.
They contain 24 and 579 account groups respectively, with 24 and 505 nonzero
balances; there are no NULL/missing chart groups or balances beyond native money
precision. This is useful evidence for candidate openings, not a selected cutoff.
Stock-opening documents themselves post inventory and counterpart journals.
The accepted account opening must reconcile **together with** those journals;
loading the full trial balance and adding valued stock openings independently
would change the accepted balances. A reviewed decomposition is required.

**Dates, document identity and old obligations.** Both configured fiscal
intervals are ordered, midnight-based and include the inspection date. All
inspected business dates are midnight-based, so date-only mapping loses no time
for this dataset. However, 1 sales-family header, 1 journal header and all 1,436
opening-stock rows lie outside their company's configured interval. There is
one duplicate `(company, type, document number)` group in `SMST`, covering two
headers. The original inspection used schema 3, whose document numbering would
reject it. Schema 4 now gives cash/account invoices distinct kinds and number
namespaces, matching this observed cash/account overlap. It does not migrate or
reclassify existing records. Preserve source identity separately and choose
future numbering rather than renumbering archive records.

The archive has 3,412 sales, 1 purchase return, 436 breakage receives, 1 breakage
dispatch and 1,771 journal headers; purchase/sales-return and challan tables are
empty. All recovered invoice-return/challan detail links are NULL. There are
183 nonpositive trade/breakage detail quantities and 71 journal details with
both amounts zero/NULL, which are not valid new native posting inputs. Source
breakage includes 47 headers with nonzero freight; 3 sales headers contain
received amounts. These facts further rule out replaying historical documents
through corrected native entry services.

Aggregate openings do not create native original invoice lines. Native linked
returns therefore cannot yet reference old invoices, and invoice allocation /
outstanding-invoice aging is not implemented. Any required old-invoice return or
settlement workflow needs an explicit bridge and accepted opening obligations.
Do not invent links from equal parties, dates or amounts. Original audit times
and actors remain source evidence, not authenticated native posting events.

## Minimum decisions and subsequent work

1. Approve the cutover date/time, companies, fiscal period and future numbering;
   decide whether history stays in the existing read-only archive with separately
   posted native openings. Preserve history under the owner's already accepted
   rule: **correct new transactions; preserve historical values**.
2. Approve the account hierarchy/postability and needed cash/bank/customer/
   supplier roles, inventory/counterpart mappings, unknown-rate handling, product
   base-unit corrections and future company/store memberships.
3. Approve physical opening quantities, valuation/rounding and a jointly
   reconciled stock/account opening schedule. Negative source stock cannot be
   accepted by the current native opening workflow without a distinct policy
   and implementation decision.
4. Confirm which old-invoice returns, unpaid invoices, settlements and other
   outstanding obligations must remain actionable after cutover; implement the
   required bridge before relying on native linked-return entry.

Then build an explicit, repeatable migration into a **new** native file, with
source-to-UUID manifest, normalization/exception report, authorized audit and
rollback/non-overwrite guarantees. Reconcile masters, accepted opening quantities
and the combined opening trial balance per company at the chosen cutoff, and
perform a restore drill and operator acceptance. No such importer currently
exists. Native users require fresh secure setup and reviewed grants; the 7 old
user records, 134 menu-rights rows and 4 company-access rows are review evidence,
not credentials or permissions to copy automatically.

Sources: [native schema](../bifma/native_schema.py),
[master service](../bifma/masters.py),
[transaction rules](TRANSACTION_SERVICES.md),
[verified historical reports](oracle_report_parity.json). Only counts,
reference-validity results, normalization/precision checks and historical report
aggregate diagnostics were recorded. This document contains no record names, account codes,
financial amounts or credentials.
