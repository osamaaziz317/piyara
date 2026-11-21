# Account balances by city and cash book

Development source `0.3.0.dev5` adds two native ledger reports. They read posted
entries and sealed baseline closings from the selected company, with exact
Decimal arithmetic and inclusive business dates. Original historical detail
remains preserved. Read [OPERATIONAL_REPORTS.md](OPERATIONAL_REPORTS.md) for
opening/cutoff semantics. The [source investigation](CITY_CASH_REPORT_EVIDENCE.md)
explains which original report rules are known and which require the missing
forms/templates.

## Account balances by city

Open **Reports → Account balances by city**, set the date range, then **Run
report**. In **City balance filters**, choose all cities, a specific city, or
**Unassigned** when accounts without cities have posted history. Choose an
optional account kind, literal account code/name search, and whether to retain
zero closing balances. **Show city balances** displays one row per account.

This report uses **current** city assignments, account kinds and names. Changing
an account's city can regroup its past postings; the postings themselves remain
unchanged. Issued document snapshots do not contain the account's city. Account
kinds come from explicit setup, not codes, names or balance signs. These are
account balances, not unpaid invoices, overdue amounts or collection targets.

Opening is debit minus credit before the start date. Period debit and credit
include the selected dates; closing is opening plus period debit minus period
credit. Accounts with company postings through the end date remain eligible
even when inactive or no longer postable. Zero closing accounts are included by
default. Unchecking **Include zero closing balances** removes those accounts
from the rows, city groups and every total, including opening and period debit/
credit amounts. It does not merely hide their rows. With no start date, opening
is zero.

**Totals by city** shows every filtered account across all pages. Each city's
closing debit total sums positive account closings; its closing credit total
sums the magnitude of negative account closings. They remain separate rather
than being hidden inside a net city amount. Grand totals count each direct
account once and never add hierarchy subtotals again. A filter can produce an
unbalanced subset even when the company's complete trial balance is balanced.

The service requires current company access and `trial_balance/read`. Master
setup access is not required. Filters apply to both visible pages and complete
HTML output; current labels, filter choices and separate city/grand totals are
printed explicitly.

## Cash book

Open **Reports → Cash book**, set the dates and click **Run report**. The
**Choose cash account** dialog displays the configured eligible cash account
when available. Otherwise choose explicitly; no other account is silently
substituted. Click **Show cash book**.

Eligible choices include current cash accounts that are active and accept
postings, or have posted history in this company. A former cash account requires
a saved cash role on its own posted journal activity in this company. That
verified history keeps inactive and formerly postable accounts accessible;
missing or inconsistent saved metadata cannot establish a former cash role.
Output uses the current account name and distinguishes a current cash role from
a saved one. The ordinary account ledger remains available for other accounts.

The cash book includes the baseline opening and **every posted journal line** for that company/account:
opening balances, ordinary and cash invoices, returns, receipts/payments,
journals and reversals. It does not infer entries from a received-amount field or
filter them to cash voucher kinds. Compound journals are not assigned an
invented single customer/supplier counterpart.

Rows show date, document number and kind, narration, debit/cash in, credit/cash
out and running balance. The order is business date, posting sequence, posting
time, document ID and journal-line number. Running balances include earlier
pages. Opening, cash in/out and closing totals cover the full selected period.
Originals and reversals stay on their respective dates.

The service requires `ledger/read` and current company access. Permission to
enter counter sales or receipts alone does not expose the company's cash book.

## Complete output and limits

**Save full report** creates a new standalone printable HTML file. All selected
rows, current labels, totals and filters come from one database snapshot. The
export refuses more than 100,000 detail rows while collecting them, rather than
silently truncating. Required permissions are checked again immediately before
publishing the file. Existing files are never replaced.

Neither report changes transactions, allocates payments or determines invoice
aging. Their continued baseline/native calculations can be checked
against trial balance and account ledger. Original layout, recovery-policy and
target printer/font acceptance remain separate requirements.
