"""Native authentication, session revocation, and authorization integration tests."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import unicodedata
import unittest
from uuid import uuid4

from bifma.native_schema import initialize_database, native_session, native_transaction, NativeSchemaError
from bifma.security import (
    AuthenticationError, AuthorizationError, BootstrapError, SecurityService,
    SecurityValidationError, Session, FAILED_ATTEMPT_LIMIT, LOCK_SECONDS,
    IDLE_SECONDS, SESSION_SECONDS,
    normalize_username,
)


ADMIN_PASSWORD = "Silver lanterns orbit quietly 72"
STAFF_PASSWORD = "Cobalt bridges welcome sunrise 83"
NEW_PASSWORD = "Amber orchards gather moonlight 94"


class Clock:
    def __init__(self):
        self.now = 1_800_000_000

    def __call__(self):
        return self.now


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "native.sqlite3"
        initialize_database(self.path)
        self.clock = Clock()
        self.security = SecurityService(self.path, clock=self.clock)
        self.admin = self.security.bootstrap_admin("Administrator", ADMIN_PASSWORD)
        self.company_a, self.company_b = str(uuid4()), str(uuid4())
        with native_transaction(self.path) as connection:
            connection.executemany("INSERT INTO companies(id,code,name) VALUES(?,?,?)", [
                (self.company_a, "A", "Synthetic company A"), (self.company_b, "B", "Synthetic company B")])

    def staff(self, *, username="operator", password=STAFF_PASSWORD):
        user = self.security.create_user(self.admin, username, password)
        actor = self.security.authenticate(username, password)
        return user, actor

    def test_access_editor_reads_current_grants_only_for_admin(self):
        user, actor = self.staff()
        self.security.set_company_access(self.admin, user.id, [self.company_a])
        self.security.set_permissions(self.admin, user.id, "sales", ["read", "create"])
        access = self.security.get_access(self.admin, user.id)
        self.assertEqual(access["company_ids"], [self.company_a])
        self.assertEqual(access["permissions"], {"sales": ["create", "read"]})
        self.assertNotIn("password", repr(access))
        self.assertEqual(self.security.permissions(actor, self.company_a), {"sales": ["create", "read"]})
        with self.assertRaises(AuthorizationError):
            self.security.permissions(actor, self.company_b)
        with self.assertRaises(AuthorizationError):
            self.security.get_access(actor, user.id)

    def test_no_default_account_and_single_bootstrap(self):
        self.assertEqual(self.admin.user.username, "administrator")
        with self.assertRaises(BootstrapError):
            self.security.bootstrap_admin("another", NEW_PASSWORD)
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_bootstrap_race_creates_exactly_one_administrator(self):
        target = self.path.parent / "racing.sqlite3"
        initialize_database(target)
        barrier = Barrier(2)

        def bootstrap(number):
            service = SecurityService(target, clock=self.clock)
            barrier.wait(timeout=10)
            try:
                service.bootstrap_admin(f"admin{number}", ADMIN_PASSWORD)
                return "created"
            except BootstrapError:
                return "already_created"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(bootstrap, (1, 2)))
        self.assertCountEqual(outcomes, ["created", "already_created"])
        with native_session(target) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users WHERE is_admin=1").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0], 1)

    def test_password_hash_parameters_salts_and_token_storage(self):
        self.security.create_user(self.admin, "second", ADMIN_PASSWORD)
        with native_session(self.path) as connection:
            hashes = [row[0] for row in connection.execute("SELECT password_hash FROM users ORDER BY username")]
            stored_token = connection.execute("SELECT token_hash FROM auth_sessions").fetchone()[0]
        self.assertNotEqual(hashes[0], hashes[1])
        for encoded in hashes:
            algorithm, rounds, salt, digest = encoded.split("$")
            self.assertEqual(algorithm, "pbkdf2_sha256")
            self.assertGreaterEqual(int(rounds), 600_000)
            self.assertEqual(len(bytes.fromhex(salt)), 32)
            self.assertEqual(bytes.fromhex(digest), hashlib.pbkdf2_hmac(
                "sha256", ADMIN_PASSWORD.encode("utf-8"), bytes.fromhex(salt), int(rounds)))
        self.assertEqual(stored_token, hashlib.sha256(self.admin.token.encode("ascii")).hexdigest())
        self.assertNotIn(self.admin.token, repr(self.admin))

    def test_username_normalization_unique_and_no_implicit_grants(self):
        user, actor = self.staff(username="  OpErAtOr  ")
        self.assertEqual(user.username, "operator")
        with self.assertRaises(SecurityValidationError):
            self.security.create_user(self.admin, "OPERATOR", NEW_PASSWORD)
        self.assertEqual(self.security.accessible_companies(actor), [])
        self.assertFalse(self.security.can(actor, self.company_a, "sales", "read"))

    def test_generic_failures_include_missing_disabled_and_wrong_password(self):
        user, actor = self.staff()
        messages = []
        for username, password in (("missing", STAFF_PASSWORD), ("operator", NEW_PASSWORD)):
            with self.assertRaises(AuthenticationError) as error:
                self.security.authenticate(username, password)
            messages.append(str(error.exception))
        self.security.set_active(self.admin, user.id, False)
        with self.assertRaises(AuthenticationError) as error:
            self.security.authenticate("operator", STAFF_PASSWORD)
        messages.append(str(error.exception))
        self.assertEqual(len(set(messages)), 1)
        with self.assertRaises(AuthenticationError):
            self.security.current_user(actor)

    def test_failed_attempt_lock_persists_and_expires_at_boundary(self):
        user, _ = self.staff()
        for _ in range(FAILED_ATTEMPT_LIMIT):
            with self.assertRaises(AuthenticationError):
                self.security.authenticate("operator", NEW_PASSWORD)
        second_service = SecurityService(self.path, clock=self.clock)
        with native_session(self.path) as connection:
            row = connection.execute("SELECT failed_attempts,locked_until FROM users WHERE id=?", (user.id,)).fetchone()
            self.assertEqual(tuple(row), (FAILED_ATTEMPT_LIMIT, self.clock.now + LOCK_SECONDS))
        self.clock.now += LOCK_SECONDS - 1
        with self.assertRaises(AuthenticationError):
            second_service.authenticate("operator", STAFF_PASSWORD)
        self.clock.now += 1
        second_service.authenticate("operator", STAFF_PASSWORD)
        with native_session(self.path) as connection:
            self.assertEqual(tuple(connection.execute("SELECT failed_attempts,locked_until FROM users WHERE id=?", (user.id,)).fetchone()), (0, None))

    def test_expired_lock_wrong_password_starts_new_attempt_window(self):
        user, _ = self.staff()
        for _ in range(FAILED_ATTEMPT_LIMIT):
            with self.assertRaises(AuthenticationError):
                self.security.authenticate("operator", NEW_PASSWORD)
        self.clock.now += LOCK_SECONDS
        with self.assertRaises(AuthenticationError):
            self.security.authenticate("operator", NEW_PASSWORD)
        with native_session(self.path) as connection:
            self.assertEqual(tuple(connection.execute("SELECT failed_attempts,locked_until FROM users WHERE id=?", (user.id,)).fetchone()), (1, None))

    def test_company_and_module_permissions_are_both_required(self):
        user, actor = self.staff()
        self.security.set_permissions(self.admin, user.id, "sales", {"read", "create"})
        self.assertFalse(self.security.can(actor, self.company_a, "sales", "read"))
        self.security.set_company_access(self.admin, user.id, [self.company_a])
        for action in ("read", "create"):
            self.assertTrue(self.security.can(actor, self.company_a, "sales", action))
        for action in ("update", "delete", "post"):
            self.assertFalse(self.security.can(actor, self.company_a, "sales", action))
        self.assertFalse(self.security.can(actor, self.company_b, "sales", "read"))
        self.assertFalse(self.security.can(actor, self.company_a, "purchases", "read"))
        self.assertFalse(self.security.can(actor, self.company_a, "invented_module", "read"))
        self.assertEqual(self.security.accessible_companies(actor), [(self.company_a, "Synthetic company A")])

    def test_permission_revocation_applies_to_existing_session(self):
        user, actor = self.staff()
        self.security.set_company_access(self.admin, user.id, [self.company_a])
        self.security.set_permissions(self.admin, user.id, "sales", {"read", "post"})
        self.assertTrue(self.security.can(actor, self.company_a, "sales", "post"))
        self.security.set_permissions(self.admin, user.id, "sales", {"read"})
        self.assertFalse(self.security.can(actor, self.company_a, "sales", "post"))
        self.security.set_company_access(self.admin, user.id, [])
        self.assertFalse(self.security.can(actor, self.company_a, "sales", "read"))

    def test_existing_inactive_company_assignment_can_be_preserved_during_other_access_edits(self):
        user, actor = self.staff()
        self.security.set_company_access(self.admin, user.id, [self.company_a])
        self.security.set_permissions(self.admin, user.id, "sales", ["read"])
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET is_active=0 WHERE id=?", (self.company_a,))
        self.security.set_company_access(self.admin, user.id, [self.company_a, self.company_b])
        self.assertEqual(set(self.security.get_access(self.admin, user.id)["company_ids"]), {self.company_a, self.company_b})
        self.assertFalse(self.security.can(actor, self.company_a, "sales", "read"))
        self.assertTrue(self.security.can(actor, self.company_b, "sales", "read"))
        self.assertEqual(self.security.accessible_companies(actor), [(self.company_b, "Synthetic company B")])
        with native_session(self.path) as connection:
            details = connection.execute("SELECT details_json FROM audit_events WHERE action='security.company_access_changed' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        self.assertEqual(json.loads(details), {"company_count": 2})

    def test_new_inactive_company_assignment_is_refused_without_replacing_existing_grants(self):
        user, _ = self.staff()
        self.security.set_company_access(self.admin, user.id, [self.company_a])
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET is_active=0 WHERE id=?", (self.company_b,))
            audit_count = connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='security.company_access_changed'").fetchone()[0]
        with self.assertRaises(SecurityValidationError):
            self.security.set_company_access(self.admin, user.id, [self.company_a, self.company_b])
        self.assertEqual(self.security.get_access(self.admin, user.id)["company_ids"], [self.company_a])
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='security.company_access_changed'").fetchone()[0], audit_count)

    def test_removed_inactive_assignment_cannot_be_restored_by_stale_selection_or_other_user(self):
        user, _ = self.staff()
        other, _ = self.staff(username="other_operator")
        self.security.set_company_access(self.admin, user.id, [self.company_a])
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET is_active=0 WHERE id=?", (self.company_a,))
        with self.assertRaises(SecurityValidationError):
            self.security.set_company_access(self.admin, other.id, [self.company_a])
        self.security.set_company_access(self.admin, user.id, [])
        with self.assertRaises(SecurityValidationError):
            self.security.set_company_access(self.admin, user.id, [self.company_a])
        self.assertEqual(self.security.get_access(self.admin, user.id)["company_ids"], [])
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET is_active=1 WHERE id=?", (self.company_a,))
        self.security.set_company_access(self.admin, user.id, [self.company_a])
        self.assertEqual(self.security.get_access(self.admin, user.id)["company_ids"], [self.company_a])

    def test_forged_user_metadata_does_not_authorize(self):
        _, actor = self.staff()
        forged = Session(replace(actor.user, id=self.admin.user.id, is_admin=True), actor.token, actor.expires_at)
        with self.assertRaises(AuthorizationError):
            self.security.require_admin(forged)
        for fake in (self.admin.user, self.admin.user.id, "a" * 43):
            with self.subTest(kind=type(fake).__name__):
                with self.assertRaises(AuthenticationError):
                    self.security.require_admin(fake)

    def test_authorization_inside_posting_transaction_rolls_back_denied_work(self):
        _, actor = self.staff()
        with self.assertRaises(AuthorizationError):
            with native_transaction(self.path) as connection:
                connection.execute("UPDATE companies SET name='Should roll back' WHERE id=?", (self.company_a,))
                self.security.require(actor, self.company_a, "sales", "post", connection=connection)
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT name FROM companies WHERE id=?", (self.company_a,)).fetchone()[0], "Synthetic company A")

    def test_supplied_authorization_connection_requires_transaction(self):
        with native_session(self.path, readonly=False) as connection:
            with self.assertRaises(SecurityValidationError):
                self.security.require(self.admin, self.company_a, "sales", "post", connection=connection)

    def test_admin_cannot_access_inactive_or_nonexistent_company(self):
        self.assertTrue(self.security.can(self.admin, self.company_a, "sales", "post"))
        self.assertTrue(self.security.can(self.admin, None, "companies", "create"))
        self.assertFalse(self.security.can(self.admin, None, "sales", "post"))
        self.assertFalse(self.security.can(self.admin, str(uuid4()), "sales", "read"))
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET is_active=0 WHERE id=?", (self.company_a,))
        self.assertFalse(self.security.can(self.admin, self.company_a, "sales", "read"))

    def test_password_change_verifies_current_and_revokes_other_sessions(self):
        user, actor = self.staff()
        other = self.security.authenticate("operator", STAFF_PASSWORD)
        with self.assertRaises(AuthenticationError):
            self.security.change_password(actor, NEW_PASSWORD, ADMIN_PASSWORD)
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT failed_attempts FROM users WHERE id=?", (user.id,)).fetchone()[0], 1)
        replacement = self.security.change_password(actor, STAFF_PASSWORD, NEW_PASSWORD)
        for previous in (actor, other):
            with self.assertRaises(AuthenticationError):
                self.security.current_user(previous)
        self.assertEqual(self.security.current_user(replacement).id, user.id)
        with self.assertRaises(AuthenticationError):
            self.security.authenticate("operator", STAFF_PASSWORD)
        self.assertEqual(self.security.authenticate("operator", NEW_PASSWORD).user.id, user.id)

    def test_malformed_current_password_is_never_a_valid_placeholder(self):
        _, actor = self.staff(password="invalid-authentication-input")
        with self.assertRaises(AuthenticationError):
            self.security.change_password(actor, None, NEW_PASSWORD)

    def test_admin_reset_is_explicit_and_revokes_target_sessions(self):
        user, actor = self.staff()
        with self.assertRaises(AuthorizationError):
            self.security.reset_password(actor, self.admin.user.id, NEW_PASSWORD)
        self.security.reset_password(self.admin, user.id, NEW_PASSWORD)
        with self.assertRaises(AuthenticationError):
            self.security.current_user(actor)
        self.assertEqual(self.security.authenticate("operator", NEW_PASSWORD).user.id, user.id)

    def test_disable_and_reenable_does_not_revive_old_sessions(self):
        user, actor = self.staff()
        self.security.set_active(self.admin, user.id, False)
        self.security.set_active(self.admin, user.id, True)
        with self.assertRaises(AuthenticationError):
            self.security.current_user(actor)
        self.security.authenticate("operator", STAFF_PASSWORD)

    def test_last_active_admin_is_preserved(self):
        for operation in (self.security.set_active, self.security.set_admin):
            with self.assertRaises(SecurityValidationError):
                operation(self.admin, self.admin.user.id, False)
        self.security.create_user(self.admin, "backup_admin", STAFF_PASSWORD, is_admin=True)
        self.security.set_admin(self.admin, self.admin.user.id, False)
        with self.assertRaises(AuthenticationError):
            self.security.current_user(self.admin)

    def test_idle_and_absolute_expiration_and_logout(self):
        user, actor = self.staff()
        self.clock.now += IDLE_SECONDS
        with self.assertRaises(AuthenticationError):
            self.security.current_user(actor)
        actor = self.security.authenticate("operator", STAFF_PASSWORD)
        self.clock.now = actor.expires_at - 1
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE auth_sessions SET last_seen_at=? WHERE token_hash=?", (
                self.clock.now, hashlib.sha256(actor.token.encode("ascii")).hexdigest()))
        self.security.current_user(actor)
        self.clock.now += 1
        with self.assertRaises(AuthenticationError):
            self.security.current_user(actor)
        actor = self.security.authenticate("operator", STAFF_PASSWORD)
        self.security.logout(actor)
        with self.assertRaises(AuthenticationError):
            self.security.current_user(actor)

    def test_normal_use_refreshes_idle_clock(self):
        _, actor = self.staff()
        self.clock.now += IDLE_SECONDS - 1
        self.security.current_user(actor)
        self.clock.now += IDLE_SECONDS - 1
        self.security.current_user(actor)

    def test_password_policy_length_blocklist_spaces_and_unicode_nfc(self):
        for password in ("", "passwordpassword", "x" * 1025, " " * 20, " \t\n ", "xx"):
            with self.subTest(kind=len(password)):
                with self.assertRaises(SecurityValidationError):
                    self.security.create_user(self.admin, "rejected", password)
        password = "Café lanterns welcome sunrise 83"
        user = self.security.create_user(self.admin, "unicode_user", password)
        self.assertEqual(self.security.authenticate("unicode_user", unicodedata.normalize("NFD", password)).user.id, user.id)

    def test_short_credentials_work_for_bootstrap_create_change_and_reset(self):
        target = self.path.parent / "short_credentials.sqlite3"
        initialize_database(target)
        security = SecurityService(target, clock=self.clock)
        owner = security.bootstrap_admin(" A ", "7", display_name="Short login owner")
        self.assertEqual(owner.user.username, "a")
        self.assertEqual(security.authenticate("Ａ", "7").user.id, owner.user.id)
        user = security.create_user(owner, "B", "é", display_name="Short login staff")
        staff = security.authenticate("b", "e\u0301")
        self.assertEqual(staff.user.id, user.id)
        replacement = security.change_password(staff, "é", "9")
        with self.assertRaises(AuthenticationError):
            security.current_user(staff)
        self.assertEqual(security.authenticate("b", "9").user.id, user.id)
        security.reset_password(owner, user.id, "tiny")
        with self.assertRaises(AuthenticationError):
            security.current_user(replacement)
        self.assertEqual(security.authenticate("B", "tiny").user.id, user.id)

    def test_long_credentials_are_not_truncated_at_fifteen_characters(self):
        username, password = "a" * 79 + "b", "Ab" * 512
        user = self.security.create_user(self.admin, username, password)
        self.assertEqual(user.username, username)
        self.assertEqual(self.security.authenticate(username, password).user.id, user.id)
        for entered_user, entered_password in ((username[:15], password), (username, password[:15])):
            with self.assertRaises(AuthenticationError):
                self.security.authenticate(entered_user, entered_password)

    def test_blank_credentials_cannot_bootstrap_change_or_reset_password(self):
        target = self.path.parent / "blank_credentials.sqlite3"
        initialize_database(target)
        security = SecurityService(target, clock=self.clock)
        for username, password in (("", "7"), (" \t", "7"), ("a", ""), ("a", " \t ")):
            with self.subTest(username=username, password=password):
                with self.assertRaises(SecurityValidationError):
                    security.bootstrap_admin(username, password)
        with native_session(target) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
        user, actor = self.staff()
        for password in ("", " \t "):
            with self.subTest(password=password):
                with self.assertRaises(SecurityValidationError):
                    self.security.change_password(actor, STAFF_PASSWORD, password)
                with self.assertRaises(SecurityValidationError):
                    self.security.reset_password(self.admin, user.id, password)
        self.assertEqual(self.security.current_user(actor).id, user.id)
        self.assertEqual(self.security.authenticate(user.username, STAFF_PASSWORD).user.id, user.id)

    def test_existing_password_hash_is_not_rewritten_by_lookup_or_authentication(self):
        with native_session(self.path) as connection:
            original = connection.execute("SELECT password_hash FROM users WHERE id=?", (self.admin.user.id,)).fetchone()[0]
        reopened = SecurityService(self.path, clock=self.clock)
        self.assertEqual(reopened.login_profile("ADMINISTRATOR")["display_name"], "administrator")
        self.assertEqual(reopened.authenticate("ADMINISTRATOR", ADMIN_PASSWORD).user.id, self.admin.user.id)
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT password_hash FROM users WHERE id=?", (self.admin.user.id,)).fetchone()[0], original)

    def test_login_profile_only_returns_active_metadata_without_authentication_or_writes(self):
        user = self.security.create_user(self.admin, "lookup_user", STAFF_PASSWORD, display_name="Saved Full Name")
        self.security.set_company_access(self.admin, user.id, [self.company_a])
        for _ in range(FAILED_ATTEMPT_LIMIT):
            with self.assertRaises(AuthenticationError):
                self.security.authenticate("lookup_user", NEW_PASSWORD)

        def state():
            with native_session(self.path) as connection:
                return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                        for table in ("users", "auth_sessions", "audit_events")}

        before = state()
        expected = {"display_name": "Saved Full Name", "companies": [(self.company_a, "Synthetic company A")]}
        self.assertEqual(self.security.login_profile("  LOOKUP_USER "), expected)
        for username in (None, "", " ", "missing", "lookup", "lookup_user' OR 1=1", "a" * 1025):
            with self.subTest(username=username):
                self.assertIsNone(self.security.login_profile(username))
        self.assertEqual(state(), before)
        with self.assertRaises(AuthenticationError):
            self.security.current_user(self.security.login_profile("lookup_user"))
        self.security.set_active(self.admin, user.id, False)
        self.assertIsNone(self.security.login_profile("lookup_user"))
        self.security.set_active(self.admin, user.id, True)
        self.assertEqual(self.security.login_profile("lookup_user"), expected)

    def test_login_profile_company_choices_follow_current_grants_and_active_companies(self):
        user, actor = self.staff()
        self.assertEqual(self.security.login_profile("operator")["companies"], [])
        self.assertEqual(self.security.login_profile("administrator")["companies"],
                         self.security.accessible_companies(self.admin))
        self.security.set_company_access(self.admin, user.id, [self.company_a, self.company_b])
        self.assertEqual(self.security.login_profile("operator")["companies"], self.security.accessible_companies(actor))
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET is_active=0 WHERE id=?", (self.company_a,))
        for username, session in (("operator", actor), ("administrator", self.admin)):
            self.assertEqual(self.security.login_profile(username)["companies"], [(self.company_b, "Synthetic company B")])
            self.assertEqual(self.security.login_profile(username)["companies"], self.security.accessible_companies(session))
        self.security.set_company_access(self.admin, user.id, [])
        self.assertEqual(self.security.login_profile("operator")["companies"], [])

    def test_audit_is_useful_without_passwords_hashes_or_tokens(self):
        user, actor = self.staff()
        self.security.set_company_access(self.admin, user.id, [self.company_a])
        self.security.set_permissions(self.admin, user.id, "sales", {"read"})
        with self.assertRaises(AuthenticationError):
            self.security.authenticate("operator", NEW_PASSWORD)
        self.security.reset_password(self.admin, user.id, NEW_PASSWORD)
        with native_session(self.path) as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM audit_events")]
        actions = {row["action"] for row in rows}
        self.assertTrue({"security.bootstrap", "security.user_created", "security.authenticated",
                         "security.company_access_changed", "security.permissions_changed",
                         "security.authentication_failed", "security.password_reset"} <= actions)
        encoded = json.dumps(rows)
        for secret in (ADMIN_PASSWORD, STAFF_PASSWORD, NEW_PASSWORD, self.admin.token, actor.token, "pbkdf2_sha256"):
            self.assertNotIn(secret, encoded)

    def test_no_user_admin_methods_are_available_to_regular_sessions(self):
        user, actor = self.staff()
        attempts = [
            lambda: self.security.create_user(actor, "intruder", NEW_PASSWORD),
            lambda: self.security.set_company_access(actor, user.id, [self.company_b]),
            lambda: self.security.set_permissions(actor, user.id, "sales", {"post"}),
            lambda: self.security.set_active(actor, self.admin.user.id, False),
            lambda: self.security.set_admin(actor, user.id, True),
            lambda: self.security.list_users(actor),
        ]
        for attempt in attempts:
            with self.assertRaises(AuthorizationError):
                attempt()

    def test_username_boundaries_fail_with_validation_before_sql(self):
        for username in ("", " ", "a" * 81, "operator@example", "user name", "نام"):
            with self.subTest(length=len(username)):
                with self.assertRaises(SecurityValidationError):
                    self.security.create_user(self.admin, username, STAFF_PASSWORD)
        self.assertEqual(normalize_username("ABC"), "abc")
        self.assertEqual(normalize_username("A"), "a")
        self.assertEqual(normalize_username("AB"), "ab")
        self.assertEqual(normalize_username("A" * 80), "a" * 80)
        self.assertEqual(normalize_username("_ab"), "_ab")

    def test_service_constructor_never_creates_or_upgrades_an_archive(self):
        missing = self.path.parent / "missing.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            SecurityService(missing)
        self.assertFalse(missing.exists())
        archive = self.path.parent / "synthetic_archive.sqlite3"
        with closing(sqlite3.connect(archive)) as connection, connection:
            connection.executescript("CREATE TABLE _recovery_metadata(key TEXT,value TEXT); "
                                     "CREATE TABLE UM_USERS(USER_ID TEXT,PASSWORD TEXT);")
            connection.execute("INSERT INTO UM_USERS VALUES('legacy','synthetic legacy secret')")
        before = archive.read_bytes()
        with self.assertRaises(NativeSchemaError):
            SecurityService(archive)
        self.assertEqual(archive.read_bytes(), before)

    def test_every_supported_action_can_be_granted_independently(self):
        user, actor = self.staff()
        self.security.set_company_access(self.admin, user.id, [self.company_a])
        for granted in ("read", "create", "update", "delete", "post"):
            self.security.set_permissions(self.admin, user.id, "products", {granted})
            for requested in ("read", "create", "update", "delete", "post"):
                self.assertEqual(self.security.can(actor, self.company_a, "products", requested), granted == requested)


if __name__ == "__main__":
    unittest.main()
