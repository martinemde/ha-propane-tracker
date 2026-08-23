"""Numeric inputs for the next propane delivery."""

from __future__ import annotations

from enum import StrEnum

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfVolume
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MAX_COST, MAX_GALLONS
from .entity import PropaneEntity
from .tracker import PropaneConfigEntry


class InputKind(StrEnum):
    """The editable decimal fields on a delivery ticket."""

    GALLONS = "gallons"
    COST = "cost"


async def async_setup_entry(
    hass,
    entry: PropaneConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up gallons and invoice-total inputs."""
    async_add_entities(
        [
            PropaneNumber(entry, InputKind.GALLONS),
            PropaneNumber(entry, InputKind.COST),
        ]
    )


class PropaneNumber(PropaneEntity, NumberEntity):
    """One editable number persisted as part of the draft."""

    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0.0

    def __init__(self, entry: PropaneConfigEntry, kind: InputKind) -> None:
        super().__init__(entry, entry.runtime_data, f"delivery_{kind}")
        self._kind = kind
        if kind is InputKind.GALLONS:
            self._attr_translation_key = "delivery_gallons"
            self._attr_icon = "mdi:propane-tank"
            self._attr_native_max_value = MAX_GALLONS
            self._attr_native_step = 0.1
            self._attr_native_unit_of_measurement = UnitOfVolume.GALLONS
        else:
            self._attr_translation_key = "delivery_cost"
            self._attr_icon = "mdi:cash"
            self._attr_native_max_value = MAX_COST
            self._attr_native_step = 0.01
            self._attr_native_unit_of_measurement = entry.runtime_data.hass.config.currency

    @property
    def native_value(self) -> float:
        """Return the selected draft field."""
        draft = self._tracker.ledger.draft
        value = draft.gallons if self._kind is InputKind.GALLONS else draft.cost
        return float(value)

    async def async_set_native_value(self, value: float) -> None:
        """Persist a new draft field."""
        if self._kind is InputKind.GALLONS:
            await self._tracker.async_set_draft_gallons(value)
        else:
            await self._tracker.async_set_draft_cost(value)
