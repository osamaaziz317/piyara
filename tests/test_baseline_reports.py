"""Operational closings use synthetic sealed baselines, never business files."""

from decimal import Context, Decimal, localcontext
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma import native_reports
from bifma.native_schema import native_transaction
from bifma.report_export import ReportExporter
from bifma.security import AuthorizationError
from tests import test_native_reports as report_helpers


class BaselineReportTests(unittest.TestCase):
    document = report_helpers.NativeReportTests.document
    stock_document = report_helpers.NativeReportTests.stock_document
    stock_item = report_helpers.NativeReportTests.stock_item
    stock_store = report_helpers.NativeReportTests.stock_store

    def setUp(self):
        report_helpers.NativeReportTests.setUp(self)
        self.second = self.stock_store("SECOND")
        self.city, self.parent = str(uuid4()), str(uuid4())
        self.source_hash = "a" * 64
        self.amount = "9007199254740993.01"
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO cities(id,code,name) VALUES (?,'CITY','City')", (self.city,))
            connection.execute("INSERT INTO accounts(id,code,name,kind,is_postable) VALUES (?,'GROUP','Group','group',0)", (self.parent,))
            connection.execute("UPDATE accounts SET kind='cash',city_id=?,parent_id=? WHERE id=?", (self.city, self.parent, self.debit))
            connection.execute("UPDATE companies SET cash_account_id=? WHERE id=?", (self.debit, self.company))
            for company, amounts in ((self.company, (self.amount, "-" + self.amount)), (self.other, ("77", "-77"))):
                for account_id, balance in zip((self.debit, self.credit), amounts):
                    labels = {"account": dict(connection.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()),
                              "company": dict(connection.execute("SELECT * FROM companies WHERE id=?", (company,)).fetchone())}
                    connection.execute("INSERT INTO baseline_account_balances(company_id,account_id,balance,labels_json) VALUES (?,?,?,?)",
                                       (company, account_id, balance, json.dumps(labels)))
            for store, pool, quantity in ((self.store, "regular", "-2.125"), (self.second, "regular", "10.123456"),
                                          (self.store, "breakage", "4.5")):
                labels = {"product": dict(connection.execute("SELECT * FROM products WHERE id=?", (self.product,)).fetchone()),
                          "store": dict(connection.execute("SELECT * FROM stores WHERE id=?", (store,)).fetchone()),
                          "unit": dict(connection.execute("SELECT * FROM units WHERE id=?", (self.unit,)).fetchone())}
                connection.execute("INSERT INTO baseline_stock_balances(company_id,store_id,product_id,pool,quantity,historical_value,base_unit_id,labels_json) VALUES (?,?,?,?,?,'0',?,?)",
                                   (self.company, store, self.product, pool, quantity, self.unit, json.dumps(labels)))
            connection.execute("INSERT INTO baseline_imports(singleton,source_sha256,cutoff_date,manifest_json) VALUES (1,?,'2026-09-30',?)",
                               (self.source_hash, json.dumps({"synthetic": True})))

    def test_trial_subtrial_and_cities_continue_exact_openings_without_stock_journals(self):
        self.document("NEW", "2026-10-01", "0.1", quantity="4")
        with localcontext(Context(prec=3)):
            trial = self.reports.trial_balance(self.admin, self.company, date_from="2026-10-01")
            sub = self.reports.sub_trial_balance(self.admin, self.company, date_from="2026-10-01")
            city = self.reports.city_balances(self.admin, self.company, date_from="2026-10-01", city_id=self.city)
        account = next(row for row in trial["rows"] if row["account_id"] == self.debit)
        expected = Decimal("9007199254740993.11")
        self.assertEqual((account["opening"], account["debit"], account["closing"]), (Decimal(self.amount), Decimal("0.1"), expected))
        self.assertEqual(trial["totals"], sub["totals"])
        self.assertEqual(trial["totals"]["debit"], Decimal("0.1"))
        self.assertEqual(trial["totals"]["closing"], 0)
        self.assertEqual(next(row for row in sub["rows"] if row["account_id"] == self.parent)["closing"], expected)
        self.assertEqual(city["totals"]["closing_debit"], expected)
        self.assertEqual(city["totals"]["closing_credit"], 0)
        self.assertEqual(trial["baseline"], {"source_sha256": self.source_hash, "cutoff_date": "2026-09-30"})
        self.assertEqual(self.reports.trial_balance(self.admin, self.other)["totals"]["debit"], Decimal("77"))

    def test_cutoff_row_is_explicit_and_paging_carries_into_new_ledger_entries(self):
        self.document("NEW", "2026-10-01", "0.1")
        all_rows = self.reports.ledger(self.admin, self.company, self.debit)
        self.assertEqual(all_rows["total"], 2)
        opening = all_rows["rows"][0]
        self.assertEqual((opening["source"], opening["kind"], opening["business_date"]), ("baseline", "baseline_opening", "2026-09-30"))
        self.assertIsNone(opening["document_id"])
        self.assertEqual(opening["source_sha256"], self.source_hash)
        self.assertEqual(opening["debit"], Decimal(self.amount))
        later = self.reports.ledger(self.admin, self.company, self.debit, limit=1, offset=1)
        self.assertEqual(later["rows"][0]["document_number"], "NEW")
        self.assertEqual(later["rows"][0]["balance"], Decimal("9007199254740993.11"))
        cutoff = self.reports.ledger(self.admin, self.company, self.credit, date_to="2026-09-30")
        self.assertEqual((cutoff["total"], cutoff["debit"], cutoff["credit"]), (1, Decimal(0), Decimal(self.amount)))
        future = self.reports.ledger(self.admin, self.company, self.debit, date_from="2026-10-02")
        self.assertEqual(future["rows"], [])
        self.assertEqual((future["opening"], future["closing"]), (later["closing"],) * 2)

    def test_stock_preserves_negative_openings_pool_store_scope_and_saved_labels(self):
        self.stock_document("IN", "2026-10-01", [{"quantity": "4"}])
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE products SET name='Renamed item' WHERE id=?", (self.product,))
            connection.execute("UPDATE stores SET name='Renamed store' WHERE id=?", (self.store,))
        rows = self.reports.stock(self.admin, self.company, date_from="2026-10-01")
        first = next(row for row in rows if row["store_id"] == self.store)
        self.assertEqual((first["opening"], first["in"], first["closing"]), (Decimal("-2.125"), Decimal("4"), Decimal("1.875")))
        ledger = self.reports.stock_ledger(self.admin, self.company, self.product)
        self.assertEqual((ledger["total"], ledger["closing"]), (3, Decimal("11.998456")))
        snapshot = next(row for row in ledger["rows"] if row["store_id"] == self.store and row["kind"] == "baseline_opening")
        self.assertEqual((snapshot["out"], snapshot["product_name"], snapshot["store_name"]), (Decimal("2.125"), "Item", "Store"))
        self.assertEqual((snapshot["product_label_source"], snapshot["source_sha256"]), ("baseline", self.source_hash))
        page = self.reports.stock_ledger(self.admin, self.company, self.product, offset=2, limit=1)
        self.assertEqual(page["rows"][0]["balance"], ledger["closing"])
        scoped = self.reports.stock_ledger(self.admin, self.company, self.product, store_id=self.store, date_from="2026-10-01")
        self.assertEqual((scoped["opening"], scoped["closing"], scoped["total"]), (Decimal("-2.125"), Decimal("1.875"), 1))
        self.assertEqual(self.reports.stock(self.admin, self.company, pool="breakage")[0]["closing"], Decimal("4.5"))

    def test_inactive_baseline_accounts_items_and_cash_roles_remain_accessible(self):
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET is_active=0,is_postable=0,kind='general' WHERE id=?", (self.debit,))
            connection.execute("UPDATE products SET is_active=0 WHERE id=?", (self.product,))
        self.assertIn(self.debit, [row["id"] for row in self.reports.accounts(self.admin, self.company)])
        self.assertIn(self.product, [row["id"] for row in self.reports.stock_products(self.admin, self.company)])
        self.assertNotIn(self.product, [row["id"] for row in self.reports.stock_products(self.admin, self.other)])
        self.assertEqual([row["id"] for row in self.reports.city_selectors(self.admin, self.company)["cities"]], ["unassigned", self.city])
        cash = self.reports.cash_book(self.admin, self.company, self.debit)
        self.assertEqual(cash["cash_role_source"], "baseline_snapshot")
        self.assertEqual(cash["closing"], Decimal(self.amount))
        # The shared account was not this other company's configured cash role.
        self.assertEqual(self.reports.cash_accounts(self.admin, self.other)["accounts"], [])

    def test_reports_refuse_pre_cutoff_partial_history_instead_of_false_zero(self):
        calls = ((self.reports.trial_balance, ()), (self.reports.sub_trial_balance, ()),
                 (self.reports.city_balances, ()), (self.reports.ledger, (self.debit,)),
                 (self.reports.cash_book, (self.debit,)), (self.reports.stock, ()),
                 (self.reports.stock_ledger, (self.product,)))
        for method, identifiers in calls:
            with self.subTest(method=method.__name__), self.assertRaisesRegex(ValueError, "earlier documents"):
                method(self.admin, self.company, *identifiers, date_to="2026-09-29")

    def test_dashboard_and_exports_identify_original_snapshot_without_claiming_historical_turnover(self):
        dashboard = self.reports.dashboard(self.admin, self.company)
        self.assertEqual((dashboard["posted"], dashboard["baseline"]["account_count"], dashboard["baseline"]["stock_count"]), (0, 2, 3))
        self.assertTrue(dashboard["baseline"]["manifest"]["synthetic"])
        exporter = ReportExporter(self.path, self.security)
        for report, filters in (("trial_balance", {}), ("sub_trial_balance", {}), ("city_balances", {}),
                                ("account_ledger", {"account_id": self.debit}), ("cash_book", {"account_id": self.debit}),
                                ("stock_quantities", {}), ("item_ledger", {"product_id": self.product})):
            with self.subTest(report=report):
                destination = self.path.parent / (report + ".html")
                exporter.export_html(self.admin, self.company, report, destination, **filters)
                content = destination.read_text(encoding="utf-8")
                self.assertIn("Existing balances and posted transactions", content)
                self.assertIn("2026-09-30", content)
                self.assertIn(self.source_hash, content)
                self.assertIn("does not reconstruct historical turnover", content)
                self.assertNotIn("Posted native transactions only", content)

    def test_baseline_rows_count_towards_export_limit_and_require_live_grants(self):
        with patch.object(native_reports, "MAX_EXPORT_ROWS", 1):
            for report, filters in (("trial_balance", {}), ("sub_trial_balance", {}), ("city_balances", {}),
                                    ("stock_quantities", {}), ("item_ledger", {"product_id": self.product})):
                with self.subTest(report=report), self.assertRaisesRegex(ValueError, "exceeds 1 rows"):
                    self.reports.export_snapshot(self.admin, self.company, report, **filters)
        user = self.security.create_user(self.admin, "reader", "Silver orchard 63 green river")
        actor = self.security.authenticate("reader", "Silver orchard 63 green river")
        self.security.set_company_access(self.admin, user.id, [self.company])
        self.security.set_permissions(self.admin, user.id, "trial_balance", ["read"])
        self.assertEqual(len(self.reports.trial_balance(actor, self.company)["rows"]), 2)
        with self.assertRaises(AuthorizationError):
            self.reports.trial_balance(actor, self.other)
        self.security.set_permissions(self.admin, user.id, "trial_balance", [])
        with self.assertRaises(AuthorizationError):
            self.reports.export_snapshot(actor, self.company, "trial_balance")


if __name__ == "__main__":
    unittest.main()
