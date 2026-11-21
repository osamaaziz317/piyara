# Native reports

The operational reports include sealed historical closing balances and new
postings. Read [OPERATIONAL_REPORTS.md](OPERATIONAL_REPORTS.md) for cutoff
semantics, stock valuation, the guarded periodic profit report and provenance.

## Sales and purchase detail

`NativeReports.trade_detail` and `trade_selectors` implement combined invoices,
returns and their dated reversals for either trade family. They preserve saved
labels/base units, use exact signed full-period totals and page the visible
lines. Final document totals count each document once and are omitted under
item/store filters. Account/cash selection is independent of invoice/return
activity, and combined output requires every selected transaction read grant.
See [TRADE_DETAIL_REPORTS.md](TRADE_DETAIL_REPORTS.md) for the API scope,
filters, missing-snapshot handling and complete HTML output.

## Hierarchical sub-trial balance

`NativeReports.sub_trial_balance(actor, company_id, date_from=None, date_to=None)`
uses the **current explicit `accounts.parent_id` links**. It neither interprets
account-code prefixes nor guesses a legacy depth, normal balance or classification.
Current company access and `trial_balance/read` are required within the same native
transaction that reads postings and the chart of accounts.

The result is `{"rows": [...], "totals": {...}}`. Each row contains `account_id`,
`parent_id`, current `code`, `name`, `name_urdu`, `is_postable`, `is_active`, zero-based
`depth`, and exact `Decimal` values for `opening`, `debit`, `credit` and `closing`.
Rows follow preorder: parents precede their children, and sibling groups sort by
code then UUID. Parent IDs and depths describe the hierarchy; full UUID path lists
are deliberately omitted to keep allocation linear on deep charts.

Each node includes its own direct postings **plus all descendant postings**. A
postable account can also be a parent, so its own activity must not be omitted.
The single `totals` dictionary is calculated from direct postings once, using the
same company/date scope as the ordinary trial balance. **Adding the displayed
hierarchy subtotals together would double-count activity.** Signed opening/closing
balances use debit positive and credit negative; period debit/credit columns are
unsigned. Arithmetic uses an isolated 1,000-digit decimal context.

Dates are inclusive ISO `YYYY-MM-DD`, with opening from before the start and
closing through the end. Missing start means zero opening and all prior activity
in the period; missing end includes all posted dates. Invalid/reversed dates fail.
Include every account with posted activity through the end date and all of its
ancestors, even for zero net balances, no activity within the period, inactive or
formerly postable accounts. Unused branches, other-company activity, future-only
activity and drafts do not create rows. Imported closings appear once at the
cutoff; earlier historical detail remains in Existing records.

Parent-chain traversal, preorder construction and bottom-up aggregation are
iterative, so valid deep charts do not depend on Python's recursion limit. A cycle
or missing parent in any included account's ancestor chain causes an explicit
error with no partial report. Unused disconnected branches are outside this
company/date report scope. Current reparenting or renaming changes the next report's
grouping/labels without altering posted entries or their document snapshots.

The `sub_trial_balance` HTML export uses the same query within one authorized
company/report snapshot. Its 100,000-row refusal limit counts both activity nodes
and included ancestors. Indentation is bounded for printing and every row displays
its actual level. Grand totals appear once, labeled as direct postings counted
once. See [REPORT_OUTPUT.md](REPORT_OUTPUT.md).

Focused verification:

```sh
python3 -m unittest tests.test_sub_trial
```

Synthetic cases cover multi-level and parent-owned postings, unrelated roots,
current reparenting, inactive and cancelling history, company/date/posted scope,
cycles and missing parents, a 1,200-level chart, live permissions, exact 65-digit
aggregates under low ambient precision, full HTML beyond 500 rows, ancestor row
caps, consistent concurrent snapshots and render-time permission revocation.

## Native stock ledger

`bifma.native_reports.NativeReports` reads the imported closings and posted
native records. Original rows remain in the preserved archive. Every stock
query and selector checks current company access and the
`stock/read` permission inside the same native transaction as its report reads.
Ordinary users with that permission can use these selectors without master
administration privileges.

