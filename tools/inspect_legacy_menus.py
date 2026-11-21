#!/usr/bin/env python3
"""Extract only functional menu/type definitions from the reviewed Oracle backup.

No company, account, user, permission-grant, contact, or integration values are
decoded. Only UM_MENU and ENTRY_TYPE iterators are used. No source SQL executes.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
import sys

try:
    from .recovery_oracle import RecoveryError, read_dump
except ImportError:
    from recovery_oracle import RecoveryError, read_dump


MENU_FIELDS = {
    "MENU_ID": "menu_id", "MODULE_NAME": "module_name", "MODULE_TYPE": "module_type",
    "APPLICATION": "application", "PARENT_NODE": "parent_id", "MENU_ADDRESS": "menu_address",
    "IS_VISIBLE": "visibility_flag", "FILE_NAME": "file_name", "IS_ROLE": "role_flag",
    "CATE": "category",
}
TYPE_FIELDS = {"TYPE_CODE": "type_code", "TYPE_DESC": "type_desc", "DESCRIPTION": "description"}


def project_row(row: dict, fields: dict) -> dict:
    result = {}
    for source, target in fields.items():
        value = row[source]
        if isinstance(value, Decimal):
            if value != value.to_integral_value():
                raise RecoveryError("Functional catalog identifier is not an integer")
            value = int(value)
        result[target] = value
    return result


def extract_catalog(path: Path) -> dict:
    dump = read_dump(path)
    menus = [project_row(row, MENU_FIELDS) for row in dump.tables["UM_MENU"].iter_rows()]
    types = [project_row(row, TYPE_FIELDS) for row in dump.tables["ENTRY_TYPE"].iter_rows()]
    by_id = {menu["menu_id"]: menu for menu in menus}
    if len(by_id) != len(menus):
        raise RecoveryError("Duplicate catalog menu identifier")
    for menu in menus:
        seen = {menu["menu_id"]}
        parent = menu["parent_id"]
        while parent is not None:
            if parent not in by_id or parent in seen:
                raise RecoveryError("Menu catalog contains a missing parent or cycle")
            seen.add(parent)
            parent = by_id[parent]["parent_id"]
    menus.sort(key=lambda row: row["menu_id"])
    types.sort(key=lambda row: row["type_code"])
    report_modules = [{key: menu[key] for key in ("menu_id", "module_name", "file_name")}
                      for menu in menus if menu["module_type"] == "R" and menu["file_name"]]
    return {
        "format_version": 1,
        "source": {"name": path.name, "sha256": dump.sha256, "size_bytes": dump.source_bytes,
                   "export_version": "V10.02.01", "tables_decoded": ["UM_MENU", "ENTRY_TYPE"]},
        "scope": "Functional definitions only; no user, company, account, contact or credential values",
        "summary": {"menus": len(menus), "entry_types": len(types),
                    "visible_flag_Y": sum(menu["visibility_flag"] == "Y" for menu in menus),
                    "other_visibility_flags": sum(menu["visibility_flag"] != "Y" for menu in menus),
                    "nodes_without_file": sum(not menu["file_name"] for menu in menus),
                    "module_file_tokens": sum(bool(menu["file_name"]) for menu in menus),
                    "declared_report_modules": len(report_modules)},
        "menus": menus, "entry_types": types, "declared_report_modules": report_modules,
        "limitations": [
            "Visibility is the catalog flag; effective user access was not extracted.",
            "Menu ID order is deterministic presentation, not proved original user-specific sort order.",
            "File names are tokens without extensions, directories, or recovered form/report contents.",
            "Uom Coding is declared R under Inventory Reports despite its coding label and menu address.",
            "Delivery challan, bank payment and bank receipt menus are absent from this catalog.",
            "A type or schema definition does not prove that its workflow was exposed in this application's menu.",
        ],
    }


def catalog_markdown(catalog: dict) -> str:
    menus = catalog["menus"]
    lines = [
        "# Recovered legacy functional catalog", "",
        "Reviewed 2026-10-02. This catalog contains only `UM_MENU` and `ENTRY_TYPE`",
        "functional definitions decoded from the supplied Oracle export. No user,",
        "account, company, contact, permission-grant or credential records were decoded", "for this catalog.", "",
        f"Source: `{catalog['source']['name']}` ({catalog['source']['size_bytes']:,} bytes).", "",
        f"SHA-256: `{catalog['source']['sha256']}`.", "",
        "The strict decoder pins that source fingerprint, preserves scalar bytes by",
        "round-trip validation, and checks the table terminators. The catalog extractor",
        "also validates parent references and rejects cycles. JSON preserves the original",
        "labels, spelling, flags and file tokens in [`legacy_menus.json`](legacy_menus.json).", "",
        "## Visibility and scope", "",
        f"There are **{len(menus)} menu definitions**, all with `IS_VISIBLE='Y'`, and",
        f"**{catalog['summary']['module_file_tokens']} module file tokens**. The other",
        f"{catalog['summary']['nodes_without_file']} entries are the root and containers.",
        "All entries have `APPLICATION='FMS'` and `IS_ROLE='N'`; `CATE` is null.",
        "These are catalog properties. Actual user visibility and authorization can",
        "also depend on user rights, which this extraction intentionally does not read.", "",
        "The hierarchy below follows `PARENT_NODE`. Siblings appear by `MENU_ID` for",
        "repeatable presentation; the original per-user sort order is not established.",
        "Original spelling is retained, including `Cinfiguration`, `Finalcial`,",
        "`Inventiry` and `Puchase`. A modern UI may correct these labels while retaining",
        "the represented features.", "", "## Menu hierarchy", "", "```text",
    ]
    def append_children(parent, depth):
        for menu in menus:
            if menu["parent_id"] == parent:
                token = f" -> {menu['file_name']}" if menu["file_name"] else ""
                lines.append(f"{'  ' * depth}[{menu['menu_id']}] {menu['module_name']}{token}")
                append_children(menu["menu_id"], depth + 1)
    append_children(None, 0)
    lines += ["```", "", "## Entry types", "",
              "These labels and numbers are confirmed by `ENTRY_TYPE` rows, rather than",
              "inferred from view expressions.", "",
              "| Type code | Short label | Original description |", "| ---: | --- | --- |"]
    for item in catalog["entry_types"]:
        lines.append(f"| {item['type_code']} | `{item['type_desc']}` | {item['description']} |")
    lines += ["", "Codes 4 and 5 exist for bank payment/receipt, but no corresponding",
              "menu entries occur in this catalog. Code 6 has an explicit Opening Balance",
              "menu. Code 8 has separate Sale Credit and Counter Sale modules. The specific",
              "header/line values written by each form still need application evidence.", "",
              "## Declared report modules", "",
              "These entries have `MODULE_TYPE='R'` and a non-null `FILE_NAME`. The tokens",
              "identify declared report modules, not recovered report templates. No file",
              "extension, template contents, dimensions, printer settings or parameter",
              "dialog layout is stored in these catalog rows.", "",
              "| Menu ID | Original label | File token |", "| ---: | --- | --- |"]
    for item in catalog["declared_report_modules"]:
        lines.append(f"| {item['menu_id']} | {item['module_name']} | `{item['file_name']}` |")
    lines += ["", "Menu 44 is an anomaly: `* Uom Coding` is classified `R`, placed under",
              "Inventory Reports, and addressed as `INVENTORY_CODING_MENU.UOM`. Preserve",
              "its source metadata; do not infer from that flag alone that the replacement",
              "should turn unit maintenance into a report.", "", "## External assets and parity gaps", "",
              "`MENU_ADDRESS` values are symbolic dotted menu references, not filesystem",
              "paths. `FILE_NAME` values are bare module tokens. This extraction does not",
              "resolve them against `LOCATION` or inspect any machine-specific path data.",
              "No `.fmb`, `.fmx`, `.rdf`, `.rep`, `.pll`, `.mmb` or `.mmx` files were found",
              "in the workspace during this review. The original application may require",
              "such files or other runtime assets, but their actual names, extensions and",
              "directories cannot be recovered from these two tables.", "",
              "The catalog confirms the following scope that the replacement must address:", "",
              "- Company configuration, user/catalog administration and per-user rights.",
              "- City definitions, three chart-of-account levels, item groups/subgroups,",
              "  item definitions, opening stock and unit coding.",
              "- Opening balances, cash payments/receipts and journal vouchers.",
              "- Purchases, credit sales, counter sales, standard purchase/sales returns,",
              "  breakage receive and breakage dispatch.",
              "- Voucher printing, cash statements, account ledgers, trial/sub-trial",
              "  balances, profit and loss, city recovery, stock summary/value, item",
              "  ledger and purchase/sale return detail reports.", "",
              "Breakage naming is now confirmed: type 11 is Breakage Receive (`BRR`) and",
              "type 12 is Breakage Dispatch (`BRD`), exposed by `BRCV` and `BDISP` modules.",
              "The schema's `BRMST`/`BRDTL` and `BDMST`/`BDDTL` naming is consistent with",
              "those features, but a direct table-to-form mapping remains an inference",
              "until original form code or a reference transaction confirms it. The view",
              "labels 'Breakage Sale Return' and 'Purchase Return Invoice' are historical",
              "SQL narration, not the canonical menu names.", "",
              "Delivery-challan schema/view support exists, but no delivery-challan menu",
              "entry appears among these 46 definitions. Bank vouchers have type codes but",
              "no explicit menus. This does not establish whether they were unused, invoked",
              "indirectly or provided by an external module. Likewise, the catalog names",
              "reports without proving their filters, formulas, layouts or launch behavior.", "",
              "The original application/forms and representative reports are still needed",
              "to verify these behaviors. See [`LEGACY_ANALYSIS.md`](LEGACY_ANALYSIS.md)",
              "for recovered stock/accounting semantics and known inconsistencies.", "",
              "## Reproduction", "", "```sh",
              "python3 tools/inspect_legacy_menus.py BT-270926.EXE --output-dir docs",
              "python3 tools/inspect_legacy_menus.py BT-270926.EXE --output-dir docs --check", "```", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, default=Path("BT-270926.EXE"))
    parser.add_argument("--output-dir", type=Path, default=Path("docs"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        catalog = extract_catalog(args.source)
        artifacts = {"legacy_menus.json": json.dumps(catalog, ensure_ascii=True, indent=2) + "\n",
                     "LEGACY_MENUS.md": catalog_markdown(catalog)}
        for name in artifacts:
            if (args.output_dir / name).resolve() == args.source.resolve():
                raise RecoveryError("Output path would overwrite the source")
        if args.check:
            for name, contents in artifacts.items():
                if (args.output_dir / name).read_text(encoding="utf-8") != contents:
                    raise RecoveryError("Functional catalog artifact is stale")
        else:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            for name, contents in artifacts.items():
                (args.output_dir / name).write_text(contents, encoding="utf-8")
        print(json.dumps(catalog["summary"], sort_keys=True))
        return 0
    except (OSError, RecoveryError) as error:
        print(f"Functional catalog extraction failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
