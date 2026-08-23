"""Ledger summaries and the derived tank level."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTime, UnitOfVolume
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import PropaneEntity
from .tracker import PropaneConfigEntry


class SensorKind(StrEnum):
    """Every published view of the ledger and the tank."""

    TOTAL_DELIVERED = "total_delivered"
    TOTAL_SPEND = "total_spend"
    LAST_DELIVERY = "last_delivery"
    TANK_LEVEL = "tank_level"
    TANK_GAUGE = "tank_gauge"
    CONSUMED = "consumed"
    DAYS_REMAINING = "days_remaining"


async def async_setup_entry(
    hass,
    entry: PropaneConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ledger and tank sensors."""
    async_add_entities(PropaneSensor(entry, kind) for kind in SensorKind)


class PropaneSensor(PropaneEntity, SensorEntity):
    """One derived view of the propane ledger or tank."""

    def __init__(self, entry: PropaneConfigEntry, kind: SensorKind) -> None:
        super().__init__(entry, entry.runtime_data, kind)
        self._kind = kind
        self._attr_translation_key = kind
        match kind:
            case SensorKind.TOTAL_DELIVERED:
                self._attr_icon = "mdi:truck-delivery"
                self._attr_device_class = SensorDeviceClass.VOLUME
                self._attr_native_unit_of_measurement = UnitOfVolume.GALLONS
            case SensorKind.TOTAL_SPEND:
                self._attr_icon = "mdi:cash-multiple"
                self._attr_device_class = SensorDeviceClass.MONETARY
                self._attr_native_unit_of_measurement = entry.runtime_data.hass.config.currency
            case SensorKind.LAST_DELIVERY:
                self._attr_icon = "mdi:truck-check"
                self._attr_device_class = SensorDeviceClass.DATE
            case SensorKind.TANK_LEVEL:
                self._attr_icon = "mdi:propane-tank"
                self._attr_device_class = SensorDeviceClass.VOLUME_STORAGE
                self._attr_native_unit_of_measurement = UnitOfVolume.GALLONS
                self._attr_state_class = SensorStateClass.MEASUREMENT
                self._attr_suggested_display_precision = 1
            case SensorKind.TANK_GAUGE:
                self._attr_icon = "mdi:gauge"
                self._attr_native_unit_of_measurement = PERCENTAGE
                self._attr_state_class = SensorStateClass.MEASUREMENT
                self._attr_suggested_display_precision = 0
            case SensorKind.CONSUMED:
                self._attr_icon = "mdi:fire"
                self._attr_device_class = SensorDeviceClass.VOLUME
                self._attr_native_unit_of_measurement = UnitOfVolume.GALLONS
                self._attr_state_class = SensorStateClass.TOTAL_INCREASING
                self._attr_suggested_display_precision = 2
            case SensorKind.DAYS_REMAINING:
                self._attr_icon = "mdi:calendar-clock"
                self._attr_device_class = SensorDeviceClass.DURATION
                self._attr_native_unit_of_measurement = UnitOfTime.DAYS
                self._attr_suggested_display_precision = 0

    @property
    def available(self) -> bool:
        """Report tank sensors as unavailable until a fill anchors the model."""
        if self._kind in (SensorKind.TANK_LEVEL, SensorKind.TANK_GAUGE):
            return self._tracker.tank.level is not None
        return True

    @property
    def native_value(self) -> float | date | None:
        """Return the requested ledger or tank value."""
        tracker = self._tracker
        ledger = tracker.ledger
        match self._kind:
            case SensorKind.TOTAL_DELIVERED:
                return float(ledger.total_gallons)
            case SensorKind.TOTAL_SPEND:
                return float(ledger.total_cost)
            case SensorKind.LAST_DELIVERY:
                last = ledger.last_delivery
                return last.delivery_date if last is not None else None
            case SensorKind.TANK_LEVEL:
                return _optional_float(tracker.tank.level)
            case SensorKind.TANK_GAUGE:
                level = tracker.tank.level
                if level is None:
                    return None
                return float(tracker.config.gauge_percent(level))
            case SensorKind.CONSUMED:
                return float(tracker.tank.consumed_gallons)
            case SensorKind.DAYS_REMAINING:
                return _optional_float(tracker.days_remaining)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Describe what the value was derived from."""
        tracker = self._tracker
        match self._kind:
            case SensorKind.LAST_DELIVERY:
                delivery = tracker.ledger.last_delivery
                if delivery is None:
                    return None
                return {
                    "gallons": float(delivery.gallons),
                    "cost": float(delivery.cost),
                    "price_per_gallon": float(delivery.price_per_gallon),
                    "currency": self.hass.config.currency,
                }
            case SensorKind.TANK_LEVEL:
                tank = tracker.tank
                config = tracker.config
                latest = tank.reconciliations[-1] if tank.reconciliations else None
                return {
                    "full_gallons": float(config.full),
                    "capacity_gallons": float(config.capacity),
                    "last_fill": None if tank.anchor_date is None else tank.anchor_date.isoformat(),
                    "metered_since_fill": float(tank.metered_since_fill),
                    "unmetered_last_cycle": (
                        None if latest is None else float(latest.unmetered_gallons)
                    ),
                }
            case SensorKind.CONSUMED:
                return {"meter_entity": tracker.config.meter_entity}
            case SensorKind.DAYS_REMAINING:
                return {"burn_rate_gallons_per_day": _optional_float(tracker.burn_rate)}
        return None


def _optional_float(value: Decimal | None) -> float | None:
    """Return a float, preserving an unknown value."""
    return None if value is None else float(value)
