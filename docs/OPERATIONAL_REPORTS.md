# Existing balances and operational reports

The one application carries the imported closing balances into new account and
stock reports. Original documents open in the same sales, purchase, return,
voucher and opening sections as new records; their saved values remain unchanged.
The native database stores sealed account/stock closings and their archive hash;
it does not replay old trades through the corrected posting rules.

All report sums use exact Decimal arithmetic. An account or item ledger without
a start date shows explicit **Existing balances** rows at the import cutoff.
These are opening snapshots, not invented invoices: their native document ID is
absent, and they carry source provenance. A start date after the cutoff puts
them in the opening column instead. Ending an account or item-ledger report before
the cutoff fails with guidance to open earlier documents in their module or use
the dated stock reports. Cutoff-inclusive
period columns contain the snapshot once; they are not historical turnover.
Normal application reports start on the following day.

Trial balance, sub-trial balance, account and cash ledgers, city balances, stock
quantities and item ledgers all include the same closings. Negative source stock
and exact historical stock values remain negative where recovered. Stock openings
do not generate duplicate account journals during import. Historical inactive
accounts/items remain selectable in their company. The original configured cash
role remains evidence for the cash book if the current account kind changes.

## Stock activity across the closing date

**Stock quantities** and **Stock valuation** accept dates before, after or across
the closing date. For earlier/cross-cutoff ranges, `UnifiedReports` reads exact
original `LegacyStock` movement totals from the verified archive, then adds new
posted stock movements once. It does not also add the imported stock snapshot.
An opening column contains activity before the selected start date. Ranges that
start after the cutoff use the ordinary carried snapshot plus new movements.

Original negative balances, current source-unit interpretation and historical
incoming breakage dispatches stay as recorded by the source rules. New breakage
dispatches remain outward. Company, store, regular/breakage pool and item-search
filters apply to the combined result. Inactive original items and store
memberships remain reportable. Missing or mismatched original archives fail
clearly instead of producing a partial historical total.

Both query paths require fresh company and stock-read access. The archive is
verified before entering the authorized native snapshot and checked for changes
after its read. `ReportExporter(path, security, reports=unified_reports)` uses
the same full query as the screen, enforces the export row limit and rechecks
permissions before publishing. Historical-flow output identifies its selected
original/new activity; account and item-ledger exports continue to identify their
snapshot boundary honestly.

## Stock valuation

**Reports → Stock valuation** sums preserved historical movement values and
fixed new movement costs. This follows the source's last-purchase-rate method;
closing stock value is **not** closing quantity multiplied by the latest rate.
Separate quantities are never added across different items or base units.

For new regular stock, eligible purchase/opening candidates must be dated no
later than the movement. Native posting sequence, then line number, supplies
the ordering formerly provided by numeric source IDs. It is independent of the
store. The last matching line in a newly posted purchase is eligible for that
purchase's movements. A primary purchase/opening rate takes precedence over
sale-return fallback rates, including when that primary comes from the baseline.
Discounts and header charges do not change this inventory method.

Costs are fixed under the posting lock in immutable movement valuation
snapshots. Later or backdated purchases do not rewrite those saved values.
A posted reversal uses exactly the original movement's value in the opposite
direction. Its original rate candidate ceases to apply from the reversal's
business date. Breakage receive/dispatch use their entered detail rate, with
new dispatches moving outward. Explicit stock openings retain their entered
total; a repeating value/quantity ratio is not rounded into a new unit cost.

Missing costs are displayed as **Unknown**, never inferred from a product's
default zero price. A missing source purchase rate and a negative source rate
cannot establish a new cost. Those cases preserve historical values but make
affected new closing-value totals unavailable. A known opening total can remain
available even when it has no exact terminating per-unit rate.

`NativeReports.stock_valuation` uses the ordinary stock filters and returns
`rows`, `totals`, `baseline`, `store`, `pool`, `date_from`, `date_to`. Value fields
are `value_opening`, `value_in`, `value_out`, `value_closing` (Decimal or None),
and `missing_valuation_count`. Item-ledger rows also expose saved cost, value,
running value and provenance. Complete HTML output retains the same snapshot,
filters and access checks.

## Periodic trading profit

The original menus contain profit and loss, but its original form/formula is
unavailable. **Profit and loss** therefore presents a transparent **Periodic
trading profit** calculation and reports unresolved classifications instead of
claiming original report parity:

    classified income − classified expenses
    + closing inventory − opening inventory
    − net external stock openings during the period

Both regular and breakage pools contribute inventory value. Reversals of external
stock openings reverse that adjustment too; introducing opening inventory does
not itself create profit. Income/expense contributions cover the selected period,
not imported account closings. An imported company's period must begin after
the cutoff; that following day is the default.

This result is available only when the applicable account classifications are
explicit, product purchase mappings are expenses and sales mappings are income,
actual period trade postings use compatible classifications, no separate
inventory-account postings would double count cost, and stock values are known.
Current labels/classifications and mapping issues are printed separately from
immutable posting values. Unclassified balances, incompatible mappings or
missing cost yield **Review required** and no profit number. Classifying accounts
is a real business setup decision; the software does not infer classifications
from their names or balance signs.

The service and export require all three live grants: `reports/read`,
`trial_balance/read`, and `stock/read`, with current company access. This report
does not add invoice aging, tax treatment or a claim of original print-layout
parity.

Verification uses synthetic temporary databases:

```sh
python3 -m unittest tests.test_baseline_reports tests.test_unified_reports tests.test_valuation tests.test_profit_loss
```
