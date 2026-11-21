# Independent Oracle reconciliation

Status on 2026-10-02: **all source table values, three reconstructed historical
views and 37 selected report scenarios independently match Oracle**. The
recovered archive and original dump remain unchanged. This verifies the
specified historical data and calculations, not complete application parity or
production readiness.

The source is `BT-270926.EXE`, an original Oracle Export V10.02.01 dump, SHA-256
`d2cd806aa644320cf6d2eaa6192e7d6c840adcde8226f62b5ad1422980cc7e9e`.
The comparison target is the separate `data/legacy_recovered.sqlite3` archive.
Its SHA-256 is
`d5883f726438192e9e5331cb1b70ae2d4b081d8dcf72a8b2770cb692a92d83be`.

## Results and their scope

| Comparison | Result | Evidence |
| --- | --- | --- |
| Source tables | 30 tables, 302 columns, 93,110 rows and 1,424,337 fields match; zero schema differences or missing/extra rows | [Table reconciliation](oracle_reconciliation.json) |
| Original `STOCK` view / Python movements | 56,752 rows × 17 columns match | [View parity](oracle_report_parity.json) |
| Original `STOCKB` view / Python movements | 3,475 rows × 17 columns match | [View parity](oracle_report_parity.json) |
| Original `LEDGER` view / Python entries | 28,525 rows × 9 columns match | [View parity](oracle_report_parity.json) |
| Independent filtered/aggregate SQL / Python report APIs | All 37 selected scenarios match, including separate opening, period and closing components | [Aggregate parity](oracle_aggregate_parity.json) |

All comparisons preserve duplicate multiplicity, exact numeric values, text,
NULLs and date seconds. They apply no tolerance, financial correction or
rounding normalization. Source anomalies, including both inward breakage arms,
remain part of the historical compatibility behavior.

Both source companies and a confirmed absent company were checked. Company
coverage includes identifiers from configuration, opening stock and every
relevant source header, as well as Oracle-emitted view rows. This prevents an
empty Oracle company from hiding an extra Python result.

