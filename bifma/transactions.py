"""Authenticated operational drafts, atomic postings and immutable reversals.

Only NEW transactions use the corrected posting rules. Imported historical
balances fund new entries without rewriting history. See docs/TRANSACTION_SERVICES.md for the
input contract, supported workflows and deliberately rejected paths.
"""

from __future__ import annotations

from datetime import date
from decimal import Context, Decimal, localcontext
import json
from pathlib import Path
import sqlite3
import time
from uuid import UUID, uuid4

from bifma.calculations import (DiscountKind, InvoiceLine, RoundingPolicy,
                                as_decimal, canonical_decimal, convert_quantity,
                                oracle_round)
from bifma.native_schema import native_transaction
from bifma.posting_rules import JournalEntry, TradePostingItem, build_trade_posting, validate_balanced
from bifma.security import AuthorizationError, SecurityService


MODULES = {
    "sale": "sales", "purchase": "purchases", "sale_return": "sales_returns",
    "cash_sale": "counter_sales", "cash_purchase": "purchases",
    "cash_sale_return": "sales_returns", "cash_purchase_return": "purchase_returns",
    "purchase_return": "purchase_returns", "stock_opening": "stock",
    "breakage_receive": "breakage_receive", "breakage_dispatch": "breakage_dispatch",
    "journal": "journals", "opening_balance": "journals",
    "cash_receipt": "receipts", "bank_receipt": "receipts",
    "cash_payment": "payments", "bank_payment": "payments",
}
BREAKAGE_KINDS = frozenset({"breakage_receive", "breakage_dispatch"})
CASH_ORIGINAL_KINDS = frozenset({"cash_sale", "cash_purchase"})
CASH_KINDS = CASH_ORIGINAL_KINDS | {"cash_sale_return", "cash_purchase_return"}
TRADE_KINDS = frozenset({"sale", "purchase", "sale_return", "purchase_return"}) | BREAKAGE_KINDS | CASH_KINDS
# Recovered BR uses the ordinary sale account (EXP_CODE); BD uses PUR_CODE.
# Financial direction is independent of original-invoice matching/capacity and
# of the separate breakage stock pool. Only RETURN_KINDS consume source lines.
_BREAKAGE_FINANCIAL_KINDS = {"breakage_receive": "sale_return", "breakage_dispatch": "purchase_return"}
_FINANCIAL_KINDS = {**_BREAKAGE_FINANCIAL_KINDS, **{kind: kind.removeprefix("cash_") for kind in CASH_KINDS}}
RETURN_KINDS = {"sale_return": "sale", "purchase_return": "purchase",
                "cash_sale_return": "cash_sale", "cash_purchase_return": "cash_purchase"}
VOUCHERS = frozenset({"cash_receipt", "cash_payment", "bank_receipt", "bank_payment"})
_ZERO = Decimal(0)
_CONTEXT = Context(prec=1000)


class TransactionError(ValueError):
    pass


class RevisionConflict(TransactionError):
    pass


class StockUnavailable(TransactionError):
    pass


def _id(value, label="identifier"):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise TransactionError(f"{label} must be a canonical UUID") from None
    return value


def _day(value):
    try:
        if not isinstance(value, str) or len(value) != 10 or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        raise TransactionError("business_date must be an ISO date (YYYY-MM-DD)") from None
    return value


def _text(value, label, maximum=2000, *, blank=True):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value or (not blank and not value.strip()):
        raise TransactionError(f"{label} must be {'nonblank ' if not blank else ''}text of at most {maximum} characters")
    return value


def _mapping(value, allowed, required=()):
    if not isinstance(value, dict):
        raise TransactionError("document and line inputs must be dictionaries")
    unexpected, missing = set(value) - set(allowed), set(required) - set(value)
    if unexpected:
        raise TransactionError("Unsupported fields: " + ", ".join(sorted(map(str, unexpected))))
    if missing:
        raise TransactionError("Required fields: " + ", ".join(sorted(missing)))
    return value


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _money(value, places, label="amount"):
    result = as_decimal(value, name=label)
    if result < 0 or oracle_round(result, places) != result:
        raise TransactionError(f"{label} must be nonnegative and exactly representable at posting_places")
    return result


