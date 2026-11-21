# Project context

Read `PROJECT_CONTEXT.md` before working on this application. It contains the
architecture, transaction rules, code map, and known limitations reviewed on
2026-10-04. Check the current source before relying on these notes, and update
them when application behavior or architecture changes.

Maintain one application and one public entry point: `python3 BIFMA_TRADERS_V3.py`.
The owner's 2026-10-03 instruction is to test one codebase as a whole, then build
Windows/Linux packages themselves. Do not create parallel version folders or
separate product launchers. Shared modules under `bifma/` are internal components.
The application automatically prepares/reuses `data/bifma.sqlite3` from
`data/legacy_recovered.sqlite3`, then opens one module menu after login. Original
and new records appear together in each sales, purchase, return and voucher
section's Saved records browser, requested with F8. Do not restore separate
Workspace/History navigation. First use requires a new owner login, not database
selection or empty company setup. Original details remain unchanged and are
opened through the verified, authorized `DocumentRegister`; dated stock reports
combine original movement activity and new postings without repeating baseline
closings. Account and item ledgers retain the explicit cutoff opening snapshot.
The duplicate `BIFMA_TRADERS.py` and `BIFMA_HISTORY.py` launchers were removed.
Original V2 source bytes are preserved in `reference/original_v2_source.py.txt`;
its database remains unchanged. The earlier instruction still applies: use the existing data as baseline,
without selecting or creating an empty database on normal startup. Historical
browsing remains read-only. Sealed stock/account closings now fund new postings;
original documents are not replayed through corrected rules. Read
`docs/BASELINE_INTEGRATION.md` for cutover, negative-stock policy, historical
return eligibility, unknown rates and reconciliation. The owner approved
staff-entered return adjustments capped at original totals and original
purchase-rate valuation on 2026-10-03. The latest scope instruction is to add
only capabilities supported by the original application; do not invent
unproved tax/labour or other workflows. The delivery is runnable Python
source; the owner will handle platform packaging themselves. Do not
resume Windows/Linux packaging or qualification unless requested. Read
`docs/REBUILD_PLAN.md` for the full functional scope, and
`docs/NATIVE_WORKSPACE.md` and `docs/TRANSACTION_SERVICES.md` for its implemented
scope and constraints. Complete original-application parity is not yet verified.

The owner's 2026-10-04 login photo uses a compact User → saved User Name →
Password → Company/OK flow. `LoginForm` uses code-drawn keyboard/cart artwork
and BIFMA branding. Existing names are read-only; initial owner names are editable.
Enter on the initial owner's final password-confirmation field runs Create
administrator directly; validation failure keeps the form open.
Changing User clears the previous profile/password, and stale lookup replies
must not replace a changed identity. `SecurityService.login_profile` exposes only
active saved name/company choices; it is never authorization. Actual login must
verify the password and fresh company access. Keep logout clean and preserve the
single app/worker shutdown lifecycle.

The owner removed the 15-character requirement. Usernames accept 1–80 supported
characters; passwords accept 1–1,024 NFC characters, with blanks rejected and
existing common/context checks retained. Do not restore the old minimum. Schema
6 permits short usernames while preserving all existing hashes/data/IDs and
migration 1–5 checksums. Startup explicitly upgrades verified schema-5 files,
with a sealed-baseline/source check under the write lock and verified backup
before changes; ordinary service constructors do not migrate. Current files and
concurrent completed upgrades are verified/reused; older files retain manual
upgrade. See docs/PROCESS_RECOVERY.md and tests/test_username_migration.py.

The owner's photo-based instruction on 2026-10-03 places navigation in
a native top menu bar: **File**, **Coding**, **Financial Transactions**,
**Inventory**, **Reports** and **Setup**. Do not restore the left sidebar.
Coding nests Financial Coding and Inventory Coding, with Cities directly below. Coding contains accounts, items, cities, Main Item Groups, Item Sub Groups,
units, stores, company stores and opening stock. Financial Transactions contains
exactly Opening Balance, Cash Payment Voucher, Cash Receipt Voucher and Journal
Voucher, in that order. Inventory contains the existing trade, cash-trade,
return and breakage modules. The sale module is labeled **Sale Voucher** in
the menu, form and saved-record browser; its internal `sale` kind and `sales`
permission remain unchanged. Reports has Voucher Printing and groups individual
reports under Financial reports and Inventory reports. Company configuration, users/access, audit and
backups belong to Setup. Master selectors stay within Coding or Setup; do not
restore the mixed Company & item setup selector. Bank receipt/payment entry and
new reversal actions are removed from the public UI, including posted-document
buttons. Keep backend types/services, saved records, reversal status/reference
display and reporting effects for compatibility; this menu change must not
delete or rewrite business history. It does not establish complete original
form/report parity.

Main/subgroup sections reuse product_groups IDs and parent links, with scoped
filtering before pagination and hierarchy validation inside the write transaction.
Do not flatten existing deeper groups or infer original account hierarchy from
code prefixes. Original printable copies must use OriginalDocumentExporter and
the verified DocumentRegister; keep exact recorded values and original company
labels. They are not reproductions of unavailable original print templates.
Sales/purchase detail reports combine original and native activity through
UnifiedReports, preserving missing values and source issues. Trade report date
defaults include all dates; financial/item-ledger cutoff defaults remain intact,
and explicit staff-entered/cleared filters persist when changing reports.
See docs/WORKFLOW_AUDIT.md for the section-by-section evidence limits.

