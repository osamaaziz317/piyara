# Native workspace operator notes

Maintained Python application; complete original-software parity and business
acceptance remain unverified. Updated 2026-10-04.

The workspace automatically carries original masters, stock and account
closings into the working SQLite database. New transactions use corrected
balanced posting rules; original history retains its values and rules. Read
[BASELINE_INTEGRATION.md](BASELINE_INTEGRATION.md) for source issues, original
invoice returns and exact valuation behavior. Original files remain unchanged.

## Start and set up

The default `python3 BIFMA_TRADERS_V3.py` opens the application, preparing
`data/bifma.sqlite3` automatically from the existing archive on first use.
No database picker or empty company setup is required. Tkinter and a graphical
display are required. After login, one menu opens sales, purchases, returns and
vouchers. An entry module opens its New form immediately. Press **F8** or choose
**Saved records (F8)** to browse its original and new documents together, with
shared search/date filters. **Back to entry**, or closing the saved-record
browser, restores the form with its unsaved fields and lines. Original details
retain their saved values. An explicit alternate working file can be opened with:

```sh
python3 BIFMA_TRADERS_V3.py --transactions --transaction-database /path/to/bifma_native.sqlite3
```

Use the native dropdown menus across the top to find the modules:

- **File:** Overview, Change passphrase, Sign out and Exit.
- **Coding:** Financial Coding contains Accounts; Inventory Coding contains
  Main Item Groups, Item Sub Groups, Items, Units, Stores, Company stores and Opening stock. Cities
  is available directly under Coding.
- **Financial Transactions:** Opening Balance, Cash Payment Voucher, Cash
  Receipt Voucher and Journal Voucher, in that order.
- **Inventory:** sales, purchases, returns, cash trades and breakage entries.
- **Reports:** Voucher Printing, plus individual reports grouped under Financial
  reports and Inventory reports.
- **Setup:** Companies, Users & access, Audit log, Create backup and Restore backup.

Modules and actions remain subject to the signed-in user's permissions. The
menus are disabled during an active operation or an open entry/dialog; close
that dialog before choosing another module. Signing out removes the authenticated
menu commands. The
master selector stays within its Coding or Setup section. Bank receipt/payment
entry and new reversal actions are no longer offered. Previously saved bank or
reversal records and their accounting, stock and reporting effects are retained.

Coding masters and **Setup → Companies** also open a new-entry form. Press
**F8** to find an existing record and use **Edit selected** when needed. After
saving a new master, a fresh form opens for the next entry. Users who can read a
transaction module but cannot create documents see a blank landing page with
**Saved records (F8)**; opening the module does not attempt unauthorized entry
or automatically show old records.

Main Item Groups and Item Sub Groups use separate new-entry forms and F8 lists.
A subgroup requires a main group. Existing item/group IDs remain in place;
older deeper hierarchies require review rather than being flattened on opening.
Enter and Keypad Enter advance through Coding, business-entry, administration
and report fields. On the final field they invoke its Save, Use line, Show or OK
action directly. Successful completion closes its dialog; validation retains
the form and its input. Read-only generated item codes and disabled fields are
skipped, and focused controls scroll into view. Tab/Shift+Tab move between
controls. Space toggles checkboxes; Enter moves on without changing them.

F4 opens the focused selector's Find window; type, use Up/Down and press Enter
to commit the highlighted match. Enter on an already selected field accepts it
and advances. Esc closes through the dialog's normal close/discard checks.
Ctrl+S saves vouchers for review; F8 continues to browse saved records without
losing unfinished entry. Alt+F/C/T/I/R/S opens File/Coding/Financial Transactions/
Inventory/Reports/Setup, F10 opens the menu bar, Ctrl+L focuses Company and
Ctrl+Tab changes notebook pages. Shortcuts retain permission, modal and worker
guards; selecting a choice with the mouse does not submit a form.

The compact login shows User, User Name, Password and Company, with OK/Cancel.
Enter after User looks up its saved full name and active company choices;
Enter after User Name moves to Password. With one company, Password Enter signs
in; with multiple companies it moves to Company, where Enter signs in. Changing
User clears the earlier name, company and password. Actual sign-in checks the
password and current company access again.

