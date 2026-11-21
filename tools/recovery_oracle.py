#!/usr/bin/env python3
"""Strict, read-only recovery probe for the supplied original Oracle export.

This is a source-specific investigative decoder, not an Oracle Import replacement.
No SQL from the source is executed. The CLI prints metadata/counts, never row data.
Call read_dump(path).tables[name].iter_rows() for explicitly isolated staging work.
Every decoded scalar is re-encoded and compared with its original bytes.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import struct
from typing import Iterator


KNOWN_SHA256 = "d2cd806aa644320cf6d2eaa6192e7d6c840adcde8226f62b5ad1422980cc7e9e"
KNOWN_BYTES = 7_299_072
Scalar = Decimal | str | None


class RecoveryError(ValueError):
    """Unsupported/corrupt input; messages identify location, never field contents."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RecoveryError(message)


def decode_number(raw: bytes) -> Decimal:
    """Decode documented Oracle base-100 NUMBER without decimal-context rounding."""
    if raw == b"\x80":
        return Decimal(0)
    _require(2 <= len(raw) <= 21, "Unsupported NUMBER length")
    positive = raw[0] >= 128
    exponent = raw[0] - 193 if positive else 62 - raw[0]
    mantissa = raw[1:]
    if not positive and mantissa[-1] == 102:
        mantissa = mantissa[:-1]
    elif not positive:
        _require(len(mantissa) == 20, "Missing negative NUMBER terminator")
    digits = [part - 1 if positive else 101 - part for part in mantissa]
    _require(1 <= len(digits) <= 20, "Unsupported NUMBER precision")
    _require(all(0 <= part <= 99 for part in digits), "Invalid NUMBER digit")
    _require(digits[0] != 0 and digits[-1] != 0, "Noncanonical NUMBER mantissa")
    integer = 0
    for part in digits:
        integer = integer * 100 + part
    # Building from a tuple preserves all digits, even under Decimal's default
    # 28-digit context. Arithmetic through floats or ordinary Decimal sums does not.
    return Decimal((0 if positive else 1, tuple(map(int, str(integer))),
                    2 * (exponent - len(digits) + 1)))


def encode_number(value: Decimal) -> bytes:
    """Independently reconstruct NUMBER using exact rational long division."""
    _require(value.is_finite(), "Nonfinite NUMBER")
    if value.is_zero():
        return b"\x80"
    ratio = abs(Fraction(value))
    exponent = (len(str(ratio.numerator)) - len(str(ratio.denominator))) // 2
    while ratio < Fraction(100) ** exponent:
        exponent -= 1
    while ratio >= Fraction(100) ** (exponent + 1):
        exponent += 1
    remaining = ratio / Fraction(100) ** exponent
    digits: list[int] = []
    while remaining:
        digit = remaining.numerator // remaining.denominator
        digits.append(digit)
        _require(len(digits) <= 20, "NUMBER exceeds supported precision")
        remaining = (remaining - digit) * 100
    if value > 0:
        first = exponent + 193
        _require(128 <= first <= 255, "NUMBER exponent out of range")
        return bytes([first] + [digit + 1 for digit in digits])
    first = 62 - exponent
    _require(0 <= first < 128, "NUMBER exponent out of range")
    return bytes([first] + [101 - digit for digit in digits]
                 + ([102] if len(digits) < 20 else []))


def decode_date(raw: bytes) -> str:
    _require(len(raw) == 7, "Unsupported DATE length")
    year = (raw[0] - 100) * 100 + raw[1] - 100
    _require(raw[0] >= 100 and raw[1] >= 100 and 1 <= year <= 9999,
             "Only AD DATE values are supported")
    try:
        value = datetime(year, raw[2], raw[3], raw[4] - 1,
                         raw[5] - 1, raw[6] - 1)
    except ValueError:
        raise RecoveryError("Invalid Oracle DATE fields") from None
    return value.isoformat(timespec="seconds")


def encode_date(value: str) -> bytes:
    date = datetime.fromisoformat(value)
    return bytes([date.year // 100 + 100, date.year % 100 + 100, date.month,
                  date.day, date.hour + 1, date.minute + 1, date.second + 1])


