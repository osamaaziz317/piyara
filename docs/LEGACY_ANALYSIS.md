# Legacy source evidence and fidelity requirements

Reviewed 2026-10-02. This document records what the supplied backup proves. It is
not a statement that the replacement application is complete or production ready.

## The supplied file

`BT-270926.EXE` is 7,299,072 bytes. Its first bytes identify an Oracle original
export, `EXPORT:V10.02.01`; it has neither an `MZ` executable header nor a Windows
application interface to run. The filename extension does not describe its
contents. Its SHA-256 is:

```text
d2cd806aa644320cf6d2eaa6192e7d6c840adcde8226f62b5ad1422980cc7e9e
```

The export supplies database schema, database-resident source, and data payloads.
It does not supply the original application forms, report layouts, menu code,
keyboard behavior, deployment installer, or screen screenshots. A functioning
replacement can be built from this evidence, but exact application parity needs
those missing reference materials or comparison with a working installation.

The original file and existing `bifma_traders_v2.db` were not changed by schema
inspection. Schema recovery does not open the SQLite application database.

## Recovered artifacts

- [`../tools/inspect_legacy.py`](../tools/inspect_legacy.py) is a standard-library,
  read-only extractor for the observed export layout. It does not execute SQL.
- [`legacy_schema.json`](legacy_schema.json) records source provenance, byte
  offsets, table columns, defaults, keys, references, view SQL, and function SQL.
- [`legacy_schema.sql`](legacy_schema.sql) contains the recovered **Oracle** DDL
  and program source as a reference, with export framing removed. It is not a
  SQLite migration and must not be applied to the application database.
- [`../tools/test_inspect_legacy.py`](../tools/test_inspect_legacy.py) checks export
  framing, truncation rejection, exclusion of row payloads, known relationships,
  preserved formulas, reproducibility, and unchanged source checksum.
- [`LEGACY_MENUS.md`](LEGACY_MENUS.md) and
  [`legacy_menus.json`](legacy_menus.json) separately recover the 46 functional
  menu definitions and 12 entry types, using only `UM_MENU` and `ENTRY_TYPE` rows.
  They add confirmed feature labels and the menu hierarchy without extracting
  users, company/contact details, account records or credential values.

Reproduce or verify from the project directory:

```sh
python3 tools/inspect_legacy.py BT-270926.EXE --output-dir docs
python3 tools/inspect_legacy.py BT-270926.EXE --output-dir docs --check
python3 -m unittest tools.test_inspect_legacy -v
```

The JSON format is version 1. `tables` is an ordered list; each table has `name`,
`columns` (`name`, `oracle_type`, `nullable`, optional `default`), `primary_key`,
`unique_constraints`, and `foreign_keys`. Each foreign key includes `columns`,
`references_table`, and `references_columns`. Top-level `constraints` additionally
retain exact SQL and offsets. `views` and `functions` preserve recovered source.
No business rows, password values, integration credentials, or optimizer
statistics are written to these artifacts. Schema names such as password fields
remain, because the schema itself must be documented.

| Metadata | Recovered count |
| --- | ---: |
| Tables | 30 |
| Columns | 302 |
| Primary keys | 27 |
| Unique constraints | 23 |
| Foreign keys | 79 |
| Column defaults | 25 |
| Views | 6 |
| Stored functions | 24 |
| Standalone `CREATE INDEX` records | 0 |
| Standalone `CREATE SEQUENCE` records | 0 |

Primary and unique keys retain their `USING INDEX` clauses; zero standalone
index statements does not mean the database had no indexes. No trigger,
procedure, or package creation records were found in this supplied export. This
does not prove that no such objects existed elsewhere in the original system.

The extractor validates references against recovered column names and rejects
unrecognized framing or column types. It is deliberately scoped to the supplied
original-export format, not advertised as a general Oracle backup reader. Views
use length-prefixed source chunks. Functions use `CREATE FORMAT71 FUNCTION`, a
`FD FF` marker, then little-endian 16-bit source lengths and a zero-length
terminator. Default clauses also use a length-prefixed payload; one default
spans lines, so plain `strings` output is insufficient for faithful extraction.

## Functional fidelity matrix

“Confirmed” means the named schema or source exists. It does not prove how the
original application presented or validated the feature. Remaining evidence is
part of the production/parity requirement, not a reason to silently omit a
workflow.

