"""Native authentication and authorization, independent of recovered credentials.

PBKDF2-HMAC-SHA256 uses OWASP's 600,000-iteration recommendation (reviewed
2026-10-02): https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
Passwords use NFC Unicode, no composition rules, and a local denylist. The
owner's 2026-10-04 instruction permits short nonempty credentials; upper bounds
remain in place. This module does not claim a NIST assurance level or protect
files from an OS administrator.

Only opaque session tokens authorize requests. A User value is display metadata,
never proof of authentication. Pass the writable native posting transaction to
require(..., connection=connection) to check current rights under its write lock.
There are no default accounts, imported legacy passwords, or password CLI flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
import unicodedata
from uuid import uuid4

from bifma.native_schema import native_session, native_transaction, open_native_database, require_native


PBKDF2_ITERATIONS = 600_000
SESSION_SECONDS = 12 * 60 * 60
IDLE_SECONDS = 30 * 60
FAILED_ATTEMPT_LIMIT = 5
LOCK_SECONDS = 5 * 60
ACTIONS = frozenset({"read", "create", "update", "delete", "post"})
MODULES = frozenset({
    "dashboard", "companies", "periods", "products", "product_groups", "accounts", "cities",
    "stores", "units", "sales", "counter_sales", "purchases", "sales_returns", "purchase_returns", "journals",
    "breakage_receive", "breakage_dispatch", "delivery_challans", "stock", "stock_transfers",
    "payments", "receipts", "ledger", "trial_balance", "reports", "users", "settings", "backup", "audit",
})
GLOBAL_MODULES = frozenset({"companies", "users", "settings", "backup", "audit"})
_BAD_CREDENTIALS = "Unable to sign in with those credentials."
_DUMMY_HASH = f"pbkdf2_sha256${PBKDF2_ITERATIONS}$" + "00" * 32 + "$" + "00" * 32
_COMMON_PASSWORDS = frozenset({
    "passwordpassword", "passwordpassword1", "passwordpassword123", "password12345678",
    "123456789012345", "1234567890123456", "12345678901234567890", "qwertyuiopasdfgh",
    "qwertyuiopasdfghjkl", "letmeinletmeinletmein", "adminadminadmin", "administrator123",
    "welcome123456789", "iloveyouiloveyou", "correct horse battery staple",
    "bifmatraders12345", "bifma traders123", "bifma traders1234", "bifma traders12345",
})


class SecurityError(RuntimeError):
    pass


class AuthenticationError(SecurityError):
    pass


class AuthorizationError(SecurityError):
    pass


class SecurityValidationError(SecurityError, ValueError):
    pass


class BootstrapError(SecurityError):
    pass


@dataclass(frozen=True, slots=True)
class User:
    id: str
    username: str
    display_name: str
    is_admin: bool
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class Session:
    user: User
    token: str = field(repr=False)
    expires_at: int


def normalize_username(value):
    if not isinstance(value, str) or len(value) > 1024:
        raise SecurityValidationError("A username is required.")
    value = unicodedata.normalize("NFKC", value).strip().casefold()
    if not re.fullmatch(r"[a-z0-9_.-]{1,80}", value):
        raise SecurityValidationError("Use 1–80 letters, numbers, dots, underscores, or hyphens for the username.")
    return value


def _password_bytes(password):
    if not isinstance(password, str) or not 1 <= len(password) <= 4096:
        raise SecurityValidationError("A password of at most 1,024 characters is required.")
    normalized = unicodedata.normalize("NFC", password)
    if not 1 <= len(normalized) <= 1024:
        raise SecurityValidationError("A password of at most 1,024 characters is required.")
    try:
        return normalized, normalized.encode("utf-8")
    except UnicodeEncodeError:
        raise SecurityValidationError("The password contains unsupported Unicode characters.") from None


def _hash_password(password_bytes):
    salt = secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac("sha256", password_bytes, salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def _verify_password(password_bytes, encoded):
    valid_encoding = True
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        iterations = int(iterations)
        salt, expected = bytes.fromhex(salt), bytes.fromhex(expected)
        if algorithm != "pbkdf2_sha256" or not 600_000 <= iterations <= 10_000_000 or len(salt) != 32 or len(expected) != 32:
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        valid_encoding = False
        iterations, salt, expected = PBKDF2_ITERATIONS, bytes(32), bytes(32)
    actual = hashlib.pbkdf2_hmac("sha256", password_bytes, salt, iterations)
    return hmac.compare_digest(actual, expected) and valid_encoding


def _user(row):
    return User(row["id"], row["username"], row["display_name"], bool(row["is_admin"]), bool(row["is_active"]))


def _token_hash(actor):
    token = actor.token if isinstance(actor, Session) else actor
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise AuthenticationError("Your session is unavailable. Sign in again.")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class SecurityService:
    def __init__(self, path, *, clock=time.time, password_blocklist=()):
        self.path = path
        self.clock = clock
        self.password_blocklist = _COMMON_PASSWORDS | frozenset(
            unicodedata.normalize("NFC", value).casefold() for value in password_blocklist)
        # Startup may need SQLite to restore a hot journal before readonly
        # validation. The opener rejects foreign files before any writable open.
        open_native_database(path)
        with native_session(path) as connection:
            require_native(connection)

    def _now(self):
        return int(self.clock())

    def _new_password(self, password, username):
        normalized, encoded = _password_bytes(password)
        if not normalized.strip():
            raise SecurityValidationError("A password is required.")
        context = {username, username * 2, username * 3,
                   username + "123456789", username + "password", "bifma traders", "bifmatraders"}
        if (normalized.casefold() in self.password_blocklist | context
                or (len(normalized) > 1 and len(set(normalized)) == 1)):
            raise SecurityValidationError("This password is too commonly used or easily guessed. Choose a different passphrase.")
        return _hash_password(encoded)

    def _audit(self, connection, actor_id, action, *, entity_id=None, company_id=None, details=None):
        connection.execute(
            "INSERT INTO audit_events(id,actor_id,company_id,action,entity_type,entity_id,occurred_at,details_json) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (str(uuid4()), actor_id, company_id, action, "user", entity_id, self._now(),
             json.dumps(details or {}, sort_keys=True, separators=(",", ":"))),
        )

    def _issue(self, connection, row):
        now = self._now()
        token = secrets.token_urlsafe(32)
        expires = now + SESSION_SECONDS
        connection.execute(
            "INSERT INTO auth_sessions(token_hash,user_id,issued_at,expires_at,revoked_at,last_seen_at) "
            "VALUES(?,?,?,?,NULL,?)", (_token_hash(token), row["id"], now, expires, now))
        return Session(_user(row), token, expires)

    def _actor(self, connection, actor):
        require_native(connection)
        if not connection.in_transaction:
            raise SecurityValidationError("Authorization requires an active native transaction.")
        digest = _token_hash(actor)
        now = self._now()
        row = connection.execute(
            "SELECT u.*,s.issued_at,s.expires_at,s.last_seen_at,s.revoked_at FROM auth_sessions s "
            "JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (digest,)).fetchone()
        if row is None or not row["is_active"] or row["revoked_at"] is not None or now < row["issued_at"] or row["expires_at"] <= now or row["last_seen_at"] + IDLE_SECONDS <= now:
            raise AuthenticationError("Your session is unavailable. Sign in again.")
        if row["last_seen_at"] < now:
            connection.execute("UPDATE auth_sessions SET last_seen_at=? WHERE token_hash=?", (now, digest))
        return row

    def _admin(self, connection, actor):
        row = self._actor(connection, actor)
        if not row["is_admin"]:
            raise AuthorizationError("Administrator access is required.")
        return row

    def bootstrap_admin(self, username, password, *, display_name=""):
        username = normalize_username(username)
        name = self._display_name(display_name, username)
        with native_transaction(self.path) as connection:
            if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
                raise BootstrapError("Initial administrator setup has already been completed.")
            encoded = self._new_password(password, username)
            identifier = str(uuid4())
            now = self._now()
            connection.execute(
                "INSERT INTO users(id,username,display_name,password_hash,is_admin,is_active,failed_attempts,"
                "locked_until,password_changed_at,created_at,updated_at) VALUES(?,?,?,?,1,1,0,NULL,?,?,?)",
                (identifier, username, name, encoded, now, now, now))
            row = connection.execute("SELECT * FROM users WHERE id=?", (identifier,)).fetchone()
            self._audit(connection, identifier, "security.bootstrap", entity_id=identifier)
            return self._issue(connection, row)

    def _attempt(self, connection, row, password_bytes, *, valid_input=True):
        now = self._now()
        matches = _verify_password(password_bytes, row["password_hash"] if row else _DUMMY_HASH)
        locked = row is not None and row["locked_until"] is not None and row["locked_until"] > now
        accepted = row is not None and bool(row["is_active"]) and not locked and matches and valid_input
        if accepted:
            connection.execute("UPDATE users SET failed_attempts=0,locked_until=NULL,updated_at=? WHERE id=?", (now, row["id"]))
        elif row is not None and row["is_active"] and not locked:
            failures = 0 if row["locked_until"] is not None else row["failed_attempts"]
            failures += 1
            lock = now + LOCK_SECONDS if failures >= FAILED_ATTEMPT_LIMIT else None
            connection.execute("UPDATE users SET failed_attempts=?,locked_until=?,updated_at=? WHERE id=?",
                               (failures, lock, now, row["id"]))
        if not accepted:
            self._audit(connection, None, "security.authentication_failed", entity_id=row["id"] if row else None)
        return accepted

    def authenticate(self, username, password):
        try:
            username = normalize_username(username)
        except SecurityValidationError:
            username = "!invalid"
        valid_input = True
        try:
            _, password_bytes = _password_bytes(password)
        except SecurityValidationError:
            password_bytes, valid_input = b"invalid-authentication-input", False
        result = None
        with native_transaction(self.path) as connection:
            row = connection.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if self._attempt(connection, row, password_bytes, valid_input=valid_input):
                result = self._issue(connection, row)
                self._audit(connection, row["id"], "security.authenticated", entity_id=row["id"])
        # Failed counters/audits must commit before the generic failure is raised.
        if result is None:
            raise AuthenticationError(_BAD_CREDENTIALS)
        return result

    def login_profile(self, username):
        """Return saved display name and company choices for the login form.

        The owner requested name completion before entering a password. This
        bounded exact lookup exposes no user IDs, roles or credentials and
        neither authenticates nor changes failed attempts or session state.
        Company names/IDs are selection metadata only; after authenticating,
        the caller must obtain fresh accessible_companies() before proceeding.
        """
        try:
            username = normalize_username(username)
        except SecurityValidationError:
            return None
        with native_session(self.path) as connection:
            # Read the display name and its company choices from one snapshot.
            connection.execute("BEGIN")
            require_native(connection)
            row = connection.execute(
                "SELECT id,display_name,is_admin FROM users WHERE username=? AND is_active=1",
                (username,)).fetchone()
            if row is None:
                return None
            return {"display_name": row["display_name"], "companies": self._company_choices(connection, row)}

    def authorize(self, actor, company_id, module, action, *, connection=None):
        if module not in MODULES or action not in ACTIONS:
            raise AuthorizationError("This operation is not permitted.")
        if connection is None:
            with native_transaction(self.path) as active:
                return self.authorize(actor, company_id, module, action, connection=active)
        row = self._actor(connection, actor)
        if company_id is None:
            if row["is_admin"] and module in GLOBAL_MODULES:
                return _user(row)
            raise AuthorizationError("A permitted company is required.")
        company = connection.execute("SELECT id FROM companies WHERE id=? AND is_active=1", (company_id,)).fetchone()
        if company is None:
            raise AuthorizationError("This company is unavailable.")
        if row["is_admin"]:
            return _user(row)
        allowed = connection.execute(
            f"SELECT 1 FROM user_company_grants c JOIN user_menu_grants m ON c.user_id=m.user_id "
            f"WHERE c.user_id=? AND c.company_id=? AND m.module=? AND m.can_{action}=1",
            (row["id"], company_id, module)).fetchone()
        if allowed is None:
            raise AuthorizationError("This operation is not permitted for the selected company.")
        return _user(row)

    require = authorize

    def can(self, actor, company_id, module, action):
        try:
            self.authorize(actor, company_id, module, action)
            return True
        except (AuthenticationError, AuthorizationError):
            return False

    def require_admin(self, actor, *, connection=None):
        if connection is None:
            with native_transaction(self.path) as active:
                return _user(self._admin(active, actor))
        return _user(self._admin(connection, actor))

    def current_user(self, actor):
        with native_transaction(self.path) as connection:
            return _user(self._actor(connection, actor))

    def accessible_companies(self, actor):
        with native_transaction(self.path) as connection:
            row = self._actor(connection, actor)
            return self._company_choices(connection, row)

    @staticmethod
    def _company_choices(connection, row):
        if row["is_admin"]:
            result = connection.execute("SELECT id,name FROM companies WHERE is_active=1 ORDER BY name,id")
        else:
            result = connection.execute(
                "SELECT c.id,c.name FROM companies c JOIN user_company_grants g ON g.company_id=c.id "
                "WHERE c.is_active=1 AND g.user_id=? ORDER BY c.name,c.id", (row["id"],))
        return [tuple(company) for company in result]

    def logout(self, actor):
        with native_transaction(self.path) as connection:
            row = self._actor(connection, actor)
            connection.execute("UPDATE auth_sessions SET revoked_at=? WHERE token_hash=?", (self._now(), _token_hash(actor)))
            self._audit(connection, row["id"], "security.logged_out", entity_id=row["id"])

    @staticmethod
    def _display_name(value, username):
        if not isinstance(value, str) or len(value) > 120 or any(ord(char) < 32 for char in value):
            raise SecurityValidationError("Use a display name with at most 120 characters.")
        return value.strip() or username

    @staticmethod
    def _target(connection, user_id):
        row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise SecurityValidationError("The selected user does not exist.")
        return row

    def create_user(self, actor, username, password, *, display_name="", is_admin=False):
        with native_transaction(self.path) as connection:
            admin = self._admin(connection, actor)
            if type(is_admin) is not bool:
                raise SecurityValidationError("Administrator status must be a boolean.")
            username = normalize_username(username)
            name = self._display_name(display_name, username)
            encoded = self._new_password(password, username)
            identifier, now = str(uuid4()), self._now()
            try:
                connection.execute(
                    "INSERT INTO users(id,username,display_name,password_hash,is_admin,is_active,failed_attempts,"
                    "locked_until,password_changed_at,created_at,updated_at) VALUES(?,?,?,?,?,1,0,NULL,?,?,?)",
                    (identifier, username, name, encoded, int(is_admin), now, now, now))
            except sqlite3.IntegrityError:
                raise SecurityValidationError("This username is already in use.") from None
            self._audit(connection, admin["id"], "security.user_created", entity_id=identifier, details={"is_admin": is_admin})
            return _user(self._target(connection, identifier))

    def set_company_access(self, actor, user_id, company_ids):
        """Replace assignments; retain existing inactive ones but never add them.

        An inactive assignment grants no current company access. Keeping it
        allows unrelated access edits without silently discarding the user's
        existing assignment. Explicit removal remains supported; subsequent
        re-addition requires that company to be active again.
        """
        with native_transaction(self.path) as connection:
            admin = self._admin(connection, actor)
            self._target(connection, user_id)
            if isinstance(company_ids, (str, bytes)):
                raise SecurityValidationError("Company access requires a collection of company IDs.")
            selected = set(company_ids)
            existing = {row[0] for row in connection.execute(
                "SELECT company_id FROM user_company_grants WHERE user_id=?", (user_id,))}
            for company_id in selected:
                company = connection.execute("SELECT is_active FROM companies WHERE id=?", (company_id,)).fetchone() if isinstance(company_id, str) else None
                if company is None or (not company["is_active"] and company_id not in existing):
                    raise SecurityValidationError("A selected company is unavailable.")
            connection.execute("DELETE FROM user_company_grants WHERE user_id=?", (user_id,))
            connection.executemany("INSERT INTO user_company_grants(user_id,company_id) VALUES(?,?)",
                                   [(user_id, identifier) for identifier in sorted(selected)])
            self._audit(connection, admin["id"], "security.company_access_changed", entity_id=user_id,
                        details={"company_count": len(selected)})

    def set_permissions(self, actor, user_id, module, actions):
        with native_transaction(self.path) as connection:
            admin = self._admin(connection, actor)
            self._target(connection, user_id)
            if module not in MODULES or isinstance(actions, (str, bytes)):
                raise SecurityValidationError("Select a supported module and permission collection.")
            selected = set(actions)
            if not selected <= ACTIONS:
                raise SecurityValidationError("An unsupported permission was requested.")
            ordered = ("read", "create", "update", "delete", "post")
            connection.execute(
                "INSERT INTO user_menu_grants(user_id,module,can_read,can_create,can_update,can_delete,can_post) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,module) DO UPDATE SET can_read=excluded.can_read,"
                "can_create=excluded.can_create,can_update=excluded.can_update,can_delete=excluded.can_delete,can_post=excluded.can_post",
                (user_id, module, *(int(action in selected) for action in ordered)))
            self._audit(connection, admin["id"], "security.permissions_changed", entity_id=user_id,
                        details={"module": module, "actions": sorted(selected)})

    @staticmethod
    def _preserve_admin(connection, target, *, is_admin, is_active):
        if target["is_admin"] and target["is_active"] and not (is_admin and is_active):
            other = connection.execute("SELECT 1 FROM users WHERE is_admin=1 AND is_active=1 AND id<>? LIMIT 1", (target["id"],)).fetchone()
            if other is None:
                raise SecurityValidationError("Keep at least one active administrator.")

    def set_active(self, actor, user_id, is_active):
        if type(is_active) is not bool:
            raise SecurityValidationError("Account status must be a boolean.")
        with native_transaction(self.path) as connection:
            admin = self._admin(connection, actor)
            target = self._target(connection, user_id)
            self._preserve_admin(connection, target, is_admin=target["is_admin"], is_active=is_active)
            now = self._now()
            connection.execute("UPDATE users SET is_active=?,updated_at=? WHERE id=?", (int(is_active), now, user_id))
            if not is_active:
                self._revoke(connection, user_id)
            self._audit(connection, admin["id"], "security.user_status_changed", entity_id=user_id, details={"is_active": is_active})

    def set_admin(self, actor, user_id, is_admin):
        if type(is_admin) is not bool:
            raise SecurityValidationError("Administrator status must be a boolean.")
        with native_transaction(self.path) as connection:
            admin = self._admin(connection, actor)
            target = self._target(connection, user_id)
            self._preserve_admin(connection, target, is_admin=is_admin, is_active=target["is_active"])
            connection.execute("UPDATE users SET is_admin=?,updated_at=? WHERE id=?", (int(is_admin), self._now(), user_id))
            self._revoke(connection, user_id)
            self._audit(connection, admin["id"], "security.admin_status_changed", entity_id=user_id, details={"is_admin": is_admin})

    def _revoke(self, connection, user_id):
        connection.execute("UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (self._now(), user_id))

    def change_password(self, actor, current_password, new_password):
        result = None
        with native_transaction(self.path) as connection:
            row = self._actor(connection, actor)
            valid_input = True
            try:
                _, password_bytes = _password_bytes(current_password)
            except SecurityValidationError:
                password_bytes, valid_input = b"invalid-authentication-input", False
            if self._attempt(connection, row, password_bytes, valid_input=valid_input):
                encoded = self._new_password(new_password, row["username"])
                if _verify_password(password_bytes, encoded):
                    raise SecurityValidationError("Choose a different new passphrase.")
                now = self._now()
                connection.execute("UPDATE users SET password_hash=?,password_changed_at=?,updated_at=? WHERE id=?",
                                   (encoded, now, now, row["id"]))
                self._revoke(connection, row["id"])
                self._audit(connection, row["id"], "security.password_changed", entity_id=row["id"])
                result = self._issue(connection, row)
        if result is None:
            raise AuthenticationError(_BAD_CREDENTIALS)
        return result

    def reset_password(self, actor, user_id, new_password):
        with native_transaction(self.path) as connection:
            admin = self._admin(connection, actor)
            target = self._target(connection, user_id)
            encoded = self._new_password(new_password, target["username"])
            now = self._now()
            connection.execute("UPDATE users SET password_hash=?,password_changed_at=?,updated_at=?,failed_attempts=0,locked_until=NULL WHERE id=?",
                               (encoded, now, now, user_id))
            self._revoke(connection, user_id)
            self._audit(connection, admin["id"], "security.password_reset", entity_id=user_id)

    def list_users(self, actor):
        with native_transaction(self.path) as connection:
            self._admin(connection, actor)
            return [_user(row) for row in connection.execute("SELECT id,username,display_name,is_admin,is_active FROM users ORDER BY username")]

    def get_access(self, actor, user_id):
        """Read a user's current grants for a populated administrator editor."""
        with native_transaction(self.path) as connection:
            self._admin(connection, actor)
            target = self._target(connection, user_id)
            companies = [row[0] for row in connection.execute(
                "SELECT company_id FROM user_company_grants WHERE user_id=? ORDER BY company_id", (user_id,))]
            permissions = {row["module"]: [action for action in sorted(ACTIONS) if row["can_" + action]]
                           for row in connection.execute("SELECT * FROM user_menu_grants WHERE user_id=? ORDER BY module", (user_id,))}
            return {"user": _user(target), "company_ids": companies, "permissions": permissions}

    def permissions(self, actor, company_id):
        """Current session's grants, for navigation; services still authorize writes."""
        with native_transaction(self.path) as connection:
            user = self._actor(connection, actor)
            if connection.execute("SELECT 1 FROM companies WHERE id=? AND is_active=1", (company_id,)).fetchone() is None:
                raise AuthorizationError("This company is unavailable.")
            if user["is_admin"]:
                return {module: sorted(ACTIONS) for module in MODULES}
            if connection.execute("SELECT 1 FROM user_company_grants WHERE user_id=? AND company_id=?", (user["id"], company_id)).fetchone() is None:
                raise AuthorizationError("This company is unavailable.")
            return {row["module"]: [action for action in sorted(ACTIONS) if row["can_" + action]]
                    for row in connection.execute("SELECT * FROM user_menu_grants WHERE user_id=?", (user["id"],))}
