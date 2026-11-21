"""Authenticated native backups and isolated, new-file restores.

Backups use SQLite's online backup API, not raw copying of a possibly active
database. Restore credentials are checked only on a private staged copy. Neither
operation replaces an existing destination or promotes historical databases.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
import time
from uuid import uuid4

from bifma.database import (
    backup_database, new_destination, publish_new_file, require_integrity,
)
from bifma.native_schema import (
    NativeSchemaError, native_session, native_transaction, require_native,
)
from bifma.security import SecurityService


@dataclass(frozen=True, slots=True)
class BackupResult:
    path: Path
    version: int


@dataclass(frozen=True, slots=True)
class RestoreResult:
    path: Path
    version: int
    revoked_sessions: int


def _now(clock):
    try:
        value = int(clock())
    except (TypeError, ValueError, OverflowError):
        raise ValueError("The operation time is unavailable.") from None
    if value < 0:
        raise ValueError("The operation time is unavailable.")
    return value


def _validate(path):
    """Verify the closed snapshot before it is authenticated or published."""
    try:
        with native_session(path) as connection:
            version = require_native(connection)
    except NativeSchemaError as error:
        if "upgrade is required" in str(error):
            raise NativeSchemaError(
                "This backup requires an explicit upgrade. Make a separate copy, "
                "run migrate_database on that copy, and restore the upgraded copy. "
                "The original backup was not changed."
            ) from error
        raise
    require_integrity(path)
    return version


def _audit(connection, actor_id, action, occurred_at, details):
    connection.execute(
        "INSERT INTO audit_events(id,actor_id,company_id,action,entity_type,entity_id,occurred_at,details_json) "
        "VALUES(?,?,NULL,?,'database',NULL,?,?)",
        (str(uuid4()), actor_id, action, occurred_at,
         json.dumps(details, sort_keys=True, separators=(",", ":"))),
    )


def _identity(path):
    stat = path.stat(follow_symlinks=False)
    return stat.st_dev, stat.st_ino


def _remove_owned_publication(destination, identity):
    """Clean our link after any publication/commit failure, never a competitor."""
    try:
        if identity is not None and _identity(destination) == identity:
            destination.unlink()
    except FileNotFoundError:
        pass


def _single_file(path):
    """Checkpoint only the staged copy, leaving no required WAL sidecar."""
    with native_session(path, readonly=False) as connection:
        mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if mode.lower() != "delete":
            raise RuntimeError("The staged database could not become a standalone file.")
    os.chmod(path, 0o600)


class NativeBackupService:
    def __init__(self, path, security=None, clock=time.time):
        self.path = Path(path).resolve()
        self.clock = clock
        self.security = security if security is not None else SecurityService(self.path, clock=clock)
        with native_session(self.path):
            pass

    def create_backup(self, actor, destination):
        """Publish an administrator-authorized, verified full native snapshot.

        The snapshot precedes this operation's source audit/heartbeat commit.
        Business rows are untouched. Source writers are excluded while the
        committed snapshot is copied and publication/audit complete together.
        """
        destination = new_destination(destination)
        identity = None
        try:
            with tempfile.TemporaryDirectory(prefix=".bifma-native-backup-", dir=destination.parent) as directory:
                staged = Path(directory) / "snapshot.sqlite3"
                with native_transaction(self.path) as connection:
                    administrator = self.security.require_admin(actor, connection=connection)
                    # A separate readonly connection sees committed state while
                    # the writer lock protects current authorization and data.
                    # Backing up the writer itself would block on its own writes.
                    backup_database(self.path, staged)
                    _single_file(staged)
                    version = _validate(staged)
                    _audit(connection, administrator.id, "backup.created", _now(self.clock),
                           {"schema_version": version})
                    identity = _identity(staged)
                    publish_new_file(staged, destination)
                return BackupResult(destination, version)
        except BaseException:
            _remove_owned_publication(destination, identity)
            raise


def restore_backup(source, destination, username, password, *, clock=time.time):
    """Restore into a new private file after authenticating a copied active admin.

    Source reads use SQLite's online backup API and include committed WAL data.
    All authentication writes occur on the private copy. Every session in that
    copy, including the temporary restore login, is revoked before publication.
    The result deliberately contains no session: sign in again to open it.
    """
    source = Path(source).resolve()
    destination = new_destination(destination)
    identity = None
    try:
        with tempfile.TemporaryDirectory(prefix=".bifma-native-restore-", dir=destination.parent) as directory:
            staged = Path(directory) / "restored.sqlite3"
            backup_database(source, staged)
            version = _validate(staged)
            security = SecurityService(staged, clock=clock)
            actor = security.authenticate(username, password)
            with native_transaction(staged) as connection:
                administrator = security.require_admin(actor, connection=connection)
                now = _now(clock)
                revoked = connection.execute(
                    "SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL"
                ).fetchone()[0]
                # A backup may contain sessions issued ahead of this machine's
                # clock. Their revocation must still satisfy the schema bound.
                connection.execute(
                    "UPDATE auth_sessions SET revoked_at=MAX(?,issued_at) WHERE revoked_at IS NULL", (now,)
                )
                _audit(connection, administrator.id, "backup.restored", now,
                       {"schema_version": version, "revoked_sessions": revoked})
            _single_file(staged)
            _validate(staged)
            identity = _identity(staged)
            publish_new_file(staged, destination)
            return RestoreResult(destination, version, revoked)
    except BaseException:
        _remove_owned_publication(destination, identity)
        raise
