"""Cross-component posting checks; fixtures stand in for future master services.

This proves schema/security/calculation interoperability, not completion of the
transaction service or its stock-availability, settlement and lifecycle rules.
"""

from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4

from bifma.calculations import DiscountKind, InvoiceLine, RoundingPolicy, canonical_decimal
from bifma.native_schema import initialize_database, native_session, native_transaction
from bifma.posting_rules import TradePostingItem, build_trade_posting
from bifma.security import AuthorizationError, SecurityService


class NativeIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "native.sqlite3"
        initialize_database(self.path)
        self.security = SecurityService(self.path, clock=lambda: 1790941200)
        self.admin = self.security.bootstrap_admin("owner", "Copper orchard 42 window violet")
        self.company, self.customer = str(uuid4()), str(uuid4())
        self.account_ids = [str(uuid4()), str(uuid4())]
        self.products = [str(uuid4()), str(uuid4())]
        self.store, unit = str(uuid4()), str(uuid4())
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO companies(id,code,name) VALUES (?,?,?)", (self.company, "EXAMPLE", "Example company"))
            connection.execute("INSERT INTO accounts(id,code,name,kind,classification) VALUES (?,?,?,'customer','asset')", (self.customer, "CUSTOMER", "Example customer"))
            for number, identifier in enumerate(self.account_ids):
                connection.execute("INSERT INTO accounts(id,code,name,classification) VALUES (?,?,?,'income')", (identifier, f"REVENUE{number}", f"Revenue {number}"))
            connection.execute("INSERT INTO units(id,code,name) VALUES (?, 'PCS', 'Pieces')", (unit,))
            connection.execute("INSERT INTO stores(id,code,name) VALUES (?, 'SHOP', 'Shop')", (self.store,))
            connection.execute("INSERT INTO company_stores(company_id,store_id) VALUES (?,?)", (self.company, self.store))
            for number, identifier in enumerate(self.products):
                connection.execute("INSERT INTO products(id,code,name,small_unit_id,sale_account_id) VALUES (?,?,?,?,?)", (identifier, f"ITEM{number}", f"Item {number}", unit, self.account_ids[number]))
        user = self.security.create_user(self.admin, "operator", "Amber forest 87 lantern silver")
        self.security.set_company_access(self.admin, user.id, [self.company])
        self.security.set_permissions(self.admin, user.id, "sales", ["create", "post"])
        self.operator = self.security.authenticate("operator", "Amber forest 87 lantern silver")

    def post_fixture(self, *, omit_last_credit=False):
        lines = [InvoiceLine("2", "100", "10", discount_kind=DiscountKind.PERCENT),
                 InvoiceLine("1", "50", discount_kind=DiscountKind.PERCENT)]
        posting = build_trade_posting("sale", [TradePostingItem(line, account) for line, account in zip(lines, self.account_ids)],
                                      party_account=self.customer, subtotal_rounding=RoundingPolicy.LEGACY_WHOLE,
                                      posting_places=2, discount="30", freight="10")
        document, journal = str(uuid4()), str(uuid4())
        with native_transaction(self.path) as connection:
            self.security.require(self.operator, self.company, "sales", "create", connection=connection)
            actor = self.security.require(self.operator, self.company, "sales", "post", connection=connection)
            connection.execute("INSERT INTO documents(id,company_id,kind,document_number,business_date,account_id,total_amount,rounding_policy,posting_places,created_by) VALUES (?,?,'sale','SALE-1','2026-10-02',?,?,'legacy_whole',2,?)",
                               (document, self.company, self.customer, canonical_decimal(posting.total), actor.id))
            for index, line in enumerate(lines):
                identifier = str(uuid4())
                connection.execute("INSERT INTO document_lines(id,company_id,document_id,line_number,product_id,store_id,quantity,rate,discount_percent,net_amount) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                   (identifier, self.company, document, index + 1, self.products[index], self.store,
                                    canonical_decimal(line.quantity), canonical_decimal(line.rate), canonical_decimal(line.discount), canonical_decimal(line.calculate().net)))
                connection.execute("INSERT INTO stock_movements(id,company_id,document_id,document_line_id,product_id,store_id,direction,quantity) VALUES (?,?,?,?,?,?,'out',?)",
                                   (str(uuid4()), self.company, document, identifier, self.products[index], self.store, canonical_decimal(line.quantity)))
            connection.execute("INSERT INTO journal_entries(id,company_id,document_id) VALUES (?,?,?)", (journal, self.company, document))
            entries = posting.entries[:-1] if omit_last_credit else posting.entries
            for index, entry in enumerate(entries):
                connection.execute("INSERT INTO journal_lines(id,company_id,entry_id,line_number,account_id,debit,credit) VALUES (?,?,?,?,?,?,?)",
                                   (str(uuid4()), self.company, journal, index + 1, entry.account_code,
                                    canonical_decimal(entry.debit), canonical_decimal(entry.credit)))
            connection.execute("UPDATE documents SET status='posted',revision=revision+1,posted_by=?,posted_at=? WHERE id=? AND revision=0", (actor.id, 1790941200, document))
            connection.execute("INSERT INTO audit_events(id,actor_id,company_id,action,entity_type,entity_id) VALUES (?,?,?,'document.posted','document',?)", (str(uuid4()), actor.id, self.company, document))
        return document

    def test_corrected_posting_balances_and_locks_every_component(self):
        document = self.post_fixture()
        with native_session(self.path) as connection:
            debits, credits = connection.execute("SELECT native_decimal_sum(debit),native_decimal_sum(credit) FROM posted_journal_lines").fetchone()
            self.assertEqual((debits, credits), ("210", "210"))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM posted_stock_movements WHERE direction='out'").fetchone()[0], 2)
        with self.assertRaises(sqlite3.IntegrityError):
            with native_transaction(self.path) as connection:
                connection.execute("UPDATE document_lines SET quantity='999' WHERE document_id=?", (document,))

    def test_unbalanced_write_rolls_back_header_lines_and_movements(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.post_fixture(omit_last_credit=True)
        with native_session(self.path) as connection:
            for table in ("documents", "document_lines", "journal_entries", "journal_lines", "stock_movements"):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_fresh_permission_check_prevents_posting_with_revoked_rights(self):
        self.security.set_permissions(self.admin, self.operator.user.id, "sales", ["create"])
        with self.assertRaises(AuthorizationError):
            self.post_fixture()
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)

    def test_duplicate_number_cannot_double_post_stock_or_money(self):
        self.post_fixture()
        with self.assertRaises(sqlite3.IntegrityError):
            self.post_fixture()
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM posted_stock_movements").fetchone()[0], 2)
            self.assertEqual(Decimal(connection.execute("SELECT native_decimal_sum(debit) FROM posted_journal_lines").fetchone()[0]), Decimal("210"))


if __name__ == "__main__":
    unittest.main()
