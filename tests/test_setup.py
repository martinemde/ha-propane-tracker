"""Integration smoke test against a real Home Assistant test instance."""

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.propane_tracker.const import (
    CONF_FILL_PERCENT,
    CONF_METER_UNITS_PER_GALLON,
    CONF_TANK_CAPACITY,
    DOMAIN,
)


async def test_setup_creates_native_entry_and_derived_entities(
    recorder_db_url, hass, recorder_mock
) -> None:
    """HACS installation plus config flow is sufficient; no YAML package is needed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Propane Tracker",
        unique_id=DOMAIN,
        version=2,
        data={
            CONF_TANK_CAPACITY: 250,
            CONF_FILL_PERCENT: 80,
            CONF_METER_UNITS_PER_GALLON: 26.81,
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.propane_tracker.tracker.PropaneTracker.async_initialize",
        return_value=None,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    for entity_id in (
        "date.propane_delivery_date",
        "number.propane_gallons_delivered",
        "number.propane_invoice_total",
        "button.propane_record_delivery",
        "sensor.propane_total_delivered",
        "sensor.propane_total_spend",
        "sensor.propane_last_delivery",
        "sensor.propane_tank_level",
        "sensor.propane_tank_gauge",
        "sensor.propane_metered_burn",
        "sensor.propane_days_remaining",
    ):
        assert hass.states.get(entity_id) is not None, entity_id
