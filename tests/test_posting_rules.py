"""Corrected new-entry invariants; legacy histories are tested separately."""

from decimal import Decimal, Inexact, localcontext
import unittest

from bifma.calculations import CalculationError, DiscountKind, InvoiceLine, RoundingPolicy
from bifma.posting_rules import (
    JournalEntry, PostedDeliveryLink, PostingError, SettlementDirection, StockItem,
    StockKind, StockSource, TradeKind, TradePostingItem, build_settlement_posting,
    build_stock_movements, build_trade_posting, validate_balanced,
)


def item(account="REVENUE", rate="100", quantity="1", discount="0"):
    return TradePostingItem(InvoiceLine(quantity, rate, discount, discount_kind=DiscountKind.PERCENT), account)


def trade(kind=TradeKind.SALE, items=None, **kwargs):
    return build_trade_posting(kind, items if items is not None else [item()],
                               party_account="PARTY", subtotal_rounding=RoundingPolicy.CENTS,
                               posting_places=2, **kwargs)


def stock(quantity="1", product="A", store="1"):
    return StockItem(product, store, quantity, source=StockSource.DIRECT)


class TradePostingTests(unittest.TestCase):
    def test_every_kind_has_correct_balanced_direction(self):
        for kind in TradeKind:
            with self.subTest(kind=kind):
                posting = trade(kind)
                self.assertEqual(validate_balanced(posting.entries, posting_places=2, expected_amount="100"), 100)
                party, product = posting.entries
                if kind in (TradeKind.SALE, TradeKind.PURCHASE_RETURN):
                    self.assertEqual((party.debit, party.credit, product.debit, product.credit), (100, 0, 0, 100))
                else:
                    self.assertEqual((party.debit, party.credit, product.debit, product.credit), (0, 100, 100, 0))

    def test_headers_apply_once_and_sale_discount_is_on_both_sides(self):
        posting = trade(items=[item("A", "60"), item("B", "40")], discount="10", breakage="2", freight="3")
        self.assertEqual(posting.total, 91)
        self.assertEqual([part.amount for part in posting.allocations], [Decimal("54.60"), Decimal("36.40")])
        self.assertEqual(sum(part.adjustment for part in posting.allocations), -9)
        self.assertEqual(sum(entry.debit for entry in posting.entries), 91)
        self.assertEqual(sum(entry.credit for entry in posting.entries), 91)

    def test_grouping_merges_same_account_before_allocating_headers(self):
        split = trade(items=[item("A", "30"), item("A", "30"), item("B", "40")], freight="1.01")
        merged = trade(items=[item("A", "60"), item("B", "40")], freight="1.01")
        self.assertEqual(split.allocations, merged.allocations)
        self.assertEqual(split.entries, merged.entries)

    def test_allocation_weights_use_discounted_line_values(self):
        posting = trade(items=[item("A", "100", discount="50"), item("B", "100")], discount="30")
        self.assertEqual([(part.raw_subtotal, part.amount) for part in posting.allocations], [(50, 40), (100, 80)])

    def test_ties_are_broken_by_account_code_independent_of_input_order(self):
        lines = [item("B", "1"), item("A", "1")]
        first = trade(items=lines, discount="1.99")
        second = trade(items=reversed(lines), discount="1.99")
        self.assertEqual(first.allocations, second.allocations)
        self.assertEqual([(part.account_code, part.amount) for part in first.allocations], [("A", Decimal("0.01")), ("B", 0)])
        self.assertEqual(len(first.entries), 2)

    def test_largest_remainder_preserves_total_for_nonterminating_ratios(self):
        posting = trade(items=[item("A", "1"), item("B", "1"), item("C", "1")], discount="2")
        self.assertEqual([part.amount for part in posting.allocations], [Decimal("0.34"), Decimal("0.33"), Decimal("0.33")])
        self.assertEqual(sum(part.amount for part in posting.allocations), 1)

    def test_whole_unit_allocation_uses_whole_units_without_fractional_postings(self):
        posting = build_trade_posting("sale", [item("B", "1"), item("A", "1")], party_account="PARTY", subtotal_rounding=RoundingPolicy.LEGACY_WHOLE, posting_places=0, discount="1")
        self.assertEqual([(part.account_code, part.amount) for part in posting.allocations], [("A", 1), ("B", 0)])
        self.assertEqual(validate_balanced(posting.entries, posting_places=0), 1)

    def test_both_rounding_policies_are_explicit_and_final_rounding_is_visible(self):
        lines = [item(rate="1.49")]
        whole = build_trade_posting("sale", lines, party_account="PARTY", subtotal_rounding=RoundingPolicy.LEGACY_WHOLE, posting_places=0, freight="0.50")
        cents = build_trade_posting("sale", lines, party_account="PARTY", subtotal_rounding=RoundingPolicy.CENTS, posting_places=2, freight="0.50")
        self.assertEqual((whole.invoice_totals.total, whole.total, whole.final_rounding_adjustment), (Decimal("1.50"), 2, Decimal("0.50")))
        self.assertEqual(cents.total, Decimal("1.99"))
        with self.assertRaises(TypeError):
            build_trade_posting("sale", lines, party_account="PARTY", subtotal_rounding=RoundingPolicy.CENTS)
        with self.assertRaises(TypeError):
            build_trade_posting("sale", lines, party_account="PARTY", posting_places=2)

    def test_zero_total_omits_zero_postings_and_zero_weight_freight_is_rejected(self):
        posting = trade(items=[item(discount="100")])
        self.assertEqual(posting.total, 0)
        self.assertEqual(posting.entries, ())
        self.assertEqual(posting.allocations[0].amount, 0)
        with self.assertRaises(PostingError):
            trade(items=[item(discount="100")], freight="1")

    def test_no_hardcoded_party_or_cash_account(self):
        posting = build_trade_posting("sale", [item()], party_account="EXPLICIT_CASH", subtotal_rounding=RoundingPolicy.CENTS, posting_places=2)
        self.assertEqual(posting.entries[0].account_code, "EXPLICIT_CASH")

    def test_invalid_posting_inputs_and_unknown_charges_fail(self):
        for places in (True, 1, 3, "2", 2.0):
            with self.subTest(places=places), self.assertRaises(PostingError):
                build_trade_posting("sale", [item()], party_account="PARTY", subtotal_rounding=RoundingPolicy.CENTS, posting_places=places)
        with self.assertRaises(PostingError):
            trade(kind="unknown")
        with self.assertRaises(PostingError):
            trade(items=[item("PARTY")])
        with self.assertRaises(PostingError):
            trade(items=[])
        with self.assertRaises(PostingError):
            TradePostingItem("not validated", "A")
        with self.assertRaises(TypeError):
            trade(tax="10")
        with self.assertRaises(TypeError):
            trade(labour="10")

    def test_incorrect_rates_discounts_and_nonfinite_values_cannot_enter_posting(self):
        for rate in ("-1", "NaN", "Infinity", 0.1):
            with self.subTest(rate=rate), self.assertRaises(CalculationError):
                trade(items=[item(rate=rate)])
        for discount in ("-1", "100.01", "NaN"):
            with self.subTest(discount=discount), self.assertRaises(CalculationError):
                trade(items=[item(discount=discount)])
        with self.assertRaises(CalculationError):
            trade(discount="101")

    def test_large_exact_values_and_allocation_ignore_ambient_decimal_context(self):
        lines = [item("A", "999999999999999999.999999", "999999999999999999.999999"), item("B", "0.000001")]
        expected = trade(items=lines, discount="0.01")
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            actual = trade(items=lines, discount="0.01")
        self.assertEqual(actual, expected)


