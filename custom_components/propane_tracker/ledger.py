"""Pure data model for the durable propane delivery ledger."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Self

MONEY_QUANTUM = Decimal("0.01")


class LedgerError(ValueError):
    """Base error for invalid ledger data."""


class DuplicateDeliveryError(LedgerError):
    """Raised when a delivery already exists for a date."""


def _decimal(value: Any, field: str) -> Decimal:
    """Parse a finite decimal value with a useful ledger error."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as err:
        raise LedgerError(f"{field} must be a number") from err
    if not result.is_finite():
        raise LedgerError(f"{field} must be finite")
    return result


def _date(value: Any, field: str = "delivery_date") -> date:
    """Parse an ISO date with a useful ledger error."""
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as err:
        raise LedgerError(f"{field} must use YYYY-MM-DD format") from err


def invoice_cost(gallons: Decimal, price_per_gallon: Decimal) -> Decimal:
    """Calculate commodity cost using ordinary invoice rounding."""
    return (gallons * price_per_gallon).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class Delivery:
    """One immutable propane delivery ticket."""

    delivery_date: date
    gallons: Decimal
    price_per_gallon: Decimal

    def __post_init__(self) -> None:
        if self.gallons <= 0:
            raise LedgerError("gallons must be greater than zero")
        if self.price_per_gallon <= 0:
            raise LedgerError("price_per_gallon must be greater than zero")

    @property
    def cost(self) -> Decimal:
        """Return the rounded commodity cost for this ticket."""
        return invoice_cost(self.gallons, self.price_per_gallon)

    def as_dict(self) -> dict[str, str]:
        """Serialize without losing entered decimal precision."""
        return {
            "delivery_date": self.delivery_date.isoformat(),
            "gallons": str(self.gallons),
            "price_per_gallon": str(self.price_per_gallon),
            "cost": str(self.cost),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Load and integrity-check one stored delivery."""
        delivery = cls(
            delivery_date=_date(data.get("delivery_date")),
            gallons=_decimal(data.get("gallons"), "gallons"),
            price_per_gallon=_decimal(data.get("price_per_gallon"), "price_per_gallon"),
        )
        stored_cost = _decimal(data.get("cost"), "cost")
        if stored_cost != delivery.cost:
            raise LedgerError(
                f"stored cost {stored_cost} does not match calculated cost "
                f"{delivery.cost} for {delivery.delivery_date}"
            )
        return delivery


@dataclass(frozen=True, slots=True)
class Draft:
    """The values currently shown in Home Assistant's entry controls."""

    delivery_date: date
    gallons: Decimal = Decimal("0")
    price_per_gallon: Decimal = Decimal("0")

    @property
    def estimated_cost(self) -> Decimal:
        """Return a display estimate without requiring a valid delivery yet."""
        return invoice_cost(self.gallons, self.price_per_gallon)

    def as_dict(self) -> dict[str, str]:
        """Serialize the current draft."""
        return {
            "delivery_date": self.delivery_date.isoformat(),
            "gallons": str(self.gallons),
            "price_per_gallon": str(self.price_per_gallon),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, default_date: date) -> Self:
        """Load a draft, accepting a missing draft from the first version."""
        return cls(
            delivery_date=_date(data.get("delivery_date", default_date)),
            gallons=_decimal(data.get("gallons", "0"), "draft gallons"),
            price_per_gallon=_decimal(data.get("price_per_gallon", "0"), "draft price_per_gallon"),
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