@dataclass(frozen=True)
class Column:
    name: str
    oracle_type: str
    nullable: bool
    type_code: int
    maximum_bytes: int
    charset_id: int | None = None

    def decode(self, raw: bytes) -> Decimal | str:
        if self.type_code == 2:
            result: Decimal | str = decode_number(raw)
            encoded = encode_number(result)
        elif self.type_code == 12:
            result = decode_date(raw)
            encoded = encode_date(result)
        else:
            # All character data in this specific file is BMP text. Oracle's
            # UTF8 charset also permits CESU-8 supplementary characters, which
            # are deliberately unsupported instead of decoded with replacement.
            try:
                result = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                raise RecoveryError("Unsupported character encoding") from None
            _require(all(ord(c) <= 0xFFFF for c in result),
                     "Supplementary character encoding is unsupported")
            encoded = result.encode("utf-8", errors="strict")
        _require(encoded == raw, "Scalar failed exact byte round-trip")
        return result


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    _source: bytes
    _rows_offset: int
    _boundary: int

    def _u16(self, offset: int) -> int:
        _require(offset + 2 <= self._boundary,
                 f"{self.name}: truncated length at byte {offset}")
        return struct.unpack_from("<H", self._source, offset)[0]

    def iter_rows(self) -> Iterator[dict[str, Scalar]]:
        """Stream source-order rows; raise on any unvalidated byte/value.

        Exhaust the iterator to validate its table terminator. Decimal values
        must remain Decimal or exact strings during staging, never float.
        DATE values are ISO strings including the original time of day.
        """
        offset = self._rows_offset
        row_number = 0
        while self._u16(offset) != 0xFFFF:
            row_number += 1
            row: dict[str, Scalar] = {}
            for column in self.columns:
                size = self._u16(offset)
                offset += 2
                context = f"{self.name} row {row_number} column {column.name}"
                if size == 0xFFFE:
                    _require(column.nullable, f"{context}: NULL violates source schema")
                    row[column.name] = None
                    continue
                _require(0 < size <= column.maximum_bytes,
                         f"{context}: invalid field byte length at {offset - 2}")
                _require(offset + size <= self._boundary,
                         f"{context}: truncated payload at {offset}")
                raw = self._source[offset:offset + size]
                offset += size
                try:
                    row[column.name] = column.decode(raw)
                except RecoveryError as error:
                    raise RecoveryError(f"{context}: {error}") from None
            _require(self._u16(offset) == 0,
                     f"{self.name} row {row_number}: invalid row separator")
            offset += 2
            yield row
        # These are the only two following metadata markers found in this file.
        # Do not scan forward to resynchronize if framing ever fails.
        tail = self._source[offset + 2:offset + 20]
        _require(tail.startswith((b"\nALTER TABLE ", b"\nANALSTATS ")),
                 f"{self.name}: unrecognized table terminator context")


@dataclass(frozen=True)
class RecoveryDump:
    sha256: str
    source_bytes: int
    tables: dict[str, Table]

    def audit(self) -> dict:
        results = []
        for table in self.tables.values():
            rows = nulls = fields = 0
            for row in table.iter_rows():
                rows += 1
                fields += len(row)
                nulls += sum(value is None for value in row.values())
            results.append({"table": table.name, "columns": len(table.columns),
                            "rows": rows, "fields": fields, "nulls": nulls,
                            "scalar_round_trips": fields - nulls})
        return {"source_sha256": self.sha256, "source_bytes": self.source_bytes,
                "known_source": self.sha256 == KNOWN_SHA256,
                "status": "structurally_decoded_not_oracle_reconciled",
                "table_count": len(results),
                "row_count": sum(item["rows"] for item in results),
                "tables": results}


_CREATE = re.compile(rb'CREATE TABLE "([A-Z_][A-Z0-9_]*)" \((.*?)\)  PCTFREE[^\n]*\n')
_COLUMN = re.compile(
    r'"([A-Z_][A-Z0-9_]*)" (NUMBER(?:\(\d+, \d+\))?|DATE|VARCHAR2\(\d+\)|CHAR\(\d+\))'
    r'(?: DEFAULT .+?)?( NOT NULL ENABLE)?(?=, "|$)')


