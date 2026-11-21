"""Recover this Oracle backup into a NEW, explicitly unverified SQLite archive.

Run from the project root: python3 -m tools.stage_recovery --help
This is migration evidence, not the operational application's database schema.
Never execute source SQL or silently discard/repair conflicting historical rows.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from bifma.database import new_destination, publish_new_file, require_integrity
from tools.recovery_oracle import read_dump


def identifier(name):
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
        raise ValueError("Unexpected source schema identifier")
    return '"' + name + '"'


def scalar_text(value):
    """Lossless numeric value serialization, independent of Decimal context."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Nonfinite recovered number")
        if value.is_zero():
            return "0"
        result = format(value, "f")
        return result.rstrip("0").rstrip(".") if "." in result else result
    if value is None or isinstance(value, str):
        return value
    raise TypeError("Unexpected recovered scalar type")


def hash_row(digest, values):
    # JSON's array boundaries/null/string typing prevent ambiguous concatenation.
    digest.update(json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
    digest.update(b"\n")


def check_source_constraints(connection, schema):
    """Audit Oracle constraints without deleting/rejecting historical rows.

    Oracle compound UNIQUE permits multiple all-null keys, but duplicate keys
    with only some null columns still conflict. See Oracle Database Concepts,
    Data Integrity; ordinary SQLite UNIQUE has different null semantics.
    """
    results = []
    for table in schema["tables"]:
        table_name = identifier(table["name"])
        for column in table["columns"]:
            if not column["nullable"]:
                count = connection.execute(
                    f"SELECT COUNT(*) FROM {table_name} WHERE {identifier(column['name'])} IS NULL"
                ).fetchone()[0]
                results.append({"table": table["name"], "kind": "not_null",
                                "columns": [column["name"]], "violations": count})
        keys = [("primary_key", table["primary_key"])] if table["primary_key"] else []
        keys += [("unique", columns) for columns in table["unique_constraints"]]
        for kind, columns in keys:
            grouped = ",".join(identifier(column) for column in columns)
            any_present = " OR ".join(f"{identifier(column)} IS NOT NULL" for column in columns)
            duplicates = connection.execute(
                f"SELECT COALESCE(SUM(n-1),0) FROM (SELECT COUNT(*) AS n FROM {table_name} "
                f"WHERE {any_present} GROUP BY {grouped} HAVING COUNT(*)>1)"
            ).fetchone()[0]
            nulls = 0
            if kind == "primary_key":
                any_null = " OR ".join(f"{identifier(column)} IS NULL" for column in columns)
                nulls = connection.execute(
                    f"SELECT COUNT(*) FROM {table_name} WHERE {any_null}"
                ).fetchone()[0]
            results.append({"table": table["name"], "kind": kind,
                            "columns": columns, "violations": duplicates + nulls})
        for key in table["foreign_keys"]:
            present = " AND ".join(f"child.{identifier(column)} IS NOT NULL" for column in key["columns"])
            matches = " AND ".join(
                f"child.{identifier(child)}=parent.{identifier(parent)}"
                for child, parent in zip(key["columns"], key["references_columns"])
            )
            count = connection.execute(
                f"SELECT COUNT(*) FROM {table_name} child WHERE {present} AND NOT EXISTS "
                f"(SELECT 1 FROM {identifier(key['references_table'])} parent WHERE {matches})"
            ).fetchone()[0]
            results.append({"table": table["name"], "kind": "foreign_key",
                            "columns": key["columns"], "references_table": key["references_table"],
                            "violations": count})
    return results


def stage_dump(dump, schema, destination):
    destination = new_destination(destination)
    if dump.sha256 != schema["source"]["sha256"]:
        raise ValueError("Schema fingerprint does not match source backup")
    if set(dump.tables) != {table["name"] for table in schema["tables"]}:
        raise ValueError("Source/schema table set mismatch")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".bifma-recovery-", suffix=".sqlite3", dir=destination.parent,
    )
    os.close(descriptor)  # mkstemp creates a private file (0600 on POSIX).
    temporary = Path(temporary_name)
    connection = None
    report = {"format_version": 1, "status": "recovered_unverified",
              "source_sha256": dump.sha256,
              "created_utc": datetime.now(timezone.utc).isoformat(),
              "storage": "Oracle NUMBER stored as canonical decimal TEXT; DATE as ISO TEXT",
              "oracle_reconciled": False, "tables": []}
    try:
        connection = sqlite3.connect(temporary, isolation_level=None)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        for table in schema["tables"]:
            source = dump.tables[table["name"]]
            names = [column["name"] for column in table["columns"]]
            expected = [(c["name"], c["oracle_type"], c["nullable"]) for c in table["columns"]]
            actual = [(c.name, c.oracle_type, c.nullable) for c in source.columns]
            if expected != actual:
                raise ValueError(f"{table['name']}: source/schema column mismatch")
            # TEXT affinity is essential: SQLite NUMERIC/REAL would round large
            # Oracle numbers. Do not apply defaults/constraints to an archive;
            # preserve everything and audit against the original declarations.
            definition = ",".join(f"{identifier(name)} TEXT" for name in names)
            connection.execute(f"CREATE TABLE {identifier(table['name'])} ({definition})")
            placeholders = ",".join("?" for _ in names)
            statement = f"INSERT INTO {identifier(table['name'])} VALUES ({placeholders})"
            count = 0
            digest = hashlib.sha256()
            for row in source.iter_rows():
                values = [scalar_text(row[name]) for name in names]
                connection.execute(statement, values)
                hash_row(digest, values)
                count += 1
            persisted_digest = hashlib.sha256()
            persisted_count = 0
            for row in connection.execute(f"SELECT * FROM {identifier(table['name'])} ORDER BY rowid"):
                hash_row(persisted_digest, list(row))
                persisted_count += 1
            if persisted_count != count or persisted_digest.digest() != digest.digest():
                raise ValueError(f"{table['name']}: SQLite content reconciliation failed")
            report["tables"].append({"name": table["name"], "rows": count,
                                     "content_sha256": digest.hexdigest(), "sqlite_roundtrip": True})
            # Nonunique indexes accelerate audits while preserving duplicates.
            keys = [table["primary_key"]] + table["unique_constraints"]
            for number, columns in enumerate(keys):
                if columns:
                    columns_sql = ",".join(identifier(name) for name in columns)
                    index = identifier(f"RECOVERY_{table['name']}_{number}")
                    connection.execute(f"CREATE INDEX {index} ON {identifier(table['name'])} ({columns_sql})")
        report["source_constraints"] = check_source_constraints(connection, schema)
        report["constraint_violations"] = sum(c["violations"] for c in report["source_constraints"])
        report["total_rows"] = sum(t["rows"] for t in report["tables"])
        connection.execute("CREATE TABLE _recovery_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO _recovery_metadata VALUES (?,?)", ("report", json.dumps(report)))
        connection.execute("INSERT INTO _recovery_metadata VALUES (?,?)", ("source_schema", json.dumps(schema)))
        connection.commit()
        connection.close()
        require_integrity(temporary)
        publish_new_file(temporary, destination)
        return report
    finally:
        if connection is not None:
            connection.close()
        temporary.unlink(missing_ok=True)
        Path(str(temporary) + "-journal").unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("BT-270926.EXE"))
    parser.add_argument("--schema", type=Path, default=Path("docs/legacy_schema.json"))
    parser.add_argument("--output", type=Path, required=True, help="New staging file in an existing directory")
    args = parser.parse_args()
    try:
        schema = json.loads(args.schema.read_text(encoding="utf-8"))
        report = stage_dump(read_dump(args.source), schema, args.output)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Recovery failed: {error}\n")
    # Counts/status only; the archive contains the full reconciliation manifest.
    print(json.dumps({"output": str(args.output), "status": report["status"],
                      "tables": len(report["tables"]), "rows": report["total_rows"],
                      "constraint_violations": report["constraint_violations"],
                      "oracle_reconciled": report["oracle_reconciled"]}, indent=2))


if __name__ == "__main__":
    main()
