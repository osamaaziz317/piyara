# BIFMA TRADERS project context

## Current behavior and owner decisions — refreshed 2026-11-20

### Walkthrough-driven update — 2026-11-20

The five unpacked MP4 clips and three JPEGs in `videos/` were reviewed at
one-second intervals, including the Cash Book/Party Ledger filters, output,
Sale Voucher entry, Bill preview and printer dialog. Detailed observations and
limits are in `docs/VIDEO_WORKFLOW_EVIDENCE.md`. These are partial visible
workflows; complete original-application parity is still unverified.

- `bifma/report_ui.py` now gives Cash Book, Party Ledger and Sub Trial Balance
  compact retained filter dialogs, explicit authorized Company selection and
  separate result previews. Preview close restores the filter and its modal
  focus. Cash Book is one date; Party Ledger has Date From/To and live account
  code/Find. Date controls accept DD-MM-YY, DD-MM-YYYY or ISO. The Sub Trial
  Balance filter uses Date/Company and the existing full report; the photographed
  Sub-head grouping cannot be reproduced by guessing account-code prefixes.
- `bifma/report_export.py` prints a compact monochrome financial page. Party
  Ledger uses RTL Urdu columns and blue running balances with debit/credit
  indicators; Cash Book places payments and receipts in adjacent panels. Full
  data, exact amounts, cutoff openings and report authorization are retained.
  There is no invented counterpart account when a cash entry has narration only.
- `bifma/bill_layout.py` draws BT-header, RTL Sale Voucher and Breakage Receive
  bill pages using saved Urdu/English party and product names, packing, quantities,
  rates, discounts and totals. Native sale prints the buyer again (a previous
  conditional omitted it). Original bills get labels/packing and any actual DC
  link from the verified source under the exporter's company authorization;
  final amounts still use the register's original party amount. Full recorded
  detail remains expandable on screen and is excluded from the compact printout.
  Unknown source packing/amounts remain unknown. No settlement or DC creation
  workflow was added. The photographed sale line display rounds independently
  and does not reconcile to the recovered ledger's aggregate rounding; this
  update does not change saved figures or balanced posting rules to imitate it.
- Sale entry retains saved large-unit IDs/entered quantities on reopening a
  draft. New/Bill/Exit sit next to Stock as in the video; Save and F8 stay
  available. Bill still saves/reviews and requires explicit posting.
- Bill/print preview opens a freshly authorized standalone HTML page in the
  system browser directly. Ctrl+P invokes the browser's Print/PDF dialog;
  Save printable copy remains a separate action. `bifma/print_preview.py` keeps
  preview files private for the signed-in session and removes them on sign-out
  or clean shutdown. No new Python dependency, launcher, database, automatic
  posting or automatic print job is introduced.

The dated implementation notes below describe the prior layout where they
conflict with these bullets. See `docs/REPORT_FILTERS.md` and
`docs/REPORT_PRINT_LAYOUT.md` for the financial screens/output.

Validation for this increment: 168 focused transaction, original/native export,
sale/cash entry, report, keyboard and combined-application checks passed on the
live Tk display. Four additional print-preview lifecycle checks passed;
document/preview suites were rerun after the last bill-date formatting change.
A disposable working import of the actual baseline verified both companies and
exported three available original Sale/Breakage bills without changing the
archive SHA-256. Browser/PDF reviews checked compact sale printing and complete
240-row cash/party output. This is targeted verification, not a full-suite run,
complete Oracle parity, or physical printer qualification.

This section supersedes earlier implementation-history notes below. Maintain one
codebase and one launcher: `python3 BIFMA_TRADERS_V3.py`. Packaging remains with
the owner. They asked to complete the missing operational integration, approved
staff-entered partial-return header adjustments capped at original totals, and
selected the original purchase-rate valuation method. They also explicitly
required the original feature scope: do not invent extra labour/tax or other
workflows merely because a source field exists.

- Startup opens one module menu after login, automatically preparing/reusing
  `data/bifma.sqlite3` from the original `data/legacy_recovered.sqlite3`. No
  database picker or empty company setup. First use creates a secure personal
  owner login; no default or imported passwords. Business-entry modules open
  on a new-entry form. F8 opens their Saved records browser, combining original
  and new documents in the same section. There is no Workspace/History switch.
  Original detail dialogs retain recorded values and offer no editing or
  reposting.
- The owner's latest login photo (2026-10-04) is implemented in the compact
  `LoginForm`: keyboard/cart artwork drawn in Tk, BIFMA branding, User, User Name,
  Password, Company and OK/Cancel. Username Enter looks up the exact saved active
  user name and company choices, then focuses the read-only name; Enter moves
  to Password. Password Enter submits with one company or focuses Company when
  there are multiple choices. Company Enter submits. Duplicate company names
  keep distinct selections. Username edits clear the prior name/company/password
  and stale asynchronous lookup results are ignored. Actual sign-in still checks
  the password and fresh company access. Failed sign-in clears/refocuses Password;
  logout returns to an empty compact login and sign-in restores the work window.
  Initial owner setup uses an editable name default; Enter on the final password
  confirmation invokes Create administrator directly. Invalid confirmation or
  other validation leaves the setup form open with its entered values.
- The owner removed the 15-character requirement. Usernames now allow 1–80
  supported ASCII characters after normalization; passwords allow 1–1,024 NFC
  characters, reject blanks and retain existing common/context checks. Existing
  hashes, lockouts, expiry and access rules are unchanged. `login_profile` is a
  bounded, read-only name/company lookup, not authentication; it returns no
  roles, credentials or session and does not change attempts/audits. Company
  selection is checked again after authentication before entering the shell.
- Schema 6 changes only the `users` username minimum through an explicit table
  rebuild preserving all fields, rowids and child references; migrations 1–5
  remain frozen. Application startup explicitly prepares schema-5 working files,
  verifies the sealed baseline/source association under the migration write lock,
  and creates a verified pre-upgrade backup before mutation. Current files are
  reused; older versions retain the manual upgrade path. A competing completed
  upgrade is reverified instead of returning a stale version error. Ordinary
  service constructors remain current-schema-only and all lifecycle gates apply.
- The owner's latest photo-based instruction moves navigation to a native
  top menu bar: **File**, **Coding**, **Financial Transactions**, **Inventory**,
  **Reports** and **Setup**. Coding nests Financial Coding and Inventory Coding,
  with Cities directly below. There is no left sidebar. Coding contains Accounts, Items, Cities, Main Item Groups, Item Sub Groups,
  Units, Stores, Company stores and Opening stock. Financial Transactions has
  exactly Opening Balance, Cash Payment Voucher, Cash Receipt Voucher and
  Journal Voucher, in that order. Inventory holds all implemented trade/return,
  cash-trade and breakage modules. Reports has Voucher Printing, Financial reports and Inventory
  reports with individual links to the existing reports. Companies,
  users/access, audit and backups are in Setup. Master selectors no longer mix
  company configuration with Coding. Bank receipt/payment entry and all new
  reversal actions, including the posted-document button/dialog, are removed
  from the public interface. Backend types/services and saved old records,
  reversal markers/references and report effects remain compatible. No history
  or schema is rewritten. The grouping follows the recovered menu catalog while
  retaining current supported cash variants; it is not a claim that every
  original form/report has been reproduced. Dated reversal UI evidence below
  describes earlier builds; current diagnostics use service-created reversal
  fixtures only to check retained history and reporting behavior.
- The owner's subsequent entry-flow instruction applies to Coding masters,
  Setup → Companies and all financial/inventory entry modules. Opening one
  displays its New form above a blank module landing; saved records are not
  loaded into a list automatically. F8 or Saved records (F8) opens a nested
  browser while retaining unsaved fields and lines. Back to entry or closing
  that browser restores the same form. Saving a new master, keeping a new draft or
  closing a newly posted document returns to a fresh New form. Read-only users
  get a blank module landing with explicit F8 browsing, not a creation attempt.
  Reports still wait for filters and Run report, while Users & access and Audit
  log retain their administration lists. Permissions, exact values, baseline
  integration and removed bank/reversal entry actions are unchanged.
- All shared **Find…** selectors now filter while the operator types, pastes,
  deletes or clears the search text. The cached choices match literal code,
  English name or Urdu name, ignoring case. Changing the query resets paging
  and clears the old result selection without changing the form's selected ID;
  **Use selected** or Enter commits the highlighted choice. Search remains
  available, and Up/Down changes the highlighted result without committing it.
  The widget-owned write trace is removed on destruction; no delayed job or
  database write is introduced. This applies to account/item/master and report
  account Find windows; F8 saved-record and return-invoice browser queries keep
  their explicit Search actions.