| Capability | Confirmed database evidence | Remaining application evidence or implementation requirement |
| --- | --- | --- |
| Product masters and categories | `PUR_ITEM`, `PUR_ITEM_GROUP_MST`, `PUR_ITEM_GROUP_DTL`; item codes, English/Urdu names, rates/account mappings; Main Item Groups, Item Sub Groups and Item Definition menus | Original forms, search behavior, code generation, edit rules, and category workflow |
| Two units per product | `UOM`; product large/small unit IDs and `QTY_SMALL_UOM`; transaction large/small quantities and stored `QTY`; Uom Coding menu declared under reports | Exact conversion, fractional quantity policy, and which unit transaction `RATE` represents; no conversion function was recovered; menu classification is inconsistent with its label |
| Chart of accounts and cities | `CHART`, `CITY`; account type/level, city/contact fields and discount; menus explicitly name chart levels 1, 2 and 3, plus City Definition | Account hierarchy/code interpretation, account type meanings, opening balance and edit rules |
| Company/fiscal configuration | `CONFIG` company IDs, dates, cash account; `CMP_LOGIN_ALW`; transaction company IDs | Company switching, period closing/locking, date validation, store ownership restrictions |
| Stores and opening stock | `STORE_LOC`, `OPEN_STOCK`; company/store/product/date/quantity/rate | Opening stock editing, stock-taking policy, allowed negative stock, transfers; no dedicated transfer table was recovered |
| Purchases | `PMST`/`PDTL`; type 7 is named “Purchase Invoice” in view source | Original entry form, default rates, tax behavior, document lifecycle, purchase payment handling |
| Sales | `SMST`/`SDTL`; type 8 is Sale Voucher in `ENTRY_TYPE`, “Sale Invoice” in SQL narration; distinct Sale Credit and Counter Sale menu modules; received amount exists | Original invoice screens, validation, immediate-receipt posting, reprints, cancellation, editing and stock availability rules |
| Standard purchase returns | `SMST`/`SDTL` type 9 is named “Purchase Return” by recovered views and has outward stock direction | Reference invoice workflow, rate policy, return limits, settlement/reversal rules |
| Standard sales returns | `PMST`/`PDTL` type 10 is named “Sale Return” and has inward stock direction | Reference invoice workflow, return limits and settlement/reversal rules |
| Breakage receive/dispatch | `ENTRY_TYPE` confirms type 11 Breakage Receive Voucher (`BRR`) and type 12 Breakage Dispatch Voucher (`BRD`); menus expose `BRCV` and `BDISP`; `BR`/`BD` tables and view narration exist | Direct form-to-table mapping remains an inference; differences from standard returns, stock direction and source posting anomalies need reconciliation |
| Delivery challans and conversion | `DCMST`/`DCDTL`; `SDTL.VNO_DCDTL` FK; `DCTOSALE_BAL`; no matching menu among recovered definitions | Whether exposed indirectly; dispatch/sale conversion rules, partial conversion, cancellations, and source stock-view anomaly |
| Journal vouchers, cash/bank receipts/payments, opening balances | `JMST`/`JDTL`; types 1–6 have confirmed descriptions; menus expose JV, CPV, CRV and Opening Balance; cash counterparts for types 2/3 in `LEDGER` | Bank types 4/5 lack explicit menus; validation, balancing, opening workflow, and whether sale received amounts generate journal rows |
| General ledger and balances | `LEDGER` combines active journals plus purchase/sale views | Opening balances, period filters, account filters, trial balance and reconciliation of inconsistent source postings |
| Stock quantities and values | `STOCK`, `STOCKB`, `GET_PUR_RATE` | Identify which view each original report uses; agree treatment of linked delivery lines, breakage stock and valuation anomalies |
| Discounts and other charges | Line `DIS_PER`, header `DISCOUNT`, `BREAKAGE`, `FREIGHT`; some headers contain `LABOUR`; purchase header `GST_RATE` | Original invoice/report totals may use fields absent from ledger views; tax, labour, rounding and display rules need samples |
| User accounts and permissions | `UM_USERS`, `UM_MENU`, `UM_USER_RIGHTS`, two temporary ID tables; separate insert/update/delete/query flags; Users / Catalog and User Rights menus | Actual login algorithm, permission enforcement, effective user visibility and session rules; a new secure password scheme must not trust legacy passwords directly |
| English/Urdu output | Urdu master fields, `URDU_CONFIG`, lookup functions for accounts/products/company/city/units/stores | Actual text encoding, fonts, reading direction, screen layout and report rendering |
| Printing, exports and business reports | Catalog confirms voucher printing, cash statement, account ledger, trial/sub-trial balances, profit/loss, city recovery, stock summary/value, item ledger, purchase/sale detail; `LOCATION`/`PATH` lookup | Report tokens are recovered, but no template contents, filters, layout, print engine or export behavior is supplied |
| Integration settings | `CONFIG` includes SMS integration fields | Whether integration is active, provider behavior and authorization; credential values are intentionally excluded from schema artifacts |
| Audit information | Transaction entry/update users and timestamps; user last-login and rights grant metadata | Tamper-resistant audit trail, deletion history and original edit/cancellation behavior are not proved by timestamp columns |

