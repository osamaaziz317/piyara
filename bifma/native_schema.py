"""Explicit lifecycle and versioned schema for NEW operational SQLite databases.

This module never promotes the historical archive or V2 database. Initialization
publishes a new private file; upgrades verify their lineage, create a pre-upgrade
backup under the write lock, and commit all pending migrations atomically.
Ordinary native sessions only validate the schema and register decimal functions.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
from threading import local
from uuid import uuid4

from bifma.database import DatabaseError, backup_database, exclusive_file_access, new_destination, publish_new_file, session


APPLICATION_ID = 0x4246544E  # BFTN, distinct from V2 and the Oracle recovery archive.
APPLICATION_NAME = "bifma_traders_native"
MIN_SQLITE_VERSION = (3, 37, 0)  # STRICT tables.

_startup_scope = local()
_UNVERIFIED = object()


@contextmanager
def startup_verification():
    """Share unchanged-file checks during one startup, never between startups.

    Nested service constructors on this thread share the outer scope. Another
    thread/process and every later startup start without prior verification. The
    ordinary session/startup lifecycle gates still apply to every operation.
    """
    previous = getattr(_startup_scope, "current", None)
    if previous is not None and previous[0] == os.getpid():
        yield
        return
    _startup_scope.current = (os.getpid(), {})
    try:
        yield
    finally:
        _startup_scope.current = previous


def _startup_cache():
    scope = getattr(_startup_scope, "current", None)
    return scope[1] if scope is not None and scope[0] == os.getpid() else None


def _startup_signature(path):
    """Metadata only: opening a raw descriptor could release SQLite locks.

    Reuse is deliberately unavailable for aliases or any SQLite sidecar. WAL
    snapshots and recovery journals must take the normal full-validation path.
    """
    try:
        supplied = Path(path).absolute()
        path = supplied.parent.resolve(strict=True) / supplied.name
        metadata = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            return None
        if any(os.path.lexists(str(path) + suffix) for suffix in ("-journal", "-wal", "-shm")):
            return None
        return (str(path), metadata.st_dev, metadata.st_ino, metadata.st_mode,
                metadata.st_nlink, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)
    except OSError:
        # The regular opener remains responsible for the precise refusal.
        return None


def _startup_check(path, kind):
    cache = _startup_cache()
    if cache is None:
        return None, _UNVERIFIED
    signature = _startup_signature(path)
    if signature is None:
        # A sidecar/recovery or file-identity uncertainty invalidates previous
        # observations too, even if SQLite subsequently removes that sidecar.
        cache.clear()
        return None, _UNVERIFIED
    existing = cache.get((signature[0], kind))
    if existing is not None and existing[0] == signature:
        return signature, existing[1]
    return signature, _UNVERIFIED


def _remember_startup_check(path, kind, before, result):
    cache = _startup_cache()
    if cache is not None and before is not None and _startup_signature(path) == before:
        cache[(before[0], kind)] = (before, result)


class NativeSchemaError(ValueError):
    """The file is not a supported native database or its migration is unsafe."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        source = json.dumps([self.version, self.name, self.statements],
                            ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(source.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MigrationResult:
    path: Path
    version: int
    applied_versions: tuple[int, ...]
    backup_path: Path | None = None


def _uuid(column="id"):
    return (f"length({column})=36 AND substr({column},9,1)='-' "
            f"AND substr({column},14,1)='-' AND substr({column},19,1)='-' "
            f"AND substr({column},24,1)='-' AND length(replace({column},'-',''))=32 "
            f"AND replace({column},'-','') NOT GLOB '*[^0-9a-f]*'")


def _decimal(column, *, integer=64, fraction=24, positive=False):
    """Canonical unsigned decimal TEXT check using only SQLite built-ins.

    Keeping CHECK expressions independent of application UDFs lets the shared
    SQLite backup/integrity helpers inspect this schema without custom setup.
    """
    return (
        f"typeof({column})='text' AND length({column}) BETWEEN 1 AND {integer + fraction + 1} "
        f"AND {column} NOT GLOB '*[^0-9.]*' "
        f"AND substr({column},1,1) BETWEEN '0' AND '9' "
        f"AND substr({column},-1,1) BETWEEN '0' AND '9' "
        f"AND length({column})-length(replace({column},'.',''))<=1 "
        f"AND ({column}='0' OR substr({column},1,1)<>'0' OR substr({column},1,2)='0.') "
        f"AND (instr({column},'.')=0 OR substr({column},-1,1)<>'0') "
        f"AND (CASE WHEN instr({column},'.')=0 THEN length({column}) "
        f"ELSE instr({column},'.')-1 END)<={integer} "
        f"AND (instr({column},'.')=0 OR length({column})-instr({column},'.')<={fraction})"
        + (f" AND {column}<>'0'" if positive else "")
    )


def _amount(column, *, fraction=2, positive=False, default="0", integer=64):
    default_sql = f" DEFAULT '{default}'" if default is not None else ""
    return f"{column} TEXT NOT NULL{default_sql} CHECK({_decimal(column, integer=integer, fraction=fraction, positive=positive)})"


def _date(column):
    return (f"length({column})=10 AND {column} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' "
            f"AND substr({column},1,4)>='0001' "
            f"AND date({column},'+0 days') IS NOT NULL AND date({column},'+0 days')={column}")


def _bool(column, default=0):
    return f"{column} INTEGER NOT NULL DEFAULT {default} CHECK({column} IN (0,1))"


def _time(column):
    return f"{column} INTEGER NOT NULL DEFAULT (strftime('%s','now')) CHECK({column}>=0)"


_ID = f"id TEXT NOT NULL PRIMARY KEY CHECK({_uuid()})"
_KINDS = (
    "sale", "purchase", "sale_return", "purchase_return", "breakage_receive",
    "breakage_dispatch", "delivery_challan", "stock_opening", "stock_transfer",
    "stock_adjustment", "journal", "cash_receipt", "cash_payment", "bank_receipt",
    "bank_payment", "opening_balance", "reversal",
)
# _KINDS is the immutable migration-one specification. Extend only the public
# set and the appended rebuild; changing _KINDS invalidates deployed checksums.
_CASH_KINDS = ("cash_sale", "cash_purchase", "cash_sale_return", "cash_purchase_return")
DOCUMENT_KINDS = frozenset(_KINDS + _CASH_KINDS)
STOCK_ONLY_KINDS = frozenset({"delivery_challan", "stock_transfer", "stock_adjustment"})


def _foundation_statements():
    kinds = ",".join(repr(kind) for kind in _KINDS)
    sql = [
        f"PRAGMA application_id={APPLICATION_ID}",
        "CREATE TABLE native_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
        f"application_name TEXT NOT NULL CHECK(application_name='{APPLICATION_NAME}'), {_time('created_at')}) STRICT",
        f"INSERT INTO native_meta(singleton,application_name) VALUES (1,'{APPLICATION_NAME}')",
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY CHECK(version>0), "
        "name TEXT NOT NULL, checksum TEXT NOT NULL CHECK(length(checksum)=64), "
        f"{_time('applied_at')}) STRICT",
        f"CREATE TABLE cities ({_ID}, code TEXT NOT NULL UNIQUE CHECK(length(trim(code))>0), "
        "name TEXT NOT NULL CHECK(length(trim(name))>0), name_urdu TEXT) STRICT",
        f"CREATE TABLE accounts ({_ID}, code TEXT NOT NULL UNIQUE CHECK(length(trim(code))>0), "
        "name TEXT NOT NULL CHECK(length(trim(name))>0), name_urdu TEXT, "
        "parent_id TEXT REFERENCES accounts(id), city_id TEXT REFERENCES cities(id), "
        "kind TEXT NOT NULL DEFAULT 'general' CHECK(kind IN ('group','general','customer','supplier','both','cash','bank')), "
        "classification TEXT CHECK(classification IN ('asset','liability','equity','income','expense')), "
        f"{_bool('is_postable', 1)}, {_bool('is_active', 1)}, "
        f"{_time('created_at')}, {_time('updated_at')}, CHECK(parent_id IS NULL OR parent_id<>id), "
        "CHECK(kind<>'group' OR is_postable=0)) STRICT",
        f"CREATE TABLE companies ({_ID}, code TEXT NOT NULL UNIQUE CHECK(length(trim(code))>0), "
        "name TEXT NOT NULL CHECK(length(trim(name))>0), name_urdu TEXT, "
        f"fiscal_start TEXT CHECK(fiscal_start IS NULL OR ({_date('fiscal_start')})), "
        f"fiscal_end TEXT CHECK(fiscal_end IS NULL OR ({_date('fiscal_end')})), "
        "cash_account_id TEXT REFERENCES accounts(id), "
        f"{_bool('is_active', 1)}, {_time('created_at')}, {_time('updated_at')}, "
        "CHECK((fiscal_start IS NULL AND fiscal_end IS NULL) OR "
        "(fiscal_start IS NOT NULL AND fiscal_end IS NOT NULL AND fiscal_start<=fiscal_end))) STRICT",
        f"CREATE TABLE units ({_ID}, code TEXT NOT NULL UNIQUE CHECK(length(trim(code))>0), "
        f"name TEXT NOT NULL CHECK(length(trim(name))>0), name_urdu TEXT, {_bool('is_active', 1)}) STRICT",
        f"CREATE TABLE product_groups ({_ID}, code TEXT NOT NULL UNIQUE CHECK(length(trim(code))>0), "
        "name TEXT NOT NULL CHECK(length(trim(name))>0), name_urdu TEXT, "
        "parent_id TEXT REFERENCES product_groups(id), CHECK(parent_id IS NULL OR parent_id<>id)) STRICT",
        f"CREATE TABLE products ({_ID}, code TEXT NOT NULL UNIQUE CHECK(length(trim(code))>0), "
        "name TEXT NOT NULL CHECK(length(trim(name))>0), name_urdu TEXT, "
        "group_id TEXT REFERENCES product_groups(id), small_unit_id TEXT NOT NULL REFERENCES units(id), "
        "large_unit_id TEXT REFERENCES units(id), "
        f"{_amount('unit_factor', integer=18, fraction=6, positive=True, default='1')}, "
        f"{_amount('sale_rate', integer=18, fraction=6)}, {_amount('purchase_rate', integer=18, fraction=6)}, "
        "purchase_account_id TEXT REFERENCES accounts(id), sale_account_id TEXT REFERENCES accounts(id), "
        "inventory_account_id TEXT REFERENCES accounts(id), "
        f"{_bool('is_active', 1)}, {_time('created_at')}, {_time('updated_at')}) STRICT",
        f"CREATE TABLE stores ({_ID}, code TEXT NOT NULL UNIQUE CHECK(length(trim(code))>0), "
        f"name TEXT NOT NULL CHECK(length(trim(name))>0), name_urdu TEXT, {_bool('is_active', 1)}) STRICT",
        "CREATE TABLE company_stores (company_id TEXT NOT NULL REFERENCES companies(id), "
        f"store_id TEXT NOT NULL REFERENCES stores(id), {_bool('is_active', 1)}, "
        "PRIMARY KEY(company_id,store_id)) STRICT",
        f"CREATE TABLE users ({_ID}, username TEXT NOT NULL UNIQUE "
        "CHECK(length(username) BETWEEN 3 AND 80 AND username NOT GLOB '*[^a-z0-9_.-]*'), "
        "display_name TEXT NOT NULL CHECK(length(trim(display_name)) BETWEEN 1 AND 120), "
        "password_hash TEXT NOT NULL CHECK(length(password_hash) BETWEEN 1 AND 512), "
        f"{_bool('is_admin')}, {_bool('is_active')}, "
        "failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK(failed_attempts>=0), "
        "locked_until INTEGER CHECK(locked_until IS NULL OR locked_until>=0), "
        f"{_time('password_changed_at')}, {_time('created_at')}, {_time('updated_at')}) STRICT",
        "CREATE TABLE user_company_grants (user_id TEXT NOT NULL REFERENCES users(id), "
        "company_id TEXT NOT NULL REFERENCES companies(id), PRIMARY KEY(user_id,company_id)) STRICT",
        "CREATE TABLE user_menu_grants (user_id TEXT NOT NULL REFERENCES users(id), "
        "module TEXT NOT NULL CHECK(length(trim(module)) BETWEEN 1 AND 80), "
        f"{_bool('can_read')}, {_bool('can_create')}, {_bool('can_update')}, "
        f"{_bool('can_delete')}, {_bool('can_post')}, PRIMARY KEY(user_id,module)) STRICT",
        "CREATE TABLE auth_sessions (token_hash TEXT NOT NULL PRIMARY KEY "
        "CHECK(length(token_hash)=64 AND token_hash NOT GLOB '*[^0-9a-f]*'), "
        "user_id TEXT NOT NULL REFERENCES users(id), issued_at INTEGER NOT NULL CHECK(issued_at>=0), "
        "expires_at INTEGER NOT NULL CHECK(expires_at>issued_at), "
        "last_seen_at INTEGER NOT NULL CHECK(last_seen_at>=issued_at AND last_seen_at<=expires_at), "
        "revoked_at INTEGER CHECK(revoked_at IS NULL OR revoked_at>=issued_at)) STRICT",
        "CREATE INDEX auth_sessions_user ON auth_sessions(user_id,expires_at)",
        f"CREATE TABLE documents ({_ID}, company_id TEXT NOT NULL REFERENCES companies(id), "
        f"kind TEXT NOT NULL CHECK(kind IN ({kinds})), "
        "document_number TEXT NOT NULL CHECK(length(trim(document_number)) BETWEEN 1 AND 80), "
        f"business_date TEXT NOT NULL CHECK({_date('business_date')}), "
        "account_id TEXT REFERENCES accounts(id), status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','posted')), "
        "revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0), "
        f"{_amount('total_amount')}, rounding_policy TEXT NOT NULL CHECK(rounding_policy IN ('cents','legacy_whole')), "
        "posting_places INTEGER NOT NULL CHECK(posting_places IN (0,2)), "
        "notes TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL REFERENCES users(id), "
        f"{_time('created_at')}, {_time('updated_at')}, posted_by TEXT REFERENCES users(id), "
        "posted_at INTEGER CHECK(posted_at IS NULL OR posted_at>=0), reverses_document_id TEXT, "
        "legacy_source TEXT, legacy_table TEXT, legacy_id TEXT, "
        "UNIQUE(company_id,id), UNIQUE(company_id,kind,document_number), "
        "UNIQUE(company_id,reverses_document_id), UNIQUE(legacy_source,legacy_table,legacy_id), "
        "FOREIGN KEY(company_id,reverses_document_id) REFERENCES documents(company_id,id), "
        "CHECK(reverses_document_id IS NULL OR reverses_document_id<>id), "
        "CHECK((status='draft' AND posted_by IS NULL AND posted_at IS NULL) OR "
        "(status='posted' AND posted_by IS NOT NULL AND posted_at IS NOT NULL)), "
        "CHECK((legacy_source IS NULL AND legacy_table IS NULL AND legacy_id IS NULL) OR "
        "(legacy_source IS NOT NULL AND legacy_table IS NOT NULL AND legacy_id IS NOT NULL))) STRICT",
        f"CREATE TABLE document_lines ({_ID}, company_id TEXT NOT NULL, document_id TEXT NOT NULL, "
        "line_number INTEGER NOT NULL CHECK(line_number BETWEEN 1 AND 10000), "
        "product_id TEXT NOT NULL REFERENCES products(id), store_id TEXT NOT NULL, "
        f"{_amount('quantity', integer=18, fraction=6, positive=True, default=None)}, "
        f"{_amount('rate', integer=18, fraction=6)}, {_amount('discount_percent', integer=3, fraction=6)}, "
        f"{_amount('net_amount', fraction=24)}, source_line_id TEXT, "
        "CHECK(length(CASE WHEN instr(discount_percent,'.')=0 THEN discount_percent "
        "ELSE substr(discount_percent,1,instr(discount_percent,'.')-1) END)<3 OR discount_percent='100'), "
        "UNIQUE(company_id,document_id,id,product_id), UNIQUE(company_id,id), "
        "UNIQUE(document_id,line_number), "
        "FOREIGN KEY(company_id,document_id) REFERENCES documents(company_id,id), "
        "FOREIGN KEY(company_id,store_id) REFERENCES company_stores(company_id,store_id), "
        "FOREIGN KEY(company_id,source_line_id) REFERENCES document_lines(company_id,id), "
        "CHECK(source_line_id IS NULL OR source_line_id<>id)) STRICT",
        f"CREATE TABLE journal_entries ({_ID}, company_id TEXT NOT NULL, document_id TEXT NOT NULL UNIQUE, "
        "description TEXT NOT NULL DEFAULT '', UNIQUE(company_id,id), "
        "FOREIGN KEY(company_id,document_id) REFERENCES documents(company_id,id)) STRICT",
        f"CREATE TABLE journal_lines ({_ID}, company_id TEXT NOT NULL, entry_id TEXT NOT NULL, "
        "line_number INTEGER NOT NULL CHECK(line_number BETWEEN 1 AND 20000), "
        "account_id TEXT NOT NULL REFERENCES accounts(id), "
        f"{_amount('debit')}, {_amount('credit')}, narration TEXT NOT NULL DEFAULT '', "
        "CHECK((debit='0' AND credit<>'0') OR (credit='0' AND debit<>'0')), "
        "UNIQUE(entry_id,line_number), "
        "FOREIGN KEY(company_id,entry_id) REFERENCES journal_entries(company_id,id)) STRICT",
        f"CREATE TABLE stock_movements ({_ID}, company_id TEXT NOT NULL, document_id TEXT NOT NULL, "
        "document_line_id TEXT NOT NULL, product_id TEXT NOT NULL, store_id TEXT NOT NULL, "
        "direction TEXT NOT NULL CHECK(direction IN ('in','out')), "
        f"{_amount('quantity', integer=18, fraction=6, positive=True, default=None)}, "
        f"{_amount('unit_cost', integer=18, fraction=6)}, {_amount('value', fraction=24)}, "
        "reverses_movement_id TEXT, UNIQUE(company_id,id), UNIQUE(company_id,reverses_movement_id), "
        "UNIQUE(document_id,document_line_id,store_id,direction), "
        "FOREIGN KEY(company_id,document_id,document_line_id,product_id) "
        "REFERENCES document_lines(company_id,document_id,id,product_id), "
        "FOREIGN KEY(company_id,store_id) REFERENCES company_stores(company_id,store_id), "
        "FOREIGN KEY(company_id,reverses_movement_id) REFERENCES stock_movements(company_id,id), "
        "CHECK(reverses_movement_id IS NULL OR reverses_movement_id<>id)) STRICT",
        f"CREATE TABLE audit_events ({_ID}, actor_id TEXT REFERENCES users(id), "
        "company_id TEXT REFERENCES companies(id), action TEXT NOT NULL CHECK(length(trim(action))>0), "
        "entity_type TEXT NOT NULL CHECK(length(trim(entity_type))>0), entity_id TEXT, "
        f"{_time('occurred_at')}, details_json TEXT NOT NULL DEFAULT '{{}}' "
        "CHECK(json_valid(details_json) AND json_type(details_json)='object')) STRICT",
        "CREATE INDEX documents_company_date ON documents(company_id,business_date,kind,status)",
        "CREATE INDEX journal_lines_account ON journal_lines(company_id,account_id,entry_id)",
        "CREATE INDEX stock_company_store_item ON stock_movements(company_id,store_id,product_id)",
        "CREATE INDEX audit_company_time ON audit_events(company_id,occurred_at)",
        "CREATE VIEW posted_journal_lines AS SELECT l.*,d.business_date,d.kind,d.document_number "
        "FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
        "JOIN documents d ON d.id=e.document_id WHERE d.status='posted'",
        "CREATE VIEW posted_stock_movements AS SELECT m.*,d.business_date,d.kind,d.document_number "
        "FROM stock_movements m JOIN documents d ON d.id=m.document_id WHERE d.status='posted'",
    ]
    for table in ("native_meta", "schema_migrations", "audit_events"):
        for operation in ("UPDATE", "DELETE"):
            sql.append(f"CREATE TRIGGER {table}_immutable_{operation.lower()} BEFORE {operation} ON {table} "
                       f"BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END")
    sql += [
        "CREATE TRIGGER documents_insert_draft BEFORE INSERT ON documents WHEN NEW.status<>'draft' "
        "BEGIN SELECT RAISE(ABORT,'Create a draft before posting'); END",
        "CREATE TRIGGER documents_immutable_update BEFORE UPDATE ON documents WHEN OLD.status='posted' "
        "BEGIN SELECT RAISE(ABORT,'Posted documents are immutable; create a reversal'); END",
        "CREATE TRIGGER documents_immutable_delete BEFORE DELETE ON documents WHEN OLD.status='posted' "
        "BEGIN SELECT RAISE(ABORT,'Posted documents are immutable; create a reversal'); END",
        "CREATE TRIGGER documents_origin_update BEFORE UPDATE ON documents "
        "WHEN NEW.id IS NOT OLD.id OR NEW.company_id IS NOT OLD.company_id "
        "BEGIN SELECT RAISE(ABORT,'Document identity and company are immutable'); END",
        "CREATE TRIGGER documents_revision BEFORE UPDATE ON documents WHEN NEW.revision<>OLD.revision+1 "
        "BEGIN SELECT RAISE(ABORT,'Document revision must advance by one'); END",
        "CREATE TRIGGER documents_post BEFORE UPDATE OF status ON documents "
        "WHEN OLD.status='draft' AND NEW.status='posted' BEGIN "
        "SELECT RAISE(ABORT,'A financial document with a nonzero total requires a journal') "
        "WHERE NEW.total_amount<>'0' AND NEW.kind NOT IN ('delivery_challan','stock_transfer','stock_adjustment') "
        "AND NOT EXISTS(SELECT 1 FROM journal_entries WHERE document_id=NEW.id); "
        "SELECT RAISE(ABORT,'A posted journal requires at least two lines') "
        "WHERE EXISTS(SELECT 1 FROM journal_entries e WHERE e.document_id=NEW.id "
        "AND (SELECT COUNT(*) FROM journal_lines l WHERE l.entry_id=e.id)<2); "
        "SELECT RAISE(ABORT,'Journal debits and credits must balance exactly') "
        "WHERE EXISTS(SELECT 1 FROM journal_entries e WHERE e.document_id=NEW.id "
        "AND (SELECT native_decimal_sum(debit) FROM journal_lines WHERE entry_id=e.id) "
        "IS NOT (SELECT native_decimal_sum(credit) FROM journal_lines WHERE entry_id=e.id)); "
        "SELECT RAISE(ABORT,'A reversal must reference a posted document') "
        "WHERE NEW.reverses_document_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM documents original WHERE original.id=NEW.reverses_document_id "
        "AND original.company_id=NEW.company_id AND original.status='posted'); END",
        "CREATE TRIGGER journal_lines_postable_insert BEFORE INSERT ON journal_lines "
        "WHEN EXISTS(SELECT 1 FROM accounts WHERE id=NEW.account_id AND is_postable=0) "
        "BEGIN SELECT RAISE(ABORT,'Journal account is not postable'); END",
        "CREATE TRIGGER journal_lines_postable_update BEFORE UPDATE ON journal_lines "
        "WHEN EXISTS(SELECT 1 FROM accounts WHERE id=NEW.account_id AND is_postable=0) "
        "BEGIN SELECT RAISE(ABORT,'Journal account is not postable'); END",
    ]
    # Changing a child's origin is never permitted. Draft values may be edited;
    # all rows belonging to a posted document are then permanently immutable.
    for table, parent, origins in (
        ("document_lines", "SELECT status FROM documents WHERE id={row}.document_id", ("id", "company_id", "document_id")),
        ("journal_entries", "SELECT status FROM documents WHERE id={row}.document_id", ("id", "company_id", "document_id")),
        ("journal_lines", "SELECT d.status FROM documents d JOIN journal_entries e ON e.document_id=d.id WHERE e.id={row}.entry_id", ("id", "company_id", "entry_id")),
        ("stock_movements", "SELECT status FROM documents WHERE id={row}.document_id", ("id", "company_id", "document_id", "document_line_id", "product_id")),
    ):
        expression = " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in origins)
        sql.append(f"CREATE TRIGGER {table}_origin_update BEFORE UPDATE ON {table} WHEN {expression} "
                   "BEGIN SELECT RAISE(ABORT,'Posting origin is immutable'); END")
        for operation, row in (("INSERT", "NEW"), ("UPDATE", "OLD"), ("DELETE", "OLD")):
            sql.append(f"CREATE TRIGGER {table}_posted_{operation.lower()} BEFORE {operation} ON {table} "
                       f"WHEN ({parent.format(row=row)})='posted' "
                       "BEGIN SELECT RAISE(ABORT,'Posted document components are immutable'); END")
    return tuple(sql)


def _transaction_statements():
    """Add service snapshots without rewriting the version-one specification."""
    return (
        "ALTER TABLE documents ADD COLUMN posting_sequence INTEGER CHECK(posting_sequence>0)",
        "CREATE UNIQUE INDEX documents_posting_sequence ON documents(posting_sequence) WHERE posting_sequence IS NOT NULL",
        "ALTER TABLE stock_movements ADD COLUMN pool TEXT CHECK(pool IN ('regular','breakage'))",
        "ALTER TABLE stock_movements ADD COLUMN valuation_basis TEXT "
        "CHECK(valuation_basis IN ('unvalued','opening_explicit','reversal'))",
        "CREATE TABLE document_metadata (document_id TEXT NOT NULL PRIMARY KEY, "
        "company_id TEXT NOT NULL, input_json TEXT NOT NULL "
        "CHECK(json_valid(input_json) AND json_type(input_json)='object'), "
        "snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json) AND json_type(snapshot_json)='object'), "
        "calculation_json TEXT NOT NULL CHECK(json_valid(calculation_json) AND json_type(calculation_json)='object'), "
        "FOREIGN KEY(company_id,document_id) REFERENCES documents(company_id,id)) STRICT",
        "CREATE TRIGGER document_metadata_origin_update BEFORE UPDATE ON document_metadata "
        "WHEN NEW.document_id IS NOT OLD.document_id OR NEW.company_id IS NOT OLD.company_id "
        "BEGIN SELECT RAISE(ABORT,'Document snapshot origin is immutable'); END",
        "CREATE TRIGGER document_metadata_posted_insert BEFORE INSERT ON document_metadata "
        "WHEN (SELECT status FROM documents WHERE id=NEW.document_id)='posted' "
        "BEGIN SELECT RAISE(ABORT,'Posted document snapshots are immutable'); END",
        "CREATE TRIGGER document_metadata_posted_update BEFORE UPDATE ON document_metadata "
        "WHEN (SELECT status FROM documents WHERE id=OLD.document_id)='posted' "
        "BEGIN SELECT RAISE(ABORT,'Posted document snapshots are immutable'); END",
        "CREATE TRIGGER document_metadata_posted_delete BEFORE DELETE ON document_metadata "
        "WHEN (SELECT status FROM documents WHERE id=OLD.document_id)='posted' "
        "BEGIN SELECT RAISE(ABORT,'Posted document snapshots are immutable'); END",
        "CREATE INDEX stock_pool_item ON stock_movements(company_id,pool,store_id,product_id)",
    )


def _contact_statements():
    """Append optional source-supported master text without rewriting snapshots."""
    fields = (
        ("companies", (("address", 500), ("address_urdu", 500), ("phone", 80), ("fax", 80),
                       ("email", 254), ("website", 2048), ("sales_tax_number", 80), ("national_tax_number", 80))),
        ("accounts", (("address", 500), ("phone", 80), ("mobile", 80), ("email", 254),
                      ("website", 2048), ("sales_tax_number", 80), ("national_tax_number", 80))),
    )
    return tuple(
        f"ALTER TABLE {table} ADD COLUMN {column} TEXT CHECK({column} IS NULL OR "
        f"(typeof({column})='text' AND length({column}) BETWEEN 1 AND {maximum} "
        f"AND length(trim({column}))>0 AND instr({column},char(0))=0))"
        for table, columns in fields for column, maximum in columns
    )


def _cash_document_statements():
    """SQLite's generalized table rebuild, with foreign keys off BEFORE BEGIN.

    https://www.sqlite.org/lang_altertable.html#making_other_kinds_of_table_schema_changes
    Use the frozen specifications for every existing guard/view/index. Never
    rename the old table: child foreign keys must continue to name documents.
    No posted row is re-entered through a business INSERT/UPDATE operation.
    """
    prior = _foundation_statements() + _transaction_statements()
    original = next(sql for sql in prior if sql.startswith("CREATE TABLE documents ("))
    old_check = "kind IN (" + ",".join(repr(kind) for kind in _KINDS) + ")"
    new_check = "kind IN (" + ",".join(repr(kind) for kind in _KINDS + _CASH_KINDS) + ")"
    replacement = original.replace("CREATE TABLE documents (", "CREATE TABLE documents_cash_v4 (", 1).replace(old_check, new_check, 1)
    replacement = replacement.replace("legacy_source TEXT, legacy_table TEXT, legacy_id TEXT, ",
                                      "legacy_source TEXT, legacy_table TEXT, legacy_id TEXT, "
                                      "posting_sequence INTEGER CHECK(posting_sequence>0), ", 1)
    # Triggers on child tables and views must not refer to a temporarily absent
    # table during ALTER TABLE's schema validation. Recreate their exact SQL.
    dependencies = []
    for sql in prior:
        match = re.match(r"CREATE (?:UNIQUE )?(TRIGGER|VIEW|INDEX) (\w+)\b", sql)
        if match and ((match[1] in {"TRIGGER", "VIEW"} and re.search(r"\bdocuments\b", sql))
                      or (match[1] == "INDEX" and match[2].startswith("documents_"))):
            dependencies.append((match[1], match[2], sql))
    columns = ("rowid,id,company_id,kind,document_number,business_date,account_id,status,revision,total_amount,"
               "rounding_policy,posting_places,notes,created_by,created_at,updated_at,posted_by,posted_at,"
               "reverses_document_id,legacy_source,legacy_table,legacy_id,posting_sequence")
    return tuple(f"DROP {kind} {name}" for kind, name, _ in dependencies) + (
        replacement,
        f"INSERT INTO documents_cash_v4({columns}) SELECT {columns} FROM documents",
        "DROP TABLE documents",
        "ALTER TABLE documents_cash_v4 RENAME TO documents",
    ) + tuple(sql for _, _, sql in dependencies)


def _baseline_statements():
    """Sealed historical closings supplement new postings without replaying them."""
    def signed(column):
        unsigned = _decimal(column, integer=126, fraction=130)
        negative = _decimal(f"substr({column},2)", integer=126, fraction=130, positive=True)
        return f"({unsigned}) OR (substr({column},1,1)='-' AND ({negative}))"

    labels = "labels_json TEXT NOT NULL CHECK(json_valid(labels_json) AND json_type(labels_json)='object')"
    sql = [
        "ALTER TABLE products ADD COLUMN sale_rate_known INTEGER NOT NULL DEFAULT 1 CHECK(sale_rate_known IN (0,1))",
        "ALTER TABLE products ADD COLUMN purchase_rate_known INTEGER NOT NULL DEFAULT 1 CHECK(purchase_rate_known IN (0,1))",
        "CREATE TABLE baseline_imports (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
        "source_sha256 TEXT NOT NULL CHECK(length(source_sha256)=64 AND source_sha256 NOT GLOB '*[^0-9a-f]*'), "
        f"cutoff_date TEXT NOT NULL CHECK({_date('cutoff_date')}), "
        "manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json) AND json_type(manifest_json)='object'), "
        f"{_time('created_at')}) STRICT",
        "CREATE TABLE baseline_mappings (source_table TEXT NOT NULL, source_key TEXT NOT NULL, "
        "native_table TEXT NOT NULL, native_id TEXT NOT NULL, source_sha256 TEXT NOT NULL, "
        "PRIMARY KEY(source_table,source_key), UNIQUE(native_table,native_id)) STRICT",
        "CREATE TABLE baseline_account_balances (company_id TEXT NOT NULL REFERENCES companies(id), "
        "account_id TEXT NOT NULL REFERENCES accounts(id), "
        f"balance TEXT NOT NULL CHECK({signed('balance')}), {labels}, PRIMARY KEY(company_id,account_id)) STRICT",
        "CREATE TABLE baseline_stock_balances (company_id TEXT NOT NULL REFERENCES companies(id), "
        "store_id TEXT NOT NULL REFERENCES stores(id), product_id TEXT NOT NULL REFERENCES products(id), "
        "pool TEXT NOT NULL CHECK(pool IN ('regular','breakage')), "
        f"quantity TEXT NOT NULL CHECK({signed('quantity')}), historical_value TEXT NOT NULL CHECK({signed('historical_value')}), "
        "base_unit_id TEXT NOT NULL REFERENCES units(id), "
        f"{labels}, PRIMARY KEY(company_id,store_id,product_id,pool)) STRICT",
        "CREATE TABLE baseline_document_numbers (company_id TEXT NOT NULL REFERENCES companies(id), "
        "kind TEXT NOT NULL, document_number TEXT NOT NULL, source_table TEXT NOT NULL, source_key TEXT NOT NULL, "
        "PRIMARY KEY(source_table,source_key)) STRICT",
        "CREATE TABLE baseline_purchase_rates (company_id TEXT NOT NULL REFERENCES companies(id), "
        "product_id TEXT NOT NULL REFERENCES products(id), "
        f"rate TEXT NOT NULL CHECK({signed('rate')}), "
        "rate_source TEXT NOT NULL CHECK(rate_source IN ('primary','fallback','missing')), source_id TEXT, "
        "source_json TEXT NOT NULL CHECK(json_valid(source_json) AND json_type(source_json)='object'), "
        "PRIMARY KEY(company_id,product_id)) STRICT",
        f"CREATE TABLE baseline_trade_documents ({_ID}, company_id TEXT NOT NULL REFERENCES companies(id), "
        "kind TEXT NOT NULL CHECK(kind IN ('sale','purchase','cash_sale','cash_purchase')), "
        "document_number TEXT NOT NULL, business_date TEXT NOT NULL, account_id TEXT REFERENCES accounts(id), "
        f"total_amount TEXT CHECK(total_amount IS NULL OR ({signed('total_amount')})), "
        f"historical_returned_amount TEXT NOT NULL DEFAULT '0' CHECK({_decimal('historical_returned_amount', integer=126, fraction=130)}), "
        "rounding_policy TEXT NOT NULL CHECK(rounding_policy='legacy_whole'), posting_places INTEGER NOT NULL CHECK(posting_places=0), "
        "input_json TEXT NOT NULL CHECK(json_valid(input_json)), snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)), "
        "source_table TEXT NOT NULL, source_key TEXT NOT NULL, source_json TEXT NOT NULL CHECK(json_valid(source_json)), "
        f"{_bool('return_eligible')}, exclusion_reason TEXT, UNIQUE(company_id,id), UNIQUE(source_table,source_key)) STRICT",
        f"CREATE TABLE baseline_trade_lines ({_ID}, company_id TEXT NOT NULL REFERENCES companies(id), document_id TEXT NOT NULL, "
        "line_number INTEGER NOT NULL, product_id TEXT NOT NULL REFERENCES products(id), store_id TEXT NOT NULL REFERENCES stores(id), "
        "quantity TEXT, rate TEXT, discount_percent TEXT, net_amount TEXT, "
        f"historical_returned_quantity TEXT NOT NULL DEFAULT '0' CHECK({_decimal('historical_returned_quantity', integer=126, fraction=130)}), "
        "snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)), source_table TEXT NOT NULL, source_key TEXT NOT NULL, "
        "source_json TEXT NOT NULL CHECK(json_valid(source_json)), "
        f"{_bool('return_eligible')}, exclusion_reason TEXT, UNIQUE(company_id,id), UNIQUE(source_table,source_key), "
        "FOREIGN KEY(company_id,document_id) REFERENCES baseline_trade_documents(company_id,id)) STRICT",
        "ALTER TABLE document_lines ADD COLUMN historical_source_line_id TEXT REFERENCES baseline_trade_lines(id) "
        "CHECK(historical_source_line_id IS NULL OR source_line_id IS NULL)",
        "CREATE INDEX baseline_trade_documents_lookup ON baseline_trade_documents(company_id,kind,business_date,document_number)",
        "CREATE INDEX baseline_trade_lines_document ON baseline_trade_lines(company_id,document_id,line_number)",
        "CREATE INDEX document_lines_historical_source ON document_lines(company_id,historical_source_line_id)",
        "CREATE TABLE stock_valuation_snapshots (movement_id TEXT PRIMARY KEY REFERENCES stock_movements(id), "
        f"unit_cost TEXT CHECK(unit_cost IS NULL OR ({_decimal('unit_cost', integer=252, fraction=260)})), "
        f"value TEXT CHECK(value IS NULL OR ({_decimal('value', integer=252, fraction=260)})), "
        "source_json TEXT NOT NULL CHECK(json_valid(source_json) AND json_type(source_json)='object')) STRICT",
        "CREATE TRIGGER stock_valuation_snapshots_insert_guard BEFORE INSERT ON stock_valuation_snapshots "
        "WHEN NOT EXISTS(SELECT 1 FROM stock_movements m JOIN documents d ON d.id=m.document_id "
        "WHERE m.id=NEW.movement_id AND d.status='draft') "
        "BEGIN SELECT RAISE(ABORT,'Valuation must be saved before posting'); END",
        "CREATE TRIGGER stock_valuation_snapshots_update_guard BEFORE UPDATE ON stock_valuation_snapshots "
        "BEGIN SELECT RAISE(ABORT,'Saved stock valuation is immutable'); END",
        "CREATE TRIGGER stock_valuation_snapshots_delete_guard BEFORE DELETE ON stock_valuation_snapshots "
        "BEGIN SELECT RAISE(ABORT,'Saved stock valuation is immutable'); END",
        "CREATE INDEX baseline_document_number_lookup ON baseline_document_numbers(company_id,kind,document_number)",
        "CREATE INDEX baseline_stock_company_product ON baseline_stock_balances(company_id,product_id,store_id,pool)",
    ]
    for table in ("baseline_imports", "baseline_mappings", "baseline_account_balances", "baseline_stock_balances", "baseline_document_numbers", "baseline_purchase_rates", "baseline_trade_documents", "baseline_trade_lines"):
        for operation in ("UPDATE", "DELETE"):
            sql.append(f"CREATE TRIGGER {table}_{operation.lower()}_guard BEFORE {operation} ON {table} "
                       "BEGIN SELECT RAISE(ABORT,'Historical baseline is immutable'); END")
        sql.append(f"CREATE TRIGGER {table}_insert_guard BEFORE INSERT ON {table} "
                   "WHEN EXISTS(SELECT 1 FROM baseline_imports) "
                   "BEGIN SELECT RAISE(ABORT,'Historical baseline is sealed'); END")
    for operation in ("INSERT", "UPDATE"):
        sql.append(f"CREATE TRIGGER document_lines_historical_source_{operation.lower()}_guard BEFORE {operation} ON document_lines "
                   "WHEN NEW.historical_source_line_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM baseline_trade_lines b "
                   "WHERE b.id=NEW.historical_source_line_id AND b.company_id=NEW.company_id AND b.return_eligible=1) "
                   "BEGIN SELECT RAISE(ABORT,'Historical return source is unavailable in this company'); END")
        sql.append(f"CREATE TRIGGER documents_baseline_{operation.lower()}_guard BEFORE {operation} ON documents "
                   "WHEN EXISTS(SELECT 1 FROM baseline_imports WHERE NEW.business_date<=cutoff_date) "
                   "BEGIN SELECT RAISE(ABORT,'New transactions must follow the historical closing date'); END")
        sql.append(f"CREATE TRIGGER documents_baseline_number_{operation.lower()}_guard BEFORE {operation} ON documents "
                   "WHEN EXISTS(SELECT 1 FROM baseline_document_numbers b WHERE b.company_id=NEW.company_id "
                   "AND b.kind=NEW.kind AND b.document_number=NEW.document_number) "
                   "BEGIN SELECT RAISE(ABORT,'Document number already belongs to the historical baseline'); END")
    return tuple(sql)


def _short_username_statements():
    """Lower the minimum without changing existing identities or references.

    Rebuild only users with foreign keys suspended before BEGIN, retaining its
    rowids and every stored value. Child foreign keys continue to name users;
    none of the frozen prior specifications defines a user view or trigger.
    """
    original = next(sql for sql in _foundation_statements() if sql.startswith("CREATE TABLE users ("))
    replacement = original.replace("CREATE TABLE users (", "CREATE TABLE users_short_v6 (", 1).replace(
        "length(username) BETWEEN 3 AND 80", "length(username) BETWEEN 1 AND 80", 1)
    columns = ("rowid,id,username,display_name,password_hash,is_admin,is_active,failed_attempts,"
               "locked_until,password_changed_at,created_at,updated_at")
    return (
        replacement,
        f"INSERT INTO users_short_v6({columns}) SELECT {columns} FROM users",
        "DROP TABLE users",
        "ALTER TABLE users_short_v6 RENAME TO users",
    )


MIGRATIONS = (
    Migration(1, "native_foundation", _foundation_statements()),
    Migration(2, "transaction_snapshots_and_stock_pools", _transaction_statements()),
    Migration(3, "master_contacts_and_tax_identifiers", _contact_statements()),
    Migration(4, "distinct_cash_trade_document_namespaces", _cash_document_statements()),
    Migration(5, "sealed_historical_baseline_and_unknown_rates", _baseline_statements()),
    Migration(6, "allow_short_usernames", _short_username_statements()),
)


_DECIMAL_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?\Z")


class _DecimalSum:
    def __init__(self):
        self.total = Decimal(0)

    def step(self, value):
        if value is None:
            return
        if not isinstance(value, str) or len(value) > 128 or not _DECIMAL_PATTERN.fullmatch(value):
            raise ValueError("Invalid canonical native decimal")
        number = Decimal(value)
        exponent = min(self.total.as_tuple().exponent, number.as_tuple().exponent)
        precision = max(self.total.adjusted(), number.adjusted(), 0) - exponent + 3
        with localcontext(Context(prec=max(precision, 1))):
            self.total += number

    def finalize(self):
        value = format(self.total, "f")
        return value.rstrip("0").rstrip(".") if "." in value else value


def prepare_connection(connection):
    """Register exact posting checks; does not write or migrate the database."""
    # This connection-local setting makes DELETE guards apply to SQLite's
    # implicit deletions during INSERT OR REPLACE as well as explicit DELETE.
    connection.execute("PRAGMA recursive_triggers=ON")
    connection.create_aggregate("native_decimal_sum", 1, _DecimalSum)


def _validate_specs():
    if sqlite3.sqlite_version_info < MIN_SQLITE_VERSION:
        raise NativeSchemaError("Native databases require SQLite 3.37 or newer")
    if not MIGRATIONS or tuple(m.version for m in MIGRATIONS) != tuple(range(1, len(MIGRATIONS) + 1)):
        raise NativeSchemaError("Migration versions must be a contiguous sequence starting at one")
    for migration in MIGRATIONS:
        if not migration.name or not migration.statements:
            raise NativeSchemaError("Migration specification is empty")
        for statement in migration.statements:
            if re.match(r"\s*(?:BEGIN|COMMIT|END|ROLLBACK|SAVEPOINT|RELEASE|VACUUM|ATTACH|DETACH)\b", statement, re.I):
                raise NativeSchemaError("Migration statements must not control transactions or attach databases")


def require_native(connection, *, require_current=True):
    """Validate the native marker and immutable migration history; return version.

    This performs reads only. Callers sharing a native transaction can use it to
    reject accidental use of a historical archive or another SQLite database.
    """
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise NativeSchemaError("Native connections must enforce foreign keys")
    return _require_native_lineage(connection, require_current=require_current)


def _require_native_lineage(connection, *, require_current=True):
    """Private migration validation; callers otherwise use require_native.

    The generalized rebuild temporarily disables FK enforcement on one private
    connection. It still validates lineage and every FK before committing.
    """
    _validate_specs()
    if connection.execute("PRAGMA recursive_triggers").fetchone()[0] != 1:
        raise NativeSchemaError("Native connections must enforce recursive trigger guards")
    try:
        app_id = connection.execute("PRAGMA application_id").fetchone()[0]
        meta = connection.execute("SELECT application_name FROM native_meta WHERE singleton=1").fetchall()
        rows = connection.execute("SELECT version,name,checksum FROM schema_migrations ORDER BY version").fetchall()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError as error:
        raise NativeSchemaError("This is not a recognized native BIFMA database") from error
    if app_id != APPLICATION_ID or len(meta) != 1 or meta[0][0] != APPLICATION_NAME:
        raise NativeSchemaError("This is not a recognized native BIFMA database")
    if not rows or len(rows) > len(MIGRATIONS):
        raise NativeSchemaError("Unsupported or missing native migration history")
    for index, row in enumerate(rows):
        expected = MIGRATIONS[index]
        if tuple(row) != (expected.version, expected.name, expected.checksum):
            raise NativeSchemaError("Native migration history/checksum does not match this application")
    if version != rows[-1][0]:
        raise NativeSchemaError("Native schema version and migration history disagree")
    if require_current and version != MIGRATIONS[-1].version:
        raise NativeSchemaError("Native database upgrade is required; run migrate_database explicitly")
    return version


@contextmanager
def native_session(path, *, readonly=True):
    """Open a validated native database without implicit writes or migrations."""
    with session(path, readonly=readonly) as connection:
        prepare_connection(connection)
        require_native(connection)
        yield connection


@contextmanager
def native_transaction(path):
    """Lock first, validate schema, and commit/rollback a complete native action."""
    with session(path, readonly=False) as connection:
        prepare_connection(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            require_native(connection)
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def _check_integrity(connection):
    integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    if integrity != ["ok"] or connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise NativeSchemaError("Native database failed integrity checks")


def open_native_database(path, *, require_current=True):
    """Validate an existing native file and allow SQLite's normal crash recovery.

    Startup is deliberately separate from readonly native_session: recovery may
    restore database pages and remove a hot journal using SQLite's own locking.
    The raw native marker is checked before any writable SQLite connection, then
    full lineage and integrity are checked after recovery. This does not create,
    seed, promote or migrate any file. Return the verified native schema version.

    File format: https://www.sqlite.org/fileformat.html#the_database_header
    Recovery: https://www.sqlite.org/lockingv3.html#dealing_with_hot_journals
    """
    try:
        with exclusive_file_access(path):
            # Even a scoped reuse enters the gate: startup inside a managed
            # session must still fail, and another thread's writer must finish.
            kind = ("native", bool(require_current), APPLICATION_ID, APPLICATION_NAME,
                    MIN_SQLITE_VERSION, sqlite3.sqlite_version_info,
                    tuple((item.version, item.name, item.checksum) for item in MIGRATIONS))
            before, verified = _startup_check(path, kind)
            if verified is not _UNVERIFIED:
                return verified
            version = _open_native_database(path, require_current=require_current)
            _remember_startup_check(path, kind, before, version)
            return version
    except DatabaseError as error:
        raise NativeSchemaError(str(error)) from error


def _open_native_database(path, *, require_current):
    """Run only while exclusive_file_access excludes other managed handles."""
    _validate_specs()
    try:
        supplied = Path(path).absolute()
        # Resolve the directory once, never a possibly replaced final symlink.
        native_path = supplied.parent.resolve(strict=True) / supplied.name
        initial = native_path.stat(follow_symlinks=False)
    except FileNotFoundError as error:
        # Preserve the existing startup contract without a mode=rwc open.
        raise sqlite3.OperationalError("Native database file does not exist") from error
    if not stat.S_ISREG(initial.st_mode):
        raise NativeSchemaError("Select a regular native database file, not a symlink")
    if initial.st_nlink != 1:
        raise NativeSchemaError("Native database aliases are unsafe for journal recovery; select a file without hard links")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(native_path, flags)
    with os.fdopen(descriptor, "rb") as original:
        identity = os.fstat(original.fileno())
        expected = (identity.st_dev, identity.st_ino)
        if not stat.S_ISREG(identity.st_mode) or expected != (initial.st_dev, initial.st_ino):
            raise NativeSchemaError("Native database path changed during startup; open it again")
        header = original.read(100)
        if len(header) != 100 or header[:16] != b"SQLite format 3\x00" or int.from_bytes(header[68:72], "big") != APPLICATION_ID:
            raise NativeSchemaError("This is not a recognized native BIFMA database")

        def unchanged():
            try:
                current = native_path.stat(follow_symlinks=False)
            except FileNotFoundError as error:
                raise NativeSchemaError("Native database path changed during startup; open it again") from error
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or (current.st_dev, current.st_ino) != expected:
                raise NativeSchemaError("Native database path changed during startup; open it again")

        unchanged()
        # Connect directly so no lower-level helper resolves a late leaf symlink.
        # mode=rw cannot create a disappeared file. Keep the raw descriptor open
        # until SQLite closes: closing another fd early can release POSIX locks.
        connection = sqlite3.connect(f"{native_path.as_uri()}?mode=rw", uri=True, timeout=10, isolation_level=None)
        try:
            unchanged()
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            prepare_connection(connection)
            unchanged()
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN")
            version = require_native(connection, require_current=require_current)
            _check_integrity(connection)
            unchanged()
            # No application writes are issued. The read transaction only keeps
            # lineage and integrity checks on the same committed database state.
            connection.rollback()
            return version
        finally:
            connection.close()


def _apply(connection, migrations):
    for migration in migrations:
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations(version,name,checksum) VALUES (?,?,?)",
                           (migration.version, migration.name, migration.checksum))
        connection.execute(f"PRAGMA user_version={migration.version}")
    _check_integrity(connection)


@contextmanager
def _migration_transaction(connection, migrations):
    """Bound FK suspension to the explicit document and user table rebuilds."""
    if connection.in_transaction or connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise NativeSchemaError("Migration requires an idle connection enforcing foreign keys")
    rebuild = any(migration.version in {4, 6} for migration in migrations)
    try:
        if rebuild:
            connection.execute("PRAGMA foreign_keys=OFF")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
                raise NativeSchemaError("Unable to prepare the explicit table rebuild")
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            _check_integrity(connection)
            _require_native_lineage(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    finally:
        if rebuild:
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise NativeSchemaError("Unable to restore foreign key enforcement")


def initialize_database(path):
    """Create a new empty native file atomically; refuse every existing path.

    No administrator, companies, permissions, historical rows, or credentials
    are seeded. Bootstrap/setup are separate authenticated application services.
    """
    _validate_specs()
    destination = new_destination(path)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".bifma-native-", suffix=".sqlite3", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    connection = None
    try:
        connection = sqlite3.connect(temporary, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        prepare_connection(connection)
        with _migration_transaction(connection, MIGRATIONS):
            _apply(connection, MIGRATIONS)
        require_native(connection)
        connection.close()
        connection = None
        publish_new_file(temporary, destination)
        return MigrationResult(destination, MIGRATIONS[-1].version, tuple(m.version for m in MIGRATIONS))
    finally:
        if connection is not None:
            connection.close()
        temporary.unlink(missing_ok=True)
        Path(str(temporary) + "-journal").unlink(missing_ok=True)


def migrate_database(path, *, pre_upgrade_check=None):
    """Apply supported pending migrations with a verified pre-upgrade backup.

    A healthy up-to-date file is returned unchanged. Startup may first let SQLite
    recover an interrupted native transaction. V2, archives,
    empty SQLite files, newer schemas and altered migration checksums are refused.
    Backups are retained if an upgrade fails; partial schema/data changes roll back.
    An optional caller check runs against the locked pre-upgrade connection,
    before any backup or mutation. It must not open another session or service.
    """
    path = Path(path).resolve()
    open_native_database(path, require_current=False)
    with session(path) as connection:
        prepare_connection(connection)
        current = require_native(connection, require_current=False)
    latest = MIGRATIONS[-1].version
    if current == latest:
        return MigrationResult(path, current, ())
    with session(path, readonly=False) as connection:
        prepare_connection(connection)
        # Disable FK checks, when required, before acquiring the write lock.
        # Re-read lineage under that lock; a competing upgrader may finish first.
        planned = tuple(m for m in MIGRATIONS if m.version > current)
        with _migration_transaction(connection, planned):
            current = _require_native_lineage(connection, require_current=False)
            pending = tuple(m for m in MIGRATIONS if m.version > current)
            if pre_upgrade_check is not None:
                pre_upgrade_check(connection)
            if not pending:
                return MigrationResult(path, current, ())
            backup = path.with_name(f"{path.stem[:100]}.preupgrade-v{current}-to-v{latest}-{uuid4().hex}.sqlite3")
            # This lock has not changed pages yet. A separate readonly connection
            # backs up the committed pre-upgrade state while other writers wait.
            backup_database(path, backup)
            _apply(connection, pending)
        require_native(connection)
        return MigrationResult(path, latest, tuple(m.version for m in pending), backup)
