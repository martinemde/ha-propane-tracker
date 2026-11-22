"""Summarize local Recorder snapshots; no HA connection or writes."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import chain
from math import isfinite
from pathlib import Path

from custom_components.propane_tracker.measured import HorizontalTank


@dataclass(frozen=True)
class State:
    at: datetime
    value: float | None
    reported: datetime | None = None


def timestamp(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp needs a UTC offset: {text}")
    return dt.astimezone(UTC)


def utc(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def number(value: object) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if isfinite(n) else None


def json_rows(path: Path, entities: tuple[str, ...]) -> Iterable[tuple[str, State]]:
    with path.open() as stream:
        groups = json.load(stream)
    for group in groups:
        # Full /api/history/period is grouped; filtered exports may be flat.
        for row in group if isinstance(group, list) else [group]:
            entity = row["entity_id"]
            if entity in entities:
                yield entity, State(timestamp(row["last_updated"]), number(row["state"]))


def sqlite_rows(
    path: Path, entities: tuple[str, ...], start: datetime, end: datetime
) -> Iterable[tuple[str, State]]:
    # Select only the requested interval and one lead-in state per entity.
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(states)")}
        reported = "last_reported_ts" if "last_reported_ts" in columns else "NULL"
        for entity in entities:
            ids = db.execute("SELECT metadata_id FROM states_meta WHERE entity_id = ?", (entity,))
            metadata = ids.fetchone()
            if metadata is None:
                continue
            (meta_id,) = metadata
            first, last = start.timestamp(), end.timestamp()
            columns_sql = f"SELECT last_updated_ts, state, {reported} FROM states"
            anchor = db.execute(
                f"{columns_sql} WHERE metadata_id = ? AND last_updated_ts < ? "
                "ORDER BY last_updated_ts DESC, state_id DESC LIMIT 1",
                (meta_id, first),
            ).fetchall()
            bounded = db.execute(
                f"{columns_sql} WHERE metadata_id = ? AND last_updated_ts BETWEEN ? AND ? "
                "ORDER BY last_updated_ts, state_id",
                (meta_id, first, last),
            )
            for updated, state, endpoint in chain(anchor, bounded):
                # Never move an endpoint from after the cutoff onto the cutoff.
                observed = (
                    datetime.fromtimestamp(endpoint, UTC)
                    if endpoint is not None and updated < endpoint <= last
                    else None
                )
                yield entity, State(datetime.fromtimestamp(updated, UTC), number(state), observed)


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


def through(rows: list[State], end: datetime) -> int:
    return bisect_right(rows, end, key=lambda row: row.at)


def window_rows(rows: list[State], window: Window) -> tuple[State | None, list[State]]:
    i = bisect_left(rows, window.start, key=lambda row: row.at)
    j = bisect_left(rows, window.end, key=lambda row: row.at)
    return (rows[i - 1] if i else None), rows[i:j]


@dataclass
class Held:
    durations: dict[float | None, float]
    rows: int
    numeric_rows: int
    changes: int
    endpoints: int
    longest_silence: float
    valid: float

    @property
    def values(self) -> list[float]:
        return sorted(value for value in self.durations if value is not None)

    def mean(self) -> float:
        return sum(value * self.durations[value] for value in self.values) / self.valid

    def median(self) -> float:
        cumulative = 0.0
        for value in self.values:
            cumulative += self.durations[value]
            if cumulative >= self.valid / 2:
                return value
        raise ValueError("median needs positive numeric duration")


def held(rows: list[State], window: Window) -> Held:
    prior, inside = window_rows(rows, window)
    value = prior.value if prior else None
    cursor = window.start
    points = {window.start, window.end}
    durations = defaultdict(float)
    endpoints = set()
    changes = 0
    for row in inside:
        if row.at > cursor:
            durations[value] += (row.at - cursor).total_seconds()
        changes += value is not None and row.value is not None and value != row.value
        cursor, value = row.at, row.value
        points.add(cursor)
    durations[value] += (window.end - cursor).total_seconds()
    for row in ([prior] if prior else []) + inside:
        if row.reported and window.start <= row.reported <= window.end:
            endpoints.add(row.reported)
    points = sorted(points | endpoints)
    return Held(
        dict(durations),
        len(inside),
        sum(row.value is not None for row in inside),
        changes,
        len(endpoints),
        max((b - a).total_seconds() for a, b in zip(points, points[1:])),
        sum(duration for depth, duration in durations.items() if depth is not None),
    )


def meter_delta(rows: list[State], window: Window) -> tuple[float | None, str]:
    first, last = through(rows, window.start), through(rows, window.end)
    if not first:
        return None, "no baseline at start"
    values = [row.value for row in rows[first - 1 : last]]
    if any(value is None or value < 0 for value in values):
        return None, "unknown, unavailable, or invalid counter state"
    if any(b < a for a, b in zip(values, values[1:])):
        return None, "counter decrease/reset"
    return values[-1] - values[
        0
    ], f"counter rows at {utc(rows[first - 1].at)} → {utc(rows[last - 1].at)}"


def timespan(text: str) -> Window:
    a, b = map(timestamp, text.split("/"))
    if a >= b:
        raise ValueError("interval start must precede end")
    return Window(a, b)


def width(text: str) -> timedelta:
    match = re.fullmatch(r"([1-9][0-9]*)([smhd])", text)
    if not match:
        raise ValueError(f"invalid duration {text!r}; use e.g. 90m, 24h, or 3d")
    amount, unit = match.groups()
    return timedelta(seconds=int(amount) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit])


def report_window(
    rows: list[list[State]],
    window: Window,
    geometry: HorizontalTank,
    step_mm: float | None,
    kwh_per_gallon: float,
) -> None:
    depth, temp, quality = (held(series, window) for series in rows[:3])
    gap = depth.durations.get(None, 0)
    if not depth.valid:
        status = "no numeric depth"
    elif gap:
        status = "partial depth (explicit gap or missing baseline)"
    elif not depth.numeric_rows:
        status = "held depth only (no new numeric rows)"
    else:
        status = "numeric held depth; check freshness separately"
    print(
        f"- Depth: {status}; {depth.valid:.6f}/{window.seconds:.6f} s numeric, "
        f"{gap:.6f} s missing; {depth.numeric_rows}/{depth.rows} numeric/total rows, "
        f"{depth.changes} adjacent numeric changes."
    )
    print(
        f"- Depth evidence: {depth.endpoints} stored `last_reported_ts` endpoints in the "
        f"window; longest span without a recorded row/endpoint: {depth.longest_silence:.3f} s "
        "(includes boundary tails, not an outage test)."
    )
    if depth.valid:
        lo, hi = depth.values[0], depth.values[-1]
        smallest = min((b - a for a, b in zip(depth.values, depth.values[1:])), default=None)
        print(
            f"- Depth mean / median: {depth.mean():.5f} / {depth.median():.5f} in; "
            f"range {lo:.5f}–{hi:.5f} in ({len(depth.values)} distinct); modeled volume "
            f"range {geometry.gallons_at_depth(lo):.3f}–{geometry.gallons_at_depth(hi):.3f} US gal."
        )
        if smallest is not None:
            print(
                f"- Smallest observed depth spacing: {smallest * 25.4:.3f} mm "
                "(distinct held values, not a sensor specification)."
            )
        if step_mm is not None:
            at = depth.median()
            step = step_mm / 25.4
            other = at + step if at + step <= geometry.diameter else at - step
            if other >= 0:
                gallons = abs(geometry.gallons_at_depth(other) - geometry.gallons_at_depth(at))
                print(
                    f"- A {step_mm:g} mm step at this median is {gallons:.3f} US gal "
                    "in the tank model, not a detection threshold."
                )
    if temp.valid:
        print(
            f"- Sensor temperature: {temp.values[0]:g}–{temp.values[-1]:g} °F; "
            f"{temp.valid:.6f} s numeric, {temp.numeric_rows} numeric rows."
        )
    else:
        print("- Sensor temperature: no numeric coverage.")
    distribution = ", ".join(
        f"{value:g}% for {quality.durations[value]:.6f} s "
        f"({100 * quality.durations[value] / window.seconds:.1f}% of window)"
        for value in quality.values
    )
    print(
        f"- Quality: {distribution or 'no numeric coverage'}; "
        f"{quality.durations.get(None, 0):.6f} s missing."
    )
    delta, detail = meter_delta(rows[3], window)
    if delta is None:
        print(f"- Navien: no delta ({detail}).")
    else:
        suffix = "; no recorded change, not proof of no use" if delta == 0 else ""
        print(
            f"- Navien: {delta:.5f} kWh = {delta / kwh_per_gallon:.5f} US gal ({detail}{suffix})."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--json", type=Path, help="full-timestamp Recorder history JSON")
    source.add_argument("--sqlite", type=Path, help="local SQLite Recorder snapshot")
    parser.add_argument("--start", required=True, help="known start of export, including lead-in")
    parser.add_argument("--end", required=True, help="analysis cutoff, within the export")
    parser.add_argument("--settled-from", required=True, help="observed end of commissioning")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="START/END",
        help="known outage, delivery/settle period, or change of setup; repeatable",
    )
    parser.add_argument("--window", action="append", default=[], help="extra duration, e.g. 90m")
    parser.add_argument(
        "--interval",
        action="append",
        default=[],
        metavar="START/END",
        help="extra explicit interval; repeatable",
    )
    parser.add_argument("--blocks", type=int, default=1, help="nonoverlapping blocks per duration")
    parser.add_argument("--depth", required=True, help="liquid depth entity, in inches")
    parser.add_argument("--temperature", required=True, help="sensor temperature entity, in °F")
    parser.add_argument("--quality", required=True, help="reading quality entity, in percent")
    parser.add_argument("--meter", required=True, help="cumulative Navien propane energy, in kWh")
    parser.add_argument("--diameter-in", type=float, required=True, help="inside tank diameter")
    parser.add_argument("--straight-in", type=float, required=True, help="inside cylinder length")
    parser.add_argument(
        "--step-mm", type=float, help="evaluate a physical depth step at the median"
    )
    parser.add_argument("--kwh-per-gallon", type=float, default=26.81)
    args = parser.parse_args()
    try:
        start, end, settled = map(timestamp, (args.start, args.end, args.settled_from))
        floor = max(start, settled)
        entities = (args.depth, args.temperature, args.quality, args.meter)
        constants = [args.diameter_in, args.straight_in, args.kwh_per_gallon]
        if args.step_mm is not None:
            constants.append(args.step_mm)
        if floor >= end or args.blocks < 1 or len(set(entities)) != len(entities):
            raise ValueError("need a positive post-settle interval, blocks, and distinct entities")
        if any(not isfinite(value) or value <= 0 for value in constants):
            raise ValueError("tank dimensions, conversion, and optional step must be positive")
        geometry = HorizontalTank(args.diameter_in, args.straight_in)
        labels = ("24h", "3d", "7d", *args.window)
        windows = [("Available post-settle span", Window(floor, end))]
        for label in dict.fromkeys(labels):
            span = width(label)
            for block in range(args.blocks):
                name = label if args.blocks == 1 else f"{label} block {block + 1} (newest first)"
                windows.append((name, Window(end - span * (block + 1), end - span * block)))
        windows.extend(("Supplied interval", timespan(text)) for text in args.interval)
        excluded = [timespan(text) for text in args.exclude]
        raw = (
            json_rows(args.json, entities)
            if args.json
            else sqlite_rows(args.sqlite, entities, start, end)
        )
        by_entity = {entity: [] for entity in entities}
        for entity, row in raw:
            if row.at <= end:
                if (
                    entity == args.depth
                    and row.value is not None
                    and not 0 <= row.value <= args.diameter_in
                ):
                    row = State(row.at, None, row.reported)
                by_entity[entity].append(row)
        for series in by_entity.values():
            series.sort(key=lambda row: row.at)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, OverflowError) as exc:
        parser.error(str(exc))

    print("# Recorder window comparison")
    print(f"\nExport start: {utc(start)}; cutoff: {utc(end)}; settled from: {utc(settled)}.")
    print(
        f"Source: {'history JSON (no report endpoints)' if args.json else 'SQLite snapshot'}; "
        f"tank: {args.diameter_in:g} in ID × {args.straight_in:g} in straight section, "
        f"hemispherical ends; Navien: {args.kwh_per_gallon:g} kWh/US gal."
    )
    print(
        "Rows and stored endpoints are not BLE packet counts. Results describe held states "
        "and recorded counter changes; they do not select a window or estimate range burn."
    )
    for label, window in windows:
        print(f"\n## {label}: {utc(window.start)} → {utc(window.end)}\n")
        if window.start < floor or window.end > end:
            print("Insufficient duration within the export and post-settle bounds; not clipped.")
        elif any(window.start < x.end and x.start <= window.end for x in excluded):
            print("Overlaps a supplied exclusion (including the meter end boundary); not compared.")
        else:
            # Do not carry a pre-exclusion state into resumed data.
            resume = max((x.end for x in excluded if x.end <= window.start), default=None)
            eligible = [
                series if resume is None else [row for row in series if row.at >= resume]
                for series in by_entity.values()
            ]
            report_window(eligible, window, geometry, args.step_mm, args.kwh_per_gallon)


if __name__ == "__main__":
    main()
