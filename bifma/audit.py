"""Administrator-only browsing of immutable native audit events.

Labels are resolved from current records, not historical identity snapshots.
Stored events remain unchanged. Authentication may update the session heartbeat.
"""

from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
from uuid import UUID

from bifma.native_schema import native_transaction
from bifma.security import SecurityService


class AuditError(ValueError):
    pass


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MASTER_TYPES = frozenset({"companies", "accounts", "cities", "units", "products", "product_groups", "stores"})


def _text_filter(value, name, maximum):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise AuditError(f"{name} must be text of at most {maximum} characters.")
    return value


def _company_id(value):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise AuditError("company_id must be a canonical UUID or None.") from None
    return value


def _date_bounds(date_from, date_to):
    parsed = []
    for value in (date_from, date_to):
        try:
            if value is None:
                parsed.append(None)
            elif isinstance(value, str) and len(value) == 10 and date.fromisoformat(value).isoformat() == value:
                parsed.append(date.fromisoformat(value))
            else:
                raise ValueError
        except ValueError:
            raise AuditError("Audit dates must be ISO dates (YYYY-MM-DD), interpreted in UTC.") from None
    if parsed[0] and parsed[1] and parsed[0] > parsed[1]:
        raise AuditError("The start date must not follow the end date.")
    # Exact integer arithmetic also supports year 0001/9999 without platform
    # timestamp limitations or overflowing date_to by adding a calendar day.
    bounds = [(value - _EPOCH.date()).days * 86400 if value else None for value in parsed]
    if bounds[1] is not None:
        bounds[1] += 86399
    return bounds


def _utc_text(seconds):
    try:
        value = _EPOCH + timedelta(seconds=seconds)
    except OverflowError:
        return f"UTC date unavailable (epoch seconds {seconds})"
    return value.isoformat(sep=" ", timespec="seconds").replace("+00:00", " UTC")


def _details(source, identifier):
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError
        return number

    def invalid_constant(value):
        raise ValueError

    try:
        result = json.loads(source, parse_float=finite_float, parse_constant=invalid_constant)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, TypeError, RecursionError):
        raise AuditError(f"Stored audit details are not a supported JSON object for event {identifier}.") from None


def _label(code, name):
    return " · ".join(value for value in (code, name) if value)


def _subjects(connection, rows):
    """Resolve only the bounded page, using allowlisted tables and safe columns."""
    groups = {}
    for row in rows:
        row["subject_label"] = _label(row["entity_type"], row["entity_id"])
        row["subject_label_source"] = "identifier"
        if row["entity_id"]:
            groups.setdefault(row["entity_type"], set()).add(row["entity_id"])
        elif row["entity_type"] == "database":
            row["subject_label"] = "Native database"
            row["subject_label_source"] = "context"
    resolved = {}
    for entity, identifiers in groups.items():
        placeholders = ",".join("?" for _ in identifiers)
        if entity in _MASTER_TYPES:
            found = connection.execute(f"SELECT id,code,name FROM {entity} WHERE id IN ({placeholders})", tuple(identifiers))
            resolved[entity] = {record["id"]: (_label(record["code"], record["name"]), None) for record in found}
        elif entity == "user":
            found = connection.execute(f"SELECT id,username,display_name FROM users WHERE id IN ({placeholders})", tuple(identifiers))
            resolved[entity] = {record["id"]: (_label(record["username"], record["display_name"]), None) for record in found}
        elif entity == "document":
            found = connection.execute(f"SELECT id,company_id,document_number,kind FROM documents WHERE id IN ({placeholders})", tuple(identifiers))
            resolved[entity] = {record["id"]: (_label(record["document_number"], record["kind"]), record["company_id"]) for record in found}
        elif entity == "company_stores":
            keys = [identifier.split(":") for identifier in identifiers if len(identifier.split(":")) == 2]
            membership = {}
            for start in range(0, len(keys), 200):
                batch = keys[start:start + 200]
                values = ",".join("(?,?)" for _ in batch)
                found = connection.execute(
                    f"WITH selected(company_id,store_id) AS (VALUES {values}) "
                    "SELECT m.company_id,m.store_id,c.code AS company_code,s.code AS store_code,s.name AS store_name "
                    "FROM company_stores m JOIN selected f ON f.company_id=m.company_id AND f.store_id=m.store_id "
                    "JOIN companies c ON c.id=m.company_id JOIN stores s ON s.id=m.store_id",
                    [value for key in batch for value in key])
                for record in found:
                    membership[record["company_id"] + ":" + record["store_id"]] = (
                        record["company_code"] + " / " + _label(record["store_code"], record["store_name"]), record["company_id"])
            resolved[entity] = membership
    for row in rows:
        subject = resolved.get(row["entity_type"], {}).get(row["entity_id"])
        if subject and (subject[1] is None or row["company_id"] is None or subject[1] == row["company_id"]):
            row["subject_label"], row["subject_label_source"] = subject[0], "current"