```python
reports = NativeReports(native_database_path, security=security_service)
products = reports.stock_products(actor, company_id)
stores = reports.stock_stores(actor, company_id)
result = reports.stock_ledger(
    actor, company_id, product_id,
    store_id=None, pool="regular", date_from=None, date_to=None,
    offset=0, limit=100,
)
```

Selectors return current master labels as dictionaries containing `id`, `code`,
`name`, `name_urdu` and `is_active`. Products include shared active items and
inactive items with posted stock history in the selected company. Stores include
that company's assigned/historically used stores, even when the store or its
assignment is inactive. An inactive item with history only in another company
is not exposed as this company's historical item. Missing or unavailable IDs
are rejected by the ledger.

The result contains `product`, `store` (or `None` for all company stores), `pool`,
`date_from`, `date_to`, `opening`, `in`, `out`, `closing`, `rows`, `total`, `offset`
and `limit`. Quantity totals and row quantities use exact `Decimal` values.
Value fields and baseline provenance are described in
[OPERATIONAL_REPORTS.md](OPERATIONAL_REPORTS.md).
Limits are 1–500; offsets are nonnegative integers. Date boundaries use
`YYYY-MM-DD` and include both specified days.

Opening is the signed quantity before `date_from`; with no start date it is
zero, with imported balances represented explicitly at the cutoff. Period `in`
and `out` are positive totals over the complete filtered
period. Closing equals opening plus period in minus period out. Each displayed
row's `balance` includes opening and all preceding period rows, including rows
on earlier pages. `total` counts all period movements, not just displayed rows.
An offset beyond the last row returns an empty page with the same full-period
totals. With `store_id=None`, the running quantity combines the company's stores
for the selected item/pool; individual rows retain their actual store.

Rows include immutable document number/date/kind, `movement_id`, `document_id`,
`document_line_id`, `line_number`, `product_id`, `store_id`, `pool`, `direction`,
`quantity`, `in`, `out`, `balance`, `posting_sequence` and `posted_at`. Reversal
references are `reverses_document_id`, `reverses_document_number` and
`reverses_movement_id`; original and reversal movements both appear. No stock
movement is inferred from a document's kind. Actual posted directions control
the report, and draft effects are excluded.

Order is business date, document posting sequence, posting timestamp, document
ID, document line number, then movement ID. The last fields make ties stable.
Older native records with no posting sequence appear before numbered records
on the same date, using the timestamp/ID fallback. This is deterministic and
does not claim to reconstruct an unknown original within-day posting order.
Separate page requests reflect the committed database at each request; postings
added between requests can change pagination/totals. The UI can reload the
report to see the updated state.

Product/store codes, English names and Urdu names use each document's saved
snapshot when available. Corresponding row fields are `product_code`,
`product_name`, `product_name_urdu`, `store_code`, `store_name` and
`store_name_urdu`. `product_label_source` and `store_label_source` identify
`snapshot` versus the explicit `current` master fallback for older/missing
snapshots. The top-level product/store descriptors use current selector labels.
Snapshot JSON is loaded once per document on the displayed page, avoiding a
repeated full-document snapshot on every movement.

Any posted movement for the selected company/item/store with an unclassified
`NULL` stock pool through the end date prevents the report. Earlier movements
also matter because they affect opening quantity. Unrelated products, companies,
stores outside the selected filter, future movements, and draft movements do
not block that scope. The service does not silently classify old native stock.

Focused verification:

```sh
python3 -m unittest discover -s tests -p 'test_native_reports.py'
```

Synthetic tests cover exact large/fractional quantities, page carry, chronology,
scope/date/pool filtering, saved labels after renames, real service reversals,
inactive historical selectors, live permission revocation and missing IDs.
Passing these tests does not establish original report layout parity or
production readiness of the whole application. Separate valuation and baseline
tests cover the continued operational values.

## City balances and cash book

Version `0.3.0.dev5` adds [account balances by city and a dedicated cash book](CITY_CASH_REPORTS.md).
City balances use `trial_balance/read`; the cash book uses `ledger/read`. Both
retain exact posted-ledger arithmetic, full-filter totals, explicit current
labels and complete authorized output. No invoice aging or payment allocation
is inferred.
