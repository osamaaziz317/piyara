"""One register combines source and current documents without posting history."""

from decimal import Decimal
import hashlib
from pathlib import Path
import tempfile
import unittest

from bifma.baseline import BaselineError, ensure_baseline
from bifma.database import session
from bifma.document_register import DocumentRegister
from bifma.native_schema import initialize_database, native_session, native_transaction
from bifma.security import AuthorizationError, SecurityService
from bifma.transactions import MODULES, TransactionError, TransactionService
from tests.test_baseline_import import synthetic_archive


def all_original_modules(path):
    synthetic_archive(path)
    with session(path, readonly=False) as connection:
        def add(table, **row):
            connection.execute(f"INSERT INTO {table}({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
        add("CHART", CHCODE="04010001", CHDESC="Original counter cash", ALLOWED="1")
        add("CONFIG", CMP_ID="2", CMPNAME="Other original company", CHCODE_CASH="0001",
            SDATE="2025-01-01T00:00:00", EDATE="2027-12-31T00:00:00")
        for table, detail, kind, payment_column, payment, identifier, quantity in (
                ("SMST", "SDTL", "8", "S_TYPE", "C", "2", "1"),
                ("SMST", "SDTL", "9", "S_TYPE", "R", "3", "1"),
                ("SMST", "SDTL", "9", "S_TYPE", "C", "4", "1"),
                ("SMST", "SDTL", "8", "S_TYPE", None, "5", "0"),
                ("PMST", "PDTL", "7", "P_TYPE", "R", "1", "2"),
                ("PMST", "PDTL", "7", "P_TYPE", "C", "2", "2"),
                ("PMST", "PDTL", "10", "P_TYPE", "R", "3", "1"),
                ("PMST", "PDTL", "10", "P_TYPE", "C", "4", "1")):
            add(table, TRNID=identifier, TRNNO="260001" if kind == "8" and payment == "C" else identifier,
                TRNDATE="2026-01-02T12:30:00", TRN_TYPE=kind, CMP_ID="1", CHCODE="0002",
                REMARKS="literal %_' note", **{payment_column: payment})
            add(detail, TRNID=identifier, VNO=identifier, LINE_NUM="1", STORE_ID="1", ICODE="A",
                QTY=quantity, RATE="10", UOM_ID_SMALL="1")
        for table, detail, kind in (("BRMST", "BRDTL", "11"), ("BDMST", "BDDTL", "12")):
            add(table, TRNID="1", TRNNO="1", TRNDATE="2026-01-02T00:00:00", TRN_TYPE=kind,
                CMP_ID="1", CHCODE="0002", **({"STORE_ID": "1"} if table == "BRMST" else {}))
            add(detail, TRNID="1", VNO="1", LINE_NUM="1", ICODE="A", QTY="1", RATE="3",
                **({"STORE_ID": "1"} if table == "BDMST" else {}))
        for identifier, kind, cancelled in (("2", "2", None), ("3", "1", None), ("4", "4", None),
                                            ("5", "5", None), ("6", "6", None), ("7", "3", "0"), ("8", "1", None)):
            add("JMST", TRNID=identifier, TRNNO=identifier, TRNDATE="2026-01-03T23:59:59", VTYPE=kind, CMP_ID="1", CANCEL=cancelled)
            add("JDTL", TRNID=identifier, DTL_ID=identifier + "1", SR="1", CHCODE="0002",
                **({"CR": "9.25"} if kind == "3" else {"DR": "9.25"}), NARRATION="matched %_' narration")
            if kind not in {"2", "3"}:
                add("JDTL", TRNID=identifier, DTL_ID=identifier + "2", SR="2", CHCODE="0001", CR="9.25")


class DocumentRegisterTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.source, self.path = self.directory / "original.sqlite3", self.directory / "live.sqlite3"
        all_original_modules(self.source)
        ensure_baseline(self.source, self.path)
        self.before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.security = SecurityService(self.path)
        self.actor = self.security.bootstrap_admin("owner", "Green rivers sparkle beneath moonlight 84")
        self.service = TransactionService(self.path, self.security)
        self.register = DocumentRegister(self.path, self.source, security=self.security)
        with native_session(self.path) as connection:
            self.company = connection.execute("SELECT id FROM companies WHERE code='1'").fetchone()[0]
            self.other = connection.execute("SELECT id FROM companies WHERE code='2'").fetchone()[0]
            self.product = connection.execute("SELECT id FROM products WHERE code='A'").fetchone()[0]
            self.store = connection.execute("SELECT id FROM stores WHERE code='1'").fetchone()[0]
            self.party = connection.execute("SELECT id FROM accounts WHERE code='0002'").fetchone()[0]
            self.cash = connection.execute("SELECT id FROM accounts WHERE code='0001'").fetchone()[0]

    def sale(self, *, number="260002", posted=True, when="2026-01-04"):
        draft = self.service.save_draft(self.actor, self.company, "sale", {
            "document_number": number, "business_date": when, "rounding_policy": "legacy_whole", "posting_places": 0,
            "account_id": self.party, "notes": "Current literal %_' note",
            "lines": [{"product_id": self.product, "store_id": self.store, "quantity": "1", "rate": "10", "discount_percent": "0"}]})
        return self.service.post(self.actor, self.company, draft["id"], draft["revision"]) if posted else draft

    def test_every_original_module_and_opening_remains_accessible_without_duplicate_cash_rows(self):
        expected = {kind: 1 for kind in MODULES}
        expected.update(sale=2, cash_receipt=2, journal=2)
        identities = set()
        for kind, count in expected.items():
            with self.subTest(kind=kind):
                rows = self.register.list_documents(self.actor, self.company, kind)
                self.assertEqual(len(rows), count)
                for row in rows:
                    identity = row["source_table"], row["id"]
                    self.assertNotIn(identity, identities)
                    identities.add(identity)
                    detail = self.register.get(self.actor, self.company, kind, row["id"], source="original")
                    self.assertEqual(detail["source"], "original")
                    self.assertTrue({"title", "fields", "columns", "rows", "notes"} <= detail.keys())
        self.assertEqual(len(identities), 20)
        self.assertEqual(self.register.document_counts(self.actor, self.company), {"original": 20, "current": 0})
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
            # Unlinked old returns make these source invoices ineligible for
            # automatic return, but do not hide them from the document register.
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM baseline_trade_documents WHERE return_eligible=1").fetchone()[0], 0)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.before)

    def test_current_and_original_rows_merge_before_stable_pagination_with_reversals(self):
        posted = self.sale()
        self.sale(number="260003", posted=False)
        reversed_document = self.service.reverse(self.actor, self.company, posted["id"], posted["revision"],
            document_number="REV-260002", business_date="2026-01-05", reason="Synthetic correction")
        rows = self.register.list_documents(self.actor, self.company, "sale")
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["id"], reversed_document["id"])
        self.assertEqual([row["source"] for row in rows], ["current"] * 3 + ["original"] * 2)
        for offset, row in enumerate(rows):
            self.assertEqual(self.register.list_documents(self.actor, self.company, "sale", offset=offset, limit=1), [row])
        self.assertEqual(self.register.list_documents(self.actor, self.company, "sale", offset=100), [])
        self.assertEqual(self.register.get(self.actor, self.company, "sale", posted["id"], source="current")["source"], "current")
        self.assertEqual(self.register.get(self.actor, self.company, "sale", reversed_document["id"], source="current")["kind"], "reversal")
        self.assertEqual(len(self.register.list_documents(self.actor, self.company, "reversal")), 1)
        self.assertEqual(next(row for row in rows if row["id"] == posted["id"])["status_display"], "Reversed")

    def test_voucher_counterparts_and_saved_labels_survive_renames_in_combined_lists(self):
        values = {"document_number": "CR-1", "business_date": "2026-01-04", "rounding_policy": "cents", "posting_places": 2,
                  "account_id": self.cash, "lines": [{"account_id": self.party, "amount": "9007199254740993.01", "narration": "Exact current receipt"}]}
        draft = self.service.save_draft(self.actor, self.company, "cash_receipt", values)
        saved = self.service.post(self.actor, self.company, draft["id"], draft["revision"])
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET name='Renamed counterpart' WHERE id=?", (self.party,))
        row = next(row for row in self.register.list_documents(self.actor, self.company, "cash_receipt") if row["source"] == "current")
        self.assertEqual((row["account"], row["total_amount"]), ("Party", "9007199254740993.01"))
        self.assertEqual(row["counterpart_account_ids"], [self.party])
        self.assertEqual(self.register.list_documents(self.actor, self.company, "cash_receipt", search="Party")[0]["id"], saved["id"])
        self.assertEqual(self.register.document_counts(self.actor, self.company)["current"], 1)

    def test_dates_and_literal_search_apply_to_both_sources_before_pagination(self):
        posted = self.sale()
        rows = self.register.list_documents(self.actor, self.company, "sale", search="%_'")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], posted["id"])
        self.assertEqual(self.register.list_documents(self.actor, self.company, "sale", search="%_'", offset=1, limit=1), [rows[1]])
        self.assertEqual(len(self.register.list_documents(self.actor, self.company, "sale", date_to="2026-01-02")), 2)
        self.assertEqual(len(self.register.list_documents(self.actor, self.company, "sale", date_from="2026-01-04")), 1)
        self.assertEqual(self.register.list_documents(self.actor, self.company, "sale", date_from="2026-01-03", date_to="2026-01-03"), [])
        self.assertEqual(len(self.register.list_documents(self.actor, self.company, "cash_receipt", date_to="2026-01-03")), 2)
        self.assertEqual(self.register.list_documents(self.actor, self.company, "sale", search="_not_a_wildcard"), [])
        self.assertEqual(len(self.register.list_documents(self.actor, self.company, "cash_receipt", search="matched %_'")), 1)

    def test_totals_preserve_original_rules_unknowns_and_cancelled_records(self):
        sale = self.register.get(self.actor, self.company, "sale", "1", source="original")
        self.assertEqual(sale["total_amount"], "30")
        self.assertIn(("Recorded party amount", "30"), sale["fields"])
        self.assertFalse(any("await reconciliation" in note for note in sale["notes"]))
        unknown = self.register.get(self.actor, self.company, "sale", "5", source="original")
        self.assertIsNone(unknown["total_amount"])
        self.assertTrue(any("no total has been invented" in note for note in unknown["notes"]))
        cancelled = self.register.get(self.actor, self.company, "cash_receipt", "7", source="original")
        self.assertEqual((cancelled["status"], cancelled["cancellation_marker"], cancelled["total_amount"]), ("cancelled", "0", "9.25"))
        opening = self.register.get(self.actor, self.company, "stock_opening", "1", source="original")
        self.assertEqual(opening["total_amount"], "21.23456789")
        self.assertIn(("Source record", "1"), opening["fields"])

    def test_source_get_enforces_company_kind_payment_mode_and_fresh_permissions(self):
        user = self.security.create_user(self.actor, "sales_reader", "Quiet golden lanterns frame the hills 53")
        reader = self.security.authenticate("sales_reader", "Quiet golden lanterns frame the hills 53")
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.set_permissions(self.actor, user.id, "sales", ["read"])
        self.assertEqual(len(self.register.list_documents(reader, self.company, "sale")), 2)
        with self.assertRaises(AuthorizationError):
            self.register.list_documents(reader, self.company, "cash_sale")
        with self.assertRaises(AuthorizationError):
            self.register.get(reader, self.other, "sale", "1", source="original")
        with self.assertRaises(TransactionError):
            self.register.get(reader, self.company, "sale", "2", source="original")
        with self.assertRaises(TransactionError):
            self.register.get(self.actor, self.company, "cash_receipt", "2", source="original")
        self.security.set_permissions(self.actor, user.id, "sales", [])
        with self.assertRaises(AuthorizationError):
            self.register.get(reader, self.company, "sale", "1", source="original")

    def test_lineage_changes_missing_archive_and_unrelated_database_are_refused(self):
        copy = self.directory / "unrelated.sqlite3"
        initialize_database(copy)
        with self.assertRaisesRegex(BaselineError, "association"):
            DocumentRegister(copy, self.source)
        with self.assertRaisesRegex(BaselineError, "unavailable"):
            DocumentRegister(self.path, self.directory / "missing.sqlite3")
        with session(self.source, readonly=False) as connection:
            connection.execute("UPDATE CHART SET CHDESC='Changed synthetic original'")
        with self.assertRaisesRegex(BaselineError, "does not match"):
            self.register.list_documents(self.actor, self.company, "sale")
        with self.assertRaisesRegex(BaselineError, "does not match"):
            DocumentRegister(self.path, self.source)

    def test_bad_filters_and_current_kind_spoofing_fail_without_partial_reads(self):
        posted = self.sale()
        for filters in ({"offset": -1}, {"limit": True}, {"limit": 501}, {"search": "\x00"},
                        {"date_from": "2026-02-30"}, {"date_from": "2026-01-05", "date_to": "2026-01-04"}):
            with self.subTest(filters=filters), self.assertRaises(TransactionError):
                self.register.list_documents(self.actor, self.company, "sale", **filters)
        with self.assertRaises(TransactionError):
            self.register.get(self.actor, self.company, "purchase", posted["id"], source="current")
        with self.assertRaises(TransactionError):
            self.register.get(self.actor, self.company, "sale", "1", source="current")
        with self.assertRaises(TransactionError):
            self.register.get(self.actor, self.company, "sale", posted["id"], source="legacy")


if __name__ == "__main__":
    unittest.main()
