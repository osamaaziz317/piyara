# Operator evaluation walkthrough

This is a repeatable exercise for the maintained Python application. Use the
synthetic company below in a new database. It checks
daily entry, reports and recovery; it does not import historical data or approve
the application for production use.

Use `2026-10-02` for every document and report in this exercise, even if the
screen initially shows today's date. Allow about 30–45 minutes. Keep the chosen
test passphrase privately; do not put it in an evaluation report.

## 1. Start with an empty database

Normal `python3 BIFMA_TRADERS_V3.py` opens your real working baseline. For this
synthetic exercise, explicitly prepare a new empty test file and open that file:

```sh
python3 -c "from bifma.native_schema import initialize_database; initialize_database('/path/to/Bifma evaluation/demo.sqlite3')"
python3 BIFMA_TRADERS_V3.py --transaction-database '/path/to/Bifma evaluation/demo.sqlite3'
```

Replace the example path with your writable evaluation folder. The folder must
exist first. This deliberately empty test file has no associated original archive.
Keep the `bifma/` folder beside the launcher; it contains the application.
Python must include Tkinter and SQLite 3.37 or newer. No pip packages are needed.

1. Create a writable folder such as **Bifma evaluation** outside the application
   folder. Keep the database, printable files and backups there.
2. Run the initialization and launch commands above for `demo.sqlite3`.
   Initialization refuses an existing file instead of replacing it.
3. On the initial login form, enter **User** `demo_admin`, **User Name**
   `Demo operator`, and a private test **Password**. Repeat it in
   **Confirm password**, then press Enter to create the administrator. The former
   15-character minimum has been removed; Enter advances between earlier fields.
   Invalid confirmation leaves the form open for correction.
4. Expect **Set up your company**. Click **Open setup**, or open
   **Setup** in the top menu and choose **Companies**.

There is no default password or preloaded business company. Use dialog
scrollbars when controls are below the visible area. Wait for an
operation to finish before starting the next one.

The exercise can be completed with the keyboard. Enter/Keypad Enter advances
fields and invokes the existing Save, Use line, Show or OK action at the final
field. A successful modal action closes its dialog; validation preserves the
entered fields. Tab/Shift+Tab reaches other controls, and Space toggles a
checkbox (Enter moves on without toggling it). Focused fields scroll into view.
F4 opens Find, where typing filters and Up/Down plus Enter chooses a result.
Use Tab to skip an optional unselected selector.

Use Alt+F/C/T/I/R/S for File/Coding/Financial Transactions/Inventory/Reports/Setup,
or F10 for the menu bar. Ctrl+L focuses the Company selector, Ctrl+Tab changes
notebook pages, Ctrl+S saves vouchers for review, and F8 opens saved records.
Esc closes the active dialog through its existing unsaved-change checks. These
keys obey the same permissions, modal and busy-operation checks as buttons.

## 2. Create the demo company and masters

Company configuration is under **Setup → Companies**. **Coding → Financial
Coding** contains Accounts; **Coding → Inventory Coding** contains Items, Main
Item Groups, Item Sub Groups, Units, Stores and Company stores. Cities is directly under Coding. Open the
appropriate module; its New form opens automatically. Complete the fields and
press Enter on the final field to run **Save changes**. A fresh form opens after
successful saving. Press **F8** or
choose **Saved records (F8)** to find an existing record, then use **Edit
selected** to amend it. **Back to entry** restores the unsaved form behind that
browser. Leave **Active** checked throughout this exercise. The master selector
stays within its Coding or Setup section. Close the new-entry form before
choosing another top-menu module. The menus stay disabled while a form or dialog
is open or an operation is running.

Create **Companies** first:

| Field | Value |
| --- | --- |
| Code | `DEMO` |
| Name | `Demo Evaluation Company` |
| Fiscal start (YYYY-MM-DD) | `2026-01-01` |
| Fiscal end (YYYY-MM-DD) | `2026-12-31` |
| Address | `Synthetic evaluation address` |
| Cash account | Leave unselected until the accounts below exist |

After saving, confirm **Demo Evaluation Company** is selected in the top
**Company** dropdown. Open **Coding → Financial Coding → Accounts**.
Create these accounts in order. Names and codes below are entirely synthetic.

