# FM Solutions

Windows desktop prototype for wholesale management and accounting, developed for FM Solutions by Fahad Masood. Contact 0326 3833835.

## Run the prototype

Use Python 3.10 or newer and an installed copy of Microsoft Edge or Google Chrome. Run the command for your operating system from the project folder.

**Linux terminal**

```bash
python3 app.py
```

**Windows PowerShell**

```powershell
py -3 app.py
```

The launcher opens a dedicated application window and stops its local server when the window closes. To use a standard browser window, add `--no-launch` to your command, then open [the local preview](http://127.0.0.1:8765/). Leave the terminal running during the preview.

| Preview access | Username | Password |
| --- | --- | --- |
| Administrator | `admin` | `demo123` |
| Clerk | `clerk` | `demo123` |

Choose **BIFMA Traders** to explore a plastic wholesaler or **Karachi Tyre Wholesale** to explore a tyre wholesaler. Both businesses accept these demo credentials. Use **Sign out or switch business** from the user menu. The clerk account can enter transactions and browse the masters allowed by its permissions. Sign in as the administrator to review balances, late payments, and Setup.

## Set up the Super Admin and add staff

1. Select your business and sign in with its current administrator account. Fresh demos use `admin` / `demo123`.
2. Open **Setup → Users & permissions → Set up Super Admin**. Enter your current administrator password, then choose and confirm your own new password. The form shows the username, normally `superadmin`. Each business has its own Super Admin.
3. The app switches to the Super Admin account. Built-in demo accounts that still use `demo123` are deactivated for this business.
4. In **Users & permissions**, choose **New user**. Enter the employee's username and initial password. Choose **Clerk** and select the sections and actions the employee needs. Choose **Administrator** for full administrative access.
5. Anyone signed in can choose **Change password** from the user menu at the top right and set their own password.

## Try a workflow

1. In **Coding → Party accounts**, search for `احمد` or `Ahmad` to see the bilingual account. Open **Items** to check stock balances.
2. In **Inventory → Sale credit**, press Enter on **Start invoice**, then Enter to open the party picker. Search, choose a party with Enter, then Enter on the item selector. Enter quantity and rate and use F6 to save. Open **Saved records** to browse invoices and use F7 to print the selected document.
3. Open **Accounting → Party ledger** and pick the same party. Create a **Cash Receipt Voucher** for a portion of the invoice. Enter its invoice number in **Reference number** to allocate the payment to it, then check the remaining balance in **Late Payments**.
4. Open **Accounting → Trial balance** and compare the debit and credit totals. Visit **Setup → Security & activity** to review the changes. **Reports** lists the requested report destinations while their layouts await specifications.

Use Tab and Shift+Tab to move between controls. Use Enter to advance or choose, Esc to close a dialog or working section, F2 or Ctrl+F to search, F3 to add, F4 to edit, F5 to refresh, F6 or Ctrl+S to save, F7 to print, and F8 to delete or cancel where your role permits. Ctrl+K opens workspace search. Ctrl+Page Up and Ctrl+Page Down switch open working sections. The **Window** menu can also switch sections and close them together.

## Prototype architecture

- `app.py` serves the interface on the local loopback address and starts Edge or Chrome in application mode. The dedicated browser profile keeps demonstration data across launches. The Windows app window centers around the sign-in card and maximizes after sign-in.
- `web/js/app.js` owns navigation and session presentation. Entry, accounting, dashboard, Setup and Help views use the service in `web/js/demo.js`. The interface uses English and Urdu together, with right-to-left fields and system Urdu font fallbacks.
- `web/js/demo-accounting.js` simulates balanced journal postings, inventory valuation and invoice allocation independently of screen rendering. The default fiscal year runs July through June. Credit receipts apply to a referenced invoice first, then to the oldest eligible open invoices. Weighted average is the default stock valuation setting.
- Demo service records and accounts are partitioned by business in the browser profile. Keep the same profile and port to reopen the same demonstration data. Backup and restore in Setup operate on one business. The browser profile contains sample records and demo credentials. Use fictional business data and dummy credentials here.

This delivery covers Phase 1. The desktop window, local sample repository and service boundaries support evaluation before production database, synchronized multi-device access, authentication, licensing and Windows installer work. Backup scheduling is represented as a saved preference. Detailed report screens await the supplied layouts.
