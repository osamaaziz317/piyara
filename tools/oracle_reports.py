#!/usr/bin/env python3
"""Source-specific, read-only Oracle view parity checks for the reviewed dump.

Only the seven definitions identified by exact reviewed SQL hashes may be
installed in the disposable verification schema. This is not a generic loader
for SQL from arbitrary backups. Original application report layouts are absent.
"""

import argparse
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

from oracle_compare import ComparisonError, identifier, read_json_lines, row_multiset
from oracle_reconcile import (OWNER, ROOT, RunError, atomic_json, check_network,
                              docker, json_query, load_state, number_sql,
                              save_state, schema_for, sha256, sqlplus, utc_now)

sys.path.insert(0, str(ROOT))
from bifma.legacy_ledger import LegacyLedger
from bifma.legacy_stock import LegacyStock


REVIEWED = {
    "DCTOSALE_BAL": "cfc827dcfc0e70f005f9797329e56e79ceb75e3b32d23b0a930b327f87fa9d6f",
    "PUR_VW_DTL": "595e3e3496e16a7015101d5c9b05e65677ad7e237ff6dc88e8eebc43bb05cd0b",
    "SALE_VW_DTL": "ccf618cb7d092b596df3861ab8aa4a3fae80bafda3bbc7f174adbee5def57b70",
    "STOCK": "4ea0394bd25492cd0d2e04fe92bafe0fe264ec9f7f7af4334e217068133ca388",
    "STOCKB": "af590dc1be7de07ee4a80132eb751a65770029f946996cbffe439b27ac0f8902",
    "LEDGER": "3679a240b4656d2bfa950e5a7f9e6d675279c171e3207cb5c1b8047d7ecc1c55",
    "GET_PUR_RATE": "34f464de9e0a7393cc287ee7137596d89b0d6fc691d8670b081e0b87b7701bb9",
}
INNER = "/tmp/bifma_oracle_reports"
STOCK_FIELDS = {
    "TAG": "tag", "CMP_ID": "company_id", "STORE_ID": "store_id",
    "TRNID": "transaction_id", "TRNNO": "transaction_number", "TRNDATE": "date",
    "CHCODE": "account_code", "VNO": "line_id", "ICODE": "product_code",
    "UOM_ID_LARGE": "large_unit_id", "UOM_ID_SMALL": "small_unit_id",
    "QTY_SMALL_UOM": "unit_factor", "QTY_IN": "quantity_in", "QTY_OUT": "quantity_out",
    "VALUE_IN": "value_in", "VALUE_OUT": "value_out", "RATE": "rate",
}
LEDGER_FIELDS = {
    "TRNID": "transaction_id", "TRNNO": "transaction_number", "VTYPE": "voucher_type",
    "CMP_ID": "company_id", "CHCODE": "account_code", "TRNDATE": "date",
    "DR": "debit", "CR": "credit", "NARRATION": "narration",
}


def reviewed_definitions(schema):
    definitions = {obj["name"]: obj for obj in schema["views"] + schema["functions"]}
    result = []
    for name in ("GET_PUR_RATE", *[view["name"] for view in schema["views"]]):
        definition = definitions[name]
        digest = hashlib.sha256(definition["sql"].encode("utf-8")).hexdigest()
        if name not in REVIEWED or digest != REVIEWED[name]:
            raise RunError("A stored definition differs from the manually reviewed allowlist")
        result.append(definition)
    return result


def view_columns(view):
    text_columns = {"TYPE", "TAG", "CHCODE", "ICODE", "NARRATION"}
    return [{"name": name, "nullable": True,
             "oracle_type": "DATE" if name == "TRNDATE" else
                            "VARCHAR2(4000)" if name in text_columns else "NUMBER"}
            for name in view["columns"]]


def row_expressions(columns):
    result = []
    for column in columns:
        quoted = identifier(column["name"])
        kind = column["oracle_type"]
        if kind == "NUMBER":
            result.append(number_sql(quoted))
        elif kind == "DATE":
            result.append(f"TO_CHAR({quoted},'YYYY-MM-DD\"T\"HH24:MI:SS')")
        else:
            result.append(quoted)
    return result