| Code | Name | Kind | Classification | Parent account | Accept postings |
| --- | --- | --- | --- | --- | --- |
| `G-ASSET` | Demo assets | `group` | `asset` | Unselected | **Unchecked** |
| `A-CASH` | Demo cash | `cash` | `asset` | Demo assets | Checked |
| `A-INV` | Demo inventory | `general` | `asset` | Demo assets | Checked |
| `A-CUST` | Demo customer | `customer` | `asset` | Demo assets | Checked |
| `L-SUPP` | Demo supplier | `supplier` | `liability` | Unselected | Checked |
| `I-SALES` | Demo sales | `general` | `income` | Unselected | Checked |
| `E-PUR` | Demo purchases | `general` | `expense` | Unselected | Checked |
| `Q-OPEN` | Demo opening counterpart | `general` | `equity` | Unselected | Checked |

The group account must not accept postings. Other optional fields can stay
blank. Under **Setup → Companies**, press **F8**, select the company and choose
**Edit selected**. Select **Demo cash** as **Cash account**, and save.

Create the following through **Coding → Inventory Coding**:

| Type | Entries |
| --- | --- |
| Units | Code `EA`, name `Pieces`; then code `BOX`, name `Boxes` |
| Main Item Groups | Code `DEMO-GROUP`, name `Demo items` |
| Stores | Code `STORE`, name `Demo store` |
| Company stores | Company **Demo Evaluation Company**, store **Demo store**, Active checked |

The normal Item form assigns a read-only code and reuses the established
sales/purchase posting setup. It has no party/account dropdowns. Your real
baseline already supplies that setup; this deliberately empty exercise does not.
Seed its first item through the explicit administrator service API below after
creating the masters above. This is synthetic fixture setup, not a step to run
against the real working baseline. Later routine items use automatic codes and
require no account selection; unresolved defaults require administrator review.

Replace the path in this command with the same evaluation `demo.sqlite3` path
used in step 1. It refuses a baseline-backed, populated or non-demo file, asks
privately for the synthetic administrator's passphrase, and creates the one demo
item with the deliberate mappings needed for this exercise's valued opening:

```sh
python3 - <<'PY'
from getpass import getpass
from pathlib import Path
from bifma.masters import MasterService
from bifma.native_schema import native_session
from bifma.security import SecurityService

path = Path('/path/to/Bifma evaluation/demo.sqlite3')
with native_session(path) as connection:
    companies = connection.execute('SELECT code FROM companies').fetchall()
    if (connection.execute('SELECT 1 FROM baseline_imports').fetchone()
            or connection.execute('SELECT 1 FROM products').fetchone()
            or len(companies) != 1 or companies[0]['code'] != 'DEMO'):
        raise ValueError('Use only the empty synthetic DEMO evaluation file.')

security = SecurityService(path)
masters = MasterService(path, security=security)
actor = security.authenticate('demo_admin', getpass('Demo administrator passphrase: '))
try:
    def identifier(entity, code):
        rows = [row for row in masters.list_records(actor, entity, search=code)
                if row['code'] == code]
        if len(rows) != 1:
            raise ValueError('Create the exact demo masters listed above first.')
        return rows[0]['id']

    masters.create(actor, 'products', {
        'code': 'DEMO-CUP', 'name': 'Demo cup', 'name_urdu': 'نمونہ کپ',
        'group_id': identifier('product_groups', 'DEMO-GROUP'),
        'small_unit_id': identifier('units', 'EA'),
        'large_unit_id': identifier('units', 'BOX'), 'unit_factor': '10',
        'sale_rate': '20', 'purchase_rate': '10',
        'sale_account_id': identifier('accounts', 'I-SALES'),
        'purchase_account_id': identifier('accounts', 'E-PUR'),
        'inventory_account_id': identifier('accounts', 'A-INV'),
    })
finally:
    security.logout(actor)
print('Synthetic demo item created.')
PY
```

Return to **Coding → Inventory Coding → Items** and press **F8**. **Demo cup**
should be present. Its existing `DEMO-CUP` code is preserved and read-only in the
form, as are all existing item codes. The generic administrator API intentionally
allows this fixture code; newly created routine items receive numeric codes.

