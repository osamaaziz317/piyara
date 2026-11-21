"""Operational integration tests with synthetic, isolated native databases."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma import native_schema
from bifma.native_schema import initialize_database, native_session, native_transaction
from bifma.security import AuthenticationError, AuthorizationError, SecurityService
from bifma.transactions import RevisionConflict, StockUnavailable, TransactionError, TransactionService


class TransactionFixture:
    """Shared synthetic native setup; carries no discoverable test methods."""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "native.sqlite3"
        initialize_database(self.path)
        self.clock = lambda: 1_800_000_000
        self.security = SecurityService(self.path, clock=self.clock)
        self.actor = self.security.bootstrap_admin("administrator", "Silver lanterns orbit quietly 72")
        self.service = TransactionService(self.path, self.security, self.clock)
        self.company, self.other_company, self.store, self.other_store, self.unit, self.product = [str(uuid4()) for _ in range(6)]
        self.accounts = {name: str(uuid4()) for name in ("customer", "supplier", "sales", "purchases", "inventory", "equity", "cash", "bank")}
        self.counter = 0
        with native_transaction(self.path) as connection:
            for name, identifier in self.accounts.items():
                kind = name if name in {"customer", "supplier", "cash", "bank"} else "general"
                connection.execute("INSERT INTO accounts(id,code,name,kind) VALUES(?,?,?,?)", (identifier, name.upper(), name.title(), kind))
            for identifier, code in ((self.company, "C1"), (self.other_company, "C2")):
                connection.execute("INSERT INTO companies(id,code,name,fiscal_start,fiscal_end) VALUES(?,?,?,?,?)", (identifier, code, "Synthetic " + code, "2026-01-01", "2026-12-31"))
            connection.execute("INSERT INTO units(id,code,name) VALUES(?,?,?)", (self.unit, "PC", "Piece"))
            connection.execute("INSERT INTO products(id,code,name,small_unit_id,unit_factor,sale_account_id,purchase_account_id,inventory_account_id) VALUES(?,?,?,?,?,?,?,?)", (self.product, "A", "Synthetic product", self.unit, "12", self.accounts["sales"], self.accounts["purchases"], self.accounts["inventory"]))
            for identifier, code in ((self.store, "S1"), (self.other_store, "S2")):
                connection.execute("INSERT INTO stores(id,code,name) VALUES(?,?,?)", (identifier, code, code))
            connection.execute("INSERT INTO company_stores(company_id,store_id) VALUES(?,?)", (self.company, self.store))
            connection.execute("INSERT INTO company_stores(company_id,store_id) VALUES(?,?)", (self.other_company, self.other_store))

    def common(self, when="2026-01-02", *, whole=False):
        self.counter += 1
        return {"document_number": "D-" + str(self.counter), "business_date": when,
                "rounding_policy": "legacy_whole" if whole else "cents", "posting_places": 0 if whole else 2, "notes": "Synthetic only"}

    def trade_values(self, kind="sale", quantity="1", rate="10", when="2026-01-02", *, whole=False, source=None, **headers):
        values = self.common(when, whole=whole)
        party = "customer" if kind in ("sale", "sale_return", "breakage_receive") else "supplier"
        line = {"product_id": self.product, "store_id": self.store, "quantity": quantity, "rate": rate, "discount_percent": "0"}
        if source is not None:
            line["source_line_id"] = source
        values.update(account_id=self.accounts[party], discount="0", breakage="0", freight="0", lines=[line])
        values.update(headers)
        return values

    def save(self, kind, values, actor=None):
        return self.service.save_draft(actor or self.actor, self.company, kind, values)

    def post(self, draft, actor=None):
        return self.service.post(actor or self.actor, self.company, draft["id"], draft["revision"])

    def opening(self, quantity="10", value="100", when="2026-01-01", pool="regular"):
        values = self.common(when)
        values.update(contra_account_id=self.accounts["equity"], lines=[{"product_id": self.product, "store_id": self.store, "quantity": quantity, "value": value, "pool": pool}])
        return self.post(self.save("stock_opening", values))

    def reverse(self, posted, when="2026-01-03"):
        number = self.common(when)["document_number"]
        return self.service.reverse(self.actor, self.company, posted["id"], posted["revision"], document_number=number, business_date=when, reason="Synthetic correction")

    def quantity(self, pool="regular"):
        rows = self.service.stock_balance(self.actor, self.company, pool=pool)
        return sum((Decimal(row["quantity"]) for row in rows), Decimal(0))


class TransactionTests(TransactionFixture, unittest.TestCase):
    def test_posted_source_listing_filters_drafts_before_pagination_and_reaches_old_invoice(self):
        values = self.trade_values("purchase", rate="0", when="2026-01-01")
        values.update(document_number="1", notes="")
        original = self.post(self.save("purchase", values))
        posted_ids = {original["id"]}
        for index in range(105):
            values = self.trade_values("purchase", rate="0", when="2026-02-01")
            values.update(document_number=f"1-P{index:03}", notes="")
            posted_ids.add(self.post(self.save("purchase", values))["id"])
        for index in range(100):
            values = self.trade_values("purchase", rate="0", when="2026-03-01")
            values.update(document_number=f"1-D{index:03}", notes="")
            self.save("purchase", values)
        ordinary = self.service.list_documents(self.actor, self.company, "purchase", search="1", limit=100)
        self.assertEqual(len(ordinary), 100)
        self.assertTrue(all(row["status"] == "draft" for row in ordinary))
        first = self.service.list_documents(self.actor, self.company, "purchase", search="1", status="posted", offset=0, limit=100)
        second = self.service.list_documents(self.actor, self.company, "purchase", search="1", status="posted", offset=100, limit=100)
        self.assertEqual((len(first), len(second)), (100, 6))
        self.assertTrue(all(row["status"] == "posted" for row in first + second))
        self.assertEqual({row["id"] for row in first + second}, posted_ids)
        self.assertEqual(second[-1]["id"], original["id"])
        self.assertEqual(self.service.list_documents(self.actor, self.company, "purchase", search="1", status="posted", offset=200, limit=100), [])
        self.assertEqual(self.service.get(self.actor, self.company, second[-1]["id"])["lines"][0]["id"], original["lines"][0]["id"])

    def test_document_status_filters_preserve_kind_company_and_live_read_permissions(self):
        purchase = self.post(self.save("purchase", self.trade_values("purchase", quantity="2")))
        draft = self.save("purchase", self.trade_values("purchase"))
        self.post(self.save("sale", self.trade_values()))
        other_values = self.trade_values("purchase")
        other_values["lines"][0]["store_id"] = self.other_store
        other_draft = self.service.save_draft(self.actor, self.other_company, "purchase", other_values)
        self.service.post(self.actor, self.other_company, other_draft["id"], other_draft["revision"])
        user = self.security.create_user(self.actor, "source_reader", "Cobalt bridges welcome sunrise 83")
        reader = self.security.authenticate("source_reader", "Cobalt bridges welcome sunrise 83")
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.set_permissions(self.actor, user.id, "purchases", ["read"])
        self.assertEqual([row["id"] for row in self.service.list_documents(reader, self.company, status="posted")], [purchase["id"]])
        self.assertEqual([row["id"] for row in self.service.list_documents(reader, self.company, "purchase", status="draft")], [draft["id"]])
        with self.assertRaises(AuthorizationError):
            self.service.list_documents(reader, self.other_company, "purchase", status="posted")
        self.security.set_permissions(self.actor, user.id, "purchases", [])
        with self.assertRaises(AuthorizationError):
            self.service.list_documents(reader, self.company, "purchase", status="posted")

    def test_document_status_filter_rejects_unsupported_or_nontext_values(self):
        for status in ("", "cancelled", "POSTED", True, 1, [], {}):
            with self.subTest(status=status), self.assertRaisesRegex(TransactionError, "Document status"):
                self.service.list_documents(self.actor, self.company, "purchase", status=status)

    def test_draft_preview_has_snapshots_and_no_posted_effects(self):
        draft = self.save("sale", self.trade_values(discount="1", freight="2"))
        self.assertEqual((draft["status"], draft["revision"], draft["total_amount"]), ("draft", 0, "11"))
        self.assertEqual(draft["journal_lines"], [])
        self.assertEqual(draft["stock_movements"], [])
        self.assertEqual(draft["snapshot"]["lines"][0]["product"]["small_unit"]["name"], "Piece")
        self.assertEqual(draft["snapshot"]["lines"][0]["product"]["unit_factor"], "12")
        self.assertEqual(draft["calculation"]["raw_totals"]["raw_subtotal"], "10")
        self.assertEqual(self.quantity(), 0)

    def test_sale_posting_is_balanced_stock_is_unvalued_and_source_line_is_retained(self):
        self.opening()
        sale = self.post(self.save("sale", self.trade_values(quantity="2", discount="1", freight="2")))
        self.assertEqual((sale["status"], sale["revision"], sale["total_amount"]), ("posted", 1, "21"))
        self.assertEqual(sum(Decimal(row["debit"]) for row in sale["journal_lines"]), Decimal("21"))
        self.assertEqual(sum(Decimal(row["credit"]) for row in sale["journal_lines"]), Decimal("21"))
        movement = sale["stock_movements"][0]
        self.assertEqual((movement["direction"], movement["value"], movement["valuation_basis"], movement["pool"]), ("out", "0", "unvalued", "regular"))
        self.assertEqual(movement["document_line_id"], sale["lines"][0]["id"])
        self.assertEqual(self.quantity(), 8)

    def test_purchase_opening_and_pool_boundaries(self):
        opening = self.opening(quantity="2", value="17", pool="breakage")
        self.assertEqual(opening["stock_movements"][0]["valuation_basis"], "opening_explicit")
        self.assertEqual(opening["stock_movements"][0]["value"], "17")
        sale = self.save("sale", self.trade_values())
        with self.assertRaises(StockUnavailable):
            self.post(sale)
        purchase = self.post(self.save("purchase", self.trade_values("purchase", quantity="3")))
        self.assertEqual(purchase["stock_movements"][0]["direction"], "in")
        self.assertEqual((self.quantity(), self.quantity("breakage")), (3, 2))

    def test_concurrent_sales_cannot_oversell(self):
        self.opening(quantity="1")
        drafts = [self.save("sale", self.trade_values()) for _ in range(2)]
        barrier = Barrier(2)

        def attempt(draft):
            barrier.wait(timeout=5)
            try:
                return self.post(draft)["status"]
            except StockUnavailable:
                return "insufficient"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, drafts))
        self.assertEqual(sorted(results), ["insufficient", "posted"])
        self.assertEqual(self.quantity(), 0)

    def test_concurrent_and_repeated_post_are_exactly_once(self):
        self.opening()
        draft = self.save("sale", self.trade_values())
        barrier = Barrier(2)

        def attempt(_):
            barrier.wait(timeout=5)
            try:
                return self.post(draft)["status"]
            except RevisionConflict:
                return "stale"

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(attempt, range(2))), ["posted", "stale"])
        with self.assertRaises(RevisionConflict):
            self.post(draft)
        self.assertEqual(self.quantity(), 9)

    def test_backdating_cannot_make_a_later_running_balance_negative(self):
        self.opening(quantity="10")
        self.post(self.save("sale", self.trade_values(quantity="8", when="2026-01-10")))
        self.post(self.save("purchase", self.trade_values("purchase", quantity="10", when="2026-01-20")))
        backdated = self.save("sale", self.trade_values(quantity="5", when="2026-01-05"))
        with self.assertRaises(StockUnavailable):
            self.post(backdated)
        self.assertEqual(self.quantity(), 12)

    def test_same_second_posting_order_uses_sequence_not_uuid_or_draft_order(self):
        sale = self.save("sale", self.trade_values(when="2026-01-01"))
        opening = self.opening(quantity="2", when="2026-01-01")
        posted_sale = self.post(sale)
        purchase = self.post(self.save("purchase", self.trade_values("purchase", when="2026-01-01")))
        self.assertEqual([opening["posting_sequence"], posted_sale["posting_sequence"], purchase["posting_sequence"]], [1, 2, 3])
        self.assertEqual(self.quantity(), 2)

    def test_permissions_company_membership_and_sessions_rechecked_at_post(self):
        user = self.security.create_user(self.actor, "operator", "Cobalt bridges welcome sunrise 83")
        worker = self.security.authenticate("operator", "Cobalt bridges welcome sunrise 83")
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.set_permissions(self.actor, user.id, "sales", {"create", "read", "post"})
        draft = self.save("sale", self.trade_values(), worker)
        self.security.set_permissions(self.actor, user.id, "sales", {"create", "read"})
        with self.assertRaises(AuthorizationError):
            self.post(draft, worker)
        self.security.set_permissions(self.actor, user.id, "sales", {"create", "read", "post"})
        self.security.set_company_access(self.actor, user.id, [])
        with self.assertRaises(AuthorizationError):
            self.service.get(worker, self.company, draft["id"])
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.logout(worker)
        with self.assertRaises(AuthenticationError):
            self.post(draft, worker)

    def test_master_changes_require_review_and_posted_labels_are_frozen(self):
        self.opening()
        draft = self.save("sale", self.trade_values())
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE products SET name='Reviewed product' WHERE id=?", (self.product,))
        with self.assertRaises(RevisionConflict):
            self.post(draft)
        updated = self.service.save_draft(self.actor, self.company, "sale", draft["values"], draft["id"], draft["revision"])
        posted = self.post(updated)
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE products SET name='Later rename' WHERE id=?", (self.product,))
            for statement in ("UPDATE document_metadata SET snapshot_json='{}' WHERE document_id=?", "DELETE FROM document_metadata WHERE document_id=?", "INSERT OR REPLACE INTO document_metadata SELECT * FROM document_metadata WHERE document_id=?"):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement, (posted["id"],))
        reloaded = self.service.get(self.actor, self.company, posted["id"])
        self.assertEqual(reloaded["snapshot"]["lines"][0]["product"]["name"], "Reviewed product")

    def test_post_rechecks_master_active_status_fiscal_interval_and_revision(self):
        self.opening()
        draft = self.save("sale", self.trade_values())
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET is_active=0 WHERE id=?", (self.accounts["sales"],))
        with self.assertRaises(TransactionError):
            self.post(draft)
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET is_active=1 WHERE id=?", (self.accounts["sales"],))
            connection.execute("UPDATE companies SET fiscal_start='2026-02-01' WHERE id=?", (self.company,))
        with self.assertRaises(TransactionError):
            self.post(draft)
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET fiscal_start='2026-01-01' WHERE id=?", (self.company,))
        updated = self.service.save_draft(self.actor, self.company, "sale", draft["values"], draft["id"], draft["revision"])
        with self.assertRaises(RevisionConflict):
            self.post(draft)
        self.post(updated)

    def test_atomic_failure_rolls_back_journal_stock_audit_and_status(self):
        self.opening()
        draft = self.save("sale", self.trade_values())
        original = self.service._write_effects

        def broken(*args):
            original(*args)
            raise RuntimeError("synthetic interruption after effects")

        with patch.object(self.service, "_write_effects", side_effect=broken), self.assertRaises(RuntimeError):
            self.post(draft)
        loaded = self.service.get(self.actor, self.company, draft["id"])
        self.assertEqual((loaded["status"], loaded["revision"], loaded["journal_lines"], loaded["stock_movements"]), ("draft", 0, [], []))
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit_events WHERE entity_id=? AND action='transaction.posted'", (draft["id"],)).fetchone()[0], 0)
        self.assertEqual(self.quantity(), 10)

    def test_draft_line_tampering_is_detected_before_posting(self):
        self.opening()
        draft = self.save("sale", self.trade_values())
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE document_lines SET quantity='9' WHERE document_id=?", (draft["id"],))
        with self.assertRaises(TransactionError):
            self.post(draft)

    def test_return_matching_and_cumulative_quantity_are_verified_under_lock(self):
        self.opening(quantity="3")
        sale = self.post(self.save("sale", self.trade_values(quantity="2")))
        source = sale["lines"][0]["id"]
        bad = self.trade_values("sale_return", rate="11", source=source)
        with self.assertRaises(TransactionError):
            self.save("sale_return", bad)
        drafts = [self.save("sale_return", self.trade_values("sale_return", quantity="2", source=source)) for _ in range(2)]
        returned = self.post(drafts[0])
        self.assertEqual(returned["stock_movements"][0]["direction"], "in")
        with self.assertRaises(TransactionError):
            self.post(drafts[1])
        self.assertEqual(self.quantity(), 3)

    def test_returns_never_exceed_original_money_with_whole_rounding(self):
        self.opening(quantity="2")
        sale = self.post(self.save("sale", self.trade_values(quantity="2", rate="0.6", whole=True)))
        self.assertEqual(sale["total_amount"], "1")
        source = sale["lines"][0]["id"]
        returned = self.post(self.save("sale_return", self.trade_values("sale_return", quantity="1", rate="0.6", whole=True, source=source)))
        self.assertEqual(returned["total_amount"], "1")
        with self.assertRaisesRegex(TransactionError, "original total"):
            self.save("sale_return", self.trade_values("sale_return", quantity="1", rate="0.6", whole=True, source=source))

    def test_staff_entered_return_adjustments_preserve_original_and_cap_total(self):
        self.opening()
        sale = self.post(self.save("sale", self.trade_values(quantity="4", discount="4", breakage="2", freight="4")))
        source = sale["lines"][0]["id"]
        first = self.post(self.save("sale_return", self.trade_values(
            "sale_return", quantity="1", source=source, discount="1", breakage="0.5", freight="2")))
        self.assertEqual(first["total_amount"], "10.5")
        with self.assertRaisesRegex(TransactionError, "original total"):
            self.save("sale_return", self.trade_values(
                "sale_return", quantity="3", source=source, discount="1", breakage="1", freight="1"))
        second = self.post(self.save("sale_return", self.trade_values(
            "sale_return", quantity="3", source=source, discount="1.5", breakage="1", freight="0")))
        self.assertEqual(second["total_amount"], "27.5")
        self.assertEqual(Decimal(first["total_amount"]) + Decimal(second["total_amount"]), Decimal(sale["total_amount"]))
        self.assertEqual(self.service.get(self.actor, self.company, sale["id"]), sale)
        self.assertEqual(self.quantity(), 10)

    def test_purchase_return_accepts_entered_adjustments_and_releases_cap_on_reversal(self):
        purchase = self.post(self.save("purchase", self.trade_values("purchase", quantity="2", freight="3")))
        source = purchase["lines"][0]["id"]
        returned = self.post(self.save("purchase_return", self.trade_values(
            "purchase_return", quantity="1", source=source, freight="3")))
        self.assertEqual(returned["total_amount"], "13")
        with self.assertRaisesRegex(TransactionError, "original total"):
            self.save("purchase_return", self.trade_values("purchase_return", source=source, freight="1"))
        self.reverse(returned)
        all_goods = self.post(self.save("purchase_return", self.trade_values(
            "purchase_return", quantity="2", source=source, freight="3", when="2026-01-04")))
        self.assertEqual(all_goods["total_amount"], "23")
        self.assertEqual(self.quantity(), 0)

    def test_reversing_a_return_releases_quantity_and_money_caps(self):
        self.opening(quantity="2")
        sale = self.post(self.save("sale", self.trade_values(quantity="2")))
        source = sale["lines"][0]["id"]
        returned = self.post(self.save("sale_return", self.trade_values("sale_return", quantity="1", source=source)))
        with self.assertRaisesRegex(TransactionError, "linked returns"):
            self.reverse(sale)
        self.reverse(returned)
        returned_all = self.post(self.save("sale_return", self.trade_values("sale_return", quantity="2", when="2026-01-04", source=source)))
        self.assertEqual(returned_all["total_amount"], "20")
        self.assertEqual(self.quantity(), 2)

    def test_purchase_return_reduces_stock_and_posts_opposite_side(self):
        purchase = self.post(self.save("purchase", self.trade_values("purchase", quantity="2")))
        returned = self.post(self.save("purchase_return", self.trade_values("purchase_return", source=purchase["lines"][0]["id"])))
        self.assertEqual(self.quantity(), 1)
        party_line = next(row for row in returned["journal_lines"] if row["account_id"] == self.accounts["supplier"])
        self.assertEqual((party_line["debit"], party_line["credit"]), ("10", "0"))

    def test_reversal_mirrors_exact_effects_and_keeps_original_immutable(self):
        self.opening()
        sale = self.post(self.save("sale", self.trade_values(quantity="2", discount="1", freight="0.25")))
        reversal = self.reverse(sale)
        self.assertEqual(self.quantity(), 10)
        self.assertEqual(reversal["reverses_document_id"], sale["id"])
        for original, mirrored in zip(sale["journal_lines"], reversal["journal_lines"]):
            self.assertEqual((original["account_id"], original["debit"], original["credit"]), (mirrored["account_id"], mirrored["credit"], mirrored["debit"]))
        self.assertEqual(reversal["stock_movements"][0]["reverses_movement_id"], sale["stock_movements"][0]["id"])
        self.assertEqual(reversal["snapshot"]["company"], sale["snapshot"]["company"])
        self.assertEqual(reversal["snapshot"]["lines"][0]["line_id"], reversal["lines"][0]["id"])
        with self.assertRaises(RevisionConflict):
            self.reverse(sale)
        with self.assertRaises(RevisionConflict):
            self.reverse(reversal)
        reloaded = self.service.get(self.actor, self.company, sale["id"])
        self.assertEqual((reloaded["status"], reloaded["revision"], reloaded["total_amount"]), (sale["status"], sale["revision"], sale["total_amount"]))
        self.assertEqual(reloaded["reversal"]["id"], reversal["id"])

    def test_reversing_consumed_opening_is_blocked(self):
        opening = self.opening(quantity="1")
        self.post(self.save("sale", self.trade_values()))
        with self.assertRaises(StockUnavailable):
            self.reverse(opening)

    def test_journal_and_cash_bank_vouchers_are_exact_and_do_not_move_stock(self):
        values = self.common()
        values["lines"] = [{"account_id": self.accounts["customer"], "debit": "0.3", "credit": "0", "narration": "A"},
                           {"account_id": self.accounts["equity"], "debit": "0", "credit": "0.1"},
                           {"account_id": self.accounts["sales"], "debit": "0", "credit": "0.2"}]
        journal = self.post(self.save("journal", values))
        self.assertEqual(journal["total_amount"], "0.3")
        for kind in ("cash_receipt", "cash_payment", "bank_receipt", "bank_payment"):
            voucher = self.common()
            account = self.accounts["cash" if kind.startswith("cash") else "bank"]
            voucher.update(account_id=account, lines=[{"account_id": self.accounts["customer"], "amount": "12.25"}])
            posted = self.post(self.save(kind, voucher))
            cash = next(row for row in posted["journal_lines"] if row["account_id"] == account)
            self.assertEqual((cash["debit"], cash["credit"]), ("12.25", "0") if kind.endswith("receipt") else ("0", "12.25"))
            self.assertEqual(posted["stock_movements"], [])
        self.assertEqual(self.quantity(), 0)

    def test_wrong_cash_kind_unknown_fields_and_nonfinite_values_rejected(self):
        values = self.common()
        values.update(account_id=self.accounts["customer"], lines=[{"account_id": self.accounts["supplier"], "amount": "1"}])
        with self.assertRaises(TransactionError):
            self.save("cash_receipt", values)
        for field in ("received", "tax", "labour", "delivery_id"):
            values = self.trade_values()
            values[field] = "1"
            with self.subTest(field=field), self.assertRaises(TransactionError):
                self.save("sale", values)
        for quantity in ("NaN", "-1", 0.1):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                self.save("sale", self.trade_values(quantity=quantity))

    def test_company_store_boundary_duplicate_number_and_listing(self):
        values = self.trade_values()
        values["lines"][0]["store_id"] = self.other_store
        with self.assertRaises(TransactionError):
            self.save("sale", values)
        values = self.trade_values()
        draft = self.save("sale", values)
        with self.assertRaises(TransactionError):
            self.save("sale", values)
        self.assertEqual([row["id"] for row in self.service.list_documents(self.actor, self.company, "sale", search=values["document_number"])], [draft["id"]])
        self.assertEqual(self.service.list_documents(self.actor, self.company, "sale", search="' OR 1=1 --"), [])
        with self.assertRaises(TransactionError):
            self.service.get(self.actor, self.other_company, draft["id"])

    def test_unclassified_stock_is_not_silently_ignored(self):
        values = self.common("2026-01-01")
        values.update(contra_account_id=self.accounts["equity"], lines=[{"product_id": self.product, "store_id": self.store, "quantity": "1", "value": "0", "pool": "regular"}])
        draft = self.save("stock_opening", values)
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO stock_movements(id,company_id,document_id,document_line_id,product_id,store_id,direction,quantity) VALUES(?,?,?,?,?,?,?,?)", (str(uuid4()), self.company, draft["id"], draft["lines"][0]["id"], self.product, self.store, "in", "1"))
            connection.execute("UPDATE documents SET status='posted',posted_by=?,posted_at=1,revision=revision+1 WHERE id=?", (self.actor.user.id, draft["id"]))
        with self.assertRaises(StockUnavailable):
            self.quantity()
        sale = self.save("sale", self.trade_values())
        with self.assertRaises(StockUnavailable):
            self.post(sale)

    def test_version_one_upgrade_preserves_history_and_marks_pool_unclassified(self):
        old = self.path.parent / "v1.sqlite3"
        with patch.object(native_schema, "MIGRATIONS", native_schema.MIGRATIONS[:1]):
            initialize_database(old)
        result = native_schema.migrate_database(old)
        self.assertEqual(result.applied_versions, tuple(migration.version for migration in native_schema.MIGRATIONS[1:]))
        self.assertTrue(result.backup_path.exists())
        with native_session(old) as connection:
            self.assertIn("pool", [row[1] for row in connection.execute("PRAGMA table_info(posted_stock_movements)")])
            self.assertIn("posting_sequence", [row[1] for row in connection.execute("PRAGMA table_info(documents)")])


if __name__ == "__main__":
    unittest.main()
