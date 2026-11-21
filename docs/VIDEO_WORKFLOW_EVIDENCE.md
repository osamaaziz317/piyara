# Supplied Oracle video and photograph evidence

Reviewed 2026-11-20 against the current source. This records visible evidence,
not a claim that every original module has been reproduced. All five supplied
videos were inspected in order through the 156 available one-second frame
samples, with closer inspection of the changing forms and all three JPEGs.
The Sale Bill/New transition at approximately 23–34 seconds was additionally
sampled at ten frames per second (112 frames). It confirms the report runner
and return/reset sequence, but the low-resolution recording still does not
identify every initiating keystroke or establish an unseen posting boundary.
The recordings are photographs of a screen: some labels and amounts are too
small to read reliably. A transition establishes what appeared next; it does
not identify a particular key or mouse button when the input itself is unseen.

## Source index

All source files are in `videos/`. Clip numbering follows filename order.
Times below are approximate seconds from each clip's beginning, using the
extracted frame samples under `/tmp/bifma-video-review/clip1` through `clip5`.
Those temporary frames are review aids, not required application assets.

| Clip | Filename | Samples inspected |
| --- | --- | --- |
| 1 | `WhatsApp Video 2026-11-16 at 12.24.38 AM.mp4` | 0–15 seconds |
| 2 | `WhatsApp Video 2026-11-16 at 12.24.49 AM.mp4` | 0–19 seconds |
| 3 | `WhatsApp Video 2026-11-16 at 12.25.06 AM.mp4` | 0–30 seconds |
| 4 | `WhatsApp Video 2026-11-16 at 12.25.26 AM.mp4` | 0–39 seconds |
| 5 | `WhatsApp Video 2026-11-16 at 12.25.47 AM.mp4` | 0–48 seconds |

## Visible navigation and actions