class SettlementPostingTests(unittest.TestCase):
    def settlement(self, total="100", received="125", direction=SettlementDirection.RECEIPT, places=2):
        return build_settlement_posting(total, received, party_account="PARTY", cash_account="CASH", direction=direction, posting_places=places)

    def test_overpayment_posts_entire_cash_and_separate_credit(self):
        invoice = trade()
        payment = self.settlement(invoice.total)
        self.assertEqual(invoice.total, 100)
        self.assertEqual((payment.settlement.applied, payment.settlement.due, payment.settlement.credit), (100, 0, 25))
        self.assertEqual([(entry.account_code, entry.debit, entry.credit) for entry in payment.entries], [("CASH", 125, 0), ("PARTY", 0, 125)])
        combined_party = sum(entry.debit - entry.credit for entry in (*invoice.entries, *payment.entries) if entry.account_code == "PARTY")
        self.assertEqual(combined_party, -25)

    def test_payment_direction_preserves_supplier_advance_without_invented_account(self):
        payment = self.settlement(direction="payment")
        self.assertEqual([(entry.account_code, entry.debit, entry.credit) for entry in payment.entries], [("PARTY", 125, 0), ("CASH", 0, 125)])
        self.assertEqual(payment.settlement.credit, 25)

    def test_zero_and_partial_settlements(self):
        self.assertEqual(self.settlement(received="0").entries, ())
        self.assertEqual(self.settlement(received="25.01").settlement.due, Decimal("74.99"))

    def test_actual_received_and_invoice_total_are_never_silently_rounded(self):
        for total, received, places in (("100", "1.001", 2), ("100.001", "1", 2), ("100", "1.01", 0), ("-1", "0", 2), ("100", "NaN", 2)):
            with self.subTest(total=total, received=received, places=places), self.assertRaises(CalculationError):
                self.settlement(total, received, places=places)
        with self.assertRaises(PostingError):
            build_settlement_posting("1", "1", party_account="CASH", cash_account="CASH", direction="receipt", posting_places=2)


