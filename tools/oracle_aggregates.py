#!/usr/bin/env python3
"""Compare historical report filters and aggregates with direct Oracle SELECTs.

SQL is independently written over the reviewed original views. Case selectors
and result rows stay private; only case dimensions, counts and digests are
published. This qualifies the selected dataset/filters, not absent source paths
or the unavailable original report templates.
"""

import argparse
from collections import Counter
from datetime import datetime, time
from decimal import Decimal
import json
from pathlib import Path
import sys

from oracle_compare import ComparisonError, read_json_lines
from oracle_reconcile import (OWNER, ROOT, RunError, check_network, docker, json_query,
                              load_state, private_write, save_state, schema_for, sha256,
                              sqlplus, utc_now)
from oracle_reports import (LEDGER_FIELDS, STOCK_FIELDS, comparison, row_expressions,
                            scalar, source_companies, view_columns, write_owned_report)
from bifma.legacy_ledger import LegacyLedger
from bifma.legacy_stock import LegacyStock


INNER = "/tmp/bifma_oracle_aggregates"
STOCK_BALANCE_FIELDS = {
    "CMP_ID": "company_id", "STORE_ID": "store_id", "ICODE": "product_code", "INAME": "product_name",
    "OPENING_QTY": "opening_quantity", "OPENING_VALUE": "opening_value",
    "QTY_IN": "quantity_in", "QTY_OUT": "quantity_out", "VALUE_IN": "value_in", "VALUE_OUT": "value_out",
    "CLOSING_QTY": "closing_quantity", "CLOSING_VALUE": "closing_value",
}
LEDGER_BALANCE_FIELDS = {
    "CMP_ID": "company_id", "CHCODE": "account_code", "OPENING": "opening",
    "DR": "debit", "CR": "credit", "CLOSING": "closing",
}


def literal(value):
    if value is None:
        return "NULL"
    if not isinstance(value, str) or "\x00" in value:
        raise RunError("Unsupported report selector")
    return "'" + value.replace("'", "''") + "'"


def bound(value, end=False):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if len(value) == 10:
        parsed = datetime.combine(parsed.date(), time(23, 59, 59) if end else time.min)
    if parsed.tzinfo is not None or parsed.microsecond:
        raise RunError("Report selectors require naive second-precision dates")
    return parsed.isoformat(timespec="seconds")


def date_sql(value, end=False):
    value = bound(value, end)
    return f"TO_DATE({literal(value)},'YYYY-MM-DD\"T\"HH24:MI:SS')" if value else None


def columns_for(case, schema):
    if case["kind"] in ("stock_movements", "ledger_entries"):
        return view_columns(next(view for view in schema["views"] if view["name"] == case["view"]))
    fields = STOCK_BALANCE_FIELDS if case["kind"] == "stock_balance" else LEDGER_BALANCE_FIELDS
    return [{"name": name, "nullable": True,
             "oracle_type": "VARCHAR2(4000)" if name in ("ICODE", "INAME", "CHCODE") else "NUMBER"}
            for name in fields]


def where(case, *, include_lower):
    filters = case["filters"]
    conditions = ["V.CMP_ID=" + literal(case["company"])]
    lower, upper = date_sql(filters.get("date_from")), date_sql(filters.get("date_to"), True)
    if include_lower and lower:
        conditions.append("V.TRNDATE>=" + lower)
    if upper:
        conditions.append("V.TRNDATE<=" + upper)
    if filters.get("store_id") is not None:
        conditions.append("V.STORE_ID=" + literal(filters["store_id"]))
    if filters.get("product_code") is not None:
        conditions.append("V.ICODE=" + literal(filters["product_code"]))
    if filters.get("account_code") is not None:
        conditions.append("V.CHCODE=" + literal(filters["account_code"]))
    if filters.get("account_prefix") is not None:
        prefix = literal(filters["account_prefix"])
        conditions.append(f"SUBSTR(V.CHCODE,1,LENGTH({prefix}))={prefix}")
    return " AND ".join(conditions)


