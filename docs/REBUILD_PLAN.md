# Faithful SQLite rebuild and original-workflow verification

Updated 2026-10-04. Delivery: one runnable Python application. Complete original
form, workflow and report parity remains unverified; tests alone do not establish
production readiness.

**Current integration:** V3 automatically prepares/reuses the working database
from the original archive and opens one top menu after login. Business modules
open a new-entry form. F8 opens that module's original/new Saved records browser
without discarding unfinished entry; there is no Workspace/History switch.
Sealed original stock/account
closings now support new postings, cash receipts/payments and eligible original-
invoice returns. Dated stock reports combine original and new activity; guarded
periodic profit/loss is available after the cutoff.
See [BASELINE_INTEGRATION.md](BASELINE_INTEGRATION.md) for the exact scope and
remaining source-review requirements. The dated historical progress notes below
describe earlier stages and do not override this current behavior.

**Latest owner rules:** staff enter partial-return header adjustments subject
to original quantity/amount caps; use the original purchase-rate method; keep
the original feature scope and do not invent unsupported labour/tax or unrelated
workflows. Original form/print evidence is still required to certify parity.
The owner requested a portion-by-portion appearance and working-behavior audit
on 2026-10-04. [WORKFLOW_AUDIT.md](WORKFLOW_AUDIT.md) distinguishes the evidence,
implemented behavior, gaps and precise original references needed for all
35 recovered module tokens.

## Objective and evidence

Reproduce the original BIFMA TRADERS application's functionality and workflows,
use SQLite for storage, and improve the UI. The current delivery is runnable
Python source, started with `python3 BIFMA_TRADERS_V3.py`. The coverage below
records the broader parity objective and must not be mistaken for completed work.

`BT-270926.EXE` begins with an Oracle original Export `V10.02.01` header. It is a
database backup, not an executable containing the original forms and reports.
The supplied Python V2 application is an early prototype, not the behavioral
specification for the original application.

The backup supplies 30 table definitions, 6 view definitions, and stored function
source. `docs/LEGACY_ANALYSIS.md` and `docs/legacy_schema.json` record recovered
evidence. Names alone are insufficient proof of behavior. Menu metadata, SQL,
actual screens, report output, and examples of transactions must be reconciled.

Original Cash Payment, Cash Receipt, Coding-menu and Sale Voucher photos have
since been supplied. They establish those visible layouts; the owner also
specified F8, Find and Enter behavior. The original installed program/source
location has been requested in the current audit. Other layouts, hidden button
behavior, printing and some validations still cannot be certified from the
database backup or static photos alone.

**Owner decision, 2026-10-02:** correct accounting and stock rules for new
transactions while preserving historical values. Keep recovered history and its
compatibility reports immutable. The operational posting engine must use
balanced accounting and correct outward breakage-dispatch movements; it must not
silently rewrite the source's historical discounts, quantities, or valuations.

**Latest owner delivery decision, 2026-10-02:** provide code that runs with
`python3 BIFMA_TRADERS_V3.py`; the owner will handle Windows/Linux packaging.
This supersedes the earlier request to work on both platform releases. Existing
build artifacts remain historical evidence; additional packaging and platform
qualification are outside the active task.

**Latest baseline decision, 2026-10-02:** use the existing original data and stop
asking the user to select/create an empty database on startup. V3 automatically
prepares/reuses `data/bifma.sqlite3` from `data/legacy_recovered.sqlite3`.
Historical browsing is read-only; operational integration carries its closings
into new postings while retaining the original details.

## Required functional coverage

**Owner consolidation decision, 2026-10-03:** maintain one codebase and public
launcher, `BIFMA_TRADERS_V3.py`, for whole-app testing. One module menu offers
entry and an F8 original/new Saved records browser in those same sections.
Do not create parallel source versions or separate product launchers. Original
V2 source is retained only as a non-runnable reference; packaging stays with the
owner. Operational baseline integration was implemented after this consolidation.

