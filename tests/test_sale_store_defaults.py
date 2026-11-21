"""Sale location defaults use synthetic history and current company access."""

from pathlib import Path
import tempfile
import unittest

from bifma.baseline import ensure_baseline
from bifma.database import session
from bifma.masters import MasterService
from bifma.native_schema import native_session, native_transaction
from bifma.security import AuthenticationError, AuthorizationError, SecurityService
from bifma.transactions import TransactionService
from tests.test_baseline_import import synthetic_archive
from tests.test_transactions import TransactionFixture


class OriginalSaleStoreTests(unittest.TestCase):
    def prepare(self, *, conflicting=False, cash_other_store=False):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        source = Path(directory.name) / "synthetic_archive.sqlite3"
        self.path = Path(directory.name) / "synthetic_working.sqlite3"
        synthetic_archive(source)
        with session(source, readonly=False) as connection:
            # An unused location sorts before the historical sale location.
            connection.execute("INSERT INTO STORE_LOC(STORE_ID,STORE_DESC,CMP_ID) VALUES('0','Other location','1')")
            connection.execute("INSERT INTO CONFIG(CMP_ID,CMPNAME,CHCODE_CASH,SDATE,EDATE) "
                               "VALUES('2','Other company','0001','2025-01-01T00:00:00','2027-12-31T00:00:00')")
            connection.execute("INSERT INTO STORE_LOC(STORE_ID,STORE_DESC,CMP_ID) VALUES('3','Other company location','2')")
            connection.execute("INSERT INTO SMST(TRNID,TRNNO,TRNDATE,TRN_TYPE,CMP_ID,CHCODE,S_TYPE) "
                               "VALUES('2','260002','2026-01-02T00:00:00','8','2','0002','R')")
            connection.execute("INSERT INTO SDTL(TRNID,VNO,LINE_NUM,STORE_ID,ICODE,QTY,RATE,UOM_ID_SMALL) "
                               "VALUES('2','2','1','3','A','1','10','1')")
            if conflicting:
                connection.execute("INSERT INTO SDTL(TRNID,VNO,LINE_NUM,STORE_ID,ICODE,QTY,RATE,UOM_ID_SMALL) "
                                   "VALUES('1','3','2','0','A','1','10','1')")
            if cash_other_store:
                connection.execute("INSERT INTO CHART(CHCODE,CHDESC,FK_CCODE,ALLOWED) "
                                   "VALUES('04010001','Original cash account','1','1')")
                connection.execute("INSERT INTO SMST(TRNID,TRNNO,TRNDATE,TRN_TYPE,CMP_ID,CHCODE,S_TYPE) "
                                   "VALUES('3','260003','2026-01-02T00:00:00','8','1','0002','C')")
                connection.execute("INSERT INTO SDTL(TRNID,VNO,LINE_NUM,STORE_ID,ICODE,QTY,RATE,UOM_ID_SMALL) "
                                   "VALUES('3','3','1','0','A','1','10','1')")
        ensure_baseline(source, self.path)
        self.security = SecurityService(self.path)
        self.actor = self.security.bootstrap_admin("owner", "Silver lanterns orbit quietly 72")
        self.masters = MasterService(self.path, security=self.security)
        self.transactions = TransactionService(self.path, security=self.security)
        with native_session(self.path) as connection:
            self.companies = {row["code"]: row["id"] for row in connection.execute("SELECT id,code FROM companies")}
            self.stores = {row["code"]: row["id"] for row in connection.execute("SELECT id,code FROM stores")}
            self.product = connection.execute("SELECT id FROM products").fetchone()[0]
            self.account = connection.execute("SELECT id FROM accounts WHERE code='0002'").fetchone()[0]

    def lookup(self, company="1"):
        return self.masters.lookup(self.actor, self.companies[company], "sales", "create")

    def post(self, kind, store, number):
        draft = self.transactions.save_draft(self.actor, self.companies["1"], kind, {
            "document_number": number, "business_date": "2026-01-04", "account_id": self.account,
            "rounding_policy": "legacy_whole", "posting_places": 0,
            "lines": [{"product_id": self.product, "store_id": store, "quantity": "2", "rate": "10"}],
        })
        return self.transactions.post(self.actor, self.companies["1"], draft["id"], draft["revision"])

    def inactive(self, code):
        row = self.masters.get(self.actor, "company_stores", (self.companies["1"], self.stores[code]))
        self.masters.update(self.actor, "company_stores", row["id"], {"is_active": 0}, row["version"])

    def test_original_sale_location_wins_over_sorted_choices_and_new_postings(self):
        self.prepare()
        choices = self.lookup()
        self.assertEqual([row["code"] for row in choices["stores"]], ["0", "1"])
        self.assertEqual(choices["default_sale_store_id"], self.stores["1"])
        self.post("purchase", self.stores["0"], "P-NEW")
        self.post("sale", self.stores["0"], "S-OTHER")
        self.assertEqual(self.lookup()["default_sale_store_id"], self.stores["1"])
        # The selected default remains a valid, stock-funded sale input.
        saved = self.post("sale", self.lookup()["default_sale_store_id"], "S-DEFAULT")
        self.assertEqual(saved["lines"][0]["store_id"], self.stores["1"])
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT store_id FROM baseline_trade_lines WHERE company_id=?",
                                                (self.companies["1"],)).fetchone()[0], self.stores["1"])

    def test_company_and_credit_sale_evidence_remain_separate(self):
        self.prepare(cash_other_store=True)
        self.assertEqual(self.lookup()["default_sale_store_id"], self.stores["1"])
        self.assertEqual(self.lookup("2")["default_sale_store_id"], self.stores["3"])

    def test_conflicting_original_locations_do_not_disappear_when_one_becomes_inactive(self):
        self.prepare(conflicting=True)
        self.assertIsNone(self.lookup()["default_sale_store_id"])
        self.inactive("0")
        self.assertEqual([row["id"] for row in self.lookup()["stores"]], [self.stores["1"]])
        self.assertIsNone(self.lookup()["default_sale_store_id"])

    def test_inactive_original_location_does_not_fall_back_to_new_sale_or_sole_active_store(self):
        self.prepare()
        self.post("purchase", self.stores["0"], "P-NEW")
        self.post("sale", self.stores["0"], "S-NEW")
        self.inactive("1")
        choices = self.lookup()
        self.assertEqual([row["id"] for row in choices["stores"]], [self.stores["0"]])
        self.assertIsNone(choices["default_sale_store_id"])


