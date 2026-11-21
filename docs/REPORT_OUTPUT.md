# Native printable reports

`bifma.report_export.ReportExporter` writes complete native reports as standalone
UTF-8 HTML. It opens no browser, starts no print job and uses no network assets.
The layout supports English/Urdu text and A4 landscape printing. This is a native
layout; original report templates and target printer qualification are unavailable.
Imported opening balances, frozen stock valuation and the guarded periodic
profit report are described in [OPERATIONAL_REPORTS.md](OPERATIONAL_REPORTS.md).
The application supplies its `UnifiedReports` instance to the exporter, so
Stock quantities and Stock valuation include original activity for earlier or
cross-cutoff dates. Account and item ledgers retain the closing snapshot plus
new details; this does not add reconstructed historical account turnover.

```python
exporter = ReportExporter(native_path, security=security_service)
created_path = exporter.export_html(
    actor, company_id, "item_ledger", destination,
    product_id=product_uuid, store_id=None, pool="regular",
    date_from="2026-10-01", date_to="2026-10-31",
)
```

Construct the exporter outside database sessions, following the managed native
startup contract. `security` is optional. `destination` must be a new file; the
method returns its resolved `Path`. It never exports a cached screen result.
For the same original/new stock behavior as the application, construct
`UnifiedReports(native_path, archive_path, security=security_service)` and pass
it as `ReportExporter(native_path, reports=reports)`. The injected service must
belong to that same working file. Archive verification precedes the authorized
report transaction; changed source data fails instead of yielding partial output.

## Report tokens and filters

| Token | Read permission | Required filters | Additional filters |
| --- | --- | --- | --- |
| `trial_balance` | `trial_balance/read` | None | `date_from`, `date_to` |
| `sub_trial_balance` | `trial_balance/read` | None | `date_from`, `date_to` |
| `account_ledger` | `ledger/read` | `account_id` | `date_from`, `date_to` |
| `cash_book` | `ledger/read` | Eligible `account_id` | `date_from`, `date_to` |
| `city_balances` | `trial_balance/read` | None | `city_id`, `account_kind`, `search`, `include_zero`, `date_from`, `date_to` |
| `stock_quantities` | `stock/read` | None | `store_id`, `pool`, `search`, `date_from`, `date_to` |
| `stock_valuation` | `stock/read` | None | `store_id`, `pool`, `search`, `date_from`, `date_to` |
| `profit_loss` | `reports/read`, `trial_balance/read`, `stock/read` | None | `date_from`, `date_to` |
| `item_ledger` | `stock/read` | `product_id` | `store_id`, `pool`, `date_from`, `date_to` |
| `sales_detail` | Selected sales/returns read grants | None | `kind`, `account_id`, `product_id`, `store_id`, `search`, `date_from`, `date_to` |
| `purchase_detail` | Selected purchases/returns read grants | None | Same trade filters |

Dates are optional inclusive ISO dates. The end cannot precede the start. For
balance/ledger reports, missing start includes all activity with zero opening;
a supplied start carries earlier posted activity into opening. Trade detail has
no opening balance. Missing end includes all posted dates. Pool defaults
to `regular`; only `regular` and `breakage` are accepted. Missing/`None` store means
all company stores. Search defaults to an empty string and matches the existing
stock report's item code/name search, or literal case-insensitive document-number
search for trade detail. Trade `kind` is `all`, `invoice` or `return`, defaulting
to `all`; its selected transaction kinds determine all required permissions.
Unknown filters, pagination arguments,
missing required IDs and malformed filters fail explicitly.

City balances use current account names, kinds and city assignments. `city_id`
is `None` for all cities, `"unassigned"` for accounts with no city, or a saved
city UUID. `account_kind` is `None` or an exact current kind; `search` is a literal
account code/name/Urdu-name substring of at most 200 characters. `include_zero`
defaults to `True`. Setting it to `False` excludes zero-closing accounts from
the rows, groups and all totals, including their opening and period amounts.

Cash book always requires an explicit eligible account ID; configuration never
substitutes another account. Current cash accounts must be active and postable
or have posted history in the selected company. A former cash role requires
consistent saved account metadata on that account's posted journal activity in
the same company. Names remain current; the report states whether the cash role
comes from current setup or a saved snapshot. Once eligible, the account's
complete selected-period journal activity is included, regardless of document
kind. See [CITY_CASH_REPORTS.md](CITY_CASH_REPORTS.md) for operator instructions.

## Consistent data and limits

