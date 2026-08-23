"""Manual propane delivery ledger for Home Assistant."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import voluptuous as vol

from homeassistant.components.recorder import get_instance
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_COST,
    ATTR_DELIVERY_DATE,
    ATTR_GALLONS,
    ATTR_PRICE_PER_GALLON,
    CONF_FILL_PERCENT,
    CONF_METER_UNITS_PER_GALLON,
    CONF_TANK_CAPACITY,
    DEFAULT_FILL_PERCENT,
    DEFAULT_METER_UNITS_PER_GALLON,
    DEFAULT_TANK_CAPACITY,
    DOMAIN,
    LEGACY_USAGE_STATISTIC_ID,
    MAX_COST,
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


def _has_an_amount(data: dict[str, Any]) -> dict[str, Any]:
    """Require one of the two ways to state what the delivery cost."""
    if ATTR_COST not in data and ATTR_PRICE_PER_GALLON not in data:
        raise vol.Invalid(f"{ATTR_COST} or {ATTR_PRICE_PER_GALLON} is required")
    return data


SERVICE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(ATTR_DELIVERY_DATE): _date,
            vol.Required(ATTR_GALLONS): vol.All(
                vol.Coerce(float), vol.Range(min=0.001, max=MAX_GALLONS)
            ),
            vol.Exclusive(ATTR_COST, "amount"): vol.All(
                vol.Coerce(float), vol.Range(min=0.001, max=MAX_COST)
            ),
            vol.Exclusive(ATTR_PRICE_PER_GALLON, "amount"): vol.All(
                vol.Coerce(float), vol.Range(min=0.001, max=MAX_PRICE_PER_GALLON)
            ),
        }
    ),
    _has_an_amount,
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register integration-wide actions."""

    async def _handle_record_delivery(call: ServiceCall) -> None:
        tracker = _loaded_tracker(hass)
        gallons = call.data[ATTR_GALLONS]
        cost = call.data.get(ATTR_COST)
        if cost is None:
            cost = round(gallons * call.data[ATTR_PRICE_PER_GALLON], 2)
        await tracker.async_record_delivery(
            delivery_date=call.data[ATTR_DELIVERY_DATE],
            gallons=gallons,
            cost=cost,
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


async def async_migrate_entry(hass: HomeAssistant, entry: PropaneConfigEntry) -> bool:
    """Give a version 1 entry a tank, and drop the mislabeled usage series."""
    if entry.version >= 2:
        return True

    hass.config_entries.async_update_entry(
        entry,
        data={
            CONF_TANK_CAPACITY: DEFAULT_TANK_CAPACITY,
            CONF_FILL_PERCENT: DEFAULT_FILL_PERCENT,
            CONF_METER_UNITS_PER_GALLON: DEFAULT_METER_UNITS_PER_GALLON,
            **entry.data,
        },
        version=2,
    )

    # Deliveries were published as consumption under propane_usage. They are
    # inflow, and the same rows are rebuilt from the ledger under
    # propane_delivered, so the old series is dropped rather than left to
    # double-count propane in the Energy dashboard.
    get_instance(hass).async_clear_statistics([LEGACY_USAGE_STATISTIC_ID])
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PropaneConfigEntry) -> bool:
    """Load the ledger before exposing its entities."""
    tracker = PropaneTracker(hass, entry)
    await tracker.async_initialize()
    entry.runtime_data = tracker
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    tracker.async_start_meter()
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: PropaneConfigEntry) -> None:
    """Re-read the tank configuration after the options flow changes it."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PropaneConfigEntry) -> bool:
    """Unload all Propane Tracker platforms."""
    entry.runtime_data.async_shutdown()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