def parse_dump(source: bytes, *, require_known_hash: bool = True) -> RecoveryDump:
    """Parse descriptors; iter_rows/audit performs complete row validation.

    require_known_hash=False exists for synthetic tests and corruption checks.
    It does not imply support for another Oracle export dialect.
    """
    digest = hashlib.sha256(source).hexdigest()
    if require_known_hash:
        _require(digest == KNOWN_SHA256 and len(source) == KNOWN_BYTES,
                 "Source fingerprint differs from the reviewed Oracle export")
    _require(source.startswith(b"\x03\x03gEXPORT:V10.02.01\n"),
             "Unsupported Oracle export header")
    creates = list(_CREATE.finditer(source))
    _require(bool(creates), "No supported CREATE TABLE metadata")
    tables: dict[str, Table] = {}
    for index, create in enumerate(creates):
        name = create[1].decode("ascii")
        _require(name not in tables, "Duplicate CREATE TABLE metadata")
        boundary = creates[index + 1].start() if index + 1 < len(creates) else len(source)
        ddl = create[2].decode("ascii", errors="strict")
        column_matches = list(_COLUMN.finditer(ddl))
        reconstructed = ", ".join(match[0] for match in column_matches)
        _require(reconstructed == ddl, f"{name}: unsupported column definition")
        insert = re.search(rb'INSERT INTO "' + create[1]
                           + rb'" \(([^\n]+)\) VALUES \(([^\n]+)\)\n',
                           source[create.end():boundary])
        _require(insert is not None, f"{name}: missing INSERT descriptor")
        names = tuple(match[1] for match in column_matches)
        expected_columns = ", ".join(f'"{column}"' for column in names).encode("ascii")
        _require(insert[1] == expected_columns, f"{name}: INSERT column order mismatch")
        expected_bindings = ", ".join(f":{i + 1}" for i in range(len(names))).encode("ascii")
        _require(insert[2] == expected_bindings, f"{name}: INSERT bind order mismatch")
        offset = create.end() + insert.end()

        def u16() -> int:
            nonlocal offset
            _require(offset + 2 <= boundary, f"{name}: truncated descriptor")
            value = struct.unpack_from("<H", source, offset)[0]
            offset += 2
            return value

        _require(u16() == len(names), f"{name}: column count mismatch")
        columns = []
        for match in column_matches:
            oracle_type = match[2]
            type_code, maximum = u16(), u16()
            expected_code = 2 if oracle_type.startswith("NUMBER") else (
                12 if oracle_type == "DATE" else (96 if oracle_type.startswith("CHAR") else 1))
            expected_size = 22 if expected_code == 2 else (
                7 if expected_code == 12 else int(re.search(r"\((\d+)\)", oracle_type)[1]))
            _require((type_code, maximum) == (expected_code, expected_size),
                     f"{name}.{match[1]}: descriptor disagrees with schema")
            charset_id = None
            if type_code in (1, 96):
                charset_id, character_form = u16(), u16()
                _require((charset_id, character_form) == (871, 1),
                         f"{name}.{match[1]}: unsupported charset descriptor")
            columns.append(Column(match[1], oracle_type, match[3] is None,
                                  type_code, maximum, charset_id))
        _require(source[offset:offset + 4] == b"\x00" * 4,
                 f"{name}: unsupported row preamble")
        offset += 4
        tables[name] = Table(name, tuple(columns), source, offset, boundary)
    _require(len(tables) == 30 if require_known_hash else True,
             "Reviewed source must contain exactly 30 table payloads")
    return RecoveryDump(digest, len(source), tables)


def read_dump(path: str | Path, *, require_known_hash: bool = True) -> RecoveryDump:
    path = Path(path)
    _require(path.stat().st_size <= 100_000_000, "Dump exceeds probe size limit")
    return parse_dump(path.read_bytes(), require_known_hash=require_known_hash)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", default="BT-270926.EXE")
    args = parser.parse_args()
    try:
        report = read_dump(args.source).audit()
    except (OSError, RecoveryError) as error:
        parser.exit(1, f"Recovery audit failed: {error}\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
