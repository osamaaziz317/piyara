"""Frozen movement valuation preserves baseline values with exact new costs."""

from decimal import Context, Decimal, localcontext
import json
import sqlite3
import unittest

from bifma.native_reports import NativeReports
from bifma.native_schema import native_transaction
from bifma.report_export import ReportExporter
from tests.test_transactions import TransactionFixture


class ValuationTests(TransactionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.reports = NativeReports(self.path, security=self.security)

    def baseline(self, *, rate="3", rate_source="primary", value="99", quantity="10"):
        with native_transaction(self.path) as connection:
            labels = {key: dict(connection.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone())
                      for key, table, identifier in (("product", "products", self.product), ("store", "stores", self.store), ("unit", "units", self.unit))}
            connection.execute("INSERT INTO baseline_stock_balances(company_id,store_id,product_id,pool,quantity,historical_value,base_unit_id,labels_json) VALUES (?,?,?,'regular',?,?,?,?)",
                               (self.company, self.store, self.product, quantity, value, self.unit, json.dumps(labels)))
            connection.execute("INSERT INTO baseline_purchase_rates(company_id,product_id,rate,rate_source,source_id,source_json) VALUES (?,?,?,?,?,'{}')",
                               (self.company, self.product, rate, rate_source, "91" if rate_source != "missing" else None))
            connection.execute("INSERT INTO baseline_imports(singleton,source_sha256,cutoff_date,manifest_json) VALUES (1,?,'2026-01-01','{}')", ("b" * 64,))

    @staticmethod
    def cost(document, index=0):
        movement = document["stock_movements"][index]
        return next(row for row in document["stock_valuations"] if row["movement_id"] == movement["id"])

    def trade(self, kind="sale", **values):
        return self.post(self.save(kind, self.trade_values(kind, **values)))

    def test_baseline_movement_value_is_carried_without_revaluing_quantity_and_no_cost_journal(self):
        self.baseline()
        sale = self.trade(quantity="2", rate="10")
        self.assertEqual((self.cost(sale)["unit_cost"], self.cost(sale)["value"]), ("3", "6"))
        self.assertEqual(sum(Decimal(row["debit"]) for row in sale["journal_lines"]), Decimal("20"))
        result = self.reports.stock_valuation(self.actor, self.company, date_from="2026-01-02")
        self.assertEqual(result["totals"], {"value_opening": Decimal("99"), "value_in": Decimal(0),
                         "value_out": Decimal("6"), "value_closing": Decimal("93"), "missing_valuation_count": 0})
        self.assertEqual(result["rows"][0]["closing"], 8)
        ledger = self.reports.stock_ledger(self.actor, self.company, self.product)
        self.assertEqual([row["value_balance"] for row in ledger["rows"]], [Decimal("99"), Decimal("93")])
        destination = self.path.parent / "valuation.html"
        ReportExporter(self.path, self.security).export_html(self.actor, self.company, "stock_valuation", destination,
                                                           date_from="2026-01-02")
        content = destination.read_text(encoding="utf-8")
        self.assertIn(">93</bdi>", content)
        self.assertIn("not closing quantity multiplied", content)

    def test_latest_posting_order_with_date_filter_does_not_revalue_saved_movements(self):
        self.baseline()
        original = self.trade(quantity="1", when="2026-01-02")
        self.trade("purchase", quantity="5", rate="7", when="2026-01-10")
        backdated = self.trade("purchase", quantity="5", rate="4", when="2026-01-03")
        latest = self.trade(when="2026-01-11")
        earlier = self.trade(when="2026-01-04")
        for sale in (latest, earlier):
            self.assertEqual(self.cost(sale)["unit_cost"], "4")
            self.assertEqual(json.loads(self.cost(sale)["source_json"])["document_id"], backdated["id"])
        reread = self.service.get(self.actor, self.company, original["id"])
        self.assertEqual(self.cost(reread)["value"], "3")
        original_period = self.reports.stock_valuation(self.actor, self.company, date_to="2026-01-02")
        self.assertEqual(original_period["totals"]["value_closing"], Decimal("96"))

    def test_pending_purchase_last_line_wins_across_stores_and_discounts_do_not_change_cost(self):
        self.baseline()
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO company_stores(company_id,store_id) VALUES (?,?)", (self.company, self.other_store))
        values = self.trade_values("purchase", rate="2", discount="1")
        values["lines"].append({**values["lines"][0], "store_id": self.other_store, "rate": "5", "discount_percent": "10"})
        purchase = self.post(self.save("purchase", values))
        self.assertEqual([row["unit_cost"] for row in purchase["stock_valuations"]], ["5", "5"])
        self.assertEqual([row["value"] for row in purchase["stock_valuations"]], ["5", "5"])
        self.assertEqual(self.cost(self.trade())["unit_cost"], "5")

    def test_reversal_exactly_cancels_original_cost_and_candidate_cancellation_is_dated(self):
        self.baseline()
        sale = self.trade(quantity="2")
        purchase = self.trade("purchase", quantity="4", rate="8", when="2026-01-03")
        reversal = self.reverse(sale, when="2026-01-04")
        self.assertEqual(self.cost(reversal)["value"], "6")
        self.assertEqual(json.loads(self.cost(reversal)["source_json"])["method"], "exact_reversal")
        self.reverse(purchase, when="2026-01-07")
        before = self.trade(when="2026-01-06")
        after = self.trade(when="2026-01-08")
        self.assertEqual((self.cost(before)["unit_cost"], self.cost(after)["unit_cost"]), ("8", "3"))

    def test_baseline_primary_beats_native_sale_return_fallback(self):
        self.baseline()
        sale = self.trade(quantity="2", rate="10")
        returned = self.trade("sale_return", source=sale["lines"][0]["id"], rate="10", when="2026-01-03")
        self.assertEqual(self.cost(returned)["unit_cost"], "3")
        self.assertEqual(self.cost(self.trade(when="2026-01-04"))["unit_cost"], "3")

    def test_new_return_fallback_overrides_old_fallback_until_a_primary_purchase_exists(self):
        self.baseline(rate="2", rate_source="fallback")
        sale = self.trade(quantity="2", rate="10")
        returned = self.trade("sale_return", source=sale["lines"][0]["id"], rate="10", when="2026-01-03")
        self.assertEqual(self.cost(returned)["unit_cost"], "10")
        self.assertEqual(self.cost(self.trade(when="2026-01-04"))["unit_cost"], "10")
        self.trade("purchase", rate="4", when="2026-01-05")
        self.assertEqual(self.cost(self.trade(when="2026-01-06"))["unit_cost"], "4")

    def test_missing_cost_is_unknown_and_preserved_opening_value_remains_visible(self):
        self.baseline(rate="0", rate_source="missing")
        sale = self.trade()
        self.assertIsNone(self.cost(sale)["unit_cost"])
        self.assertIsNone(self.cost(sale)["value"])
        result = self.reports.stock_valuation(self.actor, self.company, date_from="2026-01-02")
        self.assertEqual(result["totals"]["value_opening"], Decimal("99"))
        self.assertIsNone(result["totals"]["value_out"])
        self.assertIsNone(result["totals"]["value_closing"])
        self.assertEqual(result["totals"]["missing_valuation_count"], 1)
        destination = self.path.parent / "unknown.html"
        ReportExporter(self.path, self.security).export_html(self.actor, self.company, "stock_valuation", destination)
        self.assertIn("Unknown", destination.read_text(encoding="utf-8"))

    def test_negative_original_rate_is_preserved_but_not_used_as_new_cost(self):
        self.baseline(rate="-3", value="-30")
        sale = self.trade()
        self.assertEqual(json.loads(self.cost(sale)["source_json"])["issue"], "negative_source_rate")
        report = self.reports.stock_valuation(self.actor, self.company, date_from="2026-01-02")
        self.assertEqual(report["totals"]["value_opening"], Decimal("-30"))
        self.assertIsNone(report["totals"]["value_closing"])

    def test_full_source_rate_precision_survives_multiplication_and_report_aggregation(self):
        rate = "9" * 126 + "." + "1" * 130
        self.baseline(rate=rate, value="0")
        with localcontext(Context(prec=1000)):
            expected = Decimal(rate) * Decimal("1.000001")
        with localcontext(Context(prec=3)):
            sale = self.trade(quantity="1.000001")
            report = self.reports.stock_valuation(self.actor, self.company, date_from="2026-01-02")
        self.assertEqual(Decimal(self.cost(sale)["value"]), expected)
        self.assertEqual(report["totals"]["value_out"], expected)
        self.assertEqual(report["totals"]["value_closing"], expected.copy_negate())

    def test_nonterminating_opening_has_exact_total_but_no_invented_unit_rate(self):
        opening = self.opening(quantity="3", value="1")
        self.assertEqual((self.cost(opening)["unit_cost"], self.cost(opening)["value"]), (None, "1"))
        sale = self.trade()
        self.assertIsNone(self.cost(sale)["value"])
        self.assertEqual(json.loads(self.cost(sale)["source_json"])["issue"], "nonterminating_opening_unit_cost")
        report = self.reports.stock_valuation(self.actor, self.company, date_from="2026-01-02")
        self.assertEqual(report["totals"]["value_opening"], Decimal("1"))
        self.reverse(sale)
        self.assertEqual(self.cost(self.reverse(opening))["value"], "1")

    def test_breakage_uses_entered_rate_and_immutable_snapshot_guards(self):
        received = self.trade("breakage_receive", quantity="3", rate="4", discount="0", breakage="1")
        dispatched = self.trade("breakage_dispatch", quantity="1", rate="5", when="2026-01-03")
        self.assertEqual((self.cost(received)["value"], self.cost(dispatched)["value"]), ("12", "5"))
        self.assertEqual(self.reports.stock_valuation(self.actor, self.company, pool="breakage")["totals"]["value_closing"], Decimal("7"))
        with native_transaction(self.path) as connection:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                connection.execute("UPDATE stock_valuation_snapshots SET value='999'")


if __name__ == "__main__":
    unittest.main()
