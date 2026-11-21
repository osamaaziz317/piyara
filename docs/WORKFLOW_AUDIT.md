# Original application workflow audit

Reviewed 2026-10-04. The owner requested a portion-by-portion comparison of
appearance and working behavior with the original software. This audit records
what can currently be established, rather than treating every recovered column
as an implemented or required operator workflow. It covers all **35 distinct
module tokens** in the recovered menu catalog.

## Evidence available

- **M — Menu catalog:** [LEGACY_MENUS.md](LEGACY_MENUS.md) and
  [legacy_menus.json](legacy_menus.json), 46 definitions and 35 file tokens.
  Tokens prove declared modules and parent relationships, not form contents,
  screen order for each user, validation, keyboard events or print templates.
- **D — Database/source:** [LEGACY_ANALYSIS.md](LEGACY_ANALYSIS.md),
  [legacy_schema.sql](legacy_schema.sql) and the verified recovered archive.
  These establish columns, keys, stored expressions and recorded values.
  [Oracle reconciliation](ORACLE_RECONCILIATION.md) verifies recovered content;
  it does not recover missing application code.
- **P — Original screenshots and video supplied by the owner:** the 2026-10-03
  Cash Receipt Voucher, Cash Payment Voucher and Coding menu photos; the
  2026-10-04 Sale Voucher photo; and the 2026-11-18 ZIP with five MP4 clips
  and three JPEGs. The newer clips show top-menu navigation, Cash Book's
  date/company filter and output, Party Ledger's Date From/Date To and
  party/account Find plus output, Sale Voucher buyer/item Find, Bill preview
  and a Windows printer dialog. These establish only the captured screens and
  actions; they do not show every validation, calculation, popup result or
  form/report.
- **U — Explicit owner instructions:** one launcher/codebase, original baseline,
  corrected new postings with preserved history, no new unsupported workflows,
  top menus, new-entry first and F8 saved records, live Find, automatic item
  codes without account dropdowns, Enter-driven sale entry, and saved-sale date
  filters. Return adjustments and purchase-rate costing are already approved;
  they do not need another decision.

`BT-270926.EXE` is the Oracle export, beginning `EXPORT:V10.02.01`, not the
original UI executable. The newer video supplies partial visible-workflow
evidence but not form source or hidden behavior. `reference/original_v2_source.py.txt` preserves a small
Python prototype, not the Oracle application's form source. No `.fmb`, `.fmx`,
`.rdf`, `.rep`, `.pll`, `.mmb` or `.mmx` files were found in the workspace during
this audit. The original installed program/source location has been requested;
access to it has not yet been established. Do not claim to have read the
original application interface code from either of the supplied files. The
owner subsequently asked whether the EXE alone was sufficient; the response
explained this backup/form distinction. No original installed program or form
source path is available from that reply.

## Module comparison

“Available” below means usable native behavior exists. It does not mean exact
original parity. Audit changes marked implemented have focused checks; final
whole-application results are recorded separately. The final column identifies
evidence needed for comparison, not authorization to invent extra functionality.

