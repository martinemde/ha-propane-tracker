"""Config and options flow for Propane Tracker."""

from __future__ import annotations

from math import isfinite
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_DEPTH_ENTITY,
    CONF_FILL_PERCENT,
    CONF_INNER_DIAMETER,
    CONF_METER_ENTITY,
    CONF_METER_UNITS_PER_GALLON,
    CONF_STRAIGHT_LENGTH,
    CONF_TANK_CAPACITY,
    DEFAULT_FILL_PERCENT,
    DEFAULT_METER_UNITS_PER_GALLON,
    DEFAULT_TANK_CAPACITY,
    DOMAIN,
    MAX_TANK_CAPACITY,
)


def _dimension_selector() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=0.001, step="any", mode=NumberSelectorMode.BOX, unit_of_measurement="in"
        )
    )


def _measurement_errors(settings: dict[str, Any]) -> dict[str, str]:
    if not settings.get(CONF_DEPTH_ENTITY):
        return {}
    return {
        key: "missing_dimension"
        for key in (CONF_INNER_DIAMETER, CONF_STRAIGHT_LENGTH)
        if not isfinite(settings.get(key, 0)) or settings.get(key, 0) <= 0
    }


def _schema(settings: dict[str, Any]) -> vol.Schema:
    """Build the tank form, prefilled from current settings."""
    meter_entity = settings.get(CONF_METER_ENTITY)
    return vol.Schema(
        {
            vol.Required(
                CONF_TANK_CAPACITY,
                default=settings.get(CONF_TANK_CAPACITY, DEFAULT_TANK_CAPACITY),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1,
                    max=MAX_TANK_CAPACITY,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="gal",
                )
            ),
            vol.Required(
                CONF_FILL_PERCENT,
                default=settings.get(CONF_FILL_PERCENT, DEFAULT_FILL_PERCENT),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1,
                    max=100,
                    step=0.5,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="%",
                )
            ),
            vol.Optional(
                CONF_METER_ENTITY,
                description={"suggested_value": meter_entity},
            ): EntitySelector(EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_METER_UNITS_PER_GALLON,
                default=settings.get(CONF_METER_UNITS_PER_GALLON, DEFAULT_METER_UNITS_PER_GALLON),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.001,
                    max=1000000,
                    step=0.01,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_DEPTH_ENTITY,
                description={"suggested_value": settings.get(CONF_DEPTH_ENTITY)},
            ): EntitySelector(EntitySelectorConfig(domain="sensor")),
            **{
                vol.Optional(
                    key, description={"suggested_value": settings.get(key)}
                ): _dimension_selector()
                for key in (CONF_INNER_DIAMETER, CONF_STRAIGHT_LENGTH)
            },
        }
    )


class PropaneTrackerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the single local propane ledger and describe its tank."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect the tank's size, fill level, and consumption meter."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors = _measurement_errors(user_input or {})
        if user_input is not None and not errors:
            return self.async_create_entry(title="Propane Tracker", data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        """Let the tank be re-described after setup."""
        return PropaneTrackerOptionsFlow()


class PropaneTrackerOptionsFlow(OptionsFlow):
    """Edit the tank configuration without losing the ledger."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the tank form with its current values."""
        errors = _measurement_errors(user_input or {})
        if user_input is not None and not errors:
            # Explicitly override a depth source saved by initial setup when cleared.
            return self.async_create_entry(data={CONF_DEPTH_ENTITY: None, **user_input})

        settings = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(settings if user_input is None else user_input),
            errors=errors,
        )
