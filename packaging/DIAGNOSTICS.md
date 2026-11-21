# Fixed portable workflow diagnostic

From the extracted `BIFMA_TRADERS` application folder, use the command for your
platform. The native launcher accepts exactly:

```
./BIFMA_TRADERS --check-workflow --output NEW.json
.\BIFMA_TRADERS.exe --check-workflow --output NEW.json
```

The output must not already exist. This command has no database, credential,
script, module or scenario argument. It creates private temporary synthetic
records and random administrator/approver passwords. The fixed diagnostic uses
real Tk widgets and normal application services. Only native file and message
dialogs receive fixed responses. An unexpected error dialog or ignored Python
finalizer error fails the run; the ordinary unraisable-error reporter still runs.
Neither tests nor recovery tools/schema nor real business files enter the bundle.

Successful evidence is written only after every application worker has finished,
Tk is destroyed and the complete temporary directory (databases, sidecars,
backup, restored database and HTML files) has been removed. A failed step or
cleanup produces no successful result file. No generated credentials, tokens,
record IDs or temporary paths appear in the evidence.

`schema_version: 1` and `scenario: native-workflow-v2` define an ordered list of
17 named steps. The first step destroys real traced Form and standalone variable
owners on the Tk thread, verifies that their Tcl variables and callback commands
are gone, then forces cyclic collection on the actual application worker while
the UI thread waits. Weak references must clear without any ignored errors.
The primary company proves base-unit conversion, sale total 34,
regular stock 8, breakage stock 2, real reports/HTML, and a restricted approver's
post/reversal. The backup is taken after the primary company has four documents and the
cash company has five, before the approver posts and reverses a further sale.
Restore therefore contains nine posted documents. The primary company
ultimately has six posted documents.

A second company proves full cash purchase/sale, both linked return types,
refund reversal, saved settlement output, overlapping ordinary/cash invoice
numbers, and explicit cash report filtering/export. It has five posted
documents, quantity 7, cash closing -34, three sales report rows with net 20 and
zero extra receipt/payment vouchers. The original synthetic database thus has
**11 posted documents total**. After restore, the diagnostic checks zero active
copied sessions, signs in afresh through the widgets, and verifies all five cash
documents, their saved cash bindings/contact details, quantity 7 and cash ledger
closing -34. The pre-login revocation count is explicitly named
`restored_active_sessions_before_login`; a successful fresh login creates its
own legitimate session. Every posted journal is independently summed as
exact Decimal values and must balance.

Scenario v2 adds two report steps without adding financial documents. Cash book
selects the configured cash account, returns all five posted cash movements with
debits 36, credits 70 and closing -34, and exports every row. A next-day filter
exports no movements and preserves the -34 opening/closing balance. City balances
assign the existing cash account to a synthetic city and compare all three
active balances and both city groups against the independent full trial balance:
period debit/credit 106, separate closing debit/credit 54. City/kind/code filters
return only the selected cash account. Full and filtered HTML exports are checked
through the actual report widgets, including their exact named printable totals. The gate rejects old v1 workflow evidence.

The shared build-only `frozen_checks.py` drives Linux, Wine and native Windows
PowerShell recipes. It requires seven distinct gates, including this complete
workflow from a relocated directory containing spaces. Both launchers' runtime,
data-free welcome, explicit historical database requirement and read-only
synthetic historical preview remain mandatory. Archive publication validates the
matching frozen application version, all 17 ordered steps, exact typed effects
and successful cleanup; a boolean process-success flag alone is insufficient.

These checks establish fixed synthetic workflows in the actual frozen runtime.
They do not establish original Forms/report fidelity, physical printer/font
acceptance, arbitrary business workflow coverage or native Windows qualification
when the Windows runtime is hosted by Wine.

For an unfrozen check from the source checkout root, invoke the packaging launcher
explicitly (the ordinary source launcher has only its database argument):

```sh
python3 -B -c 'import sys; sys.path.insert(0, "packaging"); import native_entry; raise SystemExit(native_entry.main())' --check-workflow --output /new/path/workflow-result.json
```

Use a new output path and a working graphical display. Source evidence reports
`frozen=false` and cannot satisfy the packaged workflow gate.
