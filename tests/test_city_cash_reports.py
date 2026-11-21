"""Current-city balances and proven-role cash ledgers on synthetic native data."""

from decimal import Context, Decimal, localcontext
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma import native_reports
from bifma.native_reports import NativeReports
from bifma.native_schema import native_session, native_transaction
from bifma.security import AuthenticationError, AuthorizationError
from tests.test_transactions import TransactionFixture


_AUTO = object()


class CityCashReportTests(TransactionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.reports = NativeReports(self.path, security=self.security)

    def city(self, code, name=None):
        identifier = str(uuid4())
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO cities(id,code,name,name_urdu) VALUES(?,?,?,?)",
                               (identifier, code, name or code, "شہر " + code))
        return identifier

    def account(self, code, *, city=None, kind="general", active=True, postable=True):
        identifier = str(uuid4())
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO accounts(id,code,name,name_urdu,city_id,kind,is_active,is_postable) VALUES(?,?,?,?,?,?,?,?)",
                               (identifier, code, "Account " + code, "کھاتہ " + code, city, kind, int(active), int(postable)))
        return identifier

    def document(self, lines, *, when="2026-01-02", company=None, posted=True, sequence=None, snapshot=_AUTO):
        """Create immutable test history, allowing older or damaged metadata shapes.

        Production posting is tested separately; fixtures write effects and
        metadata while draft, then post last without disabling any guards.
        """
        company = company or self.company
        identifier, entry = str(uuid4()), str(uuid4())
        number = self.common(when)["document_number"]
        with native_transaction(self.path) as connection, localcontext(Context(prec=256)):
            total = str(sum((Decimal(debit) for _, debit, _ in lines), Decimal(0)))
            connection.execute("INSERT INTO documents(id,company_id,kind,document_number,business_date,total_amount,rounding_policy,posting_places,created_by) VALUES(?,?,'journal',?,?,?,'cents',2,?)",
                               (identifier, company, number, when, total, self.actor.user.id))
            connection.execute("INSERT INTO journal_entries(id,company_id,document_id) VALUES(?,?,?)", (entry, company, identifier))
            connection.executemany("INSERT INTO journal_lines(id,company_id,entry_id,line_number,account_id,debit,credit) VALUES(?,?,?,?,?,?,?)",
                [(str(uuid4()), company, entry, index, account, debit, credit) for index, (account, debit, credit) in enumerate(lines, 1)])
            if snapshot is _AUTO:
                snapshot = {"company": {"id": company}, "accounts": {
                    account: dict(connection.execute("SELECT * FROM accounts WHERE id=?", (account,)).fetchone())
                    for account, _, _ in lines}}
            if snapshot is not None:
                connection.execute("INSERT INTO document_metadata(document_id,company_id,input_json,snapshot_json,calculation_json) VALUES(?,?,'{}',?,'{}')",
                                   (identifier, company, json.dumps(snapshot)))
            if posted:
                connection.execute("UPDATE documents SET status='posted',revision=1,posted_by=?,posted_at=1,posting_sequence=? WHERE id=?",
                                   (self.actor.user.id, sequence, identifier))
        return identifier

    def pair(self, amount, *, debit=None, credit=None, **options):
        return self.document([(debit or self.accounts["cash"], amount, "0"),
                              (credit or self.accounts["equity"], "0", amount)], **options)

    def reader(self, *modules):
        user = self.security.create_user(self.actor, "report_reader", "Cobalt bridges welcome sunrise 83")
        actor = self.security.authenticate("report_reader", "Cobalt bridges welcome sunrise 83")
        self.security.set_company_access(self.actor, user.id, [self.company])
        for module in modules:
            self.security.set_permissions(self.actor, user.id, module, ["read"])
        return user, actor

    def test_city_exact_dates_company_and_both_closing_sides_before_grouping(self):
        city = self.city("A")
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET city_id=? WHERE id IN (?,?)", (city, self.accounts["cash"], self.accounts["equity"]))
        self.pair("9007199254740993.01", when="2026-01-01")
        self.pair("0.1", when="2026-01-02")
        self.pair("0.2", when="2026-01-03")
        self.pair("99", when="2026-01-04")
        self.pair("777", posted=False)
        self.pair("888", company=self.other_company)
        with localcontext(Context(prec=3)):
            result = self.reports.city_balances(self.actor, self.company, date_from="2026-01-02", date_to="2026-01-03", limit=1, offset=1)
        self.assertEqual((result["total"], result["offset"], result["limit"]), (2, 1, 1))
        self.assertEqual(result["rows"][0]["account_id"], self.accounts["equity"])
        self.assertEqual(result["rows"][0]["opening"], Decimal("-9007199254740993.01"))
        self.assertEqual(result["rows"][0]["closing"], Decimal("-9007199254740993.31"))
        for aggregate in (result["totals"], result["groups"][0]):
            self.assertEqual((aggregate["account_count"], aggregate["opening"], aggregate["closing"]), (2, 0, 0))
            self.assertEqual((aggregate["debit"], aggregate["credit"]), (Decimal("0.3"), Decimal("0.3")))
            self.assertEqual((aggregate["closing_debit"], aggregate["closing_credit"]), (Decimal("9007199254740993.31"),) * 2)
        empty = self.reports.city_balances(self.actor, self.company, date_from="2026-01-02", date_to="2026-01-03", offset=50)
        self.assertEqual(empty["rows"], [])
        self.assertEqual(empty["totals"], result["totals"])
        self.assertEqual(empty["groups"], result["groups"])

    def test_zero_closing_accounts_keep_period_activity_unless_explicitly_excluded(self):
        settled = self.account("SETTLED", city=self.city("Z"))
        self.pair("7", debit=settled, when="2026-01-01")
        self.pair("7", debit=self.accounts["equity"], credit=settled, when="2026-01-02")
        self.pair("2", when="2026-01-02")
        included = self.reports.city_balances(self.actor, self.company, date_from="2026-01-02")
        excluded = self.reports.city_balances(self.actor, self.company, date_from="2026-01-02", include_zero=False)
        self.assertEqual((included["total"], excluded["total"]), (3, 2))
        self.assertIn(settled, [row["account_id"] for row in included["rows"]])
        self.assertNotIn(settled, [row["account_id"] for row in excluded["rows"]])
        self.assertEqual(included["totals"]["credit"], 9)
        self.assertEqual(excluded["totals"]["credit"], 2)
        self.assertEqual(excluded["totals"]["opening"], -7)
        self.assertEqual(len(excluded["groups"]), 1)

    def test_city_selectors_and_filters_use_current_labels_and_keep_inactive_history(self):
        old, current, unused, foreign = [self.city(code) for code in ("OLD", "NEW", "UNUSED", "FOREIGN")]
        target = self.account("A%_' literal", city=old, kind="customer")
        other = self.account("FOREIGN", city=foreign)
        self.pair("5", debit=target)
        self.pair("3", debit=other, company=self.other_company)
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET city_id=?,kind='supplier',name='Current label',is_active=0,is_postable=0 WHERE id=?", (current, target))
        choices = self.reports.city_selectors(self.actor, self.company)
        self.assertEqual([row["id"] for row in choices["cities"]], ["unassigned", current])
        self.assertEqual(choices["label_basis"], "current_master")
        result = self.reports.city_balances(self.actor, self.company, city_id=current, account_kind="supplier", search="%_'")
        self.assertEqual(result["city"]["id"], current)
        self.assertEqual(result["total"], 1)
        self.assertEqual((result["rows"][0]["name"], result["rows"][0]["is_active"], result["rows"][0]["is_postable"]), ("Current label", 0, 0))
        self.assertEqual(self.reports.city_balances(self.actor, self.company, search="کھاتہ")["total"], 1)
        self.assertEqual(self.reports.city_balances(self.actor, self.company, search="A__")["total"], 0)
        self.assertEqual(self.reports.city_balances(self.actor, self.company, city_id=unused)["total"], 0)
        self.assertEqual(self.reports.city_balances(self.actor, self.company, account_kind="customer")["total"], 0)
        unassigned = self.reports.city_balances(self.actor, self.company, city_id="unassigned")
        self.assertEqual(unassigned["city"]["id"], "unassigned")
        self.assertEqual(unassigned["rows"][0]["city_name"], "Unassigned")
        self.assertIsNone(unassigned["rows"][0]["city_id"])
        self.assertEqual(self.reports.city_balances(self.actor, self.company, date_to="2026-01-01")["total"], 0)

    def test_city_totals_can_exceed_input_magnitude_without_ambient_rounding(self):
        amount = "9" * 64 + ".99"
        self.pair(amount)
        self.pair(amount)
        with localcontext(Context(prec=2)):
            result = self.reports.city_balances(self.actor, self.company)
        expected = Decimal("1" + "9" * 64 + ".98")
        self.assertEqual(result["totals"]["debit"], expected)
        self.assertEqual(result["totals"]["closing_debit"], expected)
        self.assertEqual(result["totals"]["closing_credit"], expected)

    def test_cash_book_matches_all_journal_activity_with_exact_carry_and_order(self):
        self.pair("9007199254740993.01", when="2026-01-01", sequence=10)
        later = self.pair("0.2", when="2026-01-02", sequence=30)
        earlier = self.pair("0.1", when="2026-01-02", sequence=20)
        self.pair("99", when="2026-01-03", sequence=40)
        self.pair("88", posted=False)
        self.pair("77", company=self.other_company)
        filters = dict(date_from="2026-01-02", date_to="2026-01-02", limit=1, offset=1)
        with localcontext(Context(prec=3)):
            cash = self.reports.cash_book(self.actor, self.company, self.accounts["cash"], **filters)
        ordinary = self.reports.ledger(self.actor, self.company, self.accounts["cash"], **filters)
        for key in ("rows", "total", "offset", "opening", "debit", "credit", "closing"):
            self.assertEqual(cash[key], ordinary[key])
        self.assertEqual(cash["rows"][0]["document_id"], later)
        self.assertEqual(cash["rows"][0]["balance"], Decimal("9007199254740993.31"))
        self.assertEqual(cash["cash_role_source"], "current_master")
        self.assertEqual(cash["limit"], 1)
        all_rows = self.reports.cash_book(self.actor, self.company, self.accounts["cash"], date_from="2026-01-02", date_to="2026-01-02")
        self.assertEqual([row["document_id"] for row in all_rows["rows"]], [earlier, later])

    def test_cash_ordinary_sale_journal_voucher_and_reversal_are_not_filtered_by_kind(self):
        self.opening()
        sale_values = self.trade_values(account_id=self.accounts["cash"], rate="10")
        sale = self.post(self.save("sale", sale_values))
        journal_values = self.common()
        journal_values["lines"] = [{"account_id": self.accounts["cash"], "debit": "3", "credit": "0", "narration": ""},
                                   {"account_id": self.accounts["equity"], "debit": "0", "credit": "3", "narration": ""}]
        journal = self.post(self.save("journal", journal_values))
        reversal = self.reverse(sale)
        result = self.reports.cash_book(self.actor, self.company, self.accounts["cash"])
        self.assertEqual({row["document_id"] for row in result["rows"]}, {sale["id"], journal["id"], reversal["id"]})
        self.assertEqual((result["debit"], result["credit"], result["closing"]), (13, 10, 3))

    def test_cash_choices_include_historical_inactive_roles_and_no_inferred_default(self):
        former = self.account("FORMER", kind="cash")
        inactive_used = self.account("INACTIVE_USED", kind="cash")
        inactive_unused = self.account("INACTIVE_UNUSED", kind="cash", active=False)
        nonpostable_unused = self.account("NONPOSTABLE", kind="cash", postable=False)
        self.pair("5", debit=former)
        self.pair("7", debit=inactive_used)
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET kind='general',name='Former renamed',is_active=0,is_postable=0 WHERE id=?", (former,))
            connection.execute("UPDATE accounts SET is_active=0,is_postable=0 WHERE id=?", (inactive_used,))
            connection.execute("UPDATE companies SET cash_account_id=? WHERE id=?", (former, self.company))
        choices = self.reports.cash_accounts(self.actor, self.company)
        by_id = {row["id"]: row for row in choices["accounts"]}
        self.assertEqual(set(by_id), {self.accounts["cash"], former, inactive_used})
        self.assertNotIn(inactive_unused, by_id)
        self.assertNotIn(nonpostable_unused, by_id)
        self.assertEqual(by_id[former]["cash_role_source"], "saved_snapshot")
        self.assertEqual(by_id[inactive_used]["cash_role_source"], "current_master")
        self.assertEqual(choices["default_account_id"], former)
        result = self.reports.cash_book(self.actor, self.company, former, date_to="2026-01-01")
        self.assertEqual((result["total"], result["cash_role_source"], result["account"]["name"]), (0, "saved_snapshot", "Former renamed"))
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET cash_account_id=? WHERE id=?", (self.accounts["bank"], self.company))
        self.assertIsNone(self.reports.cash_accounts(self.actor, self.company)["default_account_id"])
        with self.assertRaisesRegex(ValueError, "saved cash role"):
            self.reports.cash_book(self.actor, self.company, self.accounts["bank"])

    def test_foreign_unused_and_draft_saved_cash_roles_cannot_grant_eligibility(self):
        foreign, unused, draft = [self.account(code, kind="cash") for code in ("FOREIGN", "UNUSED", "DRAFT")]
        self.pair("5", debit=foreign, company=self.other_company)
        self.pair("2", debit=draft, posted=False)
        snapshot = {"company": {"id": self.company}, "accounts": {unused: {"id": unused, "kind": "cash", "code": "UNUSED", "name": "Unused"}}}
        self.pair("3", snapshot=snapshot)
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE accounts SET kind='general' WHERE id IN (?,?,?)", (foreign, unused, draft))
        self.assertEqual([row["id"] for row in self.reports.cash_accounts(self.actor, self.company)["accounts"]], [self.accounts["cash"]])
        for identifier in (foreign, unused, draft):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.reports.cash_book(self.actor, self.company, identifier)

    def test_bad_saved_metadata_cannot_prove_former_cash_or_hide_valid_current_cash(self):
        target = self.account("BAD", kind="general")
        good = {"id": target, "kind": "cash", "code": "BAD", "name": "Previous cash"}
        shapes = [None, {}, {"company": []}, {"company": {"id": self.other_company}, "accounts": {target: good}},
                  {"company": {"id": self.company}, "accounts": []},
                  {"company": {"id": self.company}, "accounts": {target: good | {"id": self.accounts["cash"]}}},
                  {"company": {"id": self.company}, "accounts": {target: good | {"kind": "bank"}}},
                  {"company": {"id": self.company}, "accounts": {target: good | {"code": " "}}}]
        for snapshot in shapes:
            self.pair("1", debit=target, snapshot=snapshot)
            self.pair("1", snapshot=snapshot)
        self.assertEqual([row["id"] for row in self.reports.cash_accounts(self.actor, self.company)["accounts"]], [self.accounts["cash"]])
        with self.assertRaises(ValueError):
            self.reports.cash_book(self.actor, self.company, target)
        self.assertEqual(self.reports.cash_book(self.actor, self.company, self.accounts["cash"])["closing"], len(shapes))
        valid_json = json.dumps({"company": {"id": self.company}, "accounts": {target: good}})
        for malformed in ("not JSON", "[]", "null", valid_json.replace('"kind": "cash"', '"kind": "bank", "kind": "cash"')):
            self.assertEqual(native_reports._saved_cash_accounts(malformed, self.company, [target]), set())

    def test_cash_snapshot_scan_parses_each_relevant_document_once(self):
        former = [self.account(f"FORMER{i}", kind="cash") for i in range(5)]
        self.document([(identifier, "1", "0") for identifier in former] + [(self.accounts["equity"], "0", "5")])
        with native_transaction(self.path) as connection:
            connection.executemany("UPDATE accounts SET kind='general' WHERE id=?", [(identifier,) for identifier in former])
        with patch.object(native_reports, "_saved_cash_accounts", wraps=native_reports._saved_cash_accounts) as inspect:
            choices = self.reports.cash_accounts(self.actor, self.company)
        self.assertEqual(inspect.call_count, 1)
        self.assertTrue(set(former).issubset({row["id"] for row in choices["accounts"]}))

    def test_report_and_selector_permissions_are_exact_live_and_company_scoped(self):
        user, reader = self.reader("trial_balance")
        self.reports.city_selectors(reader, self.company)
        self.reports.city_balances(reader, self.company)
        for call in (lambda: self.reports.cash_accounts(reader, self.company), lambda: self.reports.cash_book(reader, self.company, self.accounts["cash"]),
                     lambda: self.reports.city_balances(reader, self.other_company)):
            with self.assertRaises(AuthorizationError):
                call()
        self.security.set_permissions(self.actor, user.id, "trial_balance", [])
        self.security.set_permissions(self.actor, user.id, "counter_sales", ["read"])
        for call in (lambda: self.reports.city_selectors(reader, self.company), lambda: self.reports.city_balances(reader, self.company),
                     lambda: self.reports.cash_accounts(reader, self.company)):
            with self.assertRaises(AuthorizationError):
                call()
        self.security.set_permissions(self.actor, user.id, "ledger", ["read"])
        self.reports.cash_accounts(reader, self.company)
        self.reports.cash_book(reader, self.company, self.accounts["cash"])
        self.security.set_company_access(self.actor, user.id, [])
        with self.assertRaises(AuthorizationError):
            self.reports.cash_accounts(reader, self.company)
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.logout(reader)
        with self.assertRaises(AuthenticationError):
            self.reports.cash_book(reader, self.company, self.accounts["cash"])
        with patch.object(self.security, "clock", return_value=self.actor.expires_at + 1), self.assertRaises(AuthenticationError):
            self.reports.city_balances(self.actor, self.company)

    def test_strict_filters_missing_ids_and_export_defaults(self):
        invalid = [{"city_id": ""}, {"city_id": True}, {"city_id": []}, {"city_id": str(uuid4()).upper()},
                   {"city_id": str(uuid4())}, {"account_kind": "Cash"}, {"account_kind": []},
                   {"search": None}, {"search": "x" * 201}, {"search": "bad\x00"}, {"include_zero": 0},
                   {"date_from": "2026-02-30"}, {"date_from": "2026-02-02", "date_to": "2026-01-01"},
                   {"limit": True}, {"limit": 501}, {"offset": -1}, {"offset": 1.0}]
        for filters in invalid:
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                self.reports.city_balances(self.actor, self.company, **filters)
        for identifier in (None, True, "", str(uuid4()), str(uuid4()).upper()):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.reports.cash_book(self.actor, self.company, identifier)
        for report, filters in (("city_balances", {"offset": 1}), ("cash_book", {}), ("cash_book", {"account_id": self.accounts["cash"], "search": ""})):
            with self.assertRaises(ValueError):
                self.reports.export_snapshot(self.actor, self.company, report, **filters)
        snapshot = self.reports.export_snapshot(self.actor, self.company, "city_balances")
        self.assertEqual(snapshot["filters"], {"date_from": None, "date_to": None, "city_id": None, "account_kind": None, "search": "", "include_zero": True})
        self.assertEqual(snapshot["required_modules"], ("trial_balance",))
        self.assertEqual(snapshot["result"]["totals"]["account_count"], 0)

    def test_export_caps_refuse_during_gathering_and_queries_leave_business_history_unchanged(self):
        accounts = [self.account(f"A{i}") for i in range(8)]
        self.document([(identifier, "1", "0") for identifier in accounts] + [(self.accounts["cash"], "0", "8")])
        self.pair("1")
        self.pair("2")
        with native_session(self.path) as connection:
            before = {table: [tuple(row) for row in connection.execute("SELECT * FROM " + table + " ORDER BY rowid")]
                      for table in ("documents", "document_metadata", "journal_entries", "journal_lines", "audit_events")}
        checked = []
        original_limit = native_reports._row_limit
        def check(count, maximum):
            checked.append(count)
            original_limit(count, maximum)
        with patch.object(native_reports, "MAX_EXPORT_ROWS", 2), patch.object(native_reports, "_row_limit", side_effect=check):
            with self.assertRaisesRegex(ValueError, "exceeds 2 rows"):
                self.reports.export_snapshot(self.actor, self.company, "city_balances")
            self.assertEqual(checked, [1, 2, 3])
            with self.assertRaisesRegex(ValueError, "exceeds 2 rows"):
                self.reports.export_snapshot(self.actor, self.company, "cash_book", account_id=self.accounts["cash"])
        self.reports.city_selectors(self.actor, self.company)
        self.reports.city_balances(self.actor, self.company)
        self.reports.cash_accounts(self.actor, self.company)
        snapshot = self.reports.export_snapshot(self.actor, self.company, "cash_book", account_id=self.accounts["cash"])
        self.assertEqual(snapshot["required_modules"], ("ledger",))
        self.assertEqual(snapshot["result"]["total"], 3)
        with native_session(self.path) as connection:
            after = {table: [tuple(row) for row in connection.execute("SELECT * FROM " + table + " ORDER BY rowid")] for table in before}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
