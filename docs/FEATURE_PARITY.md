# Original module coverage

Reviewed 2026-11-18 against the current source. This maps all 35 recovered
module tokens to the maintained application. A usable service or screen is
not proof of the original form's behavior, printer output or production
readiness. The module-by-module evidence and specific remaining comparisons
are recorded in [WORKFLOW_AUDIT.md](WORKFLOW_AUDIT.md).

The application uses one launcher, `python3 BIFMA_TRADERS_V3.py`, and top menus.
Business modules open a new-entry form; F8 opens the same module's combined
original/new Saved records browser without discarding the unfinished entry.
Original details remain read-only. Sealed original stock/account closings fund
new postings; history is never replayed under corrected rules. See
[BASELINE_INTEGRATION.md](BASELINE_INTEGRATION.md).

The catalog evidence is [LEGACY_MENUS.md](LEGACY_MENUS.md); source formulas are
in [LEGACY_ANALYSIS.md](LEGACY_ANALYSIS.md). New accounting/stock corrections,
manual return adjustments and purchase-rate valuation follow the owner's
recorded decisions. Unproved workflows are evidence gaps, not instructions to
invent features.

| Original module | Current location | Coverage and remaining comparison |
| --- | --- | --- |
| `UM_CATALOG` | Setup → Users & access | User creation, administrator role, enable/disable and passphrase/reset. Legacy credentials are not reused; original form comparison remains. |
| `UM_USER_PRIVS` | Setup → Users & access | Current company/module/action grants. Original menu-right mapping and effective screen visibility remain to compare. |
| `CONFIG` | Setup → Companies | Names, fiscal dates, cash account, contacts, tax identifiers and activity. Other original settings and form behavior remain unverified. |
| `CITY` | Coding → Cities | Codes and English/Urdu names, new-entry and F8 browsing. Original form/defaults remain to compare. |
| `CHART_LEVEL1` | Coding → Financial Coding → Accounts | Shared native account hierarchy, not a separate original Level 1 form. Source `CHLEVEL` is preserved in the archive but not mapped to native account level/parents. |
| `CHART_LEVEL2` | Coding → Financial Coding → Accounts | Same shared hierarchy. Original Level 2 parent/code rules are not established by the imported account structure. |
| `CHART_LEVEL3` | Coding → Financial Coding → Accounts | Postability, kind/classification, city and contact/tax identifiers. Original Level 3 form and code rules remain; all source default-discount values are null. |
| `MAIN_ITEM` | Coding → Inventory Coding → Main Item Groups | Separate entry and F8 browser for existing root groups. No parent field; saves enforce root status in the transaction. Original code-entry convention and exact form layout remain to compare. |
| `SUB_ITEM` | Coding → Inventory Coding → Item Sub Groups | Separate entry and F8 browser for existing child groups. Parent choices contain only main groups and are rechecked at save. Existing IDs/links are preserved; deeper generic hierarchies require review rather than being flattened. Original exact layout/code convention remains unverified. |
| `PUR_ITEMS` | Coding → Inventory Coding → Items | Owner-requested automatic read-only numeric code, descriptions, group, units/factor and known/unknown prices. Routine item entry has no account dropdowns; posting mappings are preserved or inherited only from unambiguous setup. Original form comparison remains. |
| `OPEN_STOCK` | Coding → Inventory Coding → Opening stock | Explicit quantity/value, store/pool and accounting counterpart. Existing balances are imported independently and must not be re-entered. Original entry/edit layout remains unverified. |
| `OPEN_BAL` | Financial Transactions → Opening Balance | Exact balanced opening vouchers through the generic line editor. Original opening screen/defaults remain to compare. |
| `CPV` | Financial Transactions → Cash Payment Voucher | Photo-based inline code/title, narration and amount rows, company/date/last number and Print/Save/New/Exit. Enter advances number → date → account Find → narration → amount → next account. Original/new F8 records and exact carried balances. Save uses review/post; original Cancel and Previous Balance meanings remain unknown. |
| `CRV` | Financial Transactions → Cash Receipt Voucher | Corresponding photo-based inline receipt form, Enter flow and F8 records. Invoice allocation is not inferred; original Cancel, Previous Balance and print-template behavior remain unverified. |
| `JV` | Financial Transactions → Journal Voucher | Exact balanced journals, draft review and immutable posting through the generic editor. New reversal actions are absent from the public UI; recorded reversal effects remain compatible. |
| `PUR` | Inventory → Purchases | Items, per-line stores, percentage discounts, supported header adjustments and balanced posting. The generic form has not been compared with the original purchase screen. No labour/tax calculation is invented. |
| `SALE_CREDIT` | Inventory → Sale Voucher; 2026-11-18 clips show buyer/item Find and Bill preview | Photo-based inline Sale Voucher, Enter progression, live Find, actual unit/quantity 1/known rate defaults, optional Bill # and automatic F8 date bounds. Selected unit and entered quantity are retained and checked against base quantity; sale HTML prints those values and omits Store. Original DC, previous-balance, settlement, exact printed template and printer behavior remain unverified. |
| `PUR_RET` | Inventory → Purchase returns | Eligible original/native linked lines and staff-entered header adjustments under original quantity/money caps. Ambiguous historical capacity requires review; the original return screen remains unavailable. |
| `SALE_RET` | Inventory → Sales returns | Corresponding linked-return controls and limits. No original invoice is recreated or changed; original return form comparison remains. |
| `SALE_COUNTER` | Inventory → Counter sales | Full direct-cash posting, distinct numbers/access, saved contacts and linked cash returns. Generic entry form; no unsupported tender/change or split-payment workflow is inferred. |
| `BRCV` | Inventory → Breakage receive | Standalone one-store receipt to breakage stock, line discounts and header breakage deduction. Original-sale linkage/freight handling remains unsupported pending original evidence. |
| `BDISP` | Inventory → Breakage dispatch | Standalone per-line stores, line discounts and approved corrected outward breakage quantity. Unresolved freight and header charges are rejected; original dispatch form remains unverified. |
| `VOUCHER` | Reports → Voucher Printing; saved document → Save printable copy | Section selection and F8 browsing lead to original/native printable HTML. Original copies use verified recorded values, company labels and cancellation markers; native copies require posting. Original templates/printer behavior remain unavailable. |
| `CASH` | 2026-11-18 clip: Cash Book asks for a date and company, then shows a daily cash report | Direct single-date filter, configured eligible cash account, cutoff opening and posted activity with running balances and full output. Account Find appears only when setup has no valid configured account. This is not a reconstruction of every earlier original cash entry; exact original totals and print layout remain unverified. |
| `LEDGER` | 2026-11-18 clip: Party Ledger asks Date From/Date To and party/account, then shows the ledger | Direct Date From/Date To and live account Find; uses the existing cutoff opening and exact new opening/period/closing report service with full output. Earlier turnover is not reconstructed by this screen; exact original balances and print layout remain unverified. |
| `TRIALBALANCE` | Reports → Financial reports → Trial balance | Exact carried/new opening/period/closing totals and printable output. Cutoff snapshot is identified, not presented as earlier turnover; original grouping/layout remains unverified. |
| `SUB_TRIAL` | Reports → Financial reports → Sub-trial balance | Current native parent hierarchy, exact descendant subtotals and grand totals counted once. Imported source chart-level hierarchy is not inferred; original grouping/layout remains unverified. |
| `PLS` | Reports → Financial reports → Profit and loss | Guarded periodic trading profit with explicit classifications and inventory adjustments. Missing classifications/costs yield Review required. The original report formula/layout is not recovered. |
| `CITY_RECOVERY` | Reports → Financial reports → Account balances by city | Exact carried/new balances grouped by current city, explicit filters and separate debit/credit totals. This is not invoice aging or settlement; original recovery selection/template remains unverified. |
| `STOCK` | Reports → Inventory reports → Stock quantities | Original movement activity plus new movements for earlier/cross-cutoff/current ranges, without duplicate carried quantities. Original report layout/parameters remain unverified. |
| `STOCK_VALUE` | Reports → Inventory reports → Stock valuation | Exact original movement values plus frozen new costs under the approved purchase-rate method; missing new costs stay unknown. Original layout comparison remains. |
| `ITEM_LEDGER` | Reports → Inventory reports → Item ledger | Cutoff opening snapshot plus new dated movements, optional store, regular/breakage pool and exact running quantity/value. Earlier original turnover and original layout are not reproduced here. |
| `PUR_DETAIL` | Reports → Inventory reports → Purchases / returns detail | Verified original and native posted detail, recorded reversals, exact values and full output. Initial dates are blank to include originals; explicit/cleared dates persist. Missing original amounts remain Unknown with review notes. Original template/filter parity remains unverified. |
| `SALE_DETAIL` | Reports → Inventory reports → Sales / returns detail | Corresponding original/native detail, saved labels and all-date defaults. Header totals are omitted under item/store filters; original values are not recomputed under corrected rules. Original template/filter parity remains unverified. |
| `UOM` | Coding → Inventory Coding → Units | Codes and bilingual labels. The photo places unit maintenance in Coding; the recovered catalog's report-type anomaly is retained as source evidence, not reproduced as a misleading report. |

