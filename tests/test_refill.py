"""Replay refill candidates without a live sensor or a ledger."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from custom_components.propane_tracker.refill import (
    RefillObservation,
    RefillPolicy,
    RefillReset,
    RefillUncertainty,
    UncertaintyReason,
    replay_refill,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
POLICY = RefillPolicy(
    stable_for=timedelta(minutes=4),
    max_gap=timedelta(minutes=3),
    max_spread_gallons=0.75,
    min_rise_gallons=8,
)


def at(minutes):
    return START + timedelta(minutes=minutes)


def observations(*readings):
    return [RefillObservation(at(t), gallons) for t, gallons in readings]


def replay(events, *, start=START, policy=POLICY):
    return replay_refill(events, start=start, policy=policy)


@pytest.mark.parametrize(
    "trace",
    [
        [(0, 100), (2, 100.5), (4, 99.9), (6, 100.1), (8, 100.3), (10, 100)],
        [(0, 100), (2, 100.5), (4, 99.9), (6, 148), (8, 100.3), (10, 100), (12, 100)],
        [(0, 100), (2, 100), (4, 100), (6, 106), (8, 106), (10, 106)],
    ],
    ids=["jitter", "one-spike", "too-small"],
)
def test_no_refill_in_covered_trace(trace):
    result = replay(observations(*trace))
    assert result.candidate is None
    assert result.uncertainties == ()


@pytest.mark.parametrize("post_gallons", [122, 199.2], ids=["partial", "full"])
def test_rise_between_stable_baselines(post_gallons):
    result = replay(
        observations(
            (0, 100),
            (2, 100.3),
            (4, 99.9),
            (6, 113),
            (8, post_gallons),
            (10, post_gallons + 0.3),
            (12, post_gallons - 0.1),
        )
    )
    candidate = result.candidate
    assert candidate is not None
    assert (candidate.pre.start, candidate.pre.end) == (at(0), at(4))
    assert (candidate.post.start, candidate.post.end) == (at(8), at(12))
    assert candidate.pre.gallons == 100
    assert candidate.post.gallons == post_gallons
    assert candidate.estimated_gallons == pytest.approx(post_gallons - 100)
    assert result.uncertainties == ()


@pytest.mark.parametrize(
    "post",
    [[(6, 120), (8, 120)], [(6, 120), (8, 123), (10, 125)]],
    ids=["too-short", "sustained-but-unstable"],
)
def test_unsettled_rise_is_unknown_not_a_candidate(post):
    result = replay(observations((0, 100), (2, 100), (4, 100), *post))
    assert result.candidate is None
    assert result.uncertainties == (
        RefillUncertainty(UncertaintyReason.UNSETTLED_RISE, at(4), at(post[-1][0])),
    )


def test_insufficient_history_is_unknown():
    result = replay(observations((0, 100), (2, 100)))
    assert result.candidate is None
    assert result.uncertainties == (RefillUncertainty(UncertaintyReason.NO_BASELINE, at(0), at(2)),)


def test_one_reading_cannot_establish_the_pre_baseline():
    result = replay(observations((0, 100), (2, 121), (4, 121), (6, 121)))
    assert result.candidate is None
    assert result.uncertainties == ()


@pytest.mark.parametrize(
    "prefix, interruption, reason, bounds",
    [
        ([], [], UncertaintyReason.GAP, (4, 20)),
        ([(6, 120), (8, 120)], [], UncertaintyReason.GAP, (8, 20)),
        ([(6, 120)], [RefillReset(at(8))], UncertaintyReason.RESET, (8, 8)),
        ([(6, 120)], observations((8, None)), UncertaintyReason.INVALID_OBSERVATION, (8, 8)),
    ],
    ids=["jump-across-gap", "gap-during-rise", "reset", "invalid"],
)
def test_interruptions_require_a_fresh_baseline(prefix, interruption, reason, bounds):
    events = observations((0, 100), (2, 100), (4, 100), *prefix) + interruption
    # Use a close next reading for explicit interruptions: it would not be a gap.
    post = 20 if not interruption else 10
    events += observations((post, 120), (post + 2, 120), (post + 4, 120))
    result = replay(events)
    assert result.candidate is None
    assert result.uncertainties == (RefillUncertainty(reason, at(bounds[0]), at(bounds[1])),)

    # A later, independent rise can still become a candidate.
    later = replay(events + observations((post + 6, 142), (post + 8, 142), (post + 10, 142)))
    assert later.uncertainties == result.uncertainties
    assert later.candidate.estimated_gallons == 22
    assert later.candidate.pre.start == at(post)


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf")])
def test_bad_measurement_is_unknown(bad):
    result = replay(observations((0, 100), (2, 100), (4, 100), (6, bad)))
    assert result.candidate is None
    assert result.uncertainties[0] == RefillUncertainty(
        UncertaintyReason.INVALID_OBSERVATION, at(6), at(6)
    )


def test_explicit_start_excludes_commissioning():
    events = observations((0, 50), (2, 50), (4, 50), (6, 100), (8, 100), (10, 100))
    assert replay(events).candidate is not None
    result = replay(events, start=at(6))
    assert result.candidate is None
    assert result.uncertainties == ()


def test_same_candidate_on_replay_with_a_longer_tail_or_equivalent_timezone():
    events = observations((0, 100), (2, 100), (4, 100), (6, 125), (8, 125), (10, 125))
    result = replay(events)
    assert result.candidate is not None
    assert result == replay(iter(events))
    more = observations((12, 148), (14, 148), (16, 148))
    assert result == replay(events + more)
    offset = timezone(timedelta(hours=-7))
    shifted = [replace(event, at=event.at.astimezone(offset)) for event in events]
    assert result.candidate.id == replay(shifted).candidate.id


def test_explicit_policy_changes_detection():
    events = observations((0, 100), (2, 100), (4, 100), (6, 110), (8, 110), (10, 110))
    assert replay(events).candidate is not None
    assert replay(events, policy=replace(POLICY, min_rise_gallons=12)).candidate is None
    with pytest.raises(ValueError):
        replace(POLICY, min_rise_gallons=0.5)
