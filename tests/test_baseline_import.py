"""Reconcile immutable starting data using generated archives, never live data."""

from contextlib import closing
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma.baseline import BaselineError, baseline_info, baseline_stock, ensure_baseline, require_after_cutoff
from bifma.database import session
from bifma.legacy_ledger import LegacyLedger
from bifma.legacy_stock import LegacyStock
from bifma.native_schema import initialize_database, native_session, native_transaction


def synthetic_archive(path):
    """One company, 7 available units, cash 10 / party 20 / sales -30 at Jan 3."""
    schema = json.loads((Path(__file__).resolve().parents[1] / "docs/legacy_schema.json").read_text())
    with closing(sqlite3.connect(path)) as connection, connection:
        for table in schema["tables"]:
            columns = ",".join('"' + column["name"] + '" TEXT' for column in table["columns"])
            connection.execute('CREATE TABLE "' + table["name"] + '" (' + columns + ')')
        connection.execute("CREATE TABLE _recovery_metadata(key TEXT PRIMARY KEY,value TEXT)")
        connection.execute("INSERT INTO _recovery_metadata VALUES('report',?)", (json.dumps({"status": "recovered_unverified"}),))
        def add(table, **row):
            connection.execute(f"INSERT INTO {table}({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
        add("CITY", PK_CCODE="1", CNAME="Synthetic city", CNAME_URDU="شہر")
        for code, name, allowed in (("0001", "Cash", "1"), ("0002", "Party", "1"), ("0003", "Sales", "1"),
                                    ("0004", "Purchases", "1"), ("0005", "Review posting permission", None)):
            add("CHART", CHCODE=code, CHDESC=name, FK_CCODE="1", ALLOWED=allowed)
        add("CONFIG", CMP_ID="1", CMPNAME="Synthetic company", CHCODE_CASH="0001",
            SDATE="2025-01-01T00:00:00", EDATE="2027-12-31T00:00:00")
        add("UOM", UOM_ID="1", UOM_DESC="Piece", UOM_DESC_URDU="عدد")
        add("UOM", UOM_ID="2", UOM_DESC="Pack", UOM_DESC_URDU="پیک")
        add("PUR_ITEM_GROUP_MST", MAIN_CODE="1", MAIN_DESC="Main")
        add("PUR_ITEM_GROUP_DTL", MAIN_CODE="1", SUB_CODE="1", SUB_DESC="Sub")
        add("PUR_ITEM", ICODE="A", INAME="Synthetic item", INAME_URDU="آئٹم", UOM_ID_SMALL="1", UOM_ID_LARGE="2",
            QTY_SMALL_UOM="12", SUB_CODE="1", PUR_CODE="0004", EXP_CODE="0003")
        add("STORE_LOC", STORE_ID="1", STORE_DESC="Main store", STORE_DESC_URDU="گودام", CMP_ID="1")
        add("OPEN_STOCK", TRNID="1", ICODE="A", OPEN_QTY="10", OPEN_RATE="2.123456789", OPEN_DATE="2026-01-01T00:00:00",
            STORE_ID="1", CMP_ID="1", UOM_ID_SMALL="1")
        add("SMST", TRNID="1", TRNNO="260001", TRNDATE="2026-01-02T00:00:00", TRN_TYPE="8", CMP_ID="1", CHCODE="0002", S_TYPE="R")
        add("SDTL", TRNID="1", VNO="1", LINE_NUM="1", STORE_ID="1", ICODE="A", QTY="3", RATE="10", UOM_ID_SMALL="1")
        add("JMST", TRNID="1", TRNNO="26", TRNDATE="2026-01-03T00:00:00", VTYPE="3", CMP_ID="1")
        add("JDTL", TRNID="1", DTL_ID="1", SR="1", CHCODE="0002", CR="10", NARRATION="Synthetic receipt")
        add("ENTRY_TYPE", TYPE_CODE="8", DESCRIPTION="Sale Voucher")
        add("ENTRY_TYPE", TYPE_CODE="3", DESCRIPTION="Cash Receipt Voucher")


class BaselineImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / "archive.sqlite3"
        self.target = Path(self.directory.name) / "live.sqlite3"
        synthetic_archive(self.source)

    def digest(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def mapping(self, connection, table, key):
        return connection.execute("SELECT native_id FROM baseline_mappings WHERE source_table=? AND source_key=?", (table, key)).fetchone()[0]

    def test_exact_closings_masters_unknown_rates_and_idempotence(self):
        before = self.digest(self.source)
        result = ensure_baseline(self.source, self.target)
        self.assertTrue(result.created)
        self.assertEqual(result.cutoff_date, "2026-01-03")
        self.assertEqual(result.manifest["anomalies"]["unknown_purchase_rates"], 1)
        with native_session(self.target) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM documents").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM journal_lines").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM users").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM audit_events WHERE action='baseline.imported'").fetchone()[0], 1)
            actual = {r["code"]: Decimal(r["balance"]) for r in connection.execute("SELECT a.code,b.balance FROM baseline_account_balances b JOIN accounts a ON a.id=b.account_id")}
            expected = {r.account_code: r.closing for r in LegacyLedger(self.source).balances("1")}
            self.assertEqual(actual, expected)
            self.assertEqual(sum(actual.values()), 0)
            stock = connection.execute("SELECT * FROM baseline_stock_balances").fetchone()
            expected_stock = LegacyStock(self.source).balances("1")[0]
            self.assertEqual(Decimal(stock["quantity"]), expected_stock.closing_quantity)
            self.assertEqual(Decimal(stock["historical_value"]), expected_stock.closing_value)
            self.assertEqual(stock["historical_value"], "14.864197523")
            product = connection.execute("SELECT * FROM products").fetchone()
            self.assertEqual((product["sale_rate_known"], product["purchase_rate_known"]), (0, 0))
            self.assertIsNone(product["inventory_account_id"])
            self.assertEqual(connection.execute("SELECT is_postable FROM accounts WHERE code='0005'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM accounts WHERE parent_id IS NOT NULL OR classification IS NOT NULL").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT group_concat(code,',') FROM product_groups").fetchone()[0], "M:1,S:1")
        live_before = self.digest(self.target)
        again = ensure_baseline(self.source, self.target)
        self.assertFalse(again.created)
        self.assertEqual(again.manifest, result.manifest)
        self.assertEqual(self.digest(self.target), live_before)
        self.assertEqual(self.digest(self.source), before)

    def test_negative_stock_unit_conflicts_and_original_dates_remain_visible(self):
        with session(self.source, readonly=False) as connection:
            connection.execute("UPDATE SDTL SET QTY='13',UOM_ID_SMALL='2'")
        result = ensure_baseline(self.source, self.target)
        self.assertEqual(result.manifest["anomalies"]["negative_stock_groups"], 1)
        self.assertEqual(result.manifest["anomalies"]["unit_mismatch_rows"], 1)
        with native_session(self.target) as connection:
            row = connection.execute("SELECT * FROM baseline_stock_balances").fetchone()
            self.assertEqual(baseline_stock(connection, row["company_id"], row["store_id"], row["product_id"], row["pool"]), Decimal(-3))
            self.assertEqual(row["base_unit_id"], self.mapping(connection, "UOM", "1"))
            self.assertEqual(result.manifest["product_issues"][row["product_id"]]["source_units"], ["2"])
            with self.assertRaises(BaselineError):
                require_after_cutoff(connection, "2026-01-03")
            require_after_cutoff(connection, "2026-01-04")

    def test_sealed_rows_forbid_mutation_and_appending(self):
        ensure_baseline(self.source, self.target)
        for table in ("baseline_imports", "baseline_mappings", "baseline_account_balances", "baseline_stock_balances", "baseline_document_numbers", "baseline_purchase_rates", "baseline_trade_documents", "baseline_trade_lines"):
            with native_transaction(self.target) as connection:
                row = connection.execute(f"SELECT * FROM {table} LIMIT 1").fetchone()
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(f"DELETE FROM {table}")
                column = row.keys()[0]
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(f"UPDATE {table} SET {column}={column}")
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(f"INSERT INTO {table} SELECT * FROM {table} LIMIT 1")

    def test_mismatched_source_and_unrelated_native_refused_without_changes(self):
        ensure_baseline(self.source, self.target)
        before = self.digest(self.target)
        with session(self.source, readonly=False) as connection:
            connection.execute("UPDATE PUR_ITEM SET INAME='Changed synthetic source'")
        with self.assertRaisesRegex(BaselineError, "does not belong"):
            ensure_baseline(self.source, self.target)
        self.assertEqual(before, self.digest(self.target))
        ordinary = self.target.with_name("unrelated.sqlite3")
        initialize_database(ordinary)
        before = self.digest(ordinary)
        with self.assertRaises(BaselineError):
            ensure_baseline(self.source, ordinary)
        self.assertEqual(before, self.digest(ordinary))
        self.assertIsNone(baseline_info(ordinary))

    def test_failed_preparation_leaves_no_official_file_and_retries(self):
        before = self.digest(self.source)
        with patch("bifma.baseline._immutable_digest", side_effect=RuntimeError("Interrupted synthetic preparation")):
            with self.assertRaises(RuntimeError):
                ensure_baseline(self.source, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.glob(".bifma-baseline-*")), [])
        self.assertTrue(ensure_baseline(self.source, self.target).created)
        self.assertEqual(before, self.digest(self.source))

    def test_bad_dates_and_damaged_manifest_are_refused(self):
        with session(self.source, readonly=False) as connection:
            connection.execute("UPDATE SMST SET TRNDATE=NULL")
        with self.assertRaisesRegex(BaselineError, "date is missing"):
            ensure_baseline(self.source, self.target)
        self.assertFalse(self.target.exists())
        with session(self.source, readonly=False) as connection:
            connection.execute("UPDATE SMST SET TRNDATE='2026-01-02T00:00:00'")
        ensure_baseline(self.source, self.target)
        # Deliberately bypass guards to model damage outside supported application writes.
        with native_transaction(self.target) as connection:
            connection.execute("DROP TRIGGER baseline_stock_balances_update_guard")
            connection.execute("UPDATE baseline_stock_balances SET quantity='999'")
        with self.assertRaisesRegex(BaselineError, "no longer matches"):
            baseline_info(self.target)

    def test_cutoff_and_historical_document_number_guards_cover_direct_writes(self):
        ensure_baseline(self.source, self.target)
        with native_transaction(self.target) as connection:
            actor, document = str(uuid4()), str(uuid4())
            company = self.mapping(connection, "CONFIG", "1")
            connection.execute("INSERT INTO users(id,username,display_name,password_hash,is_admin,is_active) VALUES (?,'fixture','Fixture','synthetic',1,1)", (actor,))
            sql = "INSERT INTO documents(id,company_id,kind,document_number,business_date,created_by,rounding_policy,posting_places) VALUES (?,?,'sale',?,?,?,'cents',2)"
            for number, day in (("NEW", "2026-01-03"), ("260001", "2026-01-04")):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(sql, (document, company, number, day, actor))
            connection.execute(sql, (document, company, "NEW", "2026-01-04", actor))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE documents SET business_date='2026-01-03' WHERE id=?", (document,))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE documents SET document_number='260001' WHERE id=?", (document,))

    def test_cost_candidates_keep_original_highest_numeric_id_and_fallback(self):
        with session(self.source, readonly=False) as connection:
            connection.execute("INSERT INTO PMST(TRNID,TRNNO,TRNDATE,TRN_TYPE,CMP_ID,CHCODE,P_TYPE) VALUES ('2','2','2026-01-03T00:00:00','7','1','0002','R')")
            connection.execute("INSERT INTO PDTL(TRNID,VNO,STORE_ID,ICODE,QTY,RATE,UOM_ID_SMALL) VALUES ('2','9','1','A','1','5.123456789','1')")
            connection.execute("UPDATE OPEN_STOCK SET TRNID='100'")
            connection.execute("UPDATE PUR_ITEM SET SALE_RATE='0'")
        ensure_baseline(self.source, self.target)
        with native_session(self.target) as connection:
            rate = connection.execute("SELECT * FROM baseline_purchase_rates").fetchone()
            self.assertEqual((rate["rate"], rate["rate_source"], rate["source_id"]), ("2.123456789", "primary", "100"))
            self.assertEqual(json.loads(rate["source_json"])["table"], "OPEN_STOCK")
            self.assertEqual(connection.execute("SELECT sale_rate,sale_rate_known,purchase_rate_known FROM products").fetchone()[:], ("0", 1, 0))
        other = self.target.with_name("fallback.sqlite3")
        with session(self.source, readonly=False) as connection:
            connection.execute("DELETE FROM OPEN_STOCK")
            connection.execute("UPDATE PMST SET TRN_TYPE='10'")
        ensure_baseline(self.source, other)
        with native_session(other) as connection:
            self.assertEqual(connection.execute("SELECT rate,rate_source,source_id FROM baseline_purchase_rates").fetchone()[:], ("5.123456789", "fallback", "9"))

    def test_observed_extra_store_membership_is_preserved_but_inactive(self):
        with session(self.source, readonly=False) as connection:
            connection.execute("INSERT INTO CONFIG(CMP_ID,CMPNAME,CHCODE_CASH,SDATE,EDATE) VALUES ('2','Other company','0001','2025-01-01T00:00:00','2027-12-31T00:00:00')")
            connection.execute("INSERT INTO STORE_LOC(STORE_ID,STORE_DESC,CMP_ID) VALUES ('2','Other store','2')")
            connection.execute("UPDATE SDTL SET STORE_ID='2'")
        result = ensure_baseline(self.source, self.target)
        self.assertEqual(result.manifest["anomalies"]["inactive_observed_store_memberships"], 1)
        with native_session(self.target) as connection:
            company = self.mapping(connection, "CONFIG", "1")
            store = self.mapping(connection, "STORE_LOC", "2")
            self.assertEqual(connection.execute("SELECT is_active FROM company_stores WHERE company_id=? AND store_id=?", (company, store)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT quantity FROM baseline_stock_balances WHERE company_id=? AND store_id=?", (company, store)).fetchone()[0], "-3")

    def test_publish_race_preserves_unrelated_winner_and_source_change_refuses(self):
        def competing(staged, destination):
            initialize_database(destination)
            raise FileExistsError("Synthetic competing startup")
        with patch("bifma.baseline.publish_new_file", side_effect=competing):
            with self.assertRaisesRegex(BaselineError, "appeared"):
                ensure_baseline(self.source, self.target)
        self.assertIsNone(baseline_info(self.target))
        other = self.target.with_name("changed-source.sqlite3")
        with patch("bifma.baseline._source_digest", side_effect=[self.digest(self.source), "0" * 64]):
            with self.assertRaisesRegex(BaselineError, "changed"):
                ensure_baseline(self.source, other)
        self.assertFalse(other.exists())

    def test_process_exit_before_publication_leaves_no_live_file_and_retry_succeeds(self):
        script = """import os,sys
import bifma.baseline as b
original=b._import
def interrupted(*args):
    original(*args)
    os._exit(73)
b._import=interrupted
b.ensure_baseline(sys.argv[1],sys.argv[2])
"""
        completed = subprocess.run([sys.executable, "-B", "-c", script, str(self.source), str(self.target)],
                                   cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 73, completed.stderr)
        self.assertFalse(self.target.exists())
        self.assertTrue(ensure_baseline(self.source, self.target).created)
        self.assertIsNotNone(baseline_info(self.target))

    def test_original_invoice_bridge_preserves_adjusted_party_total_and_raw_lines(self):
        with session(self.source, readonly=False) as connection:
            connection.execute("UPDATE SMST SET DISCOUNT='5'")
        ensure_baseline(self.source, self.target)
        with native_session(self.target) as connection:
            header = connection.execute("SELECT * FROM baseline_trade_documents").fetchone()
            line = connection.execute("SELECT * FROM baseline_trade_lines").fetchone()
            self.assertEqual((header["total_amount"], header["return_eligible"]), ("25", 1))
            self.assertEqual((line["quantity"], line["rate"], line["net_amount"], line["return_eligible"]), ("3", "10", "30", 1))
            self.assertEqual(json.loads(header["input_json"])["discount"], "5")
            self.assertEqual(json.loads(header["source_json"])["DISCOUNT"], "5")
            self.assertIsNone(json.loads(line["source_json"])["DIS_PER"])
            snapshot = json.loads(line["snapshot_json"])
            self.assertEqual(snapshot["line_id"], line["id"])
            self.assertEqual(snapshot["product"]["small_unit_id"], self.mapping(connection, "UOM", "1"))
            self.assertEqual(snapshot["posting_account"]["id"], self.mapping(connection, "CHART", "0003"))
            self.assertEqual(connection.execute("SELECT count(*) FROM documents").fetchone()[0], 0)

    def test_explicit_historical_return_reduces_original_quantity_and_amount_capacity(self):
        with session(self.source, readonly=False) as connection:
            connection.execute("INSERT INTO PMST(TRNID,TRNNO,TRNDATE,TRN_TYPE,CMP_ID,CHCODE,P_TYPE) VALUES ('2','2','2026-01-04T00:00:00','10','1','0002','R')")
            connection.execute("INSERT INTO PDTL(TRNID,VNO,VNO_SDTL,STORE_ID,ICODE,QTY,RATE,UOM_ID_SMALL) VALUES ('2','2','1','1','A','1','10','1')")
        ensure_baseline(self.source, self.target)
        with native_session(self.target) as connection:
            self.assertEqual(connection.execute("SELECT historical_returned_amount,return_eligible FROM baseline_trade_documents").fetchone()[:], ("10", 1))
            self.assertEqual(connection.execute("SELECT historical_returned_quantity,return_eligible FROM baseline_trade_lines").fetchone()[:], ("1", 1))

    def test_unlinked_historical_returns_and_conflicting_units_are_not_guessed(self):
        with session(self.source, readonly=False) as connection:
            connection.execute("INSERT INTO PMST(TRNID,TRNNO,TRNDATE,TRN_TYPE,CMP_ID,CHCODE,P_TYPE) VALUES ('2','2','2026-01-04T00:00:00','10','1','0002','R')")
            connection.execute("INSERT INTO PDTL(TRNID,VNO,STORE_ID,ICODE,QTY,RATE,UOM_ID_SMALL) VALUES ('2','2','1','A','1','10','1')")
        ensure_baseline(self.source, self.target)
        with native_session(self.target) as connection:
            self.assertEqual(connection.execute("SELECT return_eligible,exclusion_reason FROM baseline_trade_documents").fetchone()[:], (0, "unlinked_historical_returns_require_review"))
        other = self.target.with_name("unit-conflict.sqlite3")
        with session(self.source, readonly=False) as connection:
            connection.execute("DELETE FROM PDTL")
            connection.execute("DELETE FROM PMST")
            connection.execute("UPDATE SDTL SET UOM_ID_SMALL='2'")
        ensure_baseline(self.source, other)
        with native_session(other) as connection:
            self.assertEqual(connection.execute("SELECT return_eligible,exclusion_reason FROM baseline_trade_lines").fetchone()[:], (0, "original_unit_conflict_requires_review"))

    def test_final_live_symlink_is_not_followed(self):
        ensure_baseline(self.source, self.target)
        alias = self.target.with_name("alias.sqlite3")
        try:
            alias.symlink_to(self.target)
        except OSError:
            self.skipTest("Symlinks unavailable")
        before = self.digest(self.target)
        with self.assertRaises(ValueError):
            ensure_baseline(self.source, alias)
        self.assertEqual(before, self.digest(self.target))

    def test_cash_invoice_binding_follows_original_ledger_without_replacing_company_cash(self):
        with session(self.source, readonly=False) as connection:
            connection.execute("INSERT INTO CHART(CHCODE,CHDESC,ALLOWED) VALUES ('04010001','Original invoice cash','1')")
            connection.execute("UPDATE SMST SET S_TYPE='C',CUSTOMER='Synthetic walk-in'")
        result = ensure_baseline(self.source, self.target)
        with native_session(self.target) as connection:
            original_cash = self.mapping(connection, "CHART", "04010001")
            configured_cash = self.mapping(connection, "CHART", "0001")
            header = connection.execute("SELECT * FROM baseline_trade_documents").fetchone()
            self.assertEqual((header["kind"], header["account_id"], header["return_eligible"]), ("cash_sale", original_cash, 1))
            self.assertEqual(json.loads(header["snapshot_json"])["cash_settlement"]["cash_account_id"], original_cash)
            self.assertEqual(connection.execute("SELECT cash_account_id FROM companies").fetchone()[0], configured_cash)
            self.assertEqual(connection.execute("SELECT kind FROM accounts WHERE id=?", (original_cash,)).fetchone()[0], "cash")
            self.assertEqual(result.manifest["cash_role_evidence"][original_cash], {"configuration": False, "invoice_party_arm": True})


if __name__ == "__main__":
    unittest.main()
