# Native posting process-interruption checks

`tests/test_native_process_recovery.py` covers two synthetic subprocess
interruption boundaries and the related startup/refusal paths. Every run creates a new temporary database and
uses the public schema, security, master and transaction services for setup.
No original database or recovery archive is opened. The child runs the current
Python executable with an argument list, without a shell. Paths containing
spaces are covered, and child execution has a 30-second timeout.

1. **Interruption before commit.** A child wraps `TransactionService._write_effects`
   only within that test process. The wrapper calls the real implementation,
   lowers that connection's cache size to force dirty-page spill, verifies that
   journal/stock rows exist in its open transaction and the header remains a
   draft, then calls `os._exit(73)`. Python exception handlers and
   transaction context cleanup do not run. Reopening through fresh services must
   recover the exact original draft, including saved labels/inputs, line IDs,
   revision and timestamps. No journal entry, journal line, stock movement or
   posting audit event may survive. Posted stock, opening stock and integrity
   checks must be correct. A normal retry must post exactly once.
2. **Commit without client acknowledgement.** A child completes `post`, confirms
   committed status/effects through a separate connection, then exits abruptly
   without returning any success payload. A fresh caller must observe one posted
   document, one journal, one stock movement, one posting audit event and the
   expected exact monetary/quantity totals. Retrying the original document ID and
   pre-post revision in both the restarted parent and another Python process must
   raise/reach `RevisionConflict`, preserving all committed effect rows unchanged.

This verifies deduplication for retries of the **same saved document ID**. It does
not provide an external request-id API or deduplicate a user creating a different
document with a different number. Authentication before/after the interruption
may legitimately add login audit events or refresh session state; assertions
isolate the business document and its own posting audit trail.

Run with:

```sh
python3 -m unittest discover -s tests -p 'test_native_process_recovery.py'
```

The **19 focused checks passed on the available Linux host** on 2026-10-02. The
dirty-page-spill test first exposed a restart defect: readonly constructor
validation could not recover a hot journal and incorrectly rejected the native
database. Startup now calls `native_schema.open_native_database` before ordinary
readonly validation. The test verifies a nonzero on-disk rollback-journal header
before interruption, and verifies that an ordinary readonly session still
refuses recovery without changing either file. Fresh security, master and backup
service startup can then recover the intact draft.

The opener reads the raw SQLite header and native application ID before allowing
an existing-file writable SQLite connection. SQLite itself performs journal
recovery; the application never removes recovery journals manually. Full native
lineage/version and integrity checks follow recovery. Refusal tests cover
missing/empty/text files, read-only archives/V2-like files, symlink/hard-link
aliases, a path replaced during open, false native markers, unsupported versions
and foreign-key violations. Healthy startup leaves database bytes unchanged.
An interrupted older native file also recovers through the explicit upgrade
entry point; its pre-upgrade backup contains only the recovered committed state.

For the owner's short-username login change, the application now explicitly
prepares schema-5 files for schema 6 before constructing services. This is a
bounded startup step, not an implicit migration in `open_native_database` or
ordinary service constructors. It verifies the sealed baseline and expected
source association again under the upgrade write lock before backup or changes.
The username-table rebuild preserves users, hashes, sessions, grants, rowids and
business history; migrations 1–5 remain unchanged. Older schemas still use the
explicit upgrade path. `tests/test_username_migration.py` covers preservation,
source mismatch, tampering, rollback, current-file reuse and startup concurrency.

Additional process/thread checks verify that header inspection cannot release
another managed transaction's POSIX advisory lock, including through a renamed
file; startup waits for other threads' sessions while normal reads remain
concurrent. Same-thread nested startup and inverse managed-session entry are
rejected across all files, with a bounded two-thread subprocess checking the
cross-file deadlock case. Failed connection configuration closes the real handle
before releasing its lifecycle registration. Operational callers must use the
managed session APIs; bare SQLite/connect handles and moving active database
files are outside the supported lifecycle.
See [NATIVE_DESIGN.md](NATIVE_DESIGN.md#native-startup-and-crash-recovery) for the
contract and official SQLite references.

## Scoped startup verification

The normal startup worker wraps baseline preparation and service construction in
`native_schema.startup_verification()`. This removes repeated full integrity and
immutable-baseline scans of the same unchanged file during that one startup.
The first check still performs native header/lineage verification, SQLite
recovery when needed, full integrity/foreign-key checks and the complete sealed
baseline digest. Archive hashes are still verified; this scope does not cache
archive trust or authorize historical reads.

The scope exists only in the current thread and process, with no persistent
cache. Nested constructors share it; leaving the outer scope, including through
an exception, discards it. Later startup, retry, another thread and another
process perform their own full checks. Reuse requires the same canonical path,
regular-file identity, link count, mode, size and nanosecond modification/change
timestamps, and the same migration specification and current-version policy.
Changes force full validation. Any SQLite journal, WAL or shared-memory sidecar,
alias or unstatable path clears prior observations and takes the normal path.
These are short startup-only observations on the existing supported local-file
lifecycle, not a general cache for long-lived database sessions.

Every native opener, including a reuse hit, still enters the existing exclusive
file-access lifecycle gate. Metadata checks never open raw file descriptors, so
they cannot release another SQLite connection's POSIX locks. `baseline_info`
starts a read transaction and reads its manifest before comparing the current
file signature. The immutable digest and manifest therefore describe the same
committed snapshot; the manifest itself is checked afresh on each call. Digest
reuse cannot precede snapshot establishment, and a result is retained only when
the file signature remains unchanged through successful verification.

`tests/test_startup_verification.py` adds 18 synthetic checks for once-per-startup
work, scope expiry, mutation before snapshot establishment, committed changes,
same-path replacement, alias refusal, lineage/foreign-key damage, thread/process
isolation, journal markers, genuine interrupted-writer recovery and unchanged
archive verification. Together with native schema, process-recovery, baseline
import and document-register checks, **89 tests passed on 2026-10-04**.

The tests preserve the application's default rollback journal mode; only the
child's cache threshold is reduced to force this failure boundary using a small
fixture. They perform real process termination, not a simulated exception, but
do not simulate power loss, kernel failure, disk-controller/cache failure,
network filesystems or every possible interruption point. The Windows target,
installed release package and target storage still require separate
qualification. This evidence does not establish overall production readiness
or complete legacy-workflow parity.