| Source/time | Observed behavior | Evidence boundary |
| --- | --- | --- |
| Clip 1, 0–15s | Home contains the large BIFMA TRADERS wordmark, with Backup and Exit near the bottom. A coding menu is briefly visible around 4s; Opening Stock can be read. | The camera cuts off most of the top menu in this clip. It does not demonstrate backup completion or any transaction. |
| Clip 2, 2–4s | Coding opens Financial Coding, Inventory Coding and City Definition. Inventory Coding exposes main groups, subgroups, items, units and opening stock. | Menu labels establish destinations, not Save/Edit rules or the account hierarchy. |
| Clip 2, 5–7s | Financial Transactions menu opens; Opening Balance, Cash Payment Voucher, Cash Receipt Voucher and Journal Voucher are visible. | No voucher is entered in this clip. |
| Clip 2, 8–9s and 18s | Inventory exposes Purchase, Counter Sale, Sale Credit, Purchase Return, Sale Return, Breakage Receive and Breakage Dispatch. | Sale Credit is a menu label; the form title demonstrated in clip 5 is Sale Voucher. |
| Clip 2, 10–12s | Reports → Financial Reports opens a submenu containing Voucher Printing, Cash Statement, Account Ledger, Trial Balance, Sub Trial Balance, City Wise Recovery report and Profit And Loss Report. | Most of these reports are not run in the supplied recordings. |
| Clip 2, 13–15s | Reports → Inventory Reports exposes Stock Summary, Stock Summary With Value, Item Ledger, Purchase / Purchase Return Detail and Sale / Sale Return Detail. | No inventory summary/detail report is generated in this clip. |
| Clip 3, 0–2s | A compact CASH BOOK dialog has an Urdu title, one date, company selector, View and Exit. | It is not a date-range form. |
| Clip 3, 3–11s | View leads to a separate Oracle report runner window while the result loads. | The launcher itself contains no report transactions to copy. |
| Clip 3, 12–22s | The Cash Book parameter dialog is visible again with the chosen date/company. | The samples do not prove whether returning here was automatic or invoked by the operator. |
| Clip 3, 23–30s | Cash report preview appears, headed All Companies, with Urdu headings, two independent side-by-side tables and a report date. Scrolling reveals totals. Each side contains narration/account text, voucher type/number and amount. CPV/CRV references can be seen. | Exact row values and every Urdu column title cannot be reliably transcribed from this low-resolution recording. Receipt/payment placement must follow labeled output, not a guess from the English left-to-right screen order. |
| Clip 4, 0–2s | PARTY LEDGER REPORT has Date From/To, party/account lookup, company, a balance area, View and Exit. The dates are edited before choosing the party. | This is distinct from the single-date Cash Book filter. |
| Clip 4, 3–8s | Party Find opens with an editable Find query and account choices showing English names and Urdu text. Typing a BIL-prefixed term changes the matches; a highlighted account is selected. | The recording does not prove prefix-only matching rather than another matching rule. The application may match code and name without changing stable IDs. |
| Clip 4, 9–11s | Selecting the party closes Find, fills its name/code and displays a balance of Dr 109,336.00. View starts the report runner. | Whether that displayed amount is an all-time or date-bound balance is not established by these samples. |
| Clip 4, 12–16s | External report runner is visible. | Loading a report need not reproduce this delay in the Python application. |
| Clip 4, 17–28s | The selected parameter form is visible again. | No data change is demonstrated. |
| Clip 4, 29–39s | A paginated Urdu account ledger opens. Visible columns represent document/voucher reference, date, narration, debit, credit, running balance and its Dr/Cr marker. Header includes company/party and a date range. The operator scrolls through rows and navigates report pages, revealing totals and a closing balance. | The exact historical values and all page labels cannot be verified from the pixels alone. The report does not establish an account parent/child tree. |
| Clip 5, 2–5s | Inventory → Sale Credit opens a blank Sale Voucher. Date receives focus. Header contains Sale #, Bill #, Buyer, Remarks and company, with New/Bill/DC/Exit beside a separate Stock display. | DC is visible but is not operated; its behavior remains unproved. No Store control is visible. |
| Clip 5, 6–9s | Buyer Find opens; its query changes matching party names. Choosing a party fills the buyer code and title, then returns to the voucher. | The video does not expose the physical keys used between all samples. The owner's explicit Enter-navigation request supplies that keyboard requirement. |
| Clip 5, 10–18s | A larger Item Find opens; typing filters the choices. Selecting the item returns to an inline row. Unit, quantity, factor, rate, discount and value fields are visible. Stock displays −21 for the selected item. A first line becomes populated. | A negative source stock display is evidence of the original value; it does not override the owner's approved policy for new stock postings. |
| Clip 5, 20–24s | Item Find opens again, narrows its choices, then returns to a second inline item row. Stock shows −26 for that selected item. Voucher totals change as the second row is entered. | Exact item amounts are insufficiently legible for a new calculation rule. |
| Clip 5, 26–29s | External report runner appears after the populated voucher. | The exact initiating click is between samples. |
| Clip 5, 30–41s | The sale form returns, then its rows/header are cleared to a fresh-entry state and Date is focused. | This shows reset/new-entry behavior, not proof of every database commit boundary. |
| Clip 5, 42–44s | A compact Urdu sale bill preview appears, with BT heading, party/date/references, line table and amount summary. | The high-resolution sale JPEG is the stronger source for exact printed columns. |
| Clip 5, 45–48s | Standard Windows Print dialog overlays the bill, then closes back to the preview. | Printer selection and operating-system chrome are platform facilities, not business database logic. |

## High-resolution photographs

`WhatsApp Image 2026-11-16 at 12.24.50 AM.jpeg` shows SUB TRIAL BALANCE:
one as-of date (12-11-26), a subgroup selector defaulting to All and a company
field. Visible choices include ADVANCES, BANK FINANCE, CASH AT BANK, CASH IN
HAND, COMMISSION AGENTS, CREDITORS, DEBTORS, EMPLOYEE LOAN and FINANCIAL
EXPENSES A/C. This establishes a real grouping filter. It does not establish
which account-code prefixes or parent records belong to those groups; use
verified stored relationships and explicit reviewed mappings.

`WhatsApp Image 2026-11-16 at 12.24.51 AM.jpeg` shows SALE_BILL_DIS preview:

- BT heading, sale date 11-NOV-26, SR 3610, sale number 261141, CR marker,
  account code, Urdu party name and remarks.
- Right-to-left line presentation: serial, DC #, description, packing shown as
  count × factor, quantity, rate, discount percent and net amount.
- Summary for gross amount, discount, net amount and balance amount.
- A single-page preview with Print, zoom and page navigation controls.

`WhatsApp Image 2026-11-16 at 12.24.51 AM (1).jpeg` shows BRCV_BILL preview:

- BT heading, received date 15-OCT-26, received number 26117, party code/name
  and a remarks area.
