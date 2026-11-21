"""Authenticated maintenance of native shared masters and company configuration.

Shared master mutation is administrator-only. Operational dropdowns instead use
the caller's current company/module/action permissions in the same transaction.
No passwords, session tokens, or business field values enter master audit JSON.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import unicodedata
from uuid import UUID, uuid4

from bifma.calculations import CalculationError, as_decimal, canonical_decimal
from bifma.native_schema import native_session, native_transaction
from bifma.security import SecurityService


class MasterError(ValueError):
    """An invalid or conflicting master maintenance request."""


class MasterValidationError(MasterError):
    pass


class MasterNotFoundError(MasterError):
    pass


class MasterConflictError(MasterError):
    """The record changed after it was loaded by the editor."""


@dataclass(frozen=True)
class _Entity:
    fields: frozenset[str]
    defaults: dict
    required: frozenset[str]
    has_updated_at: bool = False


_COMMON = {"code", "name", "name_urdu"}
_REQUIRED = frozenset({"code", "name"})
_CONTACT_TEXT_LIMITS = {
    "address": 500, "address_urdu": 500, "phone": 80, "fax": 80, "mobile": 80,
    "email": 254, "website": 2048, "sales_tax_number": 80, "national_tax_number": 80,
}
_COMPANY_CONTACTS = frozenset(_CONTACT_TEXT_LIMITS) - {"mobile"}
_ACCOUNT_CONTACTS = frozenset(_CONTACT_TEXT_LIMITS) - {"address_urdu", "fax"}
ENTITIES = {
    "companies": _Entity(frozenset(_COMMON | {"fiscal_start", "fiscal_end", "cash_account_id", "is_active"}) | _COMPANY_CONTACTS,
                         {"name_urdu": None, "fiscal_start": None, "fiscal_end": None, "cash_account_id": None, "is_active": 1,
                          **dict.fromkeys(_COMPANY_CONTACTS)}, _REQUIRED, True),
    "cities": _Entity(frozenset(_COMMON), {"name_urdu": None}, _REQUIRED),
    "accounts": _Entity(frozenset(_COMMON | {"parent_id", "city_id", "kind", "classification", "is_postable", "is_active"}) | _ACCOUNT_CONTACTS,
                        {"name_urdu": None, "parent_id": None, "city_id": None, "kind": "general", "classification": None,
                         "is_postable": 1, "is_active": 1, **dict.fromkeys(_ACCOUNT_CONTACTS)}, _REQUIRED, True),
    "units": _Entity(frozenset(_COMMON | {"is_active"}), {"name_urdu": None, "is_active": 1}, _REQUIRED),
    "product_groups": _Entity(frozenset(_COMMON | {"parent_id"}), {"name_urdu": None, "parent_id": None}, _REQUIRED),
    "products": _Entity(frozenset(_COMMON | {"group_id", "small_unit_id", "large_unit_id", "unit_factor", "sale_rate", "purchase_rate",
                                             "sale_rate_known", "purchase_rate_known",
                                             "purchase_account_id", "sale_account_id", "inventory_account_id", "is_active"}),
                        {"name_urdu": None, "group_id": None, "large_unit_id": None, "unit_factor": "1", "sale_rate": "0", "purchase_rate": "0",
                         "sale_rate_known": 1, "purchase_rate_known": 1,
                         "purchase_account_id": None, "sale_account_id": None, "inventory_account_id": None, "is_active": 1},
                        _REQUIRED | {"small_unit_id"}, True),
    "stores": _Entity(frozenset(_COMMON | {"is_active"}), {"name_urdu": None, "is_active": 1}, _REQUIRED),
    "company_stores": _Entity(frozenset({"company_id", "store_id", "is_active"}), {"is_active": 1}, frozenset({"company_id", "store_id"})),
}
LOOKUP_MODULES = frozenset({
    "sales", "counter_sales", "purchases", "sales_returns", "purchase_returns", "stock", "journals",
    "receipts", "payments", "breakage_receive", "breakage_dispatch", "delivery_challans", "stock_transfers",
})
_KINDS = frozenset({"group", "general", "customer", "supplier", "both", "cash", "bank"})
_CLASSIFICATIONS = frozenset({"asset", "liability", "equity", "income", "expense"})
_PRODUCT_ACCOUNTS = ("purchase_account_id", "sale_account_id", "inventory_account_id")
_AUDIT_ACTIONS = ("masters.created", "masters.updated")
_CODE_LENGTH = 80


def _entity(value):
    if not isinstance(value, str) or value not in ENTITIES:
        raise MasterValidationError("Unknown master entity.")
    return ENTITIES[value]


def _group_section(entity, section):
    if section is not None and (entity != "product_groups" or not isinstance(section, str)
                                or section not in {"main", "sub"}):
        raise MasterValidationError("Choose the main or sub item-group section.")
    return section


def _identifier(value, *, field="record ID", optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise MasterValidationError(f"{field} must be a native UUID.")
    try:
        valid = str(UUID(value)) == value
    except ValueError:
        valid = False
    if not valid:
        raise MasterValidationError(f"{field} must be a canonical native UUID.")
    return value


def _key(entity, identifier):
    if entity == "company_stores":
        if not isinstance(identifier, (tuple, list)) or len(identifier) != 2:
            raise MasterValidationError("A store membership ID is (company_id, store_id).")
        values = tuple(_identifier(value) for value in identifier)
        return "company_id=? AND store_id=?", values, ":".join(values)
    identifier = _identifier(identifier)
    return "id=?", (identifier,), identifier


def _text(value, *, field, maximum, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise MasterValidationError(f"{field} must be text.")
    value = unicodedata.normalize("NFC", value.strip())
    if not value and optional:
        return None
    if not 1 <= len(value) <= maximum or any(unicodedata.category(char) in {"Cc", "Cs"} for char in value):
        raise MasterValidationError(f"{field} must contain 1–{maximum} characters without control characters.")
    return value


def _flag(value, field):
    if isinstance(value, bool):
        return int(value)
    if type(value) is int and value in (0, 1):
        return value
    raise MasterValidationError(f"{field} must be true or false.")


def _number(value, field, *, positive=False):
    try:
        number = as_decimal(value, name=field)
        _, digits, exponent = number.as_tuple()
        if max(len(digits) + exponent, 0) > 18 or max(-exponent, 0) > 6:
            raise MasterValidationError(f"{field} allows at most 18 integer and 6 fractional places.")
        if number < 0 or (positive and number == 0):
            raise MasterValidationError(f"{field} must be {'positive' if positive else 'nonnegative'}.")
        return canonical_decimal(number)
    except CalculationError as error:
        raise MasterValidationError(str(error)) from None


def _business_date(value, field):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise MasterValidationError(f"{field} must be an ISO YYYY-MM-DD date.")
    try:
        valid = date.fromisoformat(value).isoformat() == value
    except ValueError:
        valid = False
    if not valid:
        raise MasterValidationError(f"{field} must be a valid ISO YYYY-MM-DD date.")
    return value


def _literal_search(value):
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class MasterService:
    def __init__(self, path, security=None, clock=time.time):
        self.path = Path(path).resolve()
        self.clock = clock
        self.security = security if security is not None else SecurityService(self.path, clock=clock)
        with native_session(self.path):
            pass

    def _now(self):
        try:
            now = int(self.clock())
        except (TypeError, ValueError, OverflowError):
            raise MasterValidationError("The operation time is unavailable.") from None
        if now < 0:
            raise MasterValidationError("The operation time is unavailable.")
        return now

    def _values(self, entity, values, *, old=None):
        definition = ENTITIES[entity]
        if not isinstance(values, Mapping) or not values or any(not isinstance(key, str) for key in values):
            raise MasterValidationError("Provide a nonempty field mapping.")
        if set(values) - definition.fields:
            raise MasterValidationError("The request contains unsupported or immutable fields.")
        if old is not None and entity == "company_stores" and set(values) & {"company_id", "store_id"}:
            raise MasterValidationError("Membership keys cannot change; create a separate membership.")
        candidate = dict(definition.defaults) if old is None else {key: old[key] for key in definition.fields}
        candidate.update(values)
        if entity == "products":
            for rate in ("sale_rate", "purchase_rate"):
                known = rate + "_known"
                if rate in values:
                    candidate[known] = int(values[rate] not in (None, ""))
                    if not candidate[known]:
                        candidate[rate] = "0"
                elif known in values and not _flag(values[known], known):
                    candidate[rate] = "0"
        if definition.required - candidate.keys():
            raise MasterValidationError("Required fields are missing: " + ", ".join(sorted(definition.required - candidate.keys())))
        if entity == "accounts" and old is None and candidate["kind"] == "group" and "is_postable" not in values:
            candidate["is_postable"] = 0
        for field, value in list(candidate.items()):
            if field in {"code", "name", "name_urdu"}:
                candidate[field] = _text(value, field=field, maximum=_CODE_LENGTH if field == "code" else 150, optional=field == "name_urdu")
            elif field in _CONTACT_TEXT_LIMITS:
                candidate[field] = _text(value, field=field, maximum=_CONTACT_TEXT_LIMITS[field], optional=True)
            elif field in {"is_active", "is_postable", "sale_rate_known", "purchase_rate_known"}:
                candidate[field] = _flag(value, field)
            elif field.endswith("_id"):
                candidate[field] = _identifier(value, field=field, optional=field not in definition.required)
            elif field in {"unit_factor", "sale_rate", "purchase_rate"}:
                candidate[field] = _number(value, field, positive=field == "unit_factor")
            elif field in {"fiscal_start", "fiscal_end"}:
                candidate[field] = _business_date(value, field)
        if entity == "accounts":
            if not isinstance(candidate["kind"], str) or candidate["kind"] not in _KINDS:
                raise MasterValidationError("Unknown account kind.")
            if candidate["classification"] is not None and (not isinstance(candidate["classification"], str) or candidate["classification"] not in _CLASSIFICATIONS):
                raise MasterValidationError("Unknown account classification.")
            if candidate["kind"] == "group" and candidate["is_postable"]:
                raise MasterValidationError("Group accounts cannot accept journal postings.")
        if entity == "companies":
            start, end = candidate["fiscal_start"], candidate["fiscal_end"]
            if (start is None) != (end is None) or (start is not None and start > end):
                raise MasterValidationError("Set both fiscal dates in chronological order, or leave both unset.")
        if entity == "products":
            if candidate["large_unit_id"] in (None, candidate["small_unit_id"]) and candidate["unit_factor"] != "1":
                raise MasterValidationError("Use factor 1 when there is no separate large unit.")
        return candidate

    @staticmethod
    def _reference(connection, table, identifier, *, active=False, postable=False, kind=None):
        if identifier is None:
            return None
        row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise MasterValidationError(f"The selected {table} reference does not exist.")
        if active and "is_active" in row.keys() and not row["is_active"]:
            raise MasterValidationError(f"The selected {table} reference is inactive.")
        if postable and not row["is_postable"]:
            raise MasterValidationError("The selected account cannot accept journal postings.")
        if kind is not None and row["kind"] != kind:
            raise MasterValidationError(f"The selected account must have kind '{kind}'.")
        return row

    def _hierarchy(self, connection, entity, identifier, parent, *, active=False):
        visited = {identifier} if identifier else set()
        while parent is not None:
            if parent in visited:
                raise MasterValidationError("The parent assignment would create a hierarchy cycle.")
            visited.add(parent)
            row = self._reference(connection, entity, parent, active=active,
                                  kind="group" if entity == "accounts" else None)
            parent = row["parent_id"]

    def _references(self, connection, entity, candidate, *, identifier=None):
        active = bool(candidate.get("is_active", 1))
        if entity == "companies":
            self._reference(connection, "accounts", candidate["cash_account_id"], active=active, postable=active, kind="cash" if active else None)
        elif entity == "accounts":
            self._reference(connection, "cities", candidate["city_id"])
            self._hierarchy(connection, entity, identifier, candidate["parent_id"], active=active)
            if identifier and candidate["kind"] != "group" and connection.execute("SELECT 1 FROM accounts WHERE parent_id=? LIMIT 1", (identifier,)).fetchone():
                raise MasterValidationError("An account with children must remain a nonpostable group.")
            if identifier and (not active or not candidate["is_postable"] or candidate["kind"] != "cash"):
                if connection.execute("SELECT 1 FROM companies WHERE is_active=1 AND cash_account_id=? LIMIT 1", (identifier,)).fetchone():
                    raise MasterValidationError("An active company still uses this account as its cash account.")
            if identifier and (not active or not candidate["is_postable"]):
                if connection.execute("SELECT 1 FROM products WHERE is_active=1 AND (purchase_account_id=? OR sale_account_id=? OR inventory_account_id=?) LIMIT 1", (identifier,) * 3).fetchone():
                    raise MasterValidationError("An active product still uses this posting account.")
            if identifier and not active and connection.execute("SELECT 1 FROM accounts WHERE parent_id=? AND is_active=1 LIMIT 1", (identifier,)).fetchone():
                raise MasterValidationError("Deactivate active child accounts before their parent.")
        elif entity == "product_groups":
            self._hierarchy(connection, entity, identifier, candidate["parent_id"])
        elif entity == "products":
            self._reference(connection, "product_groups", candidate["group_id"])
            self._reference(connection, "units", candidate["small_unit_id"], active=active)
            self._reference(connection, "units", candidate["large_unit_id"], active=active)
            if identifier:
                existing = connection.execute("SELECT small_unit_id FROM products WHERE id=?", (identifier,)).fetchone()
                if existing["small_unit_id"] != candidate["small_unit_id"] and connection.execute(
                    "SELECT 1 FROM document_lines l JOIN documents d ON d.id=l.document_id "
                    "WHERE l.product_id=? AND d.status='posted' "
                    "UNION ALL SELECT 1 FROM baseline_stock_balances WHERE product_id=? LIMIT 1",
                    (identifier, identifier)).fetchone() is not None:
                    raise MasterValidationError("The base unit cannot change after an item has posted history. Create a separate item for a different base unit.")
            for field in _PRODUCT_ACCOUNTS:
                self._reference(connection, "accounts", candidate[field], active=active, postable=active)
        elif entity == "units" and identifier and not active:
            if connection.execute("SELECT 1 FROM products WHERE is_active=1 AND (small_unit_id=? OR large_unit_id=?) LIMIT 1", (identifier, identifier)).fetchone():
                raise MasterValidationError("An active product still uses this unit.")
        elif entity == "company_stores":
            self._reference(connection, "companies", candidate["company_id"], active=active)
            self._reference(connection, "stores", candidate["store_id"], active=active)

    @staticmethod
    def _last_events(connection, entity):
        rows = connection.execute(
            "SELECT e.entity_id,e.id FROM audit_events e JOIN "
            "(SELECT entity_id,MAX(rowid) AS last_row FROM audit_events "
            "WHERE entity_type=? AND action IN ('masters.created','masters.updated') GROUP BY entity_id) latest "
            "ON latest.last_row=e.rowid", (entity,))
        return {row["entity_id"]: row["id"] for row in rows}

    @staticmethod
    def _record(entity, row, event=None):
        result = dict(row)
        source = json.dumps([entity, result, event], sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        if entity == "company_stores":
            result["id"] = (result["company_id"], result["store_id"])
        result["version"] = hashlib.sha256(source.encode("utf-8")).hexdigest()
        return result

    def _records(self, connection, entity, rows):
        events = self._last_events(connection, entity)
        return [self._record(entity, row, events.get(":".join((row["company_id"], row["store_id"])) if entity == "company_stores" else row["id"])) for row in rows]

    def _audit(self, connection, actor, entity, identifier, action, fields, *, company_id=None, previous_version=None):
        event = str(uuid4())
        details = {"changed_fields": sorted(fields)}
        if previous_version is not None:
            details["previous_version"] = previous_version
        connection.execute(
            "INSERT INTO audit_events(id,actor_id,company_id,action,entity_type,entity_id,occurred_at,details_json) VALUES (?,?,?,?,?,?,?,?)",
            (event, actor.id, company_id, action, entity, identifier, self._now(), json.dumps(details, sort_keys=True, separators=(",", ":"))),
        )
        return event

    @staticmethod
    def _integrity_error(error):
        if "UNIQUE constraint failed" in str(error):
            return MasterValidationError("This code or company/store membership already exists.")
        return MasterValidationError("The record conflicts with a database integrity rule; no changes were saved.")

    def _group_references(self, connection, candidate, section, *, old=None):
        """Validate the two original group forms without changing generic trees."""
        if section is None:
            return
        if old is not None and (old["parent_id"] is None) != (section == "main"):
            raise MasterValidationError("This record belongs to the other item-group section. Reopen it there.")
        parent = candidate["parent_id"]
        if section == "main":
            if parent is not None:
                raise MasterValidationError("A main item group cannot have a parent group.")
        else:
            if parent is None:
                raise MasterValidationError("Select a main item group for this sub group.")
            row = self._reference(connection, "product_groups", parent, active=True)
            if row["parent_id"] is not None:
                raise MasterValidationError("A sub group must belong to a main item group. Review the selected parent.")

    def _create(self, connection, user, entity, values, *, group_section=None):
        """Insert and audit within the caller's authenticated write transaction."""
        definition = _entity(entity)
        candidate = self._values(entity, values)
        self._references(connection, entity, candidate)
        self._group_references(connection, candidate, group_section)
        if entity == "company_stores":
            key = (candidate["company_id"], candidate["store_id"])
        else:
            key = str(uuid4())
            candidate["id"] = key
        if definition.has_updated_at:
            candidate["created_at"] = candidate["updated_at"] = self._now()
        fields = tuple(sorted(candidate))
        connection.execute(f"INSERT INTO {entity} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", [candidate[field] for field in fields])
        where, parameters, audit_id = _key(entity, key)
        row = connection.execute(f"SELECT * FROM {entity} WHERE {where}", parameters).fetchone()
        event = self._audit(connection, user, entity, audit_id, "masters.created", set(candidate) & definition.fields,
                            company_id=key if entity == "companies" else candidate.get("company_id"))
        return self._record(entity, row, event)

    def create(self, actor, entity, values, *, group_section=None):
        _entity(entity)
        _group_section(entity, group_section)
        try:
            with native_transaction(self.path) as connection:
                user = self.security.require_admin(actor, connection=connection)
                return self._create(connection, user, entity, values, group_section=group_section)
        except sqlite3.IntegrityError as error:
            raise self._integrity_error(error) from None

    @staticmethod
    def _next_product_code(connection):
        # Text comparison after removing leading zeroes avoids SQLite's bounded
        # integer casts, floating-point rounding and Unicode digit coercion.
        largest = "0"
        rows = connection.execute(
            "SELECT code FROM products UNION ALL "
            "SELECT m.source_key FROM baseline_mappings m JOIN baseline_imports b "
            "ON b.source_sha256=m.source_sha256 "
            "WHERE m.source_table='PUR_ITEM' AND m.native_table='products'")
        for row in rows:
            value = row[0]
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
                continue
            number = value.lstrip("0") or "0"
            if (len(number), number) > (len(largest), largest):
                largest = number
        # Manual carry keeps this exact even for directly imported long keys.
        digits = list(largest)
        index = len(digits) - 1
        while index >= 0 and digits[index] == "9":
            digits[index] = "0"
            index -= 1
        if index < 0:
            digits.insert(0, "1")
        else:
            digits[index] = chr(ord(digits[index]) + 1)
        result = "".join(digits)
        if len(result) > _CODE_LENGTH:
            raise MasterValidationError(
                f"The next automatic item code exceeds {_CODE_LENGTH} digits. Review the saved item codes.")
        return result

    @staticmethod
    def _product_posting_defaults(connection):
        # Inactive items still describe existing accounting configuration. A
        # partial mapping cannot establish a pair, and ambiguity is not guessed.
        pairs = connection.execute(
            "SELECT DISTINCT purchase_account_id,sale_account_id FROM products "
            "WHERE purchase_account_id IS NOT NULL AND sale_account_id IS NOT NULL").fetchall()
        if len(pairs) != 1:
            raise MasterValidationError(
                "Items must have one consistent sales and purchases account setup before a new item can be saved. "
                "Ask the administrator to review the item accounting setup.")
        defaults = dict(pairs[0])
        conflicting_partial = connection.execute(
            "SELECT 1 FROM products WHERE "
            "(purchase_account_id IS NOT NULL AND purchase_account_id<>?) OR "
            "(sale_account_id IS NOT NULL AND sale_account_id<>?) LIMIT 1",
            (defaults["purchase_account_id"], defaults["sale_account_id"])).fetchone()
        if conflicting_partial is not None:
            raise MasterValidationError(
                "Some items have conflicting sales or purchases account mappings. "
                "Ask the administrator to review the item accounting setup.")
        for identifier in defaults.values():
            row = connection.execute(
                "SELECT kind,is_active,is_postable FROM accounts WHERE id=?", (identifier,)).fetchone()
            if row is None or not row["is_active"] or not row["is_postable"] or row["kind"] != "general":
                raise MasterValidationError(
                    "The existing item accounting setup must use active, postable general accounts for sales and purchases.")
        return defaults

    def next_product_code(self, actor):
        """Preview the next item code without reserving it or changing items."""
        with native_transaction(self.path) as connection:
            self.security.require_admin(actor, connection=connection)
            return self._next_product_code(connection)

    def create_product(self, actor, values):
        """Save an ordinary item with its code and established posting mappings."""
        try:
            with native_transaction(self.path) as connection:
                user = self.security.require_admin(actor, connection=connection)
                if not isinstance(values, Mapping) or not values:
                    raise MasterValidationError("Provide a nonempty field mapping.")
                if {"code", *_PRODUCT_ACCOUNTS} & values.keys():
                    raise MasterValidationError("Item codes and posting accounts are assigned automatically.")
                candidate = dict(values)
                candidate["code"] = self._next_product_code(connection)
                candidate.update(self._product_posting_defaults(connection))
                return self._create(connection, user, "products", candidate)
        except sqlite3.IntegrityError as error:
            raise self._integrity_error(error) from None

    def update(self, actor, entity, record_id, values, expected_version, *, group_section=None):
        definition = _entity(entity)
        _group_section(entity, group_section)
        where, parameters, audit_id = _key(entity, record_id)
        if not isinstance(expected_version, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_version):
            raise MasterValidationError("Reload the record and provide its current version.")
        try:
            with native_transaction(self.path) as connection:
                user = self.security.require_admin(actor, connection=connection)
                old = connection.execute(f"SELECT * FROM {entity} WHERE {where}", parameters).fetchone()
                if old is None:
                    raise MasterNotFoundError("The requested master record does not exist.")
                previous = self._records(connection, entity, [old])[0]
                if previous["version"] != expected_version:
                    raise MasterConflictError("The record changed after it was loaded. Reload it before saving.")
                candidate = self._values(entity, values, old=old)
                self._references(connection, entity, candidate, identifier=old["id"] if entity != "company_stores" else None)
                self._group_references(connection, candidate, group_section, old=old)
                changed = {field for field in definition.fields if candidate[field] != old[field]}
                if not changed:
                    return previous
                updates = {field: candidate[field] for field in changed}
                if definition.has_updated_at:
                    updates["updated_at"] = self._now()
                fields = tuple(sorted(updates))
                connection.execute(f"UPDATE {entity} SET " + ",".join(f"{field}=?" for field in fields) + f" WHERE {where}",
                                   [updates[field] for field in fields] + list(parameters))
                row = connection.execute(f"SELECT * FROM {entity} WHERE {where}", parameters).fetchone()
                event = self._audit(connection, user, entity, audit_id, "masters.updated", changed,
                                    company_id=record_id if entity == "companies" else candidate.get("company_id"), previous_version=expected_version)
                return self._record(entity, row, event)
        except sqlite3.IntegrityError as error:
            raise self._integrity_error(error) from None

    def get(self, actor, entity, record_id):
        _entity(entity)
        where, parameters, _ = _key(entity, record_id)
        with native_transaction(self.path) as connection:
            self.security.require_admin(actor, connection=connection)
            row = connection.execute(f"SELECT * FROM {entity} WHERE {where}", parameters).fetchone()
            if row is None:
                raise MasterNotFoundError("The requested master record does not exist.")
            return self._records(connection, entity, [row])[0]

    def list_records(self, actor, entity, *, search="", active_only=False, limit=100, offset=0, group_section=None):
        definition = _entity(entity)
        _group_section(entity, group_section)
        if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or offset < 0:
            raise MasterValidationError("Use a page size from 1 to 500 and a nonnegative offset.")
        if (not isinstance(search, str) or len(search) > 150 or type(active_only) is not bool
                or any(unicodedata.category(char) in {"Cc", "Cs"} for char in search)):
            raise MasterValidationError("Invalid master search or activity filter.")
        clauses, parameters = [], []
        source = entity + " m"
        if group_section is not None:
            clauses.append("m.parent_id IS NULL" if group_section == "main" else "m.parent_id IS NOT NULL")
        if active_only and "is_active" in definition.fields:
            clauses.append("m.is_active=1")
        if entity == "company_stores":
            source += " JOIN companies c ON c.id=m.company_id JOIN stores s ON s.id=m.store_id"
            search_fields, ordering = ("c.code", "c.name", "s.code", "s.name"), "c.code,s.code,m.company_id,m.store_id"
        else:
            search_fields, ordering = ("m.code", "m.name", "m.name_urdu"), "m.code,m.id"
        if search.strip():
            clauses.append("(" + " OR ".join(field + " LIKE ? ESCAPE '\\'" for field in search_fields) + ")")
            parameters.extend([_literal_search(search.strip())] * len(search_fields))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with native_transaction(self.path) as connection:
            self.security.require_admin(actor, connection=connection)
            rows = connection.execute(f"SELECT m.* FROM {source}{where} ORDER BY {ordering} LIMIT ? OFFSET ?", parameters + [limit, offset]).fetchall()
            return self._records(connection, entity, rows)

    @staticmethod
    def _sale_store_default(connection, company_id, stores):
        """Keep the established sale location without asking operators to pick it.

        Original sale lines take precedence over newly posted sales. Check all
        evidence before active memberships: excluding an inactive or conflicting
        location first could silently redirect stock to a different location.
        """
        active = {store["id"] for store in stores}
        for statement in (
            "SELECT DISTINCT l.store_id FROM baseline_trade_documents d "
            "JOIN baseline_trade_lines l ON l.company_id=d.company_id AND l.document_id=d.id "
            "WHERE d.company_id=? AND d.kind='sale' LIMIT 2",
            "SELECT DISTINCT l.store_id FROM documents d "
            "JOIN document_lines l ON l.company_id=d.company_id AND l.document_id=d.id "
            "WHERE d.company_id=? AND d.kind='sale' AND d.status='posted' LIMIT 2",
        ):
            observed = [row[0] for row in connection.execute(statement, (company_id,))]
            if observed:
                return observed[0] if len(observed) == 1 and observed[0] in active else None
        return next(iter(active)) if len(active) == 1 else None

    def lookup(self, actor, company_id, module, action="read"):
        company_id = _identifier(company_id, field="company_id")
        if not isinstance(module, str) or module not in LOOKUP_MODULES:
            raise MasterValidationError("This module does not have operational master lookups.")
        with native_transaction(self.path) as connection:
            self.security.require(actor, company_id, module, action, connection=connection)
            queries = {
                "companies": ("SELECT * FROM companies WHERE id=? AND is_active=1", [company_id]),
                "accounts": ("SELECT * FROM accounts WHERE is_active=1 AND is_postable=1 ORDER BY code,id", []),
                "products": (
                    "SELECT p.* FROM products p JOIN units small ON small.id=p.small_unit_id AND small.is_active=1 "
                    "LEFT JOIN units large ON large.id=p.large_unit_id "
                    "WHERE p.is_active=1 AND (p.large_unit_id IS NULL OR large.is_active=1) AND "
                    + " AND ".join(f"(p.{field} IS NULL OR EXISTS(SELECT 1 FROM accounts a WHERE a.id=p.{field} AND a.is_active=1 AND a.is_postable=1))" for field in _PRODUCT_ACCOUNTS)
                    + " ORDER BY p.code,p.id", []),
                "stores": ("SELECT s.* FROM stores s JOIN company_stores m ON m.store_id=s.id "
                           "WHERE m.company_id=? AND m.is_active=1 AND s.is_active=1 ORDER BY s.code,s.id", [company_id]),
                "units": ("SELECT * FROM units WHERE is_active=1 ORDER BY code,id", []),
                "product_groups": ("SELECT * FROM product_groups ORDER BY code,id", []),
            }
            result = {entity: self._records(connection, entity, connection.execute(statement, parameters).fetchall())
                      for entity, (statement, parameters) in queries.items()}
            result["groups"] = result.pop("product_groups")
            if module == "sales":
                result["default_sale_store_id"] = self._sale_store_default(connection, company_id, result["stores"])
            return result