class NativeSaleStoreTests(TransactionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.masters = MasterService(self.path, security=self.security, clock=self.clock)

    def lookup(self, *, actor=None, company=None, action="create"):
        return self.masters.lookup(actor or self.actor, company or self.company, "sales", action)

    def add_second_location(self):
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO company_stores(company_id,store_id) VALUES(?,?)",
                               (self.company, self.other_store))

    def post_at(self, kind, store):
        values = self.trade_values(kind)
        values["lines"][0]["store_id"] = store
        return self.post(self.save(kind, values))

    def inactive(self, store, *, membership=True):
        entity, identifier = ("company_stores", (self.company, store)) if membership else ("stores", store)
        row = self.masters.get(self.actor, entity, identifier)
        self.masters.update(self.actor, entity, row["id"], {"is_active": 0}, row["version"])

    def test_sole_location_default_and_posted_sales_fallback_ignore_drafts_and_other_kinds(self):
        self.assertEqual(self.lookup()["default_sale_store_id"], self.store)
        self.assertNotIn("default_sale_store_id", self.masters.lookup(self.actor, self.company, "purchases"))
        self.add_second_location()
        self.assertIsNone(self.lookup()["default_sale_store_id"])
        draft = self.save("sale", self.trade_values())
        self.post_at("purchase", self.store)
        self.assertIsNone(self.lookup()["default_sale_store_id"])
        self.post(draft)
        self.assertEqual(self.lookup()["default_sale_store_id"], self.store)
        # A saved draft at a different location cannot change the established default.
        values = self.trade_values()
        values["lines"][0]["store_id"] = self.other_store
        self.save("sale", values)
        self.assertEqual(self.lookup()["default_sale_store_id"], self.store)

    def test_conflicting_posted_locations_stay_ambiguous_after_membership_deactivation(self):
        self.add_second_location()
        for store in (self.store, self.other_store):
            self.post_at("purchase", store)
            self.post_at("sale", store)
        self.assertIsNone(self.lookup()["default_sale_store_id"])
        self.inactive(self.other_store)
        self.assertIsNone(self.lookup()["default_sale_store_id"])

    def test_inactive_posted_location_does_not_redirect_to_sole_active_location(self):
        self.add_second_location()
        self.post_at("purchase", self.store)
        self.post_at("sale", self.store)
        self.inactive(self.store, membership=False)
        choices = self.lookup()
        self.assertEqual([row["id"] for row in choices["stores"]], [self.other_store])
        self.assertIsNone(choices["default_sale_store_id"])

    def test_reversing_a_sale_does_not_erase_its_recorded_location_evidence(self):
        self.add_second_location()
        self.post_at("purchase", self.store)
        sale = self.post_at("sale", self.store)
        self.assertEqual(self.lookup()["default_sale_store_id"], self.store)
        self.reverse(sale)
        self.assertEqual(self.lookup()["default_sale_store_id"], self.store)

    def test_default_lookup_requires_fresh_company_action_and_session_authorization(self):
        password = "Cobalt bridges welcome sunrise 83"
        user = self.security.create_user(self.actor, "sale_entry", password)
        operator = self.security.authenticate("sale_entry", password)
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.set_permissions(self.actor, user.id, "sales", ["create"])
        self.assertEqual(self.lookup(actor=operator)["default_sale_store_id"], self.store)
        with self.assertRaises(AuthorizationError):
            self.lookup(actor=operator, action="read")
        with self.assertRaises(AuthorizationError):
            self.lookup(actor=operator, company=self.other_company)
        self.security.set_permissions(self.actor, user.id, "sales", [])
        with self.assertRaises(AuthorizationError):
            self.lookup(actor=operator)
        self.security.set_permissions(self.actor, user.id, "sales", ["create"])
        self.security.set_company_access(self.actor, user.id, [])
        with self.assertRaises(AuthorizationError):
            self.lookup(actor=operator)
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.logout(operator)
        with self.assertRaises(AuthenticationError):
            self.lookup(actor=operator)


if __name__ == "__main__":
    unittest.main()