- The shared Find popup now activates its editable input after it becomes
  visible. Real pointer/keyboard tests reproduced a mapped nested window with
  a grab but no focus; keys previously disappeared in this case. Selector text
  click focuses the selector, and typing/paste opens the same live Find with
  that query. Its dropdown arrow still opens the normal list. Typing/pasting
  while that list has focus also starts Find, after closing the native popup;
  normal dropdown navigation/selection remains available. Find belongs to
  the actual form, restores the previous field/grab when closed, and retains
  stable IDs even for duplicate names. Menu commands are disabled during
  workers and entry dialogs and guarded against invocation during a modal.
- `bifma/cash_voucher_ui.py` provides inline Cash Payment/Receipt forms based on
  the supplied original screenshots: blue/maroon title, voucher/date/company,
  last numeric voucher number, account code/title, narration, amount, exact
  running total and Print/Save/New/Exit. Rows scroll independently of the pinned
  footer. Exact account codes resolve IDs and shared live Find selects names.
  New forms accept two decimal places without silently rounding line amounts;
  draft edits preserve their stored precision and cash mapping. Save still uses
  the existing draft review/post pipeline. Print saves a draft, requires explicit
  Post and print, then opens the verified posted HTML export chooser. New/Exit
  confirms discarding changed fields. F8 keeps all inline input intact.
  `TransactionService.document_numbering` supplies the last observed numeric
  original/native number and next suggestion without loading the record list.
  No source record, posting rule or schema is changed. Original Cancel and
  Previous Balance meanings remain unverified and are not fabricated.
- `bifma/document_register.py` merges source and working document lists before
  pagination, with shared search/date filters and fresh company/module grants.
  The original archive's hash must match the sealed baseline before reads.
  Original opening-stock rows also remain accessible through their section.
- The sale module is labeled **Sale Voucher** across Inventory, new-entry
  titles, Saved records and Voucher Printing, matching the owner's 2026-10-04
  naming request. Internal `sale` document kinds and `sales` permissions, sales
  return/report labels, and stored history remain unchanged.
- `bifma/sale_voucher_ui.py` implements the owner's 2026-10-04 Sale Voucher
  reference with inline item code/description, unit, unit quantity × factor,
  base quantity, rate, discount and value rows; Sale #/date/company, optional
  Bill #, buyer/remarks, stock as of the displayed date, exact totals and
  Print/Save/New/Exit. Enter advances Sale # → Date → Buyer Find → Item Find →
  unit → quantity → rate → discount → next item. Search highlights its first
  live match, with Up/Down and Enter selection after restoring the popup's
  parent focus/grab. Enter selection is now the shared Find default.
  Enter/Keypad Enter on Dis %, computed Discount or Value opens the next row's
  Item Find; computed amounts stay read-only and are skipped by Tab. Appended
  rows are laid out before focus moves, so cancelling Find returns to the new
  row's item code. This also works beyond the initial visible rows.
  Choosing an item defaults its actual base unit, quantity 1 and known sale
  rate; unknown prices stay blank. Units convert quantity only: rate remains
  per base unit. The owner's subsequent instruction removes the Sale Voucher's
  Store header, row column and Find step: Buyer selection opens Item Find
  directly. For the `sales` module, `MasterService.lookup` supplies
  `default_sale_store_id` using a single location from the company's original
  sale lines, or posted native sale lines when no original sale evidence exists.
  With no sale evidence, a sole active company store may supply the default.
  Conflicting locations or evidence pointing to an inactive membership leave
  the default unresolved; they do not select an arbitrary location or activate
  a membership. Existing draft stores, quantities, rates and precision are
  preserved; stock previews and posting use each line's internal store ID.
  Save/Print retain the
  existing review/post/export path. Sale-only Bill # is an exact text reference
  in saved values and posted snapshots/HTML; empty references preserve the old
  metadata shape. No schema or accounting rules changed for this field. The
  screen does not establish DC or previous-balance behavior. New native sale
  rows also retain the selected item unit and entered quantity in saved
  metadata. The service validates their conversion against the base quantity;
  stock and accounting still use only that base quantity. The posted sale HTML
  shows item code/description, entry unit and quantity, factor, base quantity,
  rate, discount and value without a Store column. This is a compact native A4
  HTML print view, not the unavailable Oracle print template or its
  printer-dialog behavior.
- The owner clarified Date From / Date To means the F8 **Sale Voucher**
  saved-record browser.
  `DocumentRegister.date_bounds` checks the same archive and live permissions
  as the register, taking valid original/native header dates without computing
  invoice totals. F8 supplies those bounds and refreshes them after saves;
  explicit Search/Clear choices persist. Browsing/filtering preserves all
  unfinished sale entry fields and rows.
- Startup uses `native_schema.startup_verification()` around baseline reopen
  and service construction, avoiding repeated identical integrity/immutable
  scans in that single worker operation. First access still performs recovery,
  migration lineage, FK/integrity and sealed baseline verification. Reuse is
  scoped to the process/thread and unchanged file metadata, disabled by
  sidecars/aliases, and discarded before login. Every native open still enters
  the existing lifecycle gate; authorization and managed-session contracts
  remain unchanged. See `docs/PROCESS_RECOVERY.md` for invalidation details.
- The 2026-10-04 whole-section audit separates Main Item Groups and Item Sub
  Groups using the existing product-group IDs and parent links. Scoped lists
  filter before pagination; subgroup choices contain root groups only. Create
  and update recheck the hierarchy under the write lock. Existing deeper trees
  and generic administrator APIs remain intact; there is no data migration.
  Do not infer the original chart-of-account levels from code prefixes: the
  archive does not establish those parent relationships or form behavior.
- The owner's latest 2026-10-04 keyboard instruction makes Enter and Keypad Enter
  navigation the shared Form default across entry, administration and report
  controls. Enter advances through editable fields; the final field invokes the
  existing Save, Use line, Show or OK action. Successful completion closes its
  modal; validation leaves its entered values available for correction. Mouse
  choice selection does not submit a form. Generated read-only codes and disabled
  fields are skipped. Tab/Shift+Tab traverse controls, Space toggles checkboxes,
  and Enter advances without toggling them. Focused fields remain visible in
  scrolling forms. Shared Find Enter commits after restoring the parent focus
  and grab; a selected selector advances on Enter and F4 reopens Find.
- Cash advances number → date → account Find → narration → amount → next account;
  Enter on a blank account-code row saves the populated voucher. Sale Enter on a
  blank item code reaches header discount → breakage → freight, whose final
  Enter saves. Ctrl+S saves vouchers for review from their entry fields. Generic
  header completion opens Add line; final line Enter validates/applies and closes
  the line dialog, restoring Add line focus. Generic tables use Enter to edit,
  Insert to add and Delete to remove; inline cash/sale rows use Ctrl+Delete.
  Quantity-converter completion applies its result and restores line quantity.
  Initial review focus is Keep as draft, and posting still requires explicitly
  choosing Post. Posted dialogs focus Close. Return-source search/selection is
  retained when a line is cancelled or rejected; success focuses return quantity.
- Keyboard navigation reaches the top menus through Alt+F/C/T/I/R/S or F10,
  Company through Ctrl+L, and notebook pages through Ctrl+Tab. Escape uses each
  dialog's existing close/discard guards. Shortcuts respect active workers,
  modals and permissions; F8 retains its existing unsaved-entry browsing behavior.
- `bifma/keyboard.py` owns window-scoped button/field fallbacks, focus scrolling
  and callback cleanup. Explicit form handlers take precedence; do not change
  global Tk class bindings. Register notebook traversal only once per widget.
  Use `unbind_callback` when removing one callback: older Tkinter versions erase
  every binding for that sequence even when a callback ID is supplied. Destroyed
  dialogs are removed from owner tracking, and workspace shutdown preserves
  unrelated root handlers. Saved-list Search focuses the first result; Escape
  restores the original entry field and its unsaved values.
- `OriginalDocumentExporter` creates exact printable HTML copies through the
  verified `DocumentRegister`, including original company labels, missing values,
  notes and cancellation status. It rechecks permissions/company association and
  archive identity before atomic private publication and never overwrites an
  existing file. Reports → Voucher Printing starts with a section chooser and
  explicit F8 browsing; original detail dialogs now offer Save printable copy.
  New documents keep their existing posted-copy path. This reproduces recorded
  content, not unavailable original print templates.
