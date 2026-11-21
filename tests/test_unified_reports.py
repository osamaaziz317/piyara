"""Original and new stock share filters without double-counting the baseline."""

from decimal import Context, Decimal, localcontext
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bifma import native_reports, report_export
from bifma.baseline import BaselineError, ensure_baseline
from bifma.database import session
from bifma.document_register import DocumentRegister
from bifma.legacy_stock import LegacyStock
from bifma.native_reports import NativeReports
from bifma.native_schema import native_session, native_transaction
from bifma.report_export import ReportExporter
from bifma.security import AuthorizationError, SecurityService
from bifma.transactions import TransactionService
from bifma.unified_reports import UnifiedReports
from tests.test_baseline_import import synthetic_archive


class UnifiedReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / "original.sqlite3"
        self.path = Path(self.directory.name) / "live.sqlite3"
        synthetic_archive(self.source)
        self.before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        ensure_baseline(self.source, self.path)
        self.security = SecurityService(self.path)
        self.actor = self.security.bootstrap_admin("owner", "Green rivers sparkle beneath moonlight 84")
        self.transactions = TransactionService(self.path, self.security)
        self.register = DocumentRegister(self.path, self.source, security=self.security)
        self.reports = UnifiedReports(self.path, security=self.security, register=self.register)
        self.native = NativeReports(self.path, security=self.security)
        self.exporter = ReportExporter(self.path, self.security, reports=self.reports)
        with native_session(self.path) as connection:
            self.company = connection.execute("SELECT id FROM companies WHERE code='1'").fetchone()[0]
            self.product = connection.execute("SELECT id FROM products WHERE code='A'").fetchone()[0]
            self.store = connection.execute("SELECT id FROM stores WHERE code='1'").fetchone()[0]
            self.party = connection.execute("SELECT id FROM accounts WHERE code='0002'").fetchone()[0]

    def post(self, kind, day, quantity, rate):
        draft = self.transactions.save_draft(self.actor, self.company, kind, {
            "document_number": kind + "-new", "business_date": day,
            "account_id": self.party, "rounding_policy": "cents", "posting_places": 2,
            "lines": [{"product_id": self.product, "store_id": self.store,
                       "quantity": quantity, "rate": rate, "discount_percent": "0"}],
        })
        return self.transactions.post(self.actor, self.company, draft["id"], draft["revision"])

    def new_activity(self):
        self.post("purchase", "2026-01-04", "2", "5")
        self.post("sale", "2026-01-05", "1", "9")

    def assert_amounts(self, row, amounts):
        keys = ("opening", "in", "out", "closing", "value_opening", "value_in", "value_out", "value_closing")
        self.assertEqual(tuple(row[key] for key in keys), tuple(Decimal(value) for value in amounts))
        self.assertEqual(row["missing_valuation_count"], 0)

    def reader(self):
        user = self.security.create_user(self.actor, "reader", "Silver orchard 63 green river")
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.set_permissions(self.actor, user.id, "stock", ["read"])
        return user, self.security.authenticate("reader", "Silver orchard 63 green river")

    def test_earlier_period_reconstructs_original_flows_not_cutoff_snapshot(self):
        self.new_activity()
        with localcontext(Context(prec=3)):
            row = self.reports.stock(self.actor, self.company, date_from="2026-01-02", date_to="2026-01-02")[0]
        self.assert_amounts(row, ("10", "0", "3", "7", "21.23456789", "0", "6.370370367", "14.864197523"))
        self.assertEqual(self.reports.stock(self.actor, self.company, date_to="2025-12-31"), [])
        self.assertEqual(self.reports.stock(self.actor, self.company, pool="breakage"), [])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.before)

    def test_cross_cutoff_and_all_dates_use_each_original_and_new_movement_once(self):
        self.new_activity()
        with localcontext(Context(prec=3)):
            period = self.reports.stock_valuation(self.actor, self.company, date_from="2026-01-02", date_to="2026-01-05")
            all_dates = self.reports.stock(self.actor, self.company)
        self.assert_amounts(period["rows"][0], ("10", "2", "4", "8", "21.23456789", "10", "11.370370367", "19.864197523"))
        self.assert_amounts(all_dates[0], ("0", "12", "4", "8", "0", "31.23456789", "11.370370367", "19.864197523"))
        self.assertEqual(period["totals"]["value_closing"], Decimal("19.864197523"))
        self.assertTrue(period["historical_activity"])
        after = {"date_from": "2026-01-04", "date_to": "2026-01-05"}
        self.assertEqual(self.reports.stock(self.actor, self.company, **after), self.native.stock(self.actor, self.company, **after))
        value = self.reports.stock_valuation(self.actor, self.company, **after)
        self.assertFalse(value.pop("historical_activity"))
        self.assertEqual(value, self.native.stock_valuation(self.actor, self.company, **after))

    def test_filters_preserve_inactive_original_references_and_current_labels(self):
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE products SET name='Renamed item',is_active=0 WHERE id=?", (self.product,))
            connection.execute("UPDATE company_stores SET is_active=0 WHERE company_id=? AND store_id=?", (self.company, self.store))
        row = self.reports.stock(self.actor, self.company, store_id=self.store, search="renamed", date_to="2026-01-02")[0]
        self.assertEqual((row["name"], row["closing"]), ("Renamed item", Decimal(7)))
        self.assertEqual(self.reports.stock(self.actor, self.company, search="not present"), [])
        with self.assertRaisesRegex(ValueError, "store"):
            self.reports.stock(self.actor, self.company, store_id="missing")
        for filters in ({"date_from": "2026-01-03", "date_to": "2026-01-02"}, {"pool": "missing"}, {"date_to": "invalid"}):
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                self.reports.stock(self.actor, self.company, **filters)

    def test_historical_reports_require_fresh_company_and_stock_grants(self):
        user, reader = self.reader()
        self.assertEqual(len(self.reports.stock(reader, self.company, date_to="2026-01-02")), 1)
        self.security.set_permissions(self.actor, user.id, "stock", [])
        for method, arguments in ((self.reports.stock, ()), (self.reports.stock_valuation, ()),
                                  (self.reports.export_snapshot, ("stock_quantities",))):
            with self.subTest(method=method.__name__), self.assertRaises(AuthorizationError):
                method(reader, self.company, *arguments, date_to="2026-01-02")
        self.security.set_permissions(self.actor, user.id, "stock", ["read"])
        self.security.set_company_access(self.actor, user.id, [])
        with self.assertRaises(AuthorizationError):
            self.reports.stock(reader, self.company, date_to="2026-01-02")

    def test_full_exports_share_original_flow_totals_and_final_authorization(self):
        self.new_activity()
        for report in ("stock_quantities", "stock_valuation"):
            with self.subTest(report=report):
                destination = self.path.parent / (report + ".html")
                self.exporter.export_html(self.actor, self.company, report, destination,
                                          date_from="2026-01-02", date_to="2026-01-05")
                content = destination.read_text(encoding="utf-8")
                self.assertIn("Original and new stock activity", content)
                self.assertIn("not added a second time", content)
                self.assertNotIn("does not reconstruct historical turnover", content)
                self.assertIn(self.before, content)
                self.assertEqual(content.count('<tr class="report-row">'), 1)
                if report == "stock_valuation":
                    self.assertIn(">19.864197523</bdi>", content)
        user, reader = self.reader()
        render = report_export._render
        def revoke(snapshot):
            content = render(snapshot)
            self.security.set_permissions(self.actor, user.id, "stock", [])
            return content
        destination = self.path.parent / "denied.html"
        with patch.object(report_export, "_render", side_effect=revoke), self.assertRaises(AuthorizationError):
            self.exporter.export_html(reader, self.company, "stock_quantities", destination, date_to="2026-01-02")
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.path.parent.glob(".bifma-report-*")), [])

    def test_export_row_limit_and_database_association_cannot_be_bypassed(self):
        with patch.object(native_reports, "MAX_EXPORT_ROWS", 0):
            for report in ("stock_quantities", "stock_valuation"):
                with self.subTest(report=report), self.assertRaisesRegex(ValueError, "exceeds 0 rows"):
                    self.reports.export_snapshot(self.actor, self.company, report, date_to="2026-01-02")
        with self.assertRaisesRegex(ValueError, "different working database"):
            ReportExporter(self.path.parent / "different.sqlite3", reports=self.reports)

    def test_changed_archive_is_refused_before_original_stock_is_read(self):
        with session(self.source, readonly=False) as connection:
            connection.execute("UPDATE SDTL SET QTY='9'")
        with patch.object(LegacyStock, "balances", side_effect=AssertionError("must not read changed archive")):
            with self.assertRaisesRegex(BaselineError, "does not match"):
                self.reports.stock(self.actor, self.company, date_to="2026-01-02")

    def test_source_change_during_read_discards_result_and_creates_no_export(self):
        balances = LegacyStock.balances
        def change(legacy, *arguments, **filters):
            rows = balances(legacy, *arguments, **filters)
            with session(self.source, readonly=False) as connection:
                connection.execute("UPDATE SDTL SET QTY='9'")
            return rows
        destination = self.path.parent / "changed.html"
        with patch.object(LegacyStock, "balances", new=change), self.assertRaisesRegex(BaselineError, "changed"):
            self.exporter.export_html(self.actor, self.company, "stock_valuation", destination, date_to="2026-01-02")
        self.assertFalse(destination.exists())

    def test_source_negative_stock_and_incoming_breakage_dispatch_remain_exact(self):
        source, target = self.path.parent / "anomalous.sqlite3", self.path.parent / "anomalous-live.sqlite3"
        synthetic_archive(source)
        with session(source, readonly=False) as connection:
            connection.execute("UPDATE SDTL SET QTY='13'")
            connection.execute("INSERT INTO BRMST(TRNID,TRNNO,TRNDATE,TRN_TYPE,CMP_ID,CHCODE,STORE_ID) "
                               "VALUES('2','2','2026-01-02T00:00:00','11','1','0002','1')")
            connection.execute("INSERT INTO BRDTL(TRNID,VNO,ICODE,QTY,RATE,UOM_ID_SMALL) VALUES('2','2','A','2','1.75','1')")
            connection.execute("INSERT INTO BDMST(TRNID,TRNNO,TRNDATE,TRN_TYPE,CMP_ID,CHCODE) "
                               "VALUES('3','3','2026-01-02T00:00:00','12','1','0002')")
            connection.execute("INSERT INTO BDDTL(TRNID,VNO,ICODE,QTY,RATE,STORE_ID,UOM_ID_SMALL) "
                               "VALUES('3','3','A','1','2.25','1','1')")
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        ensure_baseline(source, target)
        security = SecurityService(target)
        actor = security.bootstrap_admin("owner", "Green rivers sparkle beneath moonlight 84")
        reports = UnifiedReports(target, source, security=security)
        with native_session(target) as connection:
            company = connection.execute("SELECT id FROM companies WHERE code='1'").fetchone()[0]
        regular = reports.stock(actor, company, date_to="2026-01-02")[0]
        broken = reports.stock(actor, company, pool="breakage", date_to="2026-01-02")[0]
        self.assert_amounts(regular, ("0", "10", "13", "-3", "0", "21.23456789", "27.604938257", "-6.370370367"))
        self.assert_amounts(broken, ("0", "3", "0", "3", "0", "5.75", "0", "5.75"))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
