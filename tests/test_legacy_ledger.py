"""Synthetic histories exercising source differences, not presumed balancing."""

from contextlib import contextmanager
from decimal import Decimal, Inexact, localcontext
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from bifma.database import session
from bifma.legacy_ledger import LegacyLedger, LegacyLedgerError


class LegacyLedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "synthetic.sqlite3"
        self.connection = sqlite3.connect(self.path)
        self.addCleanup(self.connection.close)
        base = "TRNID TRNNO TRNDATE TRN_TYPE CMP_ID CHCODE BILLNO "
        tables = {
            "PMST": base + "DISCOUNT BREAKAGE FREIGHT P_TYPE",
            "SMST": base + "DISCOUNT BREAKAGE FREIGHT S_TYPE",
            "BDMST": base + "DISCOUNT FREIGHT",
            "BRMST": base + "BREAKAGE FREIGHT",
            "PUR_ITEM": "ICODE PUR_CODE EXP_CODE",
            "JMST": "TRNID TRNNO TRNDATE VTYPE CANCEL CMP_ID",
            "JDTL": "TRNID CHCODE DR CR NARRATION",
            "CONFIG": "CMP_ID CHCODE_CASH",
        }
        for family in ("P", "S", "BD", "BR"):
            tables[family + "DTL"] = "TRNID ICODE QTY RATE DIS_PER"
        self.columns = {table: names.split() for table, names in tables.items()}
        for table, columns in self.columns.items():
            self.connection.execute(f"CREATE TABLE {table} ({','.join(column + ' TEXT' for column in columns)})")
        self.insert("PUR_ITEM", ICODE="A", PUR_CODE="PUR", EXP_CODE="EXP")
        self.insert("CONFIG", CMP_ID="1", CHCODE_CASH="CONFIG_CASH")
        self.report = LegacyLedger(self.path)

    def insert(self, table, **values):
        self.connection.execute(
            f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
            [str(value) if value is not None else None for value in values.values()],
        )
        self.connection.commit()

    def invoice(self, family="S", identifier="1", *, company="1", when="2025-01-02T00:00:00", kind=None, discount=None, breakage=None, freight=None, payment="R", account="PARTY", bill="B1", lines=None):
        values = dict(TRNID=identifier, TRNNO=identifier, TRNDATE=when, TRN_TYPE=kind or {"P": "7", "S": "8", "BD": "9", "BR": "11"}[family], CMP_ID=company, CHCODE=account, BILLNO=bill, DISCOUNT=discount, BREAKAGE=breakage, FREIGHT=freight, P_TYPE=payment, S_TYPE=payment)
        self.insert(family + "MST", **{key: value for key, value in values.items() if key in self.columns[family + "MST"]})
        for item, quantity, rate, percent in (lines if lines is not None else [("A", "1", "100", None)]):
            self.insert(family + "DTL", TRNID=identifier, ICODE=item, QTY=quantity, RATE=rate, DIS_PER=percent)

    def journal(self, identifier="1", *, kind="1", company="1", when="2025-01-01T00:00:00", cancel=None, lines=None):
        self.insert("JMST", TRNID=identifier, TRNNO=identifier, TRNDATE=when, VTYPE=kind, CANCEL=cancel, CMP_ID=company)
        for account, debit, credit, narration in (lines if lines is not None else [("PARTY", "10.25", None, "journal")]):
            self.insert("JDTL", TRNID=identifier, CHCODE=account, DR=debit, CR=credit, NARRATION=narration)

    def arms(self, **filters):
        return {row.source_arm: row for row in self.report.entries("1", **filters)}

    def test_sale_counterpart_omits_header_discount(self):
        self.invoice(discount="10", breakage="2", freight="3", lines=[("A", "1", "100", "10")])
        rows = self.arms()
        self.assertEqual(rows["sale_party"].debit, 81)
        self.assertEqual(rows["sale_product"].credit, 91)
        self.assertEqual(rows["sale_party"].narration, "Sale Invoice#B1 Discount 10")
        self.assertEqual(rows["sale_product"].account_code, "EXP")

    def test_purchase_adjustments_repeat_per_full_product_account_group(self):
        self.insert("PUR_ITEM", ICODE="B", PUR_CODE="PUR", EXP_CODE="OTHER_EXP")
        self.invoice("P", discount="5", breakage="2", freight="3", lines=[("A", "1", "60", None), ("B", "1", "60", None)])
        rows = self.report.entries("1")
        product = [row for row in rows if row.source_arm == "purchase_product"]
        self.assertEqual(len(product), 2)  # EXP_CODE remains a GROUP BY key for purchases.
        self.assertEqual([row.account_code for row in product], ["PUR", "PUR"])
        self.assertEqual([row.debit for row in product], [56, 56])
        self.assertEqual(self.arms()["purchase_party"].credit, 116)

    def test_purchase_return_preserves_opposite_freight_signs_and_ignores_discount(self):
        self.invoice("BD", discount="90", freight="5")
        rows = self.arms()
        self.assertEqual(rows["purchase_return_product"].credit, 95)
        self.assertEqual(rows["purchase_return_party"].debit, 105)
        self.assertEqual(rows["purchase_return_party"].narration, "Purchase Return Invoice#B1 Discount 90")

    def test_breakage_arms_subtract_breakage_and_freight(self):
        self.invoice("BR", breakage="5", freight="7")
        rows = self.arms()
        self.assertEqual(rows["breakage_return_product"].debit, 88)
        self.assertEqual(rows["breakage_return_party"].credit, 88)
        self.assertEqual(rows["breakage_return_party"].narration, "Breakage Sale Return")

    def test_having_is_arm_specific_and_can_retain_zero_or_negative_final_entries(self):
        self.invoice("P", discount="100")
        rows = self.arms()
        self.assertEqual(set(rows), {"purchase_product"})
        self.assertEqual(rows["purchase_product"].debit, 0)
        self.invoice("BR", identifier="2", breakage="100", freight="5")
        rows = self.arms()
        self.assertNotIn("breakage_return_party", rows)
        self.assertEqual(rows["breakage_return_product"].debit, -5)

    def test_two_rounding_stages_and_negative_half_ties(self):
        self.invoice("P", discount="0.5", lines=[("A", "1", "0.5", None)])
        self.assertEqual(self.arms()["purchase_party"].credit, 1)  # ROUND(ROUND(.5)-.5)
        self.invoice("BR", identifier="2", breakage="1", freight="0.5", lines=[("A", "1", "0.5", None)])
        self.assertEqual(self.arms()["breakage_return_product"].debit, -1)  # ROUND(1-1-.5)

    def test_rounded_zero_has_no_negative_sign_like_oracle_number(self):
        self.invoice("BR", breakage="100", freight="0.1")
        row = self.arms()["breakage_return_product"]
        self.assertEqual(row.debit, 0)
        self.assertFalse(row.debit.is_signed())

    def test_subtotals_round_after_summing_and_having_does_not_use_freight(self):
        self.invoice("S", lines=[("A", "1", "0.49", None), ("A", "1", "0.49", None)])
        self.assertEqual(self.arms()["sale_party"].debit, 1)
        self.invoice("S", identifier="2", freight="100", lines=[("A", "1", "0.49", None)])
        self.assertEqual({row.transaction_id for row in self.report.entries("1")}, {"1"})

    def test_product_inner_join_drops_only_product_arm_rows(self):
        self.invoice(lines=[("A", "1", "100", None), ("MISSING", "1", "25", None)])
        rows = self.arms()
        self.assertEqual(rows["sale_party"].debit, 125)
        self.assertEqual(rows["sale_product"].credit, 100)

    def test_duplicate_product_rows_multiply_product_arm_but_not_party(self):
        self.insert("PUR_ITEM", ICODE="A", PUR_CODE="PUR", EXP_CODE="EXP")
        self.invoice()
        self.assertEqual(self.arms()["sale_party"].debit, 100)
        self.assertEqual(self.arms()["sale_product"].credit, 200)

    def test_null_nvl_sum_and_narration_zero_differ_from_null(self):
        self.invoice(lines=[("A", None, "100", None), ("A", "1", None, "50"), ("A", "1", "10", None)])
        rows = self.arms()
        self.assertEqual(rows["sale_party"].debit, 10)
        self.assertEqual(rows["sale_party"].narration, "Sale Invoice#B1 ")
        self.invoice(identifier="2", discount="0", bill=None)
        narration = next(row.narration for row in self.report.entries("1") if row.transaction_id == "2")
        self.assertEqual(narration, "Sale Invoice# Discount 0")

    def test_cash_accounts_differ_between_invoice_and_journal(self):
        self.invoice("P", payment="C")
        self.invoice("S", identifier="2", payment="C")
        self.journal(identifier="3", kind="2")
        rows = self.arms()
        self.assertEqual(rows["purchase_party"].account_code, "04010001")
        self.assertEqual(rows["sale_party"].account_code, "04010001")
        self.assertEqual(rows["journal_cash"].account_code, "CONFIG_CASH")
        self.assertEqual(rows["journal_cash"].credit, Decimal("10.25"))

    def test_journal_cash_direction_and_fractional_entries_are_preserved(self):
        self.journal(kind="3", lines=[("X", None, "3.125", None), ("Y", "999", "4.875", "note")])
        rows = self.arms()
        self.assertEqual((rows["journal_cash"].debit, rows["journal_cash"].credit), (8, 0))
        details = [row for row in self.report.entries("1") if row.source_arm == "journal_detail"]
        self.assertEqual([row.credit for row in details], [Decimal("3.125"), Decimal("4.875")])
        self.assertIsNone(rows["journal_cash"].narration)

    def test_cancellation_requires_null_and_missing_config_affects_only_cash_arm(self):
        self.journal(identifier="1", cancel="0")
        self.journal(identifier="2", cancel="Y")
        self.journal(identifier="3", cancel="")
        self.journal(identifier="4", kind="2", company="2")
        self.assertEqual(self.report.entries("1"), [])
        company_two = self.report.entries("2")
        self.assertEqual([row.source_arm for row in company_two], ["journal_detail"])

    def test_duplicate_config_rows_multiply_grouped_cash_amount_only(self):
        self.insert("CONFIG", CMP_ID="1", CHCODE_CASH="CONFIG_CASH")
        self.journal(kind="2")
        rows = self.arms()
        self.assertEqual(rows["journal_detail"].debit, Decimal("10.25"))
        self.assertEqual(rows["journal_cash"].credit, Decimal("20.50"))

    def test_unknown_types_preserve_null_decode_accounts(self):
        self.invoice(kind="42", payment="?")
        rows = self.report.entries("1")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.account_code is None for row in rows))
        self.assertTrue(all(row.narration == "B1 " for row in rows))

    def test_return_transaction_type_account_mapping(self):
        self.invoice("P", kind="10")
        self.invoice("S", identifier="2", kind="9")
        rows = self.arms()
        self.assertEqual(rows["purchase_product"].account_code, "EXP")
        self.assertEqual(rows["purchase_party"].narration, "Sale Return#B1 ")
        self.assertEqual(rows["sale_product"].account_code, "PUR")
        self.assertEqual(rows["sale_party"].narration, "Purchase Return#B1 ")

    def test_period_balances_separate_opening_and_apply_literal_account_prefix(self):
        self.journal(lines=[("PARTY", "10.25", None, "opening")])
        self.invoice("P", when="2025-01-02T23:59:59")
        self.invoice("S", identifier="2", when="2025-01-03T00:00:00")
        balances = self.report.balances("1", date_from="2025-01-02", date_to="2025-01-02", account_prefix="PART")
        self.assertEqual(len(balances), 1)
        balance = balances[0]
        self.assertEqual((balance.opening, balance.debit, balance.credit, balance.closing), (Decimal("10.25"), 0, 100, Decimal("-89.75")))
        self.assertEqual((balance.opening_debit, balance.opening_credit), (Decimal("10.25"), 0))
        self.assertEqual((balance.closing_debit, balance.closing_credit), (0, Decimal("89.75")))
        self.assertEqual(self.report.balances("1", account_prefix="P%"), [])
        rows = self.report.entries("1", date_from="2025-01-02", date_to="2025-01-02", account_code="PARTY")
        self.assertEqual([row.source_arm for row in rows], ["purchase_party"])
        self.assertEqual(self.report.entries("1", account_code="PARTY' OR 1=1 --"), [])

    def test_historical_decimal_precision_is_independent_of_host_context(self):
        self.invoice("P", lines=[("A", "1e125", "1e125", "0.00000000000000000000000000000000000001")])
        self.journal(lines=[("PUR", "1e-130", None, None)])
        with localcontext() as context:
            context.prec = 600
            expected = Decimal("1e250") - Decimal("1e210") + Decimal("1e-130")
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            balance = self.report.balances("1", account_prefix="PUR")[0]
        self.assertEqual(balance.closing, expected)

    def test_one_readonly_snapshot_and_source_file_unchanged(self):
        self.invoice()
        before = hashlib.sha256(self.path.read_bytes()).digest()
        calls, statements = [], []

        @contextmanager
        def observed(path, **kwargs):
            calls.append(kwargs)
            with session(path, **kwargs) as connection:
                connection.set_trace_callback(statements.append)
                yield connection

        with patch("bifma.legacy_ledger.session", observed):
            self.report.balances("1")
        self.assertEqual(calls, [{}])
        self.assertEqual(sum(statement == "BEGIN" for statement in statements), 1)
        self.assertEqual(sum(statement.startswith("SELECT") for statement in statements), 10)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), before)
        self.assertIn("unverified", self.report.compatibility)

    def test_invalid_filters_and_nonfinite_history_fail(self):
        for filters in ({"date_from": "2025-02-02", "date_to": "2025-02-01"}, {"date_to": "bad"}, {"account_code": 1}):
            with self.subTest(filters=filters), self.assertRaises(LegacyLedgerError):
                self.report.entries("1", **filters)
        with self.assertRaises(LegacyLedgerError):
            self.report.entries(1.0)
        self.invoice(lines=[("A", "NaN", "1", None)])
        with self.assertRaises(LegacyLedgerError):
            self.report.entries("1")


if __name__ == "__main__":
    unittest.main()
