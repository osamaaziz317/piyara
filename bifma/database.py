"""Small SQLite connection, transaction, and verified backup primitives.

Connections are short lived. Every writer takes an IMMEDIATE transaction so a
stock check and its resulting movements can eventually share the same lock.
All schema migrations must happen explicitly, never as a side effect of reads.
"""

from contextlib import contextmanager
from pathlib import Path
import os
import sqlite3
import stat
import tempfile
from threading import Condition, get_ident


class DatabaseError(RuntimeError):
    """The database failed an integrity or lifecycle check."""


class _FileActivity:
    def __init__(self):
        self.sessions = {}
        self.startup_thread = None
        self.waiting_startups = 0
        self.references = 0


_file_condition = Condition()
_file_activity = {}


def _thread_has_sessions(thread):
    return any(thread in activity.sessions for activity in _file_activity.values())


def _thread_has_startup(thread):
    return any(activity.startup_thread == thread for activity in _file_activity.values())


def _activity_reference(path):
    """Called only under _file_condition; waiting users retain their state."""
    resolved = Path(path).resolve()
    # Use existing-file identity as well as canonical spelling: an alias or a
    # renamed active file must not bypass the descriptor/SQLite lifetime gate.
    # stat itself does not open/close a raw descriptor or disturb POSIX locks.
    try:
        metadata = resolved.stat()
    except FileNotFoundError:
        metadata = None
    key = ("inode", metadata.st_dev, metadata.st_ino) if metadata is not None and stat.S_ISREG(metadata.st_mode) and metadata.st_ino else ("path", os.path.normcase(str(resolved)))
    activity = _file_activity.setdefault(key, _FileActivity())
    activity.references += 1
    return key, activity


def _release_activity(key, activity):
    activity.references -= 1
    if activity.references == 0:
        del _file_activity[key]
    _file_condition.notify_all()


@contextmanager
def _session_lifetime(path):
    """Keep raw startup file access separate from every managed SQLite handle."""
    thread = get_ident()
    with _file_condition:
        key, activity = _activity_reference(path)
        try:
            if _thread_has_startup(thread):
                raise DatabaseError("A managed database session cannot start inside native startup")
            # Existing operations may open nested sessions (online backup uses
            # this, sometimes across files). Blocking an operation's nested
            # sessions behind waiting startups can create a cross-file cycle.
            while activity.startup_thread is not None or (activity.waiting_startups and not _thread_has_sessions(thread)):
                _file_condition.wait()
            activity.sessions[thread] = activity.sessions.get(thread, 0) + 1
        except BaseException:
            _release_activity(key, activity)
            raise
    try:
        yield
    finally:
        with _file_condition:
            activity.sessions[thread] -= 1
            if activity.sessions[thread] == 0:
                del activity.sessions[thread]
            _release_activity(key, activity)


@contextmanager
def exclusive_file_access(path):
    """Isolate native startup's raw header descriptor from in-process sessions.

    POSIX close() on any descriptor can release another connection's advisory
    locks. Wait for other managed sessions, block new ones during startup, and
    reject startup nested in any managed session/startup in the same thread
    (including another file) rather than creating a cross-file wait cycle.
    Bare connect/sqlite3 handles are outside this managed lifecycle contract.
    """
    thread = get_ident()
    with _file_condition:
        key, activity = _activity_reference(path)
        try:
            if _thread_has_sessions(thread) or _thread_has_startup(thread):
                raise DatabaseError("Native startup cannot run inside an active database session; finish that operation first")
            activity.waiting_startups += 1
            try:
                while activity.sessions or activity.startup_thread is not None:
                    _file_condition.wait()
                activity.startup_thread = thread
            finally:
                activity.waiting_startups -= 1
        except BaseException:
            _release_activity(key, activity)
            raise
    try:
        yield
    finally:
        with _file_condition:
            activity.startup_thread = None
            _release_activity(key, activity)


def connect(path, *, readonly=False):
    """Low-level open; operational callers use session for coordinated lifetime.

    A readonly connection cannot create a missing file. Direct handles are not
    covered by native startup coordination; prefer the session context manager.
    """
    path = Path(path).resolve()
    mode = "ro" if readonly else "rw"
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode={mode}", uri=True, timeout=10,
        isolation_level=None,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        if not readonly:
            connection.execute("PRAGMA synchronous=FULL")
    except BaseException:
        # Do not release a session's lifecycle registration while a connection
        # retained by an exception traceback is still open outside that gate.
        connection.close()
        raise
    return connection


@contextmanager
def session(path, *, readonly=True):
    """Close the connection on success and failure; default to readonly."""
    with _session_lifetime(path):
        connection = connect(path, readonly=readonly)
        try:
            yield connection
        finally:
            connection.close()


@contextmanager
def transaction(path):
    """Commit all writes together or roll them all back, including exceptions."""
    with session(path, readonly=False) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def check_integrity(path):
    """Return safe structural diagnostics, without exposing business rows."""
    with session(path) as connection:
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    return {
        "integrity": integrity,
        "foreign_key_violations": len(violations),
        "ok": integrity == ["ok"] and not violations,
    }


def require_integrity(path):
    result = check_integrity(path)
    if not result["ok"]:
        raise DatabaseError(
            "Database integrity check failed "
            f"({len(result['integrity'])} results, "
            f"{result['foreign_key_violations']} foreign-key violations)."
        )


def publish_new_file(staged, destination):
    """Atomically publish a same-filesystem file without replacing any file.

    The caller stages in destination.parent. Hard-link creation fails if another
    process creates the destination while the file is being prepared. Unlike
    os.replace, this cannot overwrite a database that appeared after a check.

    The caller owns a writable staging file: Windows file flushing needs write
    access even though this helper does not change its contents. Publication
    preserves the file's permissions; POSIX mode 0600 does not establish a
    private Windows ACL, which still requires target-environment qualification.
    """
    staged, destination = Path(staged), Path(destination)
    with staged.open("r+b") as file:
        os.fsync(file.fileno())
    os.link(staged, destination)
    staged.unlink()
    if os.name == "posix":
        descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def new_destination(path):
    """Resolve its directory without following an existing final symlink."""
    path = Path(path)
    destination = path.parent.resolve() / path.name
    if os.path.lexists(destination):
        raise FileExistsError(f"Destination already exists: {destination}")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"Destination directory is missing: {destination.parent}")
    return destination


def backup_database(source, destination):
    """Make a consistent SQLite backup, verify it, and publish to a new path.

    This works for active SQLite databases, including committed data in WAL.
    It never overwrites an existing backup or the source. Restoring into an
    active application is intentionally not provided by this low-level helper.
    """
    source, destination = Path(source).resolve(), new_destination(destination)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".bifma-backup-", suffix=".sqlite3", dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with session(source) as original:
            target = sqlite3.connect(temporary)
            try:
                original.backup(target)
            finally:
                target.close()
        require_integrity(temporary)
        publish_new_file(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
