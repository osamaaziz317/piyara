#!/usr/bin/env python3
"""Recover schema metadata from this project's Oracle original-export backup.

This is a deliberately narrow, read-only extractor for the observed export
layout, not an Oracle dump importer. It never decodes rows, optimizer statistics,
account credentials, or application settings. No recovered SQL is executed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys


class SchemaError(ValueError):
    """The source is unsupported, incomplete, or internally inconsistent."""


IDENTIFIER = rb'[A-Z][A-Z0-9_$#]*'


def schema_text(raw: bytes) -> str:
    """Fail closed on non-schema bytes, without including their contents."""
    if any(value < 32 and value not in (9, 10, 13) or value > 126 for value in raw):
        raise SchemaError("Unexpected non-ASCII or control bytes in schema metadata")
    return raw.decode("ascii")


def read_chunk(data: bytes, offset: int) -> tuple[bytes, int]:
    if offset + 2 > len(data):
        raise SchemaError("Truncated metadata length")
    size = struct.unpack_from("<H", data, offset)[0]
    offset += 2
    if offset + size > len(data):
        raise SchemaError("Truncated metadata payload")
    return data[offset:offset + size], offset + size


def read_chunks(data: bytes, offset: int, *, format71: bool = False) -> tuple[str, int]:
    if format71:
        if data[offset:offset + 2] != b"\xfd\xff":
            raise SchemaError("Unsupported FORMAT71 source framing")
        offset += 2
    parts = []
    total = 0
    while True:
        part, offset = read_chunk(data, offset)
        if not part:
            return schema_text(b"".join(parts)), offset
        total += len(part)
        if total > 10 * 1024 * 1024:
            raise SchemaError("Schema source exceeds the extraction limit")
        parts.append(part)


def split_columns(text: str) -> list[str]:
    """Split a column list while retaining commas inside NUMBER(p,s)."""
    pieces = []
    start = depth = 0
    quote = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise SchemaError("Unbalanced column declaration")
        elif char == "," and depth == 0:
            pieces.append(text[start:index].strip())
            start = index + 1
        index += 1
    if quote or depth:
        raise SchemaError("Unclosed column declaration")
    pieces.append(text[start:].strip())
    return pieces


def table_columns(sql: str) -> list[dict]:
    # The observed table DDL ends its column list before PCTFREE. Reject a
    # different layout instead of accidentally treating storage SQL as columns.
    match = re.fullmatch(r'CREATE TABLE "[^"]+" \((.*)\)\s+PCTFREE .*', sql)
    if not match:
        raise SchemaError("Unsupported CREATE TABLE metadata layout")
    columns = []
    for declaration in split_columns(match[1]):
        col = re.fullmatch(
            r'"([^"]+)"\s+(NUMBER(?:\(\d+,\s*\d+\))?|DATE|'
            r'VARCHAR2\(\d+\)|CHAR\(\d+\))( NOT NULL ENABLE)?', declaration,
        )
        if not col:
            raise SchemaError("Unsupported column type or column clause")
        columns.append({"name": col[1], "oracle_type": col[2], "nullable": not bool(col[3])})
    if len({col["name"] for col in columns}) != len(columns):
        raise SchemaError("Duplicate column name")
    return columns


def quoted_identifiers(text: str) -> list[str]:
    names = re.findall(r'"([A-Z][A-Z0-9_$#]*)"', text)
    if not names:
        raise SchemaError("Expected quoted column identifiers")
    return names


def extract_schema(data: bytes, *, source_name: str = "BT-270926.EXE") -> dict:
    header = re.match(rb'\x03\x03.EXPORT:(V\d+\.\d+\.\d+)\n', data)
    if not header:
        raise SchemaError("Expected an Oracle original-export file, not an executable")
    inventory = {
        "format_version": 1,
        "source": {
            "name": Path(source_name).name,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "export_version": header[1].decode("ascii"),
        },
        "summary": {}, "tables": [], "constraints": [], "defaults": [],
        "views": [], "functions": [], "indexes": [], "sequences": [],
    }
    table_by_name = {}
    table_pattern = rb'(?m)^TABLE "(' + IDENTIFIER + rb')"\n(CREATE TABLE "\1" [^\r\n]+)'
    for match in re.finditer(table_pattern, data):
        name = match[1].decode("ascii")
        if name in table_by_name:
            raise SchemaError("Duplicate table definition")
        sql = schema_text(match[2])
        table = {
            "name": name, "offset": match.start(2), "sql": sql,
            "columns": table_columns(sql), "primary_key": [],
            "unique_constraints": [], "foreign_keys": [],
        }
        table_by_name[name] = table
        inventory["tables"].append(table)
    if not table_by_name:
        raise SchemaError("No supported table definitions found")

    # ADD clauses are complete text records. MODIFY DEFAULT is an export
    # instruction whose following length-prefixed payload holds the real DDL.
    alter_pattern = rb'(?m)^ALTER TABLE "(' + IDENTIFIER + rb')" (ADD [^\r\n]+)'
    for match in re.finditer(alter_pattern, data):
        name = match[1].decode("ascii")
        if name not in table_by_name:
            raise SchemaError("Constraint references an unextracted table")
        body = schema_text(match[2])
        key = re.match(r'ADD\s+(PRIMARY KEY|UNIQUE|FOREIGN KEY) \(([^)]+)\)', body)
        if not key:
            raise SchemaError("Unsupported table constraint")
        kind = {"PRIMARY KEY": "primary_key", "UNIQUE": "unique", "FOREIGN KEY": "foreign_key"}[key[1]]
        entry = {"table": name, "kind": kind, "columns": quoted_identifiers(key[2]),
                 "offset": match.start(), "sql": schema_text(match[0])}
        table = table_by_name[name]
        if kind == "primary_key":
            if table["primary_key"]:
                raise SchemaError("Multiple primary keys on one table")
            table["primary_key"] = entry["columns"]
        elif kind == "unique":
            table["unique_constraints"].append(entry["columns"])
        else:
            reference = re.search(r'REFERENCES "([^"]+)" \(([^)]+)\)', body)
            if not reference:
                raise SchemaError("Missing foreign key target")
            entry.update(references_table=reference[1], references_columns=quoted_identifiers(reference[2]))
            table["foreign_keys"].append({field: entry[field] for field in (
                "columns", "references_table", "references_columns")})
        inventory["constraints"].append(entry)

    default_pattern = rb'(?m)^ALTER TABLE "(' + IDENTIFIER + rb')" MODIFY DEFAULT\n'
    for match in re.finditer(default_pattern, data):
        name = match[1].decode("ascii")
        raw, _ = read_chunk(data, match.end())
        sql = schema_text(raw)
        default = re.fullmatch(r'ALTER TABLE "([^"]+)" MODIFY \("([^"]+)" DEFAULT (.*?)\s*\)', sql, re.S)
        if not default or default[1] != name or name not in table_by_name:
            raise SchemaError("Unsupported default metadata")
        columns = {col["name"]: col for col in table_by_name[name]["columns"]}
        if default[2] not in columns:
            raise SchemaError("Default references an unknown column")
        columns[default[2]]["default"] = default[3].strip()
        inventory["defaults"].append({"table": name, "column": default[2],
            "expression": default[3].strip(), "offset": match.end() + 2, "sql": sql})

    view_pattern = rb'(?m)^CREATE VIEW "(' + IDENTIFIER + rb')" \(([^\r\n]+)\) AS *\n'
    for match in re.finditer(view_pattern, data):
        body, _ = read_chunks(data, match.end())
        if not body.lstrip().upper().startswith("SELECT "):
            raise SchemaError("Unsupported view body")
        inventory["views"].append({"name": match[1].decode("ascii"),
            "columns": quoted_identifiers(schema_text(match[2])), "offset": match.start(),
            "sql": schema_text(match[0]) + body})

    function_pattern = rb'(?m)^CREATE FORMAT71 FUNCTION "(' + IDENTIFIER + rb')" *\n'
    for match in re.finditer(function_pattern, data):
        source, _ = read_chunks(data, match.end(), format71=True)
        name = match[1].decode("ascii")
        if not re.match(r"CREATE\s+FUNCTION\s+" + re.escape(name) + r"\b", source, re.I):
            raise SchemaError("Function metadata and source disagree")
        inventory["functions"].append({"name": name, "offset": match.start(), "sql": source})

    # None occur in the supplied export, but preserve simple standalone records
    # if this utility is used on another export with the same framing.
    for keyword, collection in (("INDEX", "indexes"), ("SEQUENCE", "sequences")):
        pattern = rb'(?m)^CREATE (?:UNIQUE )?' + keyword.encode() + rb' "(' + IDENTIFIER + rb')"[^\r\n]+'
        for match in re.finditer(pattern, data):
            inventory[collection].append({"name": match[1].decode("ascii"),
                "offset": match.start(), "sql": schema_text(match[0])})

    for entry in inventory["constraints"]:
        names = {col["name"] for col in table_by_name[entry["table"]]["columns"]}
        if not set(entry["columns"]) <= names:
            raise SchemaError("Constraint references an unknown column")
        if entry["kind"] == "foreign_key":
            target = table_by_name.get(entry["references_table"])
            if target is None or not set(entry["references_columns"]) <= {col["name"] for col in target["columns"]}:
                raise SchemaError("Foreign key references an unextracted table or column")
            if len(entry["columns"]) != len(entry["references_columns"]):
                raise SchemaError("Foreign key column count mismatch")

    summary = {kind: len(inventory[kind]) for kind in (
        "tables", "defaults", "views", "functions", "indexes", "sequences")}
    summary["columns"] = sum(len(table["columns"]) for table in inventory["tables"])
    for kind, title in (("primary_key", "primary_keys"), ("unique", "unique_constraints"), ("foreign_key", "foreign_keys")):
        summary[title] = sum(entry["kind"] == kind for entry in inventory["constraints"])
    inventory["summary"] = summary
    return inventory


def schema_sql(inventory: dict) -> str:
    sections = [
        "-- Recovered Oracle source metadata. REFERENCE ONLY: NOT a SQLite migration.",
        "-- No data rows, account settings, or optimizer statistics are included.",
        f"-- Source: {inventory['source']['name']}",
        f"-- SHA-256: {inventory['source']['sha256']}",
        "-- Export framing was removed; SQL expressions were not corrected.",
    ]
    for collection in ("tables", "defaults", "constraints", "indexes", "sequences", "functions", "views"):
        sections.append(f"\n-- {collection.upper()}")
        for entry in inventory[collection]:
            label = entry.get("name", entry.get("table", ""))
            sections.append(f"\n-- {label}; source byte offset {entry['offset']}")
            sql = entry["sql"].rstrip()
            if collection == "functions":
                sections.append(sql + "\n/")
            else:
                sections.append(sql.rstrip(";") + ";")
    return "\n".join(sections) + "\n"


def artifact_texts(inventory: dict) -> dict[str, str]:
    return {"legacy_schema.json": json.dumps(inventory, indent=2, ensure_ascii=True) + "\n",
            "legacy_schema.sql": schema_sql(inventory)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, default=Path("BT-270926.EXE"))
    parser.add_argument("--output-dir", type=Path, default=Path("docs"))
    parser.add_argument("--check", action="store_true", help="Compare existing artifacts without writing files")
    args = parser.parse_args(argv)
    try:
        inventory = extract_schema(args.source.read_bytes(), source_name=args.source.name)
        artifacts = artifact_texts(inventory)
        for filename in artifacts:
            if (args.output_dir / filename).resolve() == args.source.resolve():
                raise SchemaError("An output path would overwrite the source")
        if args.check:
            for filename, contents in artifacts.items():
                path = args.output_dir / filename
                if not path.exists() or path.read_text(encoding="utf-8") != contents:
                    raise SchemaError(f"Generated artifact is missing or stale: {filename}")
        else:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            for filename, contents in artifacts.items():
                (args.output_dir / filename).write_text(contents, encoding="utf-8")
        print(json.dumps({"source_sha256": inventory["source"]["sha256"],
                          "verified" if args.check else "extracted": inventory["summary"]}, sort_keys=True))
        return 0
    except (OSError, SchemaError) as exc:
        print(f"Schema inspection failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