## 3. Enter the five core documents

For each document, open its top-menu module; the New form opens immediately. Set
**Date (YYYY-MM-DD)** to `2026-10-02`. On stock, journal and other trade forms, set
**Subtotal rounding** to `legacy_whole` and **Final decimal places** to `0`.
New Sale Vouchers already use those settings; editing retains a draft's saved
precision. Keep invoice header adjustments at zero. New cash vouchers accept amounts with
up to two decimal places without exposing rounding controls; existing drafts
retain their saved precision settings. The cash amount in this exercise is `36`.
These are explicit choices for this example, not a company-wide accounting rule.

For stock, journal and other trade documents, Enter after the last header field
opens **Add line**. Fill its fields; the final-field Enter validates the line,
runs **Use line** and closes that dialog. Enter again on the focused Add line
button to enter another line. Invalid input stays available for correction.
Ctrl+S runs **Save draft & review**. In the line table, Enter edits the selected
line, Insert adds and Delete removes.
For **Sale Voucher**, use the inline form: enter **Sale #**, then press Enter
to reach Date and again to open Buyer search. Type the buyer name and press
Enter to select the highlighted match. Item search opens directly; type the name and press
Enter. The base unit, quantity `1` and known sale rate fill automatically.
Sale Voucher has no Store selector or Store search. New rows use the established
company stock location internally; existing draft rows retain their locations.
If setup cannot establish an active location, an administrator must correct it
before saving new lines.
Continue with Enter through unit, quantity, rate and discount to the next item.
You can also type complete buyer/item codes directly. To finish, Esc closes the
next Item Find and returns to its blank item code. Enter there advances through
header discount, breakage and freight; Enter after freight saves for review.
Ctrl+S also saves directly. Ctrl+Delete removes the focused inline row.
For cash receipts/payments, enter the **Voucher #** in the header and fill the
inline account-code/title, narration and receipt/payment columns. Typing a full
account code selects its title; **Find…** or typing a name in the account-title
field opens a search whose results update as you type. Use **Use selected** to
commit the account, or Enter to choose the highlighted match. Enter advances
through narration and amount to the next row. Enter on the next blank account
code, or Ctrl+S, opens review. Inspect the total and available **Items**,
**Accounting** and **Stock** tabs using Ctrl+Tab. Review initially focuses
**Keep as draft**: Enter there closes review without posting. For this exercise,
Tab to **Post document** and press Enter explicitly. The posted dialog focuses
**Close**; Enter returns to a fresh New form. Esc closes that unchanged form
before choosing another top-menu module. To see a saved document, press **F8**
from its module and use **Open selected**. Saved Sale Voucher records supply **Date From /
Date To** automatically from the saved dates. These bounds refresh after saving
new sales; an explicitly searched or cleared range remains your choice.

| Menu / Document number | Header selection | Lines | Expected effect after posting |
| --- | --- | --- | --- |
| **Coding → Inventory Coding → Opening stock** / `OS-001` | **Opening balance counterpart**: Demo opening counterpart | Item Demo cup; Store Demo store; **Quantity in base units** `10`; **Opening value** `100`; **Stock pool** `regular` | Stock `10`; inventory debit `100`, opening counterpart credit `100` |
| **Financial Transactions → Opening Balance** / `OB-001` | No party field | First line: Account Demo cash, Debit `1000`, Credit `0`. Second line: Account Demo opening counterpart, Debit `0`, Credit `1000` | Cash `1000`; this document's debits and credits both `1000` |
| **Inventory → Purchases** / `P-001` | **Party account**: Demo supplier | Demo cup / Demo store; quantity `5`; rate `10`; line discount `0` | Total `50`; stock `15`; supplier credit balance `50` |
| **Inventory → Sale Voucher** / `S-001` | **Buyer**: Demo customer | Icode `DEMO-CUP`; base Unit; **Unit Qty** `2`; automatic Rate `20`; **Dis %** `10` | Total `36`; stock `13`; customer debit balance `36` |
| **Financial Transactions → Cash Receipt Voucher** / `CR-001` | Company cash account displays Demo cash | Acct. Code `A-CUST` (Demo customer); **Receipt** `36`; Narration `Demo receipt` | Cash `1036`; customer balance `0`; stock remains `13` |

