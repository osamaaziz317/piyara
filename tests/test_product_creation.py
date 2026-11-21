"""Ordinary item creation uses exact atomic codes and existing posting setup."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from bifma.baseline import ensure_baseline
from bifma.database import session
from bifma.masters import MasterService, MasterValidationError
from bifma.native_schema import initialize_database, native_session
from bifma.security import AuthenticationError, AuthorizationError, SecurityService
from bifma.transactions import TransactionService
from tests.test_baseline_import import synthetic_archive


PASSWORD = "Copper lanterns shine past Jupiter 72!"


class ProductCreationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "working.sqlite3"
        initialize_database(self.path)
        self.clock = lambda: 1_800_000_000
        self.security = SecurityService(self.path, clock=self.clock)
        self.actor = self.security.bootstrap_admin("administrator", PASSWORD)
        self.service = MasterService(self.path, security=self.security, clock=self.clock)
        self.unit = self.create("units", code="PC", name="Piece")
        self.sales = self.create("accounts", code="SALES", name="Sales")
        self.purchases = self.create("accounts", code="PURCHASES", name="Purchases")
        self.template = self.product("TEMPLATE")

    def create(self, entity, **values):
        return self.service.create(self.actor, entity, values)

    def product(self, code, **values):
        fields = {"code": code, "name": "Synthetic item " + code[:12], "small_unit_id": self.unit["id"],
                  "sale_account_id": self.sales["id"], "purchase_account_id": self.purchases["id"]}
        fields.update(values)
        return self.create("products", **fields)

    def values(self, **values):
        return {"name": "New synthetic item", "small_unit_id": self.unit["id"], **values}

    def update(self, entity, record, **values):
        return self.service.update(self.actor, entity, record["id"], values, record["version"])

    def counts(self):
        with native_session(self.path) as connection:
            return tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                         for table in ("products", "accounts", "audit_events"))

    def test_preview_is_not_a_reservation_and_empty_numeric_sequence_starts_at_one(self):
        before = self.counts()
        self.assertEqual(self.service.next_product_code(self.actor), "1")
        self.assertEqual(self.service.next_product_code(self.actor), "1")
        self.assertEqual(self.counts(), before)
        values = self.values()
        result = self.service.create_product(self.actor, values)
        self.assertEqual(values, self.values())
        self.assertEqual(result["code"], "1")
        self.assertEqual(result["sale_account_id"], self.sales["id"])
        self.assertEqual(result["purchase_account_id"], self.purchases["id"])
        self.assertIsNone(result["inventory_account_id"])
        self.assertEqual(self.service.get(self.actor, "products", result["id"]), result)
        self.assertEqual(self.service.next_product_code(self.actor), "2")
        self.assertEqual(self.counts(), (before[0] + 1, before[1], before[2] + 1))

    def test_maximum_numeric_code_counts_inactive_leading_zeroes_and_gaps(self):
        for code in ("0000", "7", "010", "4999", "P999999", "+999999", "1e9", "99999.0", "９９９９９", "٩٩٩٩٩"):
            self.product(code)
        highest = self.product("0005000", is_active=False)
        self.assertEqual(self.service.next_product_code(self.actor), "5001")
        created = self.service.create_product(self.actor, self.values())
        self.assertEqual(created["code"], "5001")
        self.assertEqual(self.service.get(self.actor, "products", highest["id"])["code"], "0005000")

    def test_numeric_codes_are_exact_beyond_sqlite_integer_and_float_ranges(self):
        self.product("9007199254740993")
        self.assertEqual(self.service.next_product_code(self.actor), "9007199254740994")
        self.product("9223372036854775807")
        self.assertEqual(self.service.create_product(self.actor, self.values())["code"], "9223372036854775808")
        self.product("8" + "9" * 79)
        self.assertEqual(self.service.create_product(self.actor, self.values())["code"], "9" + "0" * 79)
        self.product("9" * 80)
        before = self.counts()
        for operation in (lambda: self.service.next_product_code(self.actor),
                          lambda: self.service.create_product(self.actor, self.values())):
            with self.assertRaisesRegex(MasterValidationError, "exceeds 80 digits"):
                operation()
        self.assertEqual(self.counts(), before)

    def test_concurrent_saves_use_fresh_codes_after_identical_previews(self):
        self.product("5000")
        barrier = Barrier(4)

        def save(number):
            service = MasterService(self.path, security=self.security, clock=self.clock)
            preview = service.next_product_code(self.actor)
            barrier.wait(timeout=10)
            record = service.create_product(self.actor, self.values(name=f"Concurrent item {number}"))
            return preview, record["code"], record["id"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(save, range(4)))
        self.assertEqual([row[0] for row in results], ["5001"] * 4)
        self.assertEqual(sorted(row[1] for row in results), ["5001", "5002", "5003", "5004"])
        self.assertEqual(len({row[2] for row in results}), 4)

    def test_validation_and_audit_failures_do_not_consume_code_or_create_accounts(self):
        self.product("5000")
        before = self.counts()
        for values in (self.values(name=""), self.values(unit_factor="0"),
                       self.values(small_unit_id="not-a-uuid"), self.values(unknown="field")):
            with self.assertRaises(MasterValidationError):
                self.service.create_product(self.actor, values)
        with patch.object(self.service, "_audit", side_effect=RuntimeError("Synthetic failure after insert")):
            with self.assertRaises(RuntimeError):
                self.service.create_product(self.actor, self.values())
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.service.next_product_code(self.actor), "5001")
        self.assertEqual(self.service.create_product(self.actor, self.values())["code"], "5001")

    def test_automatic_fields_cannot_be_overridden_but_explicit_setup_api_stays_available(self):
        before = self.counts()
        for field in ("code", "sale_account_id", "purchase_account_id", "inventory_account_id"):
            with self.subTest(field=field), self.assertRaisesRegex(MasterValidationError, "assigned automatically"):
                self.service.create_product(self.actor, self.values(**{field: None}))
        for invalid in (None, [], {}, {1: "invalid key"}):
            with self.subTest(value=invalid), self.assertRaises(MasterValidationError):
                self.service.create_product(self.actor, invalid)
        self.assertEqual(self.counts(), before)
        explicit = self.product("0000042", sale_account_id=None, purchase_account_id=None)
        self.assertEqual(explicit["code"], "0000042")
        self.assertIsNone(explicit["sale_account_id"])
        self.assertEqual(self.service.next_product_code(self.actor), "43")

    def test_preview_and_save_recheck_admin_and_session_after_creation(self):
        user = self.security.create_user(self.actor, "operator", PASSWORD)
        staff = self.security.authenticate("operator", PASSWORD)
        self.security.set_permissions(self.actor, user.id, "products", {"read", "create"})
        before = self.counts()
        for actor, error in ((staff, AuthorizationError), (self.actor.user, AuthenticationError)):
            for operation in (lambda: self.service.next_product_code(actor),
                              lambda: self.service.create_product(actor, self.values())):
                with self.assertRaises(error):
                    operation()
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.service.next_product_code(self.actor), "1")
        self.security.logout(self.actor)
        before = self.counts()
        for operation in (lambda: self.service.next_product_code(self.actor),
                          lambda: self.service.create_product(self.actor, self.values())):
            with self.assertRaises(AuthenticationError):
                operation()
        self.assertEqual(self.counts(), before)

    def test_missing_or_ambiguous_complete_mapping_is_rejected_without_number_reservation(self):
        template = self.update("products", self.template, sale_account_id=None, purchase_account_id=None)
        before = self.counts()
        with self.assertRaisesRegex(MasterValidationError, "one consistent"):
            self.service.create_product(self.actor, self.values())
        self.assertEqual(self.counts(), before)
        self.update("products", template, sale_account_id=self.sales["id"], purchase_account_id=self.purchases["id"])
        another = self.create("accounts", code="SECOND-SALES", name="Another configured sales account")
        conflicting = self.product("ARCHIVED", sale_account_id=another["id"], is_active=False)
        before = self.counts()
        with self.assertRaisesRegex(MasterValidationError, "one consistent"):
            self.service.create_product(self.actor, self.values())
        self.assertEqual(self.counts(), before)
        self.update("products", conflicting, sale_account_id=self.sales["id"])
        self.assertEqual(self.service.create_product(self.actor, self.values())["code"], "1")

    def test_partial_mapping_must_not_contradict_the_only_complete_pair(self):
        another = self.create("accounts", code="SECOND-PURCHASES", name="Another purchases account")
        partial = self.product("PARTIAL", sale_account_id=None, purchase_account_id=another["id"])
        before = self.counts()
        with self.assertRaisesRegex(MasterValidationError, "conflicting"):
            self.service.create_product(self.actor, self.values())
        self.assertEqual(self.counts(), before)
        partial = self.update("products", partial, purchase_account_id=self.purchases["id"])
        self.assertEqual(self.service.create_product(self.actor, self.values())["code"], "1")
        self.update("products", partial, purchase_account_id=None)
        self.assertEqual(self.service.create_product(self.actor, self.values())["code"], "2")

    def test_invalid_posting_candidates_are_not_used_even_when_old_items_are_inactive(self):
        self.update("products", self.template, is_active=False)
        sales = self.sales
        for kind in ("customer", "supplier", "both", "cash", "bank", "group"):
            sales = self.update("accounts", sales, kind=kind, is_postable=kind != "group")
            before = self.counts()
            with self.subTest(kind=kind), self.assertRaisesRegex(MasterValidationError, "active, postable general"):
                self.service.create_product(self.actor, self.values())
            self.assertEqual(self.counts(), before)
        sales = self.update("accounts", sales, kind="general", is_postable=True, is_active=False)
        with self.assertRaisesRegex(MasterValidationError, "active, postable general"):
            self.service.create_product(self.actor, self.values())
        self.update("accounts", sales, is_active=True)
        self.assertEqual(self.service.create_product(self.actor, self.values())["code"], "1")

    def test_editing_normal_fields_preserves_hidden_code_and_all_existing_mappings(self):
        inventory = self.create("accounts", code="INVENTORY", name="Existing inventory account")
        old = self.update("products", self.template, inventory_account_id=inventory["id"])
        edited = self.update("products", old, name="Revised description", sale_rate="12.34")
        for field in ("code", "sale_account_id", "purchase_account_id", "inventory_account_id"):
            self.assertEqual(edited[field], old[field])
        created = self.service.create_product(self.actor, self.values())
        self.assertIsNone(created["inventory_account_id"])

    def test_sealed_original_code_survives_rename_and_new_item_can_purchase_and_sell(self):
        source, target = self.directory / "original.sqlite3", self.directory / "imported.sqlite3"
        synthetic_archive(source)
        with session(source, readonly=False) as connection:
            for table in ("PUR_ITEM", "OPEN_STOCK", "SDTL"):
                connection.execute(f"UPDATE {table} SET ICODE='0005000' WHERE ICODE='A'")
        source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        ensure_baseline(source, target)
        security = SecurityService(target, clock=self.clock)
        actor = security.bootstrap_admin("imported_owner", PASSWORD)
        masters = MasterService(target, security=security, clock=self.clock)
        transactions = TransactionService(target, security=security, clock=self.clock)
        old = masters.list_records(actor, "products")[0]
        masters.update(actor, "products", old["id"], {"code": "RENAMED", "is_active": False}, old["version"])
        self.assertEqual(masters.next_product_code(actor), "5001")
        new = masters.create_product(actor, {"name": "Synthetic new item", "small_unit_id": old["small_unit_id"]})
        self.assertEqual(new["code"], "5001")
        self.assertEqual(new["sale_account_id"], old["sale_account_id"])
        self.assertEqual(new["purchase_account_id"], old["purchase_account_id"])
        company = masters.list_records(actor, "companies")[0]["id"]
        store = masters.list_records(actor, "stores")[0]["id"]
        party = next(row["id"] for row in masters.list_records(actor, "accounts") if row["code"] == "0002")
        posted = {}
        for kind, quantity, rate in (("purchase", "3", "2"), ("sale", "1", "5")):
            draft = transactions.save_draft(actor, company, kind, {
                "document_number": "NEW-" + kind, "business_date": "2026-01-04",
                "rounding_policy": "cents", "posting_places": 2, "account_id": party,
                "lines": [{"product_id": new["id"], "store_id": store, "quantity": quantity, "rate": rate}]})
            posted[kind] = transactions.post(actor, company, draft["id"], draft["revision"])
        with native_session(target) as connection:
            credit = connection.execute("SELECT l.credit FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                                        "WHERE e.document_id=? AND l.account_id=?",
                                        (posted["sale"]["id"], new["sale_account_id"])).fetchone()[0]
            debit = connection.execute("SELECT l.debit FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                                       "WHERE e.document_id=? AND l.account_id=?",
                                       (posted["purchase"]["id"], new["purchase_account_id"])).fetchone()[0]
            original_key = connection.execute("SELECT source_key FROM baseline_mappings WHERE native_id=?", (old["id"],)).fetchone()[0]
        self.assertEqual((credit, debit), ("5", "6"))
        self.assertEqual(original_key, "0005000")
        stock = next(row for row in transactions.stock_balance(actor, company) if row["product_id"] == new["id"])
        self.assertEqual(stock["quantity"], "2")
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_digest)


if __name__ == "__main__":
    unittest.main()