- `UnifiedReports.trade_detail` and `trade_selectors` now combine original and
  native sales/purchase invoices and returns, including cash partitions, before
  paging and totals. Selectors include mapped original history; original labels,
  exact amounts, missing values and unit conflicts remain visible. Ambiguous
  original party totals remain Unknown. Full HTML uses the same query, fresh
  grants and verified archive. Trade reports default to blank dates (all dates);
  untouched financial/item defaults keep cutoff + 1 day, while explicit/cleared
  filters persist across report changes. Account/item-ledger historical turnover
  remains outside this change. See `docs/TRADE_DETAIL_REPORTS.md`.
- The owner supplied only `BT-270926.EXE` when asked for original form/program
  source. Its `EXPORT:V10.02.01` header identifies an Oracle database export;
  no original form/report files are available. The 161-line V2 Python reference
  is a prototype, not the original Oracle interface. Available screenshots,
  recovered menus/data/rules and current behavior are distinguished in
  `docs/WORKFLOW_AUDIT.md`; exact unseen forms, Save/Edit/Cancel, DC, previous
  balance and print-template parity must not be claimed from this evidence.
- `bifma/baseline.py` atomically publishes a fully imported new working file,
  with source SHA, durable UUID mappings and sealed exact stock/account closings,
  cost candidates and original invoice references. Reopening verifies origin and
  immutable content and never replaces new postings. Native schema is **6**;
  prior migration specifications remain unchanged.
- Actual source cutoff is **2026-09-27**. New documents must follow it and stay
  within configured fiscal dates. Original account/stock effects are carried
  independently, with no duplicate opening-stock journal. Existing negative
  stock is preserved; incoming transactions can reduce deficits, while new
  outward transactions must have stock, including every affected later date.
- Trial/sub-trial/city/cash/account and item-ledger reports include sealed
  baseline plus new postings; earlier account/item turnover is not reconstructed.
  `bifma/unified_reports.py` gives Stock quantities and Stock valuation their
  original movement activity for earlier or cross-cutoff date ranges, followed
  by new movements once. Screens and full HTML exports share that query path.
  Starting data contains 603 account closings and 6,894 stock closings; both
  companies' original trial balances reconcile. Original data is unchanged.
- Historical original invoices are separate immutable reference records, not
  native posted documents: 3,412 invoices and 55,403 lines. New returns can
  reference eligible historical or native lines; quantity/money caps include
  known prior linked returns and fresh permissions. Original history remains
  untouched. Eleven documents and 212 lines are explicitly ineligible for
  automatic returns because of source anomalies; no capacity is guessed.
- New returns accept staff-entered discount/breakage/freight; original and
  cumulative quantity/amount limits still apply at post. Number suggestions
  advance original/native numeric sequences, with atomic duplicate rejection.
- Missing master rates have explicit known flags; blank rates stay unknown,
  and numeric zero is intentional. Imported stock also freezes base-unit identity.
  Original unit conflicts, negative quantities, undeclared store memberships and
  unknown account postability are visible for review instead of being rewritten.
- The owner requested automatic Item codes and removed party/account choices
  from routine item entry. New/edit forms show a read-only code with description,
  group, units and prices. `MasterService.next_product_code` previews the code;
  `create_product` allocates it under the write lock using the maximum current
  ASCII-numeric code or sealed original item key plus one. Original codes are
  preserved; the source sequence is exactly 1–6207, so its next code is 6208.
  New items inherit the sole consistent established sales/purchase pair only
  when both accounts are active, postable and general; conflicting partial
  mappings also block saving. Inventory mapping stays unset for new items.
  Routine edits preserve existing code/mappings by omission. All 6,207 original
  items use one posting pair, but this observation does not prove the original
  form's manual workflow. Missing/ambiguous defaults require administrator review.
  Explicit administrator master APIs remain available for deliberate setup.
  This change requires no schema migration.
- `bifma/valuation.py` freezes each new movement's exact cost in immutable
  `stock_valuation_snapshots`. Original highest-ID rate candidates are preserved;
  native posting sequence/line order continues the ordering with date and
  company/item restrictions. Primary opening/purchase candidates precede sales-
  return fallback. Later purchases cannot rewrite saved costs; reversals mirror
  original values. Missing costs remain unknown. Historical accumulated stock
  value is carried exactly, not replaced by quantity multiplied by latest price.
- Stock valuation and guarded periodic P&L have screens and full HTML output.
  P&L requires reviewed classifications, appropriate purchase-account treatment
  and complete values; otherwise no asserted profit is shown. It adjusts both
  stock pools and excludes explicit stock-opening injections from profit.
- See `docs/BASELINE_INTEGRATION.md` and `docs/OPERATIONAL_REPORTS.md` for the
  current contracts. Exact original form/print parity and ambiguous original
  workflows still need reference evidence and business acceptance. Passing
  tests is not proof of overall production readiness. No platform build work
  resumed and no new parallel source version was created.

Sale Voucher Store-control removal verification on 2026-10-04: **66 affected
service/UI/integration tests passed without skips or failures** in 62.904
seconds. Synthetic baseline and native fixtures cover automatic location
selection with multiple active stores, company isolation, conflicting/inactive
evidence and fresh authorization. Real Tk checks cover Buyer → Item Find with
no Store controls, exact draft line/location preservation, six successive item
rows, F8, save/review/post and printing. The integrated application diagnostic
also passed. All four original source/data fingerprints remained unchanged;
tests used disposable databases.

Sale Voucher naming and row-continuation verification on 2026-10-04: **33
affected UI/integration tests passed without skips or failures** in 53.480
seconds. The new regression reproduced computed Discount Enter returning to
the same row before the fix, then verified six consecutive items with Return
and Keypad Enter, exact saved quantities/rates/discounts, dynamic row creation
and Find cancellation focus. Coverage also includes public menu naming, F8,
permissions, draft/post/print and the integrated application diagnostic. All
four original source/data fingerprints remained unchanged.

Keyboard-workflow verification on 2026-10-04: the complete application suite
passed **714 tests with no skips or failures** in 236.670 seconds on a private
virtual display. Real Tk checks cover final-field completion, validation/retry,
saved-list search/edit/focus restoration, menu navigation, scrolling, notebook
pages, permissions, report filters and login. Callback lifecycle tests exposed
and verified the selective-unbind fix for Python 3.10. A final worker-state
guard change then passed all **12 focused voucher checks**, including a new
gated-worker test for cash, sale and journal shortcuts; this increases future
whole-suite discovery to 715 tests. While a worker is active, keyboard Save,
Delete and row creation respect the disabled controls. The source-manifest
check passed, and all four original-file fingerprints remained unchanged.
Tests used disposable data; no platform build was produced.

Login/reference and short-credential verification on 2026-10-04: the complete
application suite passed **699 tests with no skips or failures** in 219.054
seconds. Six new real-Tk workflows cover first-use/returning-user Enter focus,
single-character credentials, saved names, duplicate company selection, retry,
stale lookup replies, revoked company access and closing during a worker read.
The 36 security checks and nine populated migration/startup checks passed,
including the competing-upgrade race and unchanged hashes, grants, sessions,
posted rows, baseline seals and verified backup. The actual synthetic diagnostic
completed all 17 steps and removed its private data. Login and first-use layouts
were visually reviewed at 760×500 / 760×560. The source-manifest/syntax check
passed, and all four original-file fingerprints remained unchanged. No live
working file was upgraded or populated during testing; normal user startup will
perform its required verified schema-5 upgrade with a backup. No platform build
or parallel application version was created.

Whole-section audit verification on 2026-10-04: the complete application suite
passed **678 tests with no skips or failures** in 197.260 seconds on a private
virtual display. Coverage includes the main/subgroup routes and hierarchy guards,
cash/generic Enter focus, return-picker rejection/cancellation, original printing
and private publication, combined original/native trade reports and automatic
versus explicit date filters. The source-manifest/syntax check passed. The exact
public launcher ran from `/tmp` and passed its read-only original-data check:
2 companies, 30 screens, 11 documents and 4 stock reports. Original backup, V2
database, recovered archive and original-source fingerprints were unchanged.

A disposable working import from the actual archive also checked 12 combinations
of company, sales/purchase family and account/cash/all modes against original
header/line counts and full HTML output. Company 1 all-mode sales included 3,395
documents / 55,307 lines; its 11 unknown original party totals remained unknown.
Company 1 purchase detail retained one original return / two lines and its
unknown party total. Company 2 sales contained 17 documents / 96 lines. The
largest combined selector, detail and full-export check took about 7.4 seconds;
this is not a startup timing. Temporary imports/exports were removed and no
real working file received test entries. These results verify the maintained
implementation, not exact unseen original forms or print templates. No platform
packages or parallel application versions were created.