class JournalValidationTests(unittest.TestCase):
    def test_mismatched_totals_and_precision_fail(self):
        for entries, expected in (([JournalEntry("A", debit="10"), JournalEntry("B", credit="9")], "10"), ([JournalEntry("A", debit="10"), JournalEntry("B", credit="10")], "9"), ([JournalEntry("A", debit="0.001"), JournalEntry("B", credit="0.001")], "0.001")):
            with self.subTest(entries=entries), self.assertRaises(PostingError):
                validate_balanced(entries, posting_places=2, expected_amount=expected)
        self.assertEqual(validate_balanced([], posting_places=2, expected_amount="0"), 0)

    def test_invalid_single_rows_fail(self):
        for debit, credit in (("0", "0"), ("1", "1"), ("-1", "0"), ("NaN", "0"), (0.1, "0")):
            with self.subTest(debit=debit, credit=credit), self.assertRaises(CalculationError):
                JournalEntry("A", debit, credit)
        for account in ("", " A", "A\nB", "A\x7fB", 1):
            with self.subTest(account=account), self.assertRaises(PostingError):
                JournalEntry(account, debit="1")


class StockPostingTests(unittest.TestCase):
    def test_all_corrected_stock_directions(self):
        for kind in StockKind:
            with self.subTest(kind=kind):
                posting = build_stock_movements(kind, [stock("2.5")])
                incoming = kind in (StockKind.PURCHASE, StockKind.SALE_RETURN, StockKind.BREAKAGE_RECEIVE)
                self.assertEqual(posting.movements[0].quantity_delta, Decimal("2.5") if incoming else Decimal("-2.5"))
                self.assertEqual(posting.movements[0].quantity_in, Decimal("2.5") if incoming else 0)
                self.assertEqual(posting.movements[0].quantity_out, 0 if incoming else Decimal("2.5"))

    def test_movements_aggregate_by_product_and_store_deterministically(self):
        rows = [stock("2", "B", "2"), stock("1.25"), stock("0.75"), stock("1", "A", "2")]
        posting = build_stock_movements("purchase", rows)
        self.assertEqual(posting, build_stock_movements("purchase", reversed(rows)))
        self.assertEqual([(row.store_id, row.product_code, row.quantity_delta) for row in posting.movements], [("1", "A", 2), ("2", "A", 1), ("2", "B", 2)])

    def test_explicit_delivery_link_prevents_second_dispatch_and_is_returned_for_reservation(self):
        link = PostedDeliveryLink("movement-1", "A", "1", "2")
        linked = StockItem("A", "1", "2", source=StockSource.POSTED_DELIVERY, delivery_link=link)
        posting = build_stock_movements("sale", [linked, stock("3")])
        self.assertEqual(posting.linked_movement_ids, ("movement-1",))
        self.assertEqual(posting.movements[0].quantity_delta, -3)

    def test_missing_mismatched_partial_or_ambiguous_delivery_links_fail(self):
        with self.assertRaises(TypeError):
            StockItem("A", "1", "1")
        with self.assertRaises(PostingError):
            StockItem("A", "1", "1", source="posted_delivery")
        link = PostedDeliveryLink("movement-1", "A", "1", "2")
        for product, store, quantity in (("B", "1", "2"), ("A", "2", "2"), ("A", "1", "1")):
            with self.subTest(product=product, store=store, quantity=quantity), self.assertRaises(PostingError):
                StockItem(product, store, quantity, source="posted_delivery", delivery_link=link)
        with self.assertRaises(PostingError):
            StockItem("A", "1", "2", source="direct", delivery_link=link)
        linked = StockItem("A", "1", "2", source="posted_delivery", delivery_link=link)
        with self.assertRaises(PostingError):
            build_stock_movements("sale", [linked, linked])
        with self.assertRaises(PostingError):
            build_stock_movements("sale_return", [linked])

    def test_invalid_stock_quantities_and_kinds_fail(self):
        for quantity in ("0", "-1", "NaN", 0.1):
            with self.subTest(quantity=quantity), self.assertRaises(CalculationError):
                stock(quantity)
        with self.assertRaises(PostingError):
            build_stock_movements("unknown", [stock()])


if __name__ == "__main__":
    unittest.main()
