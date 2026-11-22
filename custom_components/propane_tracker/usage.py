"""Replay accepted usage intervals for one tank without inferring missing observations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from math import isfinite
from typing import Any

INTERVAL_FORMAT_VERSION = 1
_KWH_PER_US_GALLON = Fraction("26.81")


class UsageError(ValueError):
    """An interval or history cannot be accepted without ambiguity."""


def _require_id(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise UsageError(f"{field} must be a nonempty string")


def _gallons(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not isfinite(value):
        raise UsageError(f"{field} must be a known finite number in US liquid gallons")
    return float(value) if value else 0.0


@dataclass(frozen=True, slots=True)
class InventoryBoundary:
    """Accepted inventory at one instant, on a caller-identified tank/geometry basis.

    Start a new basis when inventory observations are no longer comparable,
    such as after a change in tank geometry or measurement calibration.
    """

    at: datetime
    inventory_gal: float
    basis_id: str

    def __post_init__(self) -> None:
        _require_id(self.basis_id, "basis_id")
        if not isinstance(self.at, datetime) or self.at.utcoffset() is None:
            raise UsageError("inventory boundary must have an offset-aware timestamp")
        object.__setattr__(self, "at", self.at.astimezone(UTC))
        gallons = _gallons(self.inventory_gal, "inventory_gal")
        if gallons < 0:
            raise UsageError("inventory_gal cannot be negative")
        object.__setattr__(self, "inventory_gal", gallons)


@dataclass(frozen=True, slots=True)
class AcceptedInterval:
    """A validated balance for (start, end] with stable identity.

    Net inflow is the known sum of physical transfers in that interval, in US
    liquid gallons on the boundaries' basis. Give zero only for known no net
    transfer; a negative transfer is allowed (e.g. a drain), but negative burn
    is not. The caller validates observations and attributes transfers before
    constructing intervals. Receipt amounts or dates alone do not do that.
    """

    interval_id: str
    start: InventoryBoundary
    end: InventoryBoundary
    net_inflow_gal: float

    def __post_init__(self) -> None:
        _require_id(self.interval_id, "interval_id")
        if self.start.at >= self.end.at:
            raise UsageError(f"interval {self.interval_id} must end after its start")
        if self.start.basis_id != self.end.basis_id:
            raise UsageError(f"interval {self.interval_id} crosses inventory bases")
        object.__setattr__(self, "net_inflow_gal", _gallons(self.net_inflow_gal, "net_inflow_gal"))
        if self._balance() < 0:
            raise UsageError(f"interval {self.interval_id} has a negative usage balance")

    def _balance(self) -> Fraction:
        # Calculate on the supplied decimals: binary float cancellation can turn
        # a delivery-only interval into apparent burn or a negative balance.
        return (
            Fraction(str(self.start.inventory_gal))
            + Fraction(str(self.net_inflow_gal))
            - Fraction(str(self.end.inventory_gal))
        )


@dataclass(frozen=True, slots=True)
class UsagePoint:
    """Usage for one interval and total usage through its UTC end."""

    interval_id: str
    end: datetime
    used_gal: float
    cumulative_kwh: float


def _ordered_intervals(intervals: Iterable[AcceptedInterval]) -> tuple[AcceptedInterval, ...]:
    by_id: dict[str, AcceptedInterval] = {}
    for interval in intervals:
        if by_id.setdefault(interval.interval_id, interval) != interval:
            raise UsageError(f"conflicting records for interval {interval.interval_id}")
    ordered = tuple(sorted(by_id.values(), key=lambda interval: interval.start.at))
    for previous, current in zip(ordered, ordered[1:]):
        if previous.end.at > current.start.at:
            raise UsageError(f"intervals {previous.interval_id} and {current.interval_id} overlap")
        if (
            previous.end.at == current.start.at
            and previous.end.basis_id == current.start.basis_id
            and previous.end != current.start
        ):
            raise UsageError(f"interval {current.interval_id} has a conflicting start boundary")
    return ordered


def replay_usage(intervals: Iterable[AcceptedInterval]) -> tuple[UsagePoint, ...]:
    """Rebuild a cumulative series from zero, counting identical retries once.

    No usage is assigned between intervals. An inconsistent history fails as a
    whole rather than returning partial totals or accepting a revision by order.
    """
    total_gal = Fraction(0)
    points = []
    for interval in _ordered_intervals(intervals):
        used_gal = interval._balance()
        total_gal += used_gal
        points.append(
            UsagePoint(
                interval.interval_id,
                interval.end.at,
                float(used_gal),
                float(total_gal * _KWH_PER_US_GALLON),
            )
        )
    return tuple(points)


def _serialize_boundary(boundary: InventoryBoundary) -> dict[str, Any]:
    return {
        "at": boundary.at.isoformat(),
        "inventory_gal": boundary.inventory_gal,
        "basis_id": boundary.basis_id,
    }


def serialize_intervals(intervals: Iterable[AcceptedInterval]) -> dict[str, Any]:
    """Build a versioned JSON payload of inputs only, not a running counter."""
    return {
        "version": INTERVAL_FORMAT_VERSION,
        "intervals": [
            {
                "interval_id": interval.interval_id,
                "start": _serialize_boundary(interval.start),
                "end": _serialize_boundary(interval.end),
                "net_inflow_gal": interval.net_inflow_gal,
            }
            for interval in _ordered_intervals(intervals)
        ],
    }


def _load_boundary(data: dict[str, Any]) -> InventoryBoundary:
    return InventoryBoundary(
        datetime.fromisoformat(data["at"]), data["inventory_gal"], data["basis_id"]
    )


def load_intervals(payload: dict[str, Any]) -> tuple[AcceptedInterval, ...]:
    """Load saved intervals with the same checks as a new history."""
    if not isinstance(payload, dict) or type(payload.get("version")) is not int:
        raise UsageError("interval history must have an integer format version")
    if payload["version"] != INTERVAL_FORMAT_VERSION:
        raise UsageError(f"unsupported interval format version: {payload['version']}")
    if not isinstance(payload.get("intervals"), list):
        raise UsageError("interval history must contain an intervals list")
    try:
        return _ordered_intervals(
            AcceptedInterval(
                record["interval_id"],
                _load_boundary(record["start"]),
                _load_boundary(record["end"]),
                record["net_inflow_gal"],
            )
            for record in payload["intervals"]
        )
    except (KeyError, TypeError, ValueError) as err:
        raise UsageError(f"invalid saved interval history: {err}") from err
