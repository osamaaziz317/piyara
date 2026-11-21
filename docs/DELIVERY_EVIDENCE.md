# Delivery challan evidence and implementation boundaries

Reviewed on 2026-10-02. This note records recovered source definitions and
count-only, read-only inspection of `data/legacy_recovered.sqlite3`. No business
records, financial amounts, private identifiers or contact details are included.
No delivery functionality was implemented during this investigation.

**There are no actual dispatches or delivery-linked invoices in the recovered
archive to verify.** The schema establishes a possible relationship between
documents; it does not establish the original conversion workflow. Missing
forms, reports and representative delivery transactions remain material evidence
gaps. See [legacy analysis](LEGACY_ANALYSIS.md) and
[Oracle reconciliation scope](ORACLE_RECONCILIATION.md#results-and-their-scope).

## What the recovered definitions establish

| Area | Proven source behavior | What it does not prove |
| --- | --- | --- |
| Document identity | `DCMST.TRNID` is the header primary key; `(DCNO,CMP_ID)` is unique. Each `DCDTL.VNO` is a globally unique detail key. | Automatic numbering, gap handling or fiscal-year resets. No relevant sequence or numbering function was recovered. |
| Header and lines | Headers store date, manual number, account, company, remarks and entry/update audit fields. Lines store product, store, large/small-unit fields, quantity and rate. | An original posting, approval, cancellation or closure lifecycle: these tables have no status, posted or cancellation columns. Required quantity/rate columns alone do not establish positivity, rounding or unit-conversion rules. |
| Invoice relationship | Nullable `SDTL.VNO_DCDTL` references `DCDTL.VNO`. The reference is at line level, not an invoice-header reference. | There is no unique constraint on the invoice's delivery reference, aggregate quantity cap, or linked company/account/product/store/unit/rate equality constraint. Multiple references are structurally possible; partial conversion being supported by the form is unproved. |
| Financial ledger | Delivery tables have no arm in `LEDGER`. `SALE_VW_DTL` does not filter on the delivery link, so linked sale lines still participate in ordinary invoice accounting. | When the original operator issued or converted a challan, or whether additional form-side actions occurred. |
| Physical stock | Active `STOCK` excludes sale details with a delivery link. A separate challan outward branch exists only inside a block comment. | A working stock-on-dispatch implementation. As exported, neither the challan nor its linked invoice contributes to this view. The commented branch suggests intent, not active behavior. |
| Returns | `PDTL` has a `VNO_SDTL` column; the recovered constraints do not declare an FK for that column. `BRDTL.VNO_SDTL` does have an FK to sale details. Neither is a direct delivery-return relation. | Whether a return restores unconverted challan capacity, only brings goods back, permits re-invoicing, or requires reversing the original conversion. |

Precise source locations:

- [DC header/detail definitions](legacy_schema.sql#L33),
  [primary keys and company-scoped number uniqueness](legacy_schema.sql#L218).
- [Sale detail definition](legacy_schema.sql#L69),
  [delivery and purchase-detail references](legacy_schema.sql#L404),
  [delivery detail foreign keys](legacy_schema.sql#L434).
- [Purchase-side detail definition](legacy_schema.sql#L54),
  [breakage return source reference](legacy_schema.sql#L563).
- [Invoice accounting](legacy_schema.sql#L1024),
  [active stock exclusion and commented challan branch](legacy_schema.sql#L1076),
  [ledger composition](legacy_schema.sql#L1106).
- [Machine-readable schema, constraints and all 24 functions](legacy_schema.json).
  None of those functions references `DCMST`, `DCDTL`, `DCTOSALE_BAL` or
  `VNO_DCDTL`. The recovered sequence list is empty.
- [Recovered menu assessment](LEGACY_MENUS.md#external-assets-and-parity-gaps):
  no delivery-challan menu appears among the 46 recovered definitions. This
  does not prove that a form could not launch it indirectly.

## Why `DCTOSALE_BAL` is not an outstanding-delivery report

The [exact view](legacy_schema.sql#L969) is a `UNION ALL` of dispatch and invoice
activity, with separate dispatched/sold quantity and value columns. It contains
no final grouping, subtraction or remaining-quantity constraint.

Both arms require positive raw `QTY * RATE`. Consequently zero-value goods are
excluded even when quantity is positive, and line/header discounts are not
reflected in these raw values. The invoice arm requires `S_TYPE='R'` and joins
the product master, but **does not require a delivery link or filter
`TRN_TYPE`**. Cash rows are excluded. There is no purchase-side sales-return or
breakage-return arm and no cancellation filter.

An application consuming the view could have added further filters, grouping or
return adjustments; no such consumer was recovered. In particular, rows with
`DC_VNO IS NULL` must not be presented as outstanding or over-converted challans.
Grouping all those unlinked invoices into one NULL delivery bucket and
subtracting their quantities would invent an obligation that the view does not
establish.

## Count-only archive findings

These counts cover the complete recovered archive, with no approved cutover
date implied. Inspection used the repository's read-only database session.
The positive-value predicate was evaluated with exact Decimal arithmetic,
without converting stored values to binary floating point.

| Check | Count |
| --- | ---: |
| Delivery headers, `DCMST` | 0 |
| Delivery details, `DCDTL` | 0 |
| Sale-side details, `SDTL` | 55,405 |
| Sale-side details with non-NULL `VNO_DCDTL` | 0 |
| Sale headers containing a delivery link | 0 |
| Purchase-side headers/details, `PMST` / `PDTL` | 0 / 0 |
| Rows qualifying for the invoice arm of `DCTOSALE_BAL` | 55,230 |
| Qualifying invoice-arm rows without a delivery link | 55,230 |
| Qualifying invoice-arm rows with type 8 | 55,230 |
| Credit details excluded by the positive-value predicate | 164 |
| Cash details excluded by the credit-only filter | 11 |

The earlier [Oracle view export receipt](oracle_report_parity.json) also records
55,230 `DCTOSALE_BAL` rows. That view was exported but **was not directly compared
to a Python counterpart**. The exact real-data matches for `STOCK`, `STOCKB`
and `LEDGER` cannot qualify empty dispatch/link branches. Matching empty source
tables proves that no rows were lost; it does not prove conversion, cancellation
or return behavior. The same absence is recorded in
[migration readiness](MIGRATION_READINESS.md).

## Bounded future implementation options

**Source-compatible read-only work can proceed without choosing business
policies.** A challan header/detail browser can expose the recovered structure.
An exact activity reconstruction can preserve both view arms, raw-value filters,
NULL links and multiplicities. It should be labelled source activity, not an
authoritative pending-delivery or valuation report. Existing data will contain
no actual challans; synthetic fixtures would need to cover the absent branches.

**A restricted corrected-native workflow is technically possible, but its
lifecycle must be described as a new interpretation.** A proposed first scope
is one immutable dispatch followed by complete, matching line conversion to one
credit invoice: physical stock leaves at dispatch, the invoice posts its
financial effects without moving the same stock again, and a transactional
reservation prevents a second conversion. Stock-out at dispatch is supported
by the commented source branch and the project's corrected-new requirement;
it is not a reproduction of the active exported stock view.

The existing [pure delivery-link primitive](../bifma/posting_rules.py#L335)
already requires a complete product/store/quantity match and avoids duplicate
stock effect within one call. It does not authenticate or reserve persisted
links. A future service must add company/account/date/unit checks, live
permissions, immutable snapshots, availability checks, concurrency protection
and persisted allocation/reversal references. The native schema's reserved
`delivery_challan` kind is not an operational service implementation.

Such a restricted increment should reject partial, mixed-source and cash
conversions and delivery-linked invoice returns until their rules are decided.
A proposed reversal boundary is to reverse the linked invoice before reversing
its dispatch; whether returns reopen invoice capacity remains unresolved.
These restrictions and lifecycle choices are proposal boundaries, not recovered
form behavior. Do not infer them from the misleading view name or silently
apply them to historical data. Full parity still needs original forms/report
consumers or representative dispatch, conversion, cancellation and return
examples, or explicit decisions defining the replacement behavior.
