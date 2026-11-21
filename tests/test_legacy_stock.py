"""Synthetic Oracle-shaped TEXT archives; no real business records are used."""

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, Inexact, localcontext
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from bifma.database import session
from bifma.legacy_stock import AmbiguousPurchaseRate, LegacyStock, LegacyStockError


class LegacyStockTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "history.sqlite3"
        self.connection = sqlite3.connect(self.path)
        self.addCleanup(self.connection.close)
        tables = {
            "PUR_ITEM": "ICODE INAME UOM_ID_LARGE UOM_ID_SMALL QTY_SMALL_UOM PUR_CODE",
            "OPEN_STOCK": "TRNID ICODE OPEN_QTY OPEN_RATE OPEN_DATE STORE_ID CMP_ID",
            "PMST": "TRNID TRNNO TRNDATE TRN_TYPE CMP_ID CHCODE",
            "PDTL": "TRNID VNO STORE_ID ICODE QTY RATE",
            "SMST": "TRNID TRNNO TRNDATE TRN_TYPE CMP_ID CHCODE",
            "SDTL": "TRNID VNO STORE_ID ICODE QTY RATE VNO_DCDTL",
            "BRMST": "TRNID STORE_ID TRNNO TRNDATE CMP_ID CHCODE",
            "BRDTL": "TRNID VNO ICODE QTY RATE",
            "BDMST": "TRNID TRNNO TRNDATE CMP_ID CHCODE",
            "BDDTL": "TRNID VNO STORE_ID ICODE QTY RATE",
        }
        for table, columns in tables.items():
            self.connection.execute(f"CREATE TABLE {table} ({','.join(column + ' TEXT' for column in columns.split())})")
        self.insert("PUR_ITEM", ICODE="A", INAME="Synthetic item", UOM_ID_LARGE="2", UOM_ID_SMALL="1", QTY_SMALL_UOM="12", PUR_CODE="0501")
        self.report = LegacyStock(self.path)

    def insert(self, table, **values):
        # Table/column identifiers here are fixed test code, never external input.
        self.connection.execute(
            f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
            [str(value) if value is not None else None for value in values.values()],
        )
        self.connection.commit()

    def opening(self, identifier="1", when="2025-01-01T00:00:00", quantity="10", rate="2", store="1", company="1", product="A"):
        self.insert("OPEN_STOCK", TRNID=identifier, ICODE=product, OPEN_QTY=quantity, OPEN_RATE=rate, OPEN_DATE=when, STORE_ID=store, CMP_ID=company)

    def purchase(self, identifier="20", line="20", when="2025-01-02T00:00:00", quantity="3", rate="5", store="1", company="1", kind="7", product="A"):
        self.insert("PMST", TRNID=identifier, TRNNO=identifier, TRNDATE=when, TRN_TYPE=kind, CMP_ID=company, CHCODE="SUPPLIER")
        self.insert("PDTL", TRNID=identifier, VNO=line, STORE_ID=store, ICODE=product, QTY=quantity, RATE=rate)

    def sale(self, identifier="30", line="30", when="2025-01-03T00:00:00", quantity="2", store="1", company="1", kind="8", challan=None, product="A"):
        self.insert("SMST", TRNID=identifier, TRNNO=identifier, TRNDATE=when, TRN_TYPE=kind, CMP_ID=company, CHCODE="CUSTOMER")
        self.insert("SDTL", TRNID=identifier, VNO=line, STORE_ID=store, ICODE=product, QTY=quantity, RATE="999", VNO_DCDTL=challan)

    def test_regular_directions_and_valuation_use_recovered_formulas(self):
        self.opening()
        self.purchase()
        self.sale()
        self.purchase(identifier="21", line="21", when="2025-01-04T00:00:00", kind="10", quantity="1", rate="99")
        self.sale(identifier="31", line="31", when="2025-01-05T00:00:00", kind="9", quantity="1")
        rows = self.report.movements("1")
        self.assertEqual([row.tag for row in rows], ["OP", "PV", "SV", "SR", "PR"])
        self.assertEqual([row.rate for row in rows], [Decimal(2), Decimal(5), Decimal(5), Decimal(5), Decimal(5)])
        balance = self.report.balances("1")[0]
        self.assertEqual((balance.quantity_in, balance.quantity_out, balance.closing_quantity), (14, 3, 11))
        self.assertEqual((balance.value_in, balance.value_out, balance.closing_value), (40, 15, 25))
        self.assertEqual(balance.opening_quantity, 0)
        self.assertIn("unverified", self.report.compatibility)

    def test_as_of_and_period_opening_include_open_stock_and_whole_end_day(self):
        self.opening()
        self.purchase(when="2025-01-02T23:59:59")
        self.sale()
        rows = self.report.movements("1", date_from="2025-01-02", date_to=date(2025, 1, 2))
        self.assertEqual([row.tag for row in rows], ["PV"])
        balance = self.report.balances("1", date_from="2025-01-02", date_to="2025-01-02")[0]
        self.assertEqual((balance.opening_quantity, balance.opening_value), (10, 20))
        self.assertEqual((balance.quantity_in, balance.quantity_out, balance.closing_quantity, balance.closing_value), (3, 0, 13, 35))
        exact = self.report.movements("1", date_to="2025-01-02T23:59:58")
        self.assertEqual([row.tag for row in exact], ["OP"])
        self.assertEqual(self.report.movements("1", date_to="2024-12-31"), [])

    def test_company_store_product_filters_do_not_limit_rate_to_store(self):
        self.opening(company="2", identifier="999", rate="999")
        self.purchase(store="2", rate="7")
        self.sale(store="1")
        rows = self.report.movements(Decimal("1.0"), store_id=1, product_code="A")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].tag, rows[0].rate), ("SV", Decimal(7)))
        self.assertEqual(self.report.movements("1", product_code="A' OR 1=1 --"), [])
        self.assertEqual(self.report.balances("2")[0].closing_quantity, 10)

    def test_rate_uses_numeric_maximum_id_not_latest_date_or_lexicographic_max(self):
        self.purchase(identifier="1", line="100", when="2025-01-01T00:00:00", rate="4")
        self.purchase(identifier="2", line="9", when="2025-01-02T00:00:00", rate="8")
        self.purchase(identifier="3", line="200", when="2025-01-04T00:00:00", rate="12")
        self.assertEqual(self.report.purchase_rate("1", "A", "2025-01-03"), 4)
        self.assertEqual(self.report.purchase_rate("1", "A", "2025-01-04"), 12)

    def test_return_rate_fallback_and_zero_when_no_rate_exists(self):
        self.purchase(kind="10", rate="6")
        self.assertEqual(self.report.purchase_rate("1", "A", "2025-01-01"), 0)
        self.assertEqual(self.report.purchase_rate("1", "A", "2025-01-02"), 6)
        # An eligible opening row wins over a later/higher fallback identifier,
        # even when its quantity is zero and its null rate NVLs to zero.
        self.opening(quantity="0", rate=None)
        self.assertEqual(self.report.purchase_rate("1", "A", "2025-01-02"), 0)

    def test_duplicate_highest_id_raises_even_when_rates_match(self):
        self.opening(identifier="20", rate="5")
        self.purchase(line="20", rate="5")
        with self.assertRaises(AmbiguousPurchaseRate):
            self.report.purchase_rate("1", "A", "2025-01-02")
        self.assertEqual(self.report.purchase_rate("1", "A", "2025-01-01"), 5)
        with self.assertRaises(AmbiguousPurchaseRate):
            self.report.movements("1")

    def test_ambiguous_fallback_and_master_join_duplicates_are_not_guessed(self):
        self.purchase(identifier="1", line="10", kind="10")
        self.purchase(identifier="2", line="10", kind="10")
        with self.assertRaises(AmbiguousPurchaseRate):
            self.report.purchase_rate("1", "A", "2025-01-02")
        self.opening(identifier="99")
        self.assertEqual(self.report.purchase_rate("1", "A", "2025-01-02"), 2)
        self.insert("PUR_ITEM", ICODE="A", INAME="Duplicate", UOM_ID_LARGE="2", UOM_ID_SMALL="1", QTY_SMALL_UOM="12", PUR_CODE="0501")
        with self.assertRaises(AmbiguousPurchaseRate):
            self.report.purchase_rate("1", "A", "2025-01-02")

    def test_null_ids_do_not_participate_in_maximum(self):
        self.opening(identifier=None, rate="999")
        self.purchase(kind="10", rate="6")
        self.assertEqual(self.report.purchase_rate("1", "A", "2025-01-02"), 6)

    def test_delivery_challan_linked_sales_and_nonpositive_quantities_are_excluded(self):
        self.opening(quantity="-1")
        self.purchase(quantity="0")
        self.sale(challan="0")
        self.sale(identifier="31", line="31", quantity=None)
        self.sale(identifier="32", line="32", quantity="0.25")
        self.assertEqual([row.line_id for row in self.report.movements("1")], ["32"])

    def test_unrecognized_transaction_type_keeps_null_tag_like_oracle_decode(self):
        self.purchase(kind="42")
        row = self.report.movements("1")[0]
        self.assertIsNone(row.tag)
        self.assertEqual(row.quantity_in, 3)
        self.assertEqual(row.rate, 0)

    def test_both_breakage_arms_are_incoming_and_use_source_store_and_rate(self):
        self.insert("BRMST", TRNID="1", STORE_ID="2", TRNNO="1", TRNDATE="2025-01-01T00:00:00", CMP_ID="1", CHCODE="C")
        self.insert("BRDTL", TRNID="1", VNO="1", ICODE="A", QTY="2", RATE="3")
        self.insert("BDMST", TRNID="2", TRNNO="2", TRNDATE="2025-01-02T00:00:00", CMP_ID="1", CHCODE="S")
        self.insert("BDDTL", TRNID="2", VNO="2", STORE_ID="3", ICODE="A", QTY="4", RATE="5")
        rows = self.report.movements("1", stream="breakage")
        self.assertEqual([(row.tag, row.store_id, row.quantity_in, row.quantity_out, row.value_in) for row in rows], [("BR", "2", 2, 0, 6), ("BD", "3", 4, 0, 20)])
        self.assertEqual(len(self.report.balances("1", stream="breakage")), 2)
        self.assertEqual(self.report.movements("1"), [])

    def test_nullable_opening_rate_remains_null_but_value_is_zero(self):
        self.opening(rate=None)
        row = self.report.movements("1")[0]
        self.assertIsNone(row.rate)
        self.assertEqual(row.value_in, 0)

    def test_historical_precision_survives_multiplication_summing_and_host_context(self):
        quantity = "123456789012345678901234567890.123456789"
        rate = "0.12345678901234567890123456789012345678"
        self.opening(quantity=quantity, rate=rate)
        self.opening(identifier="2", quantity="0.00000000000000000000000000000000000001", rate="1")
        with localcontext() as context:
            context.prec = 150
            expected_value = Decimal(quantity) * Decimal(rate) + Decimal("1e-38")
            expected_quantity = Decimal(quantity) + Decimal("1e-38")
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            balance = self.report.balances("1")[0]
        self.assertEqual(balance.closing_quantity, expected_quantity)
        self.assertEqual(balance.closing_value, expected_value)

    def test_entire_oracle_number_exponent_span_survives_valuation(self):
        self.opening(quantity="1e125", rate="1e125")
        self.opening(identifier="2", quantity="1e-130", rate="1e-130")
        with localcontext() as context:
            context.prec = 600
            expected_quantity = Decimal("1e125") + Decimal("1e-130")
            expected_value = Decimal("1e250") + Decimal("1e-260")
        balance = self.report.balances("1")[0]
        self.assertEqual(balance.closing_quantity, expected_quantity)
        self.assertEqual(balance.closing_value, expected_value)

    def test_reads_use_one_readonly_snapshot_and_leave_file_unchanged(self):
        self.opening()
        self.purchase()
        self.sale()
        before = hashlib.sha256(self.path.read_bytes()).digest()
        calls, statements = [], []

        @contextmanager
        def observed(path, **kwargs):
            calls.append(kwargs)
            with session(path, **kwargs) as connection:
                connection.set_trace_callback(statements.append)
                yield connection

        with patch("bifma.legacy_stock.session", observed):
            self.report.balances("1")
        self.assertEqual(calls, [{}])
        self.assertEqual(sum(statement == "BEGIN" for statement in statements), 1)
        self.assertLessEqual(sum(statement.startswith("SELECT") for statement in statements), 6)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), before)

    def test_invalid_filters_and_corrupt_numeric_history_fail_explicitly(self):
        cases = ({"stream": "other"}, {"date_from": "2025-02-02", "date_to": "2025-02-01"}, {"date_to": "bad"}, {"date_to": datetime.now(timezone.utc)}, {"date_to": "2025-01-01T00:00:00.1"}, {"store_id": 1.0}, {"product_code": 1})
        for filters in cases:
            with self.subTest(filters=filters), self.assertRaises(LegacyStockError):
                self.report.movements("1", **filters)
        self.opening(quantity="NaN")
        with self.assertRaises(LegacyStockError):
            self.report.movements("1")

    def test_missing_database_is_never_created(self):
        missing = Path(self.directory.name) / "missing.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            LegacyStock(missing).movements("1")
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
