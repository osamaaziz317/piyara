# Native backup and isolated restore

`bifma/native_backup.py` provides verified full-database backups and restores for
the current native schema. Restores always produce a **new file**. Neither API
overwrites an existing database, imports historical credentials, promotes the
Oracle archive/V2 database, or silently initializes/migrates its source.

## Service contract

```python
service = NativeBackupService(path, security=None, clock=time.time)
result = service.create_backup(actor, destination)
# BackupResult(path: pathlib.Path, version: int)

result = restore_backup(
    source, destination, username, password, clock=time.time
)
# RestoreResult(path: pathlib.Path, version: int, revoked_sessions: int)
```

The destination directory must already exist. The destination itself must not
exist; this includes empty files, directories and dangling symlinks. Both APIs
return a resolved destination path. They create private staging directories and
files next to the destination, then publish a complete standalone SQLite file
through the existing non-overwriting atomic hard-link primitive.

The resulting file is mode `0600`, and staging directories are `0700` on POSIX.
The target filesystem must support hard links. Publication fails explicitly if
it cannot do so; there is no non-atomic overwrite fallback. Windows filesystem,
ACL, packaging and real restore acceptance still need target-environment tests.

## Creating a backup

`actor` must be a current opaque `SecurityService` session/token belonging to an
active administrator. An arbitrary user ID/display object, expired or revoked
session, disabled user or ordinary user cannot authorize a backup. Authorization
and source audit use the same immediate native transaction, which excludes
other application writers during the snapshot operation.

SQLite's online backup API copies the full committed database through a separate
read-only source connection. It includes committed WAL data, exact decimal text,
all companies, business history, users, grants and existing session hashes. The
copy is checked for native application/migration lineage, current version,
integrity and foreign-key violations before publication. The staged copy is
converted to a standalone file so a backup does not depend on an accompanying
WAL file.

The source's authorization heartbeat and `backup.created` audit event commit
after the copied snapshot. Therefore the snapshot intentionally does not contain
this operation's new audit event or refreshed heartbeat. Business records are
unchanged. The audit records actor, time and schema version, without filesystem
paths, passwords, tokens or business values.

If copying, validation, audit, publication or source commit fails, the source
transaction rolls back and staging is removed. If publication linked our file
before a later failure, the service removes that owned link. A competing file
that appeared at the destination is preserved, using inode identity to distinguish
it from our publication.

## Restoring a backup

Restore starts by taking a consistent SQLite snapshot into a private staging
directory. It validates the snapshot before checking credentials. The entered
username/password must authenticate an **active administrator stored in that
backup**. Passwords from another database, ordinary-user credentials, an inactive
administrator or a currently locked account cannot authorize restore. A native
file with no configured administrator cannot be restored through this API.

Credential checking happens only against the staged copy. Failed authentication,
failure counters, temporary login sessions and audit writes never reach the
source. The source database pages and committed WAL frames remain unchanged.
As with other normal SQLite read-only access, SQLite can create/update its `-shm`
shared-memory reader-coordination sidecar; this is not a business-data or WAL
change. The source is not opened with `immutable=1`, since that would be unsafe
for an active database.

After successful authentication, restore rechecks administrator authorization
under the staged copy's write lock, revokes every unrevoked copied session and
the temporary restore login, and appends `backup.restored`. Future-dated copied
sessions are revoked with a timestamp no earlier than their issue time; already
revoked sessions retain their existing revocation. No usable session is returned.
The caller must sign in again to open the restored file.

The restore audit stores the authenticating actor, schema version and newly
revoked-session count. Credentials, raw tokens, file paths and business values
are absent. Business tables retain their copied values; authentication counters,
the administrator's authentication timestamp, session state and restore/login
audit records are the intended differences.

The staged copy is checkpointed into a standalone file and verified again before
publication. Any error removes the staged copy and prevents a destination from
being left behind; a racing pre-existing destination is preserved. The source,
including its failed-login counters and original session status, is never edited
by restore. There is no active-database replacement or automatic application
switch; opening the returned file is a separate UI operation.

## Versions and trust boundary

Only the current recognized native version is accepted. Future versions,
unrecognized files, inconsistent user versions and altered migration metadata
are rejected. An older recognized backup produces an explicit instruction to
make a separate copy, run `migrate_database` on that copy and restore the upgraded
copy. Native migration then creates its own pre-upgrade backup. Restore itself
never migrates or writes to the original backup.

Lineage/integrity checks verify the expected native markers, migration checksums
and SQLite structural constraints. They are not a cryptographic signature over
business content or proof that a file was never edited outside the application.
Backups contain the complete business database and password/session hashes; they
are private files, not encrypted archives. The same local operating-system access
boundary as the active database applies.

Security errors retain `AuthenticationError` / `AuthorizationError`. Native
lineage failures raise `NativeSchemaError`, structural failures `DatabaseError`,
and filesystem/SQLite failures retain their ordinary exception types. No API
returns password hashes, session hashes or credentials in its result.

## Verification

Run `python3 -m unittest tests.test_native_backup -v`. Synthetic temporary-file
tests cover full exact snapshots, committed WAL content, write-lock authorization,
private staging/files, safe audits, fresh credentials and administrator checks,
every-session revocation, source database/WAL byte preservation, invalid lineage
and foreign keys, unknown/missing files, no-overwrite and symlink handling,
competing publications, and rollback/cleanup after audit, commit or publication
failures. Production restore drills, external backup retention, encryption policy
and target Windows acceptance remain separate operational work.
