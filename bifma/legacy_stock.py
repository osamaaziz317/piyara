"""Read-only reconstruction of recovered STOCK/STOCKB and GET_PUR_RATE.

Compatibility is unverified until these results are compared with original
Oracle reports. The source is ``docs/legacy_schema.sql``. This module intentionally
preserves its anomalies: both breakage arms are incoming, delivery-challan lines
are excluded, and the regular valuation rate is the greatest eligible *numeric
ID*, across all stores, rather than the latest dated purchase. Sale returns are
only a fallback when no opening/purchase rate exists. Discounts do not affect
these stock valuations. No source data is repaired, rounded or written.

The input is the recovery archive made by tools.stage_recovery, whose NUMBER
columns are canonical decimal TEXT. Historical values use unrestricted finite
Decimal parsing and exact arithmetic, independent of new-entry precision limits.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Context, Decimal, DecimalException, MAX_EMAX, MIN_EMIN, localcontext
from itertools import groupby
from pathlib import Path
from typing import Literal

from bifma.database import session


Stream = Literal["regular", "breakage"]
DateFilter = str | date | datetime | None
_ZERO = Decimal(0)
COMPATIBILITY = "unverified_pending_oracle_report_comparison"


class LegacyStockError(ValueError):
    """An invalid report filter or unsupported historical value."""


class AmbiguousPurchaseRate(LegacyStockError):
    """The legacy SELECT INTO would encounter multiple highest-ID candidates."""


def _number(value, *, field="number", null_zero=False):
    if value is None:
        if null_zero:
            return _ZERO
        return None
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise LegacyStockError(f"{field} is not an exact recovered decimal")
    try:
        result = Decimal(value)
    except (DecimalException, ValueError) as error:
        raise LegacyStockError(f"{field} is not a valid recovered decimal") from error
    if not result.is_finite():
        raise LegacyStockError(f"{field} must be finite")
    return result


def _identifier(value, *, field):
    if value is None:
        raise LegacyStockError(f"{field} is required")
    number = _number(value, field=field)
    if number.is_zero():
        return "0"
    result = format(number, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _product(value):
    if value is not None and not isinstance(value, str):
        raise LegacyStockError("product_code must be a string")
    return value


def _date_bound(value: DateFilter, *, end: bool) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(value)
        except ValueError as error:
            raise LegacyStockError("report dates must be ISO dates or naive ISO timestamps") from error
    if isinstance(value, datetime):
        if value.tzinfo is not None or value.microsecond:
            raise LegacyStockError("report timestamps must be naive and have whole-second precision")
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return datetime.combine(value, time(23, 59, 59) if end else time.min).isoformat(timespec="seconds")
    raise LegacyStockError("report dates must be ISO dates or naive ISO timestamps")


def _source_date(value):
    # Recovery preserves Oracle DATE as a naive timestamp with second precision.
    return _date_bound(value, end=False)


def _exact_add(first: Decimal, second: Decimal) -> Decimal:
    exponent = min(first.as_tuple().exponent, second.as_tuple().exponent)
    precision = max(first.adjusted(), second.adjusted(), 0) - exponent + 3
    with localcontext(Context(prec=max(precision, 1), Emax=MAX_EMAX, Emin=MIN_EMIN)):
        return first + second


def _exact_product(first: Decimal, second: Decimal) -> Decimal:
    precision = len(first.as_tuple().digits) + len(second.as_tuple().digits) + 1
    with localcontext(Context(prec=precision, Emax=MAX_EMAX, Emin=MIN_EMIN)):
        return first * second


@dataclass(frozen=True, slots=True)
class StockMovement:
    stream: Stream
    tag: str | None
    company_id: str
    store_id: str | None
    transaction_id: str | None
    transaction_number: str | None
    date: str | None
    account_code: str | None
    line_id: str | None
    product_code: str
    product_name: str
    large_unit_id: str | None
    small_unit_id: str | None
    unit_factor: Decimal
    quantity_in: Decimal
    quantity_out: Decimal
    value_in: Decimal
    value_out: Decimal
    rate: Decimal | None
    source_table: str


@dataclass(frozen=True, slots=True)
class StockBalance:
    stream: Stream
    company_id: str
    store_id: str | None
    product_code: str
    product_name: str
    opening_quantity: Decimal
    opening_value: Decimal
    quantity_in: Decimal
    quantity_out: Decimal
    value_in: Decimal
    value_out: Decimal
    closing_quantity: Decimal
    closing_value: Decimal


@dataclass(frozen=True, slots=True)
class _RateState:
    identifier: Decimal
    rate: Decimal
    count: int


class _RateTimeline:
    """Prefix maxima make each historical lookup O(log(candidate dates))."""

    def __init__(self, candidates):
        self.dates = []
        self.states = []
        state = None
        candidates.sort(key=lambda item: item[0])
        for effective_date, group in groupby(candidates, key=lambda item: item[0]):
            for _, identifier, rate in group:
                if state is None or identifier > state.identifier:
                    state = _RateState(identifier, rate, 1)
                elif identifier == state.identifier:
                    state = _RateState(identifier, state.rate, state.count + 1)
            self.dates.append(effective_date)
            self.states.append(state)

    def at(self, effective_date):
        position = bisect_right(self.dates, effective_date) - 1
        return self.states[position] if position >= 0 else None


class _Rates:
    def __init__(self, primary, fallback):
        self.primary = {code: _RateTimeline(rows) for code, rows in primary.items()}
        self.fallback = {code: _RateTimeline(rows) for code, rows in fallback.items()}

    def at(self, product_code, effective_date):
        if effective_date is None:
            return _ZERO
        for candidates in (self.primary, self.fallback):
            timeline = candidates.get(product_code)
            state = timeline.at(effective_date) if timeline else None
            if state is None:
                continue
            if state.count != 1:
                raise AmbiguousPurchaseRate(
                    "GET_PUR_RATE has multiple eligible rows at its highest ID; "
                    "valuation requires review against Oracle, not a guessed rate"
                )
            return state.rate
        return _ZERO


class LegacyStock:
    """Report from an immutable recovery archive through a single read snapshot.

    Date-only upper bounds include their entire day. An explicit timestamp is
    exact to the second. ``balances(date_from=...)`` includes all earlier matching
    movements as opening balances; period movement totals honor both bounds.
    Without a lower bound, opening balances are zero and totals include all
    history through the upper bound, including OPEN_STOCK entries.
    """

    compatibility = COMPATIBILITY

    def __init__(self, path):
        self.path = Path(path).resolve()

    @staticmethod
    def _filters(company_id, date_from, date_to, store_id, product_code, stream):
        if stream not in ("regular", "breakage"):
            raise LegacyStockError("stream must be 'regular' or 'breakage'")
        lower = _date_bound(date_from, end=False)
        upper = _date_bound(date_to, end=True)
        if lower is not None and upper is not None and lower > upper:
            raise LegacyStockError("date_from cannot be after date_to")
        return (
            _identifier(company_id, field="company_id"),
            lower,
            upper,
            _identifier(store_id, field="store_id") if store_id is not None else None,
            _product(product_code),
            stream,
        )

    def movements(self, company_id, *, date_from=None, date_to=None, store_id=None,
                  product_code=None, stream: Stream = "regular") -> list[StockMovement]:
        filters = self._filters(company_id, date_from, date_to, store_id, product_code, stream)
        with session(self.path) as connection:
            connection.execute("BEGIN")
            return self._movements(connection, *filters)

    def balances(self, company_id, *, date_from=None, date_to=None, store_id=None,
                 product_code=None, stream: Stream = "regular") -> list[StockBalance]:
        company, lower, upper, store, product, stream = self._filters(
            company_id, date_from, date_to, store_id, product_code, stream,
        )
        with session(self.path) as connection:
            connection.execute("BEGIN")
            movements = self._movements(connection, company, None, upper, store, product, stream)
        groups = {}
        for movement in movements:
            key = (movement.company_id, movement.store_id, movement.product_code)
            if key not in groups:
                groups[key] = (movement.product_name, [_ZERO] * 6)
            _, amounts = groups[key]
            if lower is not None and movement.date is not None and movement.date < lower:
                amounts[0] = _exact_add(amounts[0], _exact_add(movement.quantity_in, movement.quantity_out.copy_negate()))
                amounts[1] = _exact_add(amounts[1], _exact_add(movement.value_in, movement.value_out.copy_negate()))
            elif lower is None or (movement.date is not None and movement.date >= lower):
                for index, amount in enumerate((movement.quantity_in, movement.quantity_out, movement.value_in, movement.value_out), 2):
                    amounts[index] = _exact_add(amounts[index], amount)
        result = []
        for (company, store, product), (name, amounts) in groups.items():
            opening_quantity, opening_value, incoming, outgoing, value_in, value_out = amounts
            result.append(StockBalance(
                stream, company, store, product, name,
                opening_quantity, opening_value, incoming, outgoing, value_in, value_out,
                _exact_add(_exact_add(opening_quantity, incoming), outgoing.copy_negate()),
                _exact_add(_exact_add(opening_value, value_in), value_out.copy_negate()),
            ))
        return sorted(result, key=lambda item: (item.company_id, item.store_id or "", item.product_code))

    def purchase_rate(self, company_id, product_code, as_of) -> Decimal:
        """Expose GET_PUR_RATE semantics for diagnostics; no store filter exists."""
        company = _identifier(company_id, field="company_id")
        product = _product(product_code)
        if product is None:
            raise LegacyStockError("product_code is required")
        upper = _date_bound(as_of, end=True)
        with session(self.path) as connection:
            connection.execute("BEGIN")
            return self._rates(connection, company, upper, product).at(product, upper)

    @staticmethod
    def _conditions(company, lower, upper, store, product, *, company_column,
                    date_column, store_column=None, product_column):
        clauses, parameters = [f"{company_column} = ?"], [company]
        for column, operator, value in (
            (date_column, ">=", lower), (date_column, "<=", upper),
            (store_column, "=", store), (product_column, "=", product),
        ):
            if value is not None:
                clauses.append(f"{column} {operator} ?")
                parameters.append(value)
        return " AND ".join(clauses), parameters

    def _rates(self, connection, company, upper, product):
        primary, fallback = {}, {}
        specs = (
            ("OPEN_STOCK O JOIN PUR_ITEM I ON O.ICODE=I.ICODE",
             "O.CMP_ID", "O.OPEN_DATE", "O.ICODE", "O.TRNID", "O.OPEN_RATE", None, primary),
            ("PMST M JOIN PDTL D ON M.TRNID=D.TRNID JOIN PUR_ITEM I ON D.ICODE=I.ICODE",
             "M.CMP_ID", "M.TRNDATE", "D.ICODE", "D.VNO", "D.RATE", "7", primary),
            ("PMST M JOIN PDTL D ON M.TRNID=D.TRNID",
             "M.CMP_ID", "M.TRNDATE", "D.ICODE", "D.VNO", "D.RATE", "10", fallback),
        )
        for source, company_column, date_column, product_column, id_column, rate_column, kind, target in specs:
            where, parameters = self._conditions(
                company, None, upper, None, product, company_column=company_column,
                date_column=date_column, product_column=product_column,
            )
            if kind is not None:
                where += " AND M.TRN_TYPE = ?"
                parameters.append(kind)
            statement = (
                f"SELECT {date_column} AS effective_date, {product_column} AS product_code, "
                f"{id_column} AS candidate_id, {rate_column} AS rate FROM {source} WHERE {where}"
            )
            for row in connection.execute(statement, parameters):
                effective_date = _source_date(row["effective_date"])
                identifier = _number(row["candidate_id"], field="rate candidate ID")
                # Oracle MAX ignores null IDs; null dates never satisfy <=.
                if effective_date is None or identifier is None:
                    continue
                target.setdefault(row["product_code"], []).append((
                    effective_date, identifier, _number(row["rate"], field="purchase rate", null_zero=True),
                ))
        return _Rates(primary, fallback)

    def _movements(self, connection, company, lower, upper, store, product, stream):
        common = (
            "I.ICODE AS product_code, I.INAME AS product_name, "
            "I.UOM_ID_LARGE AS large_unit_id, I.UOM_ID_SMALL AS small_unit_id, "
            "I.QTY_SMALL_UOM AS unit_factor"
        )
        # All expressions below are static source mappings, never user SQL.
        if stream == "regular":
            specs = (
                ("OPEN_STOCK", "OPEN_STOCK M JOIN PUR_ITEM I ON M.ICODE=I.ICODE",
                 "'OP'", "M.STORE_ID", "M.TRNID", "M.OPEN_DATE", "I.PUR_CODE", "M.TRNID", "M.ICODE", "M.OPEN_QTY", "M.OPEN_RATE", True, False, ""),
                ("PDTL", "PMST M JOIN PDTL D ON M.TRNID=D.TRNID JOIN PUR_ITEM I ON D.ICODE=I.ICODE",
                 "CASE M.TRN_TYPE WHEN '7' THEN 'PV' WHEN '10' THEN 'SR' END", "D.STORE_ID", "M.TRNNO", "M.TRNDATE", "M.CHCODE", "D.VNO", "D.ICODE", "D.QTY", "NULL", True, True, ""),
                ("SDTL", "SMST M JOIN SDTL D ON M.TRNID=D.TRNID JOIN PUR_ITEM I ON D.ICODE=I.ICODE",
                 "CASE M.TRN_TYPE WHEN '8' THEN 'SV' WHEN '9' THEN 'PR' END", "D.STORE_ID", "M.TRNNO", "M.TRNDATE", "M.CHCODE", "D.VNO", "D.ICODE", "D.QTY", "NULL", False, True, " AND D.VNO_DCDTL IS NULL"),
            )
        else:
            specs = (
                ("BRDTL", "BRMST M JOIN BRDTL D ON M.TRNID=D.TRNID JOIN PUR_ITEM I ON D.ICODE=I.ICODE",
                 "'BR'", "M.STORE_ID", "M.TRNNO", "M.TRNDATE", "M.CHCODE", "D.VNO", "D.ICODE", "D.QTY", "D.RATE", True, False, ""),
                ("BDDTL", "BDMST M JOIN BDDTL D ON M.TRNID=D.TRNID JOIN PUR_ITEM I ON D.ICODE=I.ICODE",
                 "'BD'", "D.STORE_ID", "M.TRNNO", "M.TRNDATE", "M.CHCODE", "D.VNO", "D.ICODE", "D.QTY", "D.RATE", True, False, ""),
            )
        raw = []
        for table, source, tag, store_column, number, date_column, account, line, product_column, quantity, rate, incoming, valued, extra in specs:
            where, parameters = self._conditions(
                company, lower, upper, store, product, company_column="M.CMP_ID",
                date_column=date_column, store_column=store_column, product_column=product_column,
            )
            statement = (
                f"SELECT {tag} AS tag, M.CMP_ID AS company_id, {store_column} AS store_id, "
                f"M.TRNID AS transaction_id, {number} AS transaction_number, {date_column} AS effective_date, "
                f"{account} AS account_code, {line} AS line_id, {quantity} AS quantity, {rate} AS rate, "
                f"{common} FROM {source} WHERE {where}{extra}"
            )
            for row in connection.execute(statement, parameters):
                quantity_value = _number(row["quantity"], field="stock quantity", null_zero=True)
                if quantity_value > 0:
                    raw.append((row, table, incoming, valued, quantity_value))
        rates = self._rates(connection, company, upper, product) if any(row[3] for row in raw) else None
        result = []
        for row, table, incoming, valued, quantity in raw:
            effective_date = _source_date(row["effective_date"])
            rate = rates.at(row["product_code"], effective_date) if valued else _number(row["rate"], field="stock rate")
            value = _exact_product(quantity, rate) if rate is not None else _ZERO
            result.append(StockMovement(
                stream=stream, tag=row["tag"], company_id=row["company_id"], store_id=row["store_id"],
                transaction_id=row["transaction_id"], transaction_number=row["transaction_number"],
                date=effective_date, account_code=row["account_code"], line_id=row["line_id"],
                product_code=row["product_code"], product_name=row["product_name"],
                large_unit_id=row["large_unit_id"], small_unit_id=row["small_unit_id"],
                unit_factor=_number(row["unit_factor"], field="stock unit factor", null_zero=True),
                quantity_in=quantity if incoming else _ZERO,
                quantity_out=_ZERO if incoming else quantity,
                value_in=value if incoming else _ZERO,
                value_out=_ZERO if incoming else value,
                rate=rate, source_table=table,
            ))
        return sorted(result, key=lambda item: (
            item.date or "", item.transaction_id or "", item.source_table, item.line_id or "",
        ))