Sale-entry/startup verification on 2026-10-04: the full application suite passed
**650 tests with no skips**, including real focused Enter selection, consecutive
item rows, exact quantity conversion/totals, preserved draft precision, Bill #
posting and escaped HTML, F8 date bounds refreshing after later saves, manual
and cleared date filters, and retained unfinished entries. Existing cash,
purchase/return, permissions, original/new browsing and embedded diagnostics
also passed. The source-manifest/syntax check passed without building packages.
The integrated Sale form was visually reviewed at 1050×720 and 850×610 with
two synthetic stores; header/footer remained visible and keyboard focus
revealed horizontally hidden columns. The public launcher's read-only original
display check passed for 2 companies, 30 screens, 11 documents and 4 stock
reports. Original backup, V2 database, recovered archive and original-source
fingerprints remained unchanged. Tests used disposable databases; no business
entries, platform packages or parallel application versions were created.

Startup timing on 2026-10-04 used the same private 237 MiB database imported
from the actual archive, alternating the prior and scoped verification paths
without profiling overhead. Reopen preparation plus service construction took
10.2491 / 10.2141 seconds before and 3.1508 / 3.1409 seconds after (means
**10.23 → 3.15 seconds**, a 69.3% reduction). This measures repeat startup,
not first import, login or every machine's cold disk performance. The source
and temporary database hashes were unchanged; the private import was deleted.
Focused startup/schema/recovery/baseline/register verification passed 89 tests,
including 18 new scope/invalidation/recovery regressions.

Photo-based top-menu/cash-voucher verification on 2026-10-03: the full
application suite exercised **619 tests with no skips**. 618 passed initially;
one embedded-workflow smoke assertion still looked for the menu on its frame
instead of the application root. After fixing that diagnostic helper, its full
workflow and the two final menu/cash integration cases all passed. Those cases
include exact 14.05 posting, F8 preserving multiple rows, incomplete-row discard
confirmation, draft numbering, actual posted HTML export, main/entry Find menu
blocking, permissions and logout cleanup. Shared Find tests use real XTest
pointer/keyboard input, including paste, deletion, duplicate labels and focus
restoration. Two standalone cash-component GUI tests and the source-inventory
check also passed. Top menu, payment form and Find were visually reviewed on
synthetic data at 1050×720. The public launcher's read-only baseline display
check passed for 2 companies, 30 screens, 11 documents and 4 stock reports;
original backup, V2 database, recovered archive and original-source hashes were
unchanged. No business database received test entries or schema changes, and no
platform packages were built.

Follow-up search verification reproduced ignored keystrokes after opening the
native dropdown arrow. Choice now binds typing/paste on its own popup listbox
and unposts it before transferring input to Find. Four selector checks passed,
including real keyboard/mouse input, Urdu paste, native selection/navigation,
stable IDs and callback disposal. Eight affected menu, cash-voucher and F8 GUI
checks also passed on temporary synthetic data; no business data was changed.

Earlier baseline-integration verification on 2026-10-03: the full application suite passed **575 tests**
with no skips, including GUI workflows on a private virtual display; **15**
source-manifest checks also passed without building packages. The public
launcher's read-only smoke check passed for 2 companies, 30 screens,
11 documents and 4 stock reports. An additional temporary working database
created from the actual recovered archive passed purchase, sale, cash receipt,
cash payment, sale reversal and historical-sale-return workflows, independent
account/stock reconciliation, three full HTML exports, restart without
reimport, and backup/restore with session revocation. Original source, V2
database, export and recovered-archive hashes remained unchanged. No real
working database was populated with test transactions.

Subsequent single-menu integration verification used another private temporary
working database: source counts reconciled for both companies, 14 original
module details opened, and a newly posted cash receipt appeared with originals
in its own list. Search, dates, restart, earlier/cutoff stock against the source
engine and full earlier-stock HTML output passed. All four original-file hashes
remained unchanged and temporary data was removed. Focused stock tests also
cover cross-cutoff purchases/sales, exact values, source anomalies, source
mutation refusal and permission revocation before export publication.

After the single-menu integration, the full application suite passed **593
tests with no skips** on 2026-10-03; the 15 source-manifest checks also passed.
The GUI checks cover originals and new sales/receipts in one list, entry and
refresh in that list, shared filters, exact balance continuation, original
document access after login and refusal after logout, startup/retry and worker
shutdown. The combined Sales screen was reviewed at 1050×720. No platform
packages were built and no real working file received test postings.
After the final list-status adjustment, 35 focused GUI, UI-model and register
checks passed again, including explicit Reversed/Reversal labels and returning
from a correction to the same Sales list.

Automatic-item-entry verification on 2026-10-03: the full application suite
passed **606 tests with no skips**, including 12 new service tests and 10
application GUI tests. Coverage includes concurrent saves after identical code
previews, exact numeric sequencing, rollback, preserved edit mappings, separate
party account entry and purchase/sale posting for a GUI-created item. A private
temporary working copy imported from the actual archive allocated item 6208,
suggested 6209 next, and passed purchase/sale posting with balanced journals and
the expected stock/account effects. Every one of the 6,207 pre-existing item
rows and the original archive hash remained unchanged. Temporary data was
removed; no real working database received test postings. No schema migration
or platform build was needed.

Grouped-menu verification on 2026-10-03: **41 focused checks passed** (11
application GUI cases, 18 UI-model checks and 12 diagnostic checks). The new
GUI case opens Coding forms, saves a city, posts all four financial voucher
types, selects Inventory and report modules, rejects removed bank/reversal
entry routes and reads previously saved corrections. The actual source
diagnostic also passed all **17 workflow steps**, including retained-service
correction fixtures, reporting and backup/restore; temporary data was removed.
Six menu states were visually reviewed at 1050×720, with working mouse-wheel
scrolling and fully visible date-filter controls. The synthetic operator-guide
walkthrough reconciles nine documents, debit/credit totals of 1322, stock 14
and cash 1036 without using reversal entry. No real business file received
test postings, and no database migration or platform build was performed.

Entry-first/F8 verification on 2026-10-03: **45 focused checks passed** (15
application/navigation GUI cases, 18 UI-model checks and 12 diagnostic checks).
Every Coding/company and financial/inventory entry route opens a new form
without displaying a saved list. Real focused-input F8 events cover lazy
record queries, original/current documents, repeated-key handling, paging,
search, nested edits, unchanged unfinished fields/lines, Escape/Back to entry,
next-item numbering, fresh forms after draft/post completion, read-only access,
revoked permissions and logout. Company edits refresh choices and access while
preserving the unfinished form. The four dedicated F8 cases passed again after
the fixed browser footer; four new-form/browser layouts were inspected with
the return button visible. The existing source diagnostic also passed all 17
steps, including posting/reporting and backup/restore, and removed its temporary
data. Only disposable synthetic databases were used; schema and business
records were unchanged. No packaging work was performed.

Live Find verification on 2026-10-03: both new real-Tk selector cases and the
existing complete native workflow passed (**3 checks**, no skips). Focused
keystrokes, paste, backspace and clearing update results immediately; code,
English/Urdu names, case-folding and literal punctuation retain their matching
rules. Paging resets correctly, duplicate labels retain distinct IDs, and only
Use selected commits a choice. Escape and immediate parent shutdown release
the search trace without affecting another Tk root. The tests use synthetic
in-memory choices and disposable workflow data; no business records changed.

## Oracle walkthrough evidence — 2026-11-18

The owner supplied one ZIP containing five short MP4 clips and three JPEGs.
The clips show the top menus, Cash Book filter/output, Party Ledger
filter/output, Sale Voucher item and buyer Find dialogs, the Bill preview and a
Windows printer dialog. Cash Book visibly asks for one date and company;
Party Ledger visibly asks for Date From/Date To and a party/account. Those two
reports now open their own filters directly from Reports. Cash Book uses the
company's configured cash account without a normal account-pick step; if no
valid configured cash account exists, the operator must select an eligible one.
Party Ledger keeps the live account Find and existing exact ledger service.

The video and supplied sale screenshot establish the visible sale columns and
the Bill action. They do not establish DC linkage, previous-balance effects,
all popup edge cases, or a reproducible Oracle printer template. The database
export documents data and recorded expressions, not the Oracle forms or their
hidden behavior. Preserve that boundary in future comparisons.

Reviewed: 2026-10-02. The section below records the original V2 baseline.
The rebuild summary takes precedence for files added after that review.

## Earlier rebuild implementation history

The dated progression below records prior stages. The current behavior above
supersedes earlier statements that operational baseline integration was missing.

