"""Behavior of versioned, replayable propane usage accounting."""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from custom_components.propane_tracker.usage import (
    AcceptedInterval,
    InventoryBoundary,
    UsageError,
    UsagePoint,
    load_intervals,
    replay_usage,
    serialize_intervals,
)

START = datetime(2026, 11, 21, tzinfo=UTC)


def boundary(day, gallons, basis="tank-1:geometry-1"):
    return InventoryBoundary(START + timedelta(days=day), gallons, basis)


def test_rebuild_after_saving_out_of_order_intervals_and_retries(tmp_path):
    before_delivery = AcceptedInterval("burn", boundary(0, 48), boundary(1, 46), 0)
    delivery_only = AcceptedInterval("refill", boundary(1, 46), boundary(2, 86), 40)
    delivery_and_burn = AcceptedInterval("mixed", boundary(2, 86), boundary(3, 103), 20)
    intervals = (delivery_and_burn, before_delivery, delivery_only, before_delivery)
    expected = (
        UsagePoint("burn", boundary(1, 46).at, 2, 53.62),
        UsagePoint("refill", boundary(2, 86).at, 0, 53.62),
        UsagePoint("mixed", boundary(3, 103).at, 3, 134.05),
    )

    path = tmp_path / "accepted-usage.json"
    path.write_text(json.dumps(serialize_intervals(intervals)), encoding="utf-8")
    payload = json.loads(path.read_text(encoding="utf-8"))
    restored = load_intervals(payload)

    assert restored == (before_delivery, delivery_only, delivery_and_burn)
    assert serialize_intervals(reversed(intervals)) == serialize_intervals(restored) == payload
    assert replay_usage(intervals) == replay_usage(restored + restored) == expected


def test_delivery_on_fractional_boundary_is_not_burn():
    interval = AcceptedInterval("refill", boundary(0, 46.1), boundary(1, 46.3), 0.2)
    assert replay_usage([interval]) == (UsagePoint("refill", interval.end.at, 0, 0),)


def test_signed_net_transfer_is_not_burn():
    interval = AcceptedInterval("drain", boundary(0, 46), boundary(1, 40), -6)
    assert replay_usage([interval]) == (UsagePoint("drain", interval.end.at, 0, 0),)


def test_no_bridge_across_gaps_or_geometry_changes():
    first = AcceptedInterval("before-gap", boundary(0, 200), boundary(1, 199), 0)
    after_gap = AcceptedInterval("after-gap", boundary(3, 180), boundary(4, 179), 0)
    new_basis = "tank-1:geometry-2"
    new_geometry = AcceptedInterval(
        "new-geometry", boundary(4, 140, new_basis), boundary(5, 138, new_basis), 0
    )

    assert replay_usage([first, after_gap, new_geometry]) == (
        UsagePoint("before-gap", first.end.at, 1, 26.81),
        UsagePoint("after-gap", after_gap.end.at, 1, 53.62),
        UsagePoint("new-geometry", new_geometry.end.at, 2, 107.24),
    )
    with pytest.raises(UsageError):
        AcceptedInterval("crosses-geometry", after_gap.start, new_geometry.end, 0)


@pytest.mark.parametrize("inflow", [0, 3, None, float("nan")])
def test_negative_or_unknown_balance_cannot_be_accepted(inflow):
    with pytest.raises(UsageError):
        AcceptedInterval("unexplained-rise", boundary(0, 46), boundary(1, 50), inflow)


@pytest.mark.parametrize(
    ("interval_id", "start_day", "start_gallons", "end_day"),
    [("original", 2, 47, 3), ("overlap", 1, 49, 3), ("different-baseline", 2, 49, 3)],
)
def test_ambiguous_histories_do_not_replay(interval_id, start_day, start_gallons, end_day):
    original = AcceptedInterval("original", boundary(0, 50), boundary(2, 47), 0)
    other = AcceptedInterval(
        interval_id, boundary(start_day, start_gallons), boundary(end_day, 46), 0
    )
    with pytest.raises(UsageError):
        replay_usage([original, other])
    with pytest.raises(UsageError):
        serialize_intervals([original, other])


def test_boundary_timestamps_are_instants_with_a_timezone():
    end = boundary(1, 49)
    local = end.at.astimezone(timezone(timedelta(hours=-8)))
    same_end = InventoryBoundary(local, 49, end.basis_id)
    assert same_end == end
    with pytest.raises(UsageError):
        InventoryBoundary(local.replace(tzinfo=None), 49, end.basis_id)
    with pytest.raises(UsageError):
        AcceptedInterval("empty", end, same_end, 0)


def test_invalid_saved_input_is_not_used_as_a_zero_inflow():
    valid = AcceptedInterval("one", boundary(0, 50), boundary(1, 49), 0)
    payload = serialize_intervals([valid])
    with pytest.raises(UsageError):
        load_intervals({**payload, "version": 99})
    del payload["intervals"][0]["net_inflow_gal"]
    with pytest.raises(UsageError):
        load_intervals(payload)
