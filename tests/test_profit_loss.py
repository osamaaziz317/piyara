"""Periodic profit is available only with complete costs and explicit mappings."""

from decimal import Decimal
import unittest
from uuid import uuid4

from bifma.native_reports import NativeReports
from bifma.native_schema import native_transaction
from bifma.report_export import ReportExporter
from bifma.security import AuthorizationError
from tests.test_transactions import TransactionFixture
from tests import test_valuation as valuation_helpers


class ProfitLossTests(TransactionFixture, unittest.TestCase):
    baseline = valuation_helpers.ValuationTests.baseline

    def setUp(self):
        super().setUp()
        self.reports = NativeReports(self.path, security=self.security)

    def classify(self):
        classifications = {"customer": "asset", "supplier": "liability", "sales": "income", "purchases": "expense",
                           "inventory": "asset", "equity": "equity", "cash": "asset", "bank": "asset"}
        with native_transaction(self.path) as connection:
            connection.executemany("UPDATE accounts SET classification=? WHERE id=?",
                                   [(category, self.accounts[key]) for key, category in classifications.items()])

    def trade(self, kind="sale", **values):
        return self.post(self.save(kind, self.trade_values(kind, **values)))

    def test_income_expense_and_inventory_change_reconcile_periodic_cost_without_double_count(self):
        self.classify()
        self.opening()
        self.trade("purchase", quantity="5", rate="4")
        self.trade(quantity="2", rate="20", when="2026-01-03")
        result = self.reports.profit_loss(self.actor, self.company, date_from="2026-01-02")
        self.assertEqual(result["status"], "ready")
        totals = result["totals"]
        self.assertEqual((totals["income"], totals["expense"], totals["inventory_opening"], totals["inventory_closing"]),
                         (Decimal("40"), Decimal("20"), Decimal("100"), Decimal("112")))
        self.assertEqual((totals["inventory_adjustment"], totals["profit"]), (Decimal("12"), Decimal("32")))
        self.assertEqual(result["mapping_issues"], [])
        destination = self.path.parent / "profit.html"
        ReportExporter(self.path, self.security).export_html(self.actor, self.company, "profit_loss", destination,
                                                           date_from="2026-01-02")
        content = destination.read_text(encoding="utf-8")
        self.assertIn("Periodic trading profit", content)
        self.assertIn(">32</bdi>", content)
        self.assertIn("Net external stock openings", content)

    def test_external_stock_opening_and_reversal_do_not_create_trading_profit(self):
        self.classify()
        self.opening()
        additional = self.opening(quantity="2", value="20", when="2026-01-02")
        result = self.reports.profit_loss(self.actor, self.company, date_from="2026-01-02", date_to="2026-01-02")
        self.assertEqual((result["totals"]["opening_injections"], result["totals"]["inventory_adjustment"], result["totals"]["profit"]),
                         (Decimal("20"), Decimal(0), Decimal(0)))
        self.reverse(additional)
        later = self.reports.profit_loss(self.actor, self.company, date_from="2026-01-02")
        self.assertEqual((later["totals"]["opening_injections"], later["totals"]["profit"]), (Decimal(0), Decimal(0)))

    def test_baseline_default_period_begins_after_snapshot_and_preserves_source_value(self):
        self.baseline()
        self.classify()
        self.trade(quantity="2", rate="10")
        result = self.reports.profit_loss(self.actor, self.company)
        self.assertEqual(result["date_from"], "2026-01-02")
        self.assertEqual((result["totals"]["inventory_opening"], result["totals"]["inventory_closing"], result["totals"]["profit"]),
                         (Decimal("99"), Decimal("93"), Decimal("14")))
        with self.assertRaisesRegex(ValueError, "closing snapshot"):
            self.reports.profit_loss(self.actor, self.company, date_from="2026-01-01")
        snapshot = self.reports.export_snapshot(self.actor, self.company, "profit_loss")
        self.assertEqual(snapshot["filters"]["date_from"], "2026-01-02")

    def test_unclassified_or_incompatible_postings_never_silently_produce_profit(self):
        self.opening()
        self.trade(quantity="2")
        result = self.reports.profit_loss(self.actor, self.company)
        self.assertEqual(result["status"], "review_required")
        self.assertIsNone(result["totals"]["profit"])
        self.assertGreater(result["totals"]["unclassified_count"], 0)
        self.classify()
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET classification='asset' WHERE id=?", (self.accounts["purchases"],))
        incompatible = self.reports.profit_loss(self.actor, self.company)
        self.assertIsNone(incompatible["totals"]["profit"])
        self.assertTrue(any("expense" in row["issue"] for row in incompatible["mapping_issues"]))

    def test_remapping_current_product_does_not_hide_incompatible_actual_sales_account(self):
        self.classify()
        self.opening()
        sale = self.trade()
        replacement = str(uuid4())
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET classification='asset' WHERE id=?", (self.accounts["sales"],))
            connection.execute("INSERT INTO accounts(id,code,name,classification) VALUES (?,'NEW-SALES','Replacement income','income')", (replacement,))
            connection.execute("UPDATE products SET sale_account_id=? WHERE id=?", (replacement, self.product))
        result = self.reports.profit_loss(self.actor, self.company)
        self.assertIsNone(result["totals"]["profit"])
        self.assertTrue(any(row.get("document_id") == sale["id"] for row in result["mapping_issues"]))

    def test_separate_inventory_journal_blocks_double_cogs_adjustment(self):
        self.classify()
        self.opening()
        values = self.common()
        values["lines"] = [{"account_id": self.accounts["purchases"], "debit": "5", "credit": "0"},
                           {"account_id": self.accounts["inventory"], "debit": "0", "credit": "5"}]
        self.post(self.save("journal", values))
        result = self.reports.profit_loss(self.actor, self.company, date_from="2026-01-02")
        self.assertIsNone(result["totals"]["profit"])
        self.assertTrue(any("Separate inventory" in row["issue"] for row in result["mapping_issues"]))

    def test_unknown_cost_prevents_profit_and_export_preserves_review_reasons(self):
        self.baseline(rate="0", rate_source="missing")
        self.classify()
        self.trade()
        result = self.reports.profit_loss(self.actor, self.company)
        self.assertEqual(result["totals"]["income"], Decimal("10"))
        self.assertEqual(result["totals"]["missing_valuation_count"], 1)
        self.assertIsNone(result["totals"]["profit"])
        destination = self.path.parent / "unknown-profit.html"
        ReportExporter(self.path, self.security).export_html(self.actor, self.company, "profit_loss", destination)
        content = destination.read_text(encoding="utf-8")
        self.assertIn("Review required", content)
        self.assertIn("Unknown", content)

    def test_profit_report_requires_all_underlying_grants_and_current_company_access(self):
        user = self.security.create_user(self.actor, "profit_reader", "Silver orchard 63 green river")
        actor = self.security.authenticate("profit_reader", "Silver orchard 63 green river")
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.set_permissions(self.actor, user.id, "reports", ["read"])
        for module in ("trial_balance", "stock"):
            with self.assertRaises(AuthorizationError):
                self.reports.profit_loss(actor, self.company)
            self.security.set_permissions(self.actor, user.id, module, ["read"])
        self.reports.profit_loss(actor, self.company)
        with self.assertRaises(AuthorizationError):
            self.reports.profit_loss(actor, self.other_company)
        self.security.set_permissions(self.actor, user.id, "stock", [])
        with self.assertRaises(AuthorizationError):
            self.reports.export_snapshot(actor, self.company, "profit_loss")


if __name__ == "__main__":
    unittest.main()
