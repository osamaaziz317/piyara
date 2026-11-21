"""Cash/account report separation using synthetic posted native transactions."""

from decimal import Context, Decimal, localcontext
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma import native_reports
from bifma.native_reports import NativeReports
from bifma.native_schema import native_transaction
from bifma.security import AuthorizationError
from tests.test_transactions import TransactionFixture


class CashReportFixture(TransactionFixture):
    def setUp(self):
        super().setUp()
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET cash_account_id=? WHERE id=?", (self.accounts["cash"], self.company))
        self.reports = NativeReports(self.path, security=self.security)

    def invoice(self, kind="cash_purchase", **options):
        base = kind.removeprefix("cash_")
        values = self.trade_values(base, **options)
        if kind.startswith("cash_"):
            values["account_id"] = self.accounts["cash"]
        return self.post(self.save(kind, values))

    def reader(self, modules):
        user = self.security.create_user(self.actor, "cash_reader", "Copper fields 94 river violet")
        self.security.set_company_access(self.actor, user.id, [self.company])
        for module in modules:
            self.security.set_permissions(self.actor, user.id, module, ["read"])
        return user, self.security.authenticate("cash_reader", "Copper fields 94 river violet")

    def report(self, family="sales", actor=None, **filters):
        return self.reports.trade_detail(actor or self.actor, self.company, family, **filters)