## Confirmed monetary expressions

The following descriptions are direct readings of recovered SQL, not proposed
modern accounting behavior. `NVL(value,0)` supplies zero for a null amount.

For a collection of transaction lines, define:

```text
line_net = QTY * (RATE - RATE * NVL(DIS_PER, 0) / 100)
B = ROUND(SUM(NVL(line_net, 0)))
```

`DIS_PER` is a percentage. The source rounds the **sum**, before header
adjustments, using `ROUND` without a decimal-place argument. It does not use the
V2 prototype's absolute line discount convention. Quantities, prices and
discount columns are unrestricted Oracle `NUMBER`, so a fixed decimal precision
or maximum fraction count cannot be inferred from the schema.

| Source branch | Debit | Credit | Account selection |
| --- | --- | --- | --- |
| `PUR_VW_DTL` purchase/sales-return product-account branch | `B - DISCOUNT - BREAKAGE + FREIGHT` | 0 | Type 7: item `PUR_CODE`; type 10: item `EXP_CODE` |
| `PUR_VW_DTL` purchase/sales-return counterparty branch | 0 | `B - DISCOUNT - BREAKAGE + FREIGHT` | `P_TYPE='R'`: header account; `'C'`: hard-coded `04010001` |
| `SALE_VW_DTL` sale/purchase-return counterparty branch | `B - DISCOUNT - BREAKAGE + FREIGHT` | 0 | `S_TYPE='R'`: header account; `'C'`: hard-coded `04010001` |
| `SALE_VW_DTL` sale/purchase-return product-account branch | 0 | `B - BREAKAGE + FREIGHT` | Type 8: item `EXP_CODE`; type 9: item `PUR_CODE` |
| `PUR_VW_DTL` `BD` product-account branch | 0 | `B - FREIGHT` | Item `PUR_CODE` |
| `PUR_VW_DTL` `BD` counterparty branch | `B + FREIGHT` | 0 | Header account |
| `SALE_VW_DTL` `BR` counterparty branch | 0 | `B - BREAKAGE - FREIGHT` | Header account |
| `SALE_VW_DTL` `BR` product-account branch | `B - BREAKAGE - FREIGHT` | 0 | Item `EXP_CODE` |

Header amounts in the table are null-coalesced to zero. Product-account branches
aggregate by item account mapping, while counterparty branches aggregate by the
whole document. In purchase and sale branches the grouping contains both item
purchase and expense codes. Header adjustments are repeated **per group**;
therefore, multi-account documents can produce unequal totals even when the
single-account expression appears balanced.

Additional source discrepancies that must remain visible during reconciliation:

1. The sale product-account credit omits header `DISCOUNT`. With one account
   group and a nonzero header discount, it does not equal the counterparty debit.
2. `BD` uses opposite freight signs between counterpart entries. Header
   `DISCOUNT` appears in narration but not either amount expression. `LABOUR`
   is absent from these amount expressions.
3. `HAVING` filters differ between branches. Some test `B > 0`, others test
   `B - DISCOUNT > 0` or `B - BREAKAGE > 0`. They can suppress one side while
   retaining another.
4. `GST_RATE`, `LABOUR` and sale `AMOUNT_RCV` do not enter these ledger-view
   calculations. This does not prove they were ignored by the original invoice
   forms or reports; application code may have created extra journal entries.
5. Cash sale/purchase views use a hard-coded account, while journal cash
   counterparts use `CONFIG.CHCODE_CASH`. These can differ by company.

These are properties of the recovered definitions. Do not silently repair them
and claim unchanged historical results. A replacement must preserve source
facts for reconciliation, define corrections explicitly where approved, and
ensure new postings follow the agreed accounting rules.

The owner selected **correct new transactions; preserve history** on 2026-10-02.
This authorizes correcting the operational posting rules while retaining the
source archive and original-history reports unchanged.

## Ledger and voucher semantics

`LEDGER` includes `JDTL` lines only where `JMST.CANCEL IS NULL`. It then adds a
cash-account counterpart for vouchers of type 2 or 3:

```text
type 2: credit the company's configured cash account by SUM(detail DR)
type 3: debit the company's configured cash account by SUM(detail CR)
```