The functional objective is a faithful replacement of the original trading
software with SQLite and an improved UI. The latest owner instruction on
2026-10-03 is to maintain one codebase/application for whole-app testing with
`python3 BIFMA_TRADERS_V3.py`, without creating parallel versions or launchers;
they will handle Windows/Linux packaging themselves. Do not resume packaging
or platform qualification unless requested. The owner then clarified that the
existing original data is the baseline: normal V3 startup must open it directly,
without asking for a database or sending them through empty company setup.
Full original parity is **not yet complete or production ready**.
Read `README.md`, `docs/REBUILD_PLAN.md`,
`docs/LEGACY_ANALYSIS.md`, and `docs/LEGACY_MENUS.md` before changing semantics.

- `BT-270926.EXE` is an Oracle original export (`V10.02.01`), not an executable.
  SHA-256: `d2cd806aa644320cf6d2eaa6192e7d6c840adcde8226f62b5ad1422980cc7e9e`.
- Recovered: 30 tables / 302 columns, 6 views, 24 stored functions, 46 menu rows,
  12 entry types, and 93,110 data rows. The menu hierarchy is much broader than V2.
- `tools/inspect_legacy.py` extracts schema/source; `tools/recovery_oracle.py`
  decodes the reviewed backup with exact scalar binary round-trips;
  `tools/stage_recovery.py` makes a separate SQLite archive. Nothing executes
  SQL or application code from the backup.
- `data/legacy_recovered.sqlite3` contains all recovered rows as exact TEXT/NULL,
  count/content-digest verification and zero detected source key violations.
  Independent Oracle original Import now matches all 30 tables, 93,110 rows and
  1,424,337 fields as typed multisets; schema differences and missing/extra rows
  are zero. `docs/oracle_reconciliation.json` records provenance and hashes.
  Archive bytes and its initial `recovered_unverified` metadata stay unchanged;
  the separate result verifies those bytes, not operational migration or forms.
  It includes confidential source records and is ignored by version control.
- `BIFMA_TRADERS_V3.py` is the one public application launcher, calling
  `bifma.application.main`. One Tk window has **Existing records** and
  **Transactions & setup** navigation; the historical view opens automatically.
  Default path: `<project>/data/legacy_recovered.sqlite3`, independent
  of the working directory. Missing baseline produces an error, never an empty
  replacement. Source V2 database currently has no products/accounts/trades;
  the recovered baseline contains 2 companies, 6,207 items and 703 accounts.
- `bifma/native_ui.py` provides the embedded `NativeWorkspace` for entry/setup:
  setup/login, company and shared masters, users/access, draft review/posting,
  reversals, and native reports. New databases are created explicitly and start
  empty. Read `docs/NATIVE_WORKSPACE.md` for setup and current limitations.
  The duplicate public launchers `BIFMA_TRADERS.py` and `BIFMA_HISTORY.py` were
  removed. Original V2 source bytes are preserved as the non-runnable
  `reference/original_v2_source.py.txt`; its database remains untouched.
  `NativeApp` remains a thin internal Tk host for component tests/diagnostics,
  sharing the same implementation as the embedded frame.
- `native_schema.py` is at migration 4, with immutable posted records,
  exact decimal TEXT, audit, snapshots, explicit stock pools and posting order.
  `security.py` handles secure credentials, expiring persisted sessions and fresh
  company/module/action permissions. Legacy passwords are not imported.
  Migration 3 adds optional company/account contacts and tax identifiers.
  Migration 4 adds four explicit cash document kinds by a checked table rebuild;
  migration 1–3 checksums, old rows/snapshots and all immutability guards remain
  unchanged. The verified pre-upgrade backup, bounded foreign-key suspension and
  real interruption/retry checks are described in `docs/CASH_TRADES.md`.
- `masters.py` validates admin-only setup, hierarchies and stale edits.
  Base-unit identity is frozen once an item has posted lines, even if reversed;
  large-pack factors remain editable without reinterpreting stored quantities.
  `transactions.py` implements drafts/posting for trades, constrained linked
  returns, opening stock, journals and cash/bank vouchers, plus exact reversals.
  Standalone breakage receive/dispatch now use the separate breakage pool and
  source-supported account mappings. BR allows its header breakage deduction
  and one receiving store; unsupported freight/discount/linkage is rejected.
  It serializes availability checks including later dates for backdated posts.
  Unsupported charges, settlements and delivery links are rejected explicitly.
- `native_reports.py` reports posted native trial balance, account/item ledgers
  and quantities; item ledger supports dated/paged running quantities, historical
  inactive selectors and saved labels. See `docs/NATIVE_REPORTS.md`.
  It does not merge historical balances or assert inventory valuation/COGS.
  Sub-trial balance follows current parent links iteratively, includes descendant
  subtotals, and counts direct postings only once in grand totals.
  `posting_rules.py` contains exact balanced new-trade/settlement primitives.
  Dev5 adds account balances grouped by current city (`trial_balance/read`)
  and a cash book over every posted journal line for the company's configured
  eligible cash account (`ledger/read`). The 2026-11-18 walkthrough update
  opens Cash Book directly on a single-date filter and Party Ledger directly
  on Date From/Date To plus account Find; both keep the existing report service,
  opening/closing calculations and full export. A cash-account Find appears
  only when no valid configured cash account is available. City totals keep each account's closing
  debit/credit separate. Excluding zero closings excludes those accounts' entire
  opening/period contributions too. Former cash roles require matching company,
  actual journal-account and saved snapshot evidence. Full HTML exports preserve
  the filters, exact totals and fresh grants. Read `docs/CITY_CASH_REPORTS.md`;
  these reports do not imply invoice aging, payment allocation or original layout parity.
- `document_export.py` writes standalone print-ready HTML for authorized posted
  documents using saved bilingual labels, exact totals, accounting/stock and
  reversal references. Sale output uses a compact sale-bill layout and omits
  internal accounting/stock tables; other document kinds retain their saved
  detail sections. UI has Save printable copy; original print parity and target
  printer checks remain open. Output never overwrites a file.
- `report_export.py` creates complete printable trial/sub-trial balance, account
  ledger and stock/item ledger reports from one authorized snapshot, with current filters,
  exact totals, a 100,000-row explicit refusal limit, and final access recheck.
  It uses private nonoverwriting publication; the UI has Save full report.
- `audit.py` supplies an administrator-only paginated audit browser with exact
  action, UTC date, company/global and literal search filters. Current labels are
  separate from unchanged stored events; only live auth heartbeat may change.
  The UI exposes Audit log and event details.
- `native_backup.py` performs admin-authorized consistent backups and restores
  to new private files. Restore authenticates an active backed-up administrator,
  revokes every copied session and preserves original database/WAL content.
  GUI actions are available; actual Windows/restore-drill qualification remains.
- Earlier packaging work (now outside the active task): `packaging/`,
  `requirements-build.txt`, `tools/check_release.py` and the
  Windows/Linux release documents define isolated allowlisted portable builds.
  Version `0.3.0.dev5` is built for both targets. Linux passed 491 application
  tests, 50 packaging tests, source GUI and all seven frozen checks. Windows
  CPython under private Wine 11 passed 491 application tests (two POSIX skips),
  35 platform packaging tests (two Linux-host-only skips), 50 host packaging
  tests, source GUI, all seven frozen checks and extracted ZIP startup checks.
  Both manifests have identical 41-file source inventories, including the same
  24 application inputs. Neither artifact contains business records.
  Linux's independently extracted package also passed the full frozen workflow.
  The Windows ZIP independently passed checksum/CRC, confined extraction,
  inventory, all 28 AMD64 PE files and current input hashes. Native Windows
  acceptance remains open. Current root receipts have since advanced to dev6;
  the immutable dev5 handoff preserves dev5 evidence. See `docs/linux_build_verification.json`,
  `docs/windows_build_verification.json` and `docs/windows_archive_verification.json`.
  Ubuntu 22.04.5 x64/glibc 2.35 is the Linux baseline; other distributions remain
  unqualified. Both releases remain development versions.
- `bifma/database.py` supplies explicit read-only sessions, atomic writes and
  verified non-overwriting backups. `bifma/calculations.py` contains tested exact
  Decimal new-entry rules with explicit discount/rounding formats.