def query(case):
    view = case["view"]
    if case["kind"] in ("stock_movements", "ledger_entries"):
        return f"SELECT V.* FROM {OWNER}.{view} V WHERE " + where(case, include_lower=True)
    lower = date_sql(case["filters"].get("date_from"))
    prior = "V.TRNDATE<" + lower if lower else "1=0"
    period = "V.TRNDATE>=" + lower if lower else "1=1"
    if case["kind"] == "stock_balance":
        inner = (
            "SELECT V.CMP_ID,V.STORE_ID,V.ICODE,I.INAME,"
            f"SUM(CASE WHEN {prior} THEN V.QTY_IN-V.QTY_OUT ELSE 0 END) OPENING_QTY,"
            f"SUM(CASE WHEN {prior} THEN V.VALUE_IN-V.VALUE_OUT ELSE 0 END) OPENING_VALUE,"
            f"SUM(CASE WHEN {period} THEN V.QTY_IN ELSE 0 END) QTY_IN,"
            f"SUM(CASE WHEN {period} THEN V.QTY_OUT ELSE 0 END) QTY_OUT,"
            f"SUM(CASE WHEN {period} THEN V.VALUE_IN ELSE 0 END) VALUE_IN,"
            f"SUM(CASE WHEN {period} THEN V.VALUE_OUT ELSE 0 END) VALUE_OUT "
            f"FROM {OWNER}.{view} V JOIN {OWNER}.PUR_ITEM I ON V.ICODE=I.ICODE WHERE "
            + where(case, include_lower=False) + " GROUP BY V.CMP_ID,V.STORE_ID,V.ICODE,I.INAME")
        return ("SELECT G.*,OPENING_QTY+QTY_IN-QTY_OUT CLOSING_QTY,"
                "OPENING_VALUE+VALUE_IN-VALUE_OUT CLOSING_VALUE FROM (" + inner + ") G")
    inner = ("SELECT V.CMP_ID,V.CHCODE,"
             f"SUM(CASE WHEN {prior} THEN V.DR-V.CR ELSE 0 END) OPENING,"
             f"SUM(CASE WHEN {period} THEN V.DR ELSE 0 END) DR,"
             f"SUM(CASE WHEN {period} THEN V.CR ELSE 0 END) CR "
             f"FROM {OWNER}.LEDGER V WHERE " + where(case, include_lower=False)
             + " GROUP BY V.CMP_ID,V.CHCODE")
    return "SELECT G.*,OPENING+DR-CR CLOSING FROM (" + inner + ") G"


