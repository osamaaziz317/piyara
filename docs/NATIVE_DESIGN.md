# Native SQLite foundation

Status: implemented foundation, not a complete operational application or a
production release. Reviewed 2026-10-02.

This document describes the lifecycle and version-one foundation. Migration two
adds immutable document input/snapshot/calculation metadata, explicit stock-pool
and valuation-basis fields, and a monotonic posting sequence. Operational services
and their current limits are documented in `TRANSACTION_SERVICES.md`,
`MASTER_SERVICES.md`, `BACKUP_RESTORE.md`, and `NATIVE_WORKSPACE.md`.
Migration 4 adds distinct cash trade kinds through a backed-up table rebuild;
earlier checksums, posted rows and references remain unchanged. Its bounded
foreign-key suspension, recovery tests and operator workflow are documented in
[CASH_TRADES.md](CASH_TRADES.md). Application sessions still enforce foreign keys.

The owner chose **correct new transactions while preserving historical values**.
`bifma/native_schema.py` therefore creates a separate native database. It does not
modify V2, rewrite the recovery archive, reinterpret historical postings, or
import historical passwords. Historical compatibility reports remain separate.

## Public lifecycle contract

```python
from bifma.native_schema import (
    initialize_database, migrate_database, open_native_database,
    native_session, native_transaction, require_native,
)
```

| API | Behavior |
| --- | --- |
| `initialize_database(path)` | Requires a nonexistent destination in an existing directory; builds a private temporary database, applies migrations in one transaction, validates it, then publishes without replacing any file |
| `migrate_database(path)` | Performs marker-checked startup/recovery for a known native version, then backs up before applying pending migrations in one transaction; a healthy already-current file remains unchanged |
| `open_native_database(path, require_current=True)` | Explicit native startup: checks the existing file's raw marker before writable SQLite access, permits SQLite crash recovery, then validates lineage/current version and integrity; returns the verified schema version, never creates or migrates |
| `native_session(path, readonly=True)` | Opens an existing current native database, validates marker/history, registers decimal functions, and closes the connection on exit; never creates, seeds or migrates data |
| `native_transaction(path)` | Acquires an immediate write transaction, validates the current native schema under that lock, and commits or rolls back the entire service operation |
| `require_native(connection, require_current=True)` | Performs marker, version/checksum and connection-setting validation on an existing connection; returns the schema version |
| `prepare_connection(connection)` | Registers exact-decimal aggregate support and enables connection-local recursive trigger guards; does not initialize, migrate or write database content |

Initialization and migration return
`MigrationResult(path, version, applied_versions, backup_path)`. A new/current
database has `backup_path=None`; a successful upgrade returns its retained backup.
An up-to-date check returns an empty `applied_versions` tuple.

