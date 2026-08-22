"""Tests for the authoritative pure-Python delivery ledger."""

from datetime import date
from decimal import Decimal

import pytest

from custom_components.propane_tracker.ledger import (
    Delivery,
    DuplicateDeliveryError,
    Ledger,
    LedgerError,
)


def _delivery(day: int, gallons: str, price: str) -> Delivery:
    return Delivery(
        delivery_date=date(2026, 1, day),
        gallons=Decimal(gallons),
        price_per_gallon=Decimal(price),
    )


def test_invoice_cost_uses_half_up_cent_rounding() -> None:
    """Ticket multiplication follows ordinary invoice rounding, not float math."""
    delivery = _delivery(3, "100.5", "3.249")
    assert delivery.cost == Decimal("326.52")


def test_historical_deliveries_are_sorted_and_totaled() -> None:
    """Older bills may be entered after newer ones without corrupting totals."""
    ledger = Ledger.empty(date(2026, 2, 1))
    ledger = ledger.with_delivery(_delivery(20, "80.0", "3.50"))
    ledger = ledger.with_delivery(_delivery(5, "100.0", "3.00"))

    assert [item.delivery_date.day for item in ledger.deliveries] == [5, 20]
    assert ledger.total_gallons == Decimal("180.0")
    assert ledger.total_cost == Decimal("580.00")
    assert ledger.last_delivery == ledger.deliveries[-1]


def test_duplicate_date_is_rejected() -> None:
    """A second submission for one day is treated as an accidental double tap."""
    ledger = Ledger.empty(date(2026, 2, 1)).with_delivery(_delivery(5, "100", "3"))
    with pytest.raises(DuplicateDeliveryError, match="2026-01-05"):
        ledger.with_delivery(_delivery(5, "50", "4"))


def test_storage_round_trip_preserves_entered_precision() -> None:
    """String-backed decimals survive storage without binary-float drift."""
    ledger = Ledger.empty(date(2026, 2, 1)).with_delivery(_delivery(5, "143.70", "3.249"))
    restored = Ledger.from_dict(ledger.as_dict(), today=date(2026, 2, 2))

    assert restored == ledger
    assert restored.deliveries[0].as_dict()["gallons"] == "143.70"
    assert restored.deliveries[0].as_dict()["price_per_gallon"] == "3.249"


def test_tampered_stored_cost_is_rejected() -> None:
    """A damaged ledger cannot silently change an old bill's spend."""
    raw = Ledger.empty(date(2026, 2, 1)).with_delivery(_delivery(5, "100", "3")).as_dict()
    raw["deliveries"][0]["cost"] = "1.00"

    with pytest.raises(LedgerError, match="does not match"):
        Ledger.from_dict(raw, today=date(2026, 2, 2))


@pytest.mark.parametrize(
    ("gallons", "price", "message"),
    [("0", "3", "gallons"), ("100", "0", "price_per_gallon")],
)
def test_delivery_values_must_be_positive(gallons: str, price: str, message: str) -> None:
    """An empty dashboard draft is never accepted as a real ticket."""
    with pytest.raises(LedgerError, match=message):
        _delivery(5, gallons, price)
