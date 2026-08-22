"""Summary sensors backed by the durable propane ledger."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfVolume
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import PropaneEntity
from .tracker import PropaneConfigEntry


class SummaryKind(StrEnum):
    """The three summary views of the ledger."""

    TOTAL_USAGE = "total_usage"
    TOTAL_SPEND = "total_spend"
    LAST_DELIVERY = "last_delivery"


async def async_setup_entry(
    hass,
    entry: PropaneConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ledger summary sensors."""
    async_add_entities(
        [
            PropaneSummarySensor(entry, SummaryKind.TOTAL_USAGE),
            PropaneSummarySensor(entry, SummaryKind.TOTAL_SPEND),
            PropaneSummarySensor(entry, SummaryKind.LAST_DELIVERY),
        ]
    )


class PropaneSummarySensor(PropaneEntity, SensorEntity):
    """Current total or newest-delivery view of the ledger."""

    def __init__(self, entry: PropaneConfigEntry, kind: SummaryKind) -> None:
        super().__init__(entry, entry.runtime_data, kind)
        self._kind = kind
        self._attr_translation_key = kind
        if kind is SummaryKind.TOTAL_USAGE:
            self._attr_icon = "mdi:propane-tank"
            self._attr_device_class = SensorDeviceClass.VOLUME
            self._attr_native_unit_of_measurement = UnitOfVolume.GALLONS
        elif kind is SummaryKind.TOTAL_SPEND:
            self._attr_icon = "mdi:cash-multiple"
            self._attr_device_class = SensorDeviceClass.MONETARY
            self._attr_native_unit_of_measurement = entry.runtime_data.hass.config.currency
        else:
            self._attr_icon = "mdi:truck-delivery"
            self._attr_device_class = SensorDeviceClass.DATE

    @property
    def native_value(self) -> float | date | None:
        """Return the requested ledger summary."""
        ledger = self._tracker.ledger
        if self._kind is SummaryKind.TOTAL_USAGE:
            return float(ledger.total_gallons)
        if self._kind is SummaryKind.TOTAL_SPEND:
            return float(ledger.total_cost)
        return ledger.last_delivery.delivery_date if ledger.last_delivery is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Describe the newest ticket alongside its date."""
        if self._kind is not SummaryKind.LAST_DELIVERY:
            return None
        delivery = self._tracker.ledger.last_delivery
        if delivery is None:
            return None
        return {
            "gallons": float(delivery.gallons),
            "price_per_gallon": float(delivery.price_per_gallon),
            "cost": float(delivery.cost),
            "currency": self.hass.config.currency,
        }
