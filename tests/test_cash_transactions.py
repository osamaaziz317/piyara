"""Explicit cash trade namespaces and immutable full-settlement semantics."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma.masters import MasterService
from bifma.native_schema import native_session, native_transaction
from bifma.security import AuthenticationError, AuthorizationError
from bifma.transactions import RevisionConflict, StockUnavailable, TransactionError
from tests.test_transactions import TransactionFixture


class CashTransactionTests(TransactionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.cash2 = str(uuid4())
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO accounts(id,code,name,kind) VALUES(?,?,?,'cash')", (self.cash2, "OTHER-CASH", "Other cash"))
            connection.execute("UPDATE companies SET cash_account_id=? WHERE id=?", (self.accounts["cash"], self.company))

    def cash_values(self, kind="cash_sale", **kwargs):
        return self.trade_values(kind, account_id=self.accounts["cash"], **kwargs)

    def reconfigure(self, identifier):
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET cash_account_id=? WHERE id=?", (identifier, self.company))

    def test_all_four_cash_directions_full_money_stock_and_number_namespaces(self):
        self.opening()
        invoices = {}
        for ordinary, cash, side, direction in (("sale", "cash_sale", "debit", "out"), ("purchase", "cash_purchase", "credit", "in")):
            values = self.trade_values(ordinary)
            values["document_number"] = "123"
            self.post(self.save(ordinary, values))
            values = self.cash_values(cash, quantity="2", rate="19.75")
            values.update(document_number="123", counterparty_name="  گاہک / Buyer  ", counterparty_mobile="  +92 000  ")
            values["lines"][0]["discount_percent"] = "10"
            invoice = self.post(self.save(cash, values))
            invoices[cash] = invoice
            self.assertEqual(invoice["total_amount"], "35.55")
            cash_line = next(row for row in invoice["journal_lines"] if row["account_id"] == self.accounts["cash"])
            self.assertEqual(cash_line[side], "35.55")
            self.assertEqual(invoice["stock_movements"][0]["direction"], direction)
            self.assertEqual(invoice["snapshot"]["cash_settlement"], {"mode": "cash", "cash_account_id": self.accounts["cash"], "counterparty_name": "گاہک / Buyer", "counterparty_mobile": "+92 000"})
            with self.assertRaises(TransactionError):
                self.save(cash, values)
            returned_kind = cash + "_return"
            values = self.cash_values(returned_kind, quantity="1", rate="19.75", source=invoice["lines"][0]["id"])
            values["lines"][0]["discount_percent"] = "10"
            values["document_number"] = "123"
            returned = self.post(self.save(returned_kind, values))
            returned_cash = next(row for row in returned["journal_lines"] if row["account_id"] == self.accounts["cash"])
            self.assertEqual(returned_cash["credit" if side == "debit" else "debit"], "17.78")
            self.assertEqual(returned["stock_movements"][0]["direction"], "in" if direction == "out" else "out")
            self.assertEqual(returned["snapshot"]["cash_settlement"], invoice["snapshot"]["cash_settlement"])
            self.assertEqual(returned["snapshot"]["return_of"]["kind"], cash)
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents WHERE kind IN ('cash_receipt','cash_payment')").fetchone()[0], 0)

    def test_zero_value_cash_goods_post_stock_without_fake_receipt_or_journal(self):
        self.opening()
        for kind in ("cash_sale", "cash_purchase"):
            invoice = self.post(self.save(kind, self.cash_values(kind, rate="0")))
            self.assertEqual((invoice["total_amount"], invoice["journal_lines"]), ("0", []))
            self.assertEqual(len(invoice["stock_movements"]), 1)
            values = self.cash_values(kind + "_return", rate="0", source=invoice["lines"][0]["id"])
            returned = self.post(self.save(kind + "_return", values))
            self.assertEqual(returned["total_amount"], "0")

    def test_original_binding_is_required_and_reconfiguration_forces_review(self):
        for account in (self.cash2, self.accounts["bank"], self.accounts["customer"]):
            values = self.cash_values()
            values["account_id"] = account
            with self.subTest(account=account), self.assertRaises(TransactionError):
                self.save("cash_sale", values)
        self.reconfigure(None)
        with self.assertRaisesRegex(TransactionError, "configured cash account"):
            self.save("cash_sale", self.cash_values())
        self.reconfigure(self.accounts["cash"])
        for change in ("is_active=0", "is_postable=0", "kind='bank'"):
            with native_transaction(self.path) as connection:
                connection.execute(f"UPDATE accounts SET {change} WHERE id=?", (self.accounts["cash"],))
            with self.subTest(change=change), self.assertRaises(TransactionError):
                self.save("cash_sale", self.cash_values())
            with native_transaction(self.path) as connection:
                connection.execute("UPDATE accounts SET is_active=1,is_postable=1,kind='cash' WHERE id=?", (self.accounts["cash"],))
        self.opening()
        draft = self.save("cash_sale", self.cash_values())
        self.reconfigure(self.cash2)
        with self.assertRaisesRegex(TransactionError, "resave and review"):
            self.post(draft)
        self.assertEqual(self.service.get(self.actor, self.company, draft["id"])["stock_movements"], [])
        values = dict(draft["values"], account_id=self.cash2)
        reviewed = self.service.save_draft(self.actor, self.company, "cash_sale", values, draft["id"], draft["revision"])
        self.assertEqual(self.post(reviewed)["snapshot"]["cash_settlement"]["cash_account_id"], self.cash2)

    def test_cash_returns_keep_original_account_contacts_and_release_caps_only_when_reversed(self):
        self.opening()
        original = self.post(self.save("cash_sale", self.cash_values(quantity="2", counterparty_name="Original buyer")))
        self.reconfigure(self.cash2)
        values = self.cash_values("cash_sale_return", quantity="2", source=original["lines"][0]["id"])
        draft = self.save("cash_sale_return", values)
        self.reconfigure(None)
        returned = self.post(draft)
        self.assertEqual(returned["account_id"], self.accounts["cash"])
        self.assertEqual(returned["snapshot"]["cash_settlement"], original["snapshot"]["cash_settlement"])
        with self.assertRaisesRegex(TransactionError, "quantity exceeds"):
            self.save("cash_sale_return", values)
        with self.assertRaisesRegex(TransactionError, "Reverse active linked returns"):
            self.reverse(original)
        reversed_return = self.reverse(returned)
        self.assertEqual(reversed_return["snapshot"]["cash_settlement"], returned["snapshot"]["cash_settlement"])
        self.assertEqual(reversed_return["snapshot"]["reversal_of"]["kind"], "cash_sale_return")
        self.post(self.save("cash_sale_return", dict(values, document_number="Replacement return", business_date="2026-01-03")))

    def test_cash_returns_cannot_cross_modes_or_change_contact_rate_or_account(self):
        self.opening()
        original = self.post(self.save("cash_sale", self.cash_values()))
        values = self.cash_values("cash_sale_return", source=original["lines"][0]["id"])
        with self.assertRaisesRegex(TransactionError, "corresponding invoice"):
            self.save("sale_return", values)
        with self.assertRaisesRegex(TransactionError, "corresponding invoice"):
            self.save("cash_purchase_return", values)
        for field, value in (("counterparty_name", "Changed"), ("counterparty_mobile", "999"), ("amount_received", "10"), ("cash_settlement", {})):
            with self.subTest(field=field), self.assertRaisesRegex(TransactionError, "Unsupported fields"):
                self.save("cash_sale_return", dict(values, **{field: value}))
        bad = dict(values, account_id=self.cash2)
        with self.assertRaisesRegex(TransactionError, "corresponding invoice"):
            self.save("cash_sale_return", bad)
        values["lines"][0]["rate"] = "11"
        with self.assertRaisesRegex(TransactionError, "must match"):
            self.save("cash_sale_return", values)

    def test_cash_return_rounding_cap_and_staff_adjustments(self):
        self.opening()
        original = self.post(self.save("cash_sale", self.cash_values(quantity="2", rate="0.6", whole=True)))
        values = self.cash_values("cash_sale_return", quantity="1", rate="0.6", whole=True, source=original["lines"][0]["id"])
        self.post(self.save("cash_sale_return", values))
        with self.assertRaisesRegex(TransactionError, "Returned amounts exceed"):
            self.save("cash_sale_return", dict(values, document_number="Second partial"))
        adjusted = self.post(self.save("cash_sale", self.cash_values(discount="1")))
        with self.assertRaisesRegex(TransactionError, "original total"):
            self.save("cash_sale_return", self.cash_values("cash_sale_return", source=adjusted["lines"][0]["id"]))
        returned = self.post(self.save("cash_sale_return", self.cash_values(
            "cash_sale_return", source=adjusted["lines"][0]["id"], discount="1")))
        self.assertEqual(returned["total_amount"], adjusted["total_amount"])

    def test_direct_cash_reversal_preserves_labels_binding_and_exact_effects(self):
        self.opening()
        original = self.post(self.save("cash_sale", self.cash_values(counterparty_name="Saved buyer")))
        self.reconfigure(self.cash2)
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET name='Renamed cash' WHERE id=?", (self.accounts["cash"],))
        reversed_document = self.reverse(original)
        self.assertEqual(reversed_document["snapshot"]["accounts"], original["snapshot"]["accounts"])
        self.assertEqual(reversed_document["snapshot"]["cash_settlement"], original["snapshot"]["cash_settlement"])
        self.assertEqual(reversed_document["snapshot"]["reversal_of"]["kind"], "cash_sale")
        expected = {(row["account_id"], row["credit"], row["debit"]) for row in original["journal_lines"]}
        self.assertEqual({(row["account_id"], row["debit"], row["credit"]) for row in reversed_document["journal_lines"]}, expected)
        self.assertEqual(self.quantity(), Decimal(10))
        with self.assertRaises(RevisionConflict):
            self.reverse(original)

    def test_counter_permissions_are_separate_live_and_company_scoped(self):
        self.opening()
        user = self.security.create_user(self.actor, "cash_clerk", "Cobalt bridges welcome sunrise 83")
        clerk = self.security.authenticate("cash_clerk", "Cobalt bridges welcome sunrise 83")
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.set_permissions(self.actor, user.id, "sales", ["read", "create", "post"])
        with self.assertRaises(AuthorizationError):
            self.save("cash_sale", self.cash_values(), clerk)
        self.security.set_permissions(self.actor, user.id, "counter_sales", ["read", "create", "post"])
        masters = MasterService(self.path, self.security)
        self.assertEqual(masters.lookup(clerk, self.company, "counter_sales")["companies"][0]["cash_account_id"], self.accounts["cash"])
        draft = self.save("cash_sale", self.cash_values(), clerk)
        with self.assertRaises(AuthorizationError):
            self.service.list_documents(clerk, self.other_company, "cash_sale")
        with self.assertRaisesRegex(TransactionError, "unavailable"):
            self.service.get(clerk, self.other_company, draft["id"])
        self.security.set_permissions(self.actor, user.id, "counter_sales", ["read"])
        with self.assertRaises(AuthorizationError):
            self.post(draft, clerk)
        posted = self.post(draft)
        self.security.set_permissions(self.actor, user.id, "counter_sales", [])
        with self.assertRaises(AuthorizationError):
            self.service.get(clerk, self.company, posted["id"])
        with self.assertRaises(AuthorizationError):
            self.service.list_documents(clerk, self.company, "cash_sale")
        self.security.set_permissions(self.actor, user.id, "counter_sales", ["read"])
        self.security.logout(clerk)
        with self.assertRaises(AuthenticationError):
            self.service.get(clerk, self.company, posted["id"])

    def test_cash_purchase_reuses_purchases_permission_and_cash_return_uses_return_permission(self):
        user = self.security.create_user(self.actor, "purchaser", "Cobalt bridges welcome sunrise 83")
        clerk = self.security.authenticate("purchaser", "Cobalt bridges welcome sunrise 83")
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.set_permissions(self.actor, user.id, "purchases", ["read", "create", "post"])
        purchased = self.post(self.save("cash_purchase", self.cash_values("cash_purchase"), clerk), clerk)
        values = self.cash_values("cash_purchase_return", source=purchased["lines"][0]["id"])
        with self.assertRaises(AuthorizationError):
            self.save("cash_purchase_return", values, clerk)
        self.security.set_permissions(self.actor, user.id, "purchase_returns", ["create", "post"])
        self.post(self.save("cash_purchase_return", values, clerk), clerk)

    def test_cash_fields_fail_closed_and_financial_numbers_stay_exact(self):
        for field, value in (("counterparty_name", "x" * 151), ("counterparty_mobile", "x" * 81), ("counterparty_name", "bad\x00name"), ("counterparty_mobile", 123), ("amount_received", "10"), ("payment_mode", "cash")):
            with self.subTest(field=field), self.assertRaises(TransactionError):
                self.save("cash_sale", self.cash_values(**{field: value}))
        for kind in ("sale", "purchase", "cash_sale_return"):
            with self.subTest(kind=kind), self.assertRaisesRegex(TransactionError, "Unsupported fields"):
                self.save(kind, self.cash_values(kind, counterparty_name="Unsupported"))
        for value in (float("nan"), "Infinity", "-1"):
            with self.subTest(rate=value), self.assertRaises(ValueError):
                self.save("cash_purchase", self.cash_values("cash_purchase", rate=value))
        purchased = self.post(self.save("cash_purchase", self.cash_values("cash_purchase", rate="9007199254740993.01")))
        self.assertEqual(purchased["total_amount"], "9007199254740993.01")

    def test_cash_posting_failure_and_concurrent_retry_never_duplicate_effects(self):
        self.opening()
        draft = self.save("cash_sale", self.cash_values())
        write = self.service._write_effects

        def fail_after_effects(*args):
            write(*args)
            raise RuntimeError("Synthetic interrupted effects")

        with patch.object(self.service, "_write_effects", fail_after_effects), self.assertRaises(RuntimeError):
            self.post(draft)
        unchanged = self.service.get(self.actor, self.company, draft["id"])
        self.assertEqual((unchanged["status"], unchanged["journal_lines"], unchanged["stock_movements"]), ("draft", [], []))
        barrier = Barrier(2)

        def post_once():
            barrier.wait(timeout=5)
            try:
                return self.post(draft)["status"]
            except RevisionConflict:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: post_once(), range(2)))
        self.assertCountEqual(outcomes, ["posted", "rejected"])
        self.assertEqual(self.quantity(), Decimal(9))

    def test_cash_sale_availability_and_backdating_still_fail_closed(self):
        self.opening(quantity="1")
        self.post(self.save("cash_sale", self.cash_values(when="2026-01-03")))
        draft = self.save("cash_sale", self.cash_values(when="2026-01-02"))
        with self.assertRaises(StockUnavailable):
            self.post(draft)


if __name__ == "__main__":
    unittest.main()
