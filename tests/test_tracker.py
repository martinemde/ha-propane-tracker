"""Tests for persistence ordering and Recorder projection behavior."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from homeassistant.exceptions import ConfigEntryError, ServiceValidationError

from custom_components.propane_tracker.const import (
    COST_STATISTIC_ID,
    LITERS_PER_US_GALLON,
    USAGE_STATISTIC_ID,
)
from custom_components.propane_tracker.ledger import Delivery, Ledger
from custom_components.propane_tracker.tracker import PropaneTracker


def _tracker() -> PropaneTracker:
    hass = MagicMock()
    hass.bus = MagicMock()
    entry = MagicMock()
    tracker = PropaneTracker(hass, entry)
    tracker._store = MagicMock()
    tracker._store.async_save = AsyncMock()
    tracker._store.async_load = AsyncMock(return_value=None)
    return tracker


def _delivery(day: int, gallons: str, price: str) -> Delivery:
    return Delivery(
        delivery_date=date(2026, 1, day),
        gallons=Decimal(gallons),
        price_per_gallon=Decimal(price),
    )


async def test_record_saves_ledger_before_syncing_statistics() -> None:
    """A crash cannot create a statistic that has no durable ledger row."""
    tracker = _tracker()
    events: list[str] = []
    tracker._store.async_save.side_effect = lambda data: events.append("save")

    async def sync() -> None:
        events.append("sync")

    tracker.async_sync_statistics = sync

    await tracker.async_record_delivery(
        delivery_date=date(2026, 1, 5),
        gallons=100,
        price_per_gallon=3,
    )

    assert events == ["save", "sync"]
    assert tracker.ledger.total_cost == Decimal("300.00")


async def test_historical_bill_can_be_added_after_a_newer_bill() -> None:
    """The ledger sorts history and a full rebuild fixes all cumulative sums."""
    tracker = _tracker()
    tracker._ledger = Ledger.empty(date(2026, 2, 1)).with_delivery(_delivery(20, "80", "4"))
    tracker.async_sync_statistics = AsyncMock()

    await tracker.async_record_delivery(
        delivery_date=date(2026, 1, 5),
        gallons=100,
        price_per_gallon=3,
    )

    assert [item.delivery_date.day for item in tracker.ledger.deliveries] == [5, 20]
    tracker.async_sync_statistics.assert_awaited_once()


async def test_concurrent_draft_updates_do_not_clobber_each_other() -> None:
    """Each serialized input update starts with the newest draft snapshot."""
    tracker = _tracker()

    await tracker.async_set_draft_gallons(125.5)
    await tracker.async_set_draft_price(3.25)

    assert tracker.ledger.draft.gallons == Decimal("125.5")
    assert tracker.ledger.draft.price_per_gallon == Decimal("3.25")


async def test_committed_delivery_survives_statistics_failure() -> None:
    """A Recorder outage does not turn a successful durable write into a retry trap."""
    tracker = _tracker()
    tracker.async_sync_statistics = AsyncMock(side_effect=RuntimeError("recorder unavailable"))

    delivery = await tracker.async_record_delivery(
        delivery_date=date(2026, 1, 5),
        gallons=100,
        price_per_gallon=3,
    )

    assert delivery in tracker.ledger.deliveries
    tracker._store.async_save.assert_awaited_once()


async def test_duplicate_and_future_deliveries_are_rejected() -> None:
    """Double taps and impossible ticket dates fail before storage changes."""
    tracker = _tracker()
    tracker._ledger = Ledger.empty(date(2026, 2, 1)).with_delivery(_delivery(5, "100", "3"))

    with pytest.raises(ServiceValidationError, match="double tap"):
        await tracker.async_record_delivery(
            delivery_date=date(2026, 1, 5),
            gallons=100,
            price_per_gallon=3,
        )

    with pytest.raises(ServiceValidationError, match="future"):
        await tracker.async_record_delivery(
            delivery_date=date.today() + timedelta(days=1),
            gallons=100,
            price_per_gallon=3,
        )

    tracker._store.async_save.assert_not_awaited()


async def test_initialize_refuses_to_overwrite_a_damaged_ledger() -> None:
    """Corrupt durable data blocks setup instead of being replaced with empty data."""
    tracker = _tracker()
    tracker._store.async_load.return_value = {
        "deliveries": [
            {
                "delivery_date": "2026-01-05",
                "gallons": "100",
                "price_per_gallon": "3",
                "cost": "1",
            }
        ]
    }

    with pytest.raises(ConfigEntryError, match="refusing to overwrite"):
        await tracker.async_initialize()


async def test_sync_rebuilds_cumulative_usage_and_cost_rows() -> None:
    """Every row is derivable from ticket data after Recorder loss or backfill."""
    tracker = _tracker()
    tracker._ledger = (
        Ledger.empty(date(2026, 2, 1))
        .with_delivery(_delivery(5, "100", "3"))
        .with_delivery(_delivery(20, "50", "4"))
    )
    utc = ZoneInfo("UTC")

    with (
        patch(
            "custom_components.propane_tracker.tracker.dt_util.start_of_local_day",
            side_effect=lambda value: datetime(value.year, value.month, value.day, tzinfo=utc),
        ),
        patch(
            "custom_components.propane_tracker.tracker.async_add_external_statistics"
        ) as add_statistics,
    ):
        await tracker.async_sync_statistics()

    assert add_statistics.call_count == 2
    usage_call, cost_call = add_statistics.call_args_list
    assert usage_call.args[1]["statistic_id"] == USAGE_STATISTIC_ID
    assert cost_call.args[1]["statistic_id"] == COST_STATISTIC_ID

    usage_rows = usage_call.args[2]
    cost_rows = cost_call.args[2]
    assert usage_rows[0]["state"] == pytest.approx(100 * LITERS_PER_US_GALLON)
    assert usage_rows[1]["sum"] == pytest.approx(150 * LITERS_PER_US_GALLON)
    assert cost_rows[0]["state"] == 300
    assert cost_rows[1]["sum"] == 500
    assert [row["start"].day for row in usage_rows] == [5, 20]
