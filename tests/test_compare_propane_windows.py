"""Fixture checks for the offline Recorder comparison."""

import json
import sqlite3
from contextlib import closing

import pytest

from scripts.compare_propane_windows import (
    State,
    Window,
    held,
    json_rows,
    meter_delta,
    sqlite_rows,
    timestamp,
)


def t(second: int):
    return timestamp(f"2026-11-22T00:00:{second:02d}Z")


@pytest.mark.parametrize("nested", [False, True])
def test_weighted_history_keeps_gaps_and_held_only_tails(tmp_path, nested):
    rows = [
        {"entity_id": "sensor.depth", "last_updated": t(at).isoformat(), "state": value}
        for at, value in [(1, "20"), (3, "unavailable"), (4, "22"), (5, "22"), (10, "23")]
    ]
    fixture = tmp_path / "history.json"
    fixture.write_text(json.dumps([rows] if nested else rows))
    depth = [state for _, state in json_rows(fixture, ("sensor.depth",))]
    summary = held(depth, Window(t(0), t(10)))
    assert summary.durations == {None: 2.0, 20.0: 2.0, 22.0: 6.0}
    assert (summary.rows, summary.numeric_rows, summary.changes) == (4, 3, 0)
    assert summary.mean() == 21.5
    assert summary.median() == 22
    tail = held(depth, Window(t(6), t(10)))
    assert (tail.valid, tail.numeric_rows, tail.longest_silence) == (4, 0, 4)


def test_counter_uses_exact_end_and_never_repairs_missing_or_reset():
    window = Window(t(2), t(8))
    baseline, end = State(t(1), 10), State(t(8), 12)
    assert meter_delta([baseline, end, State(t(9), 15)], window)[0] == 2
    assert meter_delta([baseline, end], Window(t(2), t(7)))[0] == 0
    assert meter_delta([end], window)[0] is None
    assert meter_delta([baseline, State(t(4), None), end], window)[0] is None
    assert meter_delta([baseline, State(t(4), 1), end], window)[0] is None


def test_sqlite_anchor_endpoints_and_cutoff(tmp_path):
    path = tmp_path / "Recorder snapshot.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE states_meta (metadata_id INTEGER, entity_id TEXT);
            INSERT INTO states_meta VALUES (1, 'sensor.depth'), (2, 'sensor.unrelated');
            CREATE TABLE states (state_id INTEGER PRIMARY KEY, metadata_id INTEGER,
                last_updated_ts REAL, last_reported_ts REAL, state TEXT);
        """)
        db.executemany(
            "INSERT INTO states VALUES (?, ?, ?, ?, ?)",
            [
                (i, meta, t(u).timestamp(), t(r).timestamp() if r else None, str(value))
                for i, (meta, u, r, value) in enumerate(
                    [
                        (1, 1, None, 19),
                        (1, 2, 5, 20),
                        (1, 7, 18, 22),
                        (1, 19, None, 23),
                        (2, 6, None, 200),
                    ]
                )
            ],
        )
    before = path.read_bytes()
    depth = [state for _, state in sqlite_rows(path, ("sensor.depth",), t(4), t(12))]
    assert depth == [State(t(2), 20, t(5)), State(t(7), 22)]
    summary = held(depth, Window(t(4), t(12)))
    assert summary.durations == {20: 3, 22: 5}
    assert (summary.numeric_rows, summary.endpoints, summary.longest_silence) == (1, 1, 5)
    assert path.read_bytes() == before