def _revision(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RevisionConflict("An exact nonnegative expected_revision is required")
    return value


def _rows(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 10000:
        raise TransactionError("lines must contain between 1 and 10,000 entries")
    return items


class TransactionService:
    def __init__(self, path, security=None, clock=time.time):
        self.path = Path(path).resolve()
        self.clock = clock
        self.security = security or SecurityService(self.path, clock=clock)

    def _audit(self, connection, user, company, action, identifier, details=None):
        connection.execute(
            "INSERT INTO audit_events(id,actor_id,company_id,action,entity_type,entity_id,occurred_at,details_json) VALUES(?,?,?,?,?,?,?,?)",
            (str(uuid4()), user.id, company, action, "document", identifier, int(self.clock()), _json(details or {})),
        )

    @staticmethod
    def _document(connection, company, identifier):
        row = connection.execute("SELECT * FROM documents WHERE company_id=? AND id=?", (company, _id(identifier, "document_id"))).fetchone()
        if row is None:
            raise TransactionError("Document is unavailable in the selected company")
        return row

    @staticmethod
    def _module(connection, document):
        kind = document["kind"]
        if kind == "reversal":
            original = connection.execute("SELECT kind FROM documents WHERE id=?", (document["reverses_document_id"],)).fetchone()
            kind = original[0] if original else None
        if kind not in MODULES:
            raise TransactionError("This document kind is not supported by the operational service")
        return MODULES[kind]

    @staticmethod
    def _company(connection, company, business_date):
        from bifma.baseline import require_after_cutoff
        try:
            require_after_cutoff(connection, business_date)
        except ValueError as error:
            raise TransactionError(str(error)) from None
        row = connection.execute("SELECT * FROM companies WHERE id=? AND is_active=1", (company,)).fetchone()
        if row is None:
            raise TransactionError("Company is inactive or unavailable")
        if row["fiscal_start"] is None or row["fiscal_end"] is None:
            raise TransactionError("Configure the company's fiscal start and end before entering transactions")
        if not row["fiscal_start"] <= business_date <= row["fiscal_end"]:
            raise TransactionError("Document date is outside the company's fiscal interval")
        return {key: row[key] for key in ("id", "code", "name", "name_urdu", "fiscal_start", "fiscal_end", "address", "address_urdu", "phone", "fax", "email", "website", "sales_tax_number", "national_tax_number") if key in row.keys()}

    @staticmethod
    def _account(connection, identifier):
        row = connection.execute("SELECT * FROM accounts WHERE id=? AND is_active=1 AND is_postable=1", (_id(identifier, "account_id"),)).fetchone()
        if row is None:
            raise TransactionError("An account is missing, inactive or nonpostable")
        return {key: row[key] for key in ("id", "code", "name", "name_urdu", "kind", "classification", "address", "phone", "mobile", "email", "website", "sales_tax_number", "national_tax_number") if key in row.keys()}

    @staticmethod
    def _product(connection, identifier):
        row = connection.execute("SELECT * FROM products WHERE id=? AND is_active=1", (_id(identifier, "product_id"),)).fetchone()
        if row is None:
            raise TransactionError("A product is missing or inactive")
        result = {key: row[key] for key in ("id", "code", "name", "name_urdu", "unit_factor", "small_unit_id", "large_unit_id", "sale_account_id", "purchase_account_id", "inventory_account_id")}
        for key in ("small_unit_id", "large_unit_id"):
            unit = None
            if row[key] is not None:
                unit = connection.execute("SELECT id,code,name,name_urdu FROM units WHERE id=? AND is_active=1", (row[key],)).fetchone()
                if unit is None:
                    raise TransactionError("A product unit is inactive or missing")
            result[key.replace("_id", "")] = dict(unit) if unit else None
        return result

    @staticmethod
    def _store(connection, company, identifier):
        row = connection.execute(
            "SELECT s.id,s.code,s.name,s.name_urdu FROM stores s JOIN company_stores cs ON cs.store_id=s.id "
            "WHERE cs.company_id=? AND s.id=? AND s.is_active=1 AND cs.is_active=1", (company, _id(identifier, "store_id")),
        ).fetchone()
        if row is None:
            raise TransactionError("Store is inactive or not assigned to this company")
        return dict(row)

    @staticmethod
    def _active_reversal(connection, identifier):
        return connection.execute("SELECT id FROM documents WHERE reverses_document_id=? AND status='posted'", (identifier,)).fetchone()

    def _source(self, connection, company, kind, source_id, party, business_date):
        row = connection.execute(
            "SELECT l.*,d.kind AS document_kind,d.document_number,d.account_id,d.status,d.business_date,d.total_amount,d.rounding_policy,d.posting_places "
            "FROM document_lines l JOIN documents d ON d.id=l.document_id WHERE l.company_id=? AND l.id=?",
            (company, _id(source_id, "source_line_id")),
        ).fetchone()
        if row is None:
            original = connection.execute(
                "SELECT l.*,d.kind AS document_kind,d.document_number,d.account_id,d.business_date,d.total_amount,"
                "d.rounding_policy,d.posting_places,d.historical_returned_amount,d.snapshot_json AS document_snapshot,"
                "d.return_eligible AS document_eligible,d.exclusion_reason AS document_exclusion "
                "FROM baseline_trade_lines l JOIN baseline_trade_documents d ON d.id=l.document_id "
                "WHERE l.company_id=? AND l.id=?",
                (company, source_id),
            ).fetchone()
            if original is None or original["document_kind"] != RETURN_KINDS[kind] or original["account_id"] != party:
                raise TransactionError("Return lines must reference a corresponding invoice for the same party and company")
            if not original["return_eligible"] or not original["document_eligible"]:
                raise TransactionError("This historical invoice line needs review before a return: "
                                       + (original["exclusion_reason"] or original["document_exclusion"] or "original data is incomplete"))
            if business_date < original["business_date"]:
                raise TransactionError("A return cannot predate its original invoice")
            snapshot = json.loads(original["document_snapshot"])
            return {**dict(original), "source": "history"}, json.loads(original["snapshot_json"]), snapshot
        if row is None or row["status"] != "posted" or row["document_kind"] != RETURN_KINDS[kind] or row["account_id"] != party:
            raise TransactionError("Return lines must reference a posted corresponding invoice for the same party and company")
        if business_date < row["business_date"] or self._active_reversal(connection, row["document_id"]):
            raise TransactionError("Return cannot predate or reference a reversed invoice")
        metadata = connection.execute("SELECT input_json,snapshot_json FROM document_metadata WHERE document_id=?", (row["document_id"],)).fetchone()
        if metadata is None:
            raise TransactionError("Original invoice has no verified operational snapshots")
        snapshot = json.loads(metadata[1])
        source_snapshot = next((line for line in snapshot["lines"] if line["line_id"] == source_id), None)
        if source_snapshot is None:
            raise TransactionError("Original line snapshot is unavailable")
        return {**dict(row), "source": "native"}, source_snapshot, snapshot

    def _prepare(self, connection, company, kind, incoming, *, line_ids=None):
        financial_kind = _FINANCIAL_KINDS.get(kind, kind)
        common = {"document_number", "business_date", "rounding_policy", "posting_places", "notes", "lines"}
        extra = {"account_id", "discount", "breakage", "freight"} if kind in TRADE_KINDS else {"contra_account_id"} if kind == "stock_opening" else {"account_id"} if kind in VOUCHERS else set()
        if kind in CASH_ORIGINAL_KINDS:
            extra |= {"counterparty_name", "counterparty_mobile"}
        if kind == "sale":
            extra.add("bill_number")
        _mapping(incoming, common | extra, common - {"notes"})
        places = incoming["posting_places"]
        if type(places) is not int or places not in (0, 2):
            raise TransactionError("posting_places must explicitly be 0 or 2")
        try:
            rounding = RoundingPolicy(incoming["rounding_policy"])
        except (ValueError, TypeError):
            raise TransactionError("rounding_policy must explicitly be cents or legacy_whole") from None
        values = {
            "document_number": _text(incoming["document_number"], "document_number", 80, blank=False).strip(),
            "business_date": _day(incoming["business_date"]), "rounding_policy": rounding.value,
            "posting_places": places, "notes": _text(incoming.get("notes", ""), "notes"), "lines": [],
        }
        source_lines = _rows(incoming["lines"])
        company_snapshot = self._company(connection, company, values["business_date"])
        snapshot = {"company": company_snapshot, "accounts": {}, "lines": []}
        if kind == "sale":
            bill = _text(incoming.get("bill_number", ""), "bill_number", 80).strip()
            if bill:
                values["bill_number"] = bill
                snapshot["bill_number"] = bill
        accounts, products, stores = {}, {}, {}

        def account(identifier):
            if identifier not in accounts:
                accounts[identifier] = self._account(connection, identifier)
                snapshot["accounts"][identifier] = accounts[identifier]
            return accounts[identifier]

        party = None
        if kind in TRADE_KINDS or kind in VOUCHERS:
            party = _id(incoming.get("account_id"), "account_id")
            values["account_id"] = party
            account(party)
            if kind in CASH_KINDS and accounts[party]["kind"] != "cash":
                raise TransactionError("Cash trades require an active, postable cash account")
            if kind in CASH_ORIGINAL_KINDS:
                configured = connection.execute("SELECT cash_account_id FROM companies WHERE id=?", (company,)).fetchone()[0]
                if configured != party:
                    raise TransactionError("Cash account configuration changed or does not match; select the company's configured cash account, resave and review")
                for field, maximum in (("counterparty_name", 150), ("counterparty_mobile", 80)):
                    values[field] = _text(incoming.get(field, ""), field, maximum).strip()
                snapshot["cash_settlement"] = {"mode": "cash", "cash_account_id": party,
                                               **{field: values[field] for field in ("counterparty_name", "counterparty_mobile")}}
            if kind in VOUCHERS and accounts[party]["kind"] != ("cash" if kind.startswith("cash_") else "bank"):
                raise TransactionError("The voucher's account_id must identify an active cash/bank account of the selected kind")
        elif kind == "stock_opening":
            party = _id(incoming.get("contra_account_id"), "contra_account_id")
            values["contra_account_id"] = party
            account(party)

        lines, journal, movements, trade_items = [], [], [], []
        requested, originals = {}, {}
        with localcontext(_CONTEXT):
            for index, source in enumerate(source_lines, 1):
                if kind in TRADE_KINDS or kind == "stock_opening":
                    allowed = {"product_id", "store_id", "quantity", "rate", "discount_percent", "source_line_id"} if kind in TRADE_KINDS else {"product_id", "store_id", "quantity", "value", "pool"}
                    if kind == "sale":
                        allowed |= {"unit_id", "entered_quantity"}
                    if kind in BREAKAGE_KINDS:
                        allowed.remove("source_line_id")
                    required = {"product_id", "store_id", "quantity", "rate"} if kind in TRADE_KINDS else {"product_id", "store_id", "quantity", "value", "pool"}
                    _mapping(source, allowed, required)
                    product_id, store_id = _id(source["product_id"], "product_id"), _id(source["store_id"], "store_id")
                    if product_id not in products:
                        products[product_id] = self._product(connection, product_id)
                    if store_id not in stores:
                        stores[store_id] = self._store(connection, company, store_id)
                    if kind == "breakage_receive" and len(stores) > 1:
                        raise TransactionError("A breakage receive document must use one store for all lines")
                    product, store = products[product_id], stores[store_id]
                    line_id = line_ids[index - 1] if line_ids else str(uuid4())
                    line = InvoiceLine(source["quantity"], source.get("rate", "0"), source.get("discount_percent", "0"), discount_kind=DiscountKind.PERCENT)
                    normalized = {"product_id": product_id, "store_id": store_id, "quantity": canonical_decimal(line.quantity)}
                    if kind == "sale" and ("unit_id" in source or "entered_quantity" in source):
                        if "unit_id" not in source or "entered_quantity" not in source:
                            raise TransactionError("Sale entry unit and entered quantity must be saved together")
                        try:
                            entered_quantity = as_decimal(source["entered_quantity"], name="entered quantity")
                            unit_id = _id(source["unit_id"], "unit_id")
                            small_unit_id = product["small_unit"]["id"] if product["small_unit"] else None
                            large_unit_id = product["large_unit"]["id"] if product["large_unit"] else None
                            if unit_id == small_unit_id:
                                factor = "1"
                            elif unit_id == large_unit_id and product["unit_factor"] is not None:
                                factor = product["unit_factor"]
                            else:
                                raise TransactionError("Sale entry unit is not one of the selected item's saved units")
                            converted = convert_quantity(large=entered_quantity, small="0", factor=factor)
                        except TransactionError:
                            raise
                        except ValueError as error:
                            raise TransactionError(str(error)) from None
                        if entered_quantity <= 0 or converted != line.quantity:
                            raise TransactionError("Sale entered quantity and unit do not equal the saved base quantity")
                        normalized.update(unit_id=unit_id, entered_quantity=canonical_decimal(entered_quantity))
                    source_id = source.get("source_line_id")
                    historical_source_id = None
                    mapping = "sale_account_id" if financial_kind in ("sale", "sale_return") else "purchase_account_id"
                    if kind == "stock_opening":
                        mapping = "inventory_account_id"
                    posting_account = product[mapping]
                    if kind in RETURN_KINDS:
                        original, origin_snapshot, original_snapshot = self._source(connection, company, kind, source_id, party, values["business_date"])
                        snapshot["return_of"] = {"id": original["document_id"], "kind": original["document_kind"],
                                                 "document_number": original["document_number"], "business_date": original["business_date"]}
                        if original["source"] == "history":
                            historical_source_id = source_id
                            snapshot["return_of"]["source"] = "history"
                        if kind in CASH_KINDS:
                            binding = original_snapshot.get("cash_settlement")
                            if (not isinstance(binding, dict) or set(binding) != {"mode", "cash_account_id", "counterparty_name", "counterparty_mobile"}
                                    or binding["mode"] != "cash" or binding["cash_account_id"] != party):
                                raise TransactionError("Original cash invoice has no verified cash binding")
                            for field, maximum in (("counterparty_name", 150), ("counterparty_mobile", 80)):
                                _text(binding[field], "original " + field, maximum)
                            snapshot["cash_settlement"] = dict(binding)
                        if original["product_id"] != product_id or original["store_id"] != store_id or Decimal(original["rate"]) != line.rate or Decimal(original["discount_percent"]) != line.discount:
                            raise TransactionError("Return product, store, rate and line discount must match the original line")
                        if any(origin_snapshot["product"][key] != product[key] for key in ("small_unit_id", "large_unit_id", "unit_factor")):
                            raise TransactionError("Original product units changed; automatic return requires verified unit continuity")
                        if original["rounding_policy"] != rounding.value or original["posting_places"] != places:
                            raise TransactionError("Return rounding policies must match its original invoice")
                        requested[source_id] = requested.get(source_id, _ZERO) + line.quantity
                        originals[original["document_id"]] = original
                        reference_column = "historical_source_line_id" if historical_source_id else "source_line_id"
                        used = connection.execute(
                            "SELECT l.quantity FROM document_lines l JOIN documents d ON d.id=l.document_id "
                            f"WHERE l.company_id=? AND l.{reference_column}=? AND d.kind=? AND d.status='posted' "
                            "AND NOT EXISTS(SELECT 1 FROM documents r WHERE r.reverses_document_id=d.id AND r.status='posted')",
                            (company, source_id, kind),
                        )
                        consumed = sum((Decimal(row[0]) for row in used), Decimal(original.get("historical_returned_quantity", "0")))
                        if consumed + requested[source_id] > Decimal(original["quantity"]):
                            raise TransactionError("Cumulative returned quantity exceeds the original posted line")
                        posting_account = origin_snapshot["posting_account"]["id"]
                    elif source_id is not None:
                        raise TransactionError("source_line_id is supported only for corresponding invoice returns")
                    if posting_account is None:
                        raise TransactionError(f"Product requires an active {mapping} mapping before this transaction")
                    posting_snapshot = account(posting_account)
                    snapshot["lines"].append({"line_id": line_id, "product": product, "store": store, "posting_account": posting_snapshot})
                    if kind in TRADE_KINDS:
                        normalized.update(rate=canonical_decimal(line.rate), discount_percent=canonical_decimal(line.discount))
                        if source_id is not None:
                            normalized["source_line_id"] = source_id
                        net = line.calculate().net
                        trade_items.append(TradePostingItem(line, posting_account))
                        pool, value, basis = "breakage" if kind in BREAKAGE_KINDS else "regular", _ZERO, "unvalued"
                        direction = "in" if financial_kind in ("purchase", "sale_return") else "out"
                    else:
                        pool = source["pool"]
                        if pool not in ("regular", "breakage"):
                            raise TransactionError("pool must explicitly be regular or breakage")
                        value = _money(source["value"], places, "opening value")
                        normalized.update(value=canonical_decimal(value), pool=pool)
                        net, basis, direction = value, "opening_explicit", "in"
                        if posting_account == party:
                            raise TransactionError("Opening inventory and contra accounts must differ")
                        if value:
                            journal.append({"account_id": posting_account, "debit": canonical_decimal(value), "credit": "0", "narration": "Opening inventory"})
                    values["lines"].append(normalized)
                    lines.append({"id": line_id, "line_number": index, "product_id": product_id, "store_id": store_id,
                                  "quantity": canonical_decimal(line.quantity), "rate": canonical_decimal(line.rate),
                                  "discount_percent": canonical_decimal(line.discount), "net_amount": canonical_decimal(net),
                                  "source_line_id": None if historical_source_id else source_id,
                                  "historical_source_line_id": historical_source_id})
                    movements.append({"document_line_id": line_id, "product_id": product_id, "store_id": store_id, "direction": direction,
                                      "quantity": canonical_decimal(line.quantity), "unit_cost": "0", "value": canonical_decimal(value),
                                      "pool": pool, "valuation_basis": basis, "reverses_movement_id": None})
                else:
                    required = {"account_id", "amount"} if kind in VOUCHERS else {"account_id", "debit", "credit"}
                    _mapping(source, required | {"narration"}, required)
                    identifier = _id(source["account_id"], "account_id")
                    account(identifier)
                    narration = _text(source.get("narration", ""), "narration", 600)
                    if kind in VOUCHERS:
                        if identifier == party:
                            raise TransactionError("Voucher counterparty must differ from its cash/bank account")
                        amount = _money(source["amount"], places)
                        if amount <= 0:
                            raise TransactionError("Voucher amounts must be positive")
                        debit = amount if kind.endswith("payment") else _ZERO
                        credit = amount if kind.endswith("receipt") else _ZERO
                        normalized = {"account_id": identifier, "amount": canonical_decimal(amount), "narration": narration}
                    else:
                        debit, credit = _money(source["debit"], places), _money(source["credit"], places)
                        JournalEntry(identifier, debit, credit)
                        normalized = {"account_id": identifier, "debit": canonical_decimal(debit), "credit": canonical_decimal(credit), "narration": narration}
                    journal.append({"account_id": identifier, "debit": canonical_decimal(debit), "credit": canonical_decimal(credit), "narration": narration})
                    values["lines"].append(normalized)

            raw_totals = {}
            if kind in TRADE_KINDS:
                for name in ("discount", "breakage", "freight"):
                    values[name] = canonical_decimal(incoming.get(name, "0"))
                if kind in BREAKAGE_KINDS:
                    unsupported = ("discount", "freight") if kind == "breakage_receive" else ("discount", "breakage", "freight")
                    if any(Decimal(values[field]) != 0 for field in unsupported):
                        label = "Breakage receive" if kind == "breakage_receive" else "Breakage dispatch"
                        permitted = " Only breakage receive supports a header breakage deduction." if kind == "breakage_dispatch" else ""
                        raise TransactionError(f"{label} requires zero header {', '.join(unsupported)}.{permitted}")
                if kind in RETURN_KINDS and len(originals) != 1:
                    raise TransactionError("A linked return must reference one original invoice")
                posting = build_trade_posting(financial_kind, trade_items, party_account=party, subtotal_rounding=rounding, posting_places=places,
                                             **{name: values[name] for name in ("discount", "breakage", "freight")})
                total = posting.total
                journal = [{"account_id": entry.account_code, "debit": canonical_decimal(entry.debit), "credit": canonical_decimal(entry.credit), "narration": values["notes"][:600]} for entry in posting.entries]
                raw_totals = {"raw_subtotal": canonical_decimal(posting.invoice_totals.raw_subtotal), "subtotal": canonical_decimal(posting.invoice_totals.subtotal),
                              "header_total": canonical_decimal(posting.invoice_totals.total), "rounding_adjustment": canonical_decimal(posting.invoice_totals.rounding_adjustment),
                              "final_rounding_adjustment": canonical_decimal(posting.final_rounding_adjustment),
                              "allocations": [{"account_id": allocation.account_code, "raw_subtotal": canonical_decimal(allocation.raw_subtotal), "amount": canonical_decimal(allocation.amount), "adjustment": canonical_decimal(allocation.adjustment)} for allocation in posting.allocations]}
                if kind in RETURN_KINDS:
                    original = next(iter(originals.values()))
                    source_table = "baseline_trade_lines" if original["source"] == "history" else "document_lines"
                    reference_column = "historical_source_line_id" if original["source"] == "history" else "source_line_id"
                    prior = connection.execute(
                        "SELECT DISTINCT d.id,d.total_amount FROM documents d JOIN document_lines l ON l.document_id=d.id "
                        f"JOIN {source_table} original_line ON original_line.id=l.{reference_column} "
                        "WHERE original_line.document_id=? AND d.kind=? AND d.status='posted' "
                        "AND NOT EXISTS(SELECT 1 FROM documents r WHERE r.reverses_document_id=d.id AND r.status='posted')",
                        (original["document_id"], kind),
                    )
                    if sum((Decimal(row["total_amount"]) for row in prior), Decimal(original.get("historical_returned_amount", "0"))) + total > Decimal(original["total_amount"]):
                        raise TransactionError("Returned amounts exceed the original total; rounding residual needs explicit correction")
            elif kind == "stock_opening":
                total = sum((Decimal(line["value"]) for line in values["lines"]), _ZERO)
                if total:
                    journal.append({"account_id": party, "debit": "0", "credit": canonical_decimal(total), "narration": "Opening inventory contra"})
            elif kind in VOUCHERS:
                total = sum((Decimal(line["amount"]) for line in values["lines"]), _ZERO)
                journal.append({"account_id": party, "debit": canonical_decimal(total) if kind.endswith("receipt") else "0", "credit": canonical_decimal(total) if kind.endswith("payment") else "0", "narration": values["notes"][:600]})
            else:
                total = sum((Decimal(line["debit"]) for line in journal), _ZERO)
            validate_balanced([JournalEntry(row["account_id"], row["debit"], row["credit"]) for row in journal], posting_places=places, expected_amount=total)
        calculation = {"total": canonical_decimal(total), "raw_totals": raw_totals, "journal_lines": journal, "stock_movements": movements}
        return {"values": values, "snapshot": snapshot, "calculation": calculation, "lines": lines, "account_id": party}

    @staticmethod
    def _write_metadata(connection, company, identifier, prepared):
        connection.execute(
            "INSERT INTO document_metadata(document_id,company_id,input_json,snapshot_json,calculation_json) VALUES(?,?,?,?,?)",
            (identifier, company, _json(prepared["values"]), _json(prepared["snapshot"]), _json(prepared["calculation"])),
        )
        for line in prepared["lines"]:
            connection.execute(
                "INSERT INTO document_lines(id,company_id,document_id,line_number,product_id,store_id,quantity,rate,discount_percent,net_amount,source_line_id,historical_source_line_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (line["id"], company, identifier, line["line_number"], line["product_id"], line["store_id"], line["quantity"], line["rate"], line["discount_percent"], line["net_amount"], line["source_line_id"], line.get("historical_source_line_id")),
            )

    @staticmethod
    def _clear_draft(connection, identifier):
        connection.execute("DELETE FROM stock_movements WHERE document_id=?", (identifier,))
        connection.execute("DELETE FROM journal_lines WHERE entry_id IN (SELECT id FROM journal_entries WHERE document_id=?)", (identifier,))
        connection.execute("DELETE FROM journal_entries WHERE document_id=?", (identifier,))
        connection.execute("DELETE FROM document_lines WHERE document_id=?", (identifier,))
        connection.execute("DELETE FROM document_metadata WHERE document_id=?", (identifier,))

    def save_draft(self, actor, company_id, kind, values, document_id=None, expected_revision=None):
        company = _id(company_id, "company_id")
        if kind not in MODULES:
            raise TransactionError("Unsupported operational kind; delivery, transfers and tax/labour workflows are not implemented")
        with native_transaction(self.path) as connection:
            user = self.security.require(actor, company, MODULES[kind], "update" if document_id else "create", connection=connection)
            if kind in RETURN_KINDS:
                self.security.require(actor, company, MODULES[RETURN_KINDS[kind]], "read", connection=connection)
            document = None
            if document_id is not None:
                document = self._document(connection, company, document_id)
                if document["status"] != "draft" or document["kind"] != kind:
                    raise TransactionError("Only a draft of the same kind can be edited")
                if document["revision"] != _revision(expected_revision):
                    raise RevisionConflict("Draft changed; reload before editing")
            elif expected_revision is not None:
                raise RevisionConflict("A new draft has no expected_revision")
            prepared = self._prepare(connection, company, kind, values)
            identifier, now = document_id or str(uuid4()), int(self.clock())
            normalized = prepared["values"]
            if connection.execute(
                "SELECT 1 FROM baseline_document_numbers WHERE company_id=? AND kind=? AND document_number=? LIMIT 1",
                (company, kind, normalized["document_number"]),
            ).fetchone() is not None:
                raise TransactionError("This document number already exists in the original history; use the next number")
            try:
                if document is None:
                    connection.execute(
                        "INSERT INTO documents(id,company_id,kind,document_number,business_date,account_id,total_amount,rounding_policy,posting_places,notes,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (identifier, company, kind, normalized["document_number"], normalized["business_date"], prepared["account_id"], prepared["calculation"]["total"], normalized["rounding_policy"], normalized["posting_places"], normalized["notes"], user.id, now, now),
                    )
                else:
                    self._clear_draft(connection, identifier)
                    connection.execute(
                        "UPDATE documents SET document_number=?,business_date=?,account_id=?,total_amount=?,rounding_policy=?,posting_places=?,notes=?,updated_at=?,revision=revision+1 WHERE id=? AND revision=?",
                        (normalized["document_number"], normalized["business_date"], prepared["account_id"], prepared["calculation"]["total"], normalized["rounding_policy"], normalized["posting_places"], normalized["notes"], now, identifier, expected_revision),
                    )
                self._write_metadata(connection, company, identifier, prepared)
            except sqlite3.IntegrityError as error:
                raise TransactionError("Draft conflicts with an existing number or a database integrity rule") from error
            self._audit(connection, user, company, "transaction.draft_saved", identifier, {"kind": kind, "updated": document is not None})
            return self._get(connection, company, identifier)

    def next_document_number(self, actor, company_id, kind):
        """Suggest the next numeric number across original history and new drafts.

        This is a suggestion, not a reservation. Saving still checks uniqueness
        under the write lock so two operators cannot create the same number.
        """
        return self.document_numbering(actor, company_id, kind)["next"]

    def document_numbering(self, actor, company_id, kind, *, action="create"):
        """Return the last observed numeric voucher number and next suggestion.

        Original and native numbers share the existing sequence. This reads
        metadata only; it neither loads a saved-record browser nor reserves a
        number. Editing uses the same fresh update grant as the draft form.
        """
        if action not in {"create", "update"}:
            raise TransactionError("Numbering requires create or update access")
        company = _id(company_id, "company_id")
        if kind not in MODULES:
            raise TransactionError("Unsupported document kind")
        with native_transaction(self.path) as connection:
            self.security.require(actor, company, MODULES[kind], action, connection=connection)
            rows = connection.execute(
                "SELECT document_number FROM documents WHERE company_id=? AND kind=? "
                "UNION ALL SELECT document_number FROM baseline_document_numbers WHERE company_id=? AND kind=?",
                (company, kind, company, kind),
            )
            greatest, width, last = 0, 1, ""
            for row in rows:
                number = row[0]
                if number.isascii() and number.isdecimal():
                    candidate = int(number)
                    if candidate > greatest or not last:
                        greatest, width, last = candidate, len(number), number
            suggested = str(greatest + 1).zfill(width)
            if len(suggested) > 80:
                raise TransactionError("The existing document number is too long; enter a new numbering series")
            return {"next": suggested, "last": last}

    @staticmethod
    def _stock_events(connection, company, keys):
        events = {key: [] for key in keys}
        if not keys:
            return events
        # Read a single committed-history snapshot. Candidate checks remain under
        # the same IMMEDIATE write lock that later publishes the new movements.
        rows = connection.execute(
            "SELECT m.*,d.business_date,d.posting_sequence FROM stock_movements m JOIN documents d ON d.id=m.document_id "
            "WHERE m.company_id=? AND d.status='posted' ORDER BY d.business_date,d.posting_sequence,m.id", (company,),
        )
        locations = {(store, product) for store, product, _ in keys}
        for row in rows:
            if (row["store_id"], row["product_id"]) not in locations:
                continue
            if row["pool"] is None:
                raise StockUnavailable("Existing stock has no verified pool classification; review pre-service movements first")
            if row["posting_sequence"] is None:
                raise StockUnavailable("Existing stock has no verified posting order; review pre-service movements first")
            key = (row["store_id"], row["product_id"], row["pool"])
            if key in events:
                quantity = Decimal(row["quantity"])
                delta = quantity if row["direction"] == "in" else quantity.copy_negate()
                events[key].append(((row["business_date"], 0, row["posting_sequence"], row["document_id"], row["id"]), delta))
        return events

    def _check_stock(self, connection, company, business_date, movements):
        keys = {(row["store_id"], row["product_id"], row["pool"]) for row in movements}
        events = self._stock_events(connection, company, keys)
        with localcontext(_CONTEXT):
            proposed = {}
            for row in movements:
                key = (row["store_id"], row["product_id"], row["pool"])
                quantity = Decimal(row["quantity"])
                delta = quantity if row["direction"] == "in" else quantity.copy_negate()
                proposed[key] = proposed.get(key, _ZERO) + delta
            for key, delta in proposed.items():
                # New same-day activity follows already posted activity. Later
                # dates are all checked, so backdating cannot borrow future stock.
                events[key].append(((business_date, 1, 0, "", ""), delta))
            for key, history in events.items():
                row = connection.execute(
                    "SELECT quantity FROM baseline_stock_balances "
                    "WHERE company_id=? AND store_id=? AND product_id=? AND pool=?",
                    (company, *key),
                ).fetchone()
                balance = Decimal(row[0]) if row is not None else _ZERO
                for _, delta in sorted(history):
                    balance += delta
                    # Preserve a source shortage exactly. Incoming stock may
                    # improve it; new outward stock cannot deepen it, even at
                    # a later date affected by this backdated transaction.
                    if delta < 0 and balance < 0:
                        raise StockUnavailable("Posting would make dated stock negative in this company, store, product or pool")

    @staticmethod
    def _write_effects(connection, company, identifier, calculation, notes):
        from bifma.valuation import valuation_for_document
        valuations = valuation_for_document(connection, company, identifier, calculation)
        if calculation["journal_lines"]:
            entry = str(uuid4())
            connection.execute("INSERT INTO journal_entries(id,company_id,document_id,description) VALUES(?,?,?,?)", (entry, company, identifier, notes))
            for index, line in enumerate(calculation["journal_lines"], 1):
                connection.execute(
                    "INSERT INTO journal_lines(id,company_id,entry_id,line_number,account_id,debit,credit,narration) VALUES(?,?,?,?,?,?,?,?)",
                    (str(uuid4()), company, entry, index, line["account_id"], line["debit"], line["credit"], line["narration"]),
                )
        for movement, valuation in zip(calculation["stock_movements"], valuations):
            movement_id = str(uuid4())
            connection.execute(
                "INSERT INTO stock_movements(id,company_id,document_id,document_line_id,product_id,store_id,direction,quantity,unit_cost,value,reverses_movement_id,pool,valuation_basis) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (movement_id, company, identifier, movement["document_line_id"], movement["product_id"], movement["store_id"], movement["direction"], movement["quantity"], movement["unit_cost"], movement["value"], movement["reverses_movement_id"], movement["pool"], movement["valuation_basis"]),
            )
            connection.execute(
                "INSERT INTO stock_valuation_snapshots(movement_id,unit_cost,value,source_json) VALUES(?,?,?,?)",
                (movement_id, valuation["unit_cost"], valuation["value"], _json(valuation["source"])),
            )

    def post(self, actor, company_id, document_id, expected_revision):
        company = _id(company_id, "company_id")
        expected_revision = _revision(expected_revision)
        with native_transaction(self.path) as connection:
            document = self._document(connection, company, document_id)
            user = self.security.require(actor, company, self._module(connection, document), "post", connection=connection)
            if document["kind"] in RETURN_KINDS:
                self.security.require(actor, company, MODULES[RETURN_KINDS[document["kind"]]], "read", connection=connection)
            if document["status"] != "draft":
                raise RevisionConflict("Document has already been posted; it cannot post twice")
            if document["revision"] != expected_revision:
                raise RevisionConflict("Draft changed; reload before posting")
            metadata = connection.execute("SELECT * FROM document_metadata WHERE document_id=?", (document_id,)).fetchone()
            if metadata is None:
                raise TransactionError("Draft has no verified service input; resave it through the current service")
            saved_lines = [dict(row) for row in connection.execute("SELECT * FROM document_lines WHERE document_id=? ORDER BY line_number", (document_id,))]
            stored_values = json.loads(metadata["input_json"])
            if document["kind"] in TRADE_KINDS | {"stock_opening"} and len(saved_lines) != len(stored_values.get("lines", [])):
                raise TransactionError("Persisted draft line count does not reconcile with its inputs")
            prepared = self._prepare(connection, company, document["kind"], stored_values, line_ids=[row["id"] for row in saved_lines])
            if _json(prepared["snapshot"]) != metadata["snapshot_json"]:
                raise RevisionConflict("Referenced masters changed; resave and review the draft before posting")
            if _json(prepared["calculation"]) != metadata["calculation_json"]:
                raise TransactionError("Draft calculation no longer reconciles; resave and review")
            for line, expected in zip(saved_lines, prepared["lines"]):
                if any(line[key] != value for key, value in expected.items()):
                    raise TransactionError("Persisted draft lines do not reconcile with the saved inputs")
            values = prepared["values"]
            checks = {"document_number": values["document_number"], "business_date": values["business_date"], "account_id": prepared["account_id"],
                      "total_amount": prepared["calculation"]["total"], "rounding_policy": values["rounding_policy"], "posting_places": values["posting_places"], "notes": values["notes"]}
            if len(saved_lines) != len(prepared["lines"]) or any(document[key] != value for key, value in checks.items()):
                raise TransactionError("Persisted draft header or line count does not reconcile")
            if connection.execute("SELECT 1 FROM journal_entries WHERE document_id=? UNION ALL SELECT 1 FROM stock_movements WHERE document_id=?", (document_id, document_id)).fetchone():
                raise TransactionError("Draft already contains unexpected posting effects")
            self._check_stock(connection, company, values["business_date"], prepared["calculation"]["stock_movements"])
            self._write_effects(connection, company, document_id, prepared["calculation"], values["notes"])
            self._audit(connection, user, company, "transaction.posted", document_id, {"kind": document["kind"], "revision": expected_revision})
            sequence = connection.execute("SELECT COALESCE(MAX(posting_sequence),0)+1 FROM documents").fetchone()[0]
            connection.execute("UPDATE documents SET status='posted',posted_by=?,posted_at=?,updated_at=?,posting_sequence=?,revision=revision+1 WHERE id=? AND revision=?",
                               (user.id, int(self.clock()), int(self.clock()), sequence, document_id, expected_revision))
            return self._get(connection, company, document_id)

    @staticmethod
    def _get(connection, company, identifier):
        document = TransactionService._document(connection, company, identifier)
        result = dict(document)
        metadata = connection.execute("SELECT * FROM document_metadata WHERE document_id=?", (identifier,)).fetchone()
        result.update(values=json.loads(metadata["input_json"]) if metadata else None,
                      snapshot=json.loads(metadata["snapshot_json"]) if metadata else None,
                      calculation=json.loads(metadata["calculation_json"]) if metadata else None)
        result["lines"] = [dict(row) for row in connection.execute("SELECT * FROM document_lines WHERE document_id=? ORDER BY line_number", (identifier,))]
        result["journal_lines"] = [dict(row) for row in connection.execute("SELECT l.* FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id WHERE e.document_id=? ORDER BY l.line_number", (identifier,))]
        result["stock_movements"] = [dict(row) for row in connection.execute("SELECT * FROM stock_movements WHERE document_id=? ORDER BY document_line_id,id", (identifier,))]
        result["stock_valuations"] = [dict(row) for row in connection.execute(
            "SELECT v.* FROM stock_valuation_snapshots v JOIN stock_movements m ON m.id=v.movement_id "
            "WHERE m.document_id=? ORDER BY m.document_line_id,m.id", (identifier,))]
        reversal = connection.execute("SELECT id,document_number,business_date FROM documents WHERE reverses_document_id=? AND status='posted'", (identifier,)).fetchone()
        result["reversal"] = dict(reversal) if reversal else None
        return result

    def get(self, actor, company_id, document_id):
        company = _id(company_id, "company_id")
        with native_transaction(self.path) as connection:
            document = self._document(connection, company, document_id)
            self.security.require(actor, company, self._module(connection, document), "read", connection=connection)
            return self._get(connection, company, document_id)

    def list_documents(self, actor, company_id, kind=None, *, search="", offset=0, limit=100, status=None):
        """List authorized headers, filtering status before pagination.

        status=None preserves the ordinary combined draft/posted list. Source
        pickers use status='posted' and advance offset over these filtered rows;
        newer matching drafts cannot hide posted invoices from the result set.
        """
        company = _id(company_id, "company_id")
        search = _text(search, "search", 200)
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 500:
            raise TransactionError("Use a nonnegative offset and a limit between 1 and 500")
        if kind is not None and kind not in MODULES and kind != "reversal":
            raise TransactionError("Unsupported document kind")
        if status is not None and (not isinstance(status, str) or status not in {"draft", "posted"}):
            raise TransactionError("Document status must be draft, posted or None")
        with native_transaction(self.path) as connection:
            permitted = []
            for candidate, module in MODULES.items():
                if kind not in (None, "reversal", candidate):
                    continue
                try:
                    self.security.require(actor, company, module, "read", connection=connection)
                    permitted.append(candidate)
                except AuthorizationError:
                    pass
            if not permitted:
                raise AuthorizationError("No document read permission is available")
            placeholders = ",".join("?" for _ in permitted)
            predicates = ["d.company_id=?", f"(d.kind IN ({placeholders}) OR (d.kind='reversal' AND original.kind IN ({placeholders})))"]
            parameters = [company, *permitted, *permitted]
            if kind is not None:
                predicates.append("d.kind=?")
                parameters.append(kind)
            if status is not None:
                predicates.append("d.status=?")
                parameters.append(status)
            if search:
                predicates.append("(instr(lower(d.document_number),lower(?))>0 OR instr(lower(d.notes),lower(?))>0)")
                parameters.extend((search, search))
            parameters.extend((limit, offset))
            rows = connection.execute(
                "SELECT d.* FROM documents d LEFT JOIN documents original ON original.id=d.reverses_document_id WHERE "
                + " AND ".join(predicates) + " ORDER BY d.business_date DESC,d.created_at DESC,d.id LIMIT ? OFFSET ?", parameters,
            )
            return [dict(row) for row in rows]

    def list_return_sources(self, actor, company_id, kind, *, search="", offset=0, limit=100):
        """Page eligible original invoices from history and new postings together."""
        company = _id(company_id, "company_id")
        if kind not in RETURN_KINDS:
            raise TransactionError("Choose a supported return kind")
        search = _text(search, "search", 150)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise TransactionError("Use a nonnegative offset and page size from 1 to 500")
        original_kind = RETURN_KINDS[kind]
        with native_transaction(self.path) as connection:
            self.security.require(actor, company, MODULES[kind], "create", connection=connection)
            self.security.require(actor, company, MODULES[original_kind], "read", connection=connection)
            rows = connection.execute(
                "SELECT * FROM (SELECT d.id,d.document_number,d.business_date,d.account_id,d.total_amount,"
                "d.kind,'native' AS source,d.notes FROM documents d "
                "WHERE d.company_id=? AND d.kind=? AND d.status='posted' AND NOT EXISTS "
                "(SELECT 1 FROM documents r WHERE r.reverses_document_id=d.id AND r.status='posted') "
                "UNION ALL SELECT d.id,d.document_number,d.business_date,d.account_id,d.total_amount,"
                "d.kind,'history' AS source,'' AS notes FROM baseline_trade_documents d "
                "WHERE d.company_id=? AND d.kind=? AND d.return_eligible=1 "
                "AND EXISTS(SELECT 1 FROM baseline_trade_lines l WHERE l.document_id=d.id AND l.return_eligible=1)) "
                "WHERE instr(lower(document_number),lower(?))>0 OR instr(lower(notes),lower(?))>0 "
                "ORDER BY business_date DESC,document_number DESC,id LIMIT ? OFFSET ?",
                (company, original_kind, company, original_kind, search, search, limit, offset),
            )
            return [dict(row) for row in rows]

    def get_return_source(self, actor, company_id, kind, document_id):
        """Read an original with its saved labels for the return-line chooser."""
        company, identifier = _id(company_id, "company_id"), _id(document_id, "document_id")
        if kind not in RETURN_KINDS:
            raise TransactionError("Choose a supported return kind")
        original_kind = RETURN_KINDS[kind]
        with native_transaction(self.path) as connection:
            self.security.require(actor, company, MODULES[kind], "create", connection=connection)
            self.security.require(actor, company, MODULES[original_kind], "read", connection=connection)
            native = connection.execute("SELECT kind,status FROM documents WHERE company_id=? AND id=?", (company, identifier)).fetchone()
            if native is not None:
                if native["kind"] != original_kind or native["status"] != "posted" or self._active_reversal(connection, identifier):
                    raise TransactionError("This invoice is unavailable for a return")
                return {**self._get(connection, company, identifier), "source": "native"}
            original = connection.execute("SELECT * FROM baseline_trade_documents WHERE company_id=? AND id=?", (company, identifier)).fetchone()
            if original is None or original["kind"] != original_kind:
                raise TransactionError("Original invoice is unavailable in the selected company")
            if not original["return_eligible"]:
                raise TransactionError("This historical invoice needs review before a return: " + original["exclusion_reason"])
            result = dict(original)
            result.update(source="history", status="historical", values=json.loads(original["input_json"]),
                          snapshot=json.loads(original["snapshot_json"]), calculation=None,
                          lines=[dict(row) for row in connection.execute(
                              "SELECT * FROM baseline_trade_lines WHERE company_id=? AND document_id=? ORDER BY line_number,id",
                              (company, identifier))], journal_lines=[], stock_movements=[], stock_valuations=[], reversal=None)
            return result

    def stock_balance(self, actor, company_id, *, product_id=None, store_id=None, pool="regular", date_to=None):
        company = _id(company_id, "company_id")
        if pool not in ("regular", "breakage"):
            raise TransactionError("pool must be regular or breakage")
        product = _id(product_id, "product_id") if product_id is not None else None
        store = _id(store_id, "store_id") if store_id is not None else None
        upper = _day(date_to) if date_to is not None else None
        with native_transaction(self.path) as connection:
            self.security.require(actor, company, "stock", "read", connection=connection)
            baseline = connection.execute("SELECT cutoff_date FROM baseline_imports WHERE singleton=1").fetchone()
            if baseline is not None and upper is not None and upper < baseline[0]:
                raise TransactionError("For dates before the starting balances, use the dated Stock quantities report.")
            clauses, params = ["company_id=?"], [company]
            for column, value in (("product_id", product), ("store_id", store)):
                if value is not None:
                    clauses.append(f"{column}=?")
                    params.append(value)
            if upper is not None:
                clauses.append("business_date<=?")
                params.append(upper)
            grouped = {}
            with localcontext(_CONTEXT):
                opening_clauses, opening_params = ["company_id=?", "pool=?"], [company, pool]
                for column, value in (("product_id", product), ("store_id", store)):
                    if value is not None:
                        opening_clauses.append(f"{column}=?")
                        opening_params.append(value)
                for row in connection.execute("SELECT store_id,product_id,quantity FROM baseline_stock_balances WHERE "
                                              + " AND ".join(opening_clauses), opening_params):
                    grouped[(row["store_id"], row["product_id"])] = Decimal(row["quantity"])
                for row in connection.execute("SELECT * FROM posted_stock_movements WHERE " + " AND ".join(clauses), params):
                    if row["pool"] is None:
                        raise StockUnavailable("Stock has unclassified pre-service movements; a verified pool is required")
                    if row["pool"] != pool:
                        continue
                    key = (row["store_id"], row["product_id"])
                    quantity = Decimal(row["quantity"])
                    grouped[key] = grouped.get(key, _ZERO) + (quantity if row["direction"] == "in" else quantity.copy_negate())
            return [{"company_id": company, "store_id": location, "product_id": item, "pool": pool, "quantity": canonical_decimal(quantity)} for (location, item), quantity in sorted(grouped.items())]

    def reverse(self, actor, company_id, document_id, expected_revision, *, document_number, business_date, reason):
        company = _id(company_id, "company_id")
        expected_revision = _revision(expected_revision)
        business_date = _day(business_date)
        reason = _text(reason, "reversal reason", 2000, blank=False)
        number = _text(document_number, "document_number", 80, blank=False).strip()
        with native_transaction(self.path) as connection:
            original = self._document(connection, company, document_id)
            user = self.security.require(actor, company, self._module(connection, original), "post", connection=connection)
            if original["status"] != "posted" or original["kind"] == "reversal" or original["revision"] != expected_revision:
                raise RevisionConflict("Reversal requires the current revision of a posted original document")
            if self._active_reversal(connection, document_id):
                raise RevisionConflict("Original document has already been reversed")
            if business_date < original["business_date"]:
                raise TransactionError("A reversal cannot predate its original")
            self._company(connection, company, business_date)
            active_return = connection.execute(
                "SELECT 1 FROM document_lines returned JOIN document_lines source ON source.id=returned.source_line_id "
                "JOIN documents d ON d.id=returned.document_id WHERE source.document_id=? AND d.status='posted' "
                "AND d.kind IN ('sale_return','purchase_return','cash_sale_return','cash_purchase_return') AND NOT EXISTS "
                "(SELECT 1 FROM documents r WHERE r.reverses_document_id=d.id AND r.status='posted') LIMIT 1", (document_id,),
            ).fetchone()
            if active_return:
                raise TransactionError("Reverse active linked returns before reversing their original invoice")
            source = self._get(connection, company, document_id)
            if source["snapshot"] is None:
                raise TransactionError("Original has no verified service snapshot; automatic reversal is unavailable")
            identifier, now = str(uuid4()), int(self.clock())
            lines, line_map = [], {}
            for line in source["lines"]:
                self._product(connection, line["product_id"])
                self._store(connection, company, line["store_id"])
                replacement = str(uuid4())
                line_map[line["id"]] = replacement
                lines.append({**{key: line[key] for key in ("line_number", "product_id", "store_id", "quantity", "rate", "discount_percent", "net_amount")},
                              "id": replacement, "source_line_id": line["id"]})
            journal, movements = [], []
            for line in source["journal_lines"]:
                self._account(connection, line["account_id"])
                journal.append({"account_id": line["account_id"], "debit": line["credit"], "credit": line["debit"], "narration": line["narration"]})
            for movement in source["stock_movements"]:
                if movement["pool"] is None:
                    raise StockUnavailable("Original stock has no verified pool; automatic reversal is unavailable")
                movements.append({"document_line_id": line_map[movement["document_line_id"]], "product_id": movement["product_id"], "store_id": movement["store_id"],
                                  "direction": "out" if movement["direction"] == "in" else "in", "quantity": movement["quantity"], "unit_cost": movement["unit_cost"], "value": movement["value"],
                                  "pool": movement["pool"], "valuation_basis": "reversal", "reverses_movement_id": movement["id"]})
            validate_balanced([JournalEntry(row["account_id"], row["debit"], row["credit"]) for row in journal], posting_places=original["posting_places"], expected_amount=original["total_amount"])
            self._check_stock(connection, company, business_date, movements)
            calculation = {"total": original["total_amount"], "raw_totals": {}, "journal_lines": journal, "stock_movements": movements}
            reversal_snapshot = {
                "company": source["snapshot"]["company"], "accounts": source["snapshot"]["accounts"],
                "lines": [{**row, "line_id": line_map[row["line_id"]]} for row in source["snapshot"]["lines"]],
                "reversal_of": {"id": document_id, "kind": original["kind"], "document_number": original["document_number"], "business_date": original["business_date"]},
                "original_snapshot": source["snapshot"],
            }
            for field in ("cash_settlement", "return_of"):
                if field in source["snapshot"]:
                    reversal_snapshot[field] = source["snapshot"][field]
            prepared = {"values": {"document_number": number, "business_date": business_date, "reason": reason, "original_document_id": document_id},
                        "snapshot": reversal_snapshot, "calculation": calculation, "lines": lines}
            try:
                connection.execute(
                    "INSERT INTO documents(id,company_id,kind,document_number,business_date,account_id,total_amount,rounding_policy,posting_places,notes,created_by,created_at,updated_at,reverses_document_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (identifier, company, "reversal", number, business_date, original["account_id"], original["total_amount"], original["rounding_policy"], original["posting_places"], reason, user.id, now, now, document_id),
                )
                self._write_metadata(connection, company, identifier, prepared)
                self._write_effects(connection, company, identifier, calculation, reason)
                self._audit(connection, user, company, "transaction.reversed", identifier, {"original_document_id": document_id, "reason": reason})
                sequence = connection.execute("SELECT COALESCE(MAX(posting_sequence),0)+1 FROM documents").fetchone()[0]
                connection.execute("UPDATE documents SET status='posted',posted_by=?,posted_at=?,updated_at=?,posting_sequence=?,revision=revision+1 WHERE id=?", (user.id, now, now, sequence, identifier))
            except sqlite3.IntegrityError as error:
                raise TransactionError("Reversal conflicts with an existing reversal, document number or integrity rule") from error
            return self._get(connection, company, identifier)
