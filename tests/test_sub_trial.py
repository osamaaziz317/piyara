"""Hierarchical native reports use only synthetic temporary business records."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Context, Decimal, localcontext
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma import native_reports, report_export
from bifma.database import session
from bifma.native_reports import NativeReports
from bifma.native_schema import native_session, native_transaction
from bifma.report_export import ReportExporter
from bifma.security import AuthenticationError, AuthorizationError
from tests import test_native_reports as report_fixtures


class SubTrialTests(unittest.TestCase):
    document = report_fixtures.NativeReportTests.document

    def setUp(self):
        report_fixtures.NativeReportTests.setUp(self)
        self.exporter = ReportExporter(self.path, self.security)
        self.root = self.account("100", "Main group", postable=False, name_urdu="مرکزی کھاتہ")
        self.branch = self.account("120", "Branch", parent=self.root)
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET parent_id=? WHERE id=?", (self.branch, self.debit))

    def account(self, code, name=None, *, parent=None, postable=True, active=True, name_urdu=None):
        identifier = str(uuid4())
        with native_transaction(self.path) as connection:
            connection.execute(
                "INSERT INTO accounts(id,code,name,name_urdu,parent_id,is_postable,is_active) VALUES (?,?,?,?,?,?,?)",
                (identifier, code, name or code, name_urdu, parent, int(postable), int(active)))
        return identifier

    def post(self, number, amount, *, debit=None, credit=None, business_date="2026-10-01", **options):
        old_debit, old_credit = self.debit, self.credit
        self.debit, self.credit = debit or old_debit, credit or old_credit
        try:
            return self.document(number, business_date, amount, **options)
        finally:
            self.debit, self.credit = old_debit, old_credit

    def report(self, actor=None, company=None, **filters):
        return self.reports.sub_trial_balance(actor or self.admin, company or self.company, **filters)

    def export(self, actor=None, company=None, filename="sub-trial.html", **filters):
        destination = self.path.parent / filename
        self.exporter.export_html(actor or self.admin, company or self.company, "sub_trial_balance", destination, **filters)
        return destination.read_text(encoding="utf-8")

    def reader(self, module="trial_balance"):
        user = self.security.create_user(self.admin, "reader", "Silver orchard 63 green river")
        actor = self.security.authenticate("reader", "Silver orchard 63 green river")
        self.security.set_company_access(self.admin, user.id, [self.company])
        self.security.set_permissions(self.admin, user.id, module, ["read"])
        return user, actor

    def test_three_levels_include_own_and_descendant_activity_but_totals_once(self):
        self.post("LEAF", "10.25")
        self.post("PARENT OWN", "2.1", debit=self.branch)
        result = self.report()
        rows = result["rows"]
        self.assertEqual([row["account_id"] for row in rows], [self.root, self.branch, self.debit, self.credit])
        self.assertEqual([row["depth"] for row in rows], [0, 1, 2, 0])
        self.assertEqual([row["parent_id"] for row in rows], [None, self.root, self.branch, None])
        self.assertEqual([row["debit"] for row in rows], [Decimal("12.35"), Decimal("12.35"), Decimal("10.25"), Decimal(0)])
        self.assertEqual(rows[0]["name_urdu"], "مرکزی کھاتہ")
        self.assertEqual(rows[0]["is_postable"], 0)
        self.assertEqual(rows[1]["is_postable"], 1)
        self.assertEqual(result["totals"], {"opening": Decimal(0), "debit": Decimal("12.35"), "credit": Decimal("12.35"), "closing": Decimal(0)})
        self.assertNotEqual(sum(row["debit"] for row in rows), result["totals"]["debit"])

    def test_explicit_parent_links_override_codes_and_siblings_sort_stably(self):
        first = self.account("001", parent=self.branch)
        last = self.account("999", parent=self.branch)
        unrelated_prefix = self.account("120-UNRELATED")
        for number, identifier in enumerate((last, first, unrelated_prefix)):
            self.post(str(number), "1", debit=identifier)
        rows = self.report()["rows"]
        self.assertEqual([row["account_id"] for row in rows], [self.root, self.branch, first, last, unrelated_prefix, self.credit])
        self.assertEqual(next(row for row in rows if row["account_id"] == unrelated_prefix)["depth"], 0)
        self.assertNotIn(self.debit, [row["account_id"] for row in rows])

    def test_company_date_and_posted_scope_match_flat_trial_balance(self):
        self.post("OPEN", "7.25", business_date="2026-09-30")
        self.post("FIRST", "0.1", business_date="2026-10-01")
        self.post("LAST", "0.2", business_date="2026-10-02")
        future = self.account("FUTURE")
        other = self.account("OTHER")
        self.post("FUTURE", "20", business_date="2026-10-03", debit=future)
        self.post("OTHER", "30", company=self.other, debit=other)
        self.post("DRAFT", "40", posted=False)
        filters = {"date_from": "2026-10-01", "date_to": "2026-10-02"}
        result = self.report(**filters)
        root = next(row for row in result["rows"] if row["account_id"] == self.root)
        self.assertEqual((root["opening"], root["debit"], root["credit"], root["closing"]), (Decimal("7.25"), Decimal("0.3"), Decimal(0), Decimal("7.55")))
        self.assertEqual(result["totals"], self.reports.trial_balance(self.admin, self.company, **filters)["totals"])
        self.assertNotIn(future, [row["account_id"] for row in result["rows"]])
        self.assertNotIn(other, [row["account_id"] for row in result["rows"]])

    def test_inactive_and_net_zero_history_and_ancestors_are_retained(self):
        self.post("FIRST", "3", business_date="2026-09-29")
        self.post("CANCEL", "3", debit=self.credit, credit=self.debit, business_date="2026-09-30")
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET is_active=0,is_postable=0 WHERE id IN (?,?,?)", (self.root, self.branch, self.debit))
        rows = self.report(date_from="2026-10-01")["rows"]
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row[key] == 0 for row in rows for key in ("opening", "debit", "credit", "closing")))
        self.assertEqual([row["is_active"] for row in rows], [0, 0, 0, 1])

    def test_empty_scope_excludes_unrelated_shared_branches(self):
        self.post("OTHER", "5", company=self.other)
        result = self.report()
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["totals"], dict.fromkeys(("opening", "debit", "credit", "closing"), Decimal(0)))
        self.assertIn("No rows in the selected period", self.export())

    def test_current_reparenting_changes_only_hierarchy_not_totals_or_postings(self):
        self.post("ENTRY", "5")
        before = self.report()
        with native_transaction(self.path) as connection:
            original = [tuple(row) for row in connection.execute("SELECT * FROM journal_lines ORDER BY id")]
            connection.execute("UPDATE accounts SET parent_id=NULL,name='Renamed branch' WHERE id=?", (self.branch,))
        after = self.report()
        self.assertEqual(before["totals"], after["totals"])
        self.assertNotIn(self.root, [row["account_id"] for row in after["rows"]])
        branch = next(row for row in after["rows"] if row["account_id"] == self.branch)
        self.assertEqual((branch["name"], branch["depth"]), ("Renamed branch", 0))
        with native_session(self.path) as connection:
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM journal_lines ORDER BY id")], original)

    def test_cycles_in_used_ancestor_chain_fail_without_partial_report(self):
        self.post("ENTRY", "1")
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET parent_id=? WHERE id=?", (self.debit, self.root))
        for action in (self.report, self.export):
            with self.assertRaisesRegex(ValueError, "hierarchy contains a cycle"):
                action()
        self.assertFalse((self.path.parent / "sub-trial.html").exists())

    def test_missing_parent_in_used_chain_is_reported_defensively(self):
        self.post("ENTRY", "1")
        # Deliberate corruption of a private synthetic fixture, never app data.
        with session(self.path, readonly=False) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("UPDATE accounts SET parent_id=? WHERE id=?", (str(uuid4()), self.root))
        with self.assertRaisesRegex(ValueError, "missing parent"):
            self.report()

    def test_unused_malformed_branch_does_not_affect_selected_company(self):
        unrelated = self.account("UNUSED")
        other = self.account("UNUSED-CHILD", parent=unrelated)
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET parent_id=? WHERE id=?", (other, unrelated))
        self.post("ENTRY", "1")
        self.assertEqual(len(self.report()["rows"]), 4)

    def test_deep_chart_is_iterative_with_linear_row_metadata_and_bounded_print_indent(self):
        parent = None
        identifiers = [str(uuid4()) for _ in range(1200)]
        with native_transaction(self.path) as connection:
            for index, identifier in enumerate(identifiers):
                connection.execute("INSERT INTO accounts(id,code,name,parent_id) VALUES (?,?,?,?)", (identifier, f"DEEP-{index:04}", "Deep account", parent))
                parent = identifier
        self.post("DEEP", "1", debit=parent)
        rows = self.report()["rows"]
        deepest = next(row for row in rows if row["account_id"] == parent)
        self.assertEqual(deepest["depth"], 1199)
        self.assertEqual(len(rows), 1201)
        self.assertTrue(all("path" not in row for row in rows))
        identifier_set = set(identifiers)
        self.assertTrue(all(row["debit"] == 1 for row in rows if row["account_id"] in identifier_set))
        content = self.export()
        self.assertIn('Level <bdi dir="auto">1200</bdi>', content)
        self.assertIn('margin-inline-start:8em', content)
        self.assertNotIn('margin-inline-start:1199em', content)

    def test_live_company_and_module_permissions_and_expiry_protect_report_and_export(self):
        user, actor = self.reader("ledger")
        for action in (self.report, self.export):
            with self.assertRaises(AuthorizationError):
                action(actor=actor)
        self.security.set_permissions(self.admin, user.id, "trial_balance", ["read"])
        self.report(actor=actor)
        self.export(actor=actor)
        with self.assertRaises(AuthorizationError):
            self.report(actor=actor, company=self.other)
        self.security.set_company_access(self.admin, user.id, [])
        with self.assertRaises(AuthorizationError):
            self.report(actor=actor)
        self.security.set_company_access(self.admin, user.id, [self.company])
        self.security.set_permissions(self.admin, user.id, "trial_balance", [])
        with self.assertRaises(AuthorizationError):
            self.report(actor=actor)
        with patch.object(self.security, "clock", return_value=self.admin.expires_at + 1), self.assertRaises(AuthenticationError):
            self.report()

    def test_large_exact_aggregates_and_negative_balances_ignore_ambient_precision(self):
        amount = "9" * 64
        self.post("ONE", amount)
        self.post("TWO", amount)
        expected = Decimal(str(2 * int(amount)))
        with localcontext(Context(prec=3)):
            result = self.report()
            content = self.export()
        root = next(row for row in result["rows"] if row["account_id"] == self.root)
        self.assertEqual(root["closing"], expected)
        self.assertEqual(result["totals"]["debit"], expected)
        self.assertIn(f'>{expected}</bdi>', content)
        self.assertIn(f'>-{expected}</bdi>', content)

    def test_export_contains_full_hierarchy_beyond_screen_sized_page_and_escaped_labels(self):
        leaves = [str(uuid4()) for _ in range(501)]
        document, journal = str(uuid4()), str(uuid4())
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET name='<script>branch</script>',name_urdu='کھاتہ' WHERE id=?", (self.root,))
            connection.executemany("INSERT INTO accounts(id,code,name,parent_id) VALUES (?,?,?,?)", [(identifier, f"LEAF-{index:04}", f"Leaf {index}", self.root) for index, identifier in enumerate(leaves)])
            connection.execute("INSERT INTO documents(id,company_id,kind,document_number,business_date,total_amount,rounding_policy,posting_places,created_by) VALUES (?,?,'journal','MANY','2026-10-01','50.1','cents',2,?)", (document, self.company, self.admin.user.id))
            connection.execute("INSERT INTO journal_entries(id,company_id,document_id) VALUES (?,?,?)", (journal, self.company, document))
            connection.executemany("INSERT INTO journal_lines(id,company_id,entry_id,line_number,account_id,debit,credit) VALUES (?,?,?,?,?,?,?)", [(str(uuid4()), self.company, journal, index + 1, identifier, "0.1", "0") for index, identifier in enumerate(leaves)] + [(str(uuid4()), self.company, journal, 502, self.credit, "0", "50.1")])
            connection.execute("UPDATE documents SET status='posted',revision=1,posted_by=?,posted_at=1 WHERE id=?", (self.admin.user.id, document))
        content = self.export()
        self.assertEqual(content.count('<tr class="report-row">'), 503)
        self.assertIn("LEAF-0500", content)
        self.assertLess(content.index("LEAF-0000"), content.index("LEAF-0500"))
        self.assertIn("&lt;script&gt;branch&lt;/script&gt;", content)
        self.assertNotIn("<script>", content)
        self.assertIn("کھاتہ", content)
        self.assertEqual(content.count("Grand totals (direct postings once)"), 1)
        self.assertIn('<th scope="row">Period debits</th><td class="number"><bdi dir="ltr">50.1</bdi>', content)
        self.assertNotIn('>100.2</bdi>', content)

    def test_export_row_cap_includes_ancestor_nodes_and_refuses_partial_files(self):
        self.post("ENTRY", "1")
        with patch.object(native_reports, "MAX_EXPORT_ROWS", 3), self.assertRaisesRegex(ValueError, "exceeds 3 rows"):
            self.export()
        self.assertFalse((self.path.parent / "sub-trial.html").exists())
        with patch.object(native_reports, "MAX_EXPORT_ROWS", 4):
            self.assertEqual(self.export().count('<tr class="report-row">'), 4)

    def test_invalid_dates_and_export_only_date_filters(self):
        for filters in ({"date_from": "2026-02-30"}, {"date_from": "2026-10-02", "date_to": "2026-10-01"}):
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                self.report(**filters)
        for filters in ({"account_id": self.debit}, {"limit": 500}, {"pool": "regular"}):
            with self.subTest(filters=filters), self.assertRaisesRegex(ValueError, "Unsupported report filters"):
                self.export(**filters)

    def test_export_snapshot_keeps_company_hierarchy_and_amounts_consistent(self):
        self.post("ENTRY", "1")
        started = Event()
        executor = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(executor.shutdown)
        query = NativeReports._query_sub_trial_balance
        render = report_export._render
        future = []
        def writer():
            started.set()
            with native_transaction(self.path) as connection:
                connection.execute("UPDATE accounts SET name='After snapshot',parent_id=NULL WHERE id=?", (self.branch,))
                connection.execute("UPDATE companies SET name='After snapshot' WHERE id=?", (self.company,))
        def snapshot(connection, company, **filters):
            future.append(executor.submit(writer))
            self.assertTrue(started.wait(2))
            self.assertFalse(future[0].done())
            return query(connection, company, **filters)
        def rendered(value):
            future[0].result(timeout=10)
            return render(value)
        with patch.object(NativeReports, "_query_sub_trial_balance", side_effect=snapshot), patch.object(report_export, "_render", side_effect=rendered):
            content = self.export()
        self.assertIn("Company A", content)
        self.assertIn("Main group", content)
        self.assertNotIn("After snapshot", content)

    def test_permission_revocation_during_render_removes_unpublished_file(self):
        user, actor = self.reader()
        render = report_export._render
        def revoke(snapshot):
            value = render(snapshot)
            self.security.set_permissions(self.admin, user.id, "trial_balance", [])
            return value
        with patch.object(report_export, "_render", side_effect=revoke), self.assertRaises(AuthorizationError):
            self.export(actor=actor)
        self.assertFalse((self.path.parent / "sub-trial.html").exists())
        self.assertEqual(list(self.path.parent.glob(".bifma-report-*")), [])


if __name__ == "__main__":
    unittest.main()
