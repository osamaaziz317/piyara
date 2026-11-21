# BIFMA TRADERS
this is dummy line to elaborate the concept of commits
Run from the project folder:

```sh
python3 BIFMA_TRADERS_V3.py
```

This is the **one maintained application**, with one menu for daily work.
Opening an entry section goes straight to its **new-entry form**. Saved records
stay hidden until you press **F8** or choose **Saved records (F8)**. That list
contains original and new records together, with search and date filters where
applicable. **Back to entry** returns to your form without clearing its unsaved
fields or lines. There is no separate Workspace/History switch.
The modules under `bifma/` are shared application code, not separate versions.

**Your existing data is used automatically.** No database selection or empty
company setup is required. On first run, V3 prepares `data/bifma.sqlite3` from
`data/legacy_recovered.sqlite3`, then opens the working database automatically.
Your companies, items, accounts, stock and account balances are already there.
Create your owner login once; subsequent launches ask you to sign in.
There is no default password. Original records retain their recorded values.

The compact login follows the supplied reference: **User**, **User Name**,
**Password**, **Company**, **OK** and **Cancel**. Type your User and press Enter
to fill its saved full name, then Enter again to reach Password. Password Enter
signs in for a single company, or moves to Company when there is a choice; select
the company and press Enter. First-time setup supplies the username as an editable
full-name default; Enter after confirming the password creates the administrator.
Passwords no longer require 15 characters; nonempty short
passwords and usernames are supported. Existing passwords continue to work.
The first opening of a schema-5 working file creates a verified backup and
upgrades it automatically for short usernames, preserving existing data.

The database path is relative to the project, not the terminal's working folder.
Keep `bifma/` and the `data/` folder beside the launcher. If the
baseline file is missing, startup reports the error; it never replaces it with
an empty database. Requires Python 3.10+, Tkinter and a graphical desktop.
No pip packages are needed. Transactions require SQLite 3.37 or newer.

## Existing baseline

The supplied archive contains 2 companies, 6,207 items, 703 accounts and the
original sales/vouchers and stock records. You can browse/search items and
accounts, open transaction details, and review regular/breakage stock with
company, date and store filters. Existing values remain unchanged.

Use the menus across the top of the application to open its modules:

| Section | Contents |
| --- | --- |
| **File** | Overview, Change passphrase, Sign out and Exit |
| **Coding** | Financial Coding → Accounts; Inventory Coding → Main Item Groups, Item Sub Groups, Items, Units, Stores, Company stores and Opening stock; Cities |
| **Financial Transactions** | Opening Balance, Cash Payment Voucher, Cash Receipt Voucher and Journal Voucher |
| **Inventory** | Sale Voucher, purchases, returns, cash trades and breakage receive/dispatch |
| **Reports** | Voucher Printing, Financial reports and Inventory reports |
| **Setup** | Companies, Users & access, Audit log and backup/restore |

**Cash Receipt Voucher** and **Cash Payment Voucher** each open a new voucher.
The form has a voucher number/date and company header, followed by editable
account-code, account-title, narration and payment/receipt rows. Type an account
code or use **Find…** to choose its name. The total updates while entering amounts.
**Enter** advances Voucher # → Date → Account Find → Narration → Amount →
the next account row. In Find, Enter selects the highlighted matching account.
When the voucher is complete, press Enter on the next blank account code, or
**Ctrl+S**, to save it for review. Invalid input keeps the entry open.
New cash vouchers accept exact amounts with up to two decimal places.
**Save** opens the existing draft review, then **Post document** records the voucher.
Press **F8** to browse original and new vouchers in that same module. Bank
receipt/payment entry and new reversal actions have been removed from the
interface. Previously saved records and their balance/report effects remain
intact.

**Inventory → Sale Voucher** opens inline entry with Bill #, buyer, remarks and item
rows. Press **Enter** through Sale # → Date → Buyer search → Item search. Type
part of the name and press Enter to select the highlighted match. The item's
base unit, quantity **1** and known sale rate fill automatically; Enter advances
through unit, quantity, rate and discount to the next item. **Enter** or
**Keypad Enter** on **Dis %**, **Discount** or **Value** opens Item Find for the
next row, including newly added rows. Buyer selection opens Item search directly;
the Sale Voucher has no Store field or Store search. Stock locations are assigned
internally from the company's established setup, and saved draft lines retain
their original locations.
**New**, **Bill** and **Exit** are beside Stock. **Bill** saves the voucher for
review, requires **Post and print**, and then opens the printable bill page.
Reopened drafts retain their chosen packing unit and entered quantities.
**F8** opens saved sales with **Date From / Date To** already covering the saved
dates. Adjust the range or clear filters without losing the unfinished voucher.

