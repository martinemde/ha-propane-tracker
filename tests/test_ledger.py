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


def _delivery(day: int, gallons: str, cost: str) -> Delivery:
    return Delivery(
        delivery_date=date(2026, 1, day),
        gallons=Decimal(gallons),
        cost=Decimal(cost),
    )


def test_price_per_gallon_is_derived_from_the_invoice_total() -> None:
    """The bill states a total; the per-gallon price is a display detail."""
    delivery = _delivery(3, "100.5", "326.52")
    assert delivery.price_per_gallon == Decimal("3.249")


def test_historical_deliveries_are_sorted_and_totaled() -> None:
    """Older bills may be entered after newer ones without corrupting totals."""
    ledger = Ledger.empty(date(2026, 2, 1))
    ledger = ledger.with_delivery(_delivery(20, "80.0", "280.00"))
    ledger = ledger.with_delivery(_delivery(5, "100.0", "300.00"))

    assert [item.delivery_date.day for item in ledger.deliveries] == [5, 20]
    assert ledger.total_gallons == Decimal("180.0")
    assert ledger.total_cost == Decimal("580.00")
    assert ledger.last_delivery == ledger.deliveries[-1]
    assert ledger.is_newest(date(2026, 1, 21))
    assert not ledger.is_newest(date(2026, 1, 6))


def test_duplicate_date_is_rejected() -> None:
    """A second submission for one day is treated as an accidental double tap."""
    ledger = Ledger.empty(date(2026, 2, 1)).with_delivery(_delivery(5, "100", "300"))
    with pytest.raises(DuplicateDeliveryError, match="2026-01-05"):
        ledger.with_delivery(_delivery(5, "50", "200"))


def test_storage_round_trip_preserves_entered_precision() -> None:
    """String-backed decimals survive storage without binary-float drift."""
    ledger = Ledger.empty(date(2026, 2, 1)).with_delivery(_delivery(5, "143.70", "466.88"))
    restored = Ledger.from_dict(ledger.as_dict(), today=date(2026, 2, 2))

    assert restored == ledger
    assert restored.deliveries[0].as_dict()["gallons"] == "143.70"
    assert restored.deliveries[0].as_dict()["cost"] == "466.88"


def test_version_one_ticket_upgrades_to_an_invoice_total() -> None:
    """A ledger written before the tank existed still loads and totals the same."""
    ledger = Ledger.from_dict(
        {
            "deliveries": [
                {
                    "delivery_date": "2026-01-05",
                    "gallons": "100.5",
                    "price_per_gallon": "3.249",
                    "cost": "326.52",
                }
            ],
            "draft": {"delivery_date": "2026-01-05", "gallons": "10", "price_per_gallon": "3"},
        },
        today=date(2026, 2, 2),
    )

    assert ledger.deliveries[0].cost == Decimal("326.52")
    assert ledger.draft.cost == Decimal("30.00")


def test_tampered_version_one_cost_is_rejected() -> None:
    """A damaged ledger cannot silently change an old bill's spend."""
    raw = {
        "deliveries": [
            {
                "delivery_date": "2026-01-05",
                "gallons": "100",
                "price_per_gallon": "3",
                "cost": "1.00",
            }
        ]
    }

    with pytest.raises(LedgerError, match="does not match"):
        Ledger.from_dict(raw, today=date(2026, 2, 2))


@pytest.mark.parametrize(
    ("gallons", "cost", "message"),
    [("0", "300", "gallons"), ("100", "0", "cost")],
)
def test_delivery_values_must_be_positive(gallons: str, cost: str, message: str) -> None:
    """An empty dashboard draft is never accepted as a real ticket."""
    with pytest.raises(LedgerError, match=message):
        _delivery(5, gallons, cost)
