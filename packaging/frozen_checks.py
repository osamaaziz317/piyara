"""Identical frozen-launcher gates for Linux, Wine and native Windows recipes.

Build-only code: this module and synthetic historical schema never enter the
application bundle. No caller-supplied code or diagnostic scenario is executed.
"""

import argparse
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

import release_support as shared

FROZEN_CHECKS = frozenset({"native-runtime", "history-runtime", "native-welcome",
    "history-explicit-database", "history-preview", "relocated-welcome", "native-workflow"})
WORKFLOW_STEPS = ("widget-variable-lifecycle", "create-admin-company", "opening-unit-conversion", "discounted-sale",
    "document-export", "native-reports", "report-export", "breakage-pool",
    "cash-purchase-sale", "cash-linked-returns-reversal", "cash-reports-separation",
    "cash-book-report", "city-balances-report", "backup",
    "restricted-approver-reversal", "balanced-journals", "restore-revokes-sessions")
WORKFLOW_EFFECTS = {"widget_variable_lifecycle": True, "sale_total": "34", "regular_quantity": "8", "breakage_quantity": "2",
    "primary_posted_documents": 6, "posted_documents": 11, "restored_documents": 9,
    "backup_scope": "primary-and-cash-before-approver", "restored_active_sessions_before_login": 0,
    "restored_cash_documents": 5, "restored_cash_regular_quantity": "7",
    "restored_cash_account_closing": "-34", "restored_cash_settlement_preserved": True,
    "journal_imbalances": 0, "cash_posted_documents": 5, "cash_regular_quantity": "7",
    "cash_account_closing": "-34", "cash_extra_vouchers": 0, "cash_sales_report_rows": 3,
    "cash_sales_report_net": "20", "cash_book_rows": 5, "cash_book_debit": "36", "cash_book_credit": "70",
    "cash_book_closing": "-34", "configured_cash_selected": True, "city_balance_accounts": 3, "city_balance_groups": 2,
    "city_debit": "106", "city_credit": "106", "city_closing_debit": "54", "city_closing_credit": "54",
    "city_filtered_accounts": 1}


def validate_workflow(value, version):
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value.get("schema_version") != 1 or value.get("scenario") != "native-workflow-v2":
        raise shared.ReleaseError("Frozen workflow evidence has an unsupported protocol.")
    if value.get("application_version") != version or value.get("frozen") is not True:
        raise shared.ReleaseError("Workflow must run in the matching frozen application.")
    if value.get("passed") is not True or value.get("temporary_data_removed") is not True:
        raise shared.ReleaseError("Frozen workflow did not finish and remove its temporary data.")
    if json.dumps(value.get("steps"), sort_keys=True) != json.dumps(
            [{"step": step, "passed": True} for step in WORKFLOW_STEPS], sort_keys=True):
        raise shared.ReleaseError("Frozen workflow named steps are incomplete.")
    if json.dumps(value.get("effects"), sort_keys=True) != json.dumps(WORKFLOW_EFFECTS, sort_keys=True):
        raise shared.ReleaseError("Frozen workflow exact effects differ.")


def validate_report(report, version):
    rows = report.get("checks", [])
    if not isinstance(rows, list) or len(rows) != len(FROZEN_CHECKS) or any(
            not isinstance(row, dict) or row.get("passed") is not True for row in rows) or {
            row.get("check") for row in rows} != FROZEN_CHECKS:
        raise shared.ReleaseError("Required frozen checks are incomplete.")
    validate_workflow(report.get("workflow"), version)


def run_checks(bundle, run, schema_path, *, execute, audit_bundle, windows):
    """Run fixed subprocesses. execute owns logging/timeouts, never scenario input."""
    audit_bundle(bundle)
    empty = run / "empty-working-directory"
    empty.mkdir()
    suffix = ".exe" if windows else ""
    native, history = bundle / ("BIFMA_TRADERS" + suffix), bundle / ("BIFMA_HISTORY" + suffix)
    checks = []
    def call(name, arguments, expected=0):
        output = execute(name, arguments, empty, expected)
        checks.append({"check": name, "passed": True})
        return output
    def native_result(name, executable, argument):
        output = run / ("frozen-" + name + ".json")
        call(name, [str(executable), argument, "--output", str(output)])
        return shared._json(output)
    native_runtime = native_result("native-runtime", native, "--check-runtime")
    history_runtime = json.loads(call("history-runtime", [str(history), "--check-runtime"]))
    welcome = native_result("native-welcome", native, "--smoke-welcome")
    if welcome != {"native_welcome": "passed", "database_opened": False}:
        raise shared.ReleaseError("Frozen native welcome did not confirm data-free startup.")
    call("history-explicit-database", [str(history)], 2)
    with tempfile.TemporaryDirectory(prefix="synthetic-history-", dir=run) as temporary:
        archive = Path(temporary) / "synthetic.sqlite3"
        with closing(sqlite3.connect(archive)) as connection, connection:
            for table in shared._json(schema_path)["tables"]:
                columns = ",".join('"' + column["name"] + '" TEXT' for column in table["columns"])
                connection.execute('CREATE TABLE "' + table["name"] + '" (' + columns + ')')
            connection.execute("CREATE TABLE _recovery_metadata (key TEXT PRIMARY KEY,value TEXT)")
            connection.execute("INSERT INTO _recovery_metadata VALUES ('report',?)", (json.dumps({"status": "recovered_unverified"}),))
            connection.executemany("INSERT INTO CONFIG (CMP_ID,CMPNAME) VALUES (?,?)", [("1", "Synthetic A"), ("2", "Synthetic B")])
        before = shared.digest(archive)
        output = call("history-preview", [str(history), "--database", str(archive), "--smoke-test"])
        if "Preview smoke test passed:" not in output or shared.digest(archive) != before:
            raise shared.ReleaseError("Frozen preview failed or changed its synthetic archive.")
    relocated = run / "relocation with spaces" / "BIFMA_TRADERS"
    shutil.copytree(bundle, relocated, symlinks=not windows)
    audit_bundle(relocated)
    if native_result("relocated-welcome", relocated / native.name, "--smoke-welcome") != welcome:
        raise shared.ReleaseError("Relocated native welcome failed.")
    workflow = native_result("native-workflow", relocated / native.name, "--check-workflow")
    report = {"native_runtime": native_runtime, "history_runtime": history_runtime,
              "checks": checks, "workflow": workflow}
    validate_report(report, native_runtime["application_version"])
    if list(empty.iterdir()):
        raise shared.ReleaseError("Frozen diagnostics wrote unexpected working-directory files.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        raise shared.ReleaseError("This command runs the native Windows frozen gates on Windows.")
    # The same AMD64 PE inventory is required for native Windows and Wine.
    import wine_release
    def execute(name, arguments, cwd, expected):
        output, _ = wine_release.logged(arguments, args.run / ("frozen-" + name + ".log"),
                                         cwd=cwd, timeout=180, expected=expected)
        return output
    report = run_checks(args.bundle, args.run, args.schema, execute=execute,
                        audit_bundle=wine_release.pe_inventory, windows=True)
    shared._write_new_json(args.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