`NativeReports.export_snapshot` authorizes the selected company and every required report module
and reads company labels, all result rows and totals in one native transaction.
It calls the same connection-taking query functions as the screen reports. It
does not collect separately paginated snapshots. Existing public paged report
APIs retain their page limits and authorization requirements.

The export limit is **100,000 result rows**. Exceeding the limit raises `ValueError`
with a request to narrow filters; no truncated file is published. For trial balance
the count is account rows; city balances likewise count retained account rows;
for sub-trial balance it includes all activity accounts
and their ancestors; for stock quantities it is product/store groups; for
ledgers it is movements or posting lines within the selected period; for trade
detail it counts selected document lines, with grouped quantity totals in an
additional table. Earlier
opening activity is fully included in arithmetic but does not consume printed
row capacity. Label lookups batch at most 500 document IDs per query using that
same connection. Arithmetic uses exact `Decimal` values and isolated precision;
output is canonical plain decimal text, without binary floats or locale rounding.

Account reports include signed opening/closing balances and full-period unsigned
debits/credits. Positive signed balances mean debit, negative mean credit.
City closing debit and credit totals split each account's closing balance before
adding it to city and grand totals. A city's net balance does not replace those
two totals. City groups cover all selected accounts, including those off screen.
Sub-trial balance follows the current explicit account parent links and prints
each node's own-plus-descendant activity in preorder with current bilingual labels.
The displayed subtotals overlap: its single grand total counts direct postings
once, rather than adding hierarchy rows. No account-code prefix or normal-balance
classification is inferred. Every row shows its level; indentation stops at eight
levels to keep deep charts printable. Missing parents/cycles in included ancestor
chains fail before any output is published. See [NATIVE_REPORTS.md](NATIVE_REPORTS.md)
for the complete hierarchy and zero-net-activity contract.
Stock quantities retain separate rows for items and stores, with no grand total
across unrelated items. Item ledger may combine one item's quantities across all
selected-company stores. Stock valuation and item-ledger values retain exact
source closing values and frozen new movement costs. Missing costs are Unknown.
The quantity-only report deliberately omits monetary values.

Rows include posted reversals and exclude drafts and other companies. Ledger
chronology remains business date, posting sequence, posting time, document ID and
line/movement order. Older native rows with no posting sequence sort before
numbered entries on the same date, using the deterministic existing fallback;
this does not claim to recover their original posting order. See
[NATIVE_REPORTS.md](NATIVE_REPORTS.md) for item-ledger scope and inactive history.

Company labels and optional `address`/`address_urdu` are current at the report
snapshot. Account headings and aggregate report labels are current masters. Item
ledger row item/store labels use saved document labels when available, including
reversal references; older missing metadata retains the existing explicit current
master fallback. Unknown relevant stock pools fail as in the screen reports,
including unknown opening movements. Reports add sealed imported closings to
new postings, except earlier/cross-cutoff stock reports, which use original
movements followed by new ones and never add the snapshot twice. The recovered
archive is unchanged. The generated-at UTC timestamp records report materialization,
not the period end or a transaction posting date.

## Authorization and publication

Session validity, company access and module `read` permission are checked live
within the report transaction. Access is checked again after rendering while the
finished file is published in a short authorization transaction. Revocation or
expiry during rendering therefore prevents publication. Business records are
unchanged; authorization may update the session heartbeat.
Combined trade exports require both invoice and return grants at both checks;
single-kind exports require their selected grant. See [TRADE_DETAIL_REPORTS.md](TRADE_DETAIL_REPORTS.md)
for signed line amounts, document totals and saved-label requirements.

All variable text is escaped and direction-isolated. No scripts, interactive
forms, external assets or hyperlinks are emitted; company website text is not
used as a network link. Inline print CSS is static and a content security policy
denies network/active content. Optional company fields absent in older supplied
row shapes are tolerated, but normal native startup still requires the current
schema or an explicit upgrade.

The complete file is staged beside its destination using private mode `0600`,
flushed, and published through `database.publish_new_file`. The final path cannot
already exist, including a live or broken symlink. Atomic non-overwriting link
creation protects against a competing destination created after validation.
On failure, staging is removed; any published file is removed only when its
device/inode identity still belongs to this operation. Another writer's file is
preserved. Permissions and filesystem durability still need target Windows
qualification; no stronger guarantee is inferred from Linux tests.

Synthetic tests cover full exports beyond 500 rows/documents, exact large balances,
hierarchy subtotals and once-counted grand totals, saved labels, bilingual text and injection escaping, company isolation and fresh
permissions, expired sessions and render-time revocation, concurrent writes during
snapshot materialization, explicit row caps, filters and unknown pools, unchanged
business records, private/non-overwriting output and publication failure cleanup.
