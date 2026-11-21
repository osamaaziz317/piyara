"""Optional master contact storage and explicit migration, with synthetic data."""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma.database import session
from bifma.masters import MasterConflictError, MasterService, MasterValidationError
from bifma import native_schema as native
from bifma.security import AuthorizationError, SecurityService


COMPANY_FIELDS = ("address", "address_urdu", "phone", "fax", "email", "website", "sales_tax_number", "national_tax_number")
ACCOUNT_FIELDS = ("address", "phone", "mobile", "email", "website", "sales_tax_number", "national_tax_number")
LIMITS = {"address": 500, "address_urdu": 500, "phone": 80, "fax": 80, "mobile": 80,
          "email": 254, "website": 2048, "sales_tax_number": 80, "national_tax_number": 80}
BASELINE_CHECKSUMS = (
    "4fe764da2b83db268aafaad4d191f5bbc2840bc7bd141f24f64205d96b96c462",
    "ec12d8a891bf47ad17e910144f5bff74c64a43b9dd900869b5abf7f3ed60245a",
)


class MasterContactTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "contacts.sqlite3"
        native.initialize_database(self.path)
        self.clock = lambda: 1_800_000_000
        self.security = SecurityService(self.path, clock=self.clock)
        self.admin = self.security.bootstrap_admin("administrator", "Silver lanterns orbit quietly 72")
        self.masters = MasterService(self.path, self.security, self.clock)
        self.company = self.masters.create(self.admin, "companies", {"code": "C1", "name": "Synthetic company"})
        self.account = self.masters.create(self.admin, "accounts", {"code": "A1", "name": "Synthetic account"})

    def update(self, entity, record, **values):
        return self.masters.update(self.admin, entity, record["id"], values, record["version"])

    def test_create_get_list_and_lookup_preserve_contacts_and_leading_zero_identifiers(self):
        company_values = {
            "code": "C2", "name": "Second synthetic company", "address": "  Cafe\u0301 Road  ",
            "address_urdu": " مصنوعی پتہ ", "phone": "001-234 ext. A", "fax": "000-040",
            "email": "Accounts@Example.INVALID", "website": "example.invalid/path",
            "sales_tax_number": "000123-A/۲", "national_tax_number": "000007",
        }
        company = self.masters.create(self.admin, "companies", company_values)
        account_values = {"code": "A2", "name": "Second synthetic account", "kind": "customer",
                          **{field: company_values[field] for field in ACCOUNT_FIELDS if field != "mobile"},
                          "mobile": "+00 (001) 000-002"}
        account = self.masters.create(self.admin, "accounts", account_values)
        self.assertEqual(company["address"], "Café Road")
        self.assertEqual(account["address"], "Café Road")
        self.assertEqual(company["address_urdu"], "مصنوعی پتہ")
        self.assertEqual(company["sales_tax_number"], "000123-A/۲")
        self.assertEqual(account["national_tax_number"], "000007")
        self.assertEqual(account["mobile"], "+00 (001) 000-002")
        self.assertEqual(self.masters.get(self.admin, "companies", company["id"]), company)
        self.assertEqual(self.masters.get(self.admin, "accounts", account["id"]), account)
        self.assertEqual(self.masters.list_records(self.admin, "accounts", search="A2"), [account])
        choices = self.masters.lookup(self.admin, company["id"], "sales")
        self.assertEqual(choices["companies"], [company])
        self.assertEqual(next(row for row in choices["accounts"] if row["id"] == account["id"]), account)
        with native.native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT national_tax_number,typeof(national_tax_number) FROM accounts WHERE id=?", (account["id"],)).fetchone()[:], ("000007", "text"))

    def test_defaults_are_null_and_blank_updates_clear_fields(self):
        for entity, original, fields in (("companies", self.company, COMPANY_FIELDS), ("accounts", self.account, ACCOUNT_FIELDS)):
            self.assertTrue(all(original[field] is None for field in fields))
            filled = self.update(entity, original, **{field: "000-contact" for field in fields})
            cleared = self.update(entity, filled, **{field: " \t " if index % 2 else None for index, field in enumerate(fields)})
            self.assertTrue(all(cleared[field] is None for field in fields))
            self.assertNotEqual(filled["version"], cleared["version"])

    def test_text_boundaries_are_enforced_by_service_and_sqlite(self):
        for entity, original, fields in (("companies", self.company, COMPANY_FIELDS), ("accounts", self.account, ACCOUNT_FIELDS)):
            current = original
            for field in fields:
                maximum = LIMITS[field]
                current = self.update(entity, current, **{field: "X" * maximum})
                self.assertEqual(len(current[field]), maximum)
                with self.subTest(entity=entity, field=field), self.assertRaises(MasterValidationError):
                    self.update(entity, current, **{field: "X" * (maximum + 1)})
                with native.native_transaction(self.path) as connection:
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(f"UPDATE {entity} SET {field}=? WHERE id=?", ("X" * (maximum + 1), current["id"]))
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(f"UPDATE {entity} SET {field}=? WHERE id=?", ("embedded\x00nul", current["id"]))

    def test_nontext_and_embedded_control_values_never_coerce_or_update(self):
        invalid = (0, 0.0, False, b"0001", [], {}, "A\nB", "A\tB", "A\x00B", "A\ud800B")
        for entity, original, fields in (("companies", self.company, COMPANY_FIELDS), ("accounts", self.account, ACCOUNT_FIELDS)):
            for field in fields:
                for value in invalid:
                    with self.subTest(entity=entity, field=field, value_type=type(value).__name__), self.assertRaises(MasterValidationError):
                        self.update(entity, original, **{field: value})
            self.assertEqual(self.masters.get(self.admin, entity, original["id"]), original)

    def test_fields_are_opaque_text_not_email_phone_or_tax_validation(self):
        changed = self.update("accounts", self.account, phone="office extension alpha", mobile="000-phone",
                              email="unverified contact text", website="intranet host / path", sales_tax_number="00 / not-validated",
                              national_tax_number="000ABC")
        self.assertEqual(changed["email"], "unverified contact text")
        self.assertEqual(changed["website"], "intranet host / path")
        self.assertEqual(changed["sales_tax_number"], "00 / not-validated")
        for entity, record, fields in (("companies", self.company, ("mobile", "sms_password", "gst_rate")),
                                       ("accounts", changed, ("address_urdu", "fax", "default_discount", "sms_password"))):
            for field in fields:
                with self.subTest(entity=entity, field=field), self.assertRaises(MasterValidationError):
                    self.update(entity, record, **{field: "ignored?"})

    def test_contact_mutation_is_admin_only_and_lookup_remains_company_module_scoped(self):
        user = self.security.create_user(self.admin, "reader", "Copper meadows welcome sunrise 83")
        actor = self.security.authenticate("reader", "Copper meadows welcome sunrise 83")
        self.security.set_company_access(self.admin, user.id, [self.company["id"]])
        self.security.set_permissions(self.admin, user.id, "sales", {"read", "create", "update"})
        company = self.update("companies", self.company, phone="001-selected-company")
        account = self.update("accounts", self.account, mobile="000-shared-account")
        other = self.masters.create(self.admin, "companies", {"code": "C2", "name": "Other company", "phone": "other company contact"})
        with self.assertRaises(AuthorizationError):
            self.masters.update(actor, "accounts", account["id"], {"mobile": "changed"}, account["version"])
        with self.assertRaises(AuthorizationError):
            self.masters.create(actor, "companies", {"code": "NO", "name": "Not allowed", "email": "no"})
        with self.assertRaises(AuthorizationError):
            self.masters.get(actor, "companies", company["id"])
        choices = self.masters.lookup(actor, company["id"], "sales")
        self.assertEqual(choices["companies"], [company])
        self.assertEqual(next(row for row in choices["accounts"] if row["id"] == account["id"])["mobile"], "000-shared-account")
        with self.assertRaises(AuthorizationError):
            self.masters.lookup(actor, other["id"], "sales")
        self.security.set_permissions(self.admin, user.id, "sales", set())
        with self.assertRaises(AuthorizationError):
            self.masters.lookup(actor, company["id"], "sales")

    def test_contact_edits_use_stale_versions_and_audit_contains_field_names_only(self):
        payload = "000 confidential synthetic contact"
        changed = self.update("companies", self.company, phone=payload, sales_tax_number="000123")
        with self.assertRaises(MasterConflictError):
            self.update("companies", self.company, email="stale edit")
        with native.native_session(self.path) as connection:
            detail = connection.execute("SELECT details_json FROM audit_events WHERE entity_id=? AND action='masters.updated'", (changed["id"],)).fetchone()[0]
        decoded = json.loads(detail)
        self.assertEqual(decoded["changed_fields"], ["phone", "sales_tax_number"])
        self.assertEqual(decoded["previous_version"], self.company["version"])
        self.assertNotIn(payload, detail)
        self.assertNotIn("000123", detail)
        with patch.object(self.masters, "_audit", side_effect=RuntimeError("Synthetic audit failure")), self.assertRaises(RuntimeError):
            self.update("companies", changed, phone="must roll back")
        self.assertEqual(self.masters.get(self.admin, "companies", changed["id"]), changed)


class ContactMigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "version-two.sqlite3"
        self.company, self.debit, self.credit, self.document, self.journal = [str(uuid4()) for _ in range(5)]
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS[:2]):
            native.initialize_database(self.path)
            security = SecurityService(self.path)
            admin = security.bootstrap_admin("owner", "Silver lanterns orbit quietly 72")
            with native.native_transaction(self.path) as connection:
                connection.execute("INSERT INTO companies(id,code,name) VALUES(?, 'OLD', 'Version two company')", (self.company,))
                for identifier, code in ((self.debit, "DR"), (self.credit, "CR")):
                    connection.execute("INSERT INTO accounts(id,code,name) VALUES(?,?,?)", (identifier, code, "Version two " + code))
                connection.execute(
                    "INSERT INTO documents(id,company_id,kind,document_number,business_date,total_amount,rounding_policy,posting_places,created_by) VALUES(?,?,'journal','POSTED-OLD','2026-10-02','1','cents',2,?)",
                    (self.document, self.company, admin.user.id))
                connection.execute("INSERT INTO journal_entries(id,company_id,document_id) VALUES(?,?,?)", (self.journal, self.company, self.document))
                for index, account, debit, credit in ((1, self.debit, "1", "0"), (2, self.credit, "0", "1")):
                    connection.execute("INSERT INTO journal_lines(id,company_id,entry_id,line_number,account_id,debit,credit) VALUES(?,?,?,?,?,?,?)",
                                       (str(uuid4()), self.company, self.journal, index, account, debit, credit))
                self.snapshot = '{"company":{"id":"' + self.company + '","name":"Issued original label"},"accounts":{},"lines":[]}'
                connection.execute("INSERT INTO document_metadata(document_id,company_id,input_json,snapshot_json,calculation_json) VALUES(?,?,?,?,?)",
                                   (self.document, self.company, '{"notes":"original input"}', self.snapshot, '{"total":"1"}'))
                connection.execute("UPDATE documents SET status='posted',revision=1,posted_by=?,posted_at=1,posting_sequence=1 WHERE id=?", (admin.user.id, self.document))
        with session(self.path) as connection:
            self.before_company = dict(connection.execute("SELECT * FROM companies WHERE id=?", (self.company,)).fetchone())
            self.before_accounts = [dict(row) for row in connection.execute("SELECT * FROM accounts ORDER BY id")]
            self.before_documents = [tuple(row) for row in connection.execute("SELECT * FROM documents ORDER BY id")]
            self.before_metadata = [tuple(row) for row in connection.execute("SELECT * FROM document_metadata ORDER BY document_id")]
            self.before_journal = [tuple(row) for row in connection.execute("SELECT * FROM journal_lines ORDER BY id")]

    def test_migrations_one_and_two_keep_their_pristine_checksums(self):
        self.assertEqual(tuple(migration.checksum for migration in native.MIGRATIONS[:2]), BASELINE_CHECKSUMS)
        self.assertEqual(native.MIGRATIONS[2].name, "master_contacts_and_tax_identifiers")
        self.assertEqual(native.MIGRATIONS[2].version, 3)

    def test_version_two_upgrade_is_explicit_backed_up_and_preserves_posted_history(self):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(native.NativeSchemaError, "upgrade is required"):
            MasterService(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        result = native.migrate_database(self.path)
        self.assertEqual(result.version, native.MIGRATIONS[-1].version)
        self.assertEqual(result.applied_versions, tuple(m.version for m in native.MIGRATIONS[2:]))
        self.assertTrue(result.backup_path.is_file())
        with session(result.backup_path) as connection:
            native.prepare_connection(connection)
            self.assertEqual(native.require_native(connection, require_current=False), 2)
            self.assertEqual(dict(connection.execute("SELECT * FROM companies WHERE id=?", (self.company,)).fetchone()), self.before_company)
            self.assertNotIn("address", {row[1] for row in connection.execute("PRAGMA table_info(companies)")})
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM document_metadata ORDER BY document_id")], self.before_metadata)
        with native.native_session(self.path) as connection:
            after = dict(connection.execute("SELECT * FROM companies WHERE id=?", (self.company,)).fetchone())
            self.assertEqual({key: after[key] for key in self.before_company}, self.before_company)
            self.assertTrue(all(after[field] is None for field in COMPANY_FIELDS))
            accounts = [dict(row) for row in connection.execute("SELECT * FROM accounts ORDER BY id")]
            for old, updated in zip(self.before_accounts, accounts):
                self.assertEqual({key: updated[key] for key in old}, old)
                self.assertTrue(all(updated[field] is None for field in ACCOUNT_FIELDS))
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM documents ORDER BY id")], self.before_documents)
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM document_metadata ORDER BY document_id")], self.before_metadata)
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM journal_lines ORDER BY id")], self.before_journal)
        security = SecurityService(self.path)
        actor = security.authenticate("owner", "Silver lanterns orbit quietly 72")
        masters = MasterService(self.path, security)
        company = masters.get(actor, "companies", self.company)
        masters.update(actor, "companies", self.company, {"address": "New current address"}, company["version"])
        with native.native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT snapshot_json FROM document_metadata WHERE document_id=?", (self.document,)).fetchone()[0], self.snapshot)
        self.assertEqual(native.migrate_database(self.path).applied_versions, ())

    def test_failed_contact_upgrade_rolls_back_all_columns_and_keeps_version_two_backup(self):
        broken = native.Migration(3, native.MIGRATIONS[2].name,
                                  native.MIGRATIONS[2].statements + ("INSERT INTO missing_contact_table VALUES ('failure')",))
        with patch.object(native, "MIGRATIONS", native.MIGRATIONS[:2] + (broken,)):
            with self.assertRaises(sqlite3.OperationalError):
                native.migrate_database(self.path)
        with session(self.path) as connection:
            native.prepare_connection(connection)
            self.assertEqual(native.require_native(connection, require_current=False), 2)
            self.assertNotIn("address", {row[1] for row in connection.execute("PRAGMA table_info(companies)")})
            self.assertNotIn("phone", {row[1] for row in connection.execute("PRAGMA table_info(accounts)")})
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM document_metadata ORDER BY document_id")], self.before_metadata)
        backups = list(self.path.parent.glob("*.preupgrade-v2-to-v3-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        with session(backups[0]) as connection:
            native.prepare_connection(connection)
            self.assertEqual(native.require_native(connection, require_current=False), 2)


if __name__ == "__main__":
    unittest.main()