class CashReportTests(CashReportFixture, unittest.TestCase):
    def test_mixed_modes_returns_reversals_keep_exact_signs_and_own_dates(self):
        self.opening(quantity="100")
        for family, base in (("sales", "sale"), ("purchases", "purchase")):
            with self.subTest(family=family):
                account = self.invoice(base, quantity="3", rate="5")
                self.invoice(base + "_return", quantity="1", rate="5", source=account["lines"][0]["id"], when="2026-01-03")
                cash = self.invoice("cash_" + base, quantity="2", rate="10")
                returned = self.invoice("cash_" + base + "_return", quantity="1", rate="10", source=cash["lines"][0]["id"], when="2026-01-03")
                reversed_return = self.reverse(returned, "2026-01-04")
                reversed_invoice = self.reverse(cash, "2026-01-05")
                default = self.report(family)
                self.assertEqual((default["payment_mode"], default["total"], default["totals"]["net"]), ("account", 2, 10))
                result = self.report(family, payment_mode="cash", limit=1, offset=1)
                self.assertEqual((result["total"], result["document_count"], len(result["rows"])), (4, 4, 1))
                self.assertEqual(result["rows"][0]["document_id"], returned["id"])
                self.assertEqual(result["totals"], {"invoice": 20, "return": -10, "reversal": -10, "net": 0})
                self.assertEqual(result["quantity_totals"][0]["net"], 0)
                full = self.report(family, payment_mode="cash")
                self.assertEqual([r["document_id"] for r in full["rows"]], [cash["id"], returned["id"], reversed_return["id"], reversed_invoice["id"]])
                self.assertEqual([r["signed_net_amount"] for r in full["rows"]], [20, -10, 10, -20])
                self.assertTrue(all(r["payment_mode"] == "cash" for r in full["rows"]))
                self.assertEqual(self.report(family, payment_mode="cash", date_from="2026-01-04", date_to="2026-01-04")["totals"]["net"], 10)
                self.assertEqual(self.report(family, payment_mode="cash", kind="invoice")["total"], 2)
                self.assertEqual(self.report(family, payment_mode="cash", kind="return")["total"], 2)
                combined = self.report(family, payment_mode="all")
                self.assertEqual(combined["totals"], {"invoice": 35, "return": -15, "reversal": -10, "net": 10})
                self.assertEqual(combined["document_totals"], combined["totals"])
                self.assertEqual(combined["quantity_totals"][0]["net"], 2)

    def test_standard_kind_using_cash_account_is_still_account_mode(self):
        values = self.trade_values("purchase", account_id=self.accounts["cash"])
        original = self.post(self.save("purchase", values))
        self.assertEqual(self.report("purchases", payment_mode="cash")["total"], 0)
        rows = self.report("purchases")["rows"]
        self.assertEqual([(r["document_id"], r["payment_mode"]) for r in rows], [(original["id"], "account")])

    def test_permissions_depend_on_mode_and_activity_even_when_result_is_empty(self):
        user, actor = self.reader([])
        cases = (
            ("sales", "invoice", "account", ("sales",)),
            ("sales", "invoice", "cash", ("counter_sales",)),
            ("sales", "invoice", "all", ("sales", "counter_sales")),
            ("sales", "return", "all", ("sales_returns",)),
            ("sales", "all", "cash", ("counter_sales", "sales_returns")),
            ("sales", "all", "all", ("sales", "counter_sales", "sales_returns")),
            ("purchases", "invoice", "cash", ("purchases",)),
            ("purchases", "return", "cash", ("purchase_returns",)),
            ("purchases", "all", "all", ("purchases", "purchase_returns")),
        )
        modules = ("sales", "counter_sales", "sales_returns", "purchases", "purchase_returns")
        for family, kind, mode, required in cases:
            with self.subTest(family=family, kind=kind, mode=mode):
                for module in modules:
                    self.security.set_permissions(self.actor, user.id, module, ["read"] if module in required else [])
                token = "sales_detail" if family == "sales" else "purchase_detail"
                calls = (
                    lambda: self.report(family, actor=actor, kind=kind, payment_mode=mode),
                    lambda: self.reports.trade_selectors(actor, self.company, family, kind, payment_mode=mode),
                    lambda: self.reports.export_snapshot(actor, self.company, token, kind=kind, payment_mode=mode),
                )
                for call in calls:
                    call()
                snapshot = calls[-1]()
                self.assertEqual(snapshot["required_modules"], required)
                for denied in required:
                    self.security.set_permissions(self.actor, user.id, denied, [])
                    for call in calls:
                        with self.assertRaises(AuthorizationError):
                            call()
                    self.security.set_permissions(self.actor, user.id, denied, ["read"])
        with self.assertRaises(AuthorizationError):
            self.reports.trade_detail(actor, self.other_company, "purchases", payment_mode="cash", kind="invoice")

    def test_cash_selectors_keep_inactive_history_but_exclude_other_mode(self):
        self.invoice("purchase")
        product, unit, store, replacement_cash = [str(uuid4()) for _ in range(4)]
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO units(id,code,name) VALUES (?,'BOX','Saved box')", (unit,))
            connection.execute("INSERT INTO stores(id,code,name) VALUES (?,'CS','Saved counter store')", (store,))
            connection.execute("INSERT INTO company_stores(company_id,store_id) VALUES (?,?)", (self.company, store))
            connection.execute("INSERT INTO products(id,code,name,small_unit_id,purchase_account_id,sale_account_id) VALUES (?,'CASH-ITEM','Saved cash item',?,?,?)", (product, unit, self.accounts["purchases"], self.accounts["sales"]))
        values = self.trade_values("purchase", account_id=self.accounts["cash"])
        values["lines"][0].update(product_id=product, store_id=store)
        self.post(self.save("cash_purchase", values))
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO accounts(id,code,name,kind) VALUES (?,'CASH2','Replacement cash','cash')", (replacement_cash,))
            connection.execute("UPDATE companies SET cash_account_id=? WHERE id=?", (replacement_cash, self.company))
            for table, identifier in (("accounts", self.accounts["cash"]), ("products", product), ("stores", store), ("units", unit)):
                connection.execute(f"UPDATE {table} SET name='Renamed current label',is_active=0 WHERE id=?", (identifier,))
        selectors = self.reports.trade_selectors(self.actor, self.company, "purchases", payment_mode="cash")
        self.assertEqual({r["id"] for r in selectors["products"]}, {product})
        self.assertEqual({r["id"] for r in selectors["stores"]}, {store})
        self.assertEqual({r["id"] for r in selectors["accounts"]}, {self.accounts["cash"]})
        self.assertTrue(all(r["is_active"] == 0 for rows in selectors.values() for r in rows))
        row = self.report("purchases", payment_mode="cash", account_id=self.accounts["cash"], product_id=product, store_id=store)["rows"][0]
        self.assertEqual((row["account_name"], row["product_name"], row["store_name"], row["unit_name"]), ("Cash", "Saved cash item", "Saved counter store", "Saved box"))
        with self.assertRaises(ValueError):
            self.report("purchases", payment_mode="account", product_id=product)
        self.assertEqual(len(self.reports.trade_selectors(self.actor, self.company, "purchases", payment_mode="all")["products"]), 2)

    def test_cash_amounts_header_omission_and_export_cap_remain_exact(self):
        values = self.trade_values("purchase", quantity="9007199254740993.123456", rate="0.123456", account_id=self.accounts["cash"], discount="0.01")
        original = self.post(self.save("cash_purchase", values))
        with localcontext(Context(prec=3)):
            result = self.report("purchases", payment_mode="cash")
        self.assertEqual(result["totals"]["net"], Decimal(original["lines"][0]["net_amount"]))
        self.assertEqual(result["document_totals"]["net"], Decimal(original["total_amount"]))
        self.assertIsNone(self.report("purchases", payment_mode="cash", product_id=self.product)["document_totals"])
        self.invoice("cash_purchase", rate="0")
        with patch.object(native_reports, "MAX_EXPORT_ROWS", 1), self.assertRaisesRegex(ValueError, "exceeds"):
            self.reports.export_snapshot(self.actor, self.company, "purchase_detail", payment_mode="cash")

    def test_invalid_payment_modes_fail_in_report_selectors_and_export(self):
        for value in (None, "", "credit", "CASH", True, 0, [], {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "payment mode"):
                    self.report(payment_mode=value)
                with self.assertRaisesRegex(ValueError, "payment mode"):
                    self.reports.trade_selectors(self.actor, self.company, "sales", payment_mode=value)
                with self.assertRaisesRegex(ValueError, "payment mode"):
                    self.reports.export_snapshot(self.actor, self.company, "sales_detail", payment_mode=value)


if __name__ == "__main__":
    unittest.main()
