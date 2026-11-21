"""Freeze exact movement costs using the source's purchase-rate method.

Historical values are imported separately and never recomputed. New costs use
the latest eligible native posting sequence and line, across stores. A native
reversal removes its rate candidate from that reversal date onward, while all
already saved movement values remain unchanged. Discounts/header charges are
not part of this legacy-compatible inventory method.
"""

from decimal import Context, Decimal, localcontext
from fractions import Fraction
import json


_PRIMARY = ("purchase", "cash_purchase", "stock_opening")
_FALLBACK = ("sale_return", "cash_sale_return")


def _text(value):
    if value.is_zero():
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _unit_rate(value, quantity):
    """A repeating ratio is not silently rounded into a future purchase rate."""
    ratio = Fraction(Decimal(value)) / Fraction(Decimal(quantity))
    denominator = ratio.denominator
    for factor in (2, 5):
        while denominator % factor == 0:
            denominator //= factor
    if denominator != 1:
        return None
    return Decimal(value) / Decimal(quantity)


def _candidate(connection, company_id, product_id, business_date, kinds):
    placeholders = ",".join("?" for _ in kinds)
    return connection.execute(
        "SELECT d.id AS document_id,d.kind,d.business_date,d.posting_sequence,l.id AS line_id,"
        "l.line_number,l.rate,l.quantity,l.net_amount FROM documents d JOIN document_lines l ON l.document_id=d.id "
        "WHERE d.company_id=? AND d.status='posted' AND l.product_id=? AND d.business_date<=? "
        f"AND d.kind IN ({placeholders}) "
        "AND (d.kind<>'stock_opening' OR EXISTS (SELECT 1 FROM stock_movements m "
        "WHERE m.document_line_id=l.id AND m.pool='regular')) "
        "AND NOT EXISTS(SELECT 1 FROM documents r WHERE r.reverses_document_id=d.id "
        "AND r.status='posted' AND r.business_date<=?) "
        "ORDER BY d.posting_sequence DESC,d.posted_at DESC,d.id DESC,l.line_number DESC LIMIT 1",
        (company_id, product_id, business_date, *kinds, business_date)).fetchone()


def _native_rate(row, rate_source):
    source = {"method": "last_purchase_rate", "origin": "native", "rate_source": rate_source,
              **{key: row[key] for key in ("document_id", "line_id", "business_date", "posting_sequence", "line_number")}}
    if row["posting_sequence"] is None:
        return None, {**source, "issue": "missing_posting_sequence"}
    rate = _unit_rate(row["net_amount"], row["quantity"]) if row["kind"] == "stock_opening" else Decimal(row["rate"])
    if rate is None:
        source["issue"] = "nonterminating_opening_unit_cost"
    return rate, source


def _purchase_rate(connection, company_id, product_id, business_date, pending):
    if product_id in pending and pending[product_id][1]["rate_source"] == "primary":
        return pending[product_id]
    primary = _candidate(connection, company_id, product_id, business_date, _PRIMARY)
    if primary is not None:
        return _native_rate(primary, "primary")
    baseline = connection.execute(
        "SELECT r.*,i.source_sha256,i.cutoff_date FROM baseline_purchase_rates r CROSS JOIN baseline_imports i "
        "WHERE r.company_id=? AND r.product_id=?", (company_id, product_id)).fetchone()

    def baseline_rate():
        source = {"method": "last_purchase_rate", "origin": "baseline", "rate_source": baseline["rate_source"],
                  "source_id": baseline["source_id"], "source_sha256": baseline["source_sha256"],
                  "cutoff_date": baseline["cutoff_date"], "original": json.loads(baseline["source_json"])}
        if baseline["rate_source"] == "missing":
            return None, {**source, "issue": "missing_source_rate"}
        rate = Decimal(baseline["rate"])
        if rate < 0:
            return None, {**source, "issue": "negative_source_rate"}
        return rate, source

    if baseline is not None and baseline["rate_source"] == "primary":
        return baseline_rate()
    if product_id in pending:
        return pending[product_id]
    fallback = _candidate(connection, company_id, product_id, business_date, _FALLBACK)
    if fallback is not None:
        return _native_rate(fallback, "fallback")
    if baseline is not None:
        return baseline_rate()
    return None, {"method": "last_purchase_rate", "origin": "unavailable", "issue": "missing_purchase_rate"}