def install(work, state):
    if not state.get("all_fields_match") or state.get("reports_installed_at"):
        raise RunError("Report installation requires a matched source and no previous installation")
    check_network(work, state)
    schema = schema_for(state)
    script = f"ALTER SESSION SET CURRENT_SCHEMA={OWNER};\n"
    for definition in reviewed_definitions(schema):
        source = definition["sql"]
        if definition["name"] == "GET_PUR_RATE":
            source = source.replace("CREATE FUNCTION GET_PUR_RATE", f"CREATE FUNCTION {OWNER}.GET_PUR_RATE", 1)
            script += source.rstrip() + "\n/\n"
        else:
            source = source.replace('CREATE VIEW "', f'CREATE VIEW {OWNER}."', 1)
            script += source.rstrip() + ";\n"
    script += ("DECLARE invalid_count NUMBER; BEGIN "
               f"SELECT COUNT(*) INTO invalid_count FROM ALL_ERRORS WHERE OWNER='{OWNER}'; "
               "IF invalid_count <> 0 THEN RAISE_APPLICATION_ERROR(-20001,'Reviewed definition compilation failed'); END IF; END;\n/\n"
               "EXIT SUCCESS;\n")
    sqlplus(work, state, script, "reports-install.log")
    state["reports_installed_at"] = utc_now()
    state["reviewed_definition_sha256"] = REVIEWED
    save_state(work, state)
    return {"reviewed_function_count": 1, "reviewed_view_count": 6, "compiled_without_errors": True}


def export_views(work, state):
    if not state.get("reports_installed_at") or state.get("reports_exported_at"):
        raise RunError("View export requires a fresh reviewed installation")
    check_network(work, state)
    schema = schema_for(state)
    docker(work, ["exec", "--user", "oracle", state["container"], "mkdir", "-m", "700", INNER])
    script = f"ALTER SESSION SET CURRENT_SCHEMA={OWNER};\nSET TRANSACTION READ ONLY;\n"
    script += f"SPOOL {INNER}/counts.jsonl\n"
    for view in schema["views"]:
        script += json_query(["'" + view["name"] + "'", number_sql("COUNT(*)")], identifier(view["name"]))
    script += "SPOOL OFF\n"
    for view in schema["views"]:
        script += f"SPOOL {INNER}/{view['name']}.jsonl\n"
        script += json_query(row_expressions(view_columns(view)), identifier(view["name"]))
        script += "SPOOL OFF\n"
    script += "COMMIT;\nEXIT SUCCESS;\n"
    sqlplus(work, state, script, "reports-export.log")
    docker(work, ["cp", state["container"] + ":" + INNER, str(work / "report_exports")])
    for file in (work / "report_exports").iterdir():
        file.chmod(0o600)
    counts = dict(read_json_lines(work / "report_exports" / "counts.jsonl"))
    state.update(reports_exported_at=utc_now(), view_counts=counts)
    save_state(work, state)
    return {"exported_view_count": len(counts), "view_row_counts": counts}


def scalar(value):
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, Decimal):
        return str(value)
    raise ComparisonError("Unexpected Python reconstructed value type")


def comparison(name, oracle_rows, python_rows, columns):
    expected, expected_digest = row_multiset(oracle_rows, columns)
    actual, actual_digest = row_multiset(python_rows, columns)
    return {"name": name, "columns": len(columns), "oracle_rows": sum(expected.values()),
            "python_rows": sum(actual.values()), "oracle_multiset_sha256": expected_digest,
            "python_multiset_sha256": actual_digest,
            "only_oracle_rows": sum((expected - actual).values()),
            "only_python_rows": sum((actual - expected).values()), "matched": expected == actual}


def source_companies(archive):
    with sqlite3.connect(Path(archive).resolve().as_uri() + "?mode=ro", uri=True) as connection:
        rows = connection.execute(" UNION ".join(
            f"SELECT CMP_ID FROM {table} WHERE CMP_ID IS NOT NULL"
            for table in ("CONFIG", "OPEN_STOCK", "JMST", "PMST", "SMST", "BRMST", "BDMST", "DCMST")))
        return sorted({row[0] for row in rows}, key=Decimal)


def write_owned_report(output, report, state):
    if output.is_symlink():
        raise RunError("Refusing to replace a report symlink")
    if output.exists():
        previous = json.loads(output.read_text())
        if previous.get("run_id") != state["run_id"] or previous.get("source_sha256") != state["source_sha256"]:
            raise RunError("Refusing to replace a report from another verification run")
        atomic_json(output, report)
    else:
        with output.open("x", encoding="utf-8") as file:
            file.write(json.dumps(report, indent=2) + "\n")