def cases_for(work, state, schema):
    companies = source_companies(state["archive"])
    cases = []
    def append(kind, view, company, filters, label):
        cases.append({"id": f"CASE_{len(cases)+1:03d}", "label": label,
                      "kind": kind, "view": view, "company": company, "filters": filters})
    for view in ("STOCK", "STOCKB", "LEDGER"):
        definition = next(item for item in schema["views"] if item["name"] == view)
        raw = list(read_json_lines(work / "report_exports" / f"{view}.jsonl"))
        offsets = {name: index for index, name in enumerate(definition["columns"])}
        for company_index, company in enumerate(companies, 1):
            rows = [row for row in raw if row[offsets["CMP_ID"]] == company]
            kind = "ledger_balance" if view == "LEDGER" else "stock_balance"
            prefix = f"{view}_company_{company_index}"
            append(kind, view, company, {}, prefix + "_all_time")
            dates = sorted({row[offsets["TRNDATE"]] for row in rows if row[offsets["TRNDATE"]] is not None})
            if not dates:
                continue
            middle, last = dates[len(dates)//2], dates[-1]
            period = {"date_from": middle[:10], "date_to": last[:10]}
            timestamp = {"date_from": middle[:10] + "T12:00:00", "date_to": last}
            if timestamp["date_from"] > last:
                timestamp["date_from"] = middle
            append(kind, view, company, period, prefix + "_date_period")
            append(kind, view, company, timestamp, prefix + "_timestamp_period")
            if view == "LEDGER":
                accounts = Counter(row[offsets["CHCODE"]] for row in rows if row[offsets["CHCODE"]] is not None)
                if accounts:
                    account = accounts.most_common(1)[0][0]
                    append(kind, view, company, {"account_prefix": account[:2]}, prefix + "_literal_prefix")
                    append(kind, view, company, {**period, "account_prefix": account[:2]}, prefix + "_prefix_period")
                    append("ledger_entries", view, company, {**period, "account_code": account}, prefix + "_filtered_entries")
            else:
                stores = [row[offsets["STORE_ID"]] for row in rows if row[offsets["STORE_ID"]] is not None]
                products = Counter(row[offsets["ICODE"]] for row in rows)
                store = Counter(stores).most_common(1)[0][0] if stores else None
                product = products.most_common(1)[0][0]
                if store is not None:
                    append(kind, view, company, {"store_id": store}, prefix + "_store")
                append(kind, view, company, {"product_code": product}, prefix + "_product")
                focused = {**period, "product_code": product}
                if store is not None:
                    focused["store_id"] = store
                append(kind, view, company, focused, prefix + "_combined_filters")
                append("stock_movements", view, company, focused, prefix + "_filtered_movements")
    absent = "999999999"
    while absent in companies:
        absent = str(int(absent) + 1)
    for view in ("STOCK", "STOCKB", "LEDGER"):
        append("ledger_balance" if view == "LEDGER" else "stock_balance", view, absent, {}, view + "_absent_company")
    return cases


def export(work, state):
    if not state.get("reports_exported_at") or state.get("aggregates_exported_at"):
        raise RunError("Aggregate export requires source view exports and no previous aggregate run")
    schema = schema_for(state)
    check_network(work, state)
    cases = cases_for(work, state, schema)
    private_write(work / "aggregate_cases.json", json.dumps(cases, indent=2))
    docker(work, ["exec", "--user", "oracle", state["container"], "mkdir", "-m", "700", INNER])
    script = f"ALTER SESSION SET CURRENT_SCHEMA={OWNER};\nSET TRANSACTION READ ONLY;\n"
    for case in cases:
        script += f"SPOOL {INNER}/{case['id']}.jsonl\n"
        script += json_query(row_expressions(columns_for(case, schema)), "(" + query(case) + ")")
        script += "SPOOL OFF\n"
    script += "COMMIT;\nEXIT SUCCESS;\n"
    sqlplus(work, state, script, "aggregates-export.log")
    docker(work, ["cp", state["container"] + ":" + INNER, str(work / "aggregate_exports")])
    for file in (work / "aggregate_exports").iterdir():
        file.chmod(0o600)
    state["aggregates_exported_at"] = utc_now()
    save_state(work, state)
    return {"exported_cases": len(cases)}


def compare(work, state, output):
    if not state.get("aggregates_exported_at"):
        raise RunError("Comparison requires complete Oracle aggregate exports")
    schema = schema_for(state)
    cases = json.loads((work / "aggregate_cases.json").read_text())
    stock, ledger = LegacyStock(state["archive"]), LegacyLedger(state["archive"])
    results = []
    for case in cases:
        filters = case["filters"]
        if case["kind"].startswith("stock"):
            operation = stock.balances if case["kind"] == "stock_balance" else stock.movements
            objects = operation(case["company"], stream="regular" if case["view"] == "STOCK" else "breakage", **filters)
            mapping = STOCK_BALANCE_FIELDS if case["kind"] == "stock_balance" else STOCK_FIELDS
        else:
            operation = ledger.balances if case["kind"] == "ledger_balance" else ledger.entries
            objects = operation(case["company"], **filters)
            mapping = LEDGER_BALANCE_FIELDS if case["kind"] == "ledger_balance" else LEDGER_FIELDS
        columns = columns_for(case, schema)
        rows = ([scalar(getattr(obj, mapping[column["name"]])) for column in columns] for obj in objects)
        result = comparison(case["label"], read_json_lines(work / "aggregate_exports" / f"{case['id']}.jsonl"), rows, columns)
        result.update(kind=case["kind"], view=case["view"], filter_dimensions=sorted(filters))
        results.append(result)
    report = {"run_id": state["run_id"], "source_sha256": state["source_sha256"],
              "archive_sha256": state["archive_sha256"], "image_id": state["image_id"],
              "verified_at": utc_now(), "case_count": len(results),
              "all_cases_match": all(result["matched"] for result in results), "cases": results,
              "method": "Direct independent Oracle SUM/CASE/WHERE SELECTs over reviewed source views versus Python report APIs; exact typed multisets, no tolerance or netting normalization",
              "scope": "Selected existing-data company/date/store/product/account filters, not unavailable original GUI or report templates",
              "python_source_sha256": {name: sha256(ROOT / "bifma" / name)
                                       for name in ("legacy_stock.py", "legacy_ledger.py")},
              "verifier_sha256": sha256(Path(__file__)), "cleanup_complete": False}
    write_owned_report(output, report, state)
    state["aggregate_report"] = str(output.absolute())
    save_state(work, state)
    return {"cases": len(results), "all_cases_match": report["all_cases_match"],
            "mismatched_cases": [row["name"] for row in results if not row["matched"]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("export", "compare"))
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=ROOT / "docs/oracle_aggregate_parity.json")
    args = parser.parse_args()
    try:
        work, state = load_state(args.work)
        result = export(work, state) if args.phase == "export" else compare(work, state, args.report)
        print(json.dumps(result, sort_keys=True))
        return 2 if args.phase == "compare" and not result["all_cases_match"] else 0
    except Exception as error:
        message = str(error) if isinstance(error, (RunError, ComparisonError)) else "Private aggregate verification failed"
        print(json.dumps({"error": message, "type": type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
