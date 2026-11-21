"""New transactions continue synthetic imported balances without changing them."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from threading import Barrier
import unittest

from bifma.masters import MasterService, MasterValidationError
from bifma.native_schema import native_session, native_transaction
from bifma.security import AuthorizationError
from bifma.transactions import StockUnavailable, TransactionError
from tests.test_transactions import TransactionFixture


class BaselinePostingTests(TransactionFixture, unittest.TestCase):
    def baseline(self, quantity="10", *, pool="regular", number="00260947"):
        with native_transaction(self.path) as connection:
            connection.execute(
                "INSERT INTO baseline_stock_balances(company_id,store_id,product_id,pool,quantity,historical_value,base_unit_id,labels_json) "
                "VALUES(?,?,?,?,?,'100',?,'{}')",
                (self.company, self.store, self.product, pool, quantity, self.unit),
            )
            for account, amount in (("customer", "9007199254740993.25"), ("equity", "-9007199254740993.25")):
                connection.execute(
                    "INSERT INTO baseline_account_balances(company_id,account_id,balance,labels_json) VALUES(?,?,?,'{}')",
                    (self.company, self.accounts[account], amount),
                )
            connection.execute(
                "INSERT INTO baseline_document_numbers(company_id,kind,document_number,source_table,source_key) VALUES(?,'sale',?,'SMST','1')",
                (self.company, number),
            )
            connection.execute(
                "INSERT INTO baseline_imports(singleton,source_sha256,cutoff_date,manifest_json) VALUES(1,?,'2026-01-01',?)",
                ("a" * 64, json.dumps({"synthetic": True})),
            )

    def test_sale_uses_imported_stock_and_preserves_closing_rows(self):
        self.baseline()
        self.assertEqual(self.quantity(), 10)
        sale = self.post(self.save("sale", self.trade_values(quantity="3")))
        self.assertEqual(self.quantity(), 7)
        self.reverse(sale)
        self.assertEqual(self.quantity(), 10)
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT quantity FROM baseline_stock_balances").fetchone()[0], "10")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 2)
            # Baseline stock never posts an extra inventory journal.
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0], 2)

    def test_negative_baseline_allows_replenishment_but_not_further_outward_stock(self):
        self.baseline("-5")
        self.post(self.save("purchase", self.trade_values("purchase", quantity="3")))
        self.assertEqual(self.quantity(), -2)
        sale = self.save("sale", self.trade_values())
        with self.assertRaises(StockUnavailable):
            self.post(sale)
        self.post(self.save("purchase", self.trade_values("purchase", quantity="4", when="2026-01-03")))
        self.assertEqual(self.quantity(), 2)
        # A sale before the replenishment still fails despite a positive closing.
        with self.assertRaises(StockUnavailable):
            self.post(sale)
        self.post(self.save("sale", self.trade_values(quantity="2", when="2026-01-03")))
        self.assertEqual(self.quantity(), 0)

    def test_backdated_sale_cannot_consume_stock_already_used_later(self):
        self.baseline("5")
        self.post(self.save("sale", self.trade_values(quantity="4", when="2026-01-10")))
        self.post(self.save("purchase", self.trade_values("purchase", quantity="10", when="2026-01-20")))
        with self.assertRaises(StockUnavailable):
            self.post(self.save("sale", self.trade_values(quantity="2", when="2026-01-05")))
        self.assertEqual(self.quantity(), 11)

    def test_concurrent_sales_cannot_oversell_imported_stock(self):
        self.baseline("1")
        drafts = [self.save("sale", self.trade_values()) for _ in range(2)]
        barrier = Barrier(2)
        def attempt(draft):
            barrier.wait(timeout=5)
            try:
                return self.post(draft)["status"]
            except StockUnavailable:
                return "insufficient"
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(attempt, drafts)), ["insufficient", "posted"])
        self.assertEqual(self.quantity(), 0)

    def test_baseline_scopes_company_store_pool_and_date(self):
        self.baseline("4", pool="breakage")
        self.assertEqual((self.quantity(), self.quantity("breakage")), (0, 4))
        self.assertEqual(self.service.stock_balance(self.actor, self.other_company, pool="breakage"), [])
        self.assertEqual(self.service.stock_balance(self.actor, self.company, store_id=self.other_store, pool="breakage"), [])
        self.assertEqual(self.service.stock_balance(self.actor, self.company, pool="breakage", date_to="2026-01-01")[0]["quantity"], "4")
        with self.assertRaisesRegex(TransactionError, "Stock quantities report"):
            self.service.stock_balance(self.actor, self.company, date_to="2025-12-31")
        with self.assertRaises(StockUnavailable):
            self.post(self.save("sale", self.trade_values()))
        self.post(self.save("breakage_dispatch", self.trade_values("breakage_dispatch", quantity="2")))
        self.assertEqual(self.quantity("breakage"), 2)

    def test_cutoff_refuses_new_drafts_without_touching_history(self):
        self.baseline()
        for when in ("2025-12-31", "2026-01-01"):
            with self.subTest(when=when), self.assertRaises(TransactionError):
                self.save("purchase", self.trade_values("purchase", when=when))
        self.post(self.save("purchase", self.trade_values("purchase")))
        self.assertEqual(self.quantity(), 11)

    def test_number_suggestions_continue_history_include_drafts_and_check_permissions(self):
        self.baseline()
        self.assertEqual(self.service.next_document_number(self.actor, self.company, "sale"), "00260948")
        values = self.trade_values()
        values["document_number"] = "00260947"
        with self.assertRaisesRegex(TransactionError, "original history"):
            self.save("sale", values)
        values["document_number"] = "00260948"
        self.save("sale", values)
        self.assertEqual(self.service.next_document_number(self.actor, self.company, "sale"), "00260949")
        self.assertEqual(self.service.next_document_number(self.actor, self.other_company, "sale"), "1")
        self.assertEqual(self.service.next_document_number(self.actor, self.company, "purchase"), "1")
        staff = self.security.create_user(self.actor, "reader", "Quiet amber lakes reflect mountains 97")
        self.security.set_company_access(self.actor, staff.id, [self.company])
        self.security.set_permissions(self.actor, staff.id, "sales", ["read"])
        reader = self.security.authenticate("reader", "Quiet amber lakes reflect mountains 97")
        with self.assertRaises(AuthorizationError):
            self.service.next_document_number(reader, self.company, "sale")

    def test_numbering_metadata_preserves_original_and_native_width_and_scope(self):
        self.baseline(number="000527")
        original = {"last": "000527", "next": "000528"}
        for _ in range(2):
            self.assertEqual(self.service.document_numbering(self.actor, self.company, "sale"), original)
        for number in ("000526", "SALE-999999"):
            values = self.trade_values(document_number=number)
            self.save("sale", values)
        self.assertEqual(self.service.document_numbering(self.actor, self.company, "sale"), original)
        newer = self.save("sale", self.trade_values(document_number="00528"))
        self.assertEqual(self.service.document_numbering(self.actor, self.company, "sale"),
                         {"last": "00528", "next": "00529"})
        self.assertEqual(self.service.document_numbering(self.actor, self.company, "sale", action="update"),
                         {"last": "00528", "next": "00529"})
        values = self.trade_values(document_number="000999")
        self.service.save_draft(self.actor, self.company, "sale", values,
                                document_id=newer["id"], expected_revision=newer["revision"])
        self.assertEqual(self.service.document_numbering(self.actor, self.company, "sale"),
                         {"last": "000999", "next": "001000"})
        for company, kind in ((self.other_company, "sale"), (self.company, "cash_payment")):
            self.assertEqual(self.service.document_numbering(self.actor, company, kind),
                             {"last": "", "next": "1"})
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT document_number FROM baseline_document_numbers").fetchone()[0], "000527")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 3)

    def test_numbering_metadata_rechecks_distinct_create_update_and_company_grants(self):
        self.baseline()
        password = "Quiet amber lakes reflect mountains 97"
        staff = self.security.create_user(self.actor, "number_operator", password)
        operator = self.security.authenticate("number_operator", password)
        self.security.set_company_access(self.actor, staff.id, [self.company])
        self.security.set_permissions(self.actor, staff.id, "sales", ["read", "create"])
        expected = {"last": "00260947", "next": "00260948"}
        self.assertEqual(self.service.document_numbering(operator, self.company, "sale"), expected)
        with self.assertRaises(AuthorizationError):
            self.service.document_numbering(operator, self.company, "sale", action="update")
        self.security.set_permissions(self.actor, staff.id, "sales", ["read", "update"])
        self.assertEqual(self.service.document_numbering(operator, self.company, "sale", action="update"), expected)
        with self.assertRaises(AuthorizationError):
            self.service.document_numbering(operator, self.company, "sale")
        with self.assertRaises(AuthorizationError):
            self.service.next_document_number(operator, self.company, "sale")
        self.security.set_permissions(self.actor, staff.id, "sales", ["read"])
        with self.assertRaises(AuthorizationError):
            self.service.document_numbering(operator, self.company, "sale", action="update")
        self.security.set_permissions(self.actor, staff.id, "sales", ["create", "update"])
        self.security.set_company_access(self.actor, staff.id, [])
        for action in ("create", "update"):
            with self.subTest(action=action), self.assertRaises(AuthorizationError):
                self.service.document_numbering(operator, self.company, "sale", action=action)
        for action in ("read", "post", None):
            with self.subTest(action=action), self.assertRaises(TransactionError):
                self.service.document_numbering(self.actor, self.company, "sale", action=action)

    def test_imported_unit_cannot_be_reinterpreted_and_unknown_prices_stay_unknown(self):
        self.baseline()
        masters = MasterService(self.path, security=self.security, clock=self.clock)
        unit = masters.create(self.actor, "units", {"code": "NEW", "name": "Different unit"})
        product = masters.get(self.actor, "products", self.product)
        with self.assertRaisesRegex(MasterValidationError, "base unit cannot change"):
            masters.update(self.actor, "products", self.product, {"small_unit_id": unit["id"], "unit_factor": "1"}, product["version"])
        product = masters.update(self.actor, "products", self.product,
                                 {"sale_rate": "", "purchase_rate": None, "unit_factor": "1"}, product["version"])
        self.assertEqual((product["sale_rate_known"], product["purchase_rate_known"]), (0, 0))
        product = masters.update(self.actor, "products", self.product, {"name": "Renamed"}, product["version"])
        self.assertEqual((product["sale_rate_known"], product["purchase_rate_known"]), (0, 0))
        product = masters.update(self.actor, "products", self.product, {"sale_rate": "0", "purchase_rate": "12.25"}, product["version"])
        self.assertEqual((product["sale_rate_known"], product["purchase_rate_known"]), (1, 1))
        self.assertEqual(Decimal(product["purchase_rate"]), Decimal("12.25"))


if __name__ == "__main__":
    unittest.main()
