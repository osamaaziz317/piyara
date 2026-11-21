# Legacy data recovery

Reviewed on 2026-10-02. The supplied `BT-270926.EXE` is an original Oracle
Export dump with an `.EXE` filename. It is not a Windows program: its first
bytes contain `EXPORT:V10.02.01`, and it contains 30 table definitions, scalar
row payloads, constraints, and database functions. No stored triggers were found.
The local decoder does not execute code from the file. A separate, isolated
Oracle comparison imports reviewed tables and independently checks values.
This investigation did not modify the source dump or
`bifma_traders_v2.db`.

## Exact source identification

- Size: 7,299,072 bytes.
- SHA-256: `d2cd806aa644320cf6d2eaa6192e7d6c840adcde8226f62b5ad1422980cc7e9e`.
- Header timestamp text: 2026-09-27 15:28:47. The dump does not establish a
  timezone for this text.
- Scalar types observed: `NUMBER`, `DATE`, `VARCHAR2`, and `CHAR`.
- Character descriptors consistently contain charset ID 871 and form 1. Every
  observed text field decodes strictly as UTF-8 BMP text and reconstructs the
  original bytes without replacement or trimming.

## What the local decoder proves

`tools/recovery_oracle.py` is a strict, read-only, source-specific decoder. Run:

```bash
python3 tools/recovery_oracle.py BT-270926.EXE
python3 -m unittest discover -s tests -p 'test_recovery_oracle.py' -v
```

The command prints metadata and counts only. The count-only result is retained
in `docs/recovery_audit.json`. No passwords, account names, contact details,
invoice contents, or individual business records are included in that report.

The complete reviewed file yields **30 tables and 93,110 rows**. Every table's
binary column count, type, size, order, and bind positions match its text DDL.
All rows respect source `NOT NULL` declarations and declared byte lengths.
Every nonnull scalar passes decode/re-encode comparison with its original bytes.
All row separators and table terminators are consumed without resynchronization,
skipping fields, or scanning past a decoding failure.

The format observed in this file is a little-endian 16-bit column count,
type/length descriptors, a four-zero-byte preamble, length-prefixed scalar fields,
a two-zero-byte separator after each row, and a `0xffff` table terminator. A
field length of `0xfffe` denotes SQL NULL. These framing observations are
reverse-engineered from this specific export, not a supported Oracle interchange
specification. The default parser rejects any source with a different checksum.

Oracle documents the internal base-100 `NUMBER` representation and seven-byte
`DATE` representation. The decoder uses exact `Decimal` construction; an
independent rational-number encoder checks the binary representation. Dates keep
the original time of day. `CHAR` padding and text bytes are retained.
([Oracle OCI data types](https://docs.oracle.com/en/database/oracle/oracle-database/19/lnoci/data-types.html))

Selected recovered counts:

| Table | Rows | Meaning established by schema/menu evidence |
| --- | ---: | --- |
| `PUR_ITEM` | 6,207 | Item master |
| `CHART` | 703 | Account chart |
| `OPEN_STOCK` | 1,436 | Opening stock |
| `SMST` / `SDTL` | 3,413 / 55,405 | Sales headers / details |
| `JMST` / `JDTL` | 1,771 / 19,825 | Journal headers / details |
| `BRMST` / `BRDTL` | 436 / 3,563 | Breakage receive headers / details |
| `BDMST` / `BDDTL` | 1 / 6 | Breakage dispatch headers / details |
| `PMST` / `PDTL` | 0 / 0 | Purchase headers / details |
| `DCMST` / `DCDTL` | 0 / 0 | Delivery challan headers / details |
| `UM_MENU` | 46 | Menu definitions |

Exported optimizer statistics are stale in several growing tables. For example,
the stored estimates for `PUR_ITEM`, `SMST`, and `SDTL` are 5,739, 3,252, and
54,828 respectively. Those statistics are **not** an independent count of the
exported rows and must not be used as the migration acceptance baseline.

## Streaming API for isolated staging

```python
from tools.recovery_oracle import read_dump

dump = read_dump("BT-270926.EXE")
for table in dump.tables.values():
    columns = table.columns
    for row in table.iter_rows():
        # row is an ordered dict: column name -> Decimal, ISO date string,
        # original text, or None. Consume all rows to validate the terminator.
        stage_without_logging_values(table.name, columns, row)
```

Use a new staging file and preserve legacy table/column names and identifiers.
Store Oracle numbers as exact decimal strings with SQLite TEXT affinity, not
REAL or NUMERIC affinity. Preserve NULL distinctly from text. The application
must not point at staging by default. Access to recovered user/security tables
must not become an automatic authentication mechanism for the replacement app.

The staging process must compare source and reread SQLite row digests, counts,
primary/unique constraints, required fields, and foreign keys. It must fail
atomically, refuse to overwrite an existing output, and label its result
`unverified` until the independent comparison below is complete. These are
acceptance requirements, not claims made by this read-only decoder.

## Independent reconciliation

The supported recovery path uses Oracle's original `imp` utility. Its version
must be at least the export version and must match the target database version.
`SHOW=y` displays SQL without importing; an original Export file requires
original Import. Restore into an isolated Oracle database after reviewing its
metadata. ([Oracle original Import documentation](https://docs.oracle.com/en/database/oracle/oracle-database/19/sutil/oracle-original-import-utility.html))

On 2026-10-02, original Import in an isolated official Oracle 26ai Free container
loaded all 30 source tables. A SQLPlus transport probe checked exact numbers,
dates, text and NULLs. Independent counts and typed row multisets match all
93,110 rows / 1,424,337 fields and all 302 column definitions. No missing or extra
rows were found. Import warning IMP-00403 referred to an empty generated SQL
file; no deferred statements or rejected row counts were found. See
[`ORACLE_RECONCILIATION.md`](ORACLE_RECONCILIATION.md) for isolation, current
evidence and reproducible tooling, and [oracle_reconciliation.json](oracle_reconciliation.json)
for the source/archive hashes and per-table results. The initial staging
manifest remains unchanged; the independent result is a separate attestation.

The completed table check and remaining acceptance work are:

1. **Passed:** restore the reviewed dump into a disposable Oracle database using original
   Import. Retain a private import log and investigate every rejected row or
   object. Do not restore over an operational database.
2. **Passed:** obtain actual `COUNT(*)` results and deterministic per-table typed row
   comparisons from Oracle. Compare every table and field with SQLite staging,
   including NULLs, decimal precision, Urdu text, padded CHAR values, and times.
   Import-log counts are useful corroboration; optimizer estimates are not.
3. **Partial:** historical STOCK, STOCKB and LEDGER view rows and 37 aggregate/
   filter scenarios now match Oracle; source key relationships passed the archive
   audit. Mapping historical cutover into the operational application still needs
   explicit rules and retained source identifiers.
   Any discrepancy blocks promotion and must remain visible in the report.
4. **Remaining:** compare original screens, entry behavior, print layouts, permissions, rounding,
   and reports with the replacement. Database recovery alone cannot prove
   a carbon copy of the application or production readiness.

The probe deliberately rejects unreviewed export variants, unsupported types,
BC dates, supplementary-character encodings, and malformed/noncanonical numeric
values. No fallback silently repairs source values. Exact local round-trips are
strong evidence of complete scalar recovery from this file; they do not replace
an independent Oracle import or prove original application behavior.
