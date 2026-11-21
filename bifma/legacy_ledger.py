"""Exact read-only reconstruction of the recovered Oracle LEDGER view.

Source: PUR_VW_DTL, SALE_VW_DTL and LEDGER in docs/legacy_schema.sql. Compatibility
remains unverified pending comparison with original Oracle reports. This module
preserves each arm's GROUP BY/HAVING, inner product joins, repeated header
adjustments, mismatched discount/freight rules, and potentially negative entries.
It never inserts balancing entries or repairs historical accounting anomalies.

The archive must use tools.stage_recovery's exact NUMBER-as-TEXT format. Invoice
detail views round their grouped subtotal, and LEDGER rounds their final amounts
again. Journal amounts are NOT rounded. Oracle narration number formatting may
depend on original NLS settings; this reader uses canonical decimal text.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, DecimalException, MAX_EMAX, MIN_EMIN, ROUND_HALF_UP, localcontext
from pathlib import Path

from bifma.database import session
# Both compatibility readers need exact historical arithmetic over Oracle's
# whole exponent range. These helpers perform no new-entry precision validation.
from bifma.legacy_stock import _date_bound, _exact_add, _exact_product, _source_date


_ZERO = Decimal(0)
COMPATIBILITY = "unverified_pending_oracle_report_comparison"


class LegacyLedgerError(ValueError):
    """An invalid ledger filter or unsupported historical value."""


def _number(value, *, zero=False):
    if value is None:
        return _ZERO if zero else None
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise LegacyLedgerError("Historical ledger amounts must be exact decimal values")
    try:
        number = Decimal(value)
    except (DecimalException, ValueError) as error:
        raise LegacyLedgerError("Invalid historical ledger decimal") from error
    if not number.is_finite():
        raise LegacyLedgerError("Historical ledger amounts must be finite")
    return number


def _text(number):
    if number is None:
        return None
    if number.is_zero():
        return "0"
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _round(number):
    with localcontext(Context(prec=max(number.adjusted() + 3, 1), Emax=MAX_EMAX, Emin=MIN_EMIN)):
        rounded = number.quantize(Decimal(1), rounding=ROUND_HALF_UP)
        # Oracle NUMBER has a single zero encoding, unlike Decimal's signed zero.
        return _ZERO if rounded.is_zero() else rounded


def _line_net(quantity, rate, percentage):
    quantity, rate = _number(quantity), _number(rate)
    if quantity is None or rate is None:
        return _ZERO  # NVL(QTY * (RATE - ...), 0), including a NULL rate.
    percentage = _number(percentage, zero=True)
    discount = _exact_product(rate, percentage)
    sign, digits, exponent = discount.as_tuple()
    discount = Decimal((sign, digits, exponent - 2))  # Exact division by 100.
    return _exact_product(quantity, _exact_add(rate, discount.copy_negate()))


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    transaction_id: str | None
    transaction_number: str | None
    voucher_type: str | None
    company_id: str
    account_code: str | None
    date: str | None
    debit: Decimal
    credit: Decimal
    narration: str | None
    source_arm: str


@dataclass(frozen=True, slots=True)
class AccountBalance:
    company_id: str
    account_code: str | None
    opening: Decimal
    debit: Decimal
    credit: Decimal
    closing: Decimal

    @property
    def opening_debit(self):
        return max(self.opening, _ZERO)

    @property
    def opening_credit(self):
        return max(self.opening.copy_negate(), _ZERO)

    @property
    def closing_debit(self):
        return max(self.closing, _ZERO)

    @property
    def closing_credit(self):
        return max(self.closing.copy_negate(), _ZERO)


_BASE = ("M.TRNID", "M.TRNNO", "M.TRNDATE", "M.TRN_TYPE", "M.CMP_ID")
_NUMERIC_GROUPS = {
    "M.TRNID", "M.TRNNO", "M.TRN_TYPE", "M.CMP_ID", "M.DISCOUNT", "M.BREAKAGE", "M.FREIGHT",
}


@dataclass(frozen=True)
class _Arm:
    name: str
    family: str
    groups: tuple[str, ...]
    product_join: bool
    side: str
    discount_sign: int = 0
    breakage_sign: int = 0
    freight_sign: int = 0
    having_adjustment: str | None = None


# Order follows the recovered UNION ALL branches. Every GROUP BY column matters,
# including unused product account codes and headers used only in narration.
_ARMS = (
    _Arm("purchase_product", "P", _BASE + ("I.PUR_CODE", "I.EXP_CODE", "M.BILLNO", "M.DISCOUNT", "M.BREAKAGE", "M.FREIGHT"), True, "debit", -1, -1, 1),
    _Arm("purchase_party", "P", _BASE + ("M.CHCODE", "M.P_TYPE", "M.DISCOUNT", "M.BILLNO", "M.BREAKAGE", "M.FREIGHT"), False, "credit", -1, -1, 1, "M.DISCOUNT"),
    _Arm("purchase_return_product", "BD", _BASE + ("I.PUR_CODE", "M.BILLNO", "M.DISCOUNT", "M.FREIGHT"), True, "credit", freight_sign=-1),
    _Arm("purchase_return_party", "BD", _BASE + ("M.CHCODE", "M.BILLNO", "M.FREIGHT", "M.DISCOUNT"), False, "debit", freight_sign=1),
    _Arm("sale_party", "S", _BASE + ("M.CHCODE", "M.S_TYPE", "M.DISCOUNT", "M.BILLNO", "M.BREAKAGE", "M.FREIGHT"), False, "debit", -1, -1, 1, "M.DISCOUNT"),
    _Arm("sale_product", "S", _BASE + ("I.PUR_CODE", "I.EXP_CODE", "M.BILLNO", "M.DISCOUNT", "M.BREAKAGE", "M.FREIGHT"), True, "credit", breakage_sign=-1, freight_sign=1),
    _Arm("breakage_return_party", "BR", _BASE + ("M.CHCODE", "M.BILLNO", "M.BREAKAGE", "M.FREIGHT"), False, "credit", breakage_sign=-1, freight_sign=-1, having_adjustment="M.BREAKAGE"),
    _Arm("breakage_return_product", "BR", _BASE + ("I.EXP_CODE", "M.BILLNO", "M.BREAKAGE", "M.FREIGHT"), True, "debit", breakage_sign=-1, freight_sign=-1),
)


def _account(arm, group):
    kind = group["M.TRN_TYPE"]
    if arm.name == "purchase_product":
        return group["I.PUR_CODE"] if kind == 7 else group["I.EXP_CODE"] if kind == 10 else None
    if arm.name == "sale_product":
        return group["I.EXP_CODE"] if kind == 8 else group["I.PUR_CODE"] if kind == 9 else None
    if arm.name == "purchase_return_product":
        return group["I.PUR_CODE"]
    if arm.name == "breakage_return_product":
        return group["I.EXP_CODE"]
    if arm.name in ("purchase_party", "sale_party"):
        payment_type = group["M.P_TYPE" if arm.family == "P" else "M.S_TYPE"]
        return group["M.CHCODE"] if payment_type == "R" else "04010001" if payment_type == "C" else None
    return group["M.CHCODE"]


def _narration(arm, group):
    if arm.family == "BR":
        return "Breakage Sale Return"
    kind = group["M.TRN_TYPE"]
    if arm.family == "P":
        prefix = "Purchase Invoice#" if kind == 7 else "Sale Return#" if kind == 10 else ""
    elif arm.family == "S":
        prefix = "Sale Invoice#" if kind == 8 else "Purchase Return#" if kind == 9 else ""
    else:
        prefix = "Purchase Return Invoice#"
    discount = group.get("M.DISCOUNT")
    suffix = "" if discount is None else "Discount " + _text(discount)
    # Oracle concatenation treats NULL operands as empty, but retains this space.
    return prefix + (group["M.BILLNO"] or "") + " " + suffix


class LegacyLedger:
    """Read a complete report from one readonly SQLite transaction snapshot.

    Date-only bounds include the complete calendar day. Signed balances use debit
    minus credit. ``date_from`` separates prior entries into opening balances;
    without it, opening is zero. Account prefixes are literal prefixes, not SQL
    LIKE patterns. Accounts missing from CHART still appear, as in source LEDGER.
    """

    compatibility = COMPATIBILITY

    def __init__(self, path):
        self.path = Path(path).resolve()

    @staticmethod
    def _filters(company_id, date_from, date_to, account):
        company = _number(company_id)
        if company is None:
            raise LegacyLedgerError("company_id is required")
        if account is not None and not isinstance(account, str):
            raise LegacyLedgerError("account filters must be strings")
        try:
            lower = _date_bound(date_from, end=False)
            upper = _date_bound(date_to, end=True)
        except ValueError as error:
            raise LegacyLedgerError(str(error)) from error
        if lower is not None and upper is not None and lower > upper:
            raise LegacyLedgerError("date_from cannot be after date_to")
        return _text(company), lower, upper

    def entries(self, company_id, *, date_from=None, date_to=None, account_code=None) -> list[LedgerEntry]:
        company, lower, upper = self._filters(company_id, date_from, date_to, account_code)
        with session(self.path) as connection:
            connection.execute("BEGIN")
            rows = self._entries(connection, company, lower, upper)
        return [row for row in rows if account_code is None or row.account_code == account_code]

    def balances(self, company_id, *, date_from=None, date_to=None, account_prefix=None) -> list[AccountBalance]:
        company, lower, upper = self._filters(company_id, date_from, date_to, account_prefix)
        with session(self.path) as connection:
            connection.execute("BEGIN")
            entries = self._entries(connection, company, None, upper)
        groups = {}
        for entry in entries:
            if account_prefix is not None and (entry.account_code is None or not entry.account_code.startswith(account_prefix)):
                continue
            totals = groups.setdefault(entry.account_code, [_ZERO, _ZERO, _ZERO])
            if lower is not None and entry.date is not None and entry.date < lower:
                totals[0] = _exact_add(totals[0], _exact_add(entry.debit, entry.credit.copy_negate()))
            elif lower is None or (entry.date is not None and entry.date >= lower):
                totals[1] = _exact_add(totals[1], entry.debit)
                totals[2] = _exact_add(totals[2], entry.credit)
        return [
            AccountBalance(company, account, opening, debit, credit,
                           _exact_add(_exact_add(opening, debit), credit.copy_negate()))
            for account, (opening, debit, credit) in sorted(groups.items(), key=lambda item: item[0] or "")
        ]

    @staticmethod
    def _where(company, lower, upper):
        where, parameters = ["M.CMP_ID = ?"], [company]
        if lower is not None:
            where.append("M.TRNDATE >= ?")
            parameters.append(lower)
        if upper is not None:
            where.append("M.TRNDATE <= ?")
            parameters.append(upper)
        return " AND ".join(where), parameters

    def _entries(self, connection, company, lower, upper):
        where, parameters = self._where(company, lower, upper)
        entries = self._journals(connection, where, parameters)
        for arm in _ARMS:
            entries.extend(self._invoice_arm(connection, where, parameters, arm))
        return sorted(entries, key=lambda item: (
            item.date or "", item.transaction_id or "", item.source_arm, item.account_code or "",
        ))

    @staticmethod
    def _journals(connection, where, parameters):
        columns = "M.TRNID, M.TRNNO, M.VTYPE, M.CMP_ID, M.TRNDATE, D.CHCODE, D.DR, D.CR, D.NARRATION"
        rows = connection.execute(
            f"SELECT {columns} FROM JMST M JOIN JDTL D ON M.TRNID=D.TRNID WHERE {where} AND M.CANCEL IS NULL",
            parameters,
        )
        result = [LedgerEntry(
            row["TRNID"], row["TRNNO"], row["VTYPE"], row["CMP_ID"], row["CHCODE"],
            _source_date(row["TRNDATE"]), _number(row["DR"], zero=True), _number(row["CR"], zero=True),
            row["NARRATION"], "journal_detail",
        ) for row in rows]
        # SQL grouping also combines duplicate CONFIG rows sharing one cash code;
        # their detail amounts are repeated in the sum, exactly as the inner join.
        rows = connection.execute(
            "SELECT M.TRNID, M.TRNNO, M.VTYPE, M.CMP_ID, M.TRNDATE, C.CHCODE_CASH, D.DR, D.CR "
            "FROM JMST M JOIN JDTL D ON M.TRNID=D.TRNID JOIN CONFIG C ON M.CMP_ID=C.CMP_ID "
            f"WHERE {where} AND M.CANCEL IS NULL AND M.VTYPE IN ('2','3')", parameters,
        )
        groups = {}
        for row in rows:
            key = (_number(row["TRNID"]), _number(row["TRNNO"]), _number(row["VTYPE"]),
                   _number(row["CMP_ID"]), _source_date(row["TRNDATE"]), row["CHCODE_CASH"])
            amount = _number(row["CR"] if key[2] == 3 else row["DR"], zero=True)
            groups[key] = _exact_add(groups.get(key, _ZERO), amount)
        for (identifier, number, kind, company, effective_date, cash), amount in groups.items():
            result.append(LedgerEntry(
                _text(identifier), _text(number), _text(kind), _text(company), cash, effective_date,
                amount if kind == 3 else _ZERO, amount if kind == 2 else _ZERO, None, "journal_cash",
            ))
        return result

    @staticmethod
    def _invoice_arm(connection, where, parameters, arm):
        aliases = [f'{column} AS "g{index}"' for index, column in enumerate(arm.groups)]
        selected = ", ".join(aliases) + ", D.QTY, D.RATE, D.DIS_PER"
        source = f"{arm.family}MST M JOIN {arm.family}DTL D ON M.TRNID=D.TRNID"
        if arm.product_join:
            source += " JOIN PUR_ITEM I ON D.ICODE=I.ICODE"
        groups = {}
        for row in connection.execute(f"SELECT {selected} FROM {source} WHERE {where}", parameters):
            key = tuple(
                _number(row[f"g{index}"]) if column in _NUMERIC_GROUPS
                else _source_date(row[f"g{index}"]) if column == "M.TRNDATE"
                else row[f"g{index}"]
                for index, column in enumerate(arm.groups)
            )
            net = _line_net(row["QTY"], row["RATE"], row["DIS_PER"])
            groups[key] = _exact_add(groups.get(key, _ZERO), net)
        result = []
        for key, subtotal in groups.items():
            group = dict(zip(arm.groups, key))
            base = _round(subtotal)
            having = base
            if arm.having_adjustment:
                adjustment = group[arm.having_adjustment]
                having = _exact_add(base, (adjustment if adjustment is not None else _ZERO).copy_negate())
            if having <= 0:
                continue
            amount = base
            for column, sign in (("M.DISCOUNT", arm.discount_sign), ("M.BREAKAGE", arm.breakage_sign), ("M.FREIGHT", arm.freight_sign)):
                if sign:
                    adjustment = group[column]
                    adjustment = adjustment if adjustment is not None else _ZERO
                    amount = _exact_add(amount, adjustment if sign > 0 else adjustment.copy_negate())
            amount = _round(amount)  # The enclosing LEDGER view's second ROUND.
            result.append(LedgerEntry(
                _text(group["M.TRNID"]), _text(group["M.TRNNO"]), _text(group["M.TRN_TYPE"]),
                _text(group["M.CMP_ID"]), _account(arm, group), group["M.TRNDATE"],
                amount if arm.side == "debit" else _ZERO,
                amount if arm.side == "credit" else _ZERO, _narration(arm, group), arm.name,
            ))
        return result