The original Import exited successfully and its per-table counts match all
93,110 rows. It emitted `IMP-00403`; the generated deferred SQL file was inspected
and contained **zero bytes and zero statements**. Nothing from that file was
executed. The warning review, empty-file hash and matching import counts are
recorded in the table report. Oracle describes that code as deferred DDL requiring
privileged review. ([Oracle IMP-00403](https://docs.oracle.com/en/error-help/db/imp-00403/))

These are dataset-specific results. `PMST` and `PDTL` contain no source rows and
`PUR_VW_DTL` returns zero rows, so purchase paths absent from this backup are not
qualified by these comparisons. All six original views were exported, but only
the three final views with Python counterparts were directly compared.
`PUR_VW_DTL` and `SALE_VW_DTL` feed the compared ledger; `DCTOSALE_BAL` has no Python
counterpart. Original GUI behavior, report templates, printing, operational
migration and production acceptance remain unverified.

## Isolation and observed environment

The first attempt used Oracle's official `database/free:latest-lite`, image
digest `sha256:cf540c3fa190d7cffad08c491652ac07fc70314e510f6b87890449517d565e94`.
It exposed two concrete prerequisites before any source import:

- Original Import (`$ORACLE_HOME/bin/imp`) is absent from this Lite image.
- A namespace containing only loopback caused Oracle startup errors
  `ORA-00600 [ksipc: no private ips avail for use]` and `ORA-29701`.

That disposable container and its owned anonymous volume were removed. A second
Lite container proved the isolated dummy-device approach: Oracle became healthy
and the SQLPlus synthetic transport probe passed on Oracle AI Database 26ai Free
23.26.3.0.0, using AL32UTF8. That test container was also removed. The successful
source import and comparisons used the full official
`container-registry.oracle.com/database/free:latest` image, pinned to digest
`sha256:f988b0c04c4c386cd306a2a914c0d7a9702d83acc31b064a28ad8eb6278a8fba`.
Its database is Oracle AI Database 26ai Free 23.26.3.0.0, with AL32UTF8 database
character set and AL16UTF16 national character set.

`tools/oracle_reconcile.py` creates a uniquely named, labeled container with a
read-only source-file bind, no published ports, Docker network mode `none`, one
CPU, 3 GiB memory/swap limit, and 1 GiB shared-memory limit. Passwords are randomly
generated into a private temporary directory and never passed as visible command
arguments or printed. Other containers, networks and volumes are not modified.

Oracle requires a non-loopback IPv4 interface for its internal discovery. The
runner adds a disconnected dummy device inside its isolated network namespace,
using the container-scoped `NET_ADMIN` capability. There is no peer, bridge, host
network attachment or default route. Before database configuration/import/export,
the runner checks the interface types and rejects an available external route.
Docker documents both the [none network driver](https://docs.docker.com/engine/network/drivers/none/)
and [using NET_ADMIN to create a dummy device](https://docs.docker.com/engine/containers/run/).

## Independent comparison method

The phases are explicit: prepare, start, status, configure, import, export,
compare and cleanup. The runner does not start or restart downloads. An already
downloaded image is pinned by immutable image ID. Container identity, labels,
mounts and resource/isolation settings are checked before operations.

Original `imp` imports only the 30 named source tables into a disposable schema.
It does not request application functions, procedures, views, grants, indexes or
optimizer statistics. Import return codes, Oracle error codes and row counts
are retained for review. Oracle documents the use of original Import for
original Export files, including table selection and schema remapping.
([Oracle original Import](https://docs.oracle.com/en/database/oracle/oracle-database/19/sutil/oracle-original-import-utility.html))

That description applies to this checksum-pinned artifact, whose reviewed
source has no triggers. It is not a promise that table-mode Import from an
arbitrary dump can never include executable objects. The initial imported
schema was independently checked to contain exactly 30 table objects before
the separately reviewed report definitions were installed.

SQLPlus exports all columns in their source order within one read-only
transaction. Oracle numbers use `TO_CHAR(...,'TM9')` with an explicit decimal
separator, dates use second-precision ISO text, and text is passed directly to
`JSON_ARRAY`. `NULL ON NULL` preserves every column position; `RETURNING CLOB`
avoids the default small JSON result limit. JSON escaping handles control
characters and quotes. Source metadata bounds each possible row below the
SQLPlus line limit. A synthetic transport probe independently checks a number
above 2^53, a 38-digit integer, a small decimal, CHAR padding, newline/quote/slash
escaping, Urdu, NULL and a non-midnight date before any comparison can pass.
([Oracle JSON_ARRAY](https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/JSON_ARRAY.html),
[Oracle number/date format models](https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/Format-Models.html))

`tools/oracle_compare.py` does not import the dump decoder or application
calculations. It reads Oracle JSON and the SQLite archive in read-only mode,
canonicalizes numeric spelling using exact Decimal values, and preserves date,
text and NULL representations. Each table is compared as a multiset of SHA-256
digests of typed column arrays; duplicates retain their multiplicities. It also
compares independent Oracle `COUNT(*)` values and all source column definitions.
A changed value, missing/extra row, lost duplicate, reordered column, precision
loss, truncated JSON or schema mismatch prevents a complete match.

The public results contain counts, fingerprints, per-table/view/case digests,
error codes and provenance only. Private exports and logs can contain confidential
records, including recovered legacy credential fields, so cleanup removes them
and generated credentials. Cleanup checks ownership before removing the one
container and its anonymous volume. Source and archive hashes are rechecked.

Cleanup completed on 2026-10-02. The owned source-verification container and its
anonymous volume were removed with `docker rm --force --volumes`; an independent
run-label listing returns no container. All private exports, selector files,
logs and generated credentials were removed. Each public report records
`cleanup_complete: true` and confirms that source/archive hashes are unchanged
after cleanup. Downloaded official images remain cached; other containers and
volumes were not altered.

## Historical calculation checks

`tools/oracle_reports.py` installs only six views and their one required
`GET_PUR_RATE` function from an exact SHA-256 allowlist. The function was reviewed
independently by two agents: its statements select data and return values; there
is no data modification, dynamic SQL, package/network call or autonomous
transaction. No other recovered function was installed. Compilation errors
prevent export. The reviewed source definitions execute in the disposable
schema, and their results are read within a read-only transaction.

Every original view field is mapped to its Python reconstruction and compared
as a typed row multiset. Extra Python presentation/provenance fields are excluded.
The report records the precise reviewed SQL and tested Python source hashes.

`tools/oracle_aggregates.py` independently writes Oracle `SUM`/`CASE`/`WHERE`
queries over the original views. Its 37 scenarios cover all-time and bounded
date periods, exact timestamp boundaries, store/product selection, literal
account prefixes, combined filters, filtered movement/ledger rows and an absent
company. Opening amounts are strictly before the lower bound; date-only upper
bounds include 23:59:59. Debit/credit and inward/outward components remain
separate, groups with zero closing values remain present, and NULL grouping
values are preserved. Product names use a master join whose source key
uniqueness was independently verified. A separate agent reviewed the SQL,
mapping, date boundaries, signs and comparison normalization without finding a
concrete defect in the selected-data methodology.

Only scenario labels and filter dimensions are public. Actual company, product,
account selectors and exported business rows remain private and are removed by
cleanup. SQLPlus diagnostics are checked even when SQLPlus exits with status
zero, since `WHENEVER SQLERROR` alone does not catch SQLPlus command errors.
([Oracle SQLPlus error handling](https://docs.oracle.com/en/database/oracle/oracle-database/12.2/sqpug/WHENEVER-SQLERROR.html))

Synthetic verification:

```bash
python3 -m unittest discover -s tools -p 'oracle_test_compare.py' -v
```

The 17 synthetic tests pass, including precision/text/NULL loss, duplicate loss,
invalid counts, interrupted-cleanup recovery, foreign container rejection and
SQLPlus errors with a zero process exit status. These establish comparator
behavior; the three separate reports above record the actual Oracle results.

The verification phases are intentionally separate: `oracle_reconcile.py` owns
table recovery and lifecycle, `oracle_reports.py` owns reviewed source view
installation/export/comparison, and `oracle_aggregates.py` owns selected report
scenario export/comparison. Each comparison returns a failing process status
when any compared result differs. Public reports do not contain source records
or credentials and do not change the archive's initial recovery metadata.
