# Trade charges and received amounts: recovered evidence

Reviewed 2026-10-02 while development builds were frozen. This is a source and
aggregate-data investigation, not a transaction change or migration proposal.
Only this document was added. The original dump, recovered archive, native
databases, application, tests and build inputs were not changed.

The supplied archive does **not** establish an additional labour, tax or partial
settlement calculation. Labour is never populated; the only tax-rate column is
in an empty purchase table; received amounts occur only on three full cash
sales. Direct full cash posting and separate ordinary vouchers already exist in
the native workspace. Their evidence should not be extended into an automatic
receipt, tax or labour policy.

## Scope and method

- Source: the 30-table schema, six views and 24 functions recovered in
  [legacy_schema.sql](legacy_schema.sql). No trigger, procedure or package
  creation records were found in this export; original Forms and report
  templates remain missing. This is a limit of the supplied material, not
  proof that the original application had no such logic.
  [Recovery scope](LEGACY_ANALYSIS.md#recovered-artifacts).
- Data: all dates and both companies in the independently reconciled archive,
  using `bifma.database.session(..., readonly=True)` and an explicit read
  transaction. Only field counts, type/mode counts and balance-consistency
  results are reported. No customer names, account identifiers, document
  identifiers, narratives, credentials or individual financial amounts are
  published here.
- Numeric inspection used exact `Decimal` values at precision 1,000; no SQLite
  floating-point `SUM`/numeric coercion was used for money. Ledger checks used
  `LegacyLedger` with the original grouping, `HAVING`, signs and rounding.
  [Reconstruction](../bifma/legacy_ledger.py).
- The archive SHA-256 remained
  `d5883f726438192e9e5331cb1b70ae2d4b081d8dcf72a8b2770cb692a92d83be`
  before/after inspection, matching [Oracle reconciliation](oracle_reconciliation.json)
  and [view parity evidence](oracle_report_parity.json). The original dump SHA-256
  recorded there is
  `d2cd806aa644320cf6d2eaa6192e7d6c840adcde8226f62b5ad1422980cc7e9e`.
  The data is not an approved opening-balance cutoff.

## Fields and observed use

`NULL` is kept distinct from a stored zero. A missing field is not counted as a
zero-valued charge.

| Table / field | Source type | Table rows | Populated | Stored zero | Positive | NULL |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `SMST.LABOUR` | `NUMBER` | 3,413 | 0 | 0 | 0 | 3,413 |
| `BDMST.LABOUR` | `NUMBER` | 1 | 0 | 0 | 0 | 1 |
| `PMST.GST_RATE` | `NUMBER` | 0 | 0 | 0 | 0 | 0 |
| `SMST.AMOUNT_RCV` | `NUMBER` | 3,413 | 3 | 0 | 3 | 3,410 |
| `SMST.DISCOUNT` | `NUMBER` | 3,413 | 0 | 0 | 0 | 3,413 |
| `SMST.BREAKAGE` | `NUMBER` | 3,413 | 76 | 0 | 76 | 3,337 |
| `SMST.FREIGHT` | `NUMBER` | 3,413 | 1 | 1 | 0 | 3,412 |
| `BDMST.DISCOUNT` / `FREIGHT`, each | `NUMBER` | 1 | 0 | 0 | 0 | 1 |
| `BRMST.BREAKAGE` | `NUMBER` | 436 | 218 | 0 | 218 | 218 |
| `BRMST.FREIGHT` | `NUMBER` | 436 | 48 | 1 | 47 | 388 |
| `CHART.DIS_PER` | `NUMBER` | 703 | 0 | 0 | 0 | 703 |

None of these populated numeric fields is negative or fractional in this
archive. Their Oracle `NUMBER` declarations themselves impose no monetary scale
or percentage validation. `PMST` and `PDTL` have no rows; this provides no used
purchase tax example. The single `BDMST` header has six detail rows, all with
zero quantity and rate, so its charge-sensitive accounting branches produce no
financial rows. Source declarations:
[sale](legacy_schema.sql#L73), [purchase](legacy_schema.sql#L58),
[BD](legacy_schema.sql#L13), [BR](legacy_schema.sql#L19),
[chart](legacy_schema.sql#L22).

Line discounts are different from header discounts. `SDTL.DIS_PER` is populated
on 3,356 of 55,405 rows: 3,335 positive, 21 zero, 52,049 NULL.
`BRDTL.DIS_PER` is populated on 773 of 3,563 rows: 695 positive, 78 zero,
2,790 NULL. All six `BDDTL.DIS_PER` values are NULL. The recovered views apply
line discounts as percentages. No used chart-default discount can be inferred
from the all-NULL `CHART.DIS_PER` column.

### Tax identifiers are not a tax calculation

| Field | Source type | Rows / populated |
| --- | --- | --- |
| `CONFIG.GST_NUM` | `VARCHAR2(18)` | 2 / 0 |
| `CONFIG.NTN_NUM` | `VARCHAR2(18)` | 2 / 0 |
| `CHART.STAX_NUM` | `VARCHAR2(18)` | 703 / 0 |
| `CHART.NTN_NUM` | `VARCHAR2(18)` | 703 / 0 |

These are identifier fields. They do not establish a percentage, taxable base,
tax amount, inclusion rule, tax account or applicable period. `PMST.GST_RATE`
is a separate numeric rate candidate, not an identifier, but has no observed
values and no recovered calculation using it. No corresponding sales tax-rate
or tax-amount column is declared in `SMST`/`SDTL`.
[Company declaration](legacy_schema.sql#L31).

The native company/account tax-identifier fields and their saved printable
snapshots are already implemented. Retaining or printing those identifiers
does not enable tax computation. [Current master contract](MASTER_SERVICES.md#optional-contact-and-tax-identifier-fields).

## Exact recovered calculation participation

In the following expressions, `B` means:

```text
ROUND(SUM(NVL(QTY * (RATE - RATE/100 * NVL(DIS_PER,0)), 0)))
```

It is computed over **each arm's own GROUP BY group**. Product-account groups
and the counterparty group are not interchangeable. Header adjustments can be
repeated on multiple product groups. `NULL` header adjustments use zero.

| Source arm | Debit | Credit |
| --- | --- | --- |
| `PUR_VW_DTL` PMST product | `B - DISCOUNT - BREAKAGE + FREIGHT` | 0 |
| `PUR_VW_DTL` PMST counterparty | 0 | `B - DISCOUNT - BREAKAGE + FREIGHT` |
| `SALE_VW_DTL` SMST counterparty | `B - DISCOUNT - BREAKAGE + FREIGHT` | 0 |
| `SALE_VW_DTL` SMST product | 0 | `B - BREAKAGE + FREIGHT` |
| `PUR_VW_DTL` BD product | 0 | `B - FREIGHT` |
| `PUR_VW_DTL` BD counterparty | `B + FREIGHT` | 0 |
| `SALE_VW_DTL` BR counterparty | 0 | `B - BREAKAGE - FREIGHT` |
| `SALE_VW_DTL` BR product | `B - BREAKAGE - FREIGHT` | 0 |

[Purchase/BD source](legacy_schema.sql#L985),
[sale/BR source](legacy_schema.sql#L1025).

`LABOUR`, `GST_RATE` and `AMOUNT_RCV` occur only in their table declarations in
the recovered SQL: **zero references in all recovered functions and views**.
The tax identifiers likewise have no function/view references. Consequently:

- None of those three numeric fields changes a recovered trade-ledger amount,
  produces a separate ledger entry, participates in its grouping or filters,
  or changes stock quantities/values in the recovered views.
- `LEDGER` rounds trade-view debit/credit amounts to whole units again after
  header adjustments. Journal detail amounts are not rounded there.
  [Final ledger source](legacy_schema.sql#L1107).
- The views have unequal `HAVING` conditions: a product group generally requires
  `B > 0`; selected party arms require `B - DISCOUNT > 0` or
  `B - BREAKAGE > 0`. They can suppress different sides. The sale product arm
  omits header discount; BD freight signs differ. None should be silently
  repaired when reconstructing history.
- `GET_PUR_RATE` selects stored opening/purchase rates using the original
  maximum-ID/fallback rules. It does not add labour/tax/freight or allocate
  header charges into cost. [Rate source](legacy_schema.sql#L573).

This proves the recovered database expressions. It does **not** prove that an
unavailable invoice form/print template ignored these fields, nor that Forms
code could not have written a separate journal. There is no source-backed
labour or GST algorithm to copy from these expressions.

## Cash/credit split and received-amount evidence

| `SMST` type / mode | Headers | `AMOUNT_RCV` populated | Headers with financial rows | Headers with no financial rows |
| --- | ---: | ---: | ---: | ---: |
| Type 8 / `C` cash sale | 5 | 3 | 3 | 2 |
| Type 8 / `R` credit sale | 3,407 | 0 | 3,398 | 9 |
| Type 9 / `R` source purchase-return branch | 1 | 0 | 0 | 1 |

All three populated received amounts are positive and equal the full final
sale-counterparty debit exactly. None is a partial payment or an overpayment.
The other two cash headers contain three positive-quantity, zero-rate lines;
the three priced cash documents contain eight nonzero-rate lines. All 11 cash
detail lines have NULL line discount. The five cash headers have NULL labour,
header discount, breakage and freight. Thus no cash charge/settlement combination
is exercised by the sample.

For `S_TYPE='C'`, the sale party arm directly debits the source's literal cash
account; for `R`, it debits the header account. `AMOUNT_RCV` does not reduce that
debit and does not add a receipt. `P_TYPE` supplies the equivalent purchase-side
cash/account selection. Both configured cash accounts in this backup match the
trade view's cash literal. Native full cash invoices already use explicit
company configuration and a distinct number namespace, with saved cash bindings
for returns/reversals. [Implemented cash contract](CASH_TRADES.md).

The archive also contains 524 cash-payment and 524 cash-receipt journal headers,
all with `CANCEL IS NULL`. `LEDGER` adds their cash counterpart from
`CONFIG.CHCODE_CASH`; it is derived from journal detail, not sale received
amounts. `JMST`/`JDTL` declare no invoice-allocation column or invoice foreign
key. [Journal columns](legacy_schema.sql#L43),
[journal references](legacy_schema.sql#L456).

As a narrow association check, there are zero receipt detail rows to configured
cash accounts, and zero same-calendar-day, same-company receipt detail matches
to the header account of the three received-amount sales. These counts are
consistent with direct cash posting. They are **not** proof about missing Forms
automation: cash receipt detail ordinarily names the counterpart account,
dates/amounts can coincide without a relationship, and no explicit allocation
key exists. No heuristic pairing is proposed.

There is no populated credit-sale received amount from which to infer partial
credit-invoice settlement, invoice allocation, tender/change or retained credit.

## Balance consistency and its limits

The reconstructed final `LEDGER` contains 28,525 rows, matching the independently
compared Oracle view multiset. Both companies balance exactly over all dates.
Grouping final entries by source family, company and transaction identifier gives:

| Source family | Headers | Headers with financial rows | Balanced among those | Headers with no financial rows |
| --- | ---: | ---: | ---: | ---: |
| SMST | 3,413 | 3,401 | 3,401 | 12 |
| PMST | 0 | 0 | 0 | 0 |
| BDMST | 1 | 0 | 0 | 1 |
| BRMST | 436 | 425 | 425 | 11 |
| JMST | 1,771 | 1,770 | 1,770 | 1 |

There are no negative debit/credit rows in these observed final entries. The
3,401 financial SMST documents each have one product-account group, as do all
425 financial BR documents. All sale header discounts are NULL; sale freight is
NULL or zero. The BD sample is zero-valued and the purchase tables are empty.
These facts explain why theoretical missing-discount, repeated-header and BD
freight anomalies are not exposed by the observed balance test. Positive BR
freight is exercised, but both BR arms use its same subtractive sign.

The Oracle comparison establishes equality for this dataset, including preserved
NULLs, grouping and duplicate rows. Empty purchase/BD financial branches and
unused labour/GST/partial-payment paths are **not** qualified by that equality.
No cancellation, hidden balancing entry or new charge rule should be invented
to explain a branch that has no financial rows.

## Implementation implications and remaining decisions

**Already established; do not ask the owner to decide again:** line discount is
a percentage in the original format; normal source trade party total subtracts
header discount/breakage and adds freight; native rounding is explicit; new
postings are corrected and balanced while history remains unchanged; full cash
trades post once to configured cash, with separate namespaces, saved bindings,
contacts, linked returns and exact reversals; ordinary receipts/payments are
separate balanced vouchers; tax identifier storage/printing is already present.

**No additional automatic charge or embedded settlement rule can safely be
inferred from this backup.** Do not add `LABOUR` to invoice totals or treat
`GST_RATE` as an inclusive/exclusive multiplier merely because a column exists.
Do not turn a cash invoice's received amount into a second cash receipt. Do not
label account balances as invoice amounts due or infer settlement from a matching
date, account or amount. Native transaction services currently reject unknown
labour/tax/received-cash fields; pure settlement arithmetic is not an implemented
posting/allocation lifecycle. [Service boundaries](TRANSACTION_SERVICES.md).

If the next requested increment is one of these financial workflows, the missing
decisions are specific:

1. **Labour:** a nonzero original invoice/print and its ledger effect, or an
   explicit new policy stating whether it is charged to the customer, paid as a
   separate expense, or informational; the posting account, sign and return
   treatment are needed. Its position in subtotal/rounding and any tax base is
   not recoverable here.
2. **Tax computation:** an actual taxed purchase/sale example or an approved
   rule defining rate and effective period, inclusive/exclusive treatment,
   taxable base after each discount/charge, rounding, ledger accounts and
   returns. Identifier values alone cannot supply any of those rules.
3. **Partial settlement on an account invoice:** whether the entered amount is
   tender, actual receipt, or an allocated payment; permitted payment accounts
   and splits; treatment of excess/change; receipt numbering/permissions; and
   how linked receipts and invoice corrections are reversed or reallocated.
   Existing separate vouchers already record an operator-approved cash movement,
   but they do not establish invoice-level paid/due or allocation.

A bounded convenience improvement could direct the operator from an account
invoice to the existing separate voucher entry, with an explicit amount/account
and ordinary review/posting. It must not guess an outstanding amount, post
automatically, or claim recovered embedded-settlement parity. That would reuse
an existing accounting rule; adding a new charge or allocation would require
one of the missing decisions above.
