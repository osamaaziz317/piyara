# SQLite recovery archive

The separate `data/legacy_recovered.sqlite3` archive was generated on 2026-10-02
from the supplied, checksum-pinned Oracle export. The original export and V2
database were left unchanged.

## Verified local results

- 30 source tables, 93,110 rows.
- Every inserted table was read back in source order and compared with its
  source count and canonical content SHA-256.
- All declared required fields, 27 primary keys, 23 unique constraints and 79
  foreign-key relationships were audited with zero detected violations.
- SQLite integrity check passed.
- The archive contains `_recovery_metadata` with the full source schema,
  provenance, counts, table digests and constraint audit.
- It is marked `recovered_unverified`, with `oracle_reconciled: false`.

These results establish local reconstruction and consistency, not an independent
Oracle import comparison or correctness of the original accounting rules.

The separate independent check has now passed: original Oracle Import and typed
multiset comparison match all 30 tables, 93,110 rows and 1,424,337 fields. See
[oracle_reconciliation.json](oracle_reconciliation.json) for exact hashes and
[ORACLE_RECONCILIATION.md](ORACLE_RECONCILIATION.md) for its scope. The archive's
initial manifest and bytes are unchanged; this result does not certify original
screen/report behavior or authorize automatic operational migration.

## Storage and preservation

Original table/column names are retained. All source values use SQLite `TEXT`
affinity except SQL NULL. Oracle `NUMBER` is canonical exact decimal text,
`DATE` is `YYYY-MM-DDTHH:MM:SS`, and character values retain original spacing and
Unicode text. Leading-zero text identifiers are preserved. Numeric and date
semantics are reconstructed by the application; do not use SQLite floating-point
aggregates on recovered financial values.

Source declarations remain metadata. The archive itself does not enforce Oracle
constraints or execute recovered SQL, functions, defaults, or views. Nonunique
indexes accelerate inspection while preserving any historical duplicates.
An audit would report conflicting source rows without silently dropping them.
Composite unique checks follow Oracle's treatment of partially-null keys.
([Oracle Data Integrity](https://docs.oracle.com/html/E10713_02/datainte.htm))

The archive includes all source tables, including user and integration settings.
It is created as a private file on POSIX and excluded from version control; the
preview exposes only business listings and documents. Recovered credential
fields are not an authentication implementation for the replacement.

## Atomic recovery and backup behavior

`python3 -m tools.stage_recovery --output NEW_PATH` builds in a private temporary
file in the output directory. Decode/insertion/reconciliation failures discard
the incomplete file. Publishing uses an atomic hard link and refuses existing
paths, including dangling symbolic links. It does not replace a database even if
another process creates the destination during recovery.

`bifma.database.backup_database(source, destination)` uses SQLite's backup API to
capture committed data, including WAL content, then verifies the result before
publishing to a new path. It does not implement in-place restore over an active
database. Final directory synchronization is attempted on POSIX; an I/O error
after publication can leave a complete destination present with durability
unconfirmed. Check the destination before retrying, rather than overwriting it.

The destination directory must already exist. Publication requires filesystem
hard-link support. The separate native application's managed backup and new-file
restore flow are implemented; see [BACKUP_RESTORE.md](BACKUP_RESTORE.md). Retention
policy and target deployment storage locations still need qualification.

## Independent acceptance

Original Oracle `imp`, every typed table value/count, the three reconstructed
stock/ledger views and 37 aggregate/filter comparisons have passed. Original
application report/print comparison and operational cutover remain separate
gates. `docs/DATA_RECOVERY.md` records their scope. Only after those checks map
the verified history into the operational schema, explicitly
handling known source inconsistencies. The preview always opens the archive in
SQLite read-only mode and cannot promote it to production.
