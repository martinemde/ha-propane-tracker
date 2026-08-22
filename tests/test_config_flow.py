"""Tests for one-click Propane Tracker setup."""

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.propane_tracker.const import DOMAIN


async def test_user_flow_creates_single_entry(recorder_db_url, hass, recorder_mock) -> None:
    """The integration has no connection settings, only explicit confirmation."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Propane Tracker"
    assert result["data"] == {}


async def test_second_entry_is_aborted(recorder_db_url, hass, recorder_mock) -> None:
    """One house-wide ledger is the only unambiguous ownership model."""
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(first["flow_id"], {})

    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "single_instance_allowed"