| Area | Supported scope and evidence to compare |
| --- | --- |
| Company and periods | Company selection, financial dates, cash account, company-scoped data and permissions |
| Products | Automatic item codes, English/Urdu descriptions, groups/subgroups, units/factors and known/unknown rates; routine forms omit posting-account choices and preserve or inherit only proven mappings |
| Accounts | Hierarchical chart, customer/supplier behavior, cities, contact/tax fields, English/Urdu names |
| Stock | Opening quantities/values, per-store balances, dated movement detail, damaged/breakage stock and valuation |
| Sales and purchases | Cash/credit types, numbers/dates, stores, conversion, percentage discount and supported header adjustments; unused labour/tax/received fields do not establish extra posting workflows |
| Returns | Sales returns, purchase returns, breakage return/delivery, references back to original lines |
| Delivery evidence | Schema/linkage and the photographed DC button require an original walkthrough before any dispatch/conversion workflow is implemented |
| Accounting | Opening, journal and cash vouchers, exact debit/credit postings and original financial reports; bank/reversal entry is removed by owner instruction and original cancellation semantics are unverified |
| Users and access | Secure native user lifecycle, menu visibility, company/module/action rights; original passwords are not reused |
| Reports and output | Original report selection, filters, dates, amounts, print formats and bilingual output |
| Operations | Backup/restore, reconciliation, error recovery, audit, managed upgrades and source-run instructions; platform packaging belongs to the owner |

Rows identify supported work and comparisons. Unproved workflows are not an
automatic implementation backlog, and labels do not prove form behavior.
[`FEATURE_PARITY.md`](FEATURE_PARITY.md) maps all 35 recovered module tokens to
the implemented screens and their remaining gaps.

## Architecture decisions

- Preserve original V2 source bytes in `reference/original_v2_source.py.txt`
  and keep `bifma_traders_v2.db` intact. Maintain only the V3 public launcher.
- Build tested application services under `bifma/`, independent of Tkinter.
- Use explicit transactions for complete documents, their stock movements, and
  accounting postings. Reads must not create schemas or seed data.
- Use exact decimal arithmetic and explicit rounding rules. The legacy line
  discount is a percentage; V2's fixed line discount requires separate handling.
- Preserve original codes/IDs and source provenance during migration. Keep raw
  recovery separate from the operational application database. No inferred
  correction to historical data is silently applied.
- Stage any recovered data in a separate new SQLite file. A successful binary
  parse and integrity check do not replace comparison against an authoritative
  Oracle import and original business reports.
- UI construction follows recovered workflows. Improve typography, navigation,
  search, resizing, validation feedback, accessibility, and keyboard operation.
  Do not publish controls for unfinished actions as though they work.
- Deliver the workspace's Python implementation through `BIFMA_TRADERS_V3.py`,
  with standard-library dependencies and documented first-run setup. Packaging
  is owner-managed. Actual printer/Urdu rendering and original output parity
  still need business acceptance before claiming complete compatibility.

## Implementation sequence and acceptance evidence

1. **Recover specification:** extract schema, views, functions, menus and report
   references; create a feature/fidelity matrix; obtain original screen/workflow
   evidence. Keep every unresolved interpretation explicit.
2. **Recover data:** validate structural decoding, exact number/date/text handling,
   row counts, keys and relationships. Use an isolated original Oracle import as
   the authority for complete content reconciliation and financial comparisons.
3. **Maintain domain/storage:** versioned schema and upgrades, exact money/unit
   calculations, atomic writes, store stock, vouchers, permissions and audit.
   Preserve historical reversal/cancellation facts without restoring removed
   entry actions or inventing unproved cancellation rules. Verify atomic failure.
4. **Build the improved interface:** all original modules, safe editing, searchable
   masters, keyboard-friendly invoice entry, document history/detail, useful
   filtered reports, print/export, users, company settings, backup and restore.
5. **Reconcile functionality:** replay representative original transactions and
   compare stock, valuations, account balances, receipt treatment and output.
   Test multiple stores/companies, returns and historical dates. Compare challan
   behavior only after its original workflow evidence is available.
6. **Verify operation:** test realistic data volume, concurrent writers, process
   interruption, disk/database failures, backup restoration and permissions.
   Complete source setup instructions and verify business acceptance against
   the original program. Packaging/target-OS qualification is owner-managed.

## Current progress

- One source entry point prepares/reuses the existing baseline automatically;
  native schema is 6. First use creates a personal owner login. No database
  picker, empty-company setup or parallel product launcher is required.
- Top menus and their grouping follow the supplied photos and owner decisions.
  New-entry/F8 navigation preserves unfinished forms. Bank voucher entry and
  new reversal actions are absent; their recorded effects remain compatible.