def compare_views(work, state, output):
    if not state.get("reports_exported_at"):
        raise RunError("Comparison requires complete Oracle view exports")
    schema = schema_for(state)
    stock, ledger = LegacyStock(state["archive"]), LegacyLedger(state["archive"])
    covered_companies = source_companies(state["archive"])
    absent_company = "999999999"
    while absent_company in covered_companies:
        absent_company = str(int(absent_company) + 1)
    comparisons, exports = [], []
    for view in schema["views"]:
        name = view["name"]
        columns = view_columns(view)
        oracle_rows = list(read_json_lines(work / "report_exports" / f"{name}.jsonl"))
        expected_count = state["view_counts"][name]
        if not isinstance(expected_count, str) or not re.fullmatch(r"0|[1-9][0-9]*", expected_count):
            raise RunError("Oracle view count metadata is invalid")
        if len(oracle_rows) != int(expected_count):
            raise RunError("An Oracle view export lost rows")
        hashes, digest = row_multiset(oracle_rows, columns)
        exports.append({"view": name, "columns": len(columns), "rows": len(oracle_rows),
                        "oracle_multiset_sha256": digest})
        if name not in ("STOCK", "STOCKB", "LEDGER"):
            continue
        company_index = view["columns"].index("CMP_ID")
        companies = sorted(set(covered_companies) | {row[company_index] for row in oracle_rows} | {absent_company}, key=Decimal)
        python_rows = []
        for company in companies:
            if name == "LEDGER":
                objects, mapping = ledger.entries(company), LEDGER_FIELDS
            else:
                objects = stock.movements(company, stream="regular" if name == "STOCK" else "breakage")
                mapping = STOCK_FIELDS
            python_rows.extend([scalar(getattr(obj, mapping[column["name"]])) for column in columns]
                               for obj in objects)
        comparisons.append(comparison(name, oracle_rows, python_rows, columns))
    report = {"source_sha256": state["source_sha256"], "archive_sha256": state["archive_sha256"],
              "run_id": state["run_id"], "image_id": state["image_id"], "verified_at": utc_now(),
              "reviewed_definition_sha256": REVIEWED, "source_view_exports": exports,
              "comparisons": comparisons, "all_compared_views_match": all(item["matched"] for item in comparisons),
              "scope": "Dataset-specific source view row parity; not original GUI/printed-report parity",
              "not_directly_compared": ["DCTOSALE_BAL", "PUR_VW_DTL", "SALE_VW_DTL"],
              "note": "PUR_VW_DTL and SALE_VW_DTL feed the compared final LEDGER view; DCTOSALE_BAL has no Python counterpart.",
              "source_company_count": len(covered_companies), "absent_company_checked": True,
              "coverage_limit": "The reviewed archive has no PMST/PDTL rows and PUR_VW_DTL returns zero rows; absent purchase paths are not qualified by this dataset.",
              "nls_numeric_characters": ".,", "nls_calendar": "GREGORIAN",
              "python_source_sha256": {name: sha256(ROOT / "bifma" / name)
                                       for name in ("legacy_stock.py", "legacy_ledger.py")},
              "verifier_sha256": sha256(Path(__file__)), "cleanup_complete": False}
    write_owned_report(output, report, state)
    state["report_parity_report"] = str(output.absolute())
    save_state(work, state)
    return {"all_compared_views_match": report["all_compared_views_match"], "comparisons": comparisons}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("install", "export", "compare"))
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=ROOT / "docs/oracle_report_parity.json")
    args = parser.parse_args()
    try:
        work, state = load_state(args.work)
        if args.phase == "install":
            result = install(work, state)
        elif args.phase == "export":
            result = export_views(work, state)
        else:
            result = compare_views(work, state, args.report)
        print(json.dumps(result, sort_keys=True))
        if args.phase == "compare" and not result["all_compared_views_match"]:
            return 2
    except Exception as error:
        message = str(error) if isinstance(error, (RunError, ComparisonError)) else "Private report verification failed"
        print(json.dumps({"error": message, "type": type(error).__name__}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