On cash and sale vouchers, **New** and **Exit** ask before discarding changed fields or
incomplete rows. **Print** saves a draft for review; choose **Post and print**
to post and save its printable HTML. It uses the same posting and export checks
as the separate review/print actions. For this walkthrough, use **Save** and
**Post document** so the print checks in section 4 remain a separate step.

Two useful checks during entry:

- On the opening-stock line, **Convert large / base quantities** can replace
  manual quantity entry. Enter **Large quantity (Boxes)** `1` and
  **Base quantity (Pieces)** `0`, then press Enter to apply **Use base quantity**.
  The converter closes and restores line quantity focus. The line must show
  `10`. The conversion changes quantity; the invoice rate stays per base unit.
- For the purchase, select the item and click **Use stored rate**: expect `10`.
  On the sale, choosing Demo cup fills its rate `20` automatically. At the purchase review, click
  **Keep as draft** once. A fresh New form opens, and stock must still be `10`.
  Press **F8** in **Purchases**, select `P-001`, click **Open selected**, and
  **Post document**. Stock becomes `15` only after posting.

The stock opening already posts its own accounting entries. The separate cash
opening above deliberately adds cash only; it does not enter the inventory
opening a second time. A receipt is a separate voucher and does not allocate
itself to an individual invoice or provide an invoice-aging report.

## 4. Check reports and printable output

Expand **Reports** and select an individual report under **Financial reports**
or **Inventory reports**. Enter `2026-10-02` in both **From (YYYY-MM-DD)** and
**To (YYYY-MM-DD)**, then click **Run report**.

| Report | Additional choices | Expected result after the five documents |
| --- | --- | --- |
| **Stock quantities** | Stock pool `regular`; Store Demo store; item search `DEMO-CUP`; **Show stock quantities** | Opening `0`, In `15`, Out `2`, Closing `13` |
| **Item ledger** | Stock pool `regular`; Item Demo cup; Store Demo store; **Show item ledger** | The opening, purchase and sale appear; final running quantity `13` |
| **Account ledger** | Select Demo customer; **Show ledger** | Sale debit `36`, receipt credit `36`, closing `0` |
| **Trial balance** | No further selection | Period debits `1222`, credits `1222` |
| **Sub-trial balance** | No further selection | Demo assets contains cash, inventory and customer; grand period totals remain `1222` on each side |

The **Trial balance** closing column uses positive numbers for debit balances
and negative numbers for credit balances:

| Account | Closing |
| --- | ---: |
| Demo cash | 1036 |
| Demo inventory | 100 |
| Demo customer | 0 |
| Demo purchases | 50 |
| Demo sales | -36 |
| Demo supplier | -50 |
| Demo opening counterpart | -1100 |

The report's opening columns are zero because every example entry is dated
inside the selected period. Group rows include their children, so do not add
group totals to the same child totals again. Stock reports establish quantities;
inventory valuation and cost of goods sold are outside this evaluation.

Under **Reports → Inventory reports**, also check **Sales / returns detail**
and **Purchases / returns detail**. Choose **Invoices and returns**, then
**Choose filters**, leave filters blank and click **Show trade detail**. Expect
sales line/final amount `36` and quantity `2`; purchases line/final amount `50`
and quantity `5`. The **Quantities by item** button shows each item's base unit.
With an item/store filter, final document totals are omitted.

From a report, click **Save full report** and choose a new `.html` filename.
Then open **Inventory → Sale Voucher**, press **F8**, select `S-001`, click **Open
selected** and **Save printable copy** to a different new `.html` file. Open each file in a
browser and use its Print or Save as PDF function. Check the document number,
company address, Urdu item name, quantities, discount, total and page boundaries.
An existing output filename must be refused. Use a new name for another export.
These are native layouts; comparison with the original software's print forms
is still separate work.

## 5. Check the grouped menu and saved sale