| Original module | Available original/user evidence | Current implementation and gap | Specific remaining original evidence |
| --- | --- | --- | --- |
| `UM_CATALOG` | M; D user fields; U new personal owner login | Available user creation, roles, enable/disable and secure passphrase/reset. Does not reuse old credentials. | Original user form and a create/edit/disable walkthrough; runtime visibility/session behavior. |
| `UM_USER_PRIVS` | M; D insert/update/delete/query flags and menu references | Available live company/module/action grants. Native actions are not claimed as an exact original flag mapping. | Original rights screen plus examples of menu visibility and allowed/denied actions for a restricted user. |
| `CONFIG` | M; D company/fiscal/cash/contact fields | Available company form and switching; unsupported integration/alternate configuration behavior is not invented. | Original configuration screen and which settings affect entry, company selection and fiscal dates. |
| `CITY` | M; D city codes and bilingual names; P Coding menu | Available new-entry/F8 city master. Original layout/code defaults remain unverified. | City Definition screen and a create/edit/search example, including code entry and duplicate behavior. |
| `CHART_LEVEL1` | M distinct Level 1 token; D recorded `CHLEVEL` | One generic Accounts form, no separate source Level 1 screen. Native import does not map source levels or infer parents. | Level 1 form and a new-account example establishing code allocation and allowed edits. |
| `CHART_LEVEL2` | M distinct Level 2 token; D recorded `CHLEVEL` | Same generic Accounts form; parent hierarchy is explicitly native, not a recovered original rule. | Level 2 form and parent-selection/code example; treatment of existing accounts. |
| `CHART_LEVEL3` | M distinct Level 3 token; D postability/city/contacts; U party fields belong in account entry | Available postable-account/contact/city editing; original level and account-type/default-discount rules unverified. | Level 3 form, account-kind meaning and code/default behavior; a populated discount example only if that workflow is used. |
| `MAIN_ITEM` | M; D main-group table; P separate Main Item Groups menu | Separate main-group entry/F8 route now implemented and checked; current root records reused, no parent field or automatic hierarchy rewrite. | Original main-group screen and code-entry/edit details for exact form parity. Separate menu is established by M/P. |
| `SUB_ITEM` | M; D required FK to main group; P separate Item Sub Groups menu | Separate subgroup entry/F8 route now implemented and checked. Root-only parent choices and transactional save guard; existing IDs/product links retained. | Exact subgroup screen/code-entry/edit behavior. Main-group linkage itself is established by the FK. |
| `PUR_ITEMS` | M; D product/unit/rate fields; U automatic code and no routine account selectors | Available owner-specified simple Item form, exact next numeric code, known/unknown prices and guarded posting defaults. | Original item form and edit/default/large-unit workflow. Stored account mappings do not prove an account-picker UI. |
| `OPEN_STOCK` | M; D dated company/store/item quantity/rate; P Coding menu | Available explicit native quantity/value/store/pool opening; carried source balances are imported independently. Generic line editor. | Original opening-stock screen, entry/edit treatment and its accounting effect; original print if any. |
| `OPEN_BAL` | M; D type 6 journal storage/ledger | Available balanced opening voucher using generic debit/credit lines. | Original opening-balance form and save/edit workflow, number/date defaults and a resulting ledger example. |
| `CPV` | M; D type 2 detail debit plus configured-cash credit; P payment screen | Available inline photo-based payment form, exact total, F8 originals/new records and Enter number → date → account Find → narration → amount → next account. Save retains draft review/post. | Original Save/Print/Cancel and Previous Balance behavior, amount validation and a printable voucher. |
| `CRV` | M; D type 3 detail credit plus configured-cash debit; P receipt screen | Available corresponding inline receipt form, Enter flow and F8 records. No invoice allocation inferred. | Original Save/Print/Cancel and Previous Balance behavior, amount validation and a printable receipt. |
| `JV` | M; D journal detail debits/credits and active-header filter | Available exact balanced journal with generic line editor and immutable posting. New reversal entry is removed by U. | Original Journal Voucher screen, imbalance validation, save/edit/cancel behavior and print. |
| `PUR` | M; D purchase type 7 and recovered expressions; no purchase rows in supplied backup | Available generic purchase entry, known stored-rate action, discounts/adjustments and corrected balanced posting. | Original Purchase form, a representative entry/print and resulting stock/ledger. No tax/labour algorithm follows from an unused column. |
| `SALE_CREDIT` | M; D sale type 8/account mode and formulas; P Sale Voucher photo and 2026-11-18 Find/Bill clips; U Enter/unit/quantity/date filters | Available inline Sale Voucher, live Find/Enter flow, optional Bill #, exact totals and automatic F8 range. Selected unit/entered quantity are saved and validated against base quantity; sale HTML shows them with no Store column. Uses native review/post. | Exact original Save/Bill/DC and print template, previous balance, edit/cancel and printer behavior. A static DC button is not a conversion specification. |
| `PUR_RET` | M; D type 9 outward stock; U capped staff-entered return adjustments | Available eligible original/native linked returns with quantity/money caps. Generic Enter navigation; rejected original selection leaves the picker available and preserves entry. | Original purchase-return form and referenced invoice/quantity/rate/edit/print walkthrough. |
| `SALE_RET` | M; D type 10 inward stock; U same return policy | Available eligible original/native linked returns preserving source history. Generic Enter navigation and retained picker after rejection. | Original sale-return form and referenced invoice/quantity/rate/edit/print walkthrough. |
| `SALE_COUNTER` | M; D cash mode and observed full received amounts | Available full direct-cash invoices with separate numbering/access/contacts and linked cash returns; generic form. | Original counter-sale form/print and a used tender or payment workflow. Partial settlement/change/split payments are not established. |
| `BRCV` | M; D type 11, header store, linked-source field and BR expressions | Available standalone one-store breakage receive with line discount/header breakage deduction. Source links and freight not supported. | Original Breakage Receive form and a linked-sale/freight example with stock/ledger effects. |
| `BDISP` | M; D type 12 and BD expressions; U corrected new outward stock | Available standalone breakage dispatch, per-line stores and line discounts; ambiguous charges rejected. | Original dispatch screen and nonzero example explaining freight/header adjustments and save/print behavior. |
| `VOUCHER` | M Voucher Printing report; P entry Print buttons | Reports → Voucher Printing provides section selection/F8 browsing. Original/native saved documents export HTML; originals preserve source labels, unknowns and cancellation markers after fresh archive/access checks. | Original Voucher Printing parameter screen, representative printouts, selection/range behavior and printer dimensions. |
| `CASH` | M Cash Statement; D configured-cash ledger entries; P 2026-11-18 date/company filter and output | Direct single-date filter, configured eligible cash account by default, cutoff opening and posted activity with running balances and full output. Account Find appears only if no valid default exists. Earlier original entry stream is not reconstructed by this screen. | Exact original balance inclusion/sign rules, treatment of opening/receipt/payment and print layout. |
| `LEDGER` | M; D independently verified source ledger; P 2026-11-18 Date From/Date To, party/account Find and output | Direct Date From/Date To and live account Find; uses the existing cutoff opening and exact native open/period/close ledger with full output. Earlier turnover is not reconstructed by this screen. | Exact original balance inclusion/sign rules, ordering/grouping and printed closing-balance placement. |
| `TRIALBALANCE` | M; D source ledger and recorded accounts | Available exact carried/new totals. Original grouping/code ranges and cutoff-period presentation not certified. | Original trial-balance parameter screen, detailed/grouped sample and zero/inactive-account rules. |
| `SUB_TRIAL` | M; D recorded chart levels, no explicit account-parent FK | Available native current-parent rollup. No source account hierarchy is guessed during import. | Original grouping/parent-code specification and a sub-trial print with totals for comparison. |
| `PLS` | M report name only; D ledger/stock inputs; U original purchase-rate cost | Available guarded periodic trading profit; unknown classifications/costs prevent an asserted number. Original formula is absent. | Actual original P&L query/template or a reconciled report with its account/stock classifications and period rules. |
| `CITY_RECOVERY` | M; D account→city FK and label functions, no recovery query | Available carried/new account balances grouped by current city. No invoice aging/settlement is inferred. | Original selection/filter screen and print defining recovery scope, zero/credit treatment, ordering and dates. |
| `STOCK` | M; D verified `STOCK`/`STOCKB`; U historical values preserved | Available original/new dated quantities including earlier/cross-cutoff ranges, separate stock pools and exact totals. | Original stock-summary filters, regular/breakage selection, grouping, unit presentation and printed sample. |
| `STOCK_VALUE` | M; D `GET_PUR_RATE` and valued movements; U purchase-rate method | Available exact historical movement values plus frozen new costs, with explicit unknowns. | Original value-report parameters, selected stock view, displayed rates/totals and print layout. |
| `ITEM_LEDGER` | M; D dated item/store source movements | Available cutoff opening plus new item movements and exact running quantity/value. Earlier original turnover is not reproduced by this screen. | Original item-ledger filters/order, unit/rate display and a print across a known opening/transaction period. |
| `PUR_DETAIL` | M report name; D purchase/return headers and lines | Verified original/native posted detail and full HTML now available. All-date defaults include originals; explicit/cleared dates persist. Unknown original amounts stay Unknown; header totals are omitted under line filters. | Original parameter screen/template, purchase-return signs, account/cash selection and document/line total treatment. |
| `SALE_DETAIL` | M report name; D sale/return headers and lines | Corresponding verified original/native detail, all-date defaults and full HTML. Recorded original labels/values are retained separately from corrected new postings. | Original parameter screen/template, return signs, account/cash selection and discount/header-total display. |
| `UOM` | M inconsistent report classification; D bilingual UOM master; P Coding placement | Available unit master under Inventory Coding, following the photo and U. No inferred report workflow. | Original UOM form, code/default/edit behavior and any actual print action. |

