# Native transaction service

`bifma.transactions.TransactionService` writes **new operational transactions**
in an explicitly initialized native database. It never promotes the recovered
archive, imports historical credentials, or changes original records. These
services are an implemented core, not proof of complete legacy-software parity
or production readiness of the entire application.

Schema 5 adds sealed original closings and invoice references, prepared by
`bifma.baseline` on normal startup. New postings use these carried quantities
and account balances without replaying historical documents. See
[BASELINE_INTEGRATION.md](BASELINE_INTEGRATION.md).

## Calls and authorization

```python
service = TransactionService(path, security=None, clock=time.time)
draft = service.save_draft(actor, company_id, kind, values,
                           document_id=None, expected_revision=None)
posted = service.post(actor, company_id, draft["id"], draft["revision"])
document = service.get(actor, company_id, document_id)
documents = service.list_documents(actor, company_id, kind=None,
                                   search="", offset=0, limit=100, status=None)
balances = service.stock_balance(actor, company_id, product_id=None,
                                  store_id=None, pool="regular", date_to=None)
reversal = service.reverse(actor, company_id, document_id, expected_revision,
                           document_number="REV-1", business_date="2026-10-02",
                           reason="Reason for the full correction")
number = service.next_document_number(actor, company_id, kind)
originals = service.list_return_sources(actor, company_id, "sale_return", search="")
original = service.get_return_source(actor, company_id, "sale_return", original_id)
```

`actor` is an authenticated SecurityService session/token, not a user UUID or
display metadata. IDs are canonical native UUID strings. Authorizations are
checked against current session state and grants inside the same `BEGIN IMMEDIATE`
transaction used for writes. Revocation, stale revisions, concurrent posting and
stock/return limits are therefore rechecked under the posting lock.

The module mapping is sale→sales, purchase→purchases,
sale_return→sales_returns, purchase_return→purchase_returns,
cash_sale→counter_sales, cash_purchase→purchases,
cash_sale_return→sales_returns, cash_purchase_return→purchase_returns,
breakage_receive→breakage_receive, breakage_dispatch→breakage_dispatch,
stock_opening→stock, journal/opening_balance→journals,
cash_receipt/bank_receipt→receipts, cash_payment/bank_payment→payments.
Draft creation uses `create`; editing uses `update`; posting and reversal use
`post`; reads use `read`. `list_documents(kind=None)` returns only permitted
kinds and their reversals. An omitted kind does not expand authorization.
Return source reads, draft saves and posts also require current read access to
the corresponding original invoice module, preventing a source-ID bypass of
the chooser's permissions.

`list_documents` optionally accepts `status="posted"` or `status="draft"`;
`None` keeps the combined list. Status, company, kind, permission and literal
number/notes search filters are applied before pagination. Results remain ordered
by descending business date, creation time and document ID. A return source
picker should request its original kind with `status="posted"`, then advance
`offset` over those results rather than removing drafts after fetching a page.
The page limit remains 1–500. A full page may have a following page; an empty page
ends the search. Separate page requests may reflect newly created/posted records.
Posted status alone does not certify return eligibility: source validation still
rejects reversed originals, exceeded capacities and other unsupported cases.

This transaction-service register describes working documents. The application's
module lists use `bifma.document_register.DocumentRegister` to merge original and
working documents with search/date filters before pagination. Its reads enforce
the same live company/module permissions and verify the associated original
archive. Original records remain separate immutable references, with no draft,
post or reversal actions. The return chooser uses `list_return_sources` and
`get_return_source`, which include eligible original references as well as new
posted invoices under both original-read and return-create permissions.

## Common input

Every draft takes an ordinary dictionary containing:

```python
{
    "document_number": "S-001",
    "business_date": "2026-10-02",
    "rounding_policy": "legacy_whole",  # or "cents", explicit
    "posting_places": 0,                # or 2, explicit
    "notes": "",                        # optional
    "lines": [...],
}
```

Dates must fall within the company's configured fiscal interval. Missing fiscal
dates prevent entry. A baseline-backed file also requires dates after its
sealed closing date. Number uniqueness is per company and document kind,
including preserved original numbers. Monetary
and quantity inputs use decimal strings, Decimal or integers; binary floats,
nonfinite values and invalid precision are rejected. All unknown keys are
rejected, including unimplemented tax, labour, received-cash and delivery fields.

Each draft contains 1–10,000 lines. Posting precision is independent of subtotal
rounding. No policy is inferred from old data or silently migrated. Saving an
edited draft replaces its draft lines and advances its revision; posted lines
and their IDs are immutable.

## Sales, purchases and corresponding returns

Kinds: `sale`, `purchase`, `sale_return`, `purchase_return`.

