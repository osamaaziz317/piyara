"""Posted-only, company-scoped exact reports against synthetic transactions."""

from decimal import Context, Decimal, localcontext
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from bifma.native_schema import initialize_database, native_transaction
from bifma.native_reports import NativeReports
from bifma.security import AuthorizationError, SecurityService
from bifma.transactions import TransactionService


class NativeReportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "reports.sqlite3"
        initialize_database(self.path)
        self.security = SecurityService(self.path)
        self.admin = self.security.bootstrap_admin("owner", "Meadow copper 87 river window")
        self.reports = NativeReports(self.path, security=self.security)
        self.company, self.other, self.debit, self.credit, self.product, self.store, self.unit = [str(uuid4()) for _ in range(7)]
        with native_transaction(self.path) as connection:
            connection.executemany("INSERT INTO companies(id,code,name) VALUES (?,?,?)", [(self.company, "A", "Company A"), (self.other, "B", "Company B")])
            connection.executemany("INSERT INTO accounts(id,code,name) VALUES (?,?,?)", [(self.debit, "DEBIT", "Debit account"), (self.credit, "CREDIT", "Credit account")])
            connection.execute("INSERT INTO units(id,code,name) VALUES (?,'PCS','Pieces')", (self.unit,))
            connection.execute("INSERT INTO stores(id,code,name) VALUES (?,'STORE','Store')", (self.store,))
            connection.execute("INSERT INTO company_stores(company_id,store_id) VALUES (?,?)", (self.company, self.store))
            connection.execute("INSERT INTO products(id,code,name,small_unit_id) VALUES (?,'ITEM','Item',?)", (self.product, self.unit))

    def stock_item(self, code, *, active=True):
        identifier = str(uuid4())
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO products(id,code,name,small_unit_id,is_active) VALUES (?,?,?,?,?)", (identifier, code, code, self.unit, int(active)))
        return identifier

    def stock_store(self, code, *, company=None):
        identifier = str(uuid4())
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO stores(id,code,name) VALUES (?,?,?)", (identifier, code, code))
            connection.execute("INSERT INTO company_stores(company_id,store_id) VALUES (?,?)", (company or self.company, identifier))
        return identifier

    def stock_document(self, number, business_date, lines, *, company=None, sequence=None, posted=True, posted_at=1, snapshot=False, identifier=None):
        """Synthetic posted quantity rows; snapshots are saved before status changes."""
        company, identifier = company or self.company, identifier or str(uuid4())
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO documents(id,company_id,kind,document_number,business_date,total_amount,rounding_policy,posting_places,created_by) VALUES (?,?,'stock_opening',?,?,'0','cents',2,?)", (identifier, company, number, business_date, self.admin.user.id))
            saved = {"company": dict(connection.execute("SELECT * FROM companies WHERE id=?", (company,)).fetchone()), "accounts": {}, "lines": []}
            for index, source in enumerate(lines, 1):
                product, store = source.get("product_id", self.product), source.get("store_id", self.store)
                line, movement = str(uuid4()), source.get("movement_id", str(uuid4()))
                connection.execute("INSERT INTO document_lines(id,company_id,document_id,line_number,product_id,store_id,quantity) VALUES (?,?,?,?,?,?,?)", (line, company, identifier, index, product, store, source["quantity"]))
                connection.execute("INSERT INTO stock_movements(id,company_id,document_id,document_line_id,product_id,store_id,direction,quantity,pool,valuation_basis) VALUES (?,?,?,?,?,?,?,?,?,'unvalued')", (movement, company, identifier, line, product, store, source.get("direction", "in"), source["quantity"], source.get("pool", "regular")))
                saved["lines"].append({"line_id": line, "product": dict(connection.execute("SELECT * FROM products WHERE id=?", (product,)).fetchone()), "store": dict(connection.execute("SELECT * FROM stores WHERE id=?", (store,)).fetchone())})
            if snapshot:
                connection.execute("INSERT INTO document_metadata(document_id,company_id,input_json,snapshot_json,calculation_json) VALUES (?,?,?,?,?)", (identifier, company, json.dumps({"document_number": number, "business_date": business_date}), json.dumps(saved), "{}"))
            if posted:
                connection.execute("UPDATE documents SET status='posted',revision=1,posted_by=?,posted_at=?,posting_sequence=? WHERE id=?", (self.admin.user.id, posted_at, sequence, identifier))
        return identifier

    def document(self, number, business_date, amount, *, company=None, posted=True, quantity=None, pool="regular", direction="in"):
        company = company or self.company
        document, journal = str(uuid4()), str(uuid4())
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO documents(id,company_id,kind,document_number,business_date,total_amount,rounding_policy,posting_places,created_by) VALUES (?,?,'journal',?,?,?,'cents',2,?)", (document, company, number, business_date, amount, self.admin.user.id))
            connection.execute("INSERT INTO journal_entries(id,company_id,document_id) VALUES (?,?,?)", (journal, company, document))
            connection.executemany("INSERT INTO journal_lines(id,company_id,entry_id,line_number,account_id,debit,credit) VALUES (?,?,?,?,?,?,?)", [(str(uuid4()), company, journal, 1, self.debit, amount, "0"), (str(uuid4()), company, journal, 2, self.credit, "0", amount)])
            if quantity:
                line = str(uuid4())
                connection.execute("INSERT INTO document_lines(id,company_id,document_id,line_number,product_id,store_id,quantity) VALUES (?,?,?,1,?,?,?)", (line, company, document, self.product, self.store, quantity))
                connection.execute("INSERT INTO stock_movements(id,company_id,document_id,document_line_id,product_id,store_id,direction,quantity,pool) VALUES (?,?,?,?,?,?,?,?,?)", (str(uuid4()), company, document, line, self.product, self.store, direction, quantity, pool))
            if posted:
                connection.execute("UPDATE documents SET status='posted',revision=1,posted_by=?,posted_at=1 WHERE id=?", (self.admin.user.id, document))
        return document

    def test_exact_opening_period_and_closing_exclude_drafts_and_other_company(self):
        self.document("OLD", "2026-09-30", "9007199254740993.01")
        self.document("ONE", "2026-10-01", "0.1")
        self.document("TWO", "2026-10-02", "0.2")
        self.document("FUTURE", "2026-10-03", "7")
        self.document("DRAFT", "2026-10-01", "99", posted=False)
        self.document("OTHER", "2026-10-01", "88", company=self.other)
        result = self.reports.trial_balance(self.admin, self.company, date_from="2026-10-01", date_to="2026-10-02")
        debit = next(row for row in result["rows"] if row["account_id"] == self.debit)
        self.assertEqual(debit["opening"], Decimal("9007199254740993.01"))
        self.assertEqual(debit["debit"], Decimal("0.3"))
        self.assertEqual(debit["closing"], Decimal("9007199254740993.31"))
        self.assertEqual(result["totals"]["closing"], 0)
        self.assertEqual(result["totals"]["debit"], result["totals"]["credit"])

    def test_ledger_paging_keeps_carry_forward_and_full_period_totals(self):
        self.document("OPEN", "2026-09-30", "10")
        self.document("FIRST", "2026-10-01", "0.1")
        self.document("SECOND", "2026-10-02", "0.2")
        result = self.reports.ledger(self.admin, self.company, self.debit, date_from="2026-10-01", limit=1, offset=1)
        self.assertEqual((result["opening"], result["closing"], result["total"]), (Decimal("10"), Decimal("10.3"), 2))
        self.assertEqual(result["rows"][0]["balance"], Decimal("10.3"))
        self.assertEqual(result["rows"][0]["document_number"], "SECOND")

    def test_inactive_formerly_postable_account_keeps_accessible_history(self):
        self.document("HISTORY", "2026-10-01", "25")
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET is_active=0,is_postable=0 WHERE id=?", (self.debit,))
        choices = self.reports.accounts(self.admin, self.company)
        self.assertIn(self.debit, [row["id"] for row in choices])
        self.assertEqual(self.reports.ledger(self.admin, self.company, self.debit)["closing"], Decimal("25"))

    def test_stock_pools_dates_and_outward_dispatch(self):
        self.document("OPEN", "2026-09-30", "1", quantity="10")
        self.document("OUT", "2026-10-01", "1", quantity="2.125", direction="out")
        self.document("BREAK", "2026-10-01", "1", quantity="4", pool="breakage")
        self.document("DISPATCH", "2026-10-02", "1", quantity="1", pool="breakage", direction="out")
        regular = self.reports.stock(self.admin, self.company, date_from="2026-10-01")[0]
        self.assertEqual((regular["opening"], regular["out"], regular["closing"]), (Decimal("10"), Decimal("2.125"), Decimal("7.875")))
        self.assertEqual(self.reports.stock(self.admin, self.company, pool="breakage")[0]["closing"], Decimal("3"))

    def test_unclassified_movement_is_rejected_instead_of_excluded(self):
        self.document("UNKNOWN", "2026-10-01", "1", quantity="2", pool=None)
        with self.assertRaisesRegex(ValueError, "no verified stock pool"):
            self.reports.stock(self.admin, self.company)

    def test_reports_recheck_permission_and_company_grant(self):
        user = self.security.create_user(self.admin, "reader", "Silver orchard 63 green river")
        actor = self.security.authenticate("reader", "Silver orchard 63 green river")
        self.security.set_company_access(self.admin, user.id, [self.company])
        self.security.set_permissions(self.admin, user.id, "trial_balance", ["read"])
        self.reports.trial_balance(actor, self.company)
        with self.assertRaises(AuthorizationError):
            self.reports.trial_balance(actor, self.other)
        self.security.set_permissions(self.admin, user.id, "trial_balance", [])
        with self.assertRaises(AuthorizationError):
            self.reports.trial_balance(actor, self.company)

    def test_invalid_report_filters_fail_before_query(self):
        with self.assertRaises(ValueError):
            self.reports.trial_balance(self.admin, self.company, date_from="2026-10-03", date_to="2026-10-01")
        with self.assertRaises(ValueError):
            self.reports.stock(self.admin, self.company, date_to="2026-02-30")
        with self.assertRaises(ValueError):
            self.reports.ledger(self.admin, self.company, self.debit, limit=True)

    def test_stock_ledger_exact_totals_and_page_carry_preserve_large_fractional_quantities(self):
        self.stock_document("OPEN", "2026-09-30", [{"quantity": "9007199254740993.123456"}])
        self.stock_document("IN", "2026-10-01", [{"quantity": "0.1"}])
        self.stock_document("OUT", "2026-10-02", [{"quantity": "0.125", "direction": "out"}])
        self.stock_document("FUTURE", "2026-10-03", [{"quantity": "99"}])
        self.stock_document("DRAFT", "2026-10-02", [{"quantity": "99"}], posted=False)
        with localcontext(Context(prec=3)):
            result = self.reports.stock_ledger(self.admin, self.company, self.product, date_from="2026-10-01", date_to="2026-10-02", offset=1, limit=1)
        self.assertEqual((result["opening"], result["in"], result["out"], result["closing"]), (Decimal("9007199254740993.123456"), Decimal("0.1"), Decimal("0.125"), Decimal("9007199254740993.098456")))
        self.assertEqual((result["total"], result["offset"], result["limit"]), (2, 1, 1))
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual((row["document_number"], row["in"], row["out"], row["balance"]), ("OUT", Decimal("0"), Decimal("0.125"), result["closing"]))
        self.assertIsInstance(row["quantity"], Decimal)
        empty_page = self.reports.stock_ledger(self.admin, self.company, self.product, date_from="2026-10-01", date_to="2026-10-02", offset=20)
        self.assertEqual(empty_page["rows"], [])
        self.assertEqual((empty_page["total"], empty_page["opening"], empty_page["closing"]), (2, result["opening"], result["closing"]))

    def test_stock_ledger_order_uses_date_then_posting_sequence_then_document_line(self):
        self.stock_document("SECOND", "2026-10-02", [{"quantity": "4"}], sequence=20, posted_at=1)
        self.stock_document("FIRST", "2026-10-02", [{"quantity": "1", "movement_id": "ffffffff-ffff-ffff-ffff-ffffffffffff"}, {"quantity": "2", "movement_id": "00000000-0000-0000-0000-000000000001"}], sequence=10, posted_at=900)
        self.stock_document("EARLIER DATE", "2026-10-01", [{"quantity": "8"}], sequence=30)
        self.stock_document("NO SEQUENCE", "2026-10-02", [{"quantity": "16"}], posted_at=999)
        result = self.reports.stock_ledger(self.admin, self.company, self.product)
        self.assertEqual([row["document_number"] for row in result["rows"]], ["EARLIER DATE", "NO SEQUENCE", "FIRST", "FIRST", "SECOND"])
        self.assertEqual([row["line_number"] for row in result["rows"]], [1, 1, 1, 2, 1])
        self.assertEqual([row["balance"] for row in result["rows"]], list(map(Decimal, ("8", "24", "25", "27", "31"))))
        self.assertEqual(result["opening"], Decimal(0))
        self.assertEqual(self.reports.stock_ledger(self.admin, self.company, self.product)["rows"], result["rows"])

    def test_stock_ledger_old_sequence_fallback_is_stable_by_posted_time_and_id(self):
        self.stock_document("LATER ID", "2026-10-01", [{"quantity": "1"}], identifier="ffffffff-ffff-ffff-ffff-ffffffffffff", posted_at=20)
        self.stock_document("EARLIER ID", "2026-10-01", [{"quantity": "1"}], identifier="00000000-0000-0000-0000-000000000001", posted_at=20)
        self.stock_document("EARLIER TIME", "2026-10-01", [{"quantity": "1"}], posted_at=10)
        result = self.reports.stock_ledger(self.admin, self.company, self.product)
        self.assertEqual([row["document_number"] for row in result["rows"]], ["EARLIER TIME", "EARLIER ID", "LATER ID"])

    def test_stock_ledger_pool_store_product_company_and_date_filters(self):
        second = self.stock_store("SECOND")
        other_store = self.stock_store("OTHER", company=self.other)
        another_item = self.stock_item("ANOTHER")
        self.stock_document("MIX", "2026-10-01", [{"quantity": "10"}, {"quantity": "4", "store_id": second}, {"quantity": "5", "pool": "breakage"}, {"quantity": "1000", "product_id": another_item}])
        self.stock_document("OTHER COMPANY", "2026-10-01", [{"quantity": "100", "store_id": other_store}], company=self.other)
        all_stores = self.reports.stock_ledger(self.admin, self.company, self.product)
        self.assertIsNone(all_stores["store"])
        self.assertEqual((all_stores["total"], all_stores["closing"]), (2, Decimal("14")))
        selected = self.reports.stock_ledger(self.admin, self.company, self.product, store_id=second)
        self.assertEqual(selected["store"]["id"], second)
        self.assertEqual((selected["total"], selected["closing"]), (1, Decimal("4")))
        self.assertEqual(self.reports.stock_ledger(self.admin, self.company, self.product, pool="breakage")["closing"], Decimal("5"))
        future_period = self.reports.stock_ledger(self.admin, self.company, self.product, date_from="2026-10-02")
        self.assertEqual((future_period["opening"], future_period["in"], future_period["out"], future_period["closing"], future_period["rows"]), (Decimal("14"), Decimal(0), Decimal(0), Decimal("14"), []))
        before_history = self.reports.stock_ledger(self.admin, self.company, self.product, date_to="2026-09-30")
        self.assertEqual((before_history["opening"], before_history["closing"], before_history["total"]), (Decimal(0), Decimal(0), 0))

    def test_stock_ledger_snapshot_labels_survive_renames_and_missing_snapshots_use_current(self):
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE products SET name_urdu='پرانا سامان' WHERE id=?", (self.product,))
            connection.execute("UPDATE stores SET name_urdu='پرانا گودام' WHERE id=?", (self.store,))
        self.stock_document("ISSUED", "2026-10-01", [{"quantity": "2"}], snapshot=True)
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE products SET code='RENAMED',name='Renamed item',name_urdu='نیا سامان' WHERE id=?", (self.product,))
            connection.execute("UPDATE stores SET code='NEWSTORE',name='Renamed store',name_urdu='نیا گودام' WHERE id=?", (self.store,))
        self.stock_document("WITHOUT SNAPSHOT", "2026-10-02", [{"quantity": "1"}])
        result = self.reports.stock_ledger(self.admin, self.company, self.product)
        saved, fallback = result["rows"]
        self.assertEqual((saved["document_number"], saved["product_code"], saved["product_name"], saved["store_name"]), ("ISSUED", "ITEM", "Item", "Store"))
        self.assertEqual((saved["product_name_urdu"], saved["store_name_urdu"]), ("پرانا سامان", "پرانا گودام"))
        self.assertEqual((saved["product_label_source"], saved["store_label_source"]), ("snapshot", "snapshot"))
        self.assertEqual((fallback["product_name"], fallback["store_name"], fallback["product_label_source"], fallback["store_label_source"]), ("Renamed item", "Renamed store", "current", "current"))
        self.assertEqual(result["product"]["name"], "Renamed item")
        self.assertEqual(self.reports.stock_products(self.admin, self.company)[0]["name"], "Renamed item")
        self.assertEqual(self.reports.stock_stores(self.admin, self.company)[0]["name"], "Renamed store")

    def test_stock_ledger_includes_real_service_reversal_with_original_labels_and_refs(self):
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET fiscal_start='2026-01-01',fiscal_end='2026-12-31' WHERE id=?", (self.company,))
        original = self.stock_document("ORIGINAL", "2026-10-01", [{"quantity": "2"}], sequence=1, snapshot=True)
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE products SET name='Later item' WHERE id=?", (self.product,))
            connection.execute("UPDATE stores SET name='Later store' WHERE id=?", (self.store,))
        service = TransactionService(self.path, security=self.security)
        reversed_document = service.reverse(self.admin, self.company, original, 1, document_number="REVERSAL", business_date="2026-10-02", reason="Synthetic correction")
        result = self.reports.stock_ledger(self.admin, self.company, self.product)
        self.assertEqual((result["in"], result["out"], result["closing"]), (Decimal("2"), Decimal("2"), Decimal("0")))
        first, reversal = result["rows"]
        self.assertEqual((reversal["document_id"], reversal["kind"], reversal["direction"]), (reversed_document["id"], "reversal", "out"))
        self.assertEqual((reversal["reverses_document_id"], reversal["reverses_document_number"], reversal["reverses_movement_id"]), (original, "ORIGINAL", first["movement_id"]))
        self.assertEqual((reversal["product_name"], reversal["store_name"]), ("Item", "Store"))
        self.assertEqual((reversal["product_label_source"], reversal["store_label_source"]), ("snapshot", "snapshot"))

    def test_stock_ledger_rejects_unclassified_opening_and_period_movements(self):
        self.stock_document("UNKNOWN OPEN", "2026-09-30", [{"quantity": "2", "pool": None}])
        for pool in ("regular", "breakage"):
            with self.subTest(pool=pool), self.assertRaisesRegex(ValueError, "no verified stock pool"):
                self.reports.stock_ledger(self.admin, self.company, self.product, pool=pool, date_from="2026-10-01")

    def test_stock_ledger_unclassified_rejection_is_limited_to_relevant_scope(self):
        other_item = self.stock_item("UNRELATED")
        second = self.stock_store("SECOND")
        other_store = self.stock_store("OTHER", company=self.other)
        self.stock_document("KNOWN", "2026-10-01", [{"quantity": "2"}])
        self.stock_document("UNRELATED", "2026-09-30", [{"quantity": "99", "pool": None, "product_id": other_item}])
        self.stock_document("OTHER STORE", "2026-10-01", [{"quantity": "99", "pool": None, "store_id": second}])
        self.stock_document("OTHER COMPANY", "2026-10-01", [{"quantity": "99", "pool": None, "store_id": other_store}], company=self.other)
        self.stock_document("FUTURE", "2026-10-03", [{"quantity": "99", "pool": None}])
        self.stock_document("DRAFT UNKNOWN", "2026-10-01", [{"quantity": "99", "pool": None}], posted=False)
        scoped = self.reports.stock_ledger(self.admin, self.company, self.product, store_id=self.store, date_to="2026-10-02")
        self.assertEqual(scoped["closing"], Decimal("2"))
        with self.assertRaisesRegex(ValueError, "no verified stock pool"):
            self.reports.stock_ledger(self.admin, self.company, self.product, date_to="2026-10-02")
        with self.assertRaisesRegex(ValueError, "no verified stock pool"):
            self.reports.stock_ledger(self.admin, self.company, self.product, store_id=self.store, date_to="2026-10-03")

    def test_stock_selectors_and_ledger_keep_inactive_company_history_accessible(self):
        inactive_unused = self.stock_item("UNUSED", active=False)
        other_only = self.stock_item("OTHER HISTORY")
        other_store = self.stock_store("OTHER", company=self.other)
        self.stock_document("HISTORY", "2026-10-01", [{"quantity": "2"}])
        self.stock_document("OTHER", "2026-10-01", [{"quantity": "3", "store_id": other_store, "product_id": other_only}], company=self.other)
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE products SET is_active=0 WHERE id IN (?,?)", (self.product, other_only))
            connection.execute("UPDATE stores SET is_active=0 WHERE id=?", (self.store,))
            connection.execute("UPDATE company_stores SET is_active=0 WHERE company_id=? AND store_id=?", (self.company, self.store))
        products = self.reports.stock_products(self.admin, self.company)
        stores = self.reports.stock_stores(self.admin, self.company)
        self.assertIn(self.product, [row["id"] for row in products])
        self.assertNotIn(inactive_unused, [row["id"] for row in products])
        self.assertNotIn(other_only, [row["id"] for row in products])
        self.assertIn(self.store, [row["id"] for row in stores])
        self.assertNotIn(other_store, [row["id"] for row in stores])
        result = self.reports.stock_ledger(self.admin, self.company, self.product, store_id=self.store)
        self.assertEqual((result["product"]["is_active"], result["store"]["is_active"], result["closing"]), (0, 0, Decimal("2")))
        with self.assertRaises(ValueError):
            self.reports.stock_ledger(self.admin, self.company, other_only)
        with self.assertRaises(ValueError):
            self.reports.stock_ledger(self.admin, self.company, self.product, store_id=other_store)

    def test_stock_read_permission_suffices_for_selectors_and_revocation_is_live(self):
        user = self.security.create_user(self.admin, "stockreader", "Copper meadows surround hills 94")
        actor = self.security.authenticate("stockreader", "Copper meadows surround hills 94")
        self.security.set_company_access(self.admin, user.id, [self.company])
        self.security.set_permissions(self.admin, user.id, "stock", ["read"])
        operations = [lambda company: self.reports.stock_products(actor, company), lambda company: self.reports.stock_stores(actor, company), lambda company: self.reports.stock_ledger(actor, company, self.product)]
        for operation in operations:
            operation(self.company)
            with self.assertRaises(AuthorizationError):
                operation(self.other)
        self.security.set_permissions(self.admin, user.id, "stock", [])
        for operation in operations:
            with self.assertRaises(AuthorizationError):
                operation(self.company)
        self.security.set_permissions(self.admin, user.id, "stock", ["read"])
        self.security.set_company_access(self.admin, user.id, [])
        for operation in operations:
            with self.assertRaises(AuthorizationError):
                operation(self.company)

    def test_stock_ledger_validates_missing_ids_pool_dates_and_page_bounds(self):
        with self.assertRaises(ValueError):
            self.reports.stock_ledger(self.admin, self.company, str(uuid4()))
        with self.assertRaises(ValueError):
            self.reports.stock_ledger(self.admin, self.company, self.product, store_id=str(uuid4()))
        for values in ({"pool": "all"}, {"pool": []}, {"date_to": "2026-02-30"}, {"date_from": "2026-10-02", "date_to": "2026-10-01"}, {"limit": True}, {"limit": 0}, {"limit": 501}, {"offset": -1}, {"offset": True}, {"store_id": []}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.reports.stock_ledger(self.admin, self.company, self.product, **values)


if __name__ == "__main__":
    unittest.main()
