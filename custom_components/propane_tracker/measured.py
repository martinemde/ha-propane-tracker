"""Occupied propane volume from a liquid depth, independent of the ledger."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import acos, isfinite, pi, sqrt

from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, UnitOfLength
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_state_report_event,
)
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import DistanceConverter

CUBIC_INCHES_PER_GALLON = 231
MAX_REPORT_AGE = timedelta(hours=2)


@dataclass(frozen=True)
class HorizontalTank:
    """Cylinder with two hemispherical ends, dimensions in internal inches."""

    diameter: float
    straight_length: float

    @property
    def capacity(self) -> float:
        """Return water capacity in US gallons."""
        r = self.diameter / 2
        return pi * r**2 * (self.straight_length + 4 * r / 3) / CUBIC_INCHES_PER_GALLON

    def gallons_at_depth(self, depth: float) -> float | None:
        """Return occupied volume, or None for a physically impossible depth."""
        if not isfinite(depth) or not 0 <= depth <= self.diameter:
            return None
        r = self.diameter / 2
        # Calculate the smaller segment, then use symmetry above half full.
        h = min(depth, self.diameter - depth)
        x = r - h
        segment = r**2 * acos(x / r) - x * sqrt(h * (2 * r - h))
        heads = pi * h**2 * (r - h / 3)
        gallons = (segment * self.straight_length + heads) / CUBIC_INCHES_PER_GALLON
        return self.capacity - gallons if depth > r else gallons

    def gallons_from_state(self, state: State) -> float | None:
        """Read the source's own HA-reported unit, not the system unit."""
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if unit not in DistanceConverter.VALID_UNITS:
            return None
        try:
            depth = float(state.state)
        except ValueError:
            return None
        return self.gallons_at_depth(DistanceConverter.convert(depth, unit, UnitOfLength.INCHES))


class MeasuredInventory:
    """Follow a live depth entity, expiring a report even if no new event arrives."""

    def __init__(self, hass: HomeAssistant, source: str, geometry: HorizontalTank) -> None:
        self.hass = hass
        self.source = source
        self.geometry = geometry
        self.gallons: float | None = None
        self._cancel_expiry: Callable[[], None] | None = None

    @callback
    def async_start(self, notify: Callable[[], None]) -> Callable[[], None]:
        """Watch state changes as well as identical repeat reports."""

        @callback
        def refresh() -> None:
            state = self.hass.states.get(self.source)
            gallons = None
            if state is not None:
                expiry = state.last_reported + MAX_REPORT_AGE
                if expiry > dt_util.utcnow():
                    gallons = self.geometry.gallons_from_state(state)
                    if gallons is not None and self._cancel_expiry is None:
                        self._cancel_expiry = async_track_point_in_utc_time(
                            self.hass, expire, expiry
                        )
            if gallons != self.gallons:
                self.gallons = gallons
                notify()

        @callback
        def expire(_: datetime) -> None:
            self._cancel_expiry = None
            refresh()

        @callback
        def on_report(_: Event) -> None:
            refresh()

        unsub_change = async_track_state_change_event(self.hass, self.source, on_report)
        unsub_report = async_track_state_report_event(self.hass, self.source, on_report)
        refresh()

        @callback
        def stop() -> None:
            unsub_change()
            unsub_report()
            if self._cancel_expiry is not None:
                self._cancel_expiry()
                self._cancel_expiry = None

        return stop