Explicit full-cash counterparts are `cash_sale`, `cash_purchase`,
`cash_sale_return` and `cash_purchase_return`. They have separate per-company
number namespaces and the same trade calculation/stock rules. Original cash
invoices require `account_id` to match the company's active configured cash
account. Returns inherit the corresponding original cash binding; no extra
receipt/payment is posted. See [CASH_TRADES.md](CASH_TRADES.md) for source evidence,
optional receipt contacts, snapshot validation, permissions and upgrade behavior.

```python
values.update({
    "account_id": party_account_uuid,
    "discount": "0", "breakage": "0", "freight": "0",  # optional defaults
    "lines": [{
        "product_id": product_uuid, "store_id": store_uuid,
        "quantity": "12", "rate": "25.50", "discount_percent": "0",
        # Required for returns; forbidden on ordinary sales/purchases:
        # "source_line_id": posted_original_line_uuid,
    }],
})
```

`quantity` is in the product's smallest stock unit. Unit labels and conversion
factor are snapshotted. The Sale Voucher may additionally send `unit_id` and
`entered_quantity` together; the service checks that this saved unit belongs to
the item and that conversion exactly equals `quantity`. These two display-input
values are kept in the document input snapshot for draft reopening and printed
sale detail. They do not change the stock/accounting quantity, and older API
callers may continue to provide base `quantity` alone. No unit or entered
quantity is guessed when the optional pair is absent. Other transaction kinds
do not accept this pair. Line discounts are explicit percentages. Master rate
changes do not overwrite the entered rate. Products must have active, postable
sale/purchase accounts as appropriate. The party account must differ from the
product posting accounts.

Ordinary `sale` also accepts optional `bill_number` text (maximum 80 characters,
no NUL). The supplied original Sale Voucher photo establishes this reference
field. Nonblank references are retained in input metadata and the issued
snapshot/HTML copy; they do not affect numbering, stock or accounting. Omitting
or clearing the field retains the earlier metadata shape. No schema migration
or historical rewrite is required.

The posted Sale Voucher HTML uses the saved item code/name, entry unit and
quantity, conversion factor, base quantity, rate, discount and value. It omits
the internal stock location from that bill view; the location remains in the
verified operational snapshot and stock posting. This is a native printable
layout, not a reproduction of the original Oracle report or printer dialog.

Corrected postings use `posting_rules.build_trade_posting`: one document total,
one application of header adjustments, exact proportional allocation across
account groups, and deterministic largest-remainder allocation. Sale/purchase
return debit party and credit product accounts; purchase/sale return reverse
those directions. Payments against account invoices use separate vouchers;
explicit cash trades already post their entire total directly to cash.

Normal trade movements affect the regular stock pool. Purchases and sale returns
increase quantity; sales and purchase returns decrease it. Each persisted
movement retains its own document-line reference and supported line quantity.
The aggregate effects produced by `posting_rules.build_stock_movements` are
appropriate for availability checks; they are **not** permission to collapse
multiple document lines into one database movement or exceed per-line limits.

### Return limits

Every returned line must reference a posted corresponding original in the same
company, for the same party, product, store, rate and line discount. The original
unit configuration and rounding policies must still agree. Product posting
accounts come from the original snapshot. A return cannot predate or reference
a reversed invoice. One return may reference only one original invoice.

Posted, unreversed returns consume original quantity and monetary capacity.
Both are rechecked while posting. Cumulative quantities cannot exceed any source
line. Cumulative document refunds cannot exceed the original posted total,
including cases where independent whole-unit rounding would otherwise overrefund.
Reversing a return restores its capacity.

**Owner-approved policy (2026-10-03):** staff enter return header discount,
breakage and freight amounts explicitly, including for adjusted original
invoices. The engine does not prorate them. Partial returns work only while
original quantity and money caps hold. A rounding residual that violates the
money cap is rejected, not silently reallocated. Full invoice reversal remains available when there
are no active linked returns and stock constraints permit it.

Eligible historical invoice references use the same input `source_line_id`
UUID; storage distinguishes these as `document_lines.historical_source_line_id`.
Source references are immutable, not native posted originals. Caps include
explicitly linked original returns; unlinked/ambiguous earlier returns or
conflicting original units prevent an automatic return instead of receiving
guessed capacity. These source issues remain visible for review.

## Standalone breakage receive and dispatch

Kinds: `breakage_receive` and `breakage_dispatch`. They use the common document
fields and product lines, including explicit quantity, rate and optional line
discount percentage. For example, a receive of two units at 10, with a 10% line
discount and a header breakage deduction of 1, totals 17:

```python
values.update({
    "account_id": party_account_uuid,
    "breakage": "1",  # receive only; optional and defaults to zero
    "lines": [{
        "product_id": product_uuid, "store_id": store_uuid,
        "quantity": "2", "rate": "10", "discount_percent": "10",
    }],
})
received = service.save_draft(actor, company_id, "breakage_receive", values)
```