- `bifma/legacy_repository.py` provides company-scoped historical document queries
  and shared masters; `bifma/legacy_stock.py` reconstructs recovered stock views
  and their unusual valuation rules without changing history.
  `bifma/legacy_ledger.py` reconstructs the original ledger including its anomalies;
  it is not the corrected new-transaction posting engine.
  Real Oracle view comparison now matches all rows of STOCK (56,752), STOCKB
  (3,475) and LEDGER (28,525), including duplicate multiplicities and all mapped
  fields. `docs/oracle_report_parity.json` pins reviewed source/Python hashes.
  All 37 independent Oracle aggregate/filter scenarios also match; see
  `docs/oracle_aggregate_parity.json`. Both source companies and an absent
  company are covered. Owned Oracle containers/volumes and confidential
  exports/logs/credentials were removed; cached official images remain.
  This is dataset-specific compatibility evidence, not original screen/print
  parity; the purchase detail view has no rows in this backup.
- `docs/MIGRATION_READINESS.md` records count-only mappings and cutover gaps.
  All-time regular stock has 4,315 negative company/store/item groups; 102
  stock/opening rows use units differing from current item masters. Both
  all-time trial balances are balanced. Native valued stock openings also post
  journals, so they must be reconciled jointly with account openings. No cutover
  date, inventory value policy, mapping or migration is implicitly approved.
  No native migration importer exists yet; archived invoice obligations cannot
  automatically serve as native linked-return originals.
- Owner explicitly chose on 2026-10-02: **correct new transactions; preserve
  history**. Implement balanced new postings and correct stock directions without
  changing historical rows or historical compatibility calculations. This is an
  accepted requirement, not a question to ask again.
- GUI preview now has 15 sections (including separate cash receipts/payments, returns and stock
  summary), background stock report loading with date/store/search filters, and
  scrollable navigation. Last full smoke: 2 companies, 30 screen visits, 11 document
  dialogs and 4 stock reports. Native UI smoke also passed setup/sign-in/opening
  stock/sale/report/sign-out, a separate read/post approver, reversal and backup/
  restore, verifying total 34 and remaining regular quantity 8 in a synthetic
  database. Expanded smoke passed six posted documents with breakage quantity 2,
  item/sub-trial reports, audit, printed contacts/full reports, permission editor
  and large/base quantity conversion through real widgets.
  Expanded UI checks also pass posted-source paging, administrator-role edits,
  retained inactive-company grants, stored-rate selection and stock filters.
  Dev5 full run: 491 tests, including 19
  actual process-crash/startup-boundary tests. Extractor: 7; independent Oracle
  comparator: 17; packaging: 50. All passed.
- Unit/integration tests use synthetic data and temporary files. Commands are in
  `README.md`; GUI smoke tests require a working graphical display.
- Crucial source differences: line discounts are percentages; Oracle views round
  subtotal sums to whole units; some counterpart postings are inconsistent;
  `STOCK` excludes delivery-linked sales while the delivery arm is commented out;
  both breakage arms are inward in `STOCKB`; purchase cost uses the highest eligible
  numeric ID, not weighted-average/latest-date cost. Do not silently correct these
  and call the resulting history identical.
