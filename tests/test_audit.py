"""Audit browsing uses temporary native databases and synthetic events only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from bifma.audit import AuditError, AuditService
from bifma.masters import MasterService
from bifma.native_schema import initialize_database, native_session, native_transaction
from bifma.security import AuthenticationError, AuthorizationError, SecurityService
from bifma.transactions import TransactionService


def seconds(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


class AuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "native.sqlite3"
        initialize_database(self.path)
        self.now = seconds("2026-10-02T12:00:00")
        self.clock = lambda: self.now
        self.security = SecurityService(self.path, clock=self.clock)
        self.admin = self.security.bootstrap_admin("owner", "Silver lanterns orbit quietly 72", display_name="Administrator name")
        self.audit = AuditService(self.path, self.security)
        self.company, self.inactive = str(uuid4()), str(uuid4())
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO companies(id,code,name,name_urdu,fiscal_start,fiscal_end) VALUES (?,'A','Company A','بفما ٹریڈرز','2026-01-01','2026-12-31')", (self.company,))
            connection.execute("INSERT INTO companies(id,code,name,is_active) VALUES (?,'B','Inactive company',0)", (self.inactive,))

    def event(self, *, action="synthetic.event", company=None, actor=..., entity="database", identifier=None, when=None, details=None, event_id=None):
        event_id = event_id or str(uuid4())
        actor = self.admin.user.id if actor is ... else actor
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO audit_events(id,actor_id,company_id,action,entity_type,entity_id,occurred_at,details_json) VALUES (?,?,?,?,?,?,?,?)",
                               (event_id, actor, company, action, entity, identifier, self.now if when is None else when, json.dumps(details or {}, ensure_ascii=False)))
        return event_id

    def test_all_companies_includes_global_and_company_filter_excludes_global(self):
        global_id = self.event()
        active_id = self.event(company=self.company)
        inactive_id = self.event(company=self.inactive)
        all_events = self.audit.list_events(self.admin, action="synthetic.event")
        self.assertEqual({row["id"] for row in all_events["rows"]}, {global_id, active_id, inactive_id})
        selected = self.audit.list_events(self.admin, company_id=self.company, action="synthetic.event")
        self.assertEqual([row["id"] for row in selected["rows"]], [active_id])
        inactive = self.audit.list_events(self.admin, company_id=self.inactive, action="synthetic.event")
        self.assertEqual(inactive["rows"][0]["id"], inactive_id)
        self.assertEqual(inactive["rows"][0]["company_is_active"], 0)
        self.assertEqual(next(row for row in all_events["rows"] if row["id"] == global_id)["company_label"], "Global")

    def test_utc_dates_are_inclusive_midnight_to_last_second_and_no_local_shift(self):
        before = self.event(when=seconds("2026-10-01T23:59:59"))
        first = self.event(when=seconds("2026-10-02T00:00:00"))
        last = self.event(when=seconds("2026-10-02T23:59:59"))
        after = self.event(when=seconds("2026-10-03T00:00:00"))
        result = self.audit.list_events(self.admin, action="synthetic.event", date_from="2026-10-02", date_to="2026-10-02")
        self.assertEqual([row["id"] for row in result["rows"]], [last, first])
        self.assertEqual(result["rows"][1]["occurred_at_utc"], "2026-10-02 00:00:00 UTC")
        self.assertEqual(self.audit.list_events(self.admin, action="synthetic.event", date_to="2026-10-01")["rows"][0]["id"], before)
        self.assertEqual(self.audit.list_events(self.admin, action="synthetic.event", date_from="2026-10-03")["rows"][0]["id"], after)
        self.assertEqual(self.audit.list_events(self.admin, action="synthetic.event", date_from="0001-01-01", date_to="9999-12-31")["total"], 4)

    def test_pagination_is_newest_then_reverse_insertion_order_for_timestamp_ties(self):
        first = self.event(event_id="ffffffff-ffff-ffff-ffff-ffffffffffff", when=self.now + 1)
        second = self.event(event_id="00000000-0000-0000-0000-000000000001", when=self.now + 1)
        newest = self.event(when=self.now + 2)
        older = self.event(when=self.now)
        ids = []
        for offset in range(4):
            page = self.audit.list_events(self.admin, action="synthetic.event", offset=offset, limit=1)
            self.assertEqual((page["total"], page["offset"], page["limit"]), (4, offset, 1))
            ids.append(page["rows"][0]["id"])
        self.assertEqual(ids, [newest, second, first, older])
        empty = self.audit.list_events(self.admin, action="synthetic.event", offset=100)
        self.assertEqual((empty["rows"], empty["total"]), ([], 4))

    def test_action_is_exact_and_search_is_literal_over_ids_action_and_current_actor(self):
        selected = self.event(action="synthetic.100%_done", entity="example", identifier="ID-'quote-%_\\")
        self.event(action="synthetic.100XXdone", entity="example", identifier="another")
        self.assertEqual(self.audit.list_events(self.admin, action="synthetic.100%")["total"], 0)
        exact = self.audit.list_events(self.admin, action="synthetic.100%_done")
        self.assertEqual([row["id"] for row in exact["rows"]], [selected])
        for search in ("%_", "'quote", "ID-'", "100%_"):
            result = self.audit.list_events(self.admin, search=search)
            self.assertEqual([row["id"] for row in result["rows"]], [selected])
        self.assertGreaterEqual(self.audit.list_events(self.admin, search="ADMINISTRATOR NAME")["total"], 2)
        self.assertGreaterEqual(self.audit.list_events(self.admin, search="owner")["total"], 2)
        self.assertEqual(self.audit.list_events(self.admin, search="' OR 1=1 --")["total"], 0)

    def test_selectors_are_exact_existing_actions_and_include_inactive_companies(self):
        self.event(action="z.custom")
        self.event(action="z.custom")
        self.event(action="a.custom")
        choices = self.audit.selectors(self.admin)
        self.assertEqual([row["id"] for row in choices["companies"]], [self.company, self.inactive])
        self.assertEqual(choices["companies"][1]["is_active"], 0)
        self.assertEqual(choices["actions"], sorted(set(choices["actions"])))
        self.assertIn("z.custom", choices["actions"])
        self.assertIn("security.bootstrap", choices["actions"])

    def test_real_master_document_and_user_subjects_resolve_current_labels(self):
        masters = MasterService(self.path, self.security, clock=self.clock)
        debit = masters.create(self.admin, "accounts", {"code": "DR", "name": "Original account"})
        credit = masters.create(self.admin, "accounts", {"code": "CR", "name": "Counter account"})
        user = self.security.create_user(self.admin, "operator", "Cobalt bridges welcome sunrise 83", display_name="Operator name")
        transaction = TransactionService(self.path, self.security, self.clock)
        draft = transaction.save_draft(self.admin, self.company, "journal", {
            "document_number": "J-1", "business_date": "2026-10-02", "rounding_policy": "cents", "posting_places": 2,
            "lines": [{"account_id": debit["id"], "debit": "1", "credit": "0"}, {"account_id": credit["id"], "debit": "0", "credit": "1"}]})
        transaction.post(self.admin, self.company, draft["id"], draft["revision"])
        masters.update(self.admin, "accounts", debit["id"], {"name": "Current account"}, debit["version"])
        rows = self.audit.list_events(self.admin)["rows"]
        account_event = next(row for row in rows if row["action"] == "masters.created" and row["entity_id"] == debit["id"])
        self.assertEqual((account_event["subject_label"], account_event["subject_label_source"]), ("DR · Current account", "current"))
        document_event = next(row for row in rows if row["action"] == "transaction.posted")
        self.assertEqual(document_event["subject_label"], "J-1 · journal")
        user_event = next(row for row in rows if row["action"] == "security.user_created" and row["entity_id"] == user.id)
        self.assertEqual(user_event["subject_label"], "operator · Operator name")
        self.assertIn("name", account_event["details"]["changed_fields"])

    def test_membership_subject_null_actor_and_unresolved_identifiers_have_clear_fallbacks(self):
        store = str(uuid4())
        with native_transaction(self.path) as connection:
            connection.execute("INSERT INTO stores(id,code,name) VALUES (?,'STORE','Store name')", (store,))
            connection.execute("INSERT INTO company_stores(company_id,store_id) VALUES (?,?)", (self.company, store))
        membership = self.event(company=self.company, entity="company_stores", identifier=self.company + ":" + store)
        anonymous = self.event(actor=None)
        missing = self.event(entity="accounts", identifier=str(uuid4()))
        unusual = self.event(entity="accounts; DROP TABLE users; --", identifier="arbitrary")
        rows = {row["id"]: row for row in self.audit.list_events(self.admin, action="synthetic.event")["rows"]}
        self.assertEqual(rows[membership]["subject_label"], "A / STORE · Store name")
        self.assertEqual((rows[anonymous]["actor_id"], rows[anonymous]["actor_label"], rows[anonymous]["actor_label_source"]), (None, "System / unknown", "context"))
        self.assertEqual(rows[anonymous]["subject_label"], "Native database")
        self.assertEqual(rows[missing]["subject_label_source"], "identifier")
        self.assertIn("DROP TABLE", rows[unusual]["subject_label"])
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_current_actor_company_labels_and_raw_details_are_not_claimed_as_snapshots(self):
        marker = self.path.parent / "must-not-exist"
        text = f"__import__('pathlib').Path({str(marker)!r}).touch() <script>alert('x')</script>"
        identifier = self.event(company=self.company, details={"reason": text, "nested": {"count": 3, "flag": False}})
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE users SET display_name='Current admin' WHERE id=?", (self.admin.user.id,))
            connection.execute("UPDATE companies SET name='Current company' WHERE id=?", (self.company,))
            raw = connection.execute("SELECT details_json FROM audit_events WHERE id=?", (identifier,)).fetchone()[0]
        row = self.audit.list_events(self.admin, action="synthetic.event")["rows"][0]
        self.assertEqual((row["actor_name"], row["company_name"]), ("Current admin", "Current company"))
        self.assertEqual((row["actor_label_source"], row["company_label_source"]), ("current", "current"))
        self.assertEqual(row["details_json"], raw)
        self.assertEqual(row["details"], json.loads(raw))
        self.assertFalse(marker.exists())
        self.assertNotIn("password_hash", row)
        self.assertNotIn("token_hash", row)
        self.assertNotIn(self.admin.token, repr(row))

    def test_ordinary_module_grants_never_grant_audit_access(self):
        user = self.security.create_user(self.admin, "reader", "Cobalt bridges welcome sunrise 83")
        actor = self.security.authenticate("reader", "Cobalt bridges welcome sunrise 83")
        self.security.set_company_access(self.admin, user.id, [self.company])
        for module in ("dashboard", "stock", "ledger", "sales"):
            self.security.set_permissions(self.admin, user.id, module, ["read"])
        with self.assertRaises(AuthorizationError):
            self.audit.list_events(actor, company_id=self.company)
        with self.assertRaises(AuthorizationError):
            self.audit.selectors(actor)

    def test_admin_and_session_revocation_are_checked_on_every_call(self):
        second = self.security.create_user(self.admin, "another_admin", "Cobalt bridges welcome sunrise 83", is_admin=True)
        other = self.security.authenticate("another_admin", "Cobalt bridges welcome sunrise 83")
        self.audit.list_events(other)
        self.security.set_admin(self.admin, second.id, False)
        with self.assertRaises(AuthenticationError):
            self.audit.list_events(other)
        fresh_nonadmin = self.security.authenticate("another_admin", "Cobalt bridges welcome sunrise 83")
        with self.assertRaises(AuthorizationError):
            self.audit.list_events(fresh_nonadmin)
        self.security.logout(self.admin)
        with self.assertRaises(AuthenticationError):
            self.audit.selectors(self.admin)

    def test_expired_session_is_refused(self):
        self.now = self.admin.expires_at
        with self.assertRaises(AuthenticationError):
            self.audit.list_events(self.admin)

    def test_filters_are_bounded_and_missing_company_is_rejected(self):
        invalid = [{"company_id": "missing"}, {"company_id": []}, {"company_id": str(uuid4())},
                   {"action": None}, {"action": "x" * 201}, {"action": "bad\x00text"}, {"search": []}, {"search": "x" * 201},
                   {"date_from": ""}, {"date_from": True}, {"date_to": "2026-02-30"}, {"date_to": "2026-10-01T01:00:00Z"},
                   {"date_from": "2026-10-03", "date_to": "2026-10-02"}, {"limit": True}, {"limit": 0}, {"limit": 501},
                   {"offset": True}, {"offset": -1}, {"offset": 2**63}, {"offset": 0.0}]
        for filters in invalid:
            with self.subTest(filters=filters), self.assertRaises(AuditError):
                self.audit.list_events(self.admin, **filters)

    def test_extreme_timestamp_uses_explicit_fallback_without_platform_failure(self):
        self.event(when=2**63 - 1)
        row = self.audit.list_events(self.admin, action="synthetic.event")["rows"][0]
        self.assertEqual(row["occurred_at"], 2**63 - 1)
        self.assertIn("UTC date unavailable", row["occurred_at_utc"])

    def test_count_rows_and_current_labels_share_the_authorized_transaction(self):
        identifier = self.event(company=self.company)
        original_require = self.security.require_admin
        entered, complete = Event(), Event()
        executor = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(executor.shutdown)
        pending = []
        def writer():
            entered.set()
            with native_transaction(self.path) as connection:
                connection.execute("UPDATE companies SET name='After snapshot' WHERE id=?", (self.company,))
            self.event(company=self.company)
            complete.set()
        def require(actor, *, connection):
            current = original_require(actor, connection=connection)
            self.assertTrue(connection.in_transaction)
            pending.append(executor.submit(writer))
            self.assertTrue(entered.wait(2))
            self.assertFalse(complete.is_set())
            return current
        with patch.object(self.security, "require_admin", side_effect=require):
            result = self.audit.list_events(self.admin, company_id=self.company, action="synthetic.event")
        pending[0].result(timeout=10)
        self.assertEqual((result["total"], len(result["rows"]), result["rows"][0]["id"]), (1, 1, identifier))
        self.assertEqual(result["rows"][0]["company_name"], "Company A")

    def test_browsing_changes_only_normal_session_heartbeat(self):
        self.event(company=self.company)
        def state():
            with native_session(self.path) as connection:
                tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                return {table: [tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')] for table in tables}
        before = state()
        self.now += 1
        self.audit.list_events(self.admin)
        self.audit.selectors(self.admin)
        after = state()
        before_sessions, after_sessions = before.pop("auth_sessions"), after.pop("auth_sessions")
        self.assertEqual(after, before)
        self.assertNotEqual(before_sessions, after_sessions)
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT last_seen_at FROM auth_sessions").fetchone()[0], self.now)


if __name__ == "__main__":
    unittest.main()
