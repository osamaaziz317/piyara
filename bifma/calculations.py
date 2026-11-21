"""Exact, explicit calculations for new BIFMA transaction entries.

The recovered normal-sale and normal-purchase *party* view arms calculate::

    ROUND(SUM(qty * (rate - rate / 100 * discount_percent)))
        - header_discount - breakage + freight

The extracted evidence is in ``docs/legacy_schema.sql`` (``SALE_VW_DTL`` and
``PUR_VW_DTL``). ``LEGACY_WHOLE`` reproduces that subtotal rounding. It does not establish a
balanced legacy ledger: other recovered view arms contain inconsistent discount
and freight expressions. Returns, GST and labour need their own verified rules.
The enclosing ``LEDGER`` view additionally rounds DR/CR to whole units; callers
comparing that view must apply ``oracle_round(total)`` explicitly after headers.
The V2 Python application's line discounts are absolute amounts, not percentages.
Every line therefore requires an explicit ``discount_kind`` and every invoice
requires an explicit ``rounding`` policy. Never infer either from a numeric value.

This module validates conservative *new entries*, not arbitrary historical data.
Inputs have at most 18 integer places and 6 fractional places; these are documented
application limits, not inferred Oracle column limits. All arithmetic is Decimal,
independent of the process-wide Decimal context, and no float input is accepted.
Only the aggregate line subtotal is rounded. Header adjustments retain their
entered precision. Payments are separate from invoice and legacy ledger totals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow
from decimal import ROUND_HALF_UP, localcontext
from enum import Enum
import re
from typing import Iterable, TypeAlias


DecimalInput: TypeAlias = Decimal | str | int
MAX_INTEGER_PLACES = 18
MAX_FRACTIONAL_PLACES = 6
MAX_INVOICE_LINES = 10_000
_MAX_TEXT_LENGTH = 128
_DECIMAL_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
# Sufficient for the documented bounds, including percentage multiplication and
# the sum of MAX_INVOICE_LINES lines. Explicit context also isolates caller traps.
_CONTEXT = Context(
    prec=96,
    rounding=ROUND_HALF_UP,
    Emin=-999999,
    Emax=999999,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


class CalculationError(ValueError):
    """A value is invalid or outside supported new-entry calculation bounds."""


class DiscountKind(str, Enum):
    PERCENT = "percent"
    AMOUNT = "amount"


class RoundingPolicy(str, Enum):
    LEGACY_WHOLE = "legacy_whole"
    CENTS = "cents"

    @property
    def places(self) -> int:
        return 0 if self is RoundingPolicy.LEGACY_WHOLE else 2


def as_decimal(value: DecimalInput, *, name: str = "value") -> Decimal:
    """Parse a finite decimal without accepting binary floats or booleans.

    This general helper permits up to 64 integer and 24 fractional places so
    calculated results can be serialized. Entry models enforce the tighter
    18/6 limits. Scientific notation is accepted within these bounds; grouping
    separators, underscores, Unicode numerals and non-numeric tokens are not.
    Redundant trailing zeros and signed zero are normalized without rounding.
    """
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise CalculationError(f"{name} must be a Decimal, decimal string, or integer; floats are not accepted")
    if isinstance(value, str):
        value = value.strip()
        if len(value) > _MAX_TEXT_LENGTH or not _DECIMAL_PATTERN.fullmatch(value):
            raise CalculationError(f"{name} must be a valid decimal number")
    elif isinstance(value, int) and value.bit_length() > 426:
        raise CalculationError(f"{name} exceeds the supported decimal precision")
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise CalculationError(f"{name} must be a valid decimal number") from exc
    if not number.is_finite():
        raise CalculationError(f"{name} must be finite")
    if number.is_zero():
        return _ZERO
    sign, digits, exponent = number.as_tuple()
    if len(digits) > _MAX_TEXT_LENGTH:
        raise CalculationError(f"{name} exceeds the supported decimal precision")
    # Finite Decimal exponents are integers. Avoid normalize(), which consults
    # the ambient context and can silently round caller-supplied values.
    assert isinstance(exponent, int)
    while digits[-1] == 0:
        digits = digits[:-1]
        exponent += 1
    number = Decimal((sign, digits, exponent))
    _check_places(number, name=name, integer_places=64, fractional_places=24)
    return number


def _check_places(
    number: Decimal, *, name: str, integer_places: int, fractional_places: int
) -> None:
    _, digits, exponent = number.as_tuple()
    assert isinstance(exponent, int)
    if max(len(digits) + exponent, 0) > integer_places:
        raise CalculationError(f"{name} must have at most {integer_places} integer places")
    if max(-exponent, 0) > fractional_places:
        raise CalculationError(f"{name} must have at most {fractional_places} fractional places")


def _entry_decimal(value: DecimalInput, *, name: str, positive: bool = False) -> Decimal:
    number = as_decimal(value, name=name)
    _check_places(
        number,
        name=name,
        integer_places=MAX_INTEGER_PLACES,
        fractional_places=MAX_FRACTIONAL_PLACES,
    )
    if number < 0 or (positive and number == 0):
        requirement = "greater than zero" if positive else "zero or greater"
        raise CalculationError(f"{name} must be {requirement}")
    return number


def canonical_decimal(value: DecimalInput) -> str:
    """Return plain decimal text suitable for SQLite TEXT or JSON serialization.

    Never convert the returned text to float for persistence. No scientific
    notation, grouping separators, insignificant trailing zeros or signed zero
    are emitted. For display formatting, use a separate presentation function.
    """
    number = as_decimal(value)
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def oracle_round(value: DecimalInput, places: int = 0) -> Decimal:
    """Round ties away from zero, matching Oracle ROUND on decimal NUMBER.

    Negative places round to tens/hundreds etc. ``places`` must be an integer
    between -18 and 18. This helper permits negative values for report/import
    verification even though new invoice entry models reject negative amounts.
    """
    if isinstance(places, bool) or not isinstance(places, int) or not -18 <= places <= 18:
        raise CalculationError("rounding places must be an integer between -18 and 18")
    number = as_decimal(value)
    with localcontext(_CONTEXT):
        return number.quantize(Decimal((0, (1,), -places)), rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class LineAmounts:
    """Unrounded amounts for one invoice line, in original input order."""

    gross: Decimal
    discount: Decimal
    net: Decimal


@dataclass(frozen=True, slots=True)
class InvoiceLine:
    """One new-entry line; ``discount_kind`` is a required keyword argument.

    Quantity must be positive. Rate may be zero for free goods. Percentage
    discounts must be in [0, 100]; absolute discounts cannot exceed line gross.
    No line amount is rounded before the invoice subtotal is calculated.
    """

    quantity: DecimalInput
    rate: DecimalInput
    discount: DecimalInput = _ZERO
    discount_kind: DiscountKind = field(kw_only=True)

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", _entry_decimal(self.quantity, name="quantity", positive=True))
        object.__setattr__(self, "rate", _entry_decimal(self.rate, name="rate"))
        object.__setattr__(self, "discount", _entry_decimal(self.discount, name="line discount"))
        try:
            kind = DiscountKind(self.discount_kind)
        except (TypeError, ValueError) as exc:
            raise CalculationError("line discount kind must be 'percent' or 'amount'") from exc
        object.__setattr__(self, "discount_kind", kind)
        if kind is DiscountKind.PERCENT and self.discount > _HUNDRED:
            raise CalculationError("percentage discount cannot exceed 100")
        if kind is DiscountKind.AMOUNT:
            with localcontext(_CONTEXT):
                if self.discount > self.quantity * self.rate:
                    raise CalculationError("absolute line discount cannot exceed line gross")

    def calculate(self) -> LineAmounts:
        with localcontext(_CONTEXT):
            gross = self.quantity * self.rate
            discount = (
                gross * self.discount / _HUNDRED
                if self.discount_kind is DiscountKind.PERCENT
                else self.discount
            )
            return LineAmounts(gross=gross, discount=discount, net=gross - discount)


@dataclass(frozen=True, slots=True)
class InvoiceTotals:
    line_amounts: tuple[LineAmounts, ...]
    rounding: RoundingPolicy
    raw_subtotal: Decimal
    subtotal: Decimal
    rounding_adjustment: Decimal
    discount: Decimal
    breakage: Decimal
    freight: Decimal
    total: Decimal


def calculate_invoice(
    lines: Iterable[InvoiceLine],
    *,
    rounding: RoundingPolicy,
    discount: DecimalInput = _ZERO,
    breakage: DecimalInput = _ZERO,
    freight: DecimalInput = _ZERO,
) -> InvoiceTotals:
    """Calculate a normal invoice; the rounding policy must be chosen explicitly.

    ``LEGACY_WHOLE`` rounds the aggregate line subtotal to whole currency units;
    ``CENTS`` rounds that same subtotal to two places. Header adjustments are
    then applied without additional rounding. Header discount plus breakage may
    not exceed the rounded subtotal, even if freight would offset the excess.
    At least one and at most MAX_INVOICE_LINES validated lines are required.
    Received cash is intentionally absent: it does not change invoice total.
    """
    try:
        policy = RoundingPolicy(rounding)
    except (TypeError, ValueError) as exc:
        raise CalculationError("rounding must be 'legacy_whole' or 'cents'") from exc
    header_discount = _entry_decimal(discount, name="header discount")
    header_breakage = _entry_decimal(breakage, name="breakage")
    header_freight = _entry_decimal(freight, name="freight")
    amounts: list[LineAmounts] = []
    try:
        iterator = iter(lines)
    except TypeError as exc:
        raise CalculationError("invoice lines must be an iterable of InvoiceLine values") from exc
    for index, line in enumerate(iterator):
        if index >= MAX_INVOICE_LINES:
            raise CalculationError(f"an invoice may contain at most {MAX_INVOICE_LINES} lines")
        if not isinstance(line, InvoiceLine):
            raise CalculationError(f"line {index + 1} must be an InvoiceLine")
        amounts.append(line.calculate())
    if not amounts:
        raise CalculationError("an invoice must contain at least one line")
    with localcontext(_CONTEXT):
        raw_subtotal = sum((amount.net for amount in amounts), _ZERO)
        subtotal = oracle_round(raw_subtotal, policy.places)
        if header_discount + header_breakage > subtotal:
            raise CalculationError("header discount plus breakage cannot exceed the rounded subtotal")
        total = subtotal - header_discount - header_breakage + header_freight
        return InvoiceTotals(
            line_amounts=tuple(amounts),
            rounding=policy,
            raw_subtotal=raw_subtotal,
            subtotal=subtotal,
            rounding_adjustment=subtotal - raw_subtotal,
            discount=header_discount,
            breakage=header_breakage,
            freight=header_freight,
            total=total,
        )


@dataclass(frozen=True, slots=True)
class Settlement:
    """Separate payment allocation; credit records overpayment explicitly."""

    invoice_total: Decimal
    received: Decimal
    applied: Decimal
    due: Decimal
    credit: Decimal
    balance: Decimal


def calculate_settlement(invoice_total: DecimalInput, received: DecimalInput) -> Settlement:
    """Calculate due/credit without changing the invoice's accounting amount.

    ``invoice_total`` can be a generated total beyond entry precision. Received
    is a nonnegative new-entry amount; excess received becomes customer credit,
    not a negative amount due or an unexplained reduction in invoice revenue.
    Persisting an actual payment and account credit is the service's duty.
    """
    total = as_decimal(invoice_total, name="invoice total")
    if total < 0:
        raise CalculationError("invoice total must be zero or greater")
    payment = _entry_decimal(received, name="received")
    with localcontext(_CONTEXT):
        balance = total - payment
        return Settlement(
            invoice_total=total,
            received=payment,
            applied=min(total, payment),
            due=max(balance, _ZERO),
            credit=max(-balance, _ZERO),
            balance=balance,
        )


def convert_quantity(*, large: DecimalInput, small: DecimalInput, factor: DecimalInput) -> Decimal:
    """Return ``large * factor + small`` in the smallest stock unit.

    Quantities are nonnegative, factor is positive, and fractional units are
    allowed. Small quantity need not be below factor: e.g. 1 carton + 14 pieces
    with factor 12 correctly becomes 26 pieces. The result must fit the new-entry
    quantity bounds; values are never truncated or silently rounded.
    """
    large_quantity = _entry_decimal(large, name="large quantity")
    small_quantity = _entry_decimal(small, name="small quantity")
    conversion = _entry_decimal(factor, name="unit factor", positive=True)
    with localcontext(_CONTEXT):
        return _entry_decimal(large_quantity * conversion + small_quantity, name="converted quantity")
