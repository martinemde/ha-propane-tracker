"""Integration smoke test against a real Home Assistant test instance."""

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.propane_tracker.const import DOMAIN


async def test_setup_creates_native_entry_and_summary_entities(
    recorder_db_url, hass, recorder_mock
) -> None:
    """HACS installation plus config flow is sufficient; no YAML package is needed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Propane Tracker",
        unique_id=DOMAIN,
        data={},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.propane_tracker.tracker.PropaneTracker.async_initialize",
        return_value=None,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get("date.propane_delivery_date") is not None
    assert hass.states.get("number.propane_gallons_delivered") is not None
    assert hass.states.get("number.propane_price_per_gallon") is not None
    assert hass.states.get("button.propane_record_delivery") is not None
    assert hass.states.get("sensor.propane_total_usage") is not None
    assert hass.states.get("sensor.propane_total_spend") is not None
    assert hass.states.get("sensor.propane_last_delivery") is not None
