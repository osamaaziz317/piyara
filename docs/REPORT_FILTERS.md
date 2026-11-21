# Original-style report filters

Updated 2026-11-20 from the supplied Cash Book and Party Ledger clips and the
Sub Trial Balance photograph.

Reports → Financial reports opens compact dedicated filters for Cash Book,
Party Ledger (the existing Account ledger menu) and Sub Trial Balance. Cash
Book asks for one date and company. Party Ledger asks Date From, Date To,
party/account and company. Sub Trial Balance asks an as-of date and company.
The headings use the Urdu titles visible in the references. Dates display as
DD-MM-YY and also accept DD-MM-YYYY or YYYY-MM-DD. Calendar errors and reversed
ranges retain the form and its entered values.

View opens a separate report preview. Closing that preview restores the same
filter, selection, focus and modal grab. The preview has receipt/payment or
debit/credit/running-balance columns, exact period totals, pagination for large
ledgers and the complete authorized print/save action. **Print / page
preview** opens the full HTML in the browser; Ctrl+P there prints/saves PDF.
**Save printable copy** keeps a file separately. Within the result preview,
Ctrl+P opens the page and Ctrl+S saves the copy, guarded against nested modals
and active workers. Keyboard Enter advances
dates → party/code/Find when applicable → Company → View. The account Find
retains the shared live search and stable-ID behavior. Company changes clear the
previous selected account and reload eligible choices through fresh service
checks. Reading, paging and printing keep the selected report company even if
it differs from the company in the main application header.

Cash Book uses the company's verified configured cash account. An explicit
account chooser is shown only when no eligible configured account exists. It
does not guess a cash role. The preview uses the existing report queries:
account balances carry the sealed cutoff opening, followed by new activity;
the original transaction stream is not replayed. Party Ledger's initial From
date retains cutoff + 1 day. Staff may enter or clear their dates explicitly.

The source photo shows a Sub-head dropdown in Sub Trial Balance. The recovered
account chart lacks established parent relationships, so this increment does
not construct a subgroup filter from code prefixes or suggest that the native
parent hierarchy is the original hierarchy. The existing native hierarchy and
full snapshot/export are used unchanged.

Implementation is in `bifma/report_ui.py`. `native_ui.py` routes the three menu
entries and binds exports to the captured company. Tests use only disposable
synthetic archives/databases and exercise filter retention, original date
format, bad dates, company changes, keyboard navigation, report opening and
export company/filters. No business database or source record is changed.
