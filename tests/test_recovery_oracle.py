"""Known Oracle scalar vectors and fail-closed recovery framing checks."""

from decimal import Decimal, localcontext
import struct
import unittest

from tools.recovery_oracle import (
    RecoveryError, decode_date, decode_number, encode_date, encode_number,
    parse_dump,
)


def fixture(*, field_payload=None, row_separator=b"\0\0", suffix=None):
    header = b'\x03\x03gEXPORT:V10.02.01\n'
    ddl = (b'CREATE TABLE "SYNTHETIC" ("ID" NUMBER NOT NULL ENABLE, '
           b'"LABEL" VARCHAR2(20), "CREATED" DATE)  PCTFREE 10\n'
           b'INSERT INTO "SYNTHETIC" ("ID", "LABEL", "CREATED") '
           b'VALUES (:1, :2, :3)\n')
    descriptors = struct.pack('<7H', 3, 2, 22, 1, 20, 871, 1)
    descriptors += struct.pack('<2H', 12, 7)
    if field_payload is None:
        field_payload = (b'\x02\0\xc1\x06' + b'\x02\0OK'
                         + b'\x07\0' + bytes([120, 126, 10, 2, 14, 15, 16]))
    if suffix is None:
        suffix = b'\xff\xff\nALTER TABLE "SYNTHETIC" ENABLE ALL TRIGGERS\n'
    return header + ddl + descriptors + b'\0' * 4 + field_payload + row_separator + suffix


class ScalarRecoveryTests(unittest.TestCase):
    def test_known_number_vectors(self):
        vectors = {
            '80': '0', 'c102': '1', 'c106': '5', 'c2050d': '412',
            'bf02': '0.0001', '3e6066': '-5', '3d644e3866': '-123.45',
            'c202182e': '123.45',
        }
        for wire, numeric in vectors.items():
            with self.subTest(numeric=numeric):
                raw = bytes.fromhex(wire)
                self.assertEqual(decode_number(raw), Decimal(numeric))
                self.assertEqual(encode_number(Decimal(numeric)), raw)

    def test_high_precision_ignores_ambient_decimal_context(self):
        value = Decimal('12345678901234567890123456789012345678.12')
        raw = encode_number(value)
        with localcontext() as context:
            context.prec = 3
            self.assertEqual(decode_number(raw), value)
            self.assertEqual(encode_number(value), raw)
        negative = value.copy_negate()
        self.assertEqual(decode_number(encode_number(negative)), negative)

    def test_number_edges(self):
        for value in ('1e-130', '-1e-130', '1e125', '-1e125', '0.01', '-0.01'):
            with self.subTest(value=value):
                number = Decimal(value)
                self.assertEqual(decode_number(encode_number(number)), number)

    def test_rejects_invalid_number(self):
        for raw in (b'', b'\xc1', b'\xc1\x00', b'\xc1\x65', b'\x3e\x60',
                    b'\xc1\x02\x01', b'\xff\x65'):
            with self.subTest(raw=raw.hex()):
                with self.assertRaises(RecoveryError):
                    decode_number(raw)

    def test_date_keeps_time_of_day(self):
        raw = bytes([120, 126, 10, 2, 14, 15, 16])
        self.assertEqual(decode_date(raw), '2026-10-02T13:14:15')
        self.assertEqual(encode_date('2026-10-02T13:14:15'), raw)

    def test_date_range_and_calendar_validation(self):
        for raw in (bytes([120, 126, 2, 30, 1, 1, 1]),
                    bytes([99, 99, 1, 1, 1, 1, 1]),
                    bytes([120, 126, 1, 1, 0, 1, 1])):
            with self.assertRaises(RecoveryError):
                decode_date(raw)


class FramingRecoveryTests(unittest.TestCase):
    def table(self, source):
        return parse_dump(source, require_known_hash=False).tables['SYNTHETIC']

    def test_source_order_and_exact_scalars(self):
        rows = list(self.table(fixture()).iter_rows())
        self.assertEqual(rows, [{'ID': Decimal(5), 'LABEL': 'OK',
                                 'CREATED': '2026-10-02T13:14:15'}])
        self.assertEqual(list(rows[0]), ['ID', 'LABEL', 'CREATED'])

    def test_nulls_and_trailing_spaces_preserved(self):
        fields = b'\x02\0\xc1\x06' + b'\x03\0OK ' + b'\xfe\xff'
        row = next(self.table(fixture(field_payload=fields)).iter_rows())
        self.assertEqual(row['LABEL'], 'OK ')
        self.assertIsNone(row['CREATED'])

    def test_default_requires_reviewed_fingerprint(self):
        with self.assertRaisesRegex(RecoveryError, 'fingerprint'):
            parse_dump(fixture())

    def test_insert_order_mismatch_rejected(self):
        source = fixture().replace(b'VALUES (:1, :2, :3)', b'VALUES (:2, :1, :3)')
        with self.assertRaisesRegex(RecoveryError, 'bind order'):
            self.table(source)

    def test_not_null_violation_rejected(self):
        fields = b'\xfe\xff' * 3
        with self.assertRaisesRegex(RecoveryError, 'NULL violates'):
            list(self.table(fixture(field_payload=fields)).iter_rows())

    def test_bad_separator_rejected(self):
        with self.assertRaisesRegex(RecoveryError, 'row separator'):
            list(self.table(fixture(row_separator=b'\x01\0')).iter_rows())

    def test_truncated_field_rejected(self):
        with self.assertRaisesRegex(RecoveryError, 'truncated'):
            list(self.table(fixture(field_payload=b'\x16\0\xc1',
                                    row_separator=b'', suffix=b'')).iter_rows())

    def test_bad_terminator_rejected_after_all_rows(self):
        with self.assertRaisesRegex(RecoveryError, 'terminator'):
            list(self.table(fixture(suffix=b'\xff\xff\nUNSUPPORTED')).iter_rows())

    def test_audit_contains_counts_only(self):
        report = parse_dump(fixture(), require_known_hash=False).audit()
        self.assertEqual(report['row_count'], 1)
        self.assertEqual(report['tables'][0]['scalar_round_trips'], 3)
        self.assertNotIn('OK', str(report))
        self.assertEqual(report['status'], 'structurally_decoded_not_oracle_reconciled')


if __name__ == '__main__':
    unittest.main()
