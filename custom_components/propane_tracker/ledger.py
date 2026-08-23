"""Pure data model for the durable propane delivery ledger."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Self

MONEY_QUANTUM = Decimal("0.01")
PRICE_QUANTUM = Decimal("0.001")


class LedgerError(ValueError):
    """Base error for invalid ledger data."""


class DuplicateDeliveryError(LedgerError):
    """Raised when a delivery already exists for a date."""


def parse_decimal(value: Any, field: str) -> Decimal:
    """Parse a finite decimal value with a useful ledger error."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as err:
        raise LedgerError(f"{field} must be a number") from err
    if not result.is_finite():
        raise LedgerError(f"{field} must be finite")
    return result


def parse_date(value: Any, field: str = "delivery_date") -> date:
    """Parse an ISO date with a useful ledger error."""
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as err:
        raise LedgerError(f"{field} must use YYYY-MM-DD format") from err


def _money(value: Decimal) -> Decimal:
    """Round a currency amount the way an invoice does."""
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _delivery_cost(data: dict[str, Any]) -> Decimal:
    """Read the invoice total, upgrading a version 1 per-gallon ticket.

    Version 1 derived cost from a per-gallon price and stored both, so a
    payload carrying both is still cross-checked before it is trusted.
    """
    if "price_per_gallon" not in data:
        return parse_decimal(data.get("cost"), "cost")
    gallons = parse_decimal(data.get("gallons"), "gallons")
    price = parse_decimal(data.get("price_per_gallon"), "price_per_gallon")
    derived = _money(gallons * price)
    if "cost" not in data:
        return derived
    stored = parse_decimal(data["cost"], "cost")
    if stored != derived:
        raise LedgerError(
            f"stored cost {stored} does not match calculated cost {derived} "
            f"for {data.get('delivery_date')}"
        )
    return stored


@dataclass(frozen=True, slots=True)
class Delivery:
    """One immutable propane delivery ticket."""

    delivery_date: date
    gallons: Decimal
    cost: Decimal

    def __post_init__(self) -> None:
        if self.gallons <= 0:
            raise LedgerError("gallons must be greater than zero")
        if self.cost <= 0:
            raise LedgerError("cost must be greater than zero")

    @property
    def price_per_gallon(self) -> Decimal:
        """Return the effective per-gallon price this ticket worked out to."""
        return (self.cost / self.gallons).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)

    def as_dict(self) -> dict[str, str]:
        """Serialize without losing entered decimal precision."""
        return {
            "delivery_date": self.delivery_date.isoformat(),
            "gallons": str(self.gallons),
            "cost": str(self.cost),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Load one stored delivery in either storage version."""
        return cls(
            delivery_date=parse_date(data.get("delivery_date")),
            gallons=parse_decimal(data.get("gallons"), "gallons"),
            cost=_delivery_cost(data),
        )


@dataclass(frozen=True, slots=True)
class Draft:
    """The values currently shown in Home Assistant's entry controls."""

    delivery_date: date
    gallons: Decimal = Decimal("0")
    cost: Decimal = Decimal("0")

    @property
    def price_per_gallon(self) -> Decimal:
        """Return the per-gallon price implied by the draft, or zero."""
        if self.gallons <= 0:
            return Decimal("0")
        return (self.cost / self.gallons).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)

    def as_dict(self) -> dict[str, str]:
        """Serialize the current draft."""
        return {
            "delivery_date": self.delivery_date.isoformat(),
            "gallons": str(self.gallons),
            "cost": str(self.cost),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, default_date: date) -> Self:
        """Load a draft, accepting a missing or version 1 draft."""
        gallons = parse_decimal(data.get("gallons", "0"), "draft gallons")
        if "cost" in data:
            cost = parse_decimal(data["cost"], "draft cost")
        else:
            price = parse_decimal(data.get("price_per_gallon", "0"), "draft price_per_gallon")
            cost = _money(gallons * price)
        return cls(
            delivery_date=parse_date(data.get("delivery_date", default_date)),
            gallons=gallons,
            cost=cost,
        )


@dataclass(frozen=True, slots=True)
class Ledger:
    """Authoritative, versioned delivery history and current draft."""

    deliveries: tuple[Delivery, ...]
    draft: Draft

    @classmethod
    def empty(cls, today: date) -> Self:
        """Create a new empty ledger."""
        return cls(deliveries=(), draft=Draft(delivery_date=today))

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, today: date) -> Self:
        """Load the storage payload and reject ambiguous/corrupt histories."""
        deliveries = tuple(
            sorted(
                (Delivery.from_dict(item) for item in data.get("deliveries", [])),
                key=lambda delivery: delivery.delivery_date,
            )
        )
        dates = [delivery.delivery_date for delivery in deliveries]
        if len(dates) != len(set(dates)):
            raise LedgerError("the ledger contains more than one delivery on a date")
        draft = Draft.from_dict(data.get("draft", {}), default_date=today)
        return cls(deliveries=deliveries, draft=draft)

    def as_dict(self) -> dict[str, Any]:
        """Serialize the complete ledger for Home Assistant Store."""
        return {
            "deliveries": [delivery.as_dict() for delivery in self.deliveries],
            "draft": self.draft.as_dict(),
        }

    def with_delivery(self, delivery: Delivery) -> Self:
        """Add a uniquely dated delivery and keep history chronological."""
        if any(existing.delivery_date == delivery.delivery_date for existing in self.deliveries):
            raise DuplicateDeliveryError(
                f"a delivery is already recorded for {delivery.delivery_date}"
            )
        deliveries = tuple(
            sorted(
                (*self.deliveries, delivery),
                key=lambda item: item.delivery_date,
            )
        )
        return replace(self, deliveries=deliveries)

    def with_draft(self, draft: Draft) -> Self:
        """Replace the current entry-control values."""
        return replace(self, draft=draft)

    @property
    def total_gallons(self) -> Decimal:
        """Return lifetime delivered gallons."""
        return sum(
            (delivery.gallons for delivery in self.deliveries),
            start=Decimal("0"),
        )

    @property
    def total_cost(self) -> Decimal:
        """Return lifetime commodity spend."""
        return sum(
            (delivery.cost for delivery in self.deliveries),
            start=Decimal("0"),
        )

    @property
    def last_delivery(self) -> Delivery | None:
        """Return the newest delivery, if any."""
        return self.deliveries[-1] if self.deliveries else None

    def is_newest(self, delivery_date: date) -> bool:
        """Report whether a date is at or after every recorded delivery."""
        last = self.last_delivery
        return last is None or delivery_date >= last.delivery_date