In **Saved records**, search or filter dates, then open a voucher to see its
recorded totals and detail lines. The baseline contains 524 cash receipt and 524 cash payment
vouchers across both companies; the selected company controls the visible list.
New receipts and payments update the carried account balances.
**Reports → Voucher Printing** opens a document-section selector. Press **F8**
to find a saved voucher, open it and choose **Bill / print preview** or
**Save printable copy**. Preview opens the page in your system browser; press
**Ctrl+P** there to print or save PDF. Original
copies retain the original company labels, recorded values and cancellation
markers. New documents must be posted before printing. Output is a standalone
HTML file that can be opened and printed from a browser.
Sale and Breakage Receive bills have a compact BT header and Urdu columns,
including the saved party, item descriptions, packing and exact amounts.
**Cash Book**, **Account ledger** and **Sub-trial balance** have compact report
filters with Company selection. **View** opens a separate result preview;
closing it returns to the same filters. Dates accept `DD-MM-YY` or `YYYY-MM-DD`.
The preview offers the full printable page and a separate save-copy action.
Cash Book prints payments and receipts side by side; Party Ledger prints an
Urdu column layout with debit/credit and running balances.
The supplied archive closes on **27 September 2026**; new entries use later dates.
**Stock quantities** and **Stock valuation** use the selected dates for original
and new activity, including ranges that cross this closing date. Account and
item ledgers start with the preserved closing balance, followed by new entries.
**Sales / returns detail** and **Purchases / returns detail** combine original
and new posted records. They initially leave dates blank to include all dates;
entered date filters stay in place when switching reports. Missing original
amounts remain **Unknown**, with review notes, in both the screen and full HTML.

An alternate recovered archive is optional:

```sh
python3 BIFMA_TRADERS_V3.py --database /path/to/legacy_recovered.sqlite3
```

`BT-270926.EXE` is an Oracle database backup despite its extension. Recovery was
independently reconciled against an original Oracle import: 30 tables, 93,110
rows and 1,424,337 fields match. See the [recovery evidence](docs/ORACLE_RECONCILIATION.md).
The original V2 source is preserved byte-for-byte as a non-runnable
[reference](reference/original_v2_source.py.txt); its database remains unchanged.
The duplicate `BIFMA_TRADERS.py` and `BIFMA_HISTORY.py` launchers were removed.

## Transactions and setup

The application supports
company/item/account setup, secure users and permissions, sales/purchases,
cash trades, supported returns, breakage,
stock/account openings, cash vouchers/journals, exact reports, printable
HTML, audit and backup/restore. New entries continue the imported stock and
account balances. Document numbering continues from history and existing drafts.
Do not enter the imported balances again as opening vouchers.

In **Coding → Inventory Coding → Items**, enter the description, group, units and
prices. **Item code** is automatic and read-only; existing codes stay unchanged.
The supplied item sequence ends at `6207`, so the next new item starts at `6208`
unless newer items already exist. Item entry has no party or account dropdowns;
new items retain the established sales/purchase posting setup automatically.
An ambiguous or invalid setup requires administrator review before saving.
**Main Item Groups** and **Item Sub Groups** have separate forms and F8 lists.
Select a main group when creating a subgroup; existing item/group links remain
in place.

Type a name in an account/item selector to open its **Find…** window, or click
**Find…**. The search box keeps keyboard focus, and matching choices appear as you
type a code or name, including Urdu names. Search and Enter remain available;
press Enter or choose **Use selected** to put the highlighted result into your form.
Typing or pasting after opening the selector's dropdown also starts Find with
that text; arrow keys and mouse selection still work in the dropdown.

The same entry-first behavior applies to the other Coding forms,
**Setup → Companies**, financial vouchers and inventory entries. After saving
a new master, or closing a newly posted document or keeping its draft, a fresh
new-entry form opens. Users with read access only can press **F8** from the
module's empty landing page to browse saved documents. Reports open their
filters and wait for **Run report**; Users & access and Audit log keep their
administration lists.
Enter or Keypad Enter advances through fields across entry, setup and report
screens. At the final field it runs the form's **Save**, **Use line**, **Show**
or **OK** action directly. Successful completion closes that dialog; validation
errors retain its fields. In a Sale Voucher, Enter on the next blank item code
moves to header discount, breakage and freight; Enter after freight saves.
Saving a document opens review, with **Keep as draft** focused. **Post document**
remains a separate explicit action.

