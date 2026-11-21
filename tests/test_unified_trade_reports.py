"""Combined trade reports use disposable archives and posted native records."""

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
from bifma.native_schema import native_session, native_transaction
from bifma.report_export import ReportExporter
from bifma.security import AuthorizationError, SecurityService
from bifma.transactions import TransactionService
from bifma.unified_reports import UnifiedReports
from tests.test_baseline_import import synthetic_archive
from tests.test_document_register import all_original_modules


class UnifiedTradeReportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.prepare(all_original_modules)

    def prepare(self, builder, suffix=""):
        self.source = self.directory / ("source" + suffix + ".sqlite3")
        self.path = self.directory / ("working" + suffix + ".sqlite3")
        builder(self.source)
        self.before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        ensure_baseline(self.source, self.path)
        self.security = SecurityService(self.path)
        self.actor = self.security.bootstrap_admin("owner", "Green rivers sparkle beneath moonlight 84")
        self.service = TransactionService(self.path, self.security)
        self.register = DocumentRegister(self.path, self.source, security=self.security)
        self.reports = UnifiedReports(self.path, security=self.security, register=self.register)
        self.exporter = ReportExporter(self.path, self.security, reports=self.reports)
        with native_session(self.path) as connection:
            self.company = connection.execute("SELECT id FROM companies WHERE code='1'").fetchone()[0]
            self.product = connection.execute("SELECT id FROM products WHERE code='A'").fetchone()[0]
            self.store = connection.execute("SELECT id FROM stores WHERE code='1'").fetchone()[0]
            self.party = connection.execute("SELECT id FROM accounts WHERE code='0002'").fetchone()[0]

    def post(self, kind="sale", number="new-sale", when="2026-01-04"):
        draft = self.service.save_draft(self.actor, self.company, kind, {
            "document_number": number, "business_date": when,
            "account_id": self.party, "rounding_policy": "cents", "posting_places": 2,
            "lines": [{"product_id": self.product, "store_id": self.store,
                       "quantity": "1", "rate": "10", "discount_percent": "0"}]})
        return self.service.post(self.actor, self.company, draft["id"], draft["revision"])

    def report(self, family="sales", **filters):
        return self.reports.trade_detail(self.actor, self.company, family, **filters)

    def reader(self, modules):
        user = self.security.create_user(self.actor, "reader", "Silver orchard 63 green river")
        self.security.set_company_access(self.actor, user.id, [self.company])
        for module in modules:
            self.security.set_permissions(self.actor, user.id, module, ["read"])
        return user, self.security.authenticate("reader", "Silver orchard 63 green river")

    def test_original_returns_and_native_reversals_merge_before_pagination(self):
        posted = self.post()
        self.service.reverse(self.actor, self.company, posted["id"], posted["revision"],
                             document_number="REV-new-sale", business_date="2026-01-05", reason="Synthetic correction")
        result = self.report()
        self.assertEqual((result["total"], result["document_count"], result["original_document_count"], result["current_document_count"]), (5, 5, 3, 2))
        self.assertEqual([row["source"] for row in result["rows"]], ["original"] * 3 + ["current"] * 2)
        self.assertEqual(result["totals"], {"invoice": 40, "return": -10, "reversal": -10, "net": 20})
        self.assertEqual(result["quantity_totals"][0]["net"], 2)
        # The zero original invoice is present even though its HAVING clause
        # establishes no party amount; no zero header total is fabricated.
        self.assertIsNone(result["document_totals"]["net"])
        self.assertEqual(result["unknown_document_amount_count"], 1)
        for index, row in enumerate(result["rows"]):
            page = self.report(offset=index, limit=1)
            self.assertEqual(page["rows"], [row])
            self.assertEqual(page["totals"], result["totals"])
            self.assertEqual(page["document_totals"], result["document_totals"])
        self.assertEqual(self.report(offset=2, limit=2)["rows"], result["rows"][2:4])
        self.assertEqual(self.report(offset=50)["rows"], [])
        original_return = next(row for row in result["rows"] if row["kind"] == "sale_return")
        self.assertEqual((original_return["source_table"], original_return["source_document_id"], original_return["signed_quantity"]), ("PMST", "3", -1))
        self.assertIsNone(original_return["document_id"])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.before)

    def test_cash_modes_returns_and_companies_partition_original_rows(self):
        for family, count, amount in (("sales", 3, 20), ("purchases", 2, 10)):
            with self.subTest(family=family):
                account = self.report(family)
                cash = self.report(family, payment_mode="cash")
                combined = self.report(family, payment_mode="all")
                self.assertEqual((account["total"], account["totals"]["net"]), (count, amount))
                self.assertEqual((cash["total"], cash["totals"]["net"]), (2, 0 if family == "sales" else 10))
                self.assertEqual(combined["total"], account["total"] + cash["total"])
                self.assertEqual({row["payment_mode"] for row in cash["rows"]}, {"cash"})
                self.assertEqual(self.report(family, payment_mode="all", kind="return")["total"], 2)
        with native_session(self.path) as connection:
            other = connection.execute("SELECT id FROM companies WHERE code='2'").fetchone()[0]
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM baseline_trade_documents WHERE return_eligible=1").fetchone()[0], 0)
        self.assertEqual(self.reports.trade_detail(self.actor, other, "sales", payment_mode="all")["total"], 0)
        self.assertEqual(self.reports.trade_selectors(self.actor, other, "sales", payment_mode="all"), {"accounts": [], "products": [], "stores": []})

    def test_original_selectors_filters_saved_labels_and_native_history_match(self):
        posted = self.post()
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE products SET name='Renamed item',is_active=0 WHERE id=?", (self.product,))
            connection.execute("UPDATE accounts SET name='Renamed party',is_active=0 WHERE id=?", (self.party,))
            connection.execute("UPDATE stores SET name='Renamed store',is_active=0 WHERE id=?", (self.store,))
        selectors = self.reports.trade_selectors(self.actor, self.company, "sales")
        self.assertEqual(selectors["products"][0]["name"], "Renamed item")
        self.assertEqual(selectors["accounts"][0]["id"], self.party)
        for filters in ({"product_id": self.product}, {"store_id": self.store}, {"product_id": self.product, "store_id": self.store}):
            result = self.report(**filters)
            self.assertEqual(result["total"], 4)
            self.assertIsNone(result["document_totals"])
            self.assertEqual({row["product_name"] for row in result["rows"]}, {"Synthetic item"})
            self.assertEqual({row["account_name"] for row in result["rows"]}, {"Party"})
            self.assertEqual({row["store_name"] for row in result["rows"]}, {"Main store"})
        earlier = self.report(account_id=self.party, product_id=self.product, date_to="2026-01-02")
        self.assertEqual(earlier["total"], 3)
        later = self.report(date_from="2026-01-04")
        self.assertEqual([row["document_id"] for row in later["rows"]], [posted["id"]])
        self.assertEqual(self.report(search="260001")["total"], 1)
        self.assertEqual(self.report(search="%_")["total"], 0)
        self.assertEqual(self.report(date_from="2026-01-03", date_to="2026-01-03")["rows"], [])

    def test_baseline_only_selectors_are_filterable_without_native_trade_history(self):
        selectors = self.reports.trade_selectors(self.actor, self.company, "purchases")
        self.assertEqual(selectors["products"][0]["id"], self.product)
        filtered = self.report("purchases", account_id=self.party, product_id=self.product, store_id=self.store)
        self.assertEqual((filtered["total"], filtered["totals"]["net"]), (2, 10))
        self.assertIsNone(filtered["document_totals"])
        with native_session(self.path) as connection:
            missing = connection.execute("SELECT id FROM accounts WHERE code='0005'").fetchone()[0]
        with self.assertRaisesRegex(ValueError, "original or posted"):
            self.report(account_id=missing)

    def test_original_exact_decimals_header_rules_and_unit_conflicts_remain_visible(self):
        def source(path):
            synthetic_archive(path)
            with session(path, readonly=False) as connection:
                connection.execute("UPDATE SMST SET DISCOUNT='1.15',BREAKAGE='.1',FREIGHT='.25'")
                connection.execute("UPDATE SDTL SET QTY='1.123456789',RATE='123456789.123456789',DIS_PER='12.3456789',UOM_ID_SMALL='2'")
        self.prepare(source, "-exact")
        with localcontext(Context(prec=1000)):
            expected = Decimal("1.123456789") * Decimal("123456789.123456789") * (1 - Decimal("12.3456789") / 100)
        with localcontext(Context(prec=3)):
            result = self.report(kind="invoice")
        self.assertEqual(result["totals"]["net"], expected)
        row = result["rows"][0]
        self.assertEqual((row["unit_code"], row["unit_name"]), ("2", "Pack"))
        self.assertIn("Recorded unit differs from original item master", row["issues"])
        detail = self.register.get(self.actor, self.company, "sale", "1", source="original")
        self.assertEqual(result["document_totals"]["net"], Decimal(detail["total_amount"]))
        self.assertNotEqual(result["document_totals"]["net"], expected)

    def test_missing_values_negative_values_and_empty_original_headers_are_not_hidden(self):
        def source(path):
            synthetic_archive(path)
            with session(path, readonly=False) as connection:
                connection.execute("UPDATE SDTL SET QTY='-2',RATE=NULL,UOM_ID_SMALL=NULL")
                connection.execute("INSERT INTO SMST(TRNID,TRNNO,TRNDATE,TRN_TYPE,CMP_ID,CHCODE,S_TYPE) "
                                   "VALUES('2','2','2026-01-02T00:00:00','8','1','0002','R')")
        self.prepare(source, "-unknown")
        result = self.report(kind="invoice")
        self.assertEqual((result["total"], result["document_count"], result["original_empty_document_count"]), (1, 2, 1))
        row = result["rows"][0]
        self.assertEqual(row["signed_quantity"], -2)
        self.assertIsNone(row["rate"])
        self.assertIsNone(row["signed_net_amount"])
        self.assertIsNone(result["totals"]["net"])
        self.assertIsNone(result["document_totals"]["net"])
        self.assertFalse(row["unit_known"])
        self.assertIn("Negative original quantity", row["issues"])
        self.assertEqual(result["missing_amount_count"], 1)
        target = self.directory / "unknown.html"
        self.exporter.export_html(self.actor, self.company, "sales_detail", target, kind="invoice")
        html = target.read_text()
        self.assertIn("Unknown", html)
        self.assertIn("Negative original quantity", html)
        self.assertIn('Original documents without lines: <bdi dir="auto">1</bdi>', html)

    def test_original_query_batches_preserve_every_free_line_and_literal_search(self):
        def source(path):
            synthetic_archive(path)
            with session(path, readonly=False) as connection:
                connection.execute("UPDATE CHART SET CHDESC='Party <& name' WHERE CHCODE='0002'")
                for index in range(2, 128):
                    connection.execute("INSERT INTO SMST(TRNID,TRNNO,TRNDATE,TRN_TYPE,CMP_ID,CHCODE,S_TYPE) "
                                       "VALUES(?,?,'2026-01-02T00:00:00','8','1','0002','R')", (str(index), str(index)))
                    connection.execute("INSERT INTO SDTL(TRNID,VNO,LINE_NUM,STORE_ID,ICODE,QTY,RATE,UOM_ID_SMALL) "
                                       "VALUES(?,?,'1','1','A','0','0','1')", (str(index), str(index)))
        self.prepare(source, "-batches")
        first = self.report(kind="invoice", limit=100)
        last = self.report(kind="invoice", offset=100, limit=100)
        self.assertEqual((first["total"], len(first["rows"]), len(last["rows"])), (127, 100, 27))
        self.assertEqual(first["totals"]["net"], 30)
        self.assertEqual(first["quantity_totals"][0]["net"], 3)
        self.assertEqual(len({row["source_document_id"] for row in first["rows"] + last["rows"]}), 127)
        target = self.directory / "batches.html"
        self.exporter.export_html(self.actor, self.company, "sales_detail", target, kind="invoice")
        html = target.read_text()
        self.assertEqual(html.count("Original record</bdi>"), 127)
        self.assertIn("Party &lt;&amp; name", html)
        self.assertNotIn("Party <& name", html)

    def test_missing_original_quantity_propagates_without_zero_quantity_total(self):
        def source(path):
            synthetic_archive(path)
            with session(path, readonly=False) as connection:
                connection.execute("UPDATE SDTL SET QTY=NULL")
        self.prepare(source, "-quantity")
        result = self.report(kind="invoice")
        row = result["rows"][0]
        self.assertIsNone(row["quantity"])
        self.assertEqual(row["rate"], 10)
        self.assertFalse(row["quantity_known"])
        self.assertIsNone(result["totals"]["net"])
        self.assertIsNone(result["quantity_totals"][0]["net"])
        self.assertEqual(result["missing_quantity_count"], 1)

    def test_source_changed_after_original_read_discards_combined_result(self):
        query = native_reports.NativeReports._query_trade_detail
        def mutate(*args, **kwargs):
            result = query(*args, **kwargs)
            with session(self.source, readonly=False) as connection:
                connection.execute("UPDATE SDTL SET RATE='11' WHERE TRNID='1'")
            return result
        with patch.object(native_reports.NativeReports, "_query_trade_detail", side_effect=mutate), self.assertRaisesRegex(BaselineError, "changed"):
            self.report()

    def test_live_scope_and_company_grants_apply_to_selectors_query_and_export(self):
        user, reader = self.reader(["sales"])
        self.assertEqual(self.reports.trade_detail(reader, self.company, "sales", kind="invoice")["total"], 2)
        for method, args, filters in ((self.reports.trade_detail, ("sales",), {}),
                                     (self.reports.trade_selectors, ("sales",), {"payment_mode": "all", "kind": "invoice"}),
                                     (self.reports.export_snapshot, ("sales_detail",), {"payment_mode": "cash", "kind": "invoice"})):
            with self.subTest(method=method.__name__), self.assertRaises(AuthorizationError):
                method(reader, self.company, *args, **filters)
        self.security.set_permissions(self.actor, user.id, "sales", [])
        with self.assertRaises(AuthorizationError):
            self.reports.trade_selectors(reader, self.company, "sales", kind="invoice")
        self.security.set_permissions(self.actor, user.id, "sales", ["read"])
        self.security.set_company_access(self.actor, user.id, [])
        with self.assertRaises(AuthorizationError):
            self.reports.trade_detail(reader, self.company, "sales", kind="invoice")

    def test_full_exports_share_combined_totals_limits_labels_and_final_authorization(self):
        self.post()
        for family, report in (("sales", "sales_detail"), ("purchases", "purchase_detail")):
            result = self.report(family, payment_mode="all")
            snapshot = self.reports.export_snapshot(self.actor, self.company, report, payment_mode="all")
            self.assertEqual(snapshot["result"]["rows"], result["rows"])
            self.assertEqual(snapshot["result"]["totals"], result["totals"])
            destination = self.directory / (report + ".html")
            self.exporter.export_html(self.actor, self.company, report, destination, payment_mode="all")
            html = destination.read_text()
            self.assertEqual(html.count('<tr class="report-row">'), result["total"] + len(result["quantity_totals"]))
            self.assertIn("Original records and new posted trade activity", html)
            self.assertIn(self.before, html)
            self.assertNotIn("Posted native transactions only", html)
        with patch.object(native_reports, "MAX_EXPORT_ROWS", 3), self.assertRaisesRegex(ValueError, "exceeds 3 rows"):
            self.reports.export_snapshot(self.actor, self.company, "sales_detail")
        user, reader = self.reader(["sales"])
        render = report_export._render
        def revoke(snapshot):
            content = render(snapshot)
            self.security.set_permissions(self.actor, user.id, "sales", [])
            return content
        target = self.directory / "revoked.html"
        with patch.object(report_export, "_render", side_effect=revoke), self.assertRaises(AuthorizationError):
            self.exporter.export_html(reader, self.company, "sales_detail", target, kind="invoice")
        self.assertFalse(target.exists())
        self.assertEqual(list(self.directory.glob(".bifma-report-*")), [])

    def test_changed_archive_before_read_or_after_render_cannot_publish(self):
        render = report_export._render
        def mutate(snapshot):
            content = render(snapshot)
            with session(self.source, readonly=False) as connection:
                connection.execute("UPDATE SDTL SET RATE='11' WHERE TRNID='1'")
            return content
        target = self.directory / "changed.html"
        with patch.object(report_export, "_render", side_effect=mutate), self.assertRaisesRegex(BaselineError, "does not match"):
            self.exporter.export_html(self.actor, self.company, "sales_detail", target)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.directory.glob(".bifma-report-*")), [])
        with self.assertRaises(BaselineError):
            self.report()
        with self.assertRaises(BaselineError):
            self.reports.trade_selectors(self.actor, self.company, "sales")


if __name__ == "__main__":
    unittest.main()
