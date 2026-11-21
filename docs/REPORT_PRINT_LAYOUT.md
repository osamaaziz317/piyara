# Financial report print layouts

Reviewed against the owner's media in `videos/` on 2026-11-20. The generated
files remain complete, privately published HTML reports that can be printed
through the browser. No browser or printer is launched by the exporter.

The Cash Book output in the videos shows a monochrome portrait page with
payments on the left and receipts on the right. Each side contains amount,
voucher reference/type and detail, followed by its total. The Party Ledger
output places voucher number, type and date at the right, narration in the
middle, debit/credit beside it, and the running balance with a Dr/Cr indicator
at the left. Running balances are blue. The new financial renderer follows
these observed arrangements, with compact Urdu/English headings and day-first
display dates. `CPV`, `CRV`, `JV` and `SL` are display abbreviations only; the
saved native kind remains in each abbreviation's title. Unknown kinds remain
unchanged.

The Sub Trial Balance screenshot (`WhatsApp Image 2026-11-16 at 12.24.50
AM.jpeg`) establishes its filter form and Urdu title, not its printed columns.
Its export now uses the same compact financial styling while retaining the
existing account hierarchy, opening/debit/credit/closing columns and explicit
direct-posting grand totals. This is not evidence of an exact original Sub
Trial print template.

All rows, amounts and totals come from the existing authorized report snapshot.
The renderer neither changes the business queries nor recalculates totals.
Cash account debit is shown as receipt and credit as payment. Each nonzero side
of a saved row is retained in that side's table. The current snapshot contains
saved cash-line narration, not the identity of every counterpart account, so the
export does not invent counterpart names. A supplied narration remains the
detail text.

Account/cash ledger reports still carry the sealed closing snapshot once at the
baseline cutoff; they do not reconstruct older transaction turnover. A range
including the cutoff shows that snapshot in the period columns. Baseline date,
archive SHA-256, current label source, cash account eligibility, sign convention
and hierarchy overlap notes remain in the compact notes at the report's end.
Positive running balances remain positive debit values, negative balances remain
negative credit values, and the Dr/Cr indicator supplements the signed value.

Urdu text uses isolated RTL spans with local Nastaliq/Naskh/FreeSerif/Windows
fallbacks. Numeric values remain isolated LTR exact decimals. No external fonts,
images, scripts or active content are added.

Validation: `python3 -m unittest tests.test_report_export
tests.test_city_cash_reports tests.test_city_cash_export
tests.test_baseline_reports` passes 56 tests,
including exact values under a low Decimal precision context, payment/receipt
placement, escaping, empty panels and nonduplicated hierarchy totals. A local
headless Chrome print review using 240 synthetic entries retained all 240 unique
row identifiers in both Cash Book (6 pages) and Party Ledger (10 pages), with
their closing totals. This verifies that fixture's pagination, not complete
Oracle report parity or every printer environment.
