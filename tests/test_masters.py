"""Native master services tested with temporary databases and synthetic records."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma.masters import MasterService, MasterValidationError, MasterConflictError, MasterNotFoundError
from bifma.native_schema import initialize_database, native_session, native_transaction, NativeSchemaError
from bifma.security import SecurityService, AuthenticationError, AuthorizationError
from bifma.transactions import TransactionService


PASSWORD = "Silver rivers flow beyond Saturn 57!"
STAFF_PASSWORD = "Quiet copper lanterns near Jupiter 92!"


class Clock:
    def __init__(self):
        self.now = 1_800_000_000

    def __call__(self):
        return self.now


class MasterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "native.sqlite3"
        initialize_database(self.path)
        self.clock = Clock()
        self.security = SecurityService(self.path, clock=self.clock)
        self.admin = self.security.bootstrap_admin("administrator", PASSWORD)
        self.service = MasterService(self.path, security=self.security, clock=self.clock)
        self.company = self.create("companies", code="A", name="Synthetic A")
        self.other_company = self.create("companies", code="B", name="Synthetic B")
        self.unit = self.create("units", code="PC", name="Piece")
        self.large_unit = self.create("units", code="CT", name="Carton")
        self.cash = self.create("accounts", code="0001", name="Synthetic cash", kind="cash", classification="asset")
        self.expense = self.create("accounts", code="0002", name="Synthetic purchases", classification="expense")
        self.income = self.create("accounts", code="0003", name="Synthetic income", classification="income")
        self.store = self.create("stores", code="S1", name="Synthetic store")
        self.membership = self.create("company_stores", company_id=self.company["id"], store_id=self.store["id"])
        self.product = self.create("products", code="0001", name="Synthetic item", small_unit_id=self.unit["id"],
                                   large_unit_id=self.large_unit["id"], unit_factor="12",
                                   sale_account_id=self.income["id"], purchase_account_id=self.expense["id"])

    def create(self, entity, **values):
        return self.service.create(self.admin, entity, values)

    def update(self, entity, row, **values):
        return self.service.update(self.admin, entity, row["id"], values, row["version"])

    def staff(self):
        user = self.security.create_user(self.admin, "operator", STAFF_PASSWORD)
        actor = self.security.authenticate("operator", STAFF_PASSWORD)
        return user, actor

    def count(self, table):
        with native_session(self.path) as connection:
            return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_create_get_list_and_code_edit_preserve_native_identity(self):
        city = self.create("cities", code="0007", name="Example city", name_urdu="مثال")
        self.assertEqual(self.service.get(self.admin, "cities", city["id"]), city)
        changed = self.update("cities", city, code="0008", name="Changed city")
        self.assertEqual(changed["id"], city["id"])
        self.assertEqual(changed["code"], "0008")
        self.assertNotEqual(changed["version"], city["version"])
        self.assertEqual(self.service.list_records(self.admin, "cities", search="Changed"), [changed])
        self.assertFalse(hasattr(self.service, "delete"))

    def test_every_supported_master_entity_has_controlled_create_and_update(self):
        group = self.create("product_groups", code="G", name="Group")
        child = self.create("product_groups", code="G1", name="Child", parent_id=group["id"])
        city = self.create("cities", code="C", name="City")
        account = self.create("accounts", code="0010", name="Customer", city_id=city["id"], kind="customer")
        product = self.update("products", self.product, group_id=child["id"], inventory_account_id=self.expense["id"])
        self.assertEqual(product["group_id"], child["id"])
        self.assertEqual(account["city_id"], city["id"])
        self.assertEqual(self.update("stores", self.store, name="New store name")["name"], "New store name")

    def test_posted_history_freezes_base_unit_even_after_full_reversal(self):
        self.update("companies", self.company, fiscal_start="2026-01-01", fiscal_end="2026-12-31")
        transactions = TransactionService(self.path, security=self.security, clock=self.clock)
        draft = transactions.save_draft(self.admin, self.company["id"], "purchase", {
            "document_number": "PUR-1", "business_date": "2026-10-02", "rounding_policy": "cents", "posting_places": 2,
            "account_id": self.cash["id"], "lines": [{"product_id": self.product["id"], "store_id": self.store["id"], "quantity": "3", "rate": "1"}]})
        posted = transactions.post(self.admin, self.company["id"], draft["id"], draft["revision"])
        for reversed_history in (False, True):
            with self.subTest(reversed_history=reversed_history):
                if reversed_history:
                    transactions.reverse(self.admin, self.company["id"], posted["id"], posted["revision"], document_number="REV-1", business_date="2026-10-02", reason="Synthetic correction")
                before = self.service.get(self.admin, "products", self.product["id"])
                with self.assertRaisesRegex(MasterValidationError, "base unit cannot change"):
                    self.update("products", before, small_unit_id=self.large_unit["id"], unit_factor="1")
                self.assertEqual(self.service.get(self.admin, "products", self.product["id"]), before)
        # Packs do not redefine the base quantities already stored.
        changed = self.update("products", before, unit_factor="24")
        self.assertEqual(changed["unit_factor"], "24")
        self.assertEqual(changed["small_unit_id"], self.unit["id"])

    def test_base_unit_can_change_before_posted_history_but_drafts_need_review(self):
        self.update("companies", self.company, fiscal_start="2026-01-01", fiscal_end="2026-12-31")
        transactions = TransactionService(self.path, security=self.security, clock=self.clock)
        draft = transactions.save_draft(self.admin, self.company["id"], "purchase", {
            "document_number": "DRAFT-1", "business_date": "2026-10-02", "rounding_policy": "cents", "posting_places": 2,
            "account_id": self.cash["id"], "lines": [{"product_id": self.product["id"], "store_id": self.store["id"], "quantity": "3", "rate": "1"}]})
        changed = self.update("products", self.product, small_unit_id=self.large_unit["id"], unit_factor="1")
        self.assertEqual(changed["small_unit_id"], self.large_unit["id"])
        from bifma.transactions import TransactionError
        with self.assertRaises(TransactionError):
            transactions.post(self.admin, self.company["id"], draft["id"], draft["revision"])

    def test_shared_master_setup_and_administrative_reads_are_admin_only(self):
        user, actor = self.staff()
        self.security.set_company_access(self.admin, user.id, [self.company["id"]])
        self.security.set_permissions(self.admin, user.id, "products", {"read", "create", "update"})
        for operation in (
            lambda: self.service.create(actor, "units", {"code": "X", "name": "X"}),
            lambda: self.service.update(actor, "products", self.product["id"], {"name": "Changed"}, self.product["version"]),
            lambda: self.service.get(actor, "products", self.product["id"]),
            lambda: self.service.list_records(actor, "companies"),
        ):
            with self.assertRaises(AuthorizationError):
                operation()
        self.assertEqual(self.service.get(self.admin, "products", self.product["id"])["name"], "Synthetic item")

    def test_forged_or_revoked_actor_cannot_read_or_mutate(self):
        with self.assertRaises(AuthenticationError):
            self.service.get(self.admin.user, "products", self.product["id"])
        self.security.logout(self.admin)
        with self.assertRaises(AuthenticationError):
            self.create("units", code="X", name="Denied")
        with self.assertRaises(AuthenticationError):
            self.service.lookup(self.admin, self.company["id"], "sales")

    def test_lookup_is_current_permission_company_and_action_scoped(self):
        user, actor = self.staff()
        self.security.set_company_access(self.admin, user.id, [self.company["id"]])
        self.security.set_permissions(self.admin, user.id, "sales", {"create"})
        with self.assertRaises(AuthorizationError):
            self.service.lookup(actor, self.company["id"], "sales")
        rows = self.service.lookup(actor, self.company["id"], "sales", action="create")
        self.assertEqual(set(rows), {"companies", "accounts", "products", "stores", "units", "groups", "default_sale_store_id"})
        self.assertEqual([row["id"] for row in rows["companies"]], [self.company["id"]])
        self.assertEqual([row["id"] for row in rows["stores"]], [self.store["id"]])
        self.assertEqual(rows["default_sale_store_id"], self.store["id"])
        with self.assertRaises(AuthorizationError):
            self.service.lookup(actor, self.other_company["id"], "sales", action="create")
        with self.assertRaises(AuthorizationError):
            self.service.lookup(actor, self.company["id"], "purchases", action="create")
        self.security.set_permissions(self.admin, user.id, "sales", set())
        with self.assertRaises(AuthorizationError):
            self.service.lookup(actor, self.company["id"], "sales", action="create")

    def test_active_store_memberships_are_separate_and_can_be_shared(self):
        self.assertEqual(self.service.lookup(self.admin, self.other_company["id"], "sales")["stores"], [])
        other = self.create("company_stores", company_id=self.other_company["id"], store_id=self.store["id"])
        self.assertEqual(other["id"], (self.other_company["id"], self.store["id"]))
        self.update("company_stores", self.membership, is_active=False)
        self.assertEqual(self.service.lookup(self.admin, self.company["id"], "sales")["stores"], [])
        self.assertEqual(len(self.service.lookup(self.admin, self.other_company["id"], "sales")["stores"]), 1)
        with self.assertRaises(MasterValidationError):
            self.update("company_stores", other, company_id=self.company["id"])
        self.update("stores", self.store, is_active=False)
        self.assertEqual(self.service.lookup(self.admin, self.other_company["id"], "sales")["stores"], [])

    def test_stale_edit_and_aba_change_with_frozen_clock_are_detected(self):
        original = self.product
        second = self.update("products", original, name="Another name")
        third = self.update("products", second, name=original["name"])
        self.assertEqual(third["updated_at"], original["updated_at"])
        self.assertNotEqual(third["version"], original["version"])
        with self.assertRaises(MasterConflictError):
            self.update("products", original, sale_rate="5")
        with self.assertRaises(MasterValidationError):
            self.service.update(self.admin, "products", original["id"], {"name": "X"}, None)

    def test_concurrent_editors_cannot_silently_overwrite_each_other(self):
        barrier = Barrier(2)

        def edit(number):
            service = MasterService(self.path, security=self.security, clock=self.clock)
            barrier.wait(timeout=10)
            try:
                service.update(self.admin, "products", self.product["id"], {"name": f"Editor {number}"}, self.product["version"])
                return "saved"
            except MasterConflictError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(edit, (1, 2)))
        self.assertCountEqual(outcomes, ["saved", "conflict"])

    def test_account_hierarchy_rejects_long_cycles_and_non_group_parents(self):
        first = self.create("accounts", code="10", name="First", kind="group")
        second = self.create("accounts", code="11", name="Second", kind="group", parent_id=first["id"])
        third = self.create("accounts", code="12", name="Third", kind="group", parent_id=second["id"])
        self.assertEqual(first["is_postable"], 0)
        with self.assertRaisesRegex(MasterValidationError, "cycle"):
            self.update("accounts", first, parent_id=third["id"])
        with self.assertRaises(MasterValidationError):
            self.create("accounts", code="13", name="Invalid parent", parent_id=self.cash["id"])
        with self.assertRaises(MasterValidationError):
            self.update("accounts", first, kind="general", is_postable=True)
        with self.assertRaises(MasterValidationError):
            self.update("accounts", first, is_active=False)
        with self.assertRaises(MasterValidationError):
            self.create("accounts", code="14", name="Bad group", kind="group", is_postable=True)

    def test_product_group_hierarchy_rejects_self_and_long_cycles(self):
        first = self.create("product_groups", code="1", name="First")
        second = self.create("product_groups", code="2", name="Second", parent_id=first["id"])
        third = self.create("product_groups", code="3", name="Third", parent_id=second["id"])
        for parent in (first["id"], third["id"]):
            with self.assertRaisesRegex(MasterValidationError, "cycle"):
                self.update("product_groups", first, parent_id=parent)

    def test_exact_rates_and_unit_factors_do_not_accept_floats_or_overprecision(self):
        product = self.update("products", self.product, sale_rate="9007199254740993.010000", purchase_rate=Decimal("0.000001"))
        self.assertEqual(product["sale_rate"], "9007199254740993.01")
        self.assertEqual(product["purchase_rate"], "0.000001")
        for field, value in (("sale_rate", 1.25), ("sale_rate", True), ("sale_rate", "NaN"), ("purchase_rate", "-1"),
                             ("unit_factor", "0"), ("sale_rate", "1.0000001"), ("sale_rate", "1000000000000000000")):
            with self.subTest(field=field, value_type=type(value).__name__), self.assertRaises(MasterValidationError):
                self.update("products", product, **{field: value})
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT typeof(sale_rate),sale_rate FROM products WHERE id=?", (product["id"],)).fetchone()[1], "9007199254740993.01")

    def test_large_small_unit_factor_and_active_references_are_coherent(self):
        with self.assertRaises(MasterValidationError):
            self.create("products", code="X", name="Invalid factor", small_unit_id=self.unit["id"], unit_factor="12")
        with self.assertRaises(MasterValidationError):
            self.update("products", self.product, large_unit_id=self.unit["id"])
        product = self.update("products", self.product, large_unit_id=None, unit_factor="1")
        self.assertEqual(product["unit_factor"], "1")
        with self.assertRaises(MasterValidationError):
            self.update("units", self.unit, is_active=False)
        product = self.update("products", product, is_active=False)
        self.update("units", self.unit, is_active=False)
        product = self.update("products", product, name="Inactive edited label")
        with self.assertRaisesRegex(MasterValidationError, "inactive"):
            self.update("products", product, is_active=True)

    def test_product_account_mappings_require_active_postable_accounts_but_can_be_unset(self):
        group = self.create("accounts", code="G", name="Group", kind="group")
        inactive = self.create("accounts", code="I", name="Inactive", is_active=False)
        for account in (group, inactive):
            with self.assertRaises(MasterValidationError):
                self.update("products", self.product, inventory_account_id=account["id"])
        with self.assertRaises(MasterValidationError):
            self.update("accounts", self.income, is_active=False)
        unmapped = self.create("products", code="U", name="Unmapped setup", small_unit_id=self.unit["id"])
        rows = self.service.lookup(self.admin, self.company["id"], "sales")
        self.assertIn(unmapped["id"], [row["id"] for row in rows["products"]])

    def test_company_fiscal_dates_cash_account_and_activation_validation(self):
        for values in ({"fiscal_start": "2026-01-01"}, {"fiscal_start": "2026-02-29", "fiscal_end": "2026-12-31"},
                       {"fiscal_start": "0000-01-01", "fiscal_end": "2026-12-31"},
                       {"fiscal_start": "2026-12-31", "fiscal_end": "2026-01-01"}, {"cash_account_id": self.income["id"]}):
            with self.subTest(fields=tuple(values)), self.assertRaises(MasterValidationError):
                self.update("companies", self.company, **values)
        company = self.update("companies", self.company, fiscal_start="2026-01-01", fiscal_end="2026-12-31", cash_account_id=self.cash["id"])
        with self.assertRaises(MasterValidationError):
            self.update("accounts", self.cash, kind="general")
        company = self.update("companies", company, is_active=False)
        with self.assertRaises(AuthorizationError):
            self.service.lookup(self.admin, company["id"], "sales")
        self.assertEqual(self.update("companies", company, is_active=True)["is_active"], 1)

    def test_audit_contains_field_names_not_business_values_and_write_rolls_back_if_audit_fails(self):
        city = self.create("cities", code="SECRET-CODE", name="Private test label")
        changed = self.update("cities", city, name="Another private label")
        with native_session(self.path) as connection:
            events = connection.execute("SELECT action,entity_id,details_json FROM audit_events WHERE entity_type='cities' ORDER BY rowid").fetchall()
        self.assertEqual([row["action"] for row in events], ["masters.created", "masters.updated"])
        self.assertEqual(events[-1]["entity_id"], city["id"])
        self.assertEqual(json.loads(events[-1]["details_json"])["changed_fields"], ["name"])
        self.assertNotIn("Private test", str([dict(row) for row in events]))
        self.assertNotIn("SECRET-CODE", str([dict(row) for row in events]))
        before = self.count("audit_events")
        with patch.object(self.service, "_audit", side_effect=RuntimeError("synthetic audit failure")):
            with self.assertRaises(RuntimeError):
                self.update("cities", changed, name="Should roll back")
            with self.assertRaises(RuntimeError):
                self.create("cities", code="ROLLBACK", name="Never committed")
        self.assertEqual(self.service.get(self.admin, "cities", city["id"]), changed)
        self.assertEqual(self.count("cities"), 1)
        self.assertEqual(self.count("audit_events"), before)

    def test_duplicate_and_invalid_updates_leave_data_audit_and_versions_unchanged(self):
        before = self.count("audit_events")
        with self.assertRaisesRegex(MasterValidationError, "already exists"):
            self.create("units", code="PC", name="Duplicate")
        with self.assertRaises(MasterValidationError):
            self.update("products", self.product, small_unit_id=str(uuid4()))
        self.assertEqual(self.service.get(self.admin, "products", self.product["id"]), self.product)
        self.assertEqual(self.count("audit_events"), before)
        no_change = self.update("products", self.product, name=self.product["name"])
        self.assertEqual(no_change, self.product)
        self.assertEqual(self.count("audit_events"), before)

    def test_sql_payloads_are_data_and_wildcard_search_is_literal(self):
        city = self.create("cities", code="Q' OR 1=1 --", name="Literal 100%_city")
        self.create("cities", code="NORMAL", name="Normal city")
        self.assertEqual(self.service.list_records(self.admin, "cities", search="%"), [city])
        self.assertEqual(self.service.list_records(self.admin, "cities", search="_"), [city])
        self.assertEqual(self.service.list_records(self.admin, "cities", search="' OR 1=1 --"), [city])
        for entity in ("users", "cities; DROP TABLE users", None):
            with self.assertRaises(MasterValidationError):
                self.service.list_records(self.admin, entity)
        with self.assertRaises(MasterValidationError):
            self.create("units", code="X", name="X", **{"name); DROP TABLE users;--": "x"})
        self.assertEqual(self.count("users"), 1)

    def test_pagination_activity_filters_and_validation(self):
        inactive = self.create("units", code="Z", name="Inactive", is_active=False)
        first = self.service.list_records(self.admin, "units", limit=1)
        second = self.service.list_records(self.admin, "units", limit=1, offset=1)
        self.assertNotEqual(first[0]["id"], second[0]["id"])
        self.assertNotIn(inactive["id"], [row["id"] for row in self.service.list_records(self.admin, "units", active_only=True)])
        for kwargs in ({"limit": 0}, {"limit": True}, {"limit": 501}, {"offset": -1}, {"search": 1}, {"active_only": "false"}):
            with self.assertRaises(MasterValidationError):
                self.service.list_records(self.admin, "units", **kwargs)
        for values in ({"code": 1, "name": "Bad code"}, {"code": "X", "name": "bad\0name"},
                       {"code": "X", "name": "X", "is_active": 1.0}, {"code": "X", "name": "X", "id": str(uuid4())}):
            with self.assertRaises(MasterValidationError):
                self.service.create(self.admin, "units", values)

    def test_authorization_shares_native_write_transaction_and_current_rights(self):
        calls = []
        actual_admin = self.security.require_admin
        actual_require = self.security.require

        def observed_admin(actor, *, connection):
            self.assertTrue(connection.in_transaction)
            calls.append("admin")
            return actual_admin(actor, connection=connection)

        def observed_require(actor, company_id, module, action, *, connection):
            self.assertTrue(connection.in_transaction)
            calls.append("lookup")
            return actual_require(actor, company_id, module, action, connection=connection)

        with patch.object(self.security, "require_admin", observed_admin), patch.object(self.security, "require", observed_require):
            row = self.create("cities", code="OBS", name="Observed")
            self.update("cities", row, name="Changed")
            self.service.get(self.admin, "cities", row["id"])
            self.service.list_records(self.admin, "cities")
            self.service.lookup(self.admin, self.company["id"], "sales")
        self.assertEqual(calls, ["admin"] * 4 + ["lookup"])

    def test_constructor_never_creates_or_promotes_other_database(self):
        missing = Path(self.directory.name) / "missing.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            MasterService(missing)
        self.assertFalse(missing.exists())
        other = Path(self.directory.name) / "other.sqlite3"
        with closing(sqlite3.connect(other)) as connection, connection:
            connection.execute("CREATE TABLE archive_data (id TEXT)")
        before = other.read_bytes()
        with self.assertRaises(NativeSchemaError):
            MasterService(other)
        self.assertEqual(other.read_bytes(), before)

    def test_unknown_record_immutable_membership_and_invalid_lookup_are_explicit(self):
        with self.assertRaises(MasterNotFoundError):
            self.service.get(self.admin, "products", str(uuid4()))
        with self.assertRaises(MasterValidationError):
            self.service.get(self.admin, "company_stores", self.company["id"])
        for module in ("users", "settings", "invented", None):
            with self.assertRaises(MasterValidationError):
                self.service.lookup(self.admin, self.company["id"], module)


if __name__ == "__main__":
    unittest.main()