- The operational baseline bridge preserves sealed account/stock closings,
  cutoff 2026-09-27, eligible original invoice returns and exact historical
  values. Dated stock quantity/value reports combine original and new movements;
  account/item ledgers retain an explicit cutoff opening snapshot.
- Simple automatic-code item entry, live Find, photo-based inline cash vouchers
  and Sale Voucher are implemented. Sale Enter navigation, default quantity/unit/
  known rate, optional Bill # and refreshing automatic F8 dates are documented in
  [NATIVE_WORKSPACE.md](NATIVE_WORKSPACE.md).
- Main Item Groups and Item Sub Groups now have distinct entry/F8 routes, using
  the existing proven parent relation. Scoped browsing filters before paging;
  saves enforce root/subgroup rules inside the transaction. Generic hierarchy
  APIs and existing IDs/links remain intact. The service and GUI checks use
  disposable data; exact original code-entry/layout parity remains unverified.
- Startup avoids duplicate identical checks only inside its tightly scoped
  verification context. First recovery, integrity and immutable-baseline checks
  still run; managed session/lifecycle contracts remain intact. See
  [PROCESS_RECOVERY.md](PROCESS_RECOVERY.md).
- The prior sale/startup increment recorded 650 passing tests, read-only original
  display verification and repeat-startup timing in
  [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). These results do not certify all
  original screens or replace verification of this later audit.
- The audit implemented verified original-document HTML/Voucher Printing,
  broader Enter/focus and retained return pickers after rejected selection, and
  original/new trade-detail reports. Trade-detail dates initially include all
  dates; explicit/cleared ranges persist. Financial/item-ledger cutoff defaults
  remain unchanged. Focused service, component and GUI checks passed; actual-
  source trade comparisons retained matching counts/full HTML and source SHA.
  [WORKFLOW_AUDIT.md](WORKFLOW_AUDIT.md) records those checks and every module's
  remaining original-reference gap. Final validation on 2026-10-04 passed all
  **678 application tests with no skips or failures** in 197.260 seconds, plus
  the syntax/source-manifest single-entry check. The four original-file hashes
  remained unchanged. Full original parity remains unverified; no unknown
  business rule is guessed.
- The public launcher's read-only `--smoke-test` passed from `/tmp` for two
  companies, 30 screens, 11 documents and four stock reports.
- The owner retains platform packaging. No new build or target-OS qualification
  is part of the current audit.

## Historical progress — pre-integration stages, 2026-10-02

The following dated notes preserve the earlier investigation and development
evidence. References to read-only V3, Transactions & setup, separate operational
work, schema 4, pending cutover or platform packages describe that earlier
stage. They are not current navigation, unfinished-work instructions or a
request to resume packaging. The current behavior above and
[PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md) supersede them.

- V3's existing-baseline browser now has separate **Financial → Cash Receipts /
  Cash Payments** sections, using original voucher type and company filters,
  exact received/paid totals, account/narration search and complete stored
  details. **All vouchers** retains the combined history. This exposes existing
  vouchers; operational posting into that baseline remains separate work.
- Original backup identified; schema, 24 stored functions and 46 menu definitions
  extracted and documented.
- A separate SQLite recovery archive now preserves all 93,110 rows with matching
  content digests and zero detected source key/relationship violations. Independent
  original Oracle Import now matches every table, row and field; report and
  operational migration acceptance remain separate requirements.
- All original V2 source reviewed; its limitations are recorded in
  `PROJECT_CONTEXT.md`.
- `bifma/database.py` adds explicit readonly sessions, atomic write transactions,
  structural checks, and consistent SQLite backups that refuse overwrite.
- Exact calculation rules and strict backup recovery have tested implementations.
- The improved read-only V3 interface has company switching, search/pagination,
  invoice/return/journal/breakage details and filtered background stock reports.
- Historical stock and ledger reconstruction services preserve source formulas
  and now match every row from the actual Oracle STOCK, STOCKB and LEDGER views
  for this backup. Corrected operational posting remains separate. This does
  not prove original print layouts or unrepresented purchase/delivery branches.
  All 37 independently queried Oracle aggregate/filter scenarios also match.
  The verification's owned containers, volumes and private exports are cleaned.
- Native schema v4, secure user/session permissions, validated shared-master
  services, exact balanced posting primitives and native report services exist.