| Keyboard control | Action |
| --- | --- |
| Tab / Shift+Tab | Next / previous control |
| Space | Toggle a checkbox; Enter advances without toggling it |
| F4 | Open Find for the selected account, item or other selector |
| F8 | Browse saved records while retaining unfinished entry |
| Ctrl+S | Save a voucher for review |
| Esc | Close the active dialog, with its existing unsaved-change checks |
| Alt+F / C / T / I / R / S, or F10 | Open File / Coding / Financial Transactions / Inventory / Reports / Setup, or the menu bar |
| Ctrl+L | Focus Company |
| Ctrl+Tab | Move between document/report notebook pages |

Menus and shortcuts follow the same permission, modal and busy-operation rules
as their buttons. Find supports typing, Up/Down and Enter; selected fields can
be accepted with Enter, and F4 lets you search for another value.

Returns can select eligible original invoices or new posted
invoices. Staff enter return discount/breakage/freight amounts; cumulative
quantities and refunds cannot exceed the original, including linked past returns.
Source invoices with ambiguous quantities, units or old return links remain
visible for review instead of receiving guessed return capacity.

Stock valuation carries the exact historical value and values new movements
using the approved original purchase-rate method. Costs are saved when posting;
later purchases do not rewrite them. Missing costs show **Unknown**.
Profit and loss requires reviewed account classifications and complete costs;
it shows **Review required** when the original data cannot support a reliable total.

Original negative stock is preserved. Incoming stock may reduce a shortage;
new outward stock requires available quantity. Missing item prices remain blank
until staff enter a rate. **View starting balance details** lists source issues for review.

For an existing native file, use:

```sh
python3 BIFMA_TRADERS_V3.py --transactions --transaction-database /path/to/bifma_native.sqlite3
```

See the [native usage guide](docs/NATIVE_WORKSPACE.md),
[synthetic worked example](docs/OPERATOR_GUIDE.md), and
[baseline integration and source issues](docs/BASELINE_INTEGRATION.md).
New posting rules are corrected; historical values are preserved.

## Current limits and development context

One source tree is maintained; fixes go into it directly. Complete parity with
the original software is not verified. Original forms/report templates and
unproved tax/labour, linked breakage and delivery behavior still need original
application evidence. Invoice allocation/aging is not inferred from account
balances. The owner has instructed us to keep the original feature scope;
unproved extra workflows are not added. Source anomalies still require business
review, especially item units, negative quantities and account classification. See
[feature coverage](docs/FEATURE_PARITY.md).
The [section-by-section workflow audit](docs/WORKFLOW_AUDIT.md) distinguishes
verified behavior from missing original references. The supplied `.EXE` contains
the database export, not the original form or print-template source.

The requested delivery is Python source. Platform packaging is owner-managed;
older packaging recipes, artifacts and receipts describe earlier release work.
Use `BIFMA_TRADERS_V3.py` as the entry point when building this application.

```sh
python3 -m unittest discover -s tests -v
python3 BIFMA_TRADERS_V3.py --smoke-test
python3 -m unittest tests.test_application -v
python3 -m tools.smoke_native_ui
```

The UI checks need a graphical display. The historical check reads the recovered
archive; the native check uses a temporary synthetic database.
The application checks exercise login, the shared original/new document lists,
entry and review in one window, and safe worker shutdown. They use temporary
synthetic data.
Voucher regression checks cover real mouse double-click, keyboard activation,
delayed window visibility and closing before display. The real-pointer check
uses X11/XTest on Linux; the application itself needs no extra Python packages.

- [Project context](PROJECT_CONTEXT.md): architecture, decisions and original V2 analysis.
- [Rebuild plan](docs/REBUILD_PLAN.md): functional scope and remaining evidence.
- [Legacy analysis](docs/LEGACY_ANALYSIS.md): original formulas and anomalies.
- [Data recovery](docs/DATA_RECOVERY.md) and [SQLite recovery](docs/SQLITE_RECOVERY.md): recovery tooling and provenance.