## Observations that must not become guessed rules

A managed read-only aggregate query of the archive on 2026-10-04 found 10 Level
1 accounts with two-character codes, 22 Level 2 accounts with four-character
codes, and 671 Level 3 accounts with eight-character codes. Of Level 3, 669 have
`ALLOWED='1'` and two have `'0'`; Levels 1/2 have null `ALLOWED`. All 703
`ACCTYPE` values are null. These counts confirm stored levels and postability,
not a general parent-prefix, customer/supplier or code-allocation algorithm.
The native import therefore does not invent source parents or classifications.

There is one main item group and 43 subgroups. Here the subgroup→main relation
is an explicit source FK and is already imported into native `parent_id`.
Separate Main/Sub group navigation now uses that proven distinction. It does
not claim the original code-entry or all editing rules are known. Class filters
are applied before paging; save validates the current parent under the same
write lock as the mutation. Main forms cannot acquire a parent and sub forms
cannot discard theirs or choose another subgroup. Existing generic deeper
trees remain untouched and visible under Sub Groups, with a review notice when
their parent is not a root. Generic administrator APIs remain unchanged. The
native group table has no activation flag; all current roots are eligible.

The supplied backup has no purchases, dispatch challans or delivery-linked
invoices to compare. The observed charges do not establish extra labour/tax or
embedded partial-settlement rules. See [TRADE_CHARGES_EVIDENCE.md](TRADE_CHARGES_EVIDENCE.md)
and [DELIVERY_EVIDENCE.md](DELIVERY_EVIDENCE.md). These are limits on what can be
copied faithfully, not a requirement to add those features speculatively.