`ENTRY_TYPE` rows independently confirm type 2 as Cash Payment Voucher and type 3
as Cash Receipt Voucher. No default `CANCEL` value or automatic balancing
constraint is declared. The final two `LEDGER` branches
include all rows from `PUR_VW_DTL` and `SALE_VW_DTL`, rounding their individual
debits and credits again to a whole number. Sale/purchase headers have no
equivalent cancellation flag in this schema.

## Stock and valuation semantics

The recovered `STOCK` view contains these active branches:

| Source | Tag | Quantity direction | Value/rate source |
| --- | --- | --- | --- |
| `OPEN_STOCK` joined to products | `OP` | Positive `OPEN_QTY` inward | `OPEN_QTY * OPEN_RATE` |
| `PMST`/`PDTL` | Type 7 `PV`, type 10 `SR` | Positive detail `QTY` inward | Quantity times `GET_PUR_RATE(company,item,date)` |
| `SMST`/`SDTL` | Type 8 `SV`, type 9 `PR` | Positive detail `QTY` outward | Quantity times `GET_PUR_RATE(company,item,date)` |

The sale branch requires `SDTL.VNO_DCDTL IS NULL`, excluding linked delivery
lines. The delivery-challan outward branch is enclosed in a block comment. As
exported, neither dispatch nor its linked invoice contributes to this stock
view. Whether that condition affected active workflows requires original report
and application evidence. No stock movement for `BD` or `BR` appears in `STOCK`.

`STOCKB` is separate: it places both `BR` and `BD` quantities in **QTY_IN**, with
zero `QTY_OUT`, and values them at detail rate. `BR` uses a header store, whereas
`BD` uses the detail store. A stock report must not combine these streams under
invented directions without an explicit parity decision.

`DCTOSALE_BAL` presents dispatch quantities/values in one set of columns and
credit (`S_TYPE='R'`) sale quantities/values in another, with the original
delivery detail key retained. It uses raw `QTY * RATE`, requires positive value,
and does not subtract line/header discounts. The view supplies movements; a
consumer must group/subtract them to calculate a remaining balance.

`GET_PUR_RATE(company,item,date)` was recovered in full. It:

1. Selects opening rows and type-7 purchase rows for the same company/item on or
   before the supplied date.
2. Chooses the **maximum numeric ID** across opening `TRNID` and purchase detail
   `VNO`, then returns that candidate's raw opening/purchase rate, null as zero.
3. On `NO_DATA_FOUND`, selects the maximum type-10 return detail `VNO` for the
   same company/item/date restriction and returns its raw rate.
4. Returns zero only if that fallback also raises `NO_DATA_FOUND`.

This is not weighted-average cost, FIFO, or an explicit latest-date sort.
`STORE_ID` does not participate; rates are company/item/date scoped. Purchase
discounts, freight and breakage do not enter the returned rate. An equal maximum
ID in opening and purchase sources can yield multiple rows; the function has no
`TOO_MANY_ROWS` handler. Future-dated rows are excluded by the date predicate,
but a higher ID from an older date can still win.

The supplied history also contains 96 sale detail rows referencing stores whose
declared company differs from the sale header's company. Historical stock filters
include those referenced stores without reassigning their original ownership.
This is a source observation, not an approved rewrite of company/store history.

## Production acceptance still required

Schema recovery proves structure and source expressions. It cannot establish
full production readiness. The replacement must be verified against a concrete
reference set that includes:

- Original screens, effective menu ordering/permissions, representative printed
  invoices/reports, and the entry/edit/return/cancel/settlement workflows. The
  static menu parent hierarchy and labels are already recovered.
- Lossless, repeatable backup recovery with row counts, keys and relationships;
  preserve leading-zero identifiers and decimal values, and verify actual text
  encoding before displaying recovered Urdu labels.
- Reconciliation of account balances, cash, stock by company/store/item, return
  links, delivery links, and source anomalies; no unapproved changes to history.
- Agreed rounding, discounts, tax/labour, units, valuation, cash account and
  multi-company policies, with end-to-end scenario tests.
- Transactional posting, foreign-key integrity, concurrency behavior, validation,
  rollback on failure, backups/restore, migration rollback, user access controls,
  audit history, and data export.
- Tested installation, upgrade and operation on the actual target Windows
  environment, including printing, fonts, high-DPI display and keyboard use.

The original application or a guided walkthrough remains necessary to verify
the missing interface and workflow details. These evidence gaps do not justify
relabeling a smaller sales/purchases demo as a carbon copy.
