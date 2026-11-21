# Recovered legacy functional catalog

Reviewed 2026-10-02. This catalog contains only `UM_MENU` and `ENTRY_TYPE`
functional definitions decoded from the supplied Oracle export. No user,
account, company, contact, permission-grant or credential records were decoded
for this catalog.

Source: `BT-270926.EXE` (7,299,072 bytes).

SHA-256: `d2cd806aa644320cf6d2eaa6192e7d6c840adcde8226f62b5ad1422980cc7e9e`.

The strict decoder pins that source fingerprint, preserves scalar bytes by
round-trip validation, and checks the table terminators. The catalog extractor
also validates parent references and rejects cycles. JSON preserves the original
labels, spelling, flags and file tokens in [`legacy_menus.json`](legacy_menus.json).

## Visibility and scope

There are **46 menu definitions**, all with `IS_VISIBLE='Y'`, and
**35 module file tokens**. The other
11 entries are the root and containers.
All entries have `APPLICATION='FMS'` and `IS_ROLE='N'`; `CATE` is null.
These are catalog properties. Actual user visibility and authorization can
also depend on user rights, which this extraction intentionally does not read.

The hierarchy below follows `PARENT_NODE`. Siblings appear by `MENU_ID` for
repeatable presentation; the original per-user sort order is not established.
Original spelling is retained, including `Cinfiguration`, `Finalcial`,
`Inventiry` and `Puchase`. A modern UI may correct these labels while retaining
the represented features.

## Menu hierarchy

```text
[1] Financial management System
  [2] Setup
    [3] User Management
      [4] * Users / Catalog -> UM_CATALOG
      [5] * User Rights -> UM_USER_PRIVS
    [6] * Company Cinfiguration -> CONFIG
  [7] Coding
    [8] * City Definition -> CITY
    [9] Finalcial Coding
      [11] * Chart Of Account ( Level1 ) -> CHART_LEVEL1
      [12] * Chart Of Account ( Level2 ) -> CHART_LEVEL2
      [13] * Chart Of Account ( Level3 ) -> CHART_LEVEL3
    [10] Inventory Coding
      [14] * Main Item Groups -> MAIN_ITEM
      [15] * Item Sub Groups -> SUB_ITEM
      [16] * Item Definition -> PUR_ITEMS
      [17] * Opening Stock -> OPEN_STOCK
  [18] Financial Transactions
    [19] * Opening Balance -> OPEN_BAL
    [20] * Cash Payment Voucher -> CPV
    [21] * Cash Receipt Voucher -> CRV
    [22] * Journal Voucher -> JV
  [23] Inventiry
    [24] * Purchase -> PUR
    [25] * Sale Credit -> SALE_CREDIT
    [26] * Puchase Return -> PUR_RET
    [27] * Sale Return -> SALE_RET
    [42] * Counter Sale -> SALE_COUNTER
    [45] * Breakage Receive -> BRCV
    [46] * Breakage Dispatch -> BDISP
  [28] Reports
    [29] Financial Reports
      [31] * Voucher Printing -> VOUCHER
      [32] * Cash Statement -> CASH
      [33] * Account Ledger -> LEDGER
      [34] * Trial Balance -> TRIALBALANCE
      [35] * Sub Trial Balance -> SUB_TRIAL
      [36] * Profit And Loss Report -> PLS
      [43] * City Wise Recovery report -> CITY_RECOVERY
    [30] Inventory Reports
      [37] * Stock Summary -> STOCK
      [38] * Stock Summary With Value -> STOCK_VALUE
      [39] * Item Ledger -> ITEM_LEDGER
      [40] * Purchase / Purchase Return Detail -> PUR_DETAIL
      [41] * Sale / Sale Return Detail -> SALE_DETAIL
      [44] * Uom Coding -> UOM
```

## Entry types

These labels and numbers are confirmed by `ENTRY_TYPE` rows, rather than
inferred from view expressions.

| Type code | Short label | Original description |
| ---: | --- | --- |
| 1 | `JV` | Journal Voucher |
| 2 | `CPV` | Cash Payment Voucher |
| 3 | `CRV` | Cash Receipt Voucher |
| 4 | `BPV` | Bank Payment Voucher |
| 5 | `BRV` | Bank Receipt Voucher |
| 6 | `OP` | Opening Balance |
| 7 | `PV` | Purchase Voucher |
| 8 | `SV` | Sale Voucher |
| 9 | `PRV` | Purchase Return Voucher |
| 10 | `SRV` | Sale Return Voucher |
| 11 | `BRR` | Breakage Receive Voucher |
| 12 | `BRD` | Breakage Dispatch Voucher |