First use creates a personal administrator. User Enter supplies an editable
full-name default; continue with Enter through full name and password. Enter on
the confirmation field runs Create administrator; invalid input keeps the form
open. There is no default account or password.
The 15-character minimum has been removed. Usernames allow 1–80 supported
characters and passwords 1–1,024 characters; blank passwords are rejected and
the existing common-password checks remain. Existing passwords still work.

Your original company/item/account setup and balances are already present.
Do not re-enter their openings. Use **Coding** for accounts/items and related
masters, and **Setup → Companies** for company configuration. For a deliberately empty
synthetic evaluation file only, the setup sequence below includes an explicit
administrator service step for the first item's posting configuration:

1. Create the company and its financial start/end dates. Transaction entry
   requires a configured financial interval containing the document date.
   Optional addresses, contact details and tax registration identifiers are
   retained in new document snapshots for printing. Identifiers remain text.
2. Create the chart of accounts. Group accounts do not accept postings. Add the
   relevant customer/supplier, sales, purchase, inventory, opening counterpart,
   and cash/bank accounts. Use the appropriate cash/bank account kind. Account
   setup also accepts addresses, phone/mobile, email, website and registration
   identifiers; these do not enable tax calculations.
3. Create units and stores, then assign stores in **Company stores**.
4. In a completely empty test file, seed the first item and its deliberate
   posting mappings through the administrator master API, as shown in
   [OPERATOR_GUIDE.md](OPERATOR_GUIDE.md). Routine **Items** forms assign a
   read-only numeric code and reuse the established sales/purchase setup;
   they have no party/account dropdowns. Enter the base unit, optional large
   unit/conversion factor and prices. Missing, conflicting or invalid posting
   defaults require administrator review. Native invoices store **base-unit
   quantities and rates**. In an item line, **Convert large / base quantities**
   combines large packs and loose base units using the configured factor. The
   result replaces the base quantity; the rate remains per base unit.
5. Enter verified opening stock, with an explicit opening value and counterpart
   account. Review and post it. Valued stock openings already debit inventory
   and credit the counterpart account. Use **Financial Transactions → Opening Balance** for the remaining
   balanced account entries, reconciling both types against accepted opening
   balances without entering inventory twice.

Setup masters are shared across companies. Administrator access is currently
required to maintain them. Deactivation and references are checked; edits use a
version token to reject stale changes. Once an item has posted history, its base
unit cannot change, including after a reversal or imported stock history. Create another item when its
base unit differs. Large-pack factors can change for future entries without
reinterpreting existing base quantities. There is no master deletion interface.

## Enter and review documents

The implemented native screens are sales, purchases, linked sales/purchase
returns, standalone breakage receive/dispatch, opening stock/balances, manual
journals, and cash receipts/payments.
**Inventory** also contains **Counter sales**, **Cash purchases**, **Counter sale
returns**, and **Cash purchase returns**. These post the full amount directly to
the configured/original cash account and have separate document-number sequences.
See [cash entry instructions](CASH_TRADES.md). Do not add a receipt/payment for
the same cash movement. The current schema is 6. Normal startup upgrades a
verified schema-5 file automatically, keeping a pre-upgrade backup beside it.
Users, password hashes, grants and business entries are preserved. Older files
retain the explicit backed-up upgrade path.

Open the document module and review the suggested number/date in its New form.
Cash payment/receipt vouchers use the original-style header and inline rows:
enter an **Acct. Code**, or choose **Account Title** through **Find…**, followed
by **Narration** and the **Payment** or **Receipt** amount. **Enter** advances
Voucher # → Date → Account Find → Narration → Amount → the next row.
In Find, Enter selects the highlighted account; **Add line** adds a row and
**×** or Ctrl+Delete on that row removes it. At the next blank account-code
field, Enter saves the populated voucher for review; Ctrl+S can save from any
entry field. The total updates as
amounts change. The company cash account is retained without asking staff to
select it on each voucher. New payments/receipts accept exact amounts with up
to two decimal places; existing drafts retain their saved precision settings.
Amounts outside the permitted precision are rejected rather than rounded away.
**Save** opens draft review; **Post document** remains the posting action.
**Print** also saves a valid draft for review; **Post and print** then posts it
and opens a freshly authorized printable page in the system browser. Ctrl+P
there opens Print/PDF; Save printable copy remains a separate file export.
It does not print an unsaved
entry as a posted voucher. **New** clears the form and **Exit** closes it, asking
before discarding changed fields or incomplete rows. Saved-record browsing does
not discard them. **Last voucher #** reflects the greatest saved numeric number
in that voucher type's original/current sequence, including drafts.

