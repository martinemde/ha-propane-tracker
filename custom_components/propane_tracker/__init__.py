"""Manual propane delivery ledger for Home Assistant."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_DELIVERY_DATE,
    ATTR_GALLONS,
    ATTR_PRICE_PER_GALLON,
    DOMAIN,
    MAX_GALLONS,
    MAX_PRICE_PER_GALLON,
    PLATFORMS,
    SERVICE_RECORD_DELIVERY,
)
from .tracker import PropaneConfigEntry, PropaneTracker


def _date(value: Any) -> date:
    """Coerce a service value to an ISO date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as err:
        raise vol.Invalid("must be a date in YYYY-MM-DD format") from err


SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DELIVERY_DATE): _date,
        vol.Required(ATTR_GALLONS): vol.All(
            vol.Coerce(float), vol.Range(min=0.001, max=MAX_GALLONS)
        ),
        vol.Required(ATTR_PRICE_PER_GALLON): vol.All(
            vol.Coerce(float),
            vol.Range(min=0.001, max=MAX_PRICE_PER_GALLON),
        ),
    }
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register integration-wide actions."""

    async def _handle_record_delivery(call: ServiceCall) -> None:
        tracker = _loaded_tracker(hass)
        await tracker.async_record_delivery(
            delivery_date=call.data[ATTR_DELIVERY_DATE],
            gallons=call.data[ATTR_GALLONS],
            price_per_gallon=call.data[ATTR_PRICE_PER_GALLON],
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RECORD_DELIVERY,
        _handle_record_delivery,
        schema=SERVICE_SCHEMA,
    )
    return True


def _loaded_tracker(hass: HomeAssistant) -> PropaneTracker:
    """Return the single loaded tracker or a user-facing action error."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise ServiceValidationError(
        "Propane Tracker is not loaded. Add or reload the integration first."
    )


async def async_setup_entry(hass: HomeAssistant, entry: PropaneConfigEntry) -> bool:
    """Load the ledger before exposing its entities."""
    tracker = PropaneTracker(hass, entry)
    await tracker.async_initialize()
    entry.runtime_data = tracker
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PropaneConfigEntry) -> bool:
    """Unload all Propane Tracker platforms."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