SQLite 3.37 or newer is required for `STRICT` tables. Runtime validation used
SQLite 3.37.2. The database has both `PRAGMA application_id=0x4246544E` and a
`native_meta` application marker. Every connection used by native services must
enable foreign keys and recursive trigger guards. SQLite foreign keys are
connection-specific, so a table declaration alone does not enable enforcement.
See [SQLite foreign key documentation](https://www.sqlite.org/foreignkeys.html).

Fresh databases contain no users, companies, permissions or business documents.
Bootstrap administration and company/master setup are separate explicit services.
`initialize_database` refuses even an empty existing file; a V2/archive/unknown
SQLite file cannot be accidentally adopted by supplying its path.

## Native startup and crash recovery

`SecurityService` startup invokes `open_native_database` before readonly schema
validation. The native UI creates that security service before checking users;
master and backup services also construct their default security service first.
An interrupted transaction can leave a hot rollback journal that needs physical
writes to restore the last committed state. Startup permits that normal SQLite
recovery. Ordinary `native_session(readonly=True)` remains readonly and never
falls back to a writable connection.

The opener first holds a readonly descriptor to a regular file and checks its
100-byte SQLite header: the initial magic string and four-byte big-endian native
application ID at offset 68. These locations follow SQLite's
[database file format](https://www.sqlite.org/fileformat.html#the_database_header).
Only then is the same path opened using SQLite URI `mode=rw`, which cannot create
a missing file; see [SQLite URI modes](https://www.sqlite.org/uri.html).
SQLite owns locking and rollback under its
[hot-journal recovery procedure](https://www.sqlite.org/lockingv3.html#dealing_with_hot_journals).
No journal/WAL file is manually removed, and no recovery data is guessed.

Final symlinks, nonregular files and hard-link aliases are rejected. The raw
descriptor stays open until the SQLite connection closes; device/inode identity
is rechecked around open and validation to detect path replacement. The header
marker is a format filter, not authentication: committed native metadata,
migration checksums/version and full integrity/foreign-key checks must still
pass. This does not protect against an operating-system actor who controls the
database directory or directly edits its files. Use one stable native path and
keep the database with its SQLite sidecars; target filesystem/Windows behavior
still requires qualification.

Raw descriptor lifetime is coordinated with every `database.session` in the
process. SQLite documents that a raw descriptor's close can cancel another
connection's [POSIX advisory locks](https://www.sqlite.org/howtocorrupt.html#posix_advisory_locks_canceled_by_a_separate_thread_doing_close_).
An identity-based lifecycle gate waits for other threads' managed sessions to
finish, then excludes new sessions during startup. Ordinary sessions remain
concurrent. Existing operations can finish nested managed sessions, including
backup reads, without waiting behind startup.

Construct services and start migrations **outside transaction/session scopes**;
construct them first and reuse them. Startup inside any managed session/startup
in the same thread is rejected, including across different files. The inverse
is also rejected: startup cannot enter a managed session, even on another file.
These rules avoid nested and cross-file deadlocks. Bare `sqlite3.connect` or
`database.connect` handles are outside this managed lifecycle: operational code
must use `database.session` or the native session/transaction wrappers. Reviewed
application exceptions use private unpublished files for initialization and
backup staging, or the direct finally-closed connection inside exclusive startup.
Moving/replacing an active database or separating it from its sidecars remains
unsupported; the gate's inode key also detects the demonstrated renamed-file
bypass rather than releasing an existing transaction's lock.

The low-level native opener changes no application rows and performs no implicit schema upgrade.
`require_current=False` permits checking an older known native version so an
explicit upgrade can be planned; it does not apply that upgrade. The explicit
migration entry point invokes that recovery check before reading the old version
or taking its pre-upgrade backup, so an interrupted old database can be upgraded.
Restore keeps
reading its source backup readonly: it does not invoke writable recovery on the
source. See [PROCESS_RECOVERY.md](PROCESS_RECOVERY.md) for Linux subprocess
evidence and remaining release limits.

## Migration contract

`MIGRATIONS` is an ordered tuple of frozen
`Migration(version, name, statements)` specifications. Versions are contiguous
from 1. The SHA-256 checksum covers the version, name and exact statement tuple.
`schema_migrations` stores those values and an application timestamp; its rows
cannot be updated or deleted through supported native connections. Released
migration specifications must never be edited: append a new migration instead.
`PRAGMA user_version` must agree with the recorded migration history.

Migrations execute individual statements, not `executescript`, so they remain
inside the caller's transaction. Migration specifications cannot issue their own
transaction commands or attach other databases. Validation rejects missing,
reordered, unknown/newer, renamed or checksum-mismatched migration history.

For an upgrade, the manager acquires `BEGIN IMMEDIATE`, revalidates the version,
then invokes the existing `bifma.database.backup_database` helper before changing
any pages. The helper reads a separate connection's committed snapshot while
the reserved write lock prevents another writer from changing the source. The
backup is integrity-checked and published to a new private file named like
`native.preupgrade-v1-to-v2-<random>.sqlite3`. It is never overwritten. The lock
and snapshot behavior follow [SQLite transaction semantics](https://www.sqlite.org/lang_transaction.html).

If backup creation fails, no migration runs. If any pending migration or final
integrity check fails, every pending change is rolled back together and the
pre-upgrade backup remains available. Backups contain confidential application
data. Restore and backup-retention interfaces are still separate release work.
Windows publication/restore behavior needs target-environment qualification.

## Migration 3: contact and tax identifier storage

The contact-data increment introduced native schema **3**. Migration
`master_contacts_and_tax_identifiers` appends optional TEXT columns to companies
and accounts. It does not change migration 1 or 2, any accounting/stock rule,
posted document or issued snapshot. Their original specification checksums are
pinned by `tests/test_master_contacts.py`:

| Version | Checksum |
| --- | --- |
| 1 | `4fe764da2b83db268aafaad4d191f5bbc2840bc7bd141f24f64205d96b96c462` |
| 2 | `ec12d8a891bf47ad17e910144f5bff74c64a43b9dd900869b5abf7f3ed60245a` |

The source-supported field mapping is:

| Native field | Original `CONFIG` company field | Original `CHART` account field | Maximum characters |
| --- | --- | --- | ---: |
| `address` | `CMPADDRESS` | `ADDRESS` | 500 |
| `address_urdu` | `CMPADDRESS_URDU` | Not added to accounts | 500 |
| `phone` | `PH_NUM` | `PHNO` | 80 |
| `fax` | `FAX_NUM` | Not added to accounts | 80 |
| `mobile` | Not added to companies | `MOBILE` | 80 |
| `email` | `EMAIL` | `EMAIL` | 254 |
| `website` | `WEB` | `WEBSITE` | 2,048 |
| `sales_tax_number` | `GST_NUM` | `STAX_NUM` | 80 |
| `national_tax_number` | `NTN_NUM` | `NTN_NUM` | 80 |

These declarations are in the recovered [company schema](legacy_schema.sql#L30)
and [account schema](legacy_schema.sql#L21). Modern length bounds allow longer
text without inferring regional syntax. All columns permit NULL and have no
non-null default. SQLite constrains non-null values to nonempty, bounded text
without NUL characters; the master service also trims/NFC-normalizes text and
rejects embedded controls, surrogates and non-string inputs. TEXT affinity alone
cannot establish whether a caller originally supplied a numeric value, so the
service performs type rejection before persistence.

Tax identifiers are stored as supplied text, preserving leading zeros and case.
No tax registration validation, tax computation, automatic discount behavior,
SMS credentials, alternate company names/addresses or aliases are introduced.
No original contact records are imported or inferred.

Version-2 files require `migrate_database(path)` explicitly. Startup/read/service
creation never applies the migration. The usual verified pre-upgrade backup
retains version 2; the 15 added columns appear together in the atomic upgrade,
initially NULL for every existing record. Failure rolls back all columns and
retains the pre-upgrade backup. Document/journal rows and document metadata are
unchanged byte-for-byte at the SQL value level; existing snapshots are never
filled from newer master values. Fresh databases apply migrations 1, 2 and 3.

`tests/test_master_contacts.py` verifies version-2 data and posted snapshots,
backup lineage, original checksums, explicit-upgrade refusal and rollback after
all contact ALTER statements if a later statement fails. Tests use temporary
synthetic databases only. Read [MASTER_SERVICES.md](MASTER_SERVICES.md) for the
authorized editing and lookup contract.

## Shared masters and company dimensions

All entity IDs are lowercase dashed UUID strings. Original display/business
codes remain separate unique text fields, preserving leading zeros. Timestamps
for native users, sessions, creation/update and audit use integer UTC epoch
seconds. A native document's business date is a validated `YYYY-MM-DD` string,
separate from the historical archive's original naive timestamp strings.

| Tables | Purpose and boundary |
| --- | --- |
| `companies` | Code/name, optional Urdu name/contact/tax-identifier text, active flag, optional fiscal dates and configured cash account |
| `cities`, `accounts` | Shared chart/city masters; accounts have a parent, postable flag, active flag, native kind, optional statement classification and contact/tax-identifier text |
| `units`, `product_groups`, `products` | Shared products/groups/units, large/small unit references, conversion factor, rates and purchase/sale/inventory account mappings |
| `stores` | Shared store master, independent of a single owner company |
| `company_stores` | Explicit company/store membership; the same store may belong to multiple companies |

Company/store membership accommodates the recovered evidence that some historical
transactions reference stores assigned elsewhere. No memberships are inferred or
imported automatically. A future reviewed migration must decide and record them.
Company-specific documents and journal entries have composite unique keys;
their children use composite foreign keys, preventing a child from claiming one
company while referencing another company's parent. Shared account/product
references remain global by design.

The starter schema blocks self-parenting. Longer master hierarchy cycles,
active-master use and fiscal closing policies still require service validation;
the original chart coding and period workflows are not fully known.

## Authentication and authorization storage

This contract is shared with `bifma/security.py`:

| Table | Columns |
| --- | --- |
| `users` | `id`, canonical lowercase ASCII `username`, `display_name`, encoded `password_hash`, `is_admin`, `is_active`, `failed_attempts`, nullable `locked_until`, `password_changed_at`, `created_at`, `updated_at` |
| `user_company_grants` | Composite primary key `user_id, company_id` |
| `user_menu_grants` | Composite primary key `user_id, module`; `can_read`, `can_create`, `can_update`, `can_delete`, `can_post` |
| `auth_sessions` | `token_hash`, `user_id`, `issued_at`, `expires_at`, `last_seen_at`, nullable `revoked_at` |
| `audit_events` | `id`, nullable `actor_id`, nullable `company_id`, `action`, `entity_type`, nullable `entity_id`, `occurred_at`, object-valued `details_json` |

Usernames are 1–80 characters from lowercase ASCII letters, digits, underscore,
dot and hyphen. All listed timestamps are integer UTC epoch seconds. Users default
to inactive and nonadmin; menu flags all default to zero. No company access is
granted merely by creating a user. Module grants apply across explicitly granted
companies; the security service defines the explicit administrator bypass.

Raw session tokens are not persisted. Authentication services must validate the
stored session and current user/company/module grants on the same transaction
used for a protected write. An actor UUID alone is not authentication. Password
hashing, lockout, idle/absolute expiry, revocation, last-admin protection, and
secret-free audit metadata belong to the security service, not bare SQL defaults.

The owner's 2026-10-04 login instruction removes the 15-character password
minimum. New passwords may contain 1–1,024 NFC characters; blank/whitespace-only
values are rejected and the existing common/context denylist remains. No stored
password hash is rewritten. `login_profile(username)` exposes only the active
user's saved display name and active company choices for the requested login
screen. It creates no session and changes no attempts, audit or grants; password
authentication and current company access checks are still required.

Migration 6 rebuilds only `users` to reduce its username minimum from three to
one. All original user columns and rowids, child references and migration 1–5
checksums are retained. Foreign keys are temporarily suspended only on the
explicit migration connection before acquiring its write transaction, then
checked before commit and restored on every exit. Normal application startup
calls `prepare_native_login` (or `ensure_baseline(..., upgrade=True)`), which
permits this schema-5 upgrade after verifying native lineage and the sealed
baseline/source association under the migration write lock. A verified backup
precedes changes. Current files remain unchanged; older versions retain their
manual upgrade path. Service constructors continue to require current schema.

Audit events are append-only, and actor foreign keys do not cascade on user
deletion. Disable users instead of deleting identities referenced by history.
Supported connections enable recursive triggers so `INSERT OR REPLACE` cannot
silently remove protected rows while bypassing their delete guards. Trigger
protection is an application/database integrity boundary, not protection against
a filesystem administrator who can alter schema or disable connection settings.
See [SQLite trigger documentation](https://www.sqlite.org/lang_createtrigger.html).

## Document/posting contract

| Table | Main fields |
| --- | --- |
| `documents` | `id`, `company_id`, `kind`, `document_number`, `business_date`, optional `account_id`, `status`, `revision`, `total_amount`, `rounding_policy`, `posting_places`, notes, creator/timestamps, poster/timestamp, optional `reverses_document_id`, optional legacy source/table/ID references |
| `document_lines` | `id`, `company_id`, `document_id`, `line_number`, `product_id`, `store_id`, `quantity`, `rate`, `discount_percent`, exact unrounded `net_amount`, optional `source_line_id` |
| `journal_entries` | `id`, `company_id`, unique `document_id`, description |
| `journal_lines` | `id`, `company_id`, `entry_id`, `line_number`, `account_id`, `debit`, `credit`, narration |
| `stock_movements` | `id`, `company_id`, `document_id`, `document_line_id`, `product_id`, `store_id`, `direction`, `quantity`, `unit_cost`, exact `value`, optional `reverses_movement_id` |

Supported kind identifiers are `sale`, `purchase`, `sale_return`,
`purchase_return`, `breakage_receive`, `breakage_dispatch`, `delivery_challan`,
`stock_opening`, `stock_transfer`, `stock_adjustment`, `journal`, `cash_receipt`,
`cash_payment`, `bank_receipt`, `bank_payment`, `opening_balance`, and `reversal`.
These are native identifiers covering the recovered scope, not a claim that every
original form is implemented. Bank vouchers and challans still lack confirmed
original menu workflows.

A posting service must assemble one complete document in `native_transaction`:

1. Validate authentication/authorization, active company/masters, business policy
   and the expected draft revision while holding the write lock.
2. Insert/edit the draft, detail lines, any journal and stock movements using
   exact validated decimal text. Record the required audit event.
3. Set `status='posted'`, poster/timestamp, and advance `revision` by one as the
   last document change. Let the transaction commit all effects together.

The database requires:

- Documents start as drafts. Their identity/company cannot be reassigned.
- Every document update advances `revision` by exactly one. Services use
  `WHERE id=? AND revision=?` and reject a zero row count to detect stale edits.
  A service editing child rows must also advance the parent revision atomically.
- A nonzero financial document has a journal. Explicit stock-only kinds
  (`delivery_challan`, `stock_transfer`, `stock_adjustment`) may omit one.
- Zero-value goods documents may omit a journal. No fake accounting entries are
  required for free goods. Cash/voucher-specific positive-total policy is a
  service responsibility.
- Every journal that exists has at least two lines when posted. Each line has
  one positive side and one zero side, uses a postable account, and total debits
  equal total credits exactly. Journal line numbers may reach 20,000, allowing
  distinct account postings for all 10,000 invoice lines plus the counterparty
  and adjustment entries. Document item line numbers remain capped at 10,000.
- Company/store membership and the stock movement's document/line/product origin
  agree through foreign keys. Quantities are positive; direction is `in`/`out`.
- Posted documents and all their lines, journals and movements cannot be added
  to, updated, deleted or replaced. Draft origins are also immutable, although
  draft values/components may be edited before posting.
- Reversal references stay within a company and point to a posted document.
  A document/movement can be explicitly reversed at most once. Ordinary partial
  returns use source-line references rather than that full-reversal key.

`posted_journal_lines` and `posted_stock_movements` expose only effects belonging
to posted documents. Reports must not sum all base rows and include drafts.
Existing posted rows never change to a “cancelled” state; correction services
must write linked reversing transactions instead.

The schema does not guess the monetary formulas, journal accounts or stock
quantities/directions for each document kind. Balanced sums alone do not prove
correct business posting. Services still must verify document/line totals, exact
reversal contents, payment allocation, inventory availability, return limits,
delivery conversion and stock valuation policy. There is no operational posting
service implied merely by these tables existing.

## Exact numeric storage

Numeric business values are unsigned canonical decimal `TEXT`. Examples are
`0`, `12`, `0.25` and `9007199254740993.01`. No exponent, leading plus/minus,
leading integer zeros, trailing fractional zeros or grouping characters are
stored. Negative net movements/balances are represented by direction/debit/credit
and calculated exactly by services/reports.

| Field family | Maximum integer places | Maximum fractional places |
| --- | ---: | ---: |
| Entered quantity, rate, unit factor, unit cost | 18 | 6 |
| Discount percentage | 3, with value restricted to 0–100 | 6 |
| Posted document/journal money | 64 | 2 |
| Exact unrounded line net and stock value | 64 | 24 |

These are explicit native limits, not inferred historical Oracle limits.
`rounding_policy` records subtotal rounding (`cents` or `legacy_whole`). The
separate, required `posting_places` records final journal/document rounding after
header adjustments and must be 0 or 2. Neither has an implicit default: whole-unit
subtotal rounding can coexist with final cents, or vice versa, as an explicit
caller decision. Services must apply both recorded choices before persistence.
The schema never rounds an amount or translates an old discount.

Built-in SQL checks validate representation without converting values to `REAL`.
`native_decimal_sum`, registered on every native connection, performs exact
Decimal summation with an independent precision context for posting balance
checks. A connection without that aggregate cannot post a document successfully.
The shared backup primitive needs no aggregate to run integrity checks because
the aggregate appears only in posting triggers, not in table `CHECK` clauses.

SQLite TEXT affinity can convert a bound Python float into text before checking
a column. The schema therefore **cannot identify and reject every originally
bound float**. Services must reject floats before persistence and use the exact
calculation/serialization API. The tests demonstrate large values and decimal
sum equality without float arithmetic; they do not make a stronger affinity claim.

## Verification and remaining work

Run `python3 -m unittest tests.test_native_schema tests.test_master_contacts -v`. The suite covers new-file
creation/refusal, no writes on reads, backup publication races, backup-before-
upgrade writer exclusion, all-migration rollback, failed-backup refusal, migration
checksums, privilege defaults, company/store boundaries, decimal/date validity,
balanced posting, zero-value goods, immutable posted origins/audit including
replacement attempts, same-company reversal references, revisions and atomic
rollback on an unbalanced posting. Tests use only synthetic temporary databases.

Separate services now implement master maintenance, company setup, supported
posting, permissions, reports, native HTML output and new-file restore. Migration
two records issued labels, unit factors, header adjustments and calculation
snapshots; migration three adds optional contact and tax-identifier storage.
These components do not complete migration from the legacy archive,
original print parity, every original workflow, tax/labour/payment allocation or
inventory valuation policies. Approval for corrected new transactions does not
settle those unspecified details.

The original application/forms and reports, target Windows installation/upgrade
and printing checks, reconciliation, backup restoration, concurrency/failure
testing and business acceptance remain production-release gates.