Codes 4 and 5 exist for bank payment/receipt, but no corresponding
menu entries occur in this catalog. Code 6 has an explicit Opening Balance
menu. Code 8 has separate Sale Credit and Counter Sale modules. The specific
header/line values written by each form still need application evidence.

## Declared report modules

These entries have `MODULE_TYPE='R'` and a non-null `FILE_NAME`. The tokens
identify declared report modules, not recovered report templates. No file
extension, template contents, dimensions, printer settings or parameter
dialog layout is stored in these catalog rows.

| Menu ID | Original label | File token |
| ---: | --- | --- |
| 31 | * Voucher Printing | `VOUCHER` |
| 32 | * Cash Statement | `CASH` |
| 33 | * Account Ledger | `LEDGER` |
| 34 | * Trial Balance | `TRIALBALANCE` |
| 35 | * Sub Trial Balance | `SUB_TRIAL` |
| 36 | * Profit And Loss Report | `PLS` |
| 37 | * Stock Summary | `STOCK` |
| 38 | * Stock Summary With Value | `STOCK_VALUE` |
| 39 | * Item Ledger | `ITEM_LEDGER` |
| 40 | * Purchase / Purchase Return Detail | `PUR_DETAIL` |
| 41 | * Sale / Sale Return Detail | `SALE_DETAIL` |
| 43 | * City Wise Recovery report | `CITY_RECOVERY` |
| 44 | * Uom Coding | `UOM` |

Menu 44 is an anomaly: `* Uom Coding` is classified `R`, placed under
Inventory Reports, and addressed as `INVENTORY_CODING_MENU.UOM`. Preserve
its source metadata; do not infer from that flag alone that the replacement
should turn unit maintenance into a report.

## External assets and parity gaps

`MENU_ADDRESS` values are symbolic dotted menu references, not filesystem
paths. `FILE_NAME` values are bare module tokens. This extraction does not
resolve them against `LOCATION` or inspect any machine-specific path data.
No `.fmb`, `.fmx`, `.rdf`, `.rep`, `.pll`, `.mmb` or `.mmx` files were found
in the workspace during this review. The original application may require
such files or other runtime assets, but their actual names, extensions and
directories cannot be recovered from these two tables.

The catalog confirms the following scope that the replacement must address:

- Company configuration, user/catalog administration and per-user rights.
- City definitions, three chart-of-account levels, item groups/subgroups,
  item definitions, opening stock and unit coding.
- Opening balances, cash payments/receipts and journal vouchers.
- Purchases, credit sales, counter sales, standard purchase/sales returns,
  breakage receive and breakage dispatch.
- Voucher printing, cash statements, account ledgers, trial/sub-trial
  balances, profit and loss, city recovery, stock summary/value, item
  ledger and purchase/sale return detail reports.

Breakage naming is now confirmed: type 11 is Breakage Receive (`BRR`) and
type 12 is Breakage Dispatch (`BRD`), exposed by `BRCV` and `BDISP` modules.
The schema's `BRMST`/`BRDTL` and `BDMST`/`BDDTL` naming is consistent with
those features, but a direct table-to-form mapping remains an inference
until original form code or a reference transaction confirms it. The view
labels 'Breakage Sale Return' and 'Purchase Return Invoice' are historical
SQL narration, not the canonical menu names.

Delivery-challan schema/view support exists, but no delivery-challan menu
entry appears among these 46 definitions. Bank vouchers have type codes but
no explicit menus. This does not establish whether they were unused, invoked
indirectly or provided by an external module. Likewise, the catalog names
reports without proving their filters, formulas, layouts or launch behavior.

The original application/forms and representative reports are still needed
to verify these behaviors. See [`LEGACY_ANALYSIS.md`](LEGACY_ANALYSIS.md)
for recovered stock/accounting semantics and known inconsistencies.

## Reproduction

```sh
python3 tools/inspect_legacy_menus.py BT-270926.EXE --output-dir docs
python3 tools/inspect_legacy_menus.py BT-270926.EXE --output-dir docs --check
```