**Inventory → Sale Voucher** opens the inline sale form. Enter advances from Sale # to
Date, then opens Buyer Find. Type a name and press Enter to select the highlighted
match; Up/Down changes the highlighted row and Escape cancels. Buyer selection
opens Item Find directly. Sale Voucher has no Store header, row column or
Store Find. Item selection fills
its base unit, quantity 1 and known sale rate. Enter advances through unit,
quantity, rate and line discount to the next item. A blank original rate remains
blank until entered. Choosing the configured large unit multiplies its entered
quantity by the saved conversion factor; the rate remains per base unit.
New rows receive their stock location internally from the company's established
setup; ambiguous or inactive setup requires correction before new lines can be
saved. No location is guessed or activated automatically. Each existing draft
row retains its original location, quantities, rates, adjustments and posting
precision. Reopening a saved large-unit line retains the selected unit and exact
entered quantity instead of treating that quantity as base units. **New**,
**Bill** and **Exit** sit beside Stock at the top of the form; **Save** and
**Saved records (F8)** remain available below. **Bill** uses the existing print
review/post action, without adding unsupported DC behavior. Bill # is an optional saved reference,
also included in posted printable copies. The stock indicator shows the selected
item/store balance as of its displayed date; posting checks availability again.
Save and Bill use the same review/post flow as cash vouchers. To finish from
the keyboard, Esc closes the next Item Find and restores its blank item code.
Enter there moves through header discount, breakage and freight; Enter after
freight saves. Ctrl+S also saves directly for review. Ctrl+Delete removes the
focused inline row and focuses the neighboring row.

In the Sale Voucher **F8** browser, **Date From / Date To** initially cover the combined
original and new saved dates and refresh automatically after saving new sales.
Press Enter in a date field to apply your own range. Explicitly searched ranges
persist; Clear filters keeps all dates visible. Returning to entry keeps every
unsaved line.

Other document forms use **Add line**, **Use line**, then **Save draft & review**.
Enter after the final header field opens Add line. Enter after the final line
field validates and applies the line, closes its dialog and focuses Add line
for another entry. Invalid line input stays open. Ctrl+S saves the document for
review. In the line table, Enter edits, Insert adds and Delete removes a line.
The quantity converter also applies its result on its final-field Enter and
returns focus to line quantity.
Their rounding is explicit:
subtotal rounding and final decimal places are separate fields. The initial UI
selection follows whole-unit legacy rounding; choose cents where the company's
reviewed policy calls for it.
On a purchase or cash-trade line, **Use stored rate** copies the selected item's
current known master rate per base unit. Missing original prices remain blank;
enter an agreed rate instead of treating absence as zero. It is an explicit action; the rate remains
editable before saving and is preserved in the issued document.

Drafts do not affect stock or accounts. **Post document** revalidates permission,
financial dates, current masters, quantities and calculations while holding one
database write lock. Posting commits the document, stock, journal and audit
together. If a referenced master changed after saving, resave and review the
draft before posting. Duplicate numbers or stale revisions are rejected.

**Keep as draft**, or **Close** after posting a new document, returns to a fresh
new-entry form. To reopen the saved draft or posted document, press **F8**,
search if needed and choose **Open selected**. Viewing saved documents does not
clear the unsaved new entry behind the browser. Closing those details returns
to the browser; **Back to entry** returns to the same new-entry form.
Review initially focuses Keep as draft, so Enter can dismiss it without posting.
Tab reaches the explicit Post action. Posted dialogs initially focus Close.

Posted documents cannot be edited. There is no **Reverse document** action or
Reversals entry screen. Existing reversals remain recorded and continue to
affect balances and reports; their references/status are still shown when
viewing saved records. Internal compatibility services remain available for
existing-data tests, not as an operator workflow.