- Original forms, report templates and workflow evidence are missing. The owner
  was asked whether the original installation/screens are accessible; no answer
  to that specific question has been received yet. Exact parity and production
  acceptance remain open. Docker image listing is available with sandbox
  escalation. Isolated original imp verification is complete. Independent table,
  three historical-view and 37 filtered aggregate comparisons all passed;
  source/archive bytes remain unchanged and private verification data was cleaned.
  Consult `docs/ORACLE_RECONCILIATION.md`.
  Read-only searches of the Desktop, Wine installation, /mnt and /media found
  no original Forms/Reports assets. Recovered LOCATION rows point to `D:\BT\`
  and reports under `D:\BT\REPORTS\`; these are the useful original directories
  to obtain. Wine's installed executables are runtime utilities, not this app.
- A separate pending question asks whether new partial returns should prorate
  original header adjustments or let staff enter them. No answer yet; current
  service rejects returns against header-adjusted originals rather than making
  that business-policy decision implicitly.
- Owner initially chose **both Windows and Linux** on 2026-10-02, then explicitly
  superseded that work with source-only delivery through `BIFMA_TRADERS_V3.py`.
  Packaging is now owner-managed. Windows x64 and Linux x86_64 portable development
  builds are available. Each requires actual target
  installation, display, Urdu/font, printer and restore qualification.
- Windows qualification work fixed nontruncating writable-handle `fsync`
  (`r+b`) and explicit SQLite fixture closure/native permission cleanup. Original
  application assertions and publication guards remain intact. Signed privately
  extracted WineHQ 11 supplies CopyFile2 needed by Windows CPython 3.13.16;
  installed Wine 6 could not create its venv. See `docs/WINDOWS_RELEASE.md` and
  `docs/windows_wine11_attempts.json` for preserved failed-gate evidence.
  The final Windows dev5 archive is
  `build/windows/run-20261002T160849Z-431026ba447a/BIFMA_TRADERS-0.3.0.dev5-windows-x64-DEVELOPMENT-WINE.zip`,
  15,777,127 bytes, SHA-256
  `98dc74675c145f7f01e95c1c69ef6a6a2fc6249d3045592cad80671334f8ef81`.
  It remains `built_under_wine: true`, `native_windows_qualified: false` and
  `production_ready: false`. Linux dev5 is
  `build/linux/run-20261002T133804Z-6b84588ef23c/attempt-20261002T160813Z-c0c52d66b362/release/BIFMA_TRADERS-0.3.0.dev5-linux-x86_64-DEVELOPMENT.tar.gz`,
  13,953,609 bytes, SHA-256
  `392d62110e2b3d1972f366a382e30fb7fca3e74e8e03a15616b3845173397651`.
  The shared source inventory fingerprint is
  `fcabc4cc332b771407e3f8d8f847ae9de4c760cf6b38c7e3ea68b3999fe4eb9a`.
- The shared-live-data versus separate-local-databases question is pending.
  Current native workspaces use local SQLite; no server or network sharing
  architecture has been implemented or implicitly selected.
- `docs/OPERATOR_GUIDE.md` is a synthetic, repeatable Windows/Linux evaluation
  walkthrough. Its five core documents produce quantity 13, cash 1036 and equal
  period debits/credits 1222; an extra sale and reversal preserve balances and
  make period totals 1262. Services and restored-copy checks passed independently
  of the existing GUI smoke. Actual distributed-build operator acceptance remains.
- Version dev3 introduced combined sales/returns and purchases/returns detail
  reports and large-list search. Saved labels/base units, signed reversals,
  grouped quantities, filtered-header omission and fresh export grants remain
  covered. The GUI smoke covers 205-choice English/Urdu search and minimum-size
  row visibility. See `docs/TRADE_DETAIL_REPORTS.md`.
  Earlier development checkouts and immutable dev3 artifacts remain preserved.
  Historical evaluation folder `dist/development-0.3.0.dev5/` collects both unchanged
  dev5 archives, checksums, receipts, synthetic screenshots and operator notes.
  Independent review verified all 24 files, 23 manifest entries, 30 companion
  links/anchors, both complete bundle inventories and the strict workflow
  evidence; see `docs/dev5_handoff_verification.json`. Dev3 and dev4 still match
  every entry in their own manifests.
  Extract each platform into its own folder: both archives have a
  `BIFMA_TRADERS` root. No separate Python is needed for either portable package.
- Version `0.3.0.dev4` introduced distinct counter sales, cash purchases and
  linked cash returns. The company cash account is checked at save/post; cash
  originals retain optional receipt name/mobile. Returns inherit the original
  cash binding and contacts; reversals preserve stored effects. Older ordinary
  invoices are not reclassified. Separate per-kind numbering permits source-
  supported cash/account overlaps. Full cash is posted once; no automatic second
  voucher, tender/change, split payment or invoice allocation is implied.
  Explicit account/cash/all trade report filters and saved printable cash details
  are implemented. Combined sales access requires sales + counter_sales grants
  and, when selected, sales_returns. See `docs/CASH_TRADES.md`.
- `bifma/diagnostics.py` implements a fixed synthetic real-widget workflow shipped
  with the native launcher. `--check-workflow --output NEW.json` accepts no data,
  credential or script input. Its v2 protocol covers 17 steps including widget
  lifecycle, cash book/city reports and their exports, cash postings, linked
  returns/reversal, exports and cash-inclusive backup/restore. Successful evidence
  is written only after temporary data cleanup. All three build recipes now
  require seven shared frozen gates; see `packaging/DIAGNOSTICS.md`.
  Current source and both actual frozen runtimes passed. The final synthetic
  database has 11 posted documents; restore has nine, with zero copied sessions
  before fresh login, five cash documents/unchanged contacts, stock 7 and cash
  closing -34 in the cash company. This fixed scenario does not prove every
  business workflow or actual workstation acceptance.
- The optional operator walkthrough cash extension has independently checked
  service totals: 12 posted documents, stock 13, cash 1056, equal period debit/
  credit totals 1382. `docs/cash_walkthrough_verification.json` records this
  synthetic result; actual target-machine operator acceptance remains open.
- Additional count-only source investigations are in `docs/DELIVERY_EVIDENCE.md`
  and `docs/CITY_CASH_REPORT_EVIDENCE.md`. There are no dispatch records or linked
  sales in the archive; DCTOSALE_BAL includes unlinked sales and cannot be labelled
  outstanding challans. Exact native account balances by current city and a cash
  book over all journal activity are now implemented for native data, without
  inferring invoice aging. Original report queries/layouts remain absent.
- Dev5 was reviewed in `build/development/dev5-ledger-reports` and integrated
  into root on 2026-10-02. `build/development/dev5-integration.json` records the
  changed inputs. Schema remains 4; original business files and dev3/dev4
  archives remain unchanged. Both dev5 packages passed their full required gates;
  41 source and 37 verification inputs match exactly across platforms. Linux
  independently passed the full workflow after extraction. Windows ZIP checksum,
  contents, all 28 AMD64 binaries and matching inputs passed independent audit;
  its runtime evidence remains Windows CPython under Wine, not native Windows.
- Native Tk variables now belong to their consuming widget and dispose Tcl
  traces/variables on that widget's destruction. Interpreter/root references
  detach there so delayed worker garbage collection cannot call Tcl through
  disposed variables. Application shutdown joins its worker before destroying
  Tk. The fixed `native-workflow-v2` diagnostic requires 17 ordered steps,
  including real widget destruction/worker collection and both new reports'
  full/filtered exact HTML totals. It rejects ignored Python finalizer errors
  through final cleanup. Posted/restored counts remain 11/9. Actual source
  workflow and minimum-window checks passed without cleanup errors. Both actual
  frozen runtimes also passed all 17 steps, with lifecycle=true and cleanup=true.
- Operator guide section 8 independently reconciles the cash extension's seven
  cash movements (1106 in, 50 out, closing 1056), seven account rows, 1382 period
  debit/credit and 1226 separate closing debit/credit totals. Assigning cash to a
  city yields 1056 debit there, and 170 debit/1226 credit unassigned. See
  `docs/city_cash_walkthrough_verification.json`. These synthetic guide totals
  differ intentionally from the packaged diagnostic's smaller scenario.
- `docs/TRADE_CHARGES_EVIDENCE.md` records aggregate-only source investigation:
  labour is NULL throughout, purchase tax has no examples, and received amounts
  occur only on three full cash sales. No recovered view/function calculates
  those fields. New labour/tax/partial-settlement policy cannot be inferred.
  Existing direct cash and separate vouchers remain the established paths.
- Read-only preview lifecycle probing found no worker-finalization errors during
  screen changes or close with an active stock read. Its final Tcl variable traces
  retain the destroyed root until standalone process exit; this narrower cleanup
  limitation is separate from the fixed native widget lifecycle. No preview
  source changes were made during the dev5 build freeze.
- Dev6 was reviewed in `build/development/dev6-widget-lifecycle` (121-file dev5
  baseline, no business data) and integrated into root at 16:36:48 UTC on
  2026-10-02. `build/development/dev6-integration.json` pins seven changed inputs.
  The new `tk_lifecycle.owned_variable` helper is shared by both workspaces;
  native_ui re-exports it for the unchanged diagnostic. Preview variables belong
  to their screen widgets. Shutdown cancels queued work, responsively polls all
  running stock reads (including superseded requests), joins the worker and
  destroys Tk on its thread. Constructor/smoke-error cleanup waits only for its
  own window and preserves any unrelated root. Four real-Tk subprocess tests
  passed without skips, proving trace/root release, responsive close/direct
  destroy, worker collection and unchanged synthetic archive bytes. Adjacent
  52 tests, 50 packaging checks and synthetic historical smoke passed. Root
  source at integration was dev6. Both dev6 builds completed before the owner's
  source-only instruction: Linux ran 495 application tests without skips;
  Windows CPython under Wine ran 495 with two existing POSIX-only skips. All four
  preview lifecycle tests ran on both. Frozen workflow and archive checks passed;
  root build receipts describe dev6. The manifests have 42 source/25 application
  inputs and 38 verification inputs. No combined dev6 handoff was assembled;
  packaging work stopped at the owner's request. These artifacts are historical
  evidence and do not include the subsequent dev7 launcher changes.
- Source dev7 makes `python3 BIFMA_TRADERS_V3.py` open `NativeApp` for daily entry
  and reports. `BIFMA_HISTORY.py` preserves the separate historical browser.
  The main welcome hint, README and setup guides use these source commands.
  First launch creates no database automatically: the user chooses Create new
  database, creates an administrator, then sets up company/accounts/items.
  No schema or posting rules changed; native schema remains 4.
  Verified the actual V3 entry script in a real Tk event loop from an empty
  temporary working directory: welcome, explicit database creation, administrator
  creation and the company setup screen passed. No files appeared before choosing
  Create new database. The six-document native UI smoke then passed entry,
  reporting, audit, permissions, printable output and backup/restore. Temporary
  test data was removed; V2 source/database, Oracle export and recovered archive
  hashes stayed unchanged. Both source launcher help commands and README links
  were checked. No packages were rebuilt for this revision.
- Source dev8 supersedes that dev7 default after the owner rejected the empty
  database selection/setup flow: V3 now opens the already-used recovered baseline
  automatically, retaining its values and company selector. `BIFMA_HISTORY.py`
  is equivalent; the native workspace remains under `BIFMA_TRADERS.py`.
  No importer, account/stock cutover or combined historical/native posting is
  implied by this startup change. A clarification asks whether the immediate
  request also includes posting new transactions against the original balances;
  no response has been received yet. Preserve this distinction in explanations.
  Verified the real V3 entry script from an empty temporary working directory:
  it automatically opened the project archive, found 2 companies/6,207 items/
  703 accounts, displayed no file picker and created no empty database. Full
  historical smoke passed 26 screen visits, 7 document dialogs and 4 stock
  reports with no stderr. V2 source/database, Oracle export and recovered archive
  hashes remained unchanged. Source syntax, help and README links passed.
- Remaining transaction workflows, printing/full reports, verified migration,
  backup/restore qualification and business acceptance remain parity gaps.
  Windows/Linux packaging and platform qualification are owner-managed.
- Source dev9 fixes the reported Sale Voucher popup error. Reproduced against
  the existing baseline with actual X11/XTest double-click input: document data
  loaded, then immediate `grab_set()` raised `TclError: grab failed: window not
  viewable`. Ordinary Tk `event_generate` and direct/Return opens did not expose
  this, because they did not reproduce the X server's held mouse-button grab.
  Voucher dialogs now acquire input/focus only on their own Visibility event;
  closed/closing windows cannot activate, and no nested wait loop is introduced.
  Handled document events return `break`. Callback messages distinguish SQLite
  read failures from interface errors without exposing source record values.
  `tests/test_preview_documents.py` covers real double-click, Escape/Return/Close,
  delayed visibility, close-before-map and error classification using synthetic
  data. Together with existing preview lifecycle/repository tests, 15 checks
  passed with no skips. The exact baseline voucher pointer reproduction passed
  after the fix and left the archive hash unchanged. No schema, data, accounting
  rules or default-baseline startup behavior changed; no packages were rebuilt.
- Source dev10 adds **Financial → Cash Receipts / Cash Payments** to V3's
  existing-baseline browser. They filter JMST by ENTRY_TYPE 3/CRV and 2/CPV,
  with company/type checks on both lists and detail retrieval. The former mixed
  Journals list is labelled **All vouchers**, retaining all journal/cash/bank/
  opening records. Cash list totals sum full voucher detail CR/DR respectively
  with Decimal precision 1000, never floating-point SQL sums or rounding.
  Literal account code/name/Urdu/narration search uses EXISTS, so matching several
  details cannot duplicate headers or limit the amount to matching lines.
  Detail views show received/paid total, configured cash account, stored DR/CR
  lines and source cancellation/ledger inclusion. Generated cash counterparts
  stay separate from stored detail rows; non-NULL cancellation markers remain
  visible but excluded from the historical ledger, including empty string/'0'.
  Verified baseline counts: 524 receipt and 524 payment vouchers. Eight new
  repository regressions, 28 existing repository/ledger tests and nine real-Tk
  document/lifecycle checks passed (45 total, no skips). Full archive UI smoke
  passed 30 screen visits, 11 documents and four stock reports. Original V2
  source/database, export and recovered archive hashes remained unchanged.
  This adds historical browsing sections, not new posting to the baseline.

- On 2026-10-03, consolidated the maintained source into one public launcher
  (`BIFMA_TRADERS_V3.py`) and one Tk application window (`bifma.application`).
  Existing records opens the original archive automatically; Transactions &
  setup lazily embeds `NativeWorkspace` as a frame in that same interpreter.
  `_NativeInterface` is the shared implementation; `NativeApp` is only the thin
  internal test/diagnostic host. Switching retains forms, search state and auth
  session; active or pending document dialogs prevent switching. Native login
  Enter bindings belong to entry controls, and styles are reapplied for the
  visible area. Native dialogs/searches now defer input grabs until visibility,
  including held-pointer double-clicks, and restore prior modal grabs on close.
  Root shutdown drains both workers before destroying Tk. Failed lazy native
  initialization cleans its frame/worker and leaves existing records usable.
  Removed duplicate public `BIFMA_TRADERS.py`/`BIFMA_HISTORY.py` launchers; moved
  original V2 source bytes into the non-runnable reference, without changing its
  database. Build-input manifest/tests were adjusted only for removed/new source
  paths; no builds or platform qualification were performed. No new numbered
  code copy or version folder was made. Full application suite: **513 tests,
  zero skips, passed**. Five composed-window tests include the complete six-
  document native widget workflow, retained form/login, actual pointer input,
  failed initialization and simultaneous-worker shutdown. Fifteen existing
  manifest/publication checks also passed. The exact V3 entry script and full
  historical smoke ran from a temporary working directory: 2 companies, 30
  screens, 11 documents, four stock reports; no database picker or empty database
  was created. Original baseline/export/V2 database and preserved source hashes
  remained unchanged. Consolidating the interface does not merge native and
  historical stock/account balances; that remains incomplete.

V2 source bytes and its SQLite database remain unchanged; source moved to
`reference/original_v2_source.py.txt` on 2026-10-03. The following details are a
baseline description of that prototype, not the new architecture.

## Purpose and structure

- Local desktop management application for a wholesale plastic business.
- All application code is in `BIFMA_TRADERS_V2.py` (161 lines at review).
- Python standard-library dependencies: `tkinter` / `ttk` / `messagebox`,
  `sqlite3`, and `datetime`. Tk support and a graphical display are required.
- `App` subclasses `tk.Tk`. Its sidebar switches screens by destroying the
  children of `self.main` and rebuilding that screen. Event handlers are mostly
  nested functions that capture widgets and `StringVar` objects.
- UI, validation, SQL, and business logic live together. There is no separate
  service layer, API, migration system, or automated test suite in the workspace.
- Its former launch command was `python3 BIFMA_TRADERS_V2.py`; the preserved
  reference is no longer an active launcher. Use V3 for the maintained app.
- `DB = 'bifma_traders_v2.db'` is relative to the process working directory,
  not the Python file. Launching elsewhere can create a different database.
- The main guard initializes the database, then starts the Tk event loop.
  Importing the module alone does not launch the UI or call `db()`.

## Code map

Original line numbers are navigation hints and may move after edits.

| Symbol | Line | Responsibility |
| --- | ---: | --- |
| `db()` | 9 | Open SQLite connection, create tables, seed defaults, commit |
| `fmt()` | 27 | Format a numeric value with commas and two decimal places |
| `App.__init__()` / `build()` | 30 / 36 | Window, styling, sidebar, initial dashboard |
| `clear()` / `heading()` | 44 / 46 | Shared screen helpers |
| `dashboard()` | 49 | Product/account counts, stock quantity, sales/purchase totals |
| `products()` | 58 | Add and list products; record opening stock |
| `accounts()` | 81 | Add and list customers/suppliers |
| `trans(kind)` | 99 | Shared sales/purchase form, item lines, totals, persistence |
| `stores()` | 139 | Add and list stores and units |
| `reports()` | 158 | All-time aggregate totals displayed as text |

## Data model

All nine tables use integer primary keys. Relationships are represented by ID
columns but have **no declared foreign-key constraints**, despite `db()` enabling
`PRAGMA foreign_keys=ON` on each connection.

| Table | Main contents and relationships |
| --- | --- |
| `stores` | Unique code, required name |
| `units` | Unique required name |
| `products` | Unique required code, required name, unit text, sale/purchase rates, global stock quantity, store ID |
| `accounts` | Unique code, required name, type, mobile, phone, address, balance |
| `sales` | Unique bill number, date, account/store IDs, discount, freight, total, received, balance |
| `sale_items` | Sale/product IDs, quantity, rate, discount, line total |
| `purchases` | Unique bill number, date, account/store IDs, discount, freight, total |
| `purchase_items` | Purchase/product IDs, quantity, rate, discount, line total |
| `stock_ledger` | Product/store IDs, date, reference type/ID, quantity in/out, note |

`db()` runs schema creation and seeding on every call, including calls from
screens that only read data. If stores are empty it inserts `SHOP` and `GODOWN1`
(display name `GODOWN #1`). It inserts missing default units on every call:
`PCS`, `CARTON`, `DOZEN`, `PACKET`, `KGS`.

