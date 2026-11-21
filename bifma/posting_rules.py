"""Corrected NEW-transaction posting primitives; never reinterpret history.

Callers must choose both subtotal rounding and final posting precision (whole
units or cents). Every trade has ONE party total. All header adjustments and
rounding are allocated once across explicitly supplied product posting accounts
in proportion to their unrounded, discounted line values. Largest remainders
distribute indivisible units; account-code order breaks ties deterministically.
This fixes missing counterpart discounts and repeated per-group adjustments.

Payments are separate balanced postings. Overpayment remains on the explicit
party account as credit/advance. No account code, tax, labour rule, COGS value or
stock valuation is invented. These unsupported rules require a separately
verified service contract before use; omitting them here is not support for them.

This module is pure calculation, not a lifecycle or authorization boundary.
Database services must verify active account/product/store references, balances,
period permissions, stock availability, and atomic/exactly-once persistence.
Services must also verify every delivery link against a posted source movement
and reserve it atomically. Only complete, matching delivery links are supported;
partial allocation across documents requires additional service rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Context, Decimal, localcontext
from enum import Enum
from fractions import Fraction
from typing import Iterable

from bifma.calculations import (
    CalculationError, DecimalInput, DiscountKind, InvoiceLine, InvoiceTotals,
    MAX_INVOICE_LINES, RoundingPolicy, Settlement, as_decimal, calculate_invoice,
    calculate_settlement, oracle_round,
)


_ZERO = Decimal(0)
_CONTEXT = Context(prec=128)


class PostingError(CalculationError):
    """A posting policy, account allocation or linkage cannot be reconciled."""


class TradeKind(str, Enum):
    SALE = "sale"
    PURCHASE = "purchase"
    SALE_RETURN = "sale_return"
    PURCHASE_RETURN = "purchase_return"


class SettlementDirection(str, Enum):
    RECEIPT = "receipt"
    PAYMENT = "payment"


class StockKind(str, Enum):
    SALE = "sale"
    PURCHASE = "purchase"
    SALE_RETURN = "sale_return"
    PURCHASE_RETURN = "purchase_return"
    BREAKAGE_RECEIVE = "breakage_receive"
    BREAKAGE_DISPATCH = "breakage_dispatch"
    DELIVERY_DISPATCH = "delivery_dispatch"


class StockSource(str, Enum):
    DIRECT = "direct"
    POSTED_DELIVERY = "posted_delivery"


def _code(value, name):
    if not isinstance(value, str) or not value or value != value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise PostingError(f"{name} must be a nonblank exact identifier without surrounding whitespace or controls")
    return value


def _places(value):
    if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 2):
        raise PostingError("posting_places must explicitly be 0 (whole units) or 2 (cents)")
    return value


def _enum(value, enum_type, name):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as error:
        raise PostingError(f"unsupported {name}") from error


def _amount(value, name):
    number = as_decimal(value, name=name)
    if number < 0:
        raise PostingError(f"{name} must be nonnegative")
    return number


def _units(amount: Decimal, places: int) -> int:
    scaled = Fraction(amount) * 10**places
    if scaled.denominator != 1:
        raise PostingError("amount cannot be represented exactly at the selected posting precision")
    return scaled.numerator


def _from_units(units: int, places: int) -> Decimal:
    if units == 0:
        return _ZERO
    return Decimal((0 if units >= 0 else 1, tuple(map(int, str(abs(units)))), -places))


def _bounded_items(items, item_type):
    try:
        iterator = iter(items)
    except TypeError as error:
        raise PostingError("posting items must be iterable") from error
    result = []
    for item in iterator:
        if len(result) >= MAX_INVOICE_LINES:
            raise PostingError(f"a document may contain at most {MAX_INVOICE_LINES} posting items")
        if not isinstance(item, item_type):
            raise PostingError(f"every posting item must be a {item_type.__name__}")
        result.append(item)
    if not result:
        raise PostingError("a document requires at least one posting item")
    return result


@dataclass(frozen=True, slots=True)
class JournalEntry:
    account_code: str
    debit: DecimalInput = _ZERO
    credit: DecimalInput = _ZERO

    def __post_init__(self):
        object.__setattr__(self, "account_code", _code(self.account_code, "account_code"))
        object.__setattr__(self, "debit", _amount(self.debit, "debit"))
        object.__setattr__(self, "credit", _amount(self.credit, "credit"))
        if (self.debit > 0) == (self.credit > 0):
            raise PostingError("each journal entry must have exactly one positive side")


def validate_balanced(entries: Iterable[JournalEntry], *, posting_places: int,
                      expected_amount: DecimalInput | None = None) -> Decimal:
    """Validate precision, positive single-sided rows and exact reconciliation.

    Empty entries are permitted for a zero-value document only. A supplied
    expected_amount must equal each side's aggregate, not merely the difference.
    """
    places = _places(posting_places)
    debit_units = credit_units = 0
    for entry in entries:
        if not isinstance(entry, JournalEntry):
            raise PostingError("journal entries must be validated JournalEntry values")
        debit_units += _units(entry.debit, places)
        credit_units += _units(entry.credit, places)
    if debit_units != credit_units:
        raise PostingError("debit and credit totals do not reconcile")
    if expected_amount is not None:
        expected_units = _units(_amount(expected_amount, "expected amount"), places)
        if debit_units != expected_units:
            raise PostingError("posting amount does not reconcile with the document total")
    return _from_units(debit_units, places)


@dataclass(frozen=True, slots=True)
class TradePostingItem:
    line: InvoiceLine
    account_code: str

    def __post_init__(self):
        if not isinstance(self.line, InvoiceLine):
            raise PostingError("trade posting items require a validated InvoiceLine")
        object.__setattr__(self, "account_code", _code(self.account_code, "posting account"))


@dataclass(frozen=True, slots=True)
class AccountAllocation:
    account_code: str
    raw_subtotal: Decimal
    amount: Decimal
    adjustment: Decimal


@dataclass(frozen=True, slots=True)
class TradePosting:
    kind: TradeKind
    party_account: str
    posting_places: int
    invoice_totals: InvoiceTotals
    total: Decimal
    final_rounding_adjustment: Decimal
    allocations: tuple[AccountAllocation, ...]
    entries: tuple[JournalEntry, ...]


def _allocate(weights, total, places):
    """Allocate integer posting units with exact rational remainder comparison."""
    total_units = _units(total, places)
    exact_weights = {account: Fraction(weight) for account, weight in weights.items()}
    all_weight = sum(exact_weights.values(), Fraction(0))
    if not all_weight:
        if total_units:
            raise PostingError("a nonzero document total cannot be allocated among zero-value account groups")
        return {account: 0 for account in sorted(weights)}
    allocated, remainders = {}, []
    for account in sorted(exact_weights):
        ideal = total_units * exact_weights[account] / all_weight
        whole = ideal.numerator // ideal.denominator
        allocated[account] = whole
        remainders.append((ideal - whole, account))
    remaining = total_units - sum(allocated.values())
    # Fraction ordering is exact; the account code is an explicit stable tie key.
    for _, account in sorted(remainders, key=lambda row: (-row[0], row[1]))[:remaining]:
        allocated[account] += 1
    if sum(allocated.values()) != total_units or any(value < 0 for value in allocated.values()):
        raise PostingError("account allocation failed exact reconciliation")
    return allocated


def build_trade_posting(
    kind: TradeKind,
    items: Iterable[TradePostingItem],
    *,
    party_account: str,
    subtotal_rounding: RoundingPolicy,
    posting_places: int,
    discount: DecimalInput = _ZERO,
    breakage: DecimalInput = _ZERO,
    freight: DecimalInput = _ZERO,
) -> TradePosting:
    """Construct corrected balanced postings without choosing account codes.

    Normal purchases and sales returns debit supplied product accounts and credit
    the party; sales and purchase returns do the reverse. Positive return amounts
    use the same explicitly entered header adjustments, then reverse direction.
    A cash trade can explicitly select its cash account as party_account.

    The aggregate subtotal follows subtotal_rounding; the final party amount is
    rounded half away from zero to posting_places. Both policies are mandatory.
    Header precision is retained until that explicit final round. Account groups
    receive their proportional share of the single final amount. Zero allocations
    produce no journal row; a zero-value trade can still have stock movements.
    """
    kind = _enum(kind, TradeKind, "trade kind")
    places = _places(posting_places)
    party = _code(party_account, "party_account")
    items = _bounded_items(items, TradePostingItem)
    if any(item.account_code == party for item in items):
        raise PostingError("the party account must differ from every product posting account")
    totals = calculate_invoice(
        [item.line for item in items], rounding=subtotal_rounding,
        discount=discount, breakage=breakage, freight=freight,
    )
    total = oracle_round(totals.total, places)
    if total.is_zero():
        total = _ZERO
    weights = {}
    with localcontext(_CONTEXT):
        for item, amounts in zip(items, totals.line_amounts):
            weights[item.account_code] = weights.get(item.account_code, _ZERO) + amounts.net
        allocated = _allocate(weights, total, places)
        allocations = tuple(
            AccountAllocation(account, weights[account], _from_units(units, places),
                              _from_units(units, places) - weights[account])
            for account, units in sorted(allocated.items())
        )
        party_debit = kind in (TradeKind.SALE, TradeKind.PURCHASE_RETURN)
        entries = []
        if total:
            entries.append(JournalEntry(party, debit=total if party_debit else _ZERO,
                                        credit=_ZERO if party_debit else total))
        for allocation in allocations:
            if allocation.amount:
                entries.append(JournalEntry(
                    allocation.account_code,
                    debit=_ZERO if party_debit else allocation.amount,
                    credit=allocation.amount if party_debit else _ZERO,
                ))
        validate_balanced(entries, posting_places=places, expected_amount=total)
        if sum((allocation.adjustment for allocation in allocations), _ZERO) != total - totals.raw_subtotal:
            raise PostingError("header and rounding adjustments do not reconcile")
        return TradePosting(kind, party, places, totals, total, total - totals.total,
                            allocations, tuple(entries))


@dataclass(frozen=True, slots=True)
class SettlementPosting:
    direction: SettlementDirection
    posting_places: int
    settlement: Settlement
    entries: tuple[JournalEntry, ...]


def build_settlement_posting(
    invoice_total: DecimalInput,
    received: DecimalInput,
    *,
    party_account: str,
    cash_account: str,
    direction: SettlementDirection,
    posting_places: int,
) -> SettlementPosting:
    """Post the entire cash movement separately from the invoice.

    Receipt debits cash and credits party; payment reverses those sides. The
    Settlement result separately exposes applied, due and excess credit/advance.
    Amounts must already fit the selected precision: actual money received is
    never silently rounded. Caller must pass the posted invoice total, not its
    raw unrounded calculation. Linking payment allocation is a service duty.
    """
    direction = _enum(direction, SettlementDirection, "settlement direction")
    places = _places(posting_places)
    party, cash = _code(party_account, "party_account"), _code(cash_account, "cash_account")
    if party == cash:
        raise PostingError("settlement cash and party accounts must differ")
    settlement = calculate_settlement(invoice_total, received)
    _units(settlement.invoice_total, places)
    _units(settlement.received, places)
    entries = ()
    if settlement.received:
        debit_account, credit_account = (cash, party) if direction is SettlementDirection.RECEIPT else (party, cash)
        entries = (JournalEntry(debit_account, debit=settlement.received),
                   JournalEntry(credit_account, credit=settlement.received))
    validate_balanced(entries, posting_places=places, expected_amount=settlement.received)
    return SettlementPosting(direction, places, settlement, entries)


def _quantity(value):
    # Reuse the same positive new-entry quantity bounds without float conversion.
    return InvoiceLine(value, _ZERO, discount_kind=DiscountKind.AMOUNT).quantity


@dataclass(frozen=True, slots=True)
class PostedDeliveryLink:
    """A service-verified, already posted outgoing delivery movement reference."""

    movement_id: str
    product_code: str
    store_id: str
    quantity: DecimalInput

    def __post_init__(self):
        for name in ("movement_id", "product_code", "store_id"):
            object.__setattr__(self, name, _code(getattr(self, name), name))
        object.__setattr__(self, "quantity", _quantity(self.quantity))


@dataclass(frozen=True, slots=True)
class StockItem:
    product_code: str
    store_id: str
    quantity: DecimalInput
    source: StockSource = field(kw_only=True)
    delivery_link: PostedDeliveryLink | None = field(default=None, kw_only=True)

    def __post_init__(self):
        object.__setattr__(self, "product_code", _code(self.product_code, "product_code"))
        object.__setattr__(self, "store_id", _code(self.store_id, "store_id"))
        object.__setattr__(self, "quantity", _quantity(self.quantity))
        source = _enum(self.source, StockSource, "stock source")
        object.__setattr__(self, "source", source)
        if source is StockSource.DIRECT:
            if self.delivery_link is not None:
                raise PostingError("direct stock must not also reference a posted delivery")
        else:
            link = self.delivery_link
            if not isinstance(link, PostedDeliveryLink):
                raise PostingError("posted-delivery stock requires an explicit verified movement link")
            if (link.product_code, link.store_id, link.quantity) != (self.product_code, self.store_id, self.quantity):
                raise PostingError("delivery linkage must match the full product, store and quantity")


@dataclass(frozen=True, slots=True)
class StockMovement:
    product_code: str
    store_id: str
    quantity_delta: Decimal

    @property
    def quantity_in(self):
        return max(self.quantity_delta, _ZERO)

    @property
    def quantity_out(self):
        return max(self.quantity_delta.copy_negate(), _ZERO)


@dataclass(frozen=True, slots=True)
class StockPosting:
    kind: StockKind
    movements: tuple[StockMovement, ...]
    linked_movement_ids: tuple[str, ...]


def build_stock_movements(kind: StockKind, items: Iterable[StockItem]) -> StockPosting:
    """Produce corrected quantity signs and explicit delivery-link decisions.

    Direct sale, purchase return, breakage dispatch and delivery dispatch decrease
    stock. Purchase, sale return and breakage receive increase it. A sale linked
    to an already posted delivery reuses that movement instead of dispatching
    stock again. Other kinds cannot reuse this outgoing delivery mechanism.
    This function cannot prove a persisted link is authentic or unused: the
    service must check and reserve linked_movement_ids in its posting transaction.
    """
    kind = _enum(kind, StockKind, "stock kind")
    items = _bounded_items(items, StockItem)
    incoming = kind in (StockKind.PURCHASE, StockKind.SALE_RETURN, StockKind.BREAKAGE_RECEIVE)
    grouped, links = {}, set()
    with localcontext(_CONTEXT):
        for item in items:
            if item.source is StockSource.POSTED_DELIVERY:
                if kind is not StockKind.SALE:
                    raise PostingError("only a sale can reuse an outgoing posted delivery movement")
                movement_id = item.delivery_link.movement_id
                if movement_id in links:
                    raise PostingError("a posted delivery movement cannot be reused twice in one document")
                links.add(movement_id)
                continue
            key = (item.store_id, item.product_code)
            grouped[key] = grouped.get(key, _ZERO) + item.quantity
        movements = tuple(
            StockMovement(product, store, quantity if incoming else quantity.copy_negate())
            for (store, product), quantity in sorted(grouped.items())
        )
    return StockPosting(kind, movements, tuple(sorted(links)))