| Kind | Product account | Party posting | Stock effect | Store rule |
| --- | --- | --- | --- | --- |
| `breakage_receive` | Debit active product `sale_account_id` | Credit explicit header `account_id` | Into `breakage` pool | All lines use one company-assigned store |
| `breakage_dispatch` | Credit active product `purchase_account_id` | Debit explicit header `account_id` | Out of `breakage` pool | Each line selects a company-assigned store |

The same exact calculation and allocation engine is used internally with
financial directions `sale_return` and `purchase_return`, respectively. This
does **not** turn breakage into a linked standard return. Each subtotal is rounded
once according to `rounding_policy`, the receive deduction is applied once to
the document, and final `posting_places` rounding and proportional account
allocation keep debits and credits equal. A deduction cannot make the total
negative. Quantity must be positive; rate may be zero and line discounts may
reach 100%. A zero-value document still moves stock and has no fabricated journal
rows. Floats, booleans, nonfinite values and blank numeric strings are rejected.

These initial breakage workflows require header `discount=0` and `freight=0`.
Dispatch also requires `breakage=0`; receive alone accepts a nonnegative
`breakage` deduction. Omitted headers default to zero. Supplying a nonzero
unsupported header fails explicitly. Tax, labour, received cash, delivery fields
and other unknown inputs are rejected even when supplied as zero. No cash account
or separate expense account is selected automatically.

Breakage entries are standalone: `source_line_id` is forbidden, including an
explicit null. No source invoice is guessed from party, product, date or amount.
They neither consume nor enlarge standard invoice-return capacities, and a
breakage line cannot serve as the original invoice for a standard return. Native
reversals remain the supported way to undo a posted breakage document; their
stored original-line/movement references are explicit reversal records.

Dispatch availability is checked only in the selected company/store/product's
breakage pool, including later running balances for backdated posts. Regular
stock cannot fund a dispatch, and received breakage cannot fund a regular sale.
Reversing a receive fails if it would leave consumed breakage stock negative.
All usual draft revisions, fresh module permissions, master snapshots, atomic
posting/audit and immutable reversal checks apply. Trade stock values remain
`unvalued`; invoice amounts do not assert an inventory cost or COGS policy.

### Source evidence and deliberate limits

