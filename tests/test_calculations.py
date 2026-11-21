"""Domain tests use no application database, GUI or third-party dependency."""

from dataclasses import FrozenInstanceError
from decimal import Decimal, Inexact, ROUND_DOWN, localcontext
import unittest

from bifma.calculations import (
    CalculationError,
    DiscountKind,
    InvoiceLine,
    MAX_INVOICE_LINES,
    RoundingPolicy,
    as_decimal,
    calculate_invoice,
    calculate_settlement,
    canonical_decimal,
    convert_quantity,
    oracle_round,
)


def percent_line(quantity, rate, discount="0"):
    return InvoiceLine(quantity, rate, discount, discount_kind=DiscountKind.PERCENT)


class DecimalInputTests(unittest.TestCase):
    def test_decimal_text_integer_and_exact_decimal_inputs(self):
        for value in (" 0012.5000 ", Decimal("12.5"), "1.25e1"):
            with self.subTest(value=value):
                self.assertEqual(as_decimal(value), Decimal("12.5"))
        self.assertEqual(as_decimal(12), Decimal(12))

    def test_float_bool_and_other_types_are_never_accepted(self):
        for value in (0.1, float("nan"), True, False, None, b"1", [], object()):
            with self.subTest(value=repr(value)):
                with self.assertRaises(CalculationError):
                    as_decimal(value)

    def test_nonfinite_or_malformed_text_is_rejected(self):
        values = ("", " ", "NaN", "sNaN", "Infinity", "-inf", "1_000", "1,000", "١٢", "+", ".", "1.2.3")
        for value in (*values, Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity")):
            with self.subTest(value=repr(value)):
                with self.assertRaises(CalculationError):
                    as_decimal(value)

    def test_extreme_exponents_and_precision_are_bounded_before_arithmetic(self):
        for value in ("1e999999999", "1e-999999999", "9" * 129, 10**1000, Decimal("1." + "1" * 128)):
            with self.subTest(value=str(value)[:30]):
                with self.assertRaises(CalculationError):
                    as_decimal(value)
        self.assertEqual(canonical_decimal("0E999999999"), "0")

    def test_canonical_text_preserves_integer_zeros_and_removes_signed_zero(self):
        cases = {"1000": "1000", "1000.000": "1000", "-0.000": "0", "1e3": "1000", "0.010000": "0.01", "-123.450": "-123.45"}
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(canonical_decimal(source), expected)
                self.assertEqual(as_decimal(canonical_decimal(source)), Decimal(source))

    def test_input_normalization_is_independent_of_decimal_context(self):
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_DOWN
            context.traps[Inexact] = True
            self.assertEqual(canonical_decimal("123456789.123456000"), "123456789.123456")


class RoundingTests(unittest.TestCase):
    def test_oracle_ties_round_away_from_zero(self):
        cases = (("2.5", 0, "3"), ("-2.5", 0, "-3"), ("1.005", 2, "1.01"), ("-1.005", 2, "-1.01"), ("149", -2, "100"), ("150", -2, "200"), ("-150", -2, "-200"))
        for value, places, expected in cases:
            with self.subTest(value=value, places=places):
                self.assertEqual(oracle_round(value, places), Decimal(expected))

    def test_invalid_places_and_float_inputs_are_rejected(self):
        for places in (True, 1.0, "2", -19, 19):
            with self.subTest(places=places), self.assertRaises(CalculationError):
                oracle_round("1", places)
        with self.assertRaises(CalculationError):
            oracle_round(1.005, 2)


class InvoiceTests(unittest.TestCase):
    def test_recovered_normal_party_total_formula(self):
        lines = [percent_line("3", "19.95", "10"), percent_line("2", "7.25", "2.5")]
        totals = calculate_invoice(lines, rounding=RoundingPolicy.LEGACY_WHOLE, discount="5", breakage="2", freight="10")
        self.assertEqual(totals.line_amounts[0].gross, Decimal("59.85"))
        self.assertEqual(totals.line_amounts[0].discount, Decimal("5.985"))
        self.assertEqual(totals.line_amounts[0].net, Decimal("53.865"))
        self.assertEqual(totals.line_amounts[1].net, Decimal("14.1375"))
        self.assertEqual(totals.raw_subtotal, Decimal("68.0025"))
        self.assertEqual(totals.subtotal, Decimal("68"))
        self.assertEqual(totals.rounding_adjustment, Decimal("-0.0025"))
        self.assertEqual(totals.total, Decimal("71"))

    def test_subtotal_is_rounded_once_not_each_line(self):
        lines = [percent_line("1", "0.49"), percent_line("1", "0.49")]
        self.assertEqual(calculate_invoice(lines, rounding=RoundingPolicy.LEGACY_WHOLE).total, Decimal("1"))
        lines = [percent_line("1", "0.005"), percent_line("1", "0.005")]
        self.assertEqual(calculate_invoice(lines, rounding=RoundingPolicy.CENTS).total, Decimal("0.01"))

    def test_v2_absolute_discount_is_explicit_and_different_from_percent(self):
        legacy = percent_line("3", "20", "10")
        v2 = InvoiceLine("3", "20", "10", discount_kind=DiscountKind.AMOUNT)
        self.assertEqual(legacy.calculate().net, Decimal("54"))
        self.assertEqual(v2.calculate().net, Decimal("50"))
        with self.assertRaises(TypeError):
            InvoiceLine("3", "20", "10")
        with self.assertRaises(TypeError):
            calculate_invoice([legacy])

    def test_header_precision_is_not_silently_discarded(self):
        totals = calculate_invoice([percent_line("1", "1.005")], rounding=RoundingPolicy.CENTS, freight="0.000001")
        self.assertEqual(totals.subtotal, Decimal("1.01"))
        self.assertEqual(totals.total, Decimal("1.010001"))

    def test_negative_zero_quantity_excess_discount_and_precision_fail(self):
        for quantity, rate, discount, kind in (
            ("0", "1", "0", DiscountKind.PERCENT),
            ("-1", "1", "0", DiscountKind.PERCENT),
            ("1", "-1", "0", DiscountKind.PERCENT),
            ("1", "1", "-1", DiscountKind.PERCENT),
            ("1", "1", "100.000001", DiscountKind.PERCENT),
            ("1", "1", "1.000001", DiscountKind.AMOUNT),
            ("1e18", "1", "0", DiscountKind.PERCENT),
            ("0.0000001", "1", "0", DiscountKind.PERCENT),
            ("1", "1.1234567", "0", DiscountKind.PERCENT),
            ("1", "1", "0", "unknown"),
        ):
            with self.subTest(quantity=quantity, rate=rate, discount=discount, kind=kind), self.assertRaises(CalculationError):
                InvoiceLine(quantity, rate, discount, discount_kind=kind)

    def test_free_goods_and_complete_discount_are_valid(self):
        for line in (percent_line("1", "0"), percent_line("1", "10", "100"), InvoiceLine("1", "10", "10", discount_kind=DiscountKind.AMOUNT)):
            with self.subTest(line=line):
                self.assertEqual(calculate_invoice([line], rounding=RoundingPolicy.LEGACY_WHOLE).total, 0)

    def test_header_adjustments_must_be_nonnegative_and_not_overdiscount(self):
        line = percent_line("1", "10")
        for adjustments in ({"discount": "-1"}, {"breakage": "-1"}, {"freight": "-1"}, {"discount": "6", "breakage": "5"}, {"discount": "11", "freight": "100"}):
            with self.subTest(adjustments=adjustments), self.assertRaises(CalculationError):
                calculate_invoice([line], rounding=RoundingPolicy.LEGACY_WHOLE, **adjustments)
        with self.assertRaises(CalculationError):
            calculate_invoice([percent_line("1", "0.49")], rounding=RoundingPolicy.LEGACY_WHOLE, discount="0.01")

    def test_invoice_line_count_types_and_rounding_are_validated(self):
        line = percent_line("1", "1")
        for lines in (None, [], ["not a line"], (line for _ in range(MAX_INVOICE_LINES + 1))):
            with self.subTest(lines=type(lines).__name__), self.assertRaises(CalculationError):
                calculate_invoice(lines, rounding=RoundingPolicy.LEGACY_WHOLE)
        with self.assertRaises(CalculationError):
            calculate_invoice([line], rounding="unknown")

    def test_results_are_exact_at_maximum_input_precision(self):
        line = percent_line("999999999999999999.999999", "999999999999999999.999999", "0.000001")
        with localcontext() as context:
            context.prec = 100
            expected = Decimal("999999999999999999.999999") ** 2 * Decimal("0.99999999")
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_DOWN
            context.traps[Inexact] = True
            totals = calculate_invoice([line], rounding=RoundingPolicy.LEGACY_WHOLE)
            self.assertEqual(totals.raw_subtotal, expected)
            self.assertEqual(as_decimal(canonical_decimal(totals.raw_subtotal)), expected)
            self.assertEqual(totals.total, oracle_round(expected))

    def test_calculation_models_are_immutable(self):
        line = percent_line("1", "10")
        with self.assertRaises(FrozenInstanceError):
            line.rate = Decimal("1")
        totals = calculate_invoice([line], rounding=RoundingPolicy.CENTS)
        with self.assertRaises(FrozenInstanceError):
            totals.total = Decimal("1")


class SettlementTests(unittest.TestCase):
    def test_received_is_separate_from_invoice_amount(self):
        totals = calculate_invoice([percent_line("1", "100")], rounding=RoundingPolicy.LEGACY_WHOLE)
        settlement = calculate_settlement(totals.total, "35.25")
        self.assertEqual(totals.total, Decimal("100"))
        self.assertEqual(settlement.invoice_total, totals.total)
        self.assertEqual(settlement.applied, Decimal("35.25"))
        self.assertEqual(settlement.due, Decimal("64.75"))
        self.assertEqual(settlement.credit, Decimal("0"))
        self.assertEqual(settlement.balance, Decimal("64.75"))

    def test_overpayment_is_explicit_credit(self):
        settlement = calculate_settlement("100", "125.01")
        self.assertEqual(settlement.applied, Decimal("100"))
        self.assertEqual(settlement.due, Decimal("0"))
        self.assertEqual(settlement.credit, Decimal("25.01"))
        self.assertEqual(settlement.balance, Decimal("-25.01"))

    def test_zero_total_and_exact_payment(self):
        self.assertEqual(calculate_settlement("0", "3").credit, Decimal("3"))
        settlement = calculate_settlement("10.01", "10.01")
        self.assertEqual((settlement.due, settlement.credit, settlement.balance), (0, 0, 0))

    def test_invalid_payment_and_total_are_rejected(self):
        for total, received in (("-1", "0"), ("1", "-1"), ("1", float("inf")), ("NaN", "0"), ("1", "1e18")):
            with self.subTest(total=total, received=received), self.assertRaises(CalculationError):
                calculate_settlement(total, received)


class QuantityTests(unittest.TestCase):
    def test_unit_conversion_preserves_fractional_quantity(self):
        self.assertEqual(convert_quantity(large="2", small="5", factor="12"), Decimal("29"))
        self.assertEqual(convert_quantity(large="1", small="14", factor="12"), Decimal("26"))
        self.assertEqual(convert_quantity(large="1.5", small="0.25", factor="12"), Decimal("18.25"))
        self.assertEqual(convert_quantity(large="0", small="0", factor="12"), 0)

    def test_invalid_and_unrepresentable_conversions_fail(self):
        for large, small, factor in (("-1", "0", "12"), ("0", "-1", "12"), ("1", "0", "0"), ("1", "0", "-1"), ("1e17", "0", "10"), ("0.000001", "0", "0.000001")):
            with self.subTest(large=large, small=small, factor=factor), self.assertRaises(CalculationError):
                convert_quantity(large=large, small=small, factor=factor)


if __name__ == "__main__":
    unittest.main()
