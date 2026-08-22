"""Shared entity behavior for Propane Tracker."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .tracker import PropaneConfigEntry, PropaneTracker


class PropaneEntity:
    """Mixin that binds an entity to the ledger and one logical device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: PropaneConfigEntry,
        tracker: PropaneTracker,
        unique_id_suffix: str,
    ) -> None:
        self._entry = entry
        self._tracker = tracker
        self._attr_unique_id = f"{entry.entry_id}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Propane",
            manufacturer="Propane Tracker",
            model="Manual delivery ledger",
        )

    async def async_added_to_hass(self) -> None:
        """Update this entity whenever the ledger or draft changes."""
        await super().async_added_to_hass()
        self.async_on_remove(self._tracker.async_add_listener(self.async_write_ha_state))