The recovered [menu and entry-type catalog](LEGACY_MENUS.md#entry-types) confirms
Breakage Receive (`BRR`, type 11, `BRCV`) and Breakage Dispatch (`BRD`, type 12,
`BDISP`). The schema defines rate, quantity and `DIS_PER` on both detail tables.
`BRMST` has a header store and `BREAKAGE`/`FREIGHT`; `BDDTL` has per-line stores,
and `BDMST` has `FREIGHT`/`DISCOUNT`/`LABOUR` but no header `BREAKAGE`. See
[`legacy_schema.sql`](legacy_schema.sql#L9).

The `SALE_VW_DTL` BR branches explicitly debit item `EXP_CODE` and credit header
`CHCODE` using rounded line-net subtotal minus header breakage and freight
([source](legacy_schema.sql#L1045)). `EXP_CODE` is also the ordinary sale's product
account and the standard sales return's product account; it is not evidence of
a distinct breakage expense mapping ([sale](legacy_schema.sql#L1035),
[sales return](legacy_schema.sql#L986)). Native `sale_account_id` therefore
serves the same posting role. BR freight subtraction is known, but freight entry
is deliberately outside this initial breakage scope.

The `PUR_VW_DTL` BD branches credit item `PUR_CODE` and debit header `CHCODE`.
Their freight signs contradict: product credit subtracts freight while party
debit adds it. Header discount only appears in narration/grouping, and labour
does not appear in either amount expression
([source](legacy_schema.sql#L1005)). Requiring zero freight, discount and labour
avoids inventing their effect; unknown labour input is rejected rather than
accepted and discarded.

`BRDTL.VNO_SDTL` is a nullable FK to `SDTL`
([source](legacy_schema.sql#L563)); no recovered routine implements a linked
breakage-return policy. A count-only inspection of the recovered archive found
436 BR headers (all type 11), with all 3,563 detail references null. The one BD
header is type 12 and its six details have zero quantity and rate; it does not
resolve actual dispatch pricing or freight behavior. These observations support
the constrained standalone entry, not claims about missing original forms.

The recovered `STOCKB` is separate from regular `STOCK`, uses the BR header/BD
detail store and places both branches inward
([source](legacy_schema.sql#L1092)). For **new** transactions, dispatch is corrected
to outward under the owner's approved rule: correct new postings and preserve
history. Historical rows and historical stock/ledger views are unchanged.

## Opening stock

Kind: `stock_opening`.

```python
values.update({
    "contra_account_id": opening_equity_or_other_explicit_contra_uuid,
    "lines": [{
        "product_id": product_uuid, "store_id": store_uuid,
        "quantity": "12", "value": "153.75", "pool": "regular",
    }],
})
```

Pool must be explicit `regular` or `breakage`. Product inventory accounts must be
configured and differ from the explicit contra account. Value is supplied by the
operator and must exactly fit posting precision; it is not inferred from a sale
price. The inventory account is debited and the contra account credited. Zero
opening value is supported without zero-valued journal rows.

Opening value is labelled `opening_explicit`. No per-unit cost is inferred from
a potentially nonterminating division. Existing `unit_cost` storage remains zero
as an unused placeholder; its value is not a cost assertion.

## Manual journals and cash/bank vouchers

Kinds `journal` and `opening_balance` take:

```python
values["lines"] = [
    {"account_id": debit_account_uuid, "debit": "100.25", "credit": "0",
     "narration": "Optional line text"},
    {"account_id": credit_account_uuid, "debit": "0", "credit": "100.25"},
]
```

Every line is positive on exactly one side. Both totals must reconcile exactly
at the selected posting precision. Manual journals never alter stock.

Kinds `cash_receipt`, `cash_payment`, `bank_receipt`, `bank_payment` take:

```python
values.update({
    "account_id": cash_or_bank_uuid,
    "lines": [{"account_id": counterparty_uuid, "amount": "125.00",
               "narration": "Optional"}],
})
```

The header account must have the matching cash/bank kind. Receipts debit it and
credit counterparties; payments do the reverse. Amounts must be positive and
exactly representable. No default cash code is hardcoded. Vouchers preserve
excess payment on the party account; invoice allocation/linkage is not yet
implemented and is not implied by matching names or amounts.

## Posting, snapshots, inventory and reversals

Saving produces `values`, immutable-at-posting `snapshot`, and `calculation`
preview data. `get()` also includes native `lines` with IDs, actual posted
`journal_lines`, `stock_movements`, and any posted `reversal` reference.
`list_documents()` returns native header dictionaries. `stock_balance()` returns
company/store/product/pool with exact canonical quantity text; date_to is
inclusive. It does not report an inferred inventory valuation.

Posting re-reads the complete saved input, recomputes all amounts, compares stored
header/line/calculation values, and revalidates current master status, company
store membership, fiscal interval, return sources and permissions. Changed master
snapshots require resaving and review. Posted names, units, account labels and
calculation inputs remain unchanged when masters are subsequently renamed.

Availability checks begin at the exact imported quantity, if present, and scan
the full new dated running history per company, store,
product and pool. A backdated posting cannot make a later balance negative.
Already posted same-day events retain monotonic posting-sequence order, including
multiple posts in the same second. New same-day effects follow existing events.
An original negative quantity may be improved by an incoming movement; new
outward movements cannot deepen a shortage. Journals, individual stock movements,
immutable valuation snapshots, audit event and final posted status commit
atomically; failures roll everything back. The final status change advances the
revision and makes all document components and snapshots immutable.

Reversal creates a new posted document with exact opposite journal sides and
stock directions. It records original-document and original-movement references,
requires a reason, and cannot repeat or predate the original. Reversals cannot
themselves be reversed. Active linked returns must first be reversed. Current
references must remain active/postable, and stock availability is checked again.
No original posted record is edited or deleted.

## Migration and remaining scope

Native migration 2 is appended without changing migration 1's specification or
checksum. It adds document snapshots, posting sequence, stock pool and valuation
basis. Native upgrades remain explicit and create a verified pre-upgrade backup.
Existing pre-service stock rows retain NULL pool/order rather than receiving a
guessed classification. Reports and availability checks fail closed for affected
stock until a separately verified migration/reconciliation process is provided.

The old `stock_movements.value/unit_cost` trade placeholders are not costs.
Schema 5 new postings store valuation separately in `stock_valuation_snapshots`,
returned by `get()` as `stock_valuations`. The owner selected the original
purchase-rate policy. Each new movement saves an exact rate/value and provenance;
missing costs are NULL, not free goods. Historical accumulated values remain
unchanged. Reversal copies the original saved cost. See
[OPERATIONAL_REPORTS.md](OPERATIONAL_REPORTS.md) for the rate and P&L contracts.

Operational delivery linkage, stock transfers/adjustments, linked breakage
returns, breakage freight and dispatch header adjustments, tax, labour,
detailed invoice settlement allocation, partial corrections,
cross-invoice returns and automatic proportional header-refund allocation are not supported
here. Unknown parameters and unsupported kinds are rejected. Application-level
deployment, restore drills, broader UI workflows and original-system comparisons
remain separate release requirements.