The owner's subsequent 2026-10-03 instruction makes business-entry modules
open on a new-entry form, without automatically displaying saved records.
This includes Coding masters, Setup → Companies, all financial vouchers and
inventory entries. F8 or Saved records (F8) opens the same module's combined
saved-record browser. Keep the unsaved form alive while browsing; Back to entry
or closing the browser restores its fields and lines. After saving a new master,
keeping a new draft or closing a newly posted document, return to a fresh
new-entry form. Read-only users get a blank module landing with F8 browsing,
not an unauthorized creation attempt or an automatic list. Report screens
continue to wait for filters and Run report; users/access and audit remain
explicit administration lists. This changes navigation, not stored data or
permissions, and must not introduce a separate History navigation section.

Cash Payment and Cash Receipt use the shared inline `CashVoucherForm`: voucher
number/date, company, last numeric voucher number, account code/title, narration,
amount, exact total and Print/Save/New/Exit. New cash forms accept two decimal
places; editing retains the saved draft's precision. Save still opens the existing
draft review; Print requires explicit Post and print before exporting a verified
posted HTML copy. Do not invent Cancel or Previous Balance semantics from the
photos. F8 preserves the inline rows, and New/Exit confirms discarding changed
fields. Shared Choice Find is parented to its actual form and explicitly focuses
the editable query only after visibility. Clicking selector text and typing or
pasting opens that live Find; stable account/item IDs change only upon selection.
Typing/pasting while the selector's dropdown is already open must also reach
Find. Close the native popup before transferring its input grab; keep dropdown
bindings local to the owning Choice and dispose them with that widget.
The owner's latest 2026-10-04 instruction requires keyboard control throughout
the interface. Shared Form Enter/Keypad Enter navigation is enabled by default:
advance to the next editable field and invoke the form's existing Save, Use line,
Show or OK action at its final field. Successful modal completion closes that
dialog; validation failure retains its input. Checkbox Enter advances without
toggling (Space toggles); Tab/Shift+Tab remain available. Read-only generated
codes and disabled controls are skipped. Shared Choice Find defaults to live
Enter selection; selected controls advance on Enter, while F4 always opens Find.
Mouse selection must not silently submit a form. Restore focus/grabs after
nested Find, completion and cancellation, and keep focused fields visible in
scrolling forms. Preserve a return source picker's search and selection when
line entry is cancelled or rejected.

Cash Amount Enter advances to the next account row; Enter on an untouched blank
account-code row saves the populated voucher. Sale blank-item-code Enter moves
to header discount, breakage and freight; final freight Enter saves. Ctrl+S
saves any voucher for review. Generic header completion opens Add line; final
line completion applies the validated line and closes its dialog. Draft review
initially focuses Keep as draft; posting remains the explicit Post action.
Escape closes through the dialog's existing close/discard guards. Top-menu
mnemonics are Alt+F/C/T/I/R/S, F10 opens the menu, Ctrl+L focuses Company, and
Ctrl+Tab moves between notebook pages. Preserve modal, permission and worker
guards for shortcuts, and retain F8 saved-record browsing with unsaved entry.

The owner's 2026-10-04 Sale Voucher photo requires inline sale rows and Enter
navigation through Sale #, Date, Buyer Find, Item Find, unit, quantity, rate and
discount to the next item. `SaleVoucherForm` uses the real base unit, quantity 1
and known sale rate; missing rates stay blank. The owner's subsequent instruction
removes all Store controls and Store Find from Sale Voucher; Buyer selection
opens Item Find directly. `MasterService.lookup` provides the evidence-based
`default_sale_store_id` for new rows. Preserve each existing draft row's store ID
and use it for stock previews and posting. Do not guess a location or activate an
inactive membership when setup is ambiguous or unavailable. Shared Find
commits Enter selection after closing/restoring its popup. Keep existing
draft precision, exact calculations and the review/post path. Optional sale
Bill # is an exact saved reference and appears in issued HTML. F8 Saved Sale Voucher records
automatically supply Date From/Date To over original and new dates and
refreshes these automatic bounds after saves. Explicitly searched or cleared
ranges persist. The owner clarified these are saved-list filters, not two dates
on a new invoice. Do not invent DC or previous-balance workflows from the image.

Startup reuses verified results only within `startup_verification()` while the
same unchanged database is being prepared and its services constructed. Full
recovery, lineage/integrity and immutable baseline checks still run on first
access. File changes, sidecars, aliases and process/thread changes prevent reuse;
the scope ends before login and never bypasses managed-session lifecycle gates.
See `docs/PROCESS_RECOVERY.md` before changing this optimization.

The owner also requires a simple Item form: read-only automatic item code,
description/group/units/prices, and no party, sales-account, purchase-account or
inventory-account dropdowns. Use `MasterService.create_product` for routine new
items; it allocates the next ASCII-numeric code atomically and retains only an
unambiguous established sales/purchase posting pair. Conflicting or invalid
defaults require administrator review, never guessed party accounts. Routine
edits omit code and account fields to preserve them. Generic explicit master
APIs remain available for administrator setup; an empty unconfigured file needs
that setup before its first routine item. Do not change original codes or infer
that the original manual entry workflow has been proven from its stored data.

`bifma_traders_v2.db`, `BT-270926.EXE`, and `data/legacy_recovered.sqlite3` contain
business data. Use read-only access for inspection and temporary databases for
experiments/tests. Preserve exact decimal/text values and distinguish recovered
source behavior from assumptions. Do not silently change source anomalies or
claim production readiness from passing unit or preview tests alone.

Construct native services and run explicit upgrades outside database sessions.
Use the managed `database.session`, `native_session` and `native_transaction`
contexts for application work; their lifecycle gate preserves SQLite locks
during startup recovery. Bare SQLite handles and moving active databases are
outside that contract. See `docs/PROCESS_RECOVERY.md` before changing it.
