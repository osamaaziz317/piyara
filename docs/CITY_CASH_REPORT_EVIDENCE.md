# City balances and cash-book evidence

Reviewed 2026-10-02. This is a read-only, count-only investigation to guide the
next native reporting increment. The proposed reports below are not implemented
by this document and are not certified copies of the original report layouts.
They can report exact posted balances without inventing invoice aging or payment
allocation. The owner-selected rule remains **correct new transactions; preserve
history**.

## Evidence and limits

The [recovered menu catalog](LEGACY_MENUS.md#declared-report-modules) identifies
two report modules: menu 32, **Cash Statement**, token `CASH`; and menu 43,
**City Wise Recovery report**, token `CITY_RECOVERY`. Both have module type `R`.
The catalog contains module routing metadata, not their report query, form,
parameter definitions or printable template.

The [recovered schema and functions](legacy_schema.sql) establish these facts:

- `CHART.FK_CCODE` is a nullable foreign key to `CITY.PK_CCODE`. Cities and
  accounts have English and Urdu names. `CHLEVEL` and `ACCTYPE` exist, but their
  original classification rules are not recovered.
- `GET_ACCOUNT_CITY` and `GET_ACCOUNT_CITY_URDU` join that relationship and
  concatenate account/city labels. `GET_CITY` and its Urdu equivalent look up a
  city name. These functions do not compute collections, overdue amounts or
  balances. None of the six views is a city-recovery query.
- `CONFIG.CHCODE_CASH` references a chart account. `LEDGER` includes active
  journal details, automatic cash counterparts for types 2/3, and purchase/sale
  view entries. Cash trade branches use a hardcoded account; voucher
  counterparts use the configured account. Both configurations match that
  hardcoded account in this backup.
- No due-date, credit-term, aging or invoice-payment allocation columns were
  recovered. Sale `AMOUNT_RCV` does not enter the ledger formula and supplies no
  invoice-to-receipt relationship. See [the monetary analysis](LEGACY_ANALYSIS.md#ledger-and-voucher-semantics)
  and [cash-trade evidence](CASH_TRADES.md#source-evidence-and-posting).

Consequently, the original reports' customer selection, account ranges,
company/date defaults, treatment of advances, zero-balance filters, collection
targets, daily summaries, ordering and print layout remain unknown. A report
token alone does not establish those rules. Account-code prefixes, balance sign
and chart level must not be used to invent customer/supplier classifications.

## Count-only observations from the verified archive

Inspection used SQLite `mode=ro` with `query_only=ON`; no business database was
written. The source is `BT-270926.EXE`, SHA-256
`d2cd806aa644320cf6d2eaa6192e7d6c840adcde8226f62b5ad1422980cc7e9e`.
The recovered archive SHA-256 remained
`d5883f726438192e9e5331cb1b70ae2d4b081d8dcf72a8b2770cb692a92d83be`.
See [Oracle table reconciliation](oracle_reconciliation.json) and
[ledger-view parity](oracle_report_parity.json) for independent verification of
the historical inputs and formulas. No names, account codes, balances, contact
details or credentials from source records are included here.

| Observation | Count |
| --- | ---: |
| Accounts / cities | 703 / 70 |
| Accounts with / without a city | 539 / 164 |
| Cities referenced by accounts | 56 |
| Broken non-null account-to-city references | 0 |
| Accounts with a populated `ACCTYPE` | 0 |
| Level 1 / level 2 / level 3 accounts | 10 / 22 / 671 |
| All-time ledger company/account groups | 603 |
| Those groups with / without a city | 538 / 65 |
| Groups with nonzero closing balances | 529 |
| Nonzero debit / credit closing groups | 351 / 178 |

All 529 nonzero groups have a city at this all-time cutoff. That does not justify
dropping unassigned accounts: 65 company/account groups without a city have
posted activity, and another reporting period can have different balances. City
assignment also does not prove that an account is a customer.

The combined count of ledger rows for each company's configured cash account is
**1,053**:

| Source voucher type | Rows |
| --- | ---: |
| Cash payment, type 2 | 524 |
| Cash receipt, type 3 | 524 |
| Cash sale, type 8 | 3 |
| Journal, type 1 | 1 |
| Opening balance, type 6 | 1 |

Thus, a receipt/payment-voucher-only statement would omit five real cash-account
entries. A cash-kind-only filter would also omit ordinary journals, openings and
other documents posting to the same account. Counts above combine two companies
for evidence only; operational queries must remain company-scoped.

## Proposed native contract: Account balances by city

This report groups direct posted account balances by their **current city
assignment**. It must not be labeled invoice recovery, overdue debt or aging.
The existing [native trial-balance query](../bifma/native_reports.py) provides
the exact accounting basis. Current native accounts explicitly store `city_id`
and account kind; [issued account snapshots](../bifma/transactions.py) do not
store a city ID or city label. Historical city assignment therefore cannot be
reconstructed from those snapshots.

| Contract area | Proposed behavior |
| --- | --- |
| Authorization | Require current company access and `trial_balance/read`, matching the underlying account-balance scope. |
| Dates | Optional ISO `date_from` / `date_to`, inclusive business dates; reject reversed ranges. No lower bound means opening zero; no upper bound includes all posted dates. |
| City filter | Explicit all, selected city, or unassigned. A missing city ID must not ambiguously mean both all and unassigned. |
| Account scope | Default all accounts with posted history through the upper date. Optional explicit current account-kind filter; show it in the report. Do not infer kinds from codes or debit/credit sign. |
| Other filters | Literal account code/name search; optional explicit zero-closing filter, default retaining zero-closing accounts with posted history. |
| Detail rows | Current account and city labels, account kind, opening balance, period debit, period credit and signed closing balance. Keep inactive/formerly postable accounts with matching history. |
| Grouping | One account row per company/account. Keep a visible Unassigned group. Group only direct postings; do not add account hierarchy subtotals again. |
| Totals | City and grand opening/period/closing totals over the full filtered result. Show closing debit and closing credit totals separately, as well as net, so credits/advances do not disappear inside a net collection figure. |
| Labels | State that city assignments and account kinds are current master data. Renaming or reassigning a city can regroup the same unchanged postings. |

For each selected account, the exact calculation is:

```text
opening = sum(debit - credit before date_from)
period_debit = sum(debit within the selected period)
period_credit = sum(credit within the selected period)
closing = opening + period_debit - period_credit

city_closing_debit = sum(max(account_closing, 0))
city_closing_credit = sum(max(-account_closing, 0))
city_net = city_closing_debit - city_closing_credit
```

An explicitly selected native customer/both-account scope can support a useful
customer-balance view. It still reports account balances, not which invoices are
unpaid, when payment is due, or how receipts should be allocated. The source
archive's missing account classifications cannot be filled using this filter.

## Proposed native contract: Cash book

This is the complete journal ledger of one explicitly selected cash account,
using the existing [native ledger query](../bifma/native_reports.py). Selection
and output must name that account; no silent consolidation of multiple cash
accounts or companies is proposed.

| Contract area | Proposed behavior |
| --- | --- |
| Authorization | Require current company access and `ledger/read`, matching the complete account-ledger scope. Counter-sales or receipt-entry permission alone does not grant access to the cash book. |
| Account | Require an explicit account ID at the service boundary. The UI may preselect the company's configured cash account, but must show the selection. Missing configuration must not silently select another account. |
| Historical access | Retain inactive cash accounts with company postings. Where saved posted account metadata proves a previous cash role, retain access after configuration/classification changes. Do not invent a past cash role where evidence is absent; the explicitly selected ordinary account ledger remains available. |
| Dates | The same optional inclusive date range and opening rules as the account ledger. |
| Included entries | Every posted journal line for the selected company/account, regardless of document kind. Include account invoices that used cash, explicit cash trades, receipts/payments, journals, openings, returns and reversals. |
| Excluded entries | Drafts and other company/account postings. Do not filter by `AMOUNT_RCV`, customer contact, current invoice mode or receipt/payment kind. |
| Detail rows | Business date, document number and kind, narration, debit/cash in, credit/cash out and exact running balance. Preserve document identity when number namespaces overlap. |
| Ordering | Existing deterministic ledger order: business date, posting sequence, posting time, document ID and journal line number. Running balances include every preceding row, including earlier pages. |
| Totals | Opening, full-period debit, full-period credit and closing. Keep original and reversal entries on their respective dates; do not net them away. |
| Counterpart details | Use explicit posted references or document drill-down. Do not invent a single customer/supplier counterpart for a compound journal. |

Selecting an account determines the ledger scope; an account's cash role must
not be used to discard some of its postings. Neither this report nor the city
report creates receipts, changes balances or allocates payments.

## Shared implementation and acceptance boundaries

Both proposed reports should use exact Decimal arithmetic, company-scoped
posted history, current labels clearly identified as current, and complete
totals independent of pagination. Read labels, filters, rows and totals from one
consistent snapshot. Follow the existing [report export boundary](../bifma/report_export.py):
new private files only, all matching rows with an explicit 100,000-row refusal
limit, visible filters, and fresh required-permission checks before publication.

These proposals reuse existing financial-report grants; they do not claim to
reproduce original per-user report rights. Any future dedicated report grants
must be explicit rather than inherited from transaction-entry permissions.

Qualification should compare city detail/grand totals to the same filtered
native trial balance and cash-book rows/running totals to the selected account
ledger. Cover unassigned/inactive accounts, changed city/cash setup, zero and
credit balances, ordinary and cash trades, journals/openings, dated reversals,
pagination and permission revocation during export. Original form/query parity
and target printing remain separate acceptance work.

No code, build inputs or tests were changed or run for this note. No historical
data or operational balances were imported or modified.
