"""Identify measured propane refills in an offline replay, without confirming deliveries."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from math import isfinite
from statistics import median


@dataclass(frozen=True)
class RefillPolicy:
    """Required thresholds for coverage and stability, in time and US gallons."""

    stable_for: timedelta
    max_gap: timedelta
    max_spread_gallons: float
    min_rise_gallons: float

    def __post_init__(self) -> None:
        if self.stable_for <= timedelta(0) or self.max_gap <= timedelta(0):
            raise ValueError("stable_for and max_gap must be positive")
        if not (
            isfinite(self.min_rise_gallons) and 0 <= self.max_spread_gallons < self.min_rise_gallons
        ):
            raise ValueError("gallon thresholds must be finite, with 0 <= max_spread < min_rise")


@dataclass(frozen=True)
class RefillObservation:
    """One actual measured volume report; None means unusable or unavailable."""

    at: datetime
    gallons: float | None


@dataclass(frozen=True)
class RefillReset:
    """Discard history across a known sensor or tank change."""

    at: datetime


@dataclass(frozen=True)
class RefillBaseline:
    """First and last reports of a stable window and their window's median gallons."""

    start: datetime
    end: datetime
    gallons: float


@dataclass(frozen=True)
class RefillCandidate:
    """A supported increase in measured inventory, not a ledger entry."""

    pre: RefillBaseline
    post: RefillBaseline

    @property
    def id(self) -> str:
        """Identify the transition by the two bounding reports in UTC."""
        return f"{self.pre.end.isoformat()}/{self.post.start.isoformat()}"

    @property
    def estimated_gallons(self) -> float:
        """Return the net measured gain, without correcting for burn or temperature."""
        return self.post.gallons - self.pre.gallons


class UncertaintyReason(StrEnum):
    INVALID_OBSERVATION = "invalid_observation"
    GAP = "gap"
    RESET = "reset"
    NO_BASELINE = "no_baseline"
    UNSETTLED_RISE = "unsettled_rise"


@dataclass(frozen=True)
class RefillUncertainty:
    """An interval (or an instant when start == end) without sufficient evidence."""

    reason: UncertaintyReason
    start: datetime
    end: datetime


@dataclass(frozen=True)
class RefillReplay:
    """The first supported candidate, if any, and preceding uncertainty."""

    candidate: RefillCandidate | None
    uncertainties: tuple[RefillUncertainty, ...]


def _as_utc(at: datetime) -> datetime:
    if at.utcoffset() is None:
        raise ValueError("replay timestamps must be timezone-aware")
    return at.astimezone(UTC)


def replay_refill(
    events: Iterable[RefillObservation | RefillReset],
    *,
    start: datetime,
    policy: RefillPolicy,
) -> RefillReplay:
    """Find at most one candidate from ordered reports at or after the replay start.

    Use only actual timestamps, without interpolation or carrying readings across gaps.
    A stable window spans stable_for and fits within max_spread_gallons. After a stable
    pre-baseline, every report in the post window must clear min_rise_gallons over the
    pre median. Stop on the first such window; appending reports cannot revise it.
    """
    start = _as_utc(start)
    last_at = segment_start = start
    previous: datetime | None = None
    window: deque[tuple[datetime, float]] = deque()
    baseline: RefillBaseline | None = None
    elevated = False
    uncertainties: list[RefillUncertainty] = []

    for event in events:
        at = _as_utc(event.at)
        if at < start:
            continue
        if at < last_at or (isinstance(event, RefillObservation) and at == previous):
            raise ValueError("replay events must be ordered, without duplicate observations")
        last_at = at

        if isinstance(event, RefillReset):
            uncertainties.append(RefillUncertainty(UncertaintyReason.RESET, at, at))
            gallons = None
        else:
            if at - (prior := previous or segment_start) > policy.max_gap:
                uncertainties.append(RefillUncertainty(UncertaintyReason.GAP, prior, at))
                window.clear()
                baseline = None
                segment_start = at
            gallons = event.gallons
            if gallons is None or not isfinite(gallons) or gallons < 0:
                uncertainties.append(
                    RefillUncertainty(UncertaintyReason.INVALID_OBSERVATION, at, at)
                )
                gallons = None

        if gallons is None:
            window.clear()
            baseline = previous = None
            elevated = False
            segment_start = at
            continue

        previous = at
        window.append((at, gallons))
        # Keep the shortest suffix spanning stable_for, including the report at its left edge.
        while len(window) > 1 and at - window[1][0] >= policy.stable_for:
            window.popleft()

        elevated = baseline is not None and gallons - baseline.gallons >= policy.min_rise_gallons
        if at - window[0][0] < policy.stable_for:
            continue
        levels = [level for _, level in window]
        low = min(levels)
        if max(levels) - low > policy.max_spread_gallons:
            continue
        settled = RefillBaseline(window[0][0], at, median(levels))
        if not elevated:
            baseline = settled
        elif baseline is not None and low - baseline.gallons >= policy.min_rise_gallons:
            return RefillReplay(RefillCandidate(baseline, settled), tuple(uncertainties))

    if baseline is None:
        uncertainties.append(
            RefillUncertainty(UncertaintyReason.NO_BASELINE, segment_start, last_at)
        )
    elif elevated:
        uncertainties.append(
            RefillUncertainty(UncertaintyReason.UNSETTLED_RISE, baseline.end, last_at)
        )
    return RefillReplay(None, tuple(uncertainties))
