"""Button that commits the current propane delivery draft."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import PropaneEntity
from .tracker import PropaneConfigEntry


async def async_setup_entry(
    hass,
    entry: PropaneConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the record-delivery button."""
    async_add_entities([PropaneRecordDeliveryButton(entry)])


class PropaneRecordDeliveryButton(PropaneEntity, ButtonEntity):
    """Commit the integration's three draft inputs as one delivery."""

    _attr_translation_key = "record_delivery"
    _attr_icon = "mdi:database-plus"

    def __init__(self, entry: PropaneConfigEntry) -> None:
        super().__init__(entry, entry.runtime_data, "record_delivery")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the draft summary for dashboards and confirmation cards."""
        draft = self._tracker.ledger.draft
        return {
            "delivery_date": draft.delivery_date.isoformat(),
            "gallons": float(draft.gallons),
            "price_per_gallon": float(draft.price_per_gallon),
            "estimated_cost": float(draft.estimated_cost),
            "currency": self.hass.config.currency,
        }

    async def async_press(self) -> None:
        """Record the draft as a durable delivery."""
        await self._tracker.async_record_draft()
