"""Tests for Propane Tracker setup and reconfiguration."""

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.propane_tracker.const import (
    CONF_FILL_PERCENT,
    CONF_METER_ENTITY,
    CONF_METER_UNITS_PER_GALLON,
    CONF_TANK_CAPACITY,
    DOMAIN,
)

TANK = {
    CONF_TANK_CAPACITY: 250,
    CONF_FILL_PERCENT: 80,
    CONF_METER_UNITS_PER_GALLON: 26.81,
}


async def test_user_flow_describes_the_tank(recorder_db_url, hass, recorder_mock) -> None:
    """Setup collects the physical facts the tank model cannot infer."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], TANK)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Propane Tracker"
    assert result["data"][CONF_TANK_CAPACITY] == 250
    assert result["data"][CONF_FILL_PERCENT] == 80


async def test_second_entry_is_aborted(recorder_db_url, hass, recorder_mock) -> None:
    """One house-wide ledger is the only unambiguous ownership model."""
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(first["flow_id"], TANK)

    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "single_instance_allowed"


async def test_options_flow_redescribes_the_tank(recorder_db_url, hass, recorder_mock) -> None:
    """A tank swap or a new meter must not require deleting the ledger."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Propane Tracker", unique_id=DOMAIN, data=TANK, version=2
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**TANK, CONF_TANK_CAPACITY: 500, CONF_METER_ENTITY: "sensor.water_heater_propane"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.runtime_data.config.full == 400
    assert entry.runtime_data.config.meter_entity == "sensor.water_heater_propane"


async def test_a_version_one_entry_gains_a_tank(recorder_db_url, hass, recorder_mock) -> None:
    """An installation that predates the tank keeps its ledger and gets defaults."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Propane Tracker", unique_id=DOMAIN, data={}, version=1
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert entry.data[CONF_TANK_CAPACITY] == 250
    assert entry.runtime_data.config.full == 200
