"""Exercise the estimated consumption statistic against Home Assistant Recorder."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from homeassistant.components.energy.data import EnergyManager, async_get_manager
from homeassistant.components.energy.validate import async_validate
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_list_statistic_ids,
    statistics_during_period,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.propane_tracker.usage import AcceptedInterval, InventoryBoundary
from custom_components.propane_tracker.usage_statistics import (
    ESTIMATED_CONSUMPTION_STATISTIC_ID,
    async_publish_estimated_consumption,
)


async def test_empty_usage_ledger_does_not_publish_a_meter(hass, recorder_mock):
    stat_ids = {ESTIMATED_CONSUMPTION_STATISTIC_ID}
    async_publish_estimated_consumption(hass, ())
    await async_wait_recording_done(hass)
    assert not await async_list_statistic_ids(hass, stat_ids)


async def test_interval_replay_is_one_gas_source_with_kwh_deltas(hass, recorder_mock):
    """Book the accounting total once, not the refill or the gaps as consumption."""
    stat_id = ESTIMATED_CONSUMPTION_STATISTIC_ID
    stat_ids = {stat_id}
    base = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=-8)))

    def boundary(hours: float, gallons: float) -> InventoryBoundary:
        return InventoryBoundary(
            at=base + timedelta(hours=hours), inventory_gal=gallons, basis_id="tank-1"
        )

    intervals = (
        AcceptedInterval(
            interval_id="first",
            start=boundary(0, 190.0),
            end=boundary(24.5, 188.0),
            net_inflow_gal=0.0,
        ),
        AcceptedInterval(
            interval_id="fill",
            start=boundary(24.5, 188.0),
            end=boundary(25, 197.0),
            net_inflow_gal=10.0,
        ),
        AcceptedInterval(
            interval_id="after-gap",
            start=boundary(70, 195.0),
            end=boundary(96, 193.0),
            net_inflow_gal=0.0,
        ),
    )
    for history in (intervals[:1], intervals, intervals):
        async_publish_estimated_consumption(hass, history)
        await async_wait_recording_done(hass)

    (metadata,) = await async_list_statistic_ids(hass, stat_ids)
    assert metadata["statistics_unit_of_measurement"] == "kWh"
    assert metadata["has_sum"]
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        base,
        base + timedelta(hours=96),
        stat_ids,
        "hour",
        None,
        {"sum", "change"},
    )
    rows = stats[stat_id]
    assert [datetime.fromtimestamp(row["start"], UTC) for row in rows] == [
        base + timedelta(hours=h) for h in (23, 24, 95)
    ]
    assert [row["sum"] for row in rows] == pytest.approx([0, 80.43, 134.05])
    assert [row["change"] for row in rows[1:]] == pytest.approx([80.43, 53.62])

    manager = await async_get_manager(hass)
    manager.data = EnergyManager.default_preferences()
    manager.data["energy_sources"] = [
        {
            "type": "gas",
            "stat_energy_from": stat_id,
            "stat_cost": None,
            "entity_energy_price": None,
            "number_energy_price": None,
        },
    ]
    result = await async_validate(hass)
    assert not result.energy_sources[0].issues