## Current audit work and boundaries

The 2026-10-04 audit implemented and checked additional Enter/focus progression
in cash and generic forms, rejected-return selection remaining available,
original-document printing and original/new trade-detail reporting. Generic
forms advance to Save changes or Use line; a further Enter activates that
action. Saving documents still requires the existing review/post flow.
Financial and item-ledger report defaults/cutoff boundaries remain unchanged.
The audit does not establish exact original behavior for unseen forms/templates.

Focused verification passed four original-print service checks plus one GUI
check covering 20 original documents including cancellation; three business
keyboard checks and 12 component checks; and 12 new plus 52 existing trade
service/export checks and one trade-report GUI check. Twelve report combinations
on a private copy of the actual source matched original header/line counts and
full HTML, with the source fingerprint unchanged. On 2026-10-04 the final
whole-application suite passed **678 tests, with no skips or failures**, in
197.260 seconds. The syntax/source-manifest single-entry check also passed;
all four original-file fingerprints remained unchanged. These checks do not
establish full original form, workflow or print-template parity.
The public launcher's read-only `--smoke-test`, started outside the project
directory, also passed for two companies, 30 screens, 11 documents and four
stock reports.

The Main Item Groups / Item Sub Groups separation is implemented and checked.
The selected class is filtered before pagination/search results are returned;
grouped-form saves cannot switch an existing record's class. Group records have
no activity flag, so no invented activation state is introduced. The generic
administrator hierarchy APIs remain available for explicit setup. Five new
service regressions, the existing 23 master checks, one new group GUI workflow
and the all-entry route check passed using disposable data.

Explicit cash purchases and cash returns retain their existing namespaces and
grants. [CASH_TRADES.md](CASH_TRADES.md) describes their direct-cash behavior.
Bank receipt/payment entry and new reversal actions are removed from the public
UI as requested; internal types/services, saved records and report effects remain
for compatibility. Do not remove business history to match the menu.

Posted documents remain immutable. Find selectors support live literal
code/name/Urdu search and stable selected IDs. Sale entry additionally supports
Enter selection and progression; cash and generic business forms also support
Enter as described above. Quantities convert exactly between configured
units; rates remain per base unit. See [NATIVE_WORKSPACE.md](NATIVE_WORKSPACE.md),
[TRANSACTION_SERVICES.md](TRANSACTION_SERVICES.md) and
[trade-detail semantics](TRADE_DETAIL_REPORTS.md).

Cross-cutting checks cover current permissions, immutable snapshots, stale-edit
rejection, atomic posting, interruption recovery and verified backup/restore.
They do not prove original workflow/print parity. Platform packaging belongs to
the owner; dated Windows/Linux development artifacts are historical evidence,
not current delivery requirements. Independent table reconciliation is recorded
in [ORACLE_RECONCILIATION.md](ORACLE_RECONCILIATION.md).