Use **Bill / print preview** on a posted or original document to open its
authorized HTML page in the system browser, then Ctrl+P for Print/PDF. Preview
files remain private until sign-out or clean application shutdown. Use
**Save printable copy** to keep a standalone HTML file. The copy includes
the issued labels and exact amounts even after masters are renamed. The native
Sale Voucher and Breakage Receive bill layouts now follow the supplied BT-header
Urdu photographs, using saved party/product/packing facts. Expandable saved
details are excluded from the compact printout. Original invoice totals remain
the register's recorded party amount, with missing values shown explicitly;
the photographed independent line rounding does not change posting formulas.
Other document layouts and target printer dimensions are still awaiting original
evidence; see `DOCUMENT_OUTPUT.md` and `VIDEO_WORKFLOW_EVIDENCE.md`.

Sales cannot consume stock that is unavailable in the selected company/store.
Backdated posting also checks later movements so it cannot make a later dated
balance negative. Inventory is kept separately for regular and breakage pools.
**Breakage receive** adds to the breakage pool; **Breakage dispatch** removes
from that pool. Regular stock cannot supply a breakage dispatch. Receive uses
one store throughout the document and allows a header breakage deduction.
Both allow line percentage discounts. Freight/header discount and links to an
original sale are not supported for these standalone breakage entries; dispatch
also has no header breakage deduction. These limits keep unresolved original
rules from becoming implicit new posting policy.

Returns select an original posted line. They must match its company, party,
item, store, units, rate, line discount and rounding. Cumulative returned quantity
and money cannot exceed the original. Staff enter return discount, breakage
and freight amounts, under the owner's approved policy. Original historical
invoices are selectable when their quantities/units/earlier returns support
verified capacity. No original invoice is recreated as a new posting.
The source picker searches historical and new posted documents and offers Previous/Next pages.
Drafts cannot hide posted originals. **Back to documents** returns to the same
search page after reviewing an invoice's lines.
Enter searches/selects in this picker. A rejected or cancelled line keeps its
source chooser and search available; an accepted line returns to quantity entry.

Payments against account invoices are entered as separate vouchers. Explicit
cash invoices already record their full cash movement. Invoice allocation,
outstanding-invoice aging, embedded settlement, tax/labour charges, partial
challan conversion and their original form behavior are not yet implemented.

## Reports and access

Expand **Reports**, then choose a report under **Financial reports** or
**Inventory reports**. The selected report opens with its existing filters.
Results appear only after **Run report**; F8 is for saved entry records.
Reports show carried trial/sub-trial balances, account and item ledgers, and
regular/breakage stock quantities. Opening balances precede the start date;
period movements fall within the selected dates. Drafts are excluded. Ledger
and trial balance signed balances are positive for debit and negative for
credit. **Stock valuation** adds frozen new movement costs to the unchanged
historical value, using the selected original purchase-rate method.
Missing costs remain unknown. **Profit and loss** requires reviewed account
classifications and a complete compatible inventory basis; it displays Review
required until these are available. See [OPERATIONAL_REPORTS.md](OPERATIONAL_REPORTS.md).
The sub-trial report follows the current account hierarchy. Group figures include
their descendants; the displayed grand totals count each posting once.
The ledger has Previous/Next navigation with full-period totals and running
balances carried across pages. Inactive accounts with posted history remain
available in the ledger selector. The item ledger filters by item, optional
store, pool and dates, with exact running quantities carried across pages.
It keeps inactive items/stores with posted history accessible and shows saved
item/store labels where available.
Stock quantities supports an optional store and item code/name search as well
as pool and dates. The stock-pool control is enabled only for stock reports.
Stock quantities and Stock valuation also accept original and cross-cutoff
dates: original stock activity and new movements are counted once. Account and
item ledgers retain the cutoff opening snapshot followed by new entries; earlier
documents remain accessible in their own module list.

**Sales / returns detail** and **Purchases / returns detail** combine original
invoices/returns with new posted invoices, linked returns and dated reversals.
Both dates initially stay blank to include all dates. Explicit dates, including
a deliberately cleared start date, persist when switching reports. Untouched
financial/item report defaults retain their cutoff boundary. Choose the **Entry type**
(Account and cash, Account entries, or Cash entries), activity and
optional party/item/store/document-number filters. Rows preserve original or
issued labels; totals cover all pages. Unknown amounts and source inconsistencies
remain visible with review notes. **Quantities by item** keeps recorded units
separate. Recorded/final document totals count each document once, and are omitted under item/store
filters because header adjustments have no approved per-item allocation.
See [TRADE_DETAIL_REPORTS.md](TRADE_DETAIL_REPORTS.md). Invoice-only or return-only
users can read their corresponding report. Combined output requires every
selected activity grant; including both account and cash sales also requires
`counter_sales/read` alongside `sales/read`.