Open the dropdown menus across the top. **Coding → Financial Coding** should
contain Accounts, **Coding → Inventory Coding** the item masters and Opening
stock, and Coding itself Cities. **Financial
Transactions** must offer Opening Balance, Cash Payment Voucher, Cash Receipt
Voucher and Journal Voucher, in that order. Bank receipt/payment and Reversals
must not appear as entry options.

Open **Inventory → Sale Voucher** and check that a new-entry form appears without a
saved-record list. Type a note, press **F8**, then use **Back to entry**; the note
must still be present. Press **F8** again and open `S-001`. Its posted values and
printable copy remain available; there is no edit or **Reverse document** action. Close
the detail, return to entry and close the unsaved test form, then rerun the
reports. The five posted documents, stock `13`, customer balance
`0` and period debit/credit `1222` each must remain unchanged. This step posts
no additional documents.

## 6. Back up, restore and reopen

1. As the administrator, click **Setup → Create backup** and save to the new filename
   `demo-backup.sqlite3`. Expect **Backup saved**.
2. Choose **Setup → Restore backup**.
3. Select `demo-backup.sqlite3`, choose the new destination
   `demo-restored.sqlite3`, and enter **Backup administrator username**
   `demo_admin` plus the passphrase held by that account when the backup was made.
   Click **Restore to new file**.
4. Sign in again. Restore requires fresh sign-in. Use **F8** in the relevant
   modules to check that all five core documents exist. Check stock is `13`, and
   the trial balance still has period totals
   `1222` on each side.
5. Close and relaunch using your actual restored path:

   ```sh
   python3 BIFMA_TRADERS_V3.py --transaction-database '/path/to/Bifma evaluation/demo-restored.sqlite3'
   ```

   Confirm the same results. The original `demo.sqlite3` and backup should still
   be present as separate files.

Do not copy the active database file as a substitute for **Create backup**.
An upgrade drill needs a separately prepared older-version test copy and the
explicit upgrade procedure documented in the native database notes.
This exercise starts with a current empty database and does not test upgrading.

## 7. Evaluate counter sales and cash returns

Continue in the restored demo database after completing the five posted
documents above. Its company already has **Demo cash** configured. Keep every
date at `2026-10-02`, both rounding choices as above and header adjustments zero.

Cash originals automatically show **Cash account**: Demo cash. Enter the optional
customer/supplier name `Demo walk-in` and mobile `001-555-0100`. They are receipt
contacts, not a separate ledger party. Cash returns use **Choose original line**
to select the corresponding cash invoice and inherit its cash account/contacts.

Open these sections under **Inventory**:

| Section / number | Entry | Expected result |
| --- | --- | --- |
| **Cash purchases** / `P-001` | Demo cup / Demo store, quantity `3`, rate `10`, line discount `0` | Cash `1006`, stock `16` |
| **Counter sales** / `S-001` | Same item/store, quantity `2`, rate `20`, line discount `0` | Cash `1046`, stock `14` |
| **Counter sale returns** / `CSR-001` | Select cash `S-001`'s original line; return quantity `1` | Cash `1026`, stock `15` |
| **Cash purchase returns** / `CPR-001` | Select cash `P-001`'s original line; return quantity `1` | Cash `1036`, stock `14` |

The ordinary `P-001` and `S-001` still exist unchanged. Cash invoices have separate
number namespaces. Do not create receipt/payment vouchers for these movements:
their invoice/return postings already record the full cash amount.

Expect **9 posted documents** in this database and period debits/credits both
**1322**. Cash is **1036**, inventory **100**, customer **0**, purchases **70**,
sales **−56**, supplier **−50** and opening counterpart **−1100**. Stock is **14**.
In trade reports choose **Entry type** → **Cash entries**, then **Invoices and
returns**. Sales show two rows/net amount **20**; purchases show two rows/net
amount **20**. Account entries remain separate; combined sales line/final total
is **56** with every header adjustment zero in this example.

Press **F8** in each corresponding module to open and print the counter invoice
and cash return. Check the full cash amount, saved
contacts and original document reference. Save a new `demo-cash-backup.sqlite3`
after this extension; the earlier backup still represents the earlier exercise.
Restore the new backup to another new file and verify the 9 documents and
balances above. The original `demo-backup.sqlite3` is not overwritten.

