"""Manual receipts on measured tanks do not change the tank."""

from datetime import timedelta

import pytest
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.propane_tracker.const import (
    CONF_DEPTH_ENTITY,
    CONF_INNER_DIAMETER,
    CONF_STRAIGHT_LENGTH,
    COST_STATISTIC_ID,
    DELIVERED_STATISTIC_ID,
    DOMAIN,
    LITERS_PER_US_GALLON,
    STORAGE_KEY,
    STORAGE_VERSION,
    UNMETERED_STATISTIC_ID,
)


@pytest.mark.parametrize("depth", ["557", STATE_UNAVAILABLE])
@pytest.mark.parametrize("action", ["button", "service"])
async def test_late_newest_receipt_is_bookkeeping_only(
    hass, hass_storage, recorder_mock, depth, action
):
    hass.config.units = US_CUSTOMARY_SYSTEM
    today = dt_util.now().date()
    old_date = today - timedelta(days=30)
    receipt_date = today - timedelta(days=7)
    old_receipt = {"delivery_date": old_date.isoformat(), "gallons": "100", "cost": "300"}
    receipt = {"delivery_date": receipt_date.isoformat(), "gallons": "75", "cost": "225"}
    # Preserve the tank state left over from before the depth sensor was configured.
    old_tank = {
        "consumed_gallons": "40",
        "meter_reading": "1072.4",
        "anchor_level": "200",
        "anchor_consumed": "0",
        "anchor_date": old_date.isoformat(),
        "reconciliations": [],
    }
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "data": {"deliveries": [old_receipt], "draft": receipt, "tank": old_tank},
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Propane Tracker",
        unique_id=DOMAIN,
        version=2,
        options={
            CONF_DEPTH_ENTITY: "sensor.tank_depth",
            CONF_INNER_DIAMETER: 29.602,
            CONF_STRAIGHT_LENGTH: 63.5,
        },
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.tank_depth", depth, {ATTR_UNIT_OF_MEASUREMENT: "mm"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    remaining = hass.states.get("sensor.propane_remaining").state
    percent = hass.states.get("sensor.propane_measured_tank_level").state
    if depth == STATE_UNAVAILABLE:
        assert remaining == percent == STATE_UNAVAILABLE
    else:
        assert float(remaining) == pytest.approx(199.26, abs=0.01)
        assert float(percent) == pytest.approx(80.35, abs=0.01)
    assert float(hass.states.get("sensor.propane_tank_level").state) == 160

    if action == "button":
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.propane_record_delivery"}, blocking=True
        )
    else:
        await hass.services.async_call(DOMAIN, "record_delivery", receipt, blocking=True)
    await hass.async_block_till_done()

    assert float(hass.states.get("sensor.propane_tank_level").state) == 160
    days_remaining = hass.states.get("sensor.propane_days_remaining")
    assert days_remaining.state == STATE_UNAVAILABLE
    assert days_remaining.attributes.get("burn_rate_gallons_per_day") is None
    assert hass.states.get("sensor.propane_remaining").state == remaining
    assert hass.states.get("sensor.propane_measured_tank_level").state == percent
    assert hass.states.get("sensor.propane_last_delivery").state == receipt_date.isoformat()
    assert float(hass.states.get("sensor.propane_total_delivered").state) == 175
    assert float(hass.states.get("sensor.propane_total_spend").state) == 525
    stored = hass_storage[STORAGE_KEY]["data"]
    assert stored["tank"] == old_tank
    assert [
        (row["delivery_date"], float(row["gallons"]), float(row["cost"]))
        for row in stored["deliveries"]
    ] == [
        (old_date.isoformat(), 100, 300),
        (receipt_date.isoformat(), 75, 225),
    ]

    await recorder_mock.async_block_till_done()
    stats = await recorder_mock.async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.start_of_local_day(old_date),
        None,
        {DELIVERED_STATISTIC_ID, COST_STATISTIC_ID, UNMETERED_STATISTIC_ID},
        "hour",
        None,
        {"sum", "state"},
    )
    assert UNMETERED_STATISTIC_ID not in stats
    delivered = stats[DELIVERED_STATISTIC_ID]
    assert [
        dt_util.as_local(dt_util.utc_from_timestamp(row["start"])).date() for row in delivered
    ] == [old_date, receipt_date]
    assert [row["state"] for row in delivered] == pytest.approx(
        [100 * LITERS_PER_US_GALLON, 75 * LITERS_PER_US_GALLON]
    )
    assert [row["sum"] for row in delivered] == pytest.approx(
        [100 * LITERS_PER_US_GALLON, 175 * LITERS_PER_US_GALLON]
    )
    assert [(row["state"], row["sum"]) for row in stats[COST_STATISTIC_ID]] == [
        (300, 300),
        (225, 525),
    ]