**Reports → Voucher Printing** lets you choose a document section, then press
**F8** to find its saved records. Open a document and use **Save printable copy**.
Original HTML copies contain the verified original fields, lines, company labels,
notes and cancellation status; they do not repost or modify history. New copies
require a posted document. Open the resulting standalone HTML in a browser to
print. The original software's exact print templates are unavailable.

Account/item selectors accept a typed name by opening **Find…** with that text.
This also works after opening the dropdown: typing/pasting closes that list and
opens Find. Mouse selection and arrow/Enter/Escape navigation remain available.
The Find search box retains focus so subsequent letters, paste and deletion
continue the search. Matching codes, names and Urdu names appear as the text
changes; Search remains available, while Enter commits the highlighted result.
The selector keeps its prior account/item until Enter or **Use selected**
commits an identified result. Use Previous/Next for 100-row pages. Changing the text
returns to the first matching page. Cancelling or an empty search result leaves
the form's existing selection unchanged.

**Save full report** creates a standalone printable HTML file for the current
report filters. It reads the complete report again with current permissions in
one database snapshot, including ledger rows beyond the displayed page. Open
the file in a browser to print or save as PDF. See `REPORT_OUTPUT.md` for limits.

**Users & access** supports creating users, changing administrator roles, setting
company/module permissions, disabling accounts and resetting passphrases.
Role changes revoke that user's sessions; at least one active administrator must
remain. Changing your own role returns you to sign-in. Other access changes take effect at the next
protected operation. Grant both company access and the needed module actions;
`read` controls navigation. Services separately check `create`, `update`, and
`post`. Administrators have full access. The permission editor loads existing
grants before changing them and offers actions for implemented modules. Shared
setup, users, audit and backup are administrator tasks.
Inactive companies are labelled. An existing inactive-company grant can be
retained or removed; new grants to inactive companies are unavailable. A retained
grant supplies no operational access until that company is reactivated.

**Audit log** lets administrators search activity across the native database,
filter a company or exact action, and view event details. Date filters and times
use UTC. Blank company includes global events; selecting a company excludes
global events. Current user/company/record labels aid identification; the stored
event remains unchanged. See `AUDIT.md` for the read and permission contract.

Use **Change passphrase** for your own account. Other sessions are revoked when
the passphrase changes. Idle sessions expire after 30 minutes; absolute session
lifetime is 12 hours. Sign in again when prompted.

## Backup and restore

Administrators can use **Setup → Create backup** to save a verified full snapshot to a
new filename. The backup includes every company and the current user/access
configuration. It must be kept in a location appropriate for business records.

Choose **Setup → Restore backup**, select the
backup and a new destination filename, and enter an active administrator's
credentials from that backup. Restore opens a new verified copy; it never
replaces the active file. All copied login sessions are revoked. Sign in again
to use the restored database. See `BACKUP_RESTORE.md` for older-version handling
and filesystem requirements.

## Verification and release limits

From a source checkout with Python, run the synthetic interface exercise on a
graphical desktop. This tool is not included in the portable packages:

```sh
python3 -m tools.smoke_native_ui
```

It creates a temporary database, signs in, creates a company through the UI,
posts opening stock and a discounted sale through actual widgets, checks their
persisted totals/stock, exercises a separate approver, verifies
backup/restore, visits setup/users/reports, and signs out. It removes its
synthetic database afterward. Reversal fixtures are created through internal
services to check compatibility with saved history; the interface offers no
new reversal action. It does not qualify all interactive paths.

Original form/report comparison, bilingual printer/font behavior, unresolved
settlement/delivery workflows and business acceptance remain open. Historical
baseline integration is implemented, but its known source anomalies still
require review. Platform packaging and workstation qualification belong to the
owner. See `REBUILD_PLAN.md`, `TRANSACTION_SERVICES.md`,
`MASTER_SERVICES.md`, and `NATIVE_DESIGN.md` in the source checkout for the
implementation boundaries.

Version `0.3.0.dev5` also offers **Account balances by city** and **Cash book**
in Reports. Read [their operator notes](CITY_CASH_REPORTS.md) for filter choices,
current-city grouping, former cash account access and complete printable output.