- Serial, description, packing count × factor, quantity, rate and amount.
- Four amounts 600, 140, 400 and 500 total 1,640; Breakage 140 is subtracted,
  leaving 1,500. This supports the observed breakage deduction on this copy.
  It does not prove an unused tax/labour workflow or a general freight rule.

## Exact arithmetic check and limits

The sale photograph supplies a useful counterexample to treating print
formatting as the accounting formula. All seven lines show 15% discount:

| Quantity | Rate | Exact net | Printed net | Independently rounded line discount |
| ---: | ---: | ---: | ---: | ---: |
| 26 | 625 | 13,812.5 | 13,813 | 2,438 |
| 4 | 625 | 2,125 | 2,125 | 375 |
| 2 | 925 | 1,572.5 | 1,573 | 278 |
| 4 | 460 | 1,564 | 1,564 | 276 |
| 1 | 260 | 221 | 221 | 39 |
| 2 | 485 | 824.5 | 825 | 146 |
| 4 | 180 | 612 | 612 | 108 |

The photographed gross is **24,390**, discount **3,660** and net **20,733**.
Summing independently half-up-rounded line net amounts gives 20,733; summing
independently half-up-rounded line discount amounts gives 3,660. These displayed
figures do not reconcile: 24,390 − 3,660 = 20,730. The exact line net sum is
20,731.5. Thus this photograph is evidence of independently rounded printed
columns, not a safe replacement for the stored party total.

The recovered `SALE_VW_DTL` party arm in `legacy_schema.sql` instead uses:

```text
ROUND(SUM(QTY * (RATE - RATE/100 * DIS_PER)))
    - header_discount - breakage + freight
```

The enclosing `LEDGER` view rounds the resulting DR/CR again. Current
`calculate_invoice(..., rounding=LEGACY_WHOLE)` returns **20,732** for this
synthetic seven-line example with zero headers, matching the recovered aggregate
formula. `build_trade_posting` balances both sides against the same final party
amount. Do not change posted amounts to 20,733 or independently rounded print
totals just to copy this photograph. Original exports must retain the original
recorded/recovered party amount; native exports must retain their posted amount.

Further source checks against current code:

- Original `SALE_VW_DTL` omits header discount on one counterpart arm and repeats
  headers when grouped by product posting account. The current balanced
  allocation is an owner-approved correction for new transactions, not a parity
  defect to reintroduce.
- Original `LEDGER` cash voucher arm uses type 3 receipt credits as the balancing
  cash debit and type 2 payment debits as the balancing cash credit. It excludes
  cancelled headers. Keep that economic direction and the existing posting
  guards; the video does not demonstrate new cancellation semantics.
- Original `GET_PUR_RATE` chooses the highest eligible numeric opening/purchase
  detail identity through the transaction date, across stores; sales-return cost
  is fallback. Current baseline cost provenance and new posting-sequence choice
  preserve that method without rewriting historical values. Discounts and
  header charges do not alter the raw inventory rate.
- Original `STOCKB` counts both receive and dispatch as incoming. The owner
  approved corrected new dispatch direction while preserving original values.
- A concrete Sale Voucher draft defect was found and fixed in this work: an
  initially empty unit selector discarded a saved large-unit ID. Reopening now
  retains its selected unit, exact entered quantity, factor and base quantity.
  No additional incorrect new arithmetic rule was established by this review.

## Data and parity boundaries

The recovered baseline closes **2026-09-27**. Read-only inspection found no
source sale numbered 261141 and no source breakage receive numbered 26117.
The October/November photographs and report dates are later than that snapshot.
Their later transactions cannot be regenerated from the September archive or
created as dummy business data. A matching layout can be rendered from available
verified data; matching those later balances requires their actual records.

Account and item ledgers currently carry the explicit cutoff opening snapshot
plus new postings. They do not claim reconstructed pre-cutoff period turnover.
Stock summary/value reports can combine verified original movements and new
activity across the cutoff. These are different report contracts; a photo date
picker cannot safely remove that distinction.

The supplied clips demonstrate menus, cash-book parameters/output, account-ledger
parameters/output, party/item Find, inline sale entry, reset, bill preview and
the operating-system print dialog. They do not demonstrate complete save/edit/
delete behavior for every coding, purchase, return, voucher, administration or
report screen. F8 draft preservation, explicit review/post, corrected new
transactions and permission checks remain the existing authorized contracts.
No account hierarchy, hidden form action, DC posting, previous-balance option,
unused charge or missing historical record is inferred from an unreadable frame.