- `BIFMA_TRADERS_V3.py` automatically opens the existing recovered baseline;
  its **Transactions & setup** area provides native setup/login, draft/post entry for sales,
  purchases, constrained linked returns, openings and vouchers, plus reversals
  and stock/ledger/trial-balance screens. Synthetic UI posting passed.
- Native posting revalidates snapshots and revisions, serializes dated stock
  checks, enforces return quantity/money caps, and keeps posted documents immutable.
  See `TRANSACTION_SERVICES.md` for supported and deliberately rejected cases.
- Standalone breakage receive/dispatch, the item ledger and an exact large/base
  quantity helper are implemented. Their restricted charges/linkage are explicit;
  six-document synthetic UI coverage checks both regular and breakage pools.
- Company/account contacts and tax identifiers have an explicit backed-up
  migration and native forms; new issued snapshots retain them. Full printable
  native reports read all filtered rows and totals from one consistent snapshot.
- Sub-trial balance rolls up the current account hierarchy without double-counting
  grand totals. An administrator audit browser filters immutable native events.
- Combined native sales/returns and purchases/returns detail reports include
  dated reversals, saved bilingual labels/base units, filtering, full-period exact
  totals and complete printable output. Header totals are not allocated to filtered
  items/stores. Large item/account selectors now have searchable, paginated pickers.
- Explicit counter sales, cash purchases and linked cash returns preserve separate
  number namespaces, full cash effects and saved receipt contacts. Cash/account
  report filtering, permission separation and printable cash facts are covered.
  Migration 4 preserves old rows and guards, including actual crash recovery and
  retry. Tender/change, partial cash settlement and invoice allocation remain open.
- Dev5 adds exact current-city account balance grouping and a complete cash book,
  with current permissions, full-filter totals and printable output. No invoice
  aging or allocation is inferred. Widget variables are disposed by their owning
  widgets on the Tk thread, preventing delayed worker cleanup from calling Tcl.
- A fixed 17-step real-widget diagnostic now ships with the native launcher and
  covers widget lifecycle, both new reports and exact HTML totals, postings,
  cash returns/reversal and verified cash-inclusive
  backup/restore. Linux, Wine and PowerShell require the same seven frozen gates.
  Dev5 source and actual frozen workflow checks passed on Linux and Windows
  under Wine; native Windows and target-machine acceptance remain open.
- Verified native backup/new-file restore services and UI are implemented;
  restore revokes copied sessions. Posted documents can be exported to a native
  standalone printable HTML layout. Original print-template and device parity
  remain unverified.
- A Windows x64 development ZIP is built with Windows CPython 3.13.16 under
  private Wine 11. Its checked run completed 491 application tests (two POSIX-only
  skips), 35 Windows packaging tests (two Linux-host-only skips), the source native
  GUI smoke and seven frozen checks, including the complete fixed native
  posting/cash/backup/restore workflow. Independent ZIP extraction/content/AMD64 checks
  passed; all application inputs match Linux dev5. It excludes business records.
  Native Windows acceptance remains pending; see `WINDOWS_RELEASE.md` and
  `windows_archive_verification.json`.
- A separate Linux x86_64 development archive has been built on Ubuntu 22.04.5 /
  glibc 2.35 after 491 application tests, 50 packaging tests and source native GUI
  smoke. Frozen launch/runtime/history/relocation checks and independent archive
  extraction checks passed. See `LINUX_RELEASE.md` and `linux_build_verification.json`.
  The independent Linux extraction also passed the full frozen workflow.
  Target distribution, fonts, printers and business acceptance remain open.
- `MIGRATION_READINESS.md` maps verified masters and records actual cutover gaps.
  All-time stock includes 4,315 negative regular groups; both historical trial
  balances balance. Valued native stock openings and account openings need joint
  reconciliation, and old-invoice obligations need an explicit bridge. No cutoff
  or migration policy is inferred from those diagnostics.
- Both packages and operator instructions are collected in
  `dist/development-0.3.0.dev5/`. These are evaluation artifacts; full replacement
  UI, complete operational services, verified migration, production packaging
  and release acceptance are still outstanding.

Update current progress against actual source and checks while retaining dated
evidence as history. Complete original parity requires the missing reference
comparisons, not speculative additions or reopening superseded packaging work.