class AuditService:
    def __init__(self, path, security=None):
        self.path = Path(path).resolve()
        self.security = security if security is not None else SecurityService(self.path)

    def selectors(self, actor):
        """Current companies, including inactive ones, and recorded exact actions."""
        with native_transaction(self.path) as connection:
            self.security.require_admin(actor, connection=connection)
            companies = [dict(row) for row in connection.execute(
                "SELECT id,code,name,name_urdu,is_active FROM companies ORDER BY code,id")]
            actions = [row[0] for row in connection.execute("SELECT DISTINCT action FROM audit_events ORDER BY action")]
            return {"companies": companies, "actions": actions}

    def list_events(self, actor, *, company_id=None, action="", date_from=None, date_to=None, search="", offset=0, limit=100):
        """Newest first, with exact actions and literal search; see docs/AUDIT.md.

        No company filter includes global events. A selected company excludes
        global events and may be inactive. Administrator access is required even
        when a caller has ordinary module/company read grants.
        """
        company_id = _company_id(company_id)
        action = _text_filter(action, "action", 200)
        search = _text_filter(search, "search", 200)
        lower, upper = _date_bounds(date_from, date_to)
        if type(offset) is not int or offset < 0 or offset > 2**63 - 1 or type(limit) is not int or not 1 <= limit <= 500:
            raise AuditError("Use a nonnegative offset and a page limit between 1 and 500.")
        with native_transaction(self.path) as connection:
            self.security.require_admin(actor, connection=connection)
            if company_id is not None and connection.execute("SELECT 1 FROM companies WHERE id=?", (company_id,)).fetchone() is None:
                raise AuditError("The selected company does not exist.")
            clauses, parameters = [], []
            for column, operator, value in (("e.company_id", "=", company_id), ("e.action", "=", action or None),
                                             ("e.occurred_at", ">=", lower), ("e.occurred_at", "<=", upper)):
                if value is not None:
                    clauses.append(f"{column}{operator}?")
                    parameters.append(value)
            if search:
                columns = ("e.action", "e.entity_type", "e.entity_id", "u.username", "u.display_name")
                clauses.append("(" + " OR ".join(f"instr(lower(COALESCE({column},'')),lower(?))>0" for column in columns) + ")")
                parameters.extend([search] * len(columns))
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            joined = " FROM audit_events e LEFT JOIN users u ON u.id=e.actor_id LEFT JOIN companies c ON c.id=e.company_id"
            count = connection.execute("SELECT COUNT(*)" + joined + where, parameters).fetchone()[0]
            selected = connection.execute(
                "SELECT e.id,e.actor_id,e.company_id,e.action,e.entity_type,e.entity_id,e.occurred_at,e.details_json,"
                "u.username AS actor_username,u.display_name AS actor_name,"
                "c.code AS company_code,c.name AS company_name,c.name_urdu AS company_name_urdu,c.is_active AS company_is_active"
                + joined + where + " ORDER BY e.occurred_at DESC,e.rowid DESC,e.id DESC LIMIT ? OFFSET ?", (*parameters, limit, offset))
            rows = []
            for source in selected:
                row = dict(source)
                row["occurred_at_utc"] = _utc_text(row["occurred_at"])
                row["actor_label"] = _label(row["actor_username"], row["actor_name"]) or row["actor_id"] or "System / unknown"
                row["company_label"] = _label(row["company_code"], row["company_name"]) or row["company_id"] or "Global"
                row["actor_label_source"] = "current" if row["actor_username"] is not None else "identifier" if row["actor_id"] else "context"
                row["company_label_source"] = "current" if row["company_code"] is not None else "identifier" if row["company_id"] else "context"
                row["details"] = _details(row["details_json"], row["id"])
                rows.append(row)
            _subjects(connection, rows)
            return {"rows": rows, "total": count, "offset": offset, "limit": limit}
