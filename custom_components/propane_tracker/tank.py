"""Tank storage model: what a fill puts in, what a meter takes out.

Home Assistant has no storage concept for a fuel source, so the tank is
modelled here the way a battery is: a level that a delivery raises and metered
burn lowers. The tank has no level sensor, so every full fill re-anchors the
model to a known quantity instead of letting error accumulate.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any, Self

from .ledger import Delivery, LedgerError, parse_date, parse_decimal

ZERO = Decimal("0")
VOLUME_QUANTUM = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class TankConfig:
    """The physical tank and the meter that watches part of its outflow."""

    capacity: Decimal
    fill_fraction: Decimal
    meter_units_per_gallon: Decimal
    meter_entity: str | None = None

    @property
    def full(self) -> Decimal:
        """Return the gallons a full tank holds, the delivery driver's stop point."""
        return self.capacity * self.fill_fraction

    def gauge_percent(self, level: Decimal) -> Decimal:
        """Return the reading the tank's own gauge shows for a level.

        The gauge is a percentage of water capacity, so a full tank reads the
        fill fraction, not 100.
        """
        if self.capacity <= 0:
            return ZERO
        return (level / self.capacity * 100).quantize(VOLUME_QUANTUM)


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """What one fill revealed about the burn no meter measured."""

    delivery_date: date
    modeled_gallons: Decimal
    actual_gallons: Decimal

    @property
    def unmetered_gallons(self) -> Decimal:
        """Return burn the meter missed; negative means the meter reads high."""
        return self.modeled_gallons - self.actual_gallons

    def as_dict(self) -> dict[str, str]:
        """Serialize one reconciliation."""
        return {
            "delivery_date": self.delivery_date.isoformat(),
            "modeled_gallons": str(self.modeled_gallons),
            "actual_gallons": str(self.actual_gallons),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Load one stored reconciliation."""
        return cls(
            delivery_date=parse_date(data.get("delivery_date")),
            modeled_gallons=parse_decimal(data.get("modeled_gallons"), "modeled_gallons"),
            actual_gallons=parse_decimal(data.get("actual_gallons"), "actual_gallons"),
        )


@dataclass(frozen=True, slots=True)
class TankState:
    """Durable meter totals and the anchor set by the most recent full fill."""

    consumed_gallons: Decimal = ZERO
    meter_reading: Decimal | None = None
    anchor_level: Decimal | None = None
    anchor_consumed: Decimal = ZERO
    anchor_date: date | None = None
    reconciliations: tuple[Reconciliation, ...] = ()

    @property
    def metered_since_fill(self) -> Decimal:
        """Return metered burn since the tank was last anchored."""
        return self.consumed_gallons - self.anchor_consumed

    @property
    def level(self) -> Decimal | None:
        """Return estimated gallons in the tank, or None before the first fill.

        Unmetered appliances make this read high until the next fill corrects
        it, so it is a floor on what has been burned, not a measurement.
        """
        if self.anchor_level is None:
            return None
        return max(ZERO, self.anchor_level - self.metered_since_fill)

    def with_meter_reading(self, reading: Decimal, units_per_gallon: Decimal) -> Self:
        """Fold a new total-increasing meter reading into consumed gallons.

        The first reading only establishes a baseline; a reading below the last
        one is treated as a counter reset, matching how Home Assistant reads a
        total_increasing sensor.
        """
        if units_per_gallon <= 0:
            raise LedgerError("meter_units_per_gallon must be greater than zero")
        if reading < 0:
            return self
        if self.meter_reading is None:
            return replace(self, meter_reading=reading)
        delta = reading if reading < self.meter_reading else reading - self.meter_reading
        return replace(
            self,
            meter_reading=reading,
            consumed_gallons=self.consumed_gallons + delta / units_per_gallon,
        )

    def with_fill(self, delivery: Delivery, config: TankConfig) -> Self:
        """Re-anchor the tank to a known level and record what the fill revealed.

        The driver stops at the fill fraction, so the gallons on the ticket say
        how empty the tank actually was. The gap between that and the modelled
        level is burn from appliances with no meter.
        """
        actual = max(ZERO, config.full - delivery.gallons)
        modeled = self.level
        reconciliations = self.reconciliations
        if modeled is not None:
            reconciliations = (
                *reconciliations,
                Reconciliation(
                    delivery_date=delivery.delivery_date,
                    modeled_gallons=modeled,
                    actual_gallons=actual,
                ),
            )
        return replace(
            self,
            anchor_level=config.full,
            anchor_consumed=self.consumed_gallons,
            anchor_date=delivery.delivery_date,
            reconciliations=reconciliations,
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialize durable tank state."""
        return {
            "consumed_gallons": str(self.consumed_gallons),
            "meter_reading": None if self.meter_reading is None else str(self.meter_reading),
            "anchor_level": None if self.anchor_level is None else str(self.anchor_level),
            "anchor_consumed": str(self.anchor_consumed),
            "anchor_date": None if self.anchor_date is None else self.anchor_date.isoformat(),
            "reconciliations": [item.as_dict() for item in self.reconciliations],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Load durable tank state, accepting a version 1 payload with none."""
        meter_reading = data.get("meter_reading")
        anchor_level = data.get("anchor_level")
        anchor_date = data.get("anchor_date")
        return cls(
            consumed_gallons=parse_decimal(data.get("consumed_gallons", "0"), "consumed_gallons"),
            meter_reading=None
            if meter_reading is None
            else parse_decimal(meter_reading, "meter_reading"),
            anchor_level=None
            if anchor_level is None
            else parse_decimal(anchor_level, "anchor_level"),
            anchor_consumed=parse_decimal(data.get("anchor_consumed", "0"), "anchor_consumed"),
            anchor_date=None if anchor_date is None else parse_date(anchor_date, "anchor_date"),
            reconciliations=tuple(
                Reconciliation.from_dict(item) for item in data.get("reconciliations", [])
            ),
        )


def burn_rate(deliveries: tuple[Delivery, ...]) -> Decimal | None:
    """Return measured gallons per day across the most recent fill cycle.

    A fill replaces exactly what was burned since the previous one, so two
    consecutive tickets measure true whole-house burn, metered or not.
    """
    if len(deliveries) < 2:
        return None
    previous, latest = deliveries[-2], deliveries[-1]
    days = Decimal((latest.delivery_date - previous.delivery_date).days)
    if days <= 0:
        return None
    return latest.gallons / days