At review, the existing database matched the source schema and contained two
stores, five units, and no rows in the other tables. This is a dated observation;
recheck it rather than assuming the database remains empty.

## Workflows and calculations

- Products require code and name. Blank unit defaults to `PCS`; blank rates and
  opening stock default to zero. A nonzero opening stock also creates an
  `OPENING` ledger entry with no reference ID.
- Account types offered are `CUSTOMER`, `SUPPLIER`, `BOTH`, and `OTHER`.
  Only the account name is explicitly required by the form.
- Sales offer customer/both accounts; purchases offer supplier/both accounts.
  Both forms offer all products and stores. Dropdown labels use `code - name`,
  and save handlers resolve IDs by querying that concatenated label.
- Item entry requires a selected product and quantity greater than zero. Rates
  are entered manually and initially zero; product master rates are not loaded.
- Line total = quantity × rate − line discount. Discounts are absolute amounts.
- Transaction total = sum of line totals − header discount + freight.
- Sale balance = total − received. Purchases force received to zero, although
  the shared UI still displays a Received field; purchases do not store payments.
- Totals recalculate only through Calculate Total or Save, not on each change.
- Save requires a nonblank bill number and at least one line. Account selection
  is optional in practice; unresolved account/store labels yield null IDs.
- Save inserts the header and detail rows, updates `products.stock`, and inserts
  ledger movements before committing. Sales subtract quantity and use `SALE`;
  purchases add quantity and use `PURCHASE`. Dates use local system time as
  `YYYY-MM-DD`. Ledger notes contain the bill number.
- Successful save shows a message and returns to the dashboard.
- Reports show product/account counts, summed stock, sales total/received/
  outstanding, and purchase total. There are no date or store filters.

## Known limitations and pitfalls confirmed in source

- `accounts.balance` is displayed but never updated by transactions. There is no
  account ledger, later payment workflow, or supplier payable tracking.
- Stock is a single quantity per product. Transaction store IDs appear in ledger
  entries, but stock availability is not maintained separately for each store.
- Sales allow stock to become negative. Most numeric fields lack checks for
  negative, non-finite, or otherwise unreasonable values.
- Money and quantities use Python floats and SQLite `REAL`, with no explicit
  decimal rounding policy beyond display formatting.
- `products.refresh()` clears the tree only inside the condition that selects
  the initial store. Later refreshes append existing rows again, causing visual
  duplicates after adding a product.
- A missing product during transaction save is silently skipped, while the
  header total still includes its line.
- Save/add error handlers generally show a dialog but do not explicitly roll
  back and close connections on failure. The direct Calculate Total callback
  and store/unit insertion callbacks lack equivalent error handling.
- Product units are free text; the units master does not constrain them.
- New store codes use timestamps rounded to seconds, which can collide.
- No edit/delete flows, transaction history/detail view, line removal, printing,
  exports, stock transfer screen, or stock ledger screen are implemented.
- UI labels reference legacy FAIT `PUR_ITEM` and `CHART` concepts, but no FAIT
  backup importer or legacy schema mapping is implemented or available here.

## Review scope and working guidance

The review read all application source, parsed it with Python's AST, and checked
SQLite schema and row counts through a read-only connection. Tkinter module
availability was confirmed. The GUI and interactive workflows were not executed.
No application code or existing database data was changed during this review.

For future checks, avoid using `db()` against the real file just to inspect it,
because it performs writes. Use SQLite URI `mode=ro` for inspection. For behavioral
tests, point the module's `DB` variable at a temporary database before calling
`db()`. Existing behavior and the limitations above are context for future tasks,
not instructions to make unsolicited changes.
