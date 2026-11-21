"""Authorized reports for preserved baseline balances and new posted entries.

The baseline is an immutable closing snapshot, not replayed historical trades.
Historical detail remains in legacy_ledger/legacy_stock. Stock quantities are
reported with saved value provenance; unknown costs remain explicit.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal, localcontext
import json
from itertools import chain
from uuid import UUID

from bifma.native_schema import native_transaction
from bifma.security import SecurityService


MAX_EXPORT_ROWS = 100_000
_ACCOUNT_KINDS = frozenset({"group", "general", "customer", "supplier", "both", "cash", "bank"})
_BALANCE_KEYS = ("opening", "debit", "credit", "closing", "closing_debit", "closing_credit")

# Keep signed imported balances as TEXT until Python Decimal reads them. SQL
# casts, ABS and SUM would silently lose precision for large/fractional values.
# These sources are local to reports: baseline stock never creates a journal,
# and original transactions never acquire a fabricated native document ID.
_ACCOUNT_SOURCE = """(
    SELECT company_id,account_id,business_date,debit,credit,NULL AS baseline_balance
    FROM posted_journal_lines
    UNION ALL
    SELECT b.company_id,b.account_id,i.cutoff_date,'0','0',b.balance
    FROM baseline_account_balances b CROSS JOIN baseline_imports i
)"""
_STOCK_SOURCE = """(
    SELECT m.company_id,m.product_id,m.store_id,m.pool,m.business_date,m.direction,m.quantity,
           NULL AS baseline_quantity,NULL AS baseline_value,
           CASE WHEN v.movement_id IS NULL AND m.valuation_basis='opening_explicit'
                THEN m.value ELSE v.value END AS valuation_value
    FROM posted_stock_movements m LEFT JOIN stock_valuation_snapshots v ON v.movement_id=m.id
    UNION ALL
    SELECT b.company_id,b.product_id,b.store_id,b.pool,i.cutoff_date,NULL,'0',b.quantity,b.historical_value,NULL
    FROM baseline_stock_balances b CROSS JOIN baseline_imports i
)"""


def _baseline_report_info(connection, date_to=None):
    source = connection.execute("SELECT source_sha256,cutoff_date FROM baseline_imports").fetchone()
    if source is None:
        return None
    if date_to is not None and date_to < source["cutoff_date"]:
        raise ValueError("This operational report begins with the existing balances at "
                         + source["cutoff_date"] + ". Open earlier documents in their sales, purchase or voucher section; "
                         "stock totals are available in the dated stock reports.")
    return dict(source)


def _journal_amounts(row):
    if row["baseline_balance"] is None:
        return Decimal(row["debit"]), Decimal(row["credit"])
    value = Decimal(row["baseline_balance"])
    return max(value, Decimal(0)), max(-value, Decimal(0))


def _stock_amount(row):
    if row["baseline_quantity"] is None:
        return row["direction"], Decimal(row["quantity"])
    value = Decimal(row["baseline_quantity"])
    return ("out", -value) if value < 0 else ("in", value)


def _value_totals():
    return {"value_opening": Decimal(0), "value_in": Decimal(0), "value_out": Decimal(0),
            "missing_valuation_count": 0}


def _add_value(totals, value, direction, before):
    key = "value_opening" if before else "value_" + direction
    if value is None:
        totals["missing_valuation_count"] += 1
        totals[key] = None
    elif totals[key] is not None:
        totals[key] += -value if before and direction == "out" else value


def _finish_values(totals):
    totals["value_closing"] = (totals["value_opening"] + totals["value_in"] - totals["value_out"]
                               if all(totals[key] is not None for key in ("value_opening", "value_in", "value_out")) else None)

_TRADE_FAMILIES = {"sales": (("sale", "sales"), ("sale_return", "sales_returns")),
                   "purchases": (("purchase", "purchases"), ("purchase_return", "purchase_returns"))}
_CASH_TRADE_FAMILIES = {"sales": (("cash_sale", "counter_sales"), ("cash_sale_return", "sales_returns")),
                        "purchases": (("cash_purchase", "purchases"), ("cash_purchase_return", "purchase_returns"))}
_CASH_TRADE_KINDS = frozenset(pair[0] for family in _CASH_TRADE_FAMILIES.values() for pair in family)


def _trade_scope(family, kind, payment_mode="account"):
    if not isinstance(family, str) or family not in _TRADE_FAMILIES:
        raise ValueError("Select the sales or purchases report family.")
    if not isinstance(kind, str) or kind not in {"all", "invoice", "return"}:
        raise ValueError("Select all, invoice or return activity.")
    if not isinstance(payment_mode, str) or payment_mode not in {"account", "cash", "all"}:
        raise ValueError("Select account, cash or all payment modes.")
    families = (_TRADE_FAMILIES, _CASH_TRADE_FAMILIES) if payment_mode == "all" else (
        _CASH_TRADE_FAMILIES if payment_mode == "cash" else _TRADE_FAMILIES,)
    activities = (0, 1) if kind == "all" else (0 if kind == "invoice" else 1,)
    chosen = tuple(modes[family][activity] for activity in activities for modes in families)
    return tuple(item[0] for item in chosen), tuple(dict.fromkeys(item[1] for item in chosen))


def _trade_filters(family, kind, date_from, date_to, account_id, product_id, store_id, search, payment_mode="account"):
    scope = _trade_scope(family, kind, payment_mode)
    _dates(date_from, date_to)
    for field, value in (("account_id", account_id), ("product_id", product_id), ("store_id", store_id)):
        if value is not None:
            try:
                valid = isinstance(value, str) and str(UUID(value)) == value
            except (ValueError, AttributeError):
                valid = False
            if not valid:
                raise ValueError(f"{field} must identify a saved record or be None.")
    if not isinstance(search, str) or len(search) > 200:
        raise ValueError("Document search must contain at most 200 characters.")
    return scope


def _trade_predicate(company_id, kinds):
    placeholders = ",".join("?" for _ in kinds)
    return ("d.company_id=? AND d.status='posted' AND "
            f"(d.kind IN ({placeholders}) OR (d.kind='reversal' AND original.status='posted' "
            f"AND original.kind IN ({placeholders})))", [company_id, *kinds, *kinds])


def _saved_trade_label(value, identifier, entity):
    if (not isinstance(value, dict) or value.get("id") != identifier
            or any(not isinstance(value.get(key), str) or not value[key] for key in ("code", "name"))
            or value.get("name_urdu") is not None and not isinstance(value["name_urdu"], str)):
        raise ValueError(f"A posted trade has an unavailable or inconsistent saved {entity} label.")
    return {entity + "_" + key: value.get(key) for key in ("code", "name", "name_urdu")}


def _trade_saved_rows(header, lines):
    """Use saved identities/labels; never substitute renamed current masters."""
    try:
        snapshot = json.loads(header["snapshot_json"])
        if not isinstance(snapshot, dict) or snapshot.get("company", {}).get("id") != header["company_id"]:
            raise ValueError
        accounts, saved = snapshot["accounts"], snapshot["lines"]
        if not isinstance(accounts, dict) or not isinstance(saved, list):
            raise ValueError
        origins = {}
        for item in saved:
            if not isinstance(item, dict) or not isinstance(item.get("line_id"), str) or item["line_id"] in origins:
                raise ValueError
            origins[item["line_id"]] = item
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("A posted trade has an unavailable or inconsistent saved snapshot.") from None
    party = _saved_trade_label(accounts.get(header["account_id"]), header["account_id"], "account")
    for line in lines:
        origin = origins.get(line["id"])
        if not isinstance(origin, dict):
            raise ValueError("A posted trade line has no matching saved snapshot.")
        product = origin.get("product")
        labels = {**party, **_saved_trade_label(product, line["product_id"], "product"),
                  **_saved_trade_label(origin.get("store"), line["store_id"], "store")}
        unit_id = product.get("small_unit_id")
        if not isinstance(unit_id, str) or not unit_id:
            raise ValueError("A posted trade line has no saved base unit.")
        labels.update(_saved_trade_label(product.get("small_unit"), unit_id, "unit"), unit_id=unit_id)
        yield line, labels


def _trade_totals():
    return {key: Decimal(0) for key in ("invoice", "return", "reversal", "net")}


def _add_trade_total(totals, effect, value):
    totals[effect] += value
    totals["net"] += value


def _row_limit(count, maximum):
    if maximum is not None and count > maximum:
        raise ValueError(f"Report exceeds {maximum:,} rows. Narrow its filters before exporting.")


def _dates(date_from, date_to):
    for value in (date_from, date_to):
        if value is not None:
            if not isinstance(value, str) or len(value) != 10 or date.fromisoformat(value).isoformat() != value:
                raise ValueError("Use a date in YYYY-MM-DD format.")
    if date_from and date_to and date_from > date_to:
        raise ValueError("The start date must not follow the end date.")


def _limits(limit, offset):
    if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or offset < 0:
        raise ValueError("Invalid report page size or offset.")


def _record_id(value, label):
    try:
        valid = isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        raise ValueError(f"{label} must identify a saved record.")
    return value


def _city_filters(date_from, date_to, city_id, account_kind, search, include_zero):
    _dates(date_from, date_to)
    if city_id is not None and city_id != "unassigned":
        _record_id(city_id, "city_id")
    if account_kind is not None and (not isinstance(account_kind, str) or account_kind not in _ACCOUNT_KINDS):
        raise ValueError("Select an exact current account kind or all kinds.")
    if not isinstance(search, str) or len(search) > 200 or "\x00" in search:
        raise ValueError("Account search must contain at most 200 characters without NUL.")
    if type(include_zero) is not bool:
        raise ValueError("include_zero must be True or False.")


def _city_choice(row):
    return {"id": row["id"], "code": row["code"], "name": row["name"], "name_urdu": row["name_urdu"]} if row else {
        "id": "unassigned", "code": "", "name": "Unassigned", "name_urdu": None}


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate snapshot key")
        result[key] = value
    return result


def _saved_cash_accounts(snapshot_json, company_id, posted_accounts):
    """Only a consistent saved account on an actual posted line proves a role.

    Absent, malformed or ambiguous older metadata grants no cash eligibility;
    its otherwise valid journal remains accessible through the ordinary ledger.
    """
    try:
        snapshot = json.loads(snapshot_json, object_pairs_hook=_unique_json_object)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("company"), dict) or snapshot["company"].get("id") != company_id:
            return set()
        accounts = snapshot.get("accounts")
        if not isinstance(accounts, dict):
            return set()
        valid = set()
        for identifier in posted_accounts:
            saved = accounts.get(identifier)
            if (isinstance(saved, dict) and saved.get("id") == identifier and saved.get("kind") == "cash"
                    and all(isinstance(saved.get(key), str) and saved[key].strip() for key in ("code", "name"))
                    and (saved.get("name_urdu") is None or isinstance(saved.get("name_urdu"), str))):
                valid.add(identifier)
        return valid
    except (ValueError, TypeError, RecursionError):
        return set()


def _stock_product(connection, company_id, product_id):
    if not isinstance(product_id, str) or not product_id:
        raise ValueError("Select an item for the stock ledger.")
    product = connection.execute(
        "SELECT p.id,p.code,p.name,p.name_urdu,p.is_active FROM products p WHERE p.id=? "
        f"AND (p.is_active=1 OR EXISTS (SELECT 1 FROM {_STOCK_SOURCE} m "
        "WHERE m.company_id=? AND m.product_id=p.id))", (product_id, company_id)).fetchone()
    if product is None:
        raise ValueError("Select an active item or one with posted stock history in this company.")
    return dict(product)


def _stock_store(connection, company_id, store_id):
    if not isinstance(store_id, str) or not store_id:
        raise ValueError("Select a store belonging to this company.")
    store = connection.execute(
        "SELECT s.id,s.code,s.name,s.name_urdu,s.is_active FROM stores s WHERE s.id=? "
        "AND (EXISTS (SELECT 1 FROM company_stores cs WHERE cs.company_id=? AND cs.store_id=s.id) "
        f"OR EXISTS (SELECT 1 FROM {_STOCK_SOURCE} m WHERE m.company_id=? AND m.store_id=s.id))",
        (store_id, company_id, company_id)).fetchone()
    if store is None:
        raise ValueError("Select a store belonging to this company or its posted stock history.")
    return dict(store)


def _metadata_snapshots(connection, company_id, document_ids):
    """Bound each IN list for full exports as well as ordinary report pages."""
    identifiers = list(document_ids)
    for start in range(0, len(identifiers), 500):
        batch = identifiers[start:start + 500]
        placeholders = ",".join("?" for _ in batch)
        yield from connection.execute(
            "SELECT document_id,snapshot_json FROM document_metadata WHERE company_id=? "
            f"AND document_id IN ({placeholders})", (company_id, *batch))


def _stock_snapshot_labels(connection, company_id, rows):
    """Load each paginated document's snapshot once; never fetch per movement.

    Posted document numbers/dates already come from immutable document headers.
    Only human product/store labels are replaced by saved snapshot labels here.
    Older native documents may have no snapshot; their explicit fallback is the
    current master label, without altering any quantity or movement reference.
    """
    by_document = {}
    for row in rows:
        if row.get("source") == "baseline":
            continue
        row.update(product_label_source="current", store_label_source="current")
        by_document.setdefault(row["document_id"], []).append(row)
    if not by_document:
        return
    for metadata in _metadata_snapshots(connection, company_id, by_document):
        snapshot = json.loads(metadata["snapshot_json"])
        saved_lines = snapshot.get("lines", [])
        origins = {row["line_id"]: row for row in saved_lines if isinstance(row, dict) and isinstance(row.get("line_id"), str)} if isinstance(saved_lines, list) else {}
        original_snapshot = snapshot.get("original_snapshot")
        original_lines = original_snapshot.get("lines", []) if isinstance(original_snapshot, dict) else []
        reversal_origins = {row["line_id"]: row for row in original_lines if isinstance(row, dict) and isinstance(row.get("line_id"), str)} if isinstance(original_lines, list) else {}
        for row in by_document[metadata["document_id"]]:
            origin = origins.get(row["document_line_id"])
            if origin is None and row["kind"] == "reversal":
                origin = reversal_origins.get(row["source_line_id"])
            if not isinstance(origin, dict):
                continue
            for entity in ("product", "store"):
                saved = origin.get(entity)
                if (isinstance(saved, dict) and saved.get("id", row[entity + "_id"]) == row[entity + "_id"]
                        and isinstance(saved.get("code"), str) and saved["code"]
                        and isinstance(saved.get("name"), str) and saved["name"]):
                    row[entity + "_code"], row[entity + "_name"] = saved["code"], saved["name"]
                    row[entity + "_name_urdu"] = saved.get("name_urdu") if isinstance(saved.get("name_urdu"), str) else None
                    row[entity + "_label_source"] = "snapshot"


def _baseline_stock_ledger_rows(connection, company_id, product_id, store_id, pool, baseline):
    if baseline is None:
        return
    rows = connection.execute(
        "SELECT b.* FROM baseline_stock_balances b JOIN stores s ON s.id=b.store_id "
        "WHERE b.company_id=? AND b.product_id=? AND b.pool=? AND (? IS NULL OR b.store_id=?) "
        "ORDER BY s.code,b.store_id", (company_id, product_id, pool, store_id, store_id))
    for source in rows:
        quantity = Decimal(source["quantity"])
        row = {"movement_id": "baseline:" + ":".join((company_id, source["store_id"], product_id, pool)),
               "company_id": company_id, "document_id": None, "document_line_id": None,
               "product_id": product_id, "store_id": source["store_id"], "pool": pool,
               "direction": "out" if quantity < 0 else "in", "quantity": abs(quantity),
               "business_date": baseline["cutoff_date"], "kind": "baseline_opening",
               "document_number": "Existing balances", "posting_sequence": None, "posted_at": None,
               "line_number": 0, "source_line_id": None, "reverses_document_id": None,
               "reverses_document_number": None, "reverses_movement_id": None,
               "source": "baseline", "source_sha256": baseline["source_sha256"],
               "base_unit_id": source["base_unit_id"], "unit_cost": None,
               "baseline_value": source["historical_value"], "valuation_value": None,
               "valuation_source_json": json.dumps({"method": "historical_snapshot", "source_sha256": baseline["source_sha256"]})}
        try:
            labels = json.loads(source["labels_json"], object_pairs_hook=_unique_json_object)
            for entity in ("product", "store"):
                saved = labels[entity]
                if saved["id"] != row[entity + "_id"]:
                    raise ValueError
                row.update(_saved_trade_label(saved, row[entity + "_id"], entity))
                row[entity + "_label_source"] = "baseline"
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            raise ValueError("An imported stock balance has an unavailable or inconsistent saved label.") from None
        yield row


def _baseline_account_ledger_rows(connection, company_id, account_id, baseline):
    if baseline is None:
        return
    source = connection.execute(
        "SELECT balance FROM baseline_account_balances WHERE company_id=? AND account_id=?",
        (company_id, account_id)).fetchone()
    if source is not None:
        balance = Decimal(source["balance"])
        yield {"id": "baseline:" + company_id + ":" + account_id, "company_id": company_id,
               "account_id": account_id, "entry_id": None, "document_id": None, "line_number": 0,
               "business_date": baseline["cutoff_date"], "kind": "baseline_opening",
               "document_number": "Existing balances", "narration": "Original archive closing balance",
               "debit": max(balance, Decimal(0)), "credit": max(-balance, Decimal(0)),
               "source": "baseline", "source_sha256": baseline["source_sha256"]}


class NativeReports:
    def __init__(self, path, *, security=None):
        self.path = path
        self.security = security or SecurityService(path)

    def export_snapshot(self, actor, company_id, report, **filters):
        """Read a complete bounded report and current company in one transaction.

        This export boundary accepts no pagination inputs. It shares query and
        arithmetic code with the screen reports, and raises instead of returning
        a truncated result when more than MAX_EXPORT_ROWS would be printed.
        """
        specifications = {
            "trial_balance": ("trial_balance", set(), set()),
            "sub_trial_balance": ("trial_balance", set(), set()),
            "account_ledger": ("ledger", {"account_id"}, {"account_id"}),
            "cash_book": ("ledger", {"account_id"}, {"account_id"}),
            "city_balances": ("trial_balance", {"city_id", "account_kind", "search", "include_zero"}, set()),
            "profit_loss": ("reports", set(), set()),
            "stock_quantities": ("stock", {"store_id", "pool", "search"}, set()),
            "stock_valuation": ("stock", {"store_id", "pool", "search"}, set()),
            "item_ledger": ("stock", {"product_id", "store_id", "pool"}, {"product_id"}),
            "sales_detail": ("sales", {"kind", "payment_mode", "account_id", "product_id", "store_id", "search"}, set()),
            "purchase_detail": ("purchases", {"kind", "payment_mode", "account_id", "product_id", "store_id", "search"}, set()),
        }
        if not isinstance(report, str) or report not in specifications:
            raise ValueError("Unknown native report export.")
        module, allowed, required = specifications[report]
        unexpected = set(filters) - (allowed | {"date_from", "date_to"})
        if unexpected:
            raise ValueError("Unsupported report filters: " + ", ".join(sorted(unexpected)))
        for key in required:
            if not isinstance(filters.get(key), str) or not filters[key]:
                raise ValueError(f"{key} is required for this report.")
        if "store_id" in filters and filters["store_id"] is not None and (not isinstance(filters["store_id"], str) or not filters["store_id"]):
            raise ValueError("store_id must identify a store or be None.")
        normalized = {"date_from": None, "date_to": None, **filters}
        _dates(normalized["date_from"], normalized["date_to"])
        if report == "city_balances":
            normalized = {"city_id": None, "account_kind": None, "search": "", "include_zero": True, **normalized}
            _city_filters(**normalized)
        elif report == "cash_book":
            _record_id(normalized["account_id"], "account_id")
        required_modules = (module,)
        if report == "profit_loss":
            required_modules = ("reports", "trial_balance", "stock")
        family = {"sales_detail": "sales", "purchase_detail": "purchases"}.get(report)
        if family:
            normalized = {"kind": "all", "payment_mode": "account", "account_id": None, "product_id": None, "store_id": None,
                          "search": "", **normalized}
            _, required_modules = _trade_filters(family, **normalized)
            module = required_modules[0]
        if report in {"stock_quantities", "stock_valuation", "item_ledger"}:
            normalized.setdefault("pool", "regular")
            normalized.setdefault("store_id", None)
            if not isinstance(normalized["pool"], str) or normalized["pool"] not in {"regular", "breakage"}:
                raise ValueError("Select the regular or breakage stock pool.")
        if report in {"stock_quantities", "stock_valuation"}:
            normalized.setdefault("search", "")
            if not isinstance(normalized["search"], str) or len(normalized["search"]) > 200:
                raise ValueError("Invalid stock search filter.")
        with native_transaction(self.path) as connection, localcontext(Context(prec=1000)):
            for required_module in required_modules:
                self.security.require(actor, company_id, required_module, "read", connection=connection)
            company = dict(connection.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone())
            options = {**normalized, "max_rows": MAX_EXPORT_ROWS}
            if family:
                result = self._query_trade_detail(connection, company_id, family, limit=MAX_EXPORT_ROWS, **options)
            elif report == "trial_balance":
                result = self._query_trial_balance(connection, company_id, **options)
            elif report == "sub_trial_balance":
                result = self._query_sub_trial_balance(connection, company_id, **options)
            elif report == "account_ledger":
                result = self._query_ledger(connection, company_id, limit=MAX_EXPORT_ROWS, **options)
            elif report == "cash_book":
                result = self._query_cash_book(connection, company_id, limit=MAX_EXPORT_ROWS, **options)
            elif report == "city_balances":
                result = self._query_city_balances(connection, company_id, limit=MAX_EXPORT_ROWS, **options)
            elif report == "profit_loss":
                result = self._query_profit_loss(connection, company_id, **options)
                normalized["date_from"] = result["date_from"]
            elif report == "item_ledger":
                result = self._query_stock_ledger(connection, company_id, limit=MAX_EXPORT_ROWS, **options)
            elif report == "stock_valuation":
                result = self._query_stock_valuation(connection, company_id, **options)
            else:
                result = {"rows": self._query_stock(connection, company_id, **options)}
                result["store"] = _stock_store(connection, company_id, normalized["store_id"]) if normalized["store_id"] else None
            return {"report": report, "module": module, "required_modules": required_modules, "company": company,
                    "baseline": _baseline_report_info(connection),
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "filters": normalized, "result": result}

    def trade_selectors(self, actor, company_id, family, kind="all", *, payment_mode="account"):
        """Current labels of selected posted trade history, including inactive masters.

        Every selected activity and payment-mode permission is required. No
        ledger, stock or master grant is needed. Selectors have no date restriction.
        """
        kinds, modules = _trade_scope(family, kind, payment_mode)
        with native_transaction(self.path) as connection:
            for module in modules:
                self.security.require(actor, company_id, module, "read", connection=connection)
            return self._query_trade_selectors(connection, company_id, kinds)

    @staticmethod
    def _query_trade_selectors(connection, company_id, kinds):
        predicate, parameters = _trade_predicate(company_id, kinds)
        result = {}
        for table, identifier in (("accounts", "d.account_id"), ("products", "l.product_id"), ("stores", "l.store_id")):
            result[table] = [dict(row) for row in connection.execute(
                f"SELECT DISTINCT m.id,m.code,m.name,m.name_urdu,m.is_active FROM {table} m "
                f"JOIN documents d ON {('m.id=d.account_id' if table == 'accounts' else '1=1')} "
                "JOIN document_lines l ON l.document_id=d.id AND l.company_id=d.company_id "
                "LEFT JOIN documents original ON original.id=d.reverses_document_id AND original.company_id=d.company_id "
                f"WHERE {predicate} AND m.id={identifier} ORDER BY m.code,m.id", parameters)]
        return result

    def trade_detail(self, actor, company_id, family, *, kind="all", payment_mode="account", date_from=None, date_to=None,
                     account_id=None, product_id=None, store_id=None, search="", offset=0, limit=100):
        """Saved invoice/return lines and dated reversals; no inferred cost or tax.

        All monetary and quantity totals are signed over the FULL selected
        period. Invoice is positive, return negative; reversal negates its
        original kind. Original rows remain on their own date. Net line amounts
        are stored pre-header/pre-rounding amounts, never current-price guesses.
        Header document totals are included once only when product/store filters
        are absent; filtered lines cannot determine allocated invoice charges.
        Quantity totals group by product and saved base-unit ID, with the first
        chronological saved labels for that group. There is no cross-item sum.
        Snapshots are mandatory for every selected line, including earlier pages.
        Separate page requests may reflect subsequently committed documents.
        """
        _, modules = _trade_filters(family, kind, date_from, date_to, account_id, product_id, store_id, search, payment_mode)
        _limits(limit, offset)
        with native_transaction(self.path) as connection, localcontext(Context(prec=1000)):
            for module in modules:
                self.security.require(actor, company_id, module, "read", connection=connection)
            return self._query_trade_detail(connection, company_id, family, kind=kind, payment_mode=payment_mode, date_from=date_from,
                    date_to=date_to, account_id=account_id, product_id=product_id, store_id=store_id,
                    search=search, offset=offset, limit=limit)

    @staticmethod
    def _query_trade_detail(connection, company_id, family, *, kind="all", payment_mode="account", date_from=None, date_to=None,
                           account_id=None, product_id=None, store_id=None, search="", offset=0, limit=100, max_rows=None):
        kinds, _ = _trade_scope(family, kind, payment_mode)
        selectors = NativeReports._query_trade_selectors(connection, company_id, kinds)
        selected = {}
        for field, identifier, table in (("account", account_id, "accounts"), ("product", product_id, "products"), ("store", store_id, "stores")):
            selected[field] = next((row for row in selectors[table] if row["id"] == identifier), None) if identifier else None
            if identifier is not None and selected[field] is None:
                raise ValueError(f"Select a {field} with posted {family} history in this company, kind and payment mode.")
        predicate, parameters = _trade_predicate(company_id, kinds)
        for field, operator, value in (("d.business_date", ">=", date_from), ("d.business_date", "<=", date_to), ("d.account_id", "=", account_id)):
            if value is not None:
                predicate += f" AND {field}{operator}?"
                parameters.append(value)
        line_clause, line_parameters = "", []
        for field, value in (("product_id", product_id), ("store_id", store_id)):
            if value is not None:
                line_clause += f" AND l.{field}=?"
                line_parameters.append(value)
        if line_parameters:
            predicate += " AND EXISTS (SELECT 1 FROM document_lines l WHERE l.document_id=d.id AND l.company_id=d.company_id" + line_clause + ")"
            parameters.extend(line_parameters)
        headers = connection.execute(
            "SELECT d.*,original.kind AS original_kind,original.document_number AS reverses_document_number,m.snapshot_json "
            "FROM documents d LEFT JOIN documents original ON original.id=d.reverses_document_id AND original.company_id=d.company_id "
            "LEFT JOIN document_metadata m ON m.document_id=d.id AND m.company_id=d.company_id "
            "WHERE " + predicate + " ORDER BY d.business_date,d.posting_sequence,d.posted_at,d.id", parameters)
        page, count, document_count, quantities = [], 0, 0, {}
        totals = _trade_totals()
        document_totals = _trade_totals() if product_id is None and store_id is None else None
        term = search.strip().casefold()
        while True:
            # Snapshots may contain thousands of saved lines per document.
            # Keep their batch bounded independently of SQLite's variable cap.
            batch = headers.fetchmany(10)
            if not batch:
                break
            batch = [row for row in batch if not term or term in row["document_number"].casefold()]
            if not batch:
                continue
            identifiers = [row["id"] for row in batch]
            placeholders = ",".join("?" for _ in identifiers)
            by_document = {identifier: [] for identifier in identifiers}
            batch_count = 0
            for line in connection.execute(
                    "SELECT l.* FROM document_lines l WHERE l.company_id=? "
                    f"AND l.document_id IN ({placeholders})" + line_clause + " ORDER BY l.document_id,l.line_number,l.id",
                    [company_id, *identifiers, *line_parameters]):
                batch_count += 1
                _row_limit(count + batch_count, max_rows)
                by_document[line["document_id"]].append(dict(line))
            for header in batch:
                lines = by_document[header["id"]]
                if not lines:
                    raise ValueError("A posted trade has no saved item lines.")
                source_kind = header["original_kind"] if header["kind"] == "reversal" else header["kind"]
                is_invoice = source_kind in (_TRADE_FAMILIES[family][0][0], _CASH_TRADE_FAMILIES[family][0][0])
                effect = "reversal" if header["kind"] == "reversal" else "invoice" if is_invoice else "return"
                sign = (1 if is_invoice else -1) * (-1 if effect == "reversal" else 1)
                document_count += 1
                if document_totals is not None:
                    _add_trade_total(document_totals, effect, Decimal(header["total_amount"]) * sign)
                for line, labels in _trade_saved_rows(header, lines):
                    count += 1
                    quantity, amount = Decimal(line["quantity"]), Decimal(line["net_amount"])
                    signed_quantity, signed_amount = quantity * sign, amount * sign
                    _add_trade_total(totals, effect, signed_amount)
                    key = line["product_id"], labels["unit_id"]
                    if key not in quantities:
                        quantities[key] = {"product_id": key[0], **{name: value for name, value in labels.items() if name.startswith(("product_", "unit_"))}, **_trade_totals()}
                    _add_trade_total(quantities[key], effect, signed_quantity)
                    if offset <= count - 1 < offset + limit:
                        page.append({"document_id": header["id"], "document_line_id": line["id"],
                            **{field: header[field] for field in ("document_number", "business_date", "kind", "posting_sequence", "posted_at", "account_id", "reverses_document_id", "reverses_document_number")},
                            **{field: line[field] for field in ("line_number", "product_id", "store_id", "source_line_id")},
                            **labels, "source_kind": source_kind,
                            "payment_mode": "cash" if source_kind in _CASH_TRADE_KINDS else "account",
                            "sign": sign, "quantity": quantity,
                            "rate": Decimal(line["rate"]), "discount_percent": Decimal(line["discount_percent"]),
                            "net_amount": amount, "signed_quantity": signed_quantity, "signed_net_amount": signed_amount})
        return {"family": family, "kind": kind, "payment_mode": payment_mode, "date_from": date_from, "date_to": date_to, "search": search,
                **selected, "rows": page, "total": count, "document_count": document_count, "offset": offset,
                "limit": limit, "totals": totals, "document_totals": document_totals,
                "quantity_totals": sorted(quantities.values(), key=lambda row: (
                    row["product_code"], row["product_name"], row["unit_code"], row["unit_name"],
                    row["product_id"], row["unit_id"]))}

    def accounts(self, actor, company_id):
        """Include inactive/nonpostable accounts that still have posted history."""
        with native_transaction(self.path) as connection:
            self.security.require(actor, company_id, "ledger", "read", connection=connection)
            return [dict(row) for row in connection.execute(
                "SELECT a.id,a.code,a.name,a.is_active FROM accounts a WHERE a.is_postable=1 OR EXISTS "
                f"(SELECT 1 FROM {_ACCOUNT_SOURCE} l WHERE l.company_id=? AND l.account_id=a.id) ORDER BY a.code,a.id", (company_id,))]

    def city_selectors(self, actor, company_id):
        """Current cities used by this company's posted account history."""
        with native_transaction(self.path) as connection:
            self.security.require(actor, company_id, "trial_balance", "read", connection=connection)
            rows = connection.execute(
                f"SELECT DISTINCT c.id,c.code,c.name,c.name_urdu FROM {_ACCOUNT_SOURCE} l "
                "JOIN accounts a ON a.id=l.account_id LEFT JOIN cities c ON c.id=a.city_id "
                "WHERE l.company_id=? ORDER BY COALESCE(c.code,''),c.id", (company_id,))
            return {"cities": [_city_choice(row if row["id"] is not None else None) for row in rows],
                    "label_basis": "current_master"}

    def city_balances(self, actor, company_id, *, date_from=None, date_to=None, city_id=None,
                      account_kind=None, search="", include_zero=True, limit=100, offset=0):
        """Account balances grouped by current city, with full filtered totals.

        Only accounts with posted activity through the end date participate.
        Opening/closing are signed debit minus credit; closing debit and credit
        are split per account before grouping. Excluding zero closing accounts
        excludes all of their amounts from the report, including period totals.
        """
        _city_filters(date_from, date_to, city_id, account_kind, search, include_zero)
        _limits(limit, offset)
        with native_transaction(self.path) as connection, localcontext(Context(prec=1000)):
            self.security.require(actor, company_id, "trial_balance", "read", connection=connection)
            return self._query_city_balances(connection, company_id, date_from=date_from, date_to=date_to,
                city_id=city_id, account_kind=account_kind, search=search, include_zero=include_zero,
                limit=limit, offset=offset)

    @staticmethod
    def _query_city_balances(connection, company_id, *, date_from=None, date_to=None, city_id=None,
                            account_kind=None, search="", include_zero=True, limit=100, offset=0, max_rows=None):
        baseline = _baseline_report_info(connection, date_to)
        selected = None
        if city_id == "unassigned":
            selected = _city_choice(None)
        elif city_id is not None:
            found = connection.execute("SELECT id,code,name,name_urdu FROM cities WHERE id=?", (city_id,)).fetchone()
            if found is None:
                raise ValueError("Select a saved city or Unassigned.")
            selected = _city_choice(found)
        where, parameters = ["l.company_id=?"], [company_id]
        if date_to is not None:
            where.append("l.business_date<=?")
            parameters.append(date_to)
        if city_id == "unassigned":
            where.append("a.city_id IS NULL")
        elif city_id is not None:
            where.append("a.city_id=?")
            parameters.append(city_id)
        if account_kind is not None:
            where.append("a.kind=?")
            parameters.append(account_kind)
        if search:
            where.append("(instr(lower(a.code),lower(?))>0 OR instr(lower(a.name),lower(?))>0 "
                         "OR instr(lower(COALESCE(a.name_urdu,'')),lower(?))>0)")
            parameters.extend([search] * 3)
        source = connection.execute(
            "SELECT a.id AS account_id,a.code,a.name,a.name_urdu,a.kind,a.is_active,a.is_postable,"
            "a.city_id,c.code AS city_code,c.name AS city_name,c.name_urdu AS city_name_urdu,"
            f"l.business_date,l.debit,l.credit,l.baseline_balance FROM {_ACCOUNT_SOURCE} l JOIN accounts a ON a.id=l.account_id "
            "LEFT JOIN cities c ON c.id=a.city_id WHERE " + " AND ".join(where)
            + " ORDER BY COALESCE(c.code,''),c.id,a.code,a.id", parameters)
        page, groups, totals = [], {}, {"account_count": 0, **{key: Decimal(0) for key in _BALANCE_KEYS}}
        current = None

        def finish(row):
            if row is None:
                return
            row["closing"] = row["opening"] + row["debit"] - row["credit"]
            if not include_zero and row["closing"] == 0:
                return
            # Refuse before retaining the next account or city group. The
            # source cursor and one current account stay bounded even when
            # many journal lines are needed to determine a closing balance.
            _row_limit(totals["account_count"] + 1, max_rows)
            row["closing_debit"] = max(row["closing"], Decimal(0))
            row["closing_credit"] = max(-row["closing"], Decimal(0))
            group = groups.setdefault(row["city_id"], {"city_id": row["city_id"], "code": row["city_code"],
                "name": row["city_name"], "name_urdu": row["city_name_urdu"], "account_count": 0,
                **{key: Decimal(0) for key in _BALANCE_KEYS}})
            if offset <= totals["account_count"] < offset + limit:
                page.append(row)
            for aggregate in (group, totals):
                aggregate["account_count"] += 1
                for key in _BALANCE_KEYS:
                    aggregate[key] += row[key]

        for source_row in source:
            if current is None or current["account_id"] != source_row["account_id"]:
                finish(current)
                if source_row["city_id"] is not None and source_row["city_name"] is None:
                    raise ValueError("A posted account references an unavailable current city.")
                current = {key: source_row[key] for key in ("account_id", "code", "name", "name_urdu", "kind",
                    "is_active", "is_postable", "city_id", "city_code", "city_name", "city_name_urdu")}
                if current["city_id"] is None:
                    current.update(city_code="", city_name="Unassigned", city_name_urdu=None)
                current.update({key: Decimal(0) for key in _BALANCE_KEYS})
            debit, credit = _journal_amounts(source_row)
            if date_from is not None and source_row["business_date"] < date_from:
                current["opening"] += debit - credit
            else:
                current["debit"] += debit
                current["credit"] += credit
        finish(current)
        return {"rows": page, "total": totals["account_count"], "offset": offset, "limit": limit,
                "baseline": baseline,
                "groups": list(groups.values()), "totals": totals, "city": selected, "label_basis": "current_master",
                "filters": {"date_from": date_from, "date_to": date_to, "city_id": city_id,
                            "account_kind": account_kind, "search": search, "include_zero": include_zero}}

    @staticmethod
    def _query_cash_accounts(connection, company_id, account_id=None):
        condition = " AND a.id=?" if account_id is not None else ""
        parameters = [company_id, company_id] + ([account_id] if account_id is not None else [])
        candidates = {row["id"]: dict(row) for row in connection.execute(
            "SELECT a.id,a.code,a.name,a.name_urdu,a.kind,a.is_active,a.is_postable,EXISTS "
            f"(SELECT 1 FROM {_ACCOUNT_SOURCE} l WHERE l.company_id=? AND l.account_id=a.id) AS has_history "
            "FROM accounts a WHERE ((a.kind='cash' AND a.is_active=1 AND a.is_postable=1) OR EXISTS "
            f"(SELECT 1 FROM {_ACCOUNT_SOURCE} l WHERE l.company_id=? AND l.account_id=a.id))"
            + condition + " ORDER BY a.code,a.id", parameters)}
        eligible = {identifier: "current_master" for identifier, row in candidates.items() if row["kind"] == "cash"}
        if any(identifier not in eligible for identifier in candidates):
            # Each relevant posted document is parsed once. Its actual journal
            # account IDs constrain the saved evidence; an unused snapshot
            # entry or another company's history cannot confer eligibility.
            evidence_parameters = [company_id] + ([account_id] if account_id is not None else [])
            for row in connection.execute(
                "SELECT m.snapshot_json,group_concat(DISTINCT l.account_id) AS posted_accounts "
                "FROM documents d JOIN document_metadata m ON m.document_id=d.id AND m.company_id=d.company_id "
                "JOIN journal_entries e ON e.document_id=d.id AND e.company_id=d.company_id "
                "JOIN journal_lines l ON l.entry_id=e.id AND l.company_id=e.company_id "
                "JOIN accounts a ON a.id=l.account_id WHERE d.company_id=? AND d.status='posted' AND a.kind<>'cash'"
                + condition + " GROUP BY d.id", evidence_parameters):
                for identifier in _saved_cash_accounts(row["snapshot_json"], company_id, row["posted_accounts"].split(",")):
                    if identifier in candidates:
                        eligible[identifier] = "saved_snapshot"
            baseline_condition = " AND account_id=?" if account_id is not None else ""
            for row in connection.execute(
                "SELECT account_id,labels_json FROM baseline_account_balances WHERE company_id=?"
                + baseline_condition, evidence_parameters):
                try:
                    saved = json.loads(row["labels_json"], object_pairs_hook=_unique_json_object)
                    company, account = saved["company"], saved["account"]
                    if (row["account_id"] in candidates and row["account_id"] not in eligible
                            and company.get("id") == company_id and company.get("cash_account_id") == row["account_id"]
                            and account.get("id") == row["account_id"] and account.get("kind") == "cash"
                            and all(isinstance(account.get(key), str) and account[key].strip() for key in ("code", "name"))):
                        eligible[row["account_id"]] = "baseline_snapshot"
                except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
                    # Missing/corrupt role evidence never invents eligibility.
                    # The exact opening remains available in the account ledger.
                    continue
        return [{key: value for key, value in row.items() if key != "has_history"}
                | {"cash_role_source": eligible[identifier]}
                for identifier, row in candidates.items() if identifier in eligible]

    def cash_accounts(self, actor, company_id):
        """Eligible current or saved historical cash roles, never a guessed default."""
        with native_transaction(self.path) as connection:
            self.security.require(actor, company_id, "ledger", "read", connection=connection)
            accounts = self._query_cash_accounts(connection, company_id)
            configured = connection.execute("SELECT cash_account_id FROM companies WHERE id=?", (company_id,)).fetchone()[0]
            return {"accounts": accounts, "default_account_id": configured if configured in {row["id"] for row in accounts} else None,
                    "label_basis": "current_master"}

    def cash_book(self, actor, company_id, account_id, *, date_from=None, date_to=None, limit=100, offset=0):
        """All posted journal activity for one explicitly selected eligible cash account."""
        _record_id(account_id, "account_id")
        _dates(date_from, date_to)
        _limits(limit, offset)
        with native_transaction(self.path) as connection, localcontext(Context(prec=1000)):
            self.security.require(actor, company_id, "ledger", "read", connection=connection)
            return self._query_cash_book(connection, company_id, account_id, date_from=date_from, date_to=date_to,
                                         limit=limit, offset=offset)

    @staticmethod
    def _query_cash_book(connection, company_id, account_id, *, date_from=None, date_to=None, limit=100, offset=0, max_rows=None):
        accounts = NativeReports._query_cash_accounts(connection, company_id, account_id)
        if not accounts:
            raise ValueError("Select a current cash account or one with a saved cash role in this company's posted history.")
        result = NativeReports._query_ledger(connection, company_id, account_id, date_from, date_to, limit, offset, max_rows=max_rows)
        result.update(account=accounts[0], limit=limit, label_basis="current_master", cash_role_source=accounts[0]["cash_role_source"])
        return result

    def stock_products(self, actor, company_id):
        """Shared active items plus this company's inactive historical items."""
        with native_transaction(self.path) as connection:
            self.security.require(actor, company_id, "stock", "read", connection=connection)
            return [dict(row) for row in connection.execute(
                "SELECT p.id,p.code,p.name,p.name_urdu,p.is_active FROM products p WHERE p.is_active=1 OR EXISTS "
                f"(SELECT 1 FROM {_STOCK_SOURCE} m WHERE m.company_id=? AND m.product_id=p.id) ORDER BY p.code,p.id",
                (company_id,))]

    def stock_stores(self, actor, company_id):
        """Assigned or historically used stores, including inactive records."""
        with native_transaction(self.path) as connection:
            self.security.require(actor, company_id, "stock", "read", connection=connection)
            return [dict(row) for row in connection.execute(
                "SELECT s.id,s.code,s.name,s.name_urdu,s.is_active FROM stores s "
                "WHERE EXISTS (SELECT 1 FROM company_stores cs WHERE cs.company_id=? AND cs.store_id=s.id) "
                f"OR EXISTS (SELECT 1 FROM {_STOCK_SOURCE} m WHERE m.company_id=? AND m.store_id=s.id) ORDER BY s.code,s.id",
                (company_id, company_id))]

    def stock_ledger(self, actor, company_id, product_id, store_id=None, pool="regular", date_from=None, date_to=None, offset=0, limit=100):
        """Posted quantity ledger; full-period totals with a paginated running balance.

        With no store filter, quantities are combined across this company's
        stores; each movement retains its own store label. Date is primary,
        followed by document posting sequence, posting time, document ID, line
        number and movement ID. Old NULL sequences sort before numbered entries
        on the same date; that deterministic fallback does not invent their
        original posting order. Saved movement values accompany the quantities;
        an unknown cost prevents a complete running value.
        """
        _dates(date_from, date_to)
        _limits(limit, offset)
        if not isinstance(pool, str) or pool not in {"regular", "breakage"}:
            raise ValueError("Select the regular or breakage stock pool.")
        with native_transaction(self.path) as connection, localcontext(Context(prec=1000)):
            self.security.require(actor, company_id, "stock", "read", connection=connection)
            return self._query_stock_ledger(connection, company_id, product_id, store_id, pool, date_from, date_to, offset, limit)

    @staticmethod
    def _query_stock_ledger(connection, company_id, product_id, store_id=None, pool="regular", date_from=None, date_to=None, offset=0, limit=100, *, max_rows=None):
        baseline = _baseline_report_info(connection, date_to)
        product = _stock_product(connection, company_id, product_id)
        store = _stock_store(connection, company_id, store_id) if store_id is not None else None
        if connection.execute(
            "SELECT 1 FROM posted_stock_movements WHERE company_id=? AND product_id=? AND pool IS NULL "
            "AND (? IS NULL OR store_id=?) AND (? IS NULL OR business_date<=?) LIMIT 1",
            (company_id, product_id, store_id, store_id, date_to, date_to)).fetchone() is not None:
            raise ValueError("Some posted movements have no verified stock pool. Reconcile them before reporting stock quantities.")
        movements = connection.execute(
            "SELECT m.id AS movement_id,m.document_id,m.document_line_id,m.product_id,m.store_id,m.pool,m.direction,m.quantity,"
            "m.reverses_movement_id,m.business_date,m.kind,m.document_number,d.posting_sequence,d.posted_at,d.reverses_document_id,"
            "original.document_number AS reverses_document_number,l.line_number,l.source_line_id,"
            "p.code AS product_code,p.name AS product_name,p.name_urdu AS product_name_urdu,"
            "s.code AS store_code,s.name AS store_name,s.name_urdu AS store_name_urdu "
            ",v.unit_cost,NULL AS baseline_value,"
            "CASE WHEN v.movement_id IS NULL AND m.valuation_basis='opening_explicit' THEN m.value ELSE v.value END AS valuation_value,"
            "v.source_json AS valuation_source_json "
            "FROM posted_stock_movements m JOIN documents d ON d.id=m.document_id "
            "LEFT JOIN stock_valuation_snapshots v ON v.movement_id=m.id "
            "JOIN document_lines l ON l.id=m.document_line_id JOIN products p ON p.id=m.product_id JOIN stores s ON s.id=m.store_id "
            "LEFT JOIN documents original ON original.id=d.reverses_document_id AND original.company_id=d.company_id "
            "WHERE m.company_id=? AND m.product_id=? AND m.pool=? AND (? IS NULL OR m.store_id=?) "
            "AND (? IS NULL OR m.business_date<=?) "
            "ORDER BY m.business_date,d.posting_sequence,d.posted_at,d.id,l.line_number,m.id",
            (company_id, product_id, pool, store_id, store_id, date_to, date_to))
        opening = running = quantity_in = quantity_out = Decimal(0)
        values, value_running = _value_totals(), Decimal(0)
        page, count = [], 0
        for movement in chain(_baseline_stock_ledger_rows(connection, company_id, product_id, store_id, pool, baseline), movements):
            row = dict(movement)
            quantity = Decimal(row["quantity"])
            running += quantity if row["direction"] == "in" else -quantity
            if row["baseline_value"] is not None:
                signed = Decimal(row["baseline_value"])
                value, value_direction = abs(signed), "out" if signed < 0 else "in"
            else:
                value = Decimal(row["valuation_value"]) if row["valuation_value"] is not None else None
                value_direction = row["direction"]
            before = bool(date_from and row["business_date"] < date_from)
            _add_value(values, value, value_direction, before)
            if value is None:
                value_running = None
            elif value_running is not None:
                value_running += value if value_direction == "in" else -value
            if before:
                opening = running
                continue
            if row["direction"] == "in":
                quantity_in += quantity
            else:
                quantity_out += quantity
            if offset <= count < offset + limit:
                row.update(quantity=quantity, balance=running,
                           unit_cost=Decimal(row["unit_cost"]) if row["unit_cost"] is not None else None,
                           value=value, value_balance=value_running,
                           value_in=value if value_direction == "in" else Decimal(0),
                           value_out=value if value_direction == "out" else Decimal(0),
                           valuation_source=json.loads(row["valuation_source_json"]) if row["valuation_source_json"] else {"method": "older_native_record"},
                           **{"in": quantity if row["direction"] == "in" else Decimal(0),
                              "out": quantity if row["direction"] == "out" else Decimal(0)})
                page.append(row)
            count += 1
            _row_limit(count, max_rows)
        _stock_snapshot_labels(connection, company_id, page)
        _finish_values(values)
        return {"product": product, "store": store, "pool": pool, "date_from": date_from, "date_to": date_to,
                "baseline": baseline,
                **values,
                "opening": opening, "in": quantity_in, "out": quantity_out, "closing": running,
                "rows": page, "total": count, "offset": offset, "limit": limit}

    def dashboard(self, actor, company_id):
        with native_transaction(self.path) as connection:
            self.security.require(actor, company_id, "dashboard", "read", connection=connection)
            counts = {row["status"]: row["count"] for row in connection.execute(
                "SELECT status,COUNT(*) AS count FROM documents WHERE company_id=? GROUP BY status", (company_id,))}
            baseline = _baseline_report_info(connection)
            if baseline is not None:
                baseline["manifest"] = json.loads(connection.execute("SELECT manifest_json FROM baseline_imports").fetchone()[0])
                for key, table in (("account_count", "baseline_account_balances"), ("stock_count", "baseline_stock_balances")):
                    baseline[key] = connection.execute(f"SELECT COUNT(*) FROM {table} WHERE company_id=?", (company_id,)).fetchone()[0]
            return {"drafts": counts.get("draft", 0), "posted": counts.get("posted", 0),
                    "baseline": baseline,
                    "company": dict(connection.execute("SELECT id,code,name,fiscal_start,fiscal_end FROM companies WHERE id=?", (company_id,)).fetchone())}

    def trial_balance(self, actor, company_id, *, date_from=None, date_to=None):
        """Signed opening/closing balances and period debit/credit, per account."""
        _dates(date_from, date_to)
        with native_transaction(self.path) as connection, localcontext(Context(prec=1000)):
            self.security.require(actor, company_id, "trial_balance", "read", connection=connection)
            return self._query_trial_balance(connection, company_id, date_from, date_to)

    def sub_trial_balance(self, actor, company_id, date_from=None, date_to=None):
        """Current account hierarchy with descendant subtotals and direct totals.

        Include accounts with posted activity through the end date and all of
        their ancestors, even when balances cancel or accounts are inactive.
        Preorder rows carry parent_id/depth; no recursive traversal or quadratic
        full-path copies are needed for deeply nested charts.
        """
        _dates(date_from, date_to)
        with native_transaction(self.path) as connection, localcontext(Context(prec=1000)):
            self.security.require(actor, company_id, "trial_balance", "read", connection=connection)
            return self._query_sub_trial_balance(connection, company_id, date_from, date_to)

    @staticmethod
    def _query_sub_trial_balance(connection, company_id, date_from=None, date_to=None, *, max_rows=None):
        direct = NativeReports._query_trial_balance(connection, company_id, date_from, date_to, max_rows=max_rows)
        if not direct["rows"]:
            return direct
        accounts = {row["id"]: dict(row) for row in connection.execute(
            "SELECT id,parent_id,code,name,name_urdu,is_postable,is_active FROM accounts")}
        included = {}
        amounts = ("opening", "debit", "credit", "closing")
        for activity in direct["rows"]:
            current, chain, seen = activity["account_id"], [], set()
            while current is not None and current not in included:
                if current in seen:
                    raise ValueError("Account hierarchy contains a cycle. Correct the parent links before reporting.")
                if current not in accounts:
                    raise ValueError("Account hierarchy references a missing parent. Correct the parent links before reporting.")
                _row_limit(len(included) + len(chain) + 1, max_rows)
                seen.add(current)
                chain.append(current)
                current = accounts[current]["parent_id"]
            # Add a chain only after verifying it reaches a root or an already
            # verified chain. A cycle can never hide inside the included set.
            for identifier in reversed(chain):
                row = accounts[identifier]
                included[identifier] = {"account_id": identifier,
                    **{key: row[key] for key in ("parent_id", "code", "name", "name_urdu", "is_postable", "is_active")},
                    **{key: Decimal(0) for key in amounts}}
            included[activity["account_id"]].update({key: activity[key] for key in amounts})

        children = {}
        for row in included.values():
            children.setdefault(row["parent_id"], []).append(row["account_id"])
        for siblings in children.values():
            siblings.sort(key=lambda identifier: (included[identifier]["code"], identifier))
        pending = [(identifier, 0) for identifier in reversed(children.get(None, []))]
        rows = []
        while pending:
            identifier, depth = pending.pop()
            row = included[identifier]
            row["depth"] = depth
            rows.append(row)
            pending.extend((child, depth + 1) for child in reversed(children.get(identifier, [])))
        # Each child is added to its parent once, after all of its own children.
        # The grand totals remain the independent direct-posting totals, not a
        # sum of these overlapping hierarchy subtotals.
        for row in reversed(rows):
            if row["parent_id"] is not None:
                parent = included[row["parent_id"]]
                for key in amounts:
                    parent[key] += row[key]
        return {"rows": rows, "totals": direct["totals"], "baseline": direct["baseline"]}

    @staticmethod
    def _query_trial_balance(connection, company_id, date_from=None, date_to=None, *, max_rows=None):
        baseline = _baseline_report_info(connection, date_to)
        rows = connection.execute(
            "SELECT l.account_id,a.code,a.name,a.name_urdu,l.business_date,l.debit,l.credit,l.baseline_balance "
            f"FROM {_ACCOUNT_SOURCE} l JOIN accounts a ON a.id=l.account_id "
            "WHERE l.company_id=? AND (? IS NULL OR l.business_date<=?) ORDER BY a.code,l.account_id",
            (company_id, date_to, date_to))
        balances = {}
        for row in rows:
            key = row["account_id"]
            if key not in balances:
                _row_limit(len(balances) + 1, max_rows)
                balances[key] = {"account_id": key, "code": row["code"], "name": row["name"], "name_urdu": row["name_urdu"],
                                 "opening": Decimal(0), "debit": Decimal(0), "credit": Decimal(0)}
            account = balances[key]
            debit, credit = _journal_amounts(row)
            if date_from and row["business_date"] < date_from:
                account["opening"] += debit - credit
            else:
                account["debit"] += debit
                account["credit"] += credit
        for account in balances.values():
            account["closing"] = account["opening"] + account["debit"] - account["credit"]
        totals = {key: sum((row[key] for row in balances.values()), Decimal(0))
                  for key in ("opening", "debit", "credit", "closing")}
        return {"rows": list(balances.values()), "totals": totals, "baseline": baseline}

    def ledger(self, actor, company_id, account_id, *, date_from=None, date_to=None, limit=100, offset=0):
        _dates(date_from, date_to)
        _limits(limit, offset)
        with native_transaction(self.path) as connection, localcontext(Context(prec=1000)):
            self.security.require(actor, company_id, "ledger", "read", connection=connection)
            return self._query_ledger(connection, company_id, account_id, date_from, date_to, limit, offset)

    @staticmethod
    def _query_ledger(connection, company_id, account_id, date_from=None, date_to=None, limit=100, offset=0, *, max_rows=None):
        baseline = _baseline_report_info(connection, date_to)
        account = connection.execute("SELECT a.id,a.code,a.name,a.name_urdu FROM accounts a WHERE a.id=? AND (a.is_postable=1 OR EXISTS "
                                     f"(SELECT 1 FROM {_ACCOUNT_SOURCE} l WHERE l.company_id=? AND l.account_id=a.id))", (account_id, company_id)).fetchone()
        if account is None:
            raise ValueError("Select an account that accepts postings.")
        rows = connection.execute(
            "SELECT l.*,e.document_id FROM posted_journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
            "JOIN documents d ON d.id=e.document_id WHERE l.company_id=? AND l.account_id=? "
            "AND (? IS NULL OR l.business_date<=?) ORDER BY l.business_date,d.posting_sequence,d.posted_at,d.id,l.line_number",
            (company_id, account_id, date_to, date_to))
        opening = running = debit = credit = Decimal(0)
        page, count = [], 0
        for source in chain(_baseline_account_ledger_rows(connection, company_id, account_id, baseline), rows):
            row = dict(source)
            row["debit"], row["credit"] = Decimal(row["debit"]), Decimal(row["credit"])
            running += row["debit"] - row["credit"]
            if date_from and row["business_date"] < date_from:
                opening = running
                continue
            debit += row["debit"]
            credit += row["credit"]
            row["balance"] = running
            if offset <= count < offset + limit:
                page.append(row)
            count += 1
            _row_limit(count, max_rows)
        return {"account": dict(account), "opening": opening, "debit": debit, "credit": credit,
                "baseline": baseline,
                "closing": running, "rows": page, "total": count, "offset": offset}

    def stock(self, actor, company_id, *, date_from=None, date_to=None, store_id=None, pool="regular", search=""):
        _dates(date_from, date_to)
        if pool not in {"regular", "breakage"} or not isinstance(search, str) or len(search) > 200:
            raise ValueError("Invalid stock filter.")
        with native_transaction(self.path) as connection, localcontext(Context(prec=1000)):
            self.security.require(actor, company_id, "stock", "read", connection=connection)
            return self._query_stock(connection, company_id, date_from, date_to, store_id, pool, search)

    def stock_valuation(self, actor, company_id, *, date_from=None, date_to=None, store_id=None, pool="regular", search=""):
        """Preserved historical movement values plus fixed new movement costs."""
        _dates(date_from, date_to)
        if pool not in {"regular", "breakage"} or not isinstance(search, str) or len(search) > 200:
            raise ValueError("Invalid stock filter.")
        with native_transaction(self.path) as connection, localcontext(Context(prec=1000)):
            self.security.require(actor, company_id, "stock", "read", connection=connection)
            return self._query_stock_valuation(connection, company_id, date_from, date_to, store_id, pool, search)

    def profit_loss(self, actor, company_id, *, date_from=None, date_to=None):
        """Periodic trading profit, explicitly incomplete until mappings reconcile.

        Income/expense amounts use current explicit account classifications.
        The inventory adjustment includes both pools and excludes net external
        stock openings. Asset purchase mappings or separate inventory journals
        prevent claiming a periodic result that would count inventory twice.
        """
        _dates(date_from, date_to)
        with native_transaction(self.path) as connection, localcontext(Context(prec=1000)):
            for module in ("reports", "trial_balance", "stock"):
                self.security.require(actor, company_id, module, "read", connection=connection)
            return self._query_profit_loss(connection, company_id, date_from, date_to)

    @staticmethod
    def _query_profit_loss(connection, company_id, date_from=None, date_to=None, *, max_rows=None):
        baseline = _baseline_report_info(connection, date_to)
        if date_from is None:
            date_from = ((date.fromisoformat(baseline["cutoff_date"]) + timedelta(days=1)).isoformat() if baseline else
                         connection.execute("SELECT fiscal_start FROM companies WHERE id=?", (company_id,)).fetchone()[0])
        if date_from is None:
            raise ValueError("Set a start date for the periodic trading profit report.")
        _dates(date_from, date_to)
        if baseline is not None and date_from <= baseline["cutoff_date"]:
            raise ValueError("Periodic trading profit must begin after " + baseline["cutoff_date"]
                             + ". Existing balances are a closing snapshot, not historical period turnover.")
        trial = NativeReports._query_trial_balance(connection, company_id, date_from, date_to, max_rows=max_rows)
        classifications = {row["id"]: row["classification"] for row in connection.execute("SELECT id,classification FROM accounts")}
        rows, income, expense, unclassified = [], Decimal(0), Decimal(0), 0
        for original in trial["rows"]:
            category = classifications[original["account_id"]]
            if category not in {"income", "expense", None} or not any(original[key] for key in ("opening", "debit", "credit", "closing")):
                continue
            contribution = (original["credit"] - original["debit"] if category == "income" else
                            original["debit"] - original["credit"] if category == "expense" else None)
            if category == "income":
                income += contribution
            elif category == "expense":
                expense += contribution
            else:
                unclassified += 1
            rows.append({**original, "classification": category, "contribution": contribution})
        stock = [NativeReports._query_stock_valuation(connection, company_id, date_from, date_to, pool=pool, max_rows=max_rows)
                 for pool in ("regular", "breakage")]
        stock_totals = {}
        for key in ("value_opening", "value_closing"):
            stock_totals[key] = (sum((item["totals"][key] for item in stock), Decimal(0))
                                 if all(item["totals"][key] is not None for item in stock) else None)
        missing = sum(item["totals"]["missing_valuation_count"] for item in stock)
        product_ids = {row["product_id"] for item in stock for row in item["rows"]}
        mapping_issues, inventory_accounts = [], set()
        for product in connection.execute("SELECT id,code,name,name_urdu,purchase_account_id,sale_account_id,inventory_account_id FROM products ORDER BY code,id"):
            if product["id"] not in product_ids:
                continue
            if product["inventory_account_id"]:
                inventory_accounts.add(product["inventory_account_id"])
            for key, required in (("purchase_account_id", "expense"), ("sale_account_id", "income")):
                if classifications.get(product[key]) != required:
                    mapping_issues.append({"product_id": product["id"], "code": product["code"], "name": product["name"],
                                           "account_id": product[key], "issue": key + " must be classified as " + required})
                    _row_limit(len(rows) + len(mapping_issues), max_rows)
        # Verify the accounts actually used by period postings as well as the
        # current mappings, since a product may have been remapped afterward.
        actual = connection.execute(
            "SELECT DISTINCT d.id,d.document_number,d.kind,o.kind AS original_kind,l.account_id,a.code,a.classification "
            "FROM posted_journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN documents d ON d.id=e.document_id "
            "LEFT JOIN documents o ON o.id=d.reverses_document_id JOIN accounts a ON a.id=l.account_id "
            "WHERE d.company_id=? AND d.business_date>=? AND (? IS NULL OR d.business_date<=?) "
            "AND (d.account_id IS NULL OR l.account_id<>d.account_id)", (company_id, date_from, date_to, date_to))
        opening_injections = Decimal(0)
        income_kinds = {"sale", "sale_return", "cash_sale", "cash_sale_return", "breakage_receive"}
        expense_kinds = {"purchase", "purchase_return", "cash_purchase", "cash_purchase_return", "breakage_dispatch"}
        for row in actual:
            original_kind = row["original_kind"] if row["kind"] == "reversal" else row["kind"]
            expected = "income" if original_kind in income_kinds else "expense" if original_kind in expense_kinds else None
            issue = None
            if expected is not None and row["classification"] != expected:
                issue = "Posted " + original_kind.replace("_", " ") + " account must be classified as " + expected
            elif row["account_id"] in inventory_accounts and original_kind != "stock_opening":
                issue = "Separate inventory-account postings require review before applying the periodic inventory adjustment"
            if issue:
                mapping_issues.append({"document_id": row["id"], "document_number": row["document_number"],
                                       "account_id": row["account_id"], "code": row["code"], "issue": issue})
                _row_limit(len(rows) + len(mapping_issues), max_rows)
        for row in connection.execute(
            "SELECT m.direction,m.value FROM posted_stock_movements m JOIN documents d ON d.id=m.document_id "
            "LEFT JOIN documents original ON original.id=d.reverses_document_id "
            "WHERE d.company_id=? AND d.business_date>=? AND (? IS NULL OR d.business_date<=?) "
            "AND (d.kind='stock_opening' OR (d.kind='reversal' AND original.kind='stock_opening'))",
            (company_id, date_from, date_to, date_to)):
            amount = Decimal(row["value"])
            opening_injections += amount if row["direction"] == "in" else -amount
        inventory_adjustment = (stock_totals["value_closing"] - stock_totals["value_opening"] - opening_injections
                                if all(value is not None for value in stock_totals.values()) else None)
        ready = unclassified == 0 and not mapping_issues and inventory_adjustment is not None
        totals = {"income": income, "expense": expense, "classified_result": income - expense,
                  "inventory_opening": stock_totals["value_opening"], "inventory_closing": stock_totals["value_closing"],
                  "opening_injections": opening_injections, "inventory_adjustment": inventory_adjustment,
                  "profit": income - expense + inventory_adjustment if ready else None,
                  "unclassified_count": unclassified, "mapping_issue_count": len(mapping_issues),
                  "missing_valuation_count": missing}
        return {"rows": rows, "totals": totals, "mapping_issues": mapping_issues, "baseline": baseline,
                "date_from": date_from, "date_to": date_to, "method": "periodic_trading_profit",
                "status": "ready" if ready else "review_required"}

    @staticmethod
    def _query_stock_valuation(connection, company_id, date_from=None, date_to=None, store_id=None, pool="regular", search="", *, max_rows=None):
        rows = NativeReports._query_stock(connection, company_id, date_from, date_to, store_id, pool, search, max_rows=max_rows)
        totals = {"missing_valuation_count": sum(row["missing_valuation_count"] for row in rows)}
        for key in ("value_opening", "value_in", "value_out", "value_closing"):
            totals[key] = sum((row[key] for row in rows), Decimal(0)) if all(row[key] is not None for row in rows) else None
        return {"rows": rows, "totals": totals, "baseline": _baseline_report_info(connection, date_to),
                "store": _stock_store(connection, company_id, store_id) if store_id is not None else None,
                "pool": pool, "date_from": date_from, "date_to": date_to}

    @staticmethod
    def _query_stock(connection, company_id, date_from=None, date_to=None, store_id=None, pool="regular", search="", *, max_rows=None):
        _baseline_report_info(connection, date_to)
        if store_id is not None and connection.execute(
            "SELECT 1 FROM company_stores WHERE company_id=? AND store_id=?", (company_id, store_id)).fetchone() is None:
            raise ValueError("Select a store belonging to this company.")
        if connection.execute(
            "SELECT 1 FROM posted_stock_movements WHERE company_id=? AND pool IS NULL "
            "AND (? IS NULL OR store_id=?) AND (? IS NULL OR business_date<=?) LIMIT 1",
            (company_id, store_id, store_id, date_to, date_to)).fetchone() is not None:
            raise ValueError("Some posted movements have no verified stock pool. Reconcile them before reporting stock quantities.")
        rows = connection.execute(
            "SELECT m.product_id,p.code,p.name,p.name_urdu,m.store_id,s.code AS store_code,s.name AS store,s.name_urdu AS store_name_urdu,m.direction,m.quantity,m.business_date,m.baseline_quantity,m.baseline_value,m.valuation_value "
            f"FROM {_STOCK_SOURCE} m JOIN products p ON p.id=m.product_id JOIN stores s ON s.id=m.store_id "
            "WHERE m.company_id=? AND m.pool=? AND (? IS NULL OR m.store_id=?) "
            "AND (? IS NULL OR m.business_date<=?) ORDER BY p.code,s.code,m.product_id,m.store_id",
            (company_id, pool, store_id, store_id, date_to, date_to))
        balances = {}
        term = search.strip().casefold()
        for row in rows:
            if term and term not in (row["code"] + " " + row["name"]).casefold():
                continue
            key = row["product_id"], row["store_id"]
            if key not in balances:
                _row_limit(len(balances) + 1, max_rows)
                balances[key] = {"product_id": key[0], "store_id": key[1], "code": row["code"], "name": row["name"], "name_urdu": row["name_urdu"],
                                 "store": row["store"], "store_code": row["store_code"], "store_name_urdu": row["store_name_urdu"], "opening": Decimal(0), "in": Decimal(0), "out": Decimal(0), **_value_totals()}
            item = balances[key]
            direction, quantity = _stock_amount(row)
            before = bool(date_from and row["business_date"] < date_from)
            if row["baseline_value"] is not None:
                signed = Decimal(row["baseline_value"])
                value, value_direction = abs(signed), "out" if signed < 0 else "in"
            else:
                value = Decimal(row["valuation_value"]) if row["valuation_value"] is not None else None
                value_direction = direction
            _add_value(item, value, value_direction, before)
            if before:
                item["opening"] += quantity if direction == "in" else -quantity
            else:
                item[direction] += quantity
        for item in balances.values():
            item["closing"] = item["opening"] + item["in"] - item["out"]
            _finish_values(item)
        return list(balances.values())