The current nine-document totals, trade-report rows, cash book and city
regrouping shown here and in section 8 were checked through services on a temporary synthetic database
on 2026-10-03. No real working database was changed.

The earlier [cash walkthrough receipt](cash_walkthrough_verification.json)
describes a dated 12-document scenario containing extra sale/reversal fixtures.
Its totals remain historical evidence and do not describe this current
nine-document operator exercise. Compatibility diagnostics may still seed
reversals through internal services; there is no reversal creation UI.

## 8. Optional city balances and cash book

Complete section 7 first so the database contains all **9 posted documents**.
Continue using `2026-10-02` for both report dates.

In **Reports → Financial reports**, choose **Cash book**, then **Run report**. The configured
**Demo cash** account should be selected in **Choose cash account**. Click
**Show cash book**. Expect six entries, opening **0**, cash in **1086**,
cash out **50**, and closing **1036**. The book includes the demo cash
opening/receipt plus four cash invoice/return postings. Save the full
report and check that the selected account, document kinds and all six rows
are present.

Choose **Account balances by city**, then **Run report**. Leave the city blank,
**Account kind** at **All kinds**, search empty and **Include zero closing
balances** checked. **Show city balances** should show seven accounts in
**Unassigned**, including Demo customer's zero closing balance. Expect closing
debit **1206** and closing credit **1206**. **Totals by city** must include all
seven accounts, period debit/credit **1322** each and net closing **0**.

To check current-city grouping:

1. In **Coding → Cities**, create code `DEMO-CITY`, name
   `Demo city`.
2. Open **Coding → Financial Coding → Accounts**, press **F8**, select **Demo cash** and choose
   **Edit selected**. Select **Demo city**, and save.
3. Rerun the city report with all cities/kinds and zero closings included.
   The same seven accounts and grand totals remain. **Demo city** has one
   account with closing debit **1036** and credit **0**; **Unassigned** has
   six accounts with closing debit **170** and credit **1206**.
4. Select **Demo city** and **Account kind: Cash** in a new report run.
   Expect only Demo cash, closing debit **1036**, credit **0**.
5. Save the full filtered report. Check the selected city/kind and the explicit
   current-city explanation. These filters must also apply to the HTML output.

Changing a current city regroups existing postings; it does not rewrite them.
The report is an account-balance view and does not assert which invoices are
unpaid or overdue. A customer/both-kind filter uses your explicit account setup.
The city report requires `trial_balance/read`; the cash book requires
`ledger/read`. Counter-sale or receipt-entry access alone is insufficient.

The dated [city/cash receipt](city_cash_walkthrough_verification.json) records
the earlier 12-document scenario with reversal fixtures. Keep that historical
result distinct from the nine-document scenario above. Manual workstation,
browser/printer and restore acceptance remain separate checks.

## Record the evaluation result

Record your application source/build, operating system, test date, display
scaling, browser/printer, and pass/fail for setup, draft/post, the totals above,
grouped navigation, new-entry/F8 browsing, printed output and restore. Check
that English/Urdu text is readable, entry works with the
available keyboard, and dialogs remain usable at normal screen size. Save only
synthetic screenshots/output with the result; never include the passphrase.
Also check the main window at its minimum size of 1050 × 720, including access
to the top menus, cash-voucher rows and lower dialog controls.
Record sections 7 and 8 separately from the five-document core results so
the expected cash and report balances are not mixed.

The automated [native GUI smoke](../tools/smoke_native_ui.py) exercises real
widgets for company setup, stock opening, sales, reports and restore,
with compatibility reversal fixtures created through services. It uses different
example numbers. These checks do not substitute for manually running the
application, printing and restoring on the intended workstation. Historical
packaged-build checks and their exact scope are recorded in
[Windows release notes](WINDOWS_RELEASE.md) and [Linux release notes](LINUX_RELEASE.md).

Linked returns select an eligible original invoice line or a new posted invoice
line; aggregate opening balances alone cannot supply one. Invoice allocation/aging,
unproved tax/labour and delivery workflows remain limited or unfinished. Consult
[native workspace notes](NATIVE_WORKSPACE.md)
before extending this evaluation to real operating procedures.
