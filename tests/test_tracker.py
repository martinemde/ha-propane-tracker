"""Tests for persistence ordering, the tank, and the Recorder projection."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from homeassistant.exceptions import ConfigEntryError, ServiceValidationError

from custom_components.propane_tracker.const import (
    CONF_FILL_PERCENT,
    CONF_METER_UNITS_PER_GALLON,
    CONF_TANK_CAPACITY,
    COST_STATISTIC_ID,
    DELIVERED_STATISTIC_ID,
    LITERS_PER_US_GALLON,
    UNMETERED_STATISTIC_ID,
)
from custom_components.propane_tracker.ledger import Delivery, Ledger
from custom_components.propane_tracker.tracker import PropaneTracker


def _tracker() -> PropaneTracker:
    hass = MagicMock()
    hass.bus = MagicMock()
    entry = MagicMock()
    entry.data = {
        CONF_TANK_CAPACITY: 250,
        CONF_FILL_PERCENT: 80,
        CONF_METER_UNITS_PER_GALLON: 26.81,
    }
    entry.options = {}
    tracker = PropaneTracker(hass, entry)
    tracker._store = MagicMock()
    tracker._store.async_save = AsyncMock()
    tracker._store.async_load = AsyncMock(return_value=None)
    return tracker


def _delivery(day: int, gallons: str, cost: str) -> Delivery:
    return Delivery(
        delivery_date=date(2026, 1, day),
        gallons=Decimal(gallons),
        cost=Decimal(cost),
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
        cost=300,
    )

    assert events == ["save", "sync"]
    assert tracker.ledger.total_cost == Decimal("300")


async def test_recording_a_delivery_refills_the_tank() -> None:
    """A fill re-anchors the level to the gallons a full tank holds."""
    tracker = _tracker()
    tracker.async_sync_statistics = AsyncMock()

    await tracker.async_record_delivery(
        delivery_date=date(2026, 1, 5),
        gallons=150,
        cost=500,
    )

    assert tracker.tank.level == Decimal("200.0")
    assert tracker.tank.anchor_date == date(2026, 1, 5)


async def test_a_backdated_bill_does_not_refill_the_tank() -> None:
    """Entering an old ticket is bookkeeping; it says nothing about today's level."""
    tracker = _tracker()
    tracker.async_sync_statistics = AsyncMock()
    tracker._ledger = Ledger.empty(date(2026, 2, 1)).with_delivery(_delivery(20, "80", "280"))
    tracker._tank = tracker._tank.with_fill(_delivery(20, "80", "280"), tracker.config)
    tracker._async_apply_reading(Decimal("0"))
    tracker._async_apply_reading(Decimal("2681"))
    assert tracker.tank.level == pytest.approx(Decimal("100"))

    await tracker.async_record_delivery(
        delivery_date=date(2026, 1, 5),
        gallons=100,
        cost=300,
    )

    assert tracker.tank.anchor_date == date(2026, 1, 20)
    assert tracker.tank.level == pytest.approx(Decimal("100"))


async def test_meter_readings_drain_the_tank_and_are_saved_lazily() -> None:
    """Readings arrive constantly, so they batch instead of writing every time."""
    tracker = _tracker()
    tracker._tank = tracker._tank.with_fill(_delivery(5, "150", "500"), tracker.config)

    tracker._async_apply_reading(Decimal("1000"))
    tracker._async_apply_reading(Decimal("1536.2"))

    assert tracker.tank.consumed_gallons == pytest.approx(Decimal("20"))
    assert tracker.tank.level == pytest.approx(Decimal("180"))
    tracker._store.async_delay_save.assert_called()
    tracker._store.async_save.assert_not_awaited()


async def test_days_remaining_uses_the_last_measured_fill_cycle() -> None:
    """Two tickets measure true burn, including the appliances with no meter."""
    tracker = _tracker()
    tracker._ledger = (
        Ledger.empty(date(2026, 2, 1))
        .with_delivery(_delivery(1, "150", "500"))
        .with_delivery(_delivery(31, "60", "200"))
    )
    tracker._tank = tracker._tank.with_fill(_delivery(31, "60", "200"), tracker.config)

    assert tracker.burn_rate == Decimal("2")
    assert tracker.days_remaining == Decimal("100")


