"""Date input for the next propane delivery."""

from __future__ import annotations

from datetime import date

from homeassistant.components.date import DateEntity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import PropaneEntity
from .tracker import PropaneConfigEntry


async def async_setup_entry(
    hass,
    entry: PropaneConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the delivery-date input."""
    async_add_entities([PropaneDeliveryDate(entry)])


class PropaneDeliveryDate(PropaneEntity, DateEntity):
    """Editable date persisted with the ledger."""

    _attr_translation_key = "delivery_date"
    _attr_icon = "mdi:calendar"

    def __init__(self, entry: PropaneConfigEntry) -> None:
        super().__init__(entry, entry.runtime_data, "delivery_date")

    @property
    def native_value(self) -> date:
        """Return the current draft date."""
        return self._tracker.ledger.draft.delivery_date

    async def async_set_value(self, value: date) -> None:
        """Persist a new draft date."""
        await self._tracker.async_set_draft_date(value)
