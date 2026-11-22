"""Measured inventory is separate from the meter and delivery ledger."""

from datetime import timedelta

import pytest
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE
from homeassistant.core import State
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.propane_tracker.const import (
    CONF_DEPTH_ENTITY,
    CONF_INNER_DIAMETER,
    CONF_STRAIGHT_LENGTH,
    DOMAIN,
)
from custom_components.propane_tracker.measured import MAX_REPORT_AGE, HorizontalTank

DEPTH = "sensor.tank_depth"
REMAINING = "sensor.propane_remaining"
PERCENT = "sensor.propane_measured_tank_level"
TANK = HorizontalTank(diameter=29.602, straight_length=63.5)


def test_hemispherical_geometry():
    assert TANK.capacity == pytest.approx(247.984, abs=0.001)
    assert TANK.gallons_at_depth(0) == 0
    assert TANK.gallons_at_depth(TANK.diameter / 2) == pytest.approx(TANK.capacity / 2)
    assert TANK.gallons_at_depth(TANK.diameter) == TANK.capacity
    volumes = [TANK.gallons_at_depth(TANK.diameter * i / 128) for i in range(129)]
    assert all(a < b for a, b in zip(volumes, volumes[1:]))


@pytest.mark.parametrize("depth,unit", [(557, "mm"), (557 / 25.4, "in")])
def test_equivalent_input_units(depth, unit):
    state = State(DEPTH, str(depth), {ATTR_UNIT_OF_MEASUREMENT: unit})
    assert TANK.gallons_from_state(state) == pytest.approx(199.26, abs=0.01)


@pytest.mark.parametrize(
    "depth,unit",
    [
        ("unknown", "mm"),
        ("unavailable", "mm"),
        ("NaN", "mm"),
        ("inf", "mm"),
        ("-1", "mm"),
        ("40.39", "in"),
        ("557", "gal"),
        ("557", None),
    ],
)
def test_rejects_unusable_input(depth, unit):
    assert TANK.gallons_from_state(State(DEPTH, depth, {ATTR_UNIT_OF_MEASUREMENT: unit})) is None


async def test_live_inventory_and_freshness(hass, recorder_mock, freezer):
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Propane Tracker",
        unique_id=DOMAIN,
        version=2,
        options={CONF_DEPTH_ENTITY: DEPTH, CONF_INNER_DIAMETER: 29.602, CONF_STRAIGHT_LENGTH: 63.5},
    )
    entry.add_to_hass(hass)
    attrs = {ATTR_UNIT_OF_MEASUREMENT: "mm"}
    hass.states.async_set(DEPTH, "557", attrs)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.propane_tank_level").state == STATE_UNAVAILABLE
    measured = hass.states.get(REMAINING)
    assert float(measured.state) == pytest.approx(199.26, abs=0.01)
    assert measured.attributes["device_class"] == "volume_storage"
    assert measured.attributes["state_class"] == "measurement"
    percent = hass.states.get(PERCENT)
    assert float(percent.state) == pytest.approx(80.35, abs=0.01)
    assert "device_class" not in percent.attributes
    assert percent.attributes["state_class"] == "measurement"

    for bad in ("1026", "unknown", "unavailable"):
        hass.states.async_set(DEPTH, bad, attrs)
        await hass.async_block_till_done()
        assert hass.states.get(REMAINING).state == STATE_UNAVAILABLE
    hass.states.async_set(DEPTH, "557", attrs)
    await hass.async_block_till_done()

    # A duplicate report has the same last_changed but extends freshness.
    freezer.tick(timedelta(hours=1))
    hass.states.async_set(DEPTH, "557", attrs)
    await hass.async_block_till_done()
    freezer.tick(MAX_REPORT_AGE - timedelta(hours=1) + timedelta(minutes=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(REMAINING).state == measured.state

    freezer.tick(timedelta(hours=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(REMAINING).state == STATE_UNAVAILABLE
    assert hass.states.get(PERCENT).state == STATE_UNAVAILABLE

    hass.states.async_set(DEPTH, "557", attrs)
    await hass.async_block_till_done()
    assert hass.states.get(REMAINING).state == measured.state