async def test_historical_bill_can_be_added_after_a_newer_bill() -> None:
    """The ledger sorts history and a full rebuild fixes all cumulative sums."""
    tracker = _tracker()
    tracker._ledger = Ledger.empty(date(2026, 2, 1)).with_delivery(_delivery(20, "80", "320"))
    tracker.async_sync_statistics = AsyncMock()

    await tracker.async_record_delivery(
        delivery_date=date(2026, 1, 5),
        gallons=100,
        cost=300,
    )

    assert [item.delivery_date.day for item in tracker.ledger.deliveries] == [5, 20]
    tracker.async_sync_statistics.assert_awaited_once()


async def test_concurrent_draft_updates_do_not_clobber_each_other() -> None:
    """Each serialized input update starts with the newest draft snapshot."""
    tracker = _tracker()

    await tracker.async_set_draft_gallons(125.5)
    await tracker.async_set_draft_cost(407.87)

    assert tracker.ledger.draft.gallons == Decimal("125.5")
    assert tracker.ledger.draft.cost == Decimal("407.87")
    assert tracker.ledger.draft.price_per_gallon == Decimal("3.250")


async def test_committed_delivery_survives_statistics_failure() -> None:
    """A Recorder outage does not turn a successful durable write into a retry trap."""
    tracker = _tracker()
    tracker.async_sync_statistics = AsyncMock(side_effect=RuntimeError("recorder unavailable"))

    delivery = await tracker.async_record_delivery(
        delivery_date=date(2026, 1, 5),
        gallons=100,
        cost=300,
    )

    assert delivery in tracker.ledger.deliveries
    tracker._store.async_save.assert_awaited_once()


async def test_duplicate_and_future_deliveries_are_rejected() -> None:
    """Double taps and impossible ticket dates fail before storage changes."""
    tracker = _tracker()
    tracker._ledger = Ledger.empty(date(2026, 2, 1)).with_delivery(_delivery(5, "100", "300"))

    with pytest.raises(ServiceValidationError, match="double tap"):
        await tracker.async_record_delivery(
            delivery_date=date(2026, 1, 5),
            gallons=100,
            cost=300,
        )

    with pytest.raises(ServiceValidationError, match="future"):
        await tracker.async_record_delivery(
            delivery_date=date.today() + timedelta(days=1),
            gallons=100,
            cost=300,
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


async def test_sync_rebuilds_delivered_cost_and_unmetered_rows() -> None:
    """Every row is derivable from durable data after Recorder loss or backfill."""
    tracker = _tracker()
    tracker._ledger = (
        Ledger.empty(date(2026, 2, 1))
        .with_delivery(_delivery(5, "100", "300"))
        .with_delivery(_delivery(20, "50", "200"))
    )
    tracker._tank = tracker._tank.with_fill(_delivery(5, "100", "300"), tracker.config)
    tracker._async_apply_reading(Decimal("0"))
    tracker._async_apply_reading(Decimal("1072.4"))
    tracker._tank = tracker._tank.with_fill(_delivery(20, "50", "200"), tracker.config)
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

    assert add_statistics.call_count == 3
    delivered_call, cost_call, unmetered_call = add_statistics.call_args_list
    assert delivered_call.args[1]["statistic_id"] == DELIVERED_STATISTIC_ID
    assert cost_call.args[1]["statistic_id"] == COST_STATISTIC_ID
    assert unmetered_call.args[1]["statistic_id"] == UNMETERED_STATISTIC_ID

    delivered_rows = delivered_call.args[2]
    cost_rows = cost_call.args[2]
    unmetered_rows = unmetered_call.args[2]
    assert delivered_rows[0]["state"] == pytest.approx(100 * LITERS_PER_US_GALLON)
    assert delivered_rows[1]["sum"] == pytest.approx(150 * LITERS_PER_US_GALLON)
    assert cost_rows[0]["state"] == 300
    assert cost_rows[1]["sum"] == 500
    assert [row["start"].day for row in delivered_rows] == [5, 20]

    # 200 gallon tank, 40 metered, refilled with 50: the range burned 10.
    assert len(unmetered_rows) == 1
    assert unmetered_rows[0]["state"] == pytest.approx(10 * LITERS_PER_US_GALLON)
    assert unmetered_rows[0]["start"].day == 20
