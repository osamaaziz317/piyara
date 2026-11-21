"""Standalone breakage postings use synthetic native records, never history."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import sqlite3
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma.native_schema import native_session, native_transaction
from bifma.security import AuthorizationError
from bifma.transactions import RevisionConflict, StockUnavailable, TransactionError
from tests.test_transactions import TransactionFixture


class BreakageTransactionTests(TransactionFixture, unittest.TestCase):
    def receive(self, quantity="10", rate="10", when="2026-01-01", **headers):
        return self.post(self.save("breakage_receive", self.trade_values(
            "breakage_receive", quantity=quantity, rate=rate, when=when, **headers)))

    def assert_balanced(self, document, total):
        self.assertEqual(document["total_amount"], str(total))
        self.assertEqual(sum((Decimal(row["debit"]) for row in document["journal_lines"]), Decimal(0)), Decimal(total))
        self.assertEqual(sum((Decimal(row["credit"]) for row in document["journal_lines"]), Decimal(0)), Decimal(total))

    def test_receive_uses_sale_accounts_and_applies_deduction_once_across_groups(self):
        second_account, second_product = str(uuid4()), str(uuid4())
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO accounts(id,code,name) VALUES(?, 'SALES2', 'Second sales account')", (second_account,))
            connection.execute(
                "INSERT INTO products(id,code,name,small_unit_id,sale_account_id,purchase_account_id) VALUES(?, 'B', 'Second product', ?, ?, ?)",
                (second_product, self.unit, second_account, self.accounts["purchases"]))
        values = self.trade_values("breakage_receive", quantity="2", rate="10", breakage="3.4")
        values["lines"][0]["discount_percent"] = "10"
        values["lines"].append({"product_id": second_product, "store_id": self.store,
                                "quantity": "1", "rate": "20", "discount_percent": "20"})
        draft = self.save("breakage_receive", values)
        self.assertEqual(draft["stock_movements"], [])
        received = self.post(draft)
        self.assert_balanced(received, "30.6")
        amounts = {row["account_id"]: (row["debit"], row["credit"]) for row in received["journal_lines"]}
        self.assertEqual(amounts, {self.accounts["customer"]: ("0", "30.6"),
                                  self.accounts["sales"]: ("16.2", "0"), second_account: ("14.4", "0")})
        self.assertEqual(received["calculation"]["raw_totals"]["raw_subtotal"], "34")
        self.assertEqual(received["calculation"]["raw_totals"]["header_total"], "30.6")
        self.assertEqual({row["account_id"] for row in received["calculation"]["raw_totals"]["allocations"]},
                         {self.accounts["sales"], second_account})
        self.assertEqual((self.quantity(), self.quantity("breakage")), (0, 3))
        self.assertEqual(len(received["stock_movements"]), 2)
        for movement in received["stock_movements"]:
            self.assertEqual((movement["pool"], movement["direction"], movement["value"], movement["valuation_basis"]),
                             ("breakage", "in", "0", "unvalued"))
        self.assertTrue(all(line["source_line_id"] is None for line in received["lines"]))

    def test_dispatch_uses_purchase_account_and_decreases_only_breakage(self):
        self.opening(quantity="7")
        self.receive(quantity="3")
        values = self.trade_values("breakage_dispatch", quantity="1", rate="5")
        values["lines"][0]["discount_percent"] = "10"
        dispatched = self.post(self.save("breakage_dispatch", values))
        self.assert_balanced(dispatched, "4.5")
        self.assertEqual({row["account_id"]: (row["debit"], row["credit"]) for row in dispatched["journal_lines"]},
                         {self.accounts["supplier"]: ("4.5", "0"), self.accounts["purchases"]: ("0", "4.5")})
        movement = dispatched["stock_movements"][0]
        self.assertEqual((movement["pool"], movement["direction"], movement["quantity"]), ("breakage", "out", "1"))
        self.assertEqual(dispatched["snapshot"]["lines"][0]["posting_account"]["id"], self.accounts["purchases"])
        self.assertEqual((self.quantity(), self.quantity("breakage")), (7, 2))

    def test_dispatch_cannot_borrow_regular_stock_and_failed_post_has_no_effects(self):
        self.opening(quantity="10")
        draft = self.save("breakage_dispatch", self.trade_values("breakage_dispatch"))
        with self.assertRaises(StockUnavailable):
            self.post(draft)
        loaded = self.service.get(self.actor, self.company, draft["id"])
        self.assertEqual((loaded["status"], loaded["revision"], loaded["journal_lines"], loaded["stock_movements"]),
                         ("draft", 0, [], []))
        self.assertEqual((self.quantity(), self.quantity("breakage")), (10, 0))
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit_events WHERE entity_id=? AND action='transaction.posted'", (draft["id"],)).fetchone()[0], 0)

    def test_regular_sale_cannot_consume_received_breakage(self):
        self.receive(quantity="2")
        sale = self.save("sale", self.trade_values(quantity="1"))
        with self.assertRaises(StockUnavailable):
            self.post(sale)
        self.assertEqual((self.quantity(), self.quantity("breakage")), (0, 2))

    def test_dispatch_backdating_cannot_consume_later_receipts_or_strand_later_dispatch(self):
        self.receive(quantity="10")
        self.post(self.save("breakage_dispatch", self.trade_values("breakage_dispatch", quantity="8", when="2026-01-10")))
        self.receive(quantity="10", when="2026-01-20")
        for when in ("2026-01-05", "2026-01-15"):
            draft = self.save("breakage_dispatch", self.trade_values("breakage_dispatch", quantity="5", when=when))
            with self.subTest(when=when), self.assertRaises(StockUnavailable):
                self.post(draft)
        self.assertEqual((self.quantity(), self.quantity("breakage")), (0, 12))

    def test_concurrent_dispatches_cannot_oversell_breakage(self):
        self.receive(quantity="1")
        drafts = [self.save("breakage_dispatch", self.trade_values("breakage_dispatch")) for _ in range(2)]
        barrier = Barrier(2)

        def attempt(draft):
            barrier.wait(timeout=5)
            try:
                return self.post(draft)["status"]
            except StockUnavailable:
                return "insufficient"

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(attempt, drafts)), ["insufficient", "posted"])
        self.assertEqual((self.quantity(), self.quantity("breakage")), (0, 0))

    def test_reversals_preserve_pool_and_block_reversal_of_consumed_receive(self):
        received = self.receive(quantity="3")
        dispatched = self.post(self.save("breakage_dispatch", self.trade_values("breakage_dispatch", quantity="1")))
        with self.assertRaises(StockUnavailable):
            self.reverse(received)
        undo_dispatch = self.reverse(dispatched)
        self.assertEqual(undo_dispatch["stock_movements"][0]["direction"], "in")
        self.assertEqual(undo_dispatch["stock_movements"][0]["pool"], "breakage")
        self.assertEqual(undo_dispatch["stock_movements"][0]["reverses_movement_id"], dispatched["stock_movements"][0]["id"])
        undo_receive = self.reverse(received)
        self.assertEqual(undo_receive["stock_movements"][0]["direction"], "out")
        self.assertEqual(undo_receive["stock_movements"][0]["pool"], "breakage")
        for original, reversed_document in ((received, undo_receive), (dispatched, undo_dispatch)):
            for row, opposite in zip(original["journal_lines"], reversed_document["journal_lines"]):
                self.assertEqual((row["account_id"], row["debit"], row["credit"]),
                                 (opposite["account_id"], opposite["credit"], opposite["debit"]))
            with self.assertRaises(RevisionConflict):
                self.reverse(original)
            reloaded = self.service.get(self.actor, self.company, original["id"])
            self.assertEqual(reloaded["snapshot"], original["snapshot"])
        self.assertEqual((self.quantity(), self.quantity("breakage")), (0, 0))

    def test_breakage_modules_have_independent_permissions_and_company_scope(self):
        user = self.security.create_user(self.actor, "breakage_operator", "Copper meadows open lanterns 74")
        worker = self.security.authenticate("breakage_operator", "Copper meadows open lanterns 74")
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.set_permissions(self.actor, user.id, "sales_returns", {"read", "create", "post"})
        with self.assertRaises(AuthorizationError):
            self.save("breakage_receive", self.trade_values("breakage_receive"), worker)
        self.security.set_permissions(self.actor, user.id, "breakage_receive", {"read", "create", "post"})
        received = self.post(self.save("breakage_receive", self.trade_values("breakage_receive", quantity="2"), worker), worker)
        dispatched = self.post(self.save("breakage_dispatch", self.trade_values("breakage_dispatch")))
        with self.assertRaises(AuthorizationError):
            self.service.get(worker, self.company, dispatched["id"])
        with self.assertRaises(AuthorizationError):
            self.save("breakage_dispatch", self.trade_values("breakage_dispatch"), worker)
        self.assertEqual([row["id"] for row in self.service.list_documents(worker, self.company)], [received["id"]])
        draft = self.save("breakage_receive", self.trade_values("breakage_receive"), worker)
        self.security.set_permissions(self.actor, user.id, "breakage_receive", {"read", "create"})
        with self.assertRaises(AuthorizationError):
            self.post(draft, worker)
        with self.assertRaises(AuthorizationError):
            self.service.reverse(worker, self.company, received["id"], received["revision"], document_number="UNAUTHORIZED", business_date="2026-01-03", reason="Synthetic only")
        self.security.set_company_access(self.actor, user.id, [])
        with self.assertRaises(AuthorizationError):
            self.service.get(worker, self.company, received["id"])

    def test_nonzero_unsupported_headers_and_unknown_fields_are_rejected(self):
        for kind in ("breakage_receive", "breakage_dispatch"):
            fields = ("discount", "freight") if kind == "breakage_receive" else ("discount", "breakage", "freight")
            for field in fields:
                for amount in ("0.01", "-0.01"):
                    values = self.trade_values(kind, **{field: amount})
                    with self.subTest(kind=kind, field=field, amount=amount), self.assertRaisesRegex(TransactionError, "zero header"):
                        self.save(kind, values)
            for field in ("labour", "tax", "received", "delivery_id", "source_line_id"):
                values = self.trade_values(kind)
                values[field] = "0"
                with self.subTest(kind=kind, field=field), self.assertRaisesRegex(TransactionError, "Unsupported fields"):
                    self.save(kind, values)

    def test_exact_numeric_validation_includes_falsey_values_and_deduction_limits(self):
        for kind in ("breakage_receive", "breakage_dispatch"):
            for field in ("discount", "breakage", "freight"):
                for amount in (0.0, False, "", "NaN"):
                    with self.subTest(kind=kind, field=field, amount=amount), self.assertRaises(ValueError):
                        self.save(kind, self.trade_values(kind, **{field: amount}))
            for field, value in (("quantity", "0"), ("quantity", "-1"), ("quantity", 0.1),
                                 ("rate", "-1"), ("rate", 0.0), ("discount_percent", "100.1"),
                                 ("discount_percent", "-1"), ("discount_percent", False)):
                values = self.trade_values(kind)
                values["lines"][0][field] = value
                with self.subTest(kind=kind, field=field, value=value), self.assertRaises(ValueError):
                    self.save(kind, values)
        for deduction in ("-1", "10.01"):
            with self.subTest(deduction=deduction), self.assertRaises(ValueError):
                self.save("breakage_receive", self.trade_values("breakage_receive", breakage=deduction))

    def test_zero_value_breakage_moves_stock_without_fabricated_journal(self):
        values = self.trade_values("breakage_receive", quantity="2", rate="10")
        values["lines"][0]["discount_percent"] = "100"
        for field in ("discount", "breakage", "freight"):
            del values[field]
        received = self.post(self.save("breakage_receive", values))
        self.assertEqual(received["journal_lines"], [])
        self.assertEqual(received["total_amount"], "0")
        self.assertEqual({field: received["values"][field] for field in ("discount", "breakage", "freight")},
                         {"discount": "0", "breakage": "0", "freight": "0"})
        dispatched = self.post(self.save("breakage_dispatch", self.trade_values("breakage_dispatch", rate="0")))
        self.assertEqual(dispatched["journal_lines"], [])
        self.assertEqual(dispatched["total_amount"], "0")
        self.assertEqual((self.quantity(), self.quantity("breakage")), (0, 1))

    def test_invoice_links_are_forbidden_and_breakage_is_not_a_standard_return_source(self):
        self.opening(quantity="2")
        sale = self.post(self.save("sale", self.trade_values(quantity="2")))
        for kind in ("breakage_receive", "breakage_dispatch"):
            for source in (None, sale["lines"][0]["id"]):
                values = self.trade_values(kind)
                values["lines"][0]["source_line_id"] = source
                with self.subTest(kind=kind, source=source is not None), self.assertRaisesRegex(TransactionError, "Unsupported fields: source_line_id"):
                    self.save(kind, values)
        received = self.receive(quantity="1", when="2026-01-02")
        for kind in ("sale_return", "purchase_return"):
            values = self.trade_values(kind, source=received["lines"][0]["id"])
            with self.subTest(kind=kind), self.assertRaisesRegex(TransactionError, "corresponding invoice"):
                self.save(kind, values)
        # Standalone breakage does not silently consume an unrelated invoice's
        # linked-return capacity merely because product, party or values match.
        returned = self.post(self.save("sale_return", self.trade_values("sale_return", quantity="2", source=sale["lines"][0]["id"])))
        self.assertEqual(returned["total_amount"], "20")
        self.assertEqual((self.quantity(), self.quantity("breakage")), (2, 1))

    def test_receive_has_one_store_and_dispatch_can_use_multiple_member_stores(self):
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO company_stores(company_id,store_id) VALUES(?,?)", (self.company, self.other_store))
        values = self.trade_values("breakage_receive")
        values["lines"].append({**values["lines"][0], "store_id": self.other_store})
        with self.assertRaisesRegex(TransactionError, "one store"):
            self.save("breakage_receive", values)
        for store in (self.store, self.other_store):
            incoming = self.trade_values("breakage_receive", when="2026-01-01")
            incoming["lines"][0]["store_id"] = store
            self.post(self.save("breakage_receive", incoming))
        values = self.trade_values("breakage_dispatch")
        values["lines"].append({**values["lines"][0], "store_id": self.other_store})
        posted = self.post(self.save("breakage_dispatch", values))
        self.assertEqual({row["store_id"] for row in posted["stock_movements"]}, {self.store, self.other_store})
        self.assertEqual(self.quantity("breakage"), 0)

    def test_company_store_membership_and_required_mappings_are_enforced(self):
        for kind, mapping in (("breakage_receive", "sale_account_id"), ("breakage_dispatch", "purchase_account_id")):
            values = self.trade_values(kind)
            values["lines"][0]["store_id"] = self.other_store
            with self.subTest(kind=kind), self.assertRaisesRegex(TransactionError, "assigned to this company"):
                self.save(kind, values)
            with native_transaction(self.path) as connection:
                connection.execute(f"UPDATE products SET {mapping}=NULL WHERE id=?", (self.product,))
            with self.subTest(mapping=mapping), self.assertRaisesRegex(TransactionError, mapping):
                self.save(kind, self.trade_values(kind))
            with native_transaction(self.path) as connection:
                account = self.accounts["sales" if kind == "breakage_receive" else "purchases"]
                connection.execute(f"UPDATE products SET {mapping}=? WHERE id=?", (account, self.product))

    def test_post_rechecks_breakage_snapshot_and_preserves_posted_labels(self):
        draft = self.save("breakage_receive", self.trade_values("breakage_receive"))
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET name='Reviewed sale account' WHERE id=?", (self.accounts["sales"],))
        with self.assertRaises(RevisionConflict):
            self.post(draft)
        updated = self.service.save_draft(self.actor, self.company, "breakage_receive", draft["values"], draft["id"], draft["revision"])
        posted = self.post(updated)
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET name='Later account label' WHERE id=?", (self.accounts["sales"],))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE document_lines SET quantity='9' WHERE document_id=?", (posted["id"],))
        reloaded = self.service.get(self.actor, self.company, posted["id"])
        self.assertEqual(reloaded["snapshot"]["lines"][0]["posting_account"]["name"], "Reviewed sale account")

    def test_breakage_posting_rolls_back_every_effect_after_failure(self):
        draft = self.save("breakage_receive", self.trade_values("breakage_receive"))
        write = self.service._write_effects

        def fail_after_effects(*args):
            write(*args)
            raise RuntimeError("Synthetic breakage failure")

        with patch.object(self.service, "_write_effects", side_effect=fail_after_effects), self.assertRaises(RuntimeError):
            self.post(draft)
        loaded = self.service.get(self.actor, self.company, draft["id"])
        self.assertEqual((loaded["status"], loaded["revision"], loaded["journal_lines"], loaded["stock_movements"]),
                         ("draft", 0, [], []))
        self.assertEqual((self.quantity(), self.quantity("breakage")), (0, 0))
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit_events WHERE entity_id=? AND action='transaction.posted'", (draft["id"],)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
