"""Compare an independent Oracle JSON export with the recovered SQLite archive.

Only aggregate counts, schema differences, and multiset digests leave this module.
No decoder or business-calculation code participates in the Oracle comparison.
"""

from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sqlite3


class ComparisonError(ValueError):
    pass


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", value):
        raise ComparisonError("Unexpected schema identifier")
    return '"' + value + '"'


def canonical_number(value):
    if not isinstance(value, str) or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?", value):
        raise ComparisonError("A numeric export value was not a string")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ComparisonError("An exported number was invalid") from None
    if not number.is_finite() or (number and not -130 <= number.adjusted() <= 125):
        raise ComparisonError("An exported number is outside the Oracle NUMBER range")
    if number.is_zero():
        return "0"
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def canonical_row(values, columns):
    if not isinstance(values, (list, tuple)) or len(values) != len(columns):
        raise ComparisonError("An exported row has the wrong column count")
    result = []
    for value, column in zip(values, columns):
        if value is None:
            if not column["nullable"]:
                raise ComparisonError("An exported NULL violates source nullability")
            result.append(None)
            continue
        if not isinstance(value, str):
            raise ComparisonError("An exported scalar has an unexpected JSON type")
        oracle_type = column["oracle_type"]
        if oracle_type.startswith("NUMBER"):
            value = canonical_number(value)
        elif oracle_type == "DATE":
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                raise ComparisonError("An exported date is invalid") from None
            if parsed.tzinfo is not None or parsed.isoformat(timespec="seconds") != value:
                raise ComparisonError("An exported date lost its exact timestamp representation")
        result.append(value)
    return result


def read_json_lines(path):
    with Path(path).open("r", encoding="utf-8", errors="strict") as file:
        for number, line in enumerate(file, 1):
            if not line.strip():
                continue
            if len(line) > 1_000_000:
                raise ComparisonError(f"Export line {number} exceeds its supported size")
            try:
                yield json.loads(line)
            except (ValueError, UnicodeError):
                raise ComparisonError(f"Export line {number} is not a complete JSON value") from None


def metadata_integer(value):
    if isinstance(value, bool) or not (isinstance(value, int) or
            isinstance(value, str) and re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value)):
        raise ComparisonError("Oracle metadata contains a non-integer value")
    return int(value)


def row_multiset(rows, columns):
    hashes = Counter()
    for values in rows:
        row = canonical_row(values, columns)
        encoded = json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        hashes[hashlib.sha256(encoded).digest()] += 1
    digest = hashlib.sha256()
    for value, count in sorted(hashes.items()):
        digest.update(value)
        digest.update(count.to_bytes(8, "big"))
    return hashes, digest.hexdigest()


def check_oracle_schema(schema, metadata_path):
    actual = {}
    for row in read_json_lines(metadata_path):
        if not isinstance(row, list) or len(row) != 8:
            raise ComparisonError("Oracle column metadata has an unexpected shape")
        table, name, kind, length, precision, scale, nullable, position = row
        actual.setdefault(table, []).append((name, kind, metadata_integer(length),
                                             metadata_integer(precision) if precision is not None else None,
                                             metadata_integer(scale) if scale is not None else None,
                                             nullable, metadata_integer(position)))
    errors = []
    expected_names = {table["name"] for table in schema["tables"]}
    if set(actual) != expected_names:
        errors.append("Oracle table set differs from source metadata")
    for table in schema["tables"]:
        expected = []
        for index, column in enumerate(table["columns"], 1):
            kind = column["oracle_type"]
            precision = scale = None
            if kind.startswith("NUMBER"):
                match = re.fullmatch(r"NUMBER\((\d+), (\d+)\)", kind)
                if match:
                    precision, scale = map(int, match.groups())
                kind, length = "NUMBER", 22
            elif kind == "DATE":
                length = 7
            else:
                match = re.fullmatch(r"(VARCHAR2|CHAR)\((\d+)\)", kind)
                if not match:
                    raise ComparisonError("Unsupported source column type")
                kind, length = match[1], int(match[2])
            expected.append((column["name"], kind, length, precision, scale,
                             "Y" if column["nullable"] else "N", index))
        if actual.get(table["name"]) != expected:
            errors.append(f"{table['name']}: Oracle column definition/order mismatch")
    return errors


def compare_archive(schema, archive, exports):
    exports = Path(exports)
    schema_errors = check_oracle_schema(schema, exports / "columns.jsonl")
    counts = {}
    for row in read_json_lines(exports / "counts.jsonl"):
        if not isinstance(row, list) or len(row) != 2 or row[0] in counts:
            raise ComparisonError("Oracle count metadata has an unexpected shape")
        if not isinstance(row[1], str) or not re.fullmatch(r"0|[1-9][0-9]*", row[1]):
            raise ComparisonError("Oracle row count is not a canonical nonnegative integer string")
        counts[row[0]] = int(row[1])
    if set(counts) != {table["name"] for table in schema["tables"]}:
        raise ComparisonError("Oracle counts do not cover exactly the source tables")
    uri = Path(archive).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        report_row = connection.execute("SELECT value FROM _recovery_metadata WHERE key='report'").fetchone()
        if report_row is None or json.loads(report_row[0])["source_sha256"] != schema["source"]["sha256"]:
            raise ComparisonError("Archive source fingerprint does not match")
        tables = []
        for table in schema["tables"]:
            name, columns = table["name"], table["columns"]
            selected = ",".join(identifier(column["name"]) for column in columns)
            sqlite_rows = connection.execute(f"SELECT {selected} FROM {identifier(name)}")
            recovered, recovered_digest = row_multiset(sqlite_rows, columns)
            oracle, oracle_digest = row_multiset(read_json_lines(exports / f"{name}.jsonl"), columns)
            sqlite_count, oracle_count = sum(recovered.values()), sum(oracle.values())
            tables.append({"table": name, "columns": len(columns),
                           "oracle_count_query": counts[name], "oracle_export_rows": oracle_count,
                           "sqlite_rows": sqlite_count, "oracle_multiset_sha256": oracle_digest,
                           "sqlite_multiset_sha256": recovered_digest,
                           "only_oracle_rows": sum((oracle - recovered).values()),
                           "only_sqlite_rows": sum((recovered - oracle).values()),
                           "matched": oracle == recovered and oracle_count == counts[name]})
        return {"source_sha256": schema["source"]["sha256"], "schema_errors": schema_errors,
                "table_count": len(tables), "total_rows": sum(table["sqlite_rows"] for table in tables),
                "column_count": sum(table["columns"] for table in tables),
                "field_count": sum(table["sqlite_rows"] * table["columns"] for table in tables),
                "all_fields_match": not schema_errors and all(table["matched"] for table in tables),
                "comparison": "SHA-256 multiset of canonical typed column arrays; duplicates retained",
                "tables": tables}
    finally:
        connection.close()