## Audit changes and acceptance

The bounded changes implemented in this audit are original-document HTML and
Voucher Printing navigation, Enter/focus and rejected-return-selection handling,
separate Main/Sub Item Group forms, and verified original/new trade-detail
reporting. The 2026-11-18 increment adds direct Cash Book/Party Ledger filters
and saved sale entry-unit details in the bill view. None changes source history,
the approved account/item-ledger cutoff snapshots or the owner-managed packaging
boundary. Trade-detail screens start
with blank dates to include originals; explicit/cleared dates persist. Other
financial/item report defaults remain the day after cutoff.

Focused checks completed for this audit:

- Original HTML: four service checks and one GUI check, covering 20 source
  documents including cancellation, exact recorded values, safe publication,
  source lineage and current access.
- Enter/focus and return selection: three business-workflow checks plus 12
  component checks. Generic forms reach Save changes/Use line through Enter;
  a second press activates the action. Posting remains explicit after review.
- Item groups: five new service checks plus the existing 23 master checks,
  one new GUI workflow and the all-entry menu-route check. IDs, product links,
  unsaved forms and the source archive remained intact.
- Trade details: 12 new and 52 existing service/export checks plus one new GUI
  check. Twelve combinations on a private actual-source copy matched original
  header/line counts and full HTML; the source SHA-256 remained unchanged.

Final whole-application validation on 2026-10-04 ran
`env DISPLAY=:96 python3 -m unittest discover -s tests -v` on the private virtual
display: **678 tests passed, with zero skips or failures**, in 197.260 seconds.
The syntax/source-manifest single-entry check passed. SHA-256 fingerprints for
the original backup, V2 database, recovered archive and preserved original V2
source remained unchanged. These results verify the current implementation and
its tested contracts; they do not establish full original form, workflow or
print-template parity.

The public `BIFMA_TRADERS_V3.py --smoke-test` also passed when launched from
`/tmp`: two companies, 30 screens, 11 documents and four stock reports. This
was a read-only baseline-display check, not a business-data posting run.

The earlier 2026-10-04 sale/startup increment recorded 650 passing tests and a
read-only baseline display check in [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).
Those results verify that earlier increment only. The focused audit results
above are separate; neither fills an original form/report evidence gap here.

For each original comparison, retain its exact reference screen/source/output,
input example, observed validation and navigation, resulting stock/account
values, and replacement result. Do not mark a module's original behavior as
matched solely because its menu opens, its service passes tests or its stored
columns resemble a screenshot.

## Walkthrough increment — 2026-11-18

The changed Python files compiled. Existing focused suites passed for
transactions (28), sale entry services (5), document export (15), cash reports
(6), native reports (18) and UI report models (18). A disposable synthetic sale
also confirmed that two cartons at a factor of 12 save and post as 24 base
units, and that the posted HTML shows the saved carton quantity/factor without
a Store column. The original archive, Oracle export and business database were
not changed. A live visual check of the new filter dialogs was not possible in
the current headless environment; this increment is not a full-suite run and
does not establish complete Oracle parity.

## Walkthrough increment — 2026-11-20

Reviewed all five unpacked videos and three JPEGs. The Cash Book, Party Ledger
and Sub Trial filters now retain their entered values behind separate report
previews, with explicit authorized Company selection. Financial HTML follows
the observed portrait cash panels and RTL account ledger. Sale actions move
beside Stock, and saved large-unit packing now survives draft reopening. Sale
and Breakage Receive bills use a BT-header Urdu layout, saved buyer/party names
and original-source packing where recorded. Bill/Print opens the verified page
in the system browser; Ctrl+P opens Print/PDF. Explicit Save copy is retained.

Sub-trial's photographed subgroup filter is still unsupported without an
established account grouping. Pre-cutoff ledger turnover, unseen DC/edit/delete
workflows, and later October/November transactions are not invented. The sale
photo also establishes independent print rounding that differs from the
recovered aggregate ledger formula. Posting and original recorded totals remain
unchanged. See `VIDEO_WORKFLOW_EVIDENCE.md`, `BILL_PRINT_LAYOUT.md`,
`REPORT_FILTERS.md` and `REPORT_PRINT_LAYOUT.md` for exact evidence and contracts.