def valuation_for_document(connection, company_id, document_id, calculation):
    """Return costs aligned with stock movements, inside the posting lock.

    The caller persists these with each inserted movement before posting the
    document. Missing costs are explicit NULLs. A known total opening value can
    exist independently of an exactly representable per-unit rate.
    """
    document = connection.execute("SELECT * FROM documents WHERE id=? AND company_id=?", (document_id, company_id)).fetchone()
    if document is None or document["status"] != "draft":
        raise ValueError("Valuation requires the current unposted document in its company.")
    lines = {row["id"]: row for row in connection.execute(
        "SELECT * FROM document_lines WHERE company_id=? AND document_id=? ORDER BY line_number", (company_id, document_id))}
    with localcontext(Context(prec=1000)):
        pending = {}
        if document["kind"] in {"purchase", "cash_purchase", "sale_return", "cash_sale_return"}:
            for row in lines.values():
                pending[row["product_id"]] = (Decimal(row["rate"]), {
                    "method": "last_purchase_rate", "origin": "current_document",
                    "rate_source": "primary" if document["kind"] in {"purchase", "cash_purchase"} else "fallback",
                    "document_id": document_id, "line_id": row["id"], "line_number": row["line_number"],
                    "business_date": document["business_date"]})
        result, costs = [], {}
        for movement in calculation["stock_movements"]:
            original_id = movement.get("reverses_movement_id")
            if original_id:
                original = connection.execute(
                    "SELECT v.unit_cost,v.value,v.source_json,m.valuation_basis,m.value AS original_value,m.quantity "
                    "FROM stock_movements m LEFT JOIN stock_valuation_snapshots v ON v.movement_id=m.id "
                    "WHERE m.id=? AND m.company_id=?", (original_id, company_id)).fetchone()
                if original is None:
                    raise ValueError("The original movement is unavailable for exact value reversal.")
                if original["source_json"] is not None:
                    result.append({"unit_cost": original["unit_cost"], "value": original["value"],
                                   "source": {"method": "exact_reversal", "movement_id": original_id,
                                              "original": json.loads(original["source_json"])}})
                elif original["valuation_basis"] == "opening_explicit":
                    rate = _unit_rate(original["original_value"], original["quantity"])
                    result.append({"unit_cost": _text(rate) if rate is not None else None,
                                   "value": original["original_value"], "source": {"method": "exact_reversal",
                                   "movement_id": original_id, "original": {"method": "explicit_opening"}}})
                else:
                    result.append({"unit_cost": None, "value": None, "source": {"method": "exact_reversal",
                                   "movement_id": original_id, "issue": "original_cost_unavailable"}})
                continue
            line = lines[movement["document_line_id"]]
            quantity = Decimal(movement["quantity"])
            if document["kind"] == "stock_opening":
                value = Decimal(movement["value"])
                rate = _unit_rate(value, quantity)
                source = {"method": "explicit_opening", "document_id": document_id, "line_id": line["id"]}
                if rate is None:
                    source["issue"] = "nonterminating_opening_unit_cost"
            elif document["kind"] in {"breakage_receive", "breakage_dispatch"}:
                rate = Decimal(line["rate"])
                value = quantity * rate
                source = {"method": "breakage_line_rate", "document_id": document_id, "line_id": line["id"]}
            else:
                product_id = movement["product_id"]
                if product_id not in costs:
                    costs[product_id] = _purchase_rate(connection, company_id, product_id, document["business_date"], pending)
                rate, source = costs[product_id]
                value = quantity * rate if rate is not None else None
            result.append({"unit_cost": _text(rate) if rate is not None else None,
                           "value": _text(value) if value is not None else None, "source": source})
        return result
