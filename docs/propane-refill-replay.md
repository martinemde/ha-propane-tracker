# Offline propane refill replay

`replay_refill` in `custom_components/propane_tracker/refill.py` finds the first
supported rise in one replay of measured US gallons for one tank. It does not
read Home Assistant, write a delivery, or confirm that any propane was delivered.

## Replay boundaries and results

Pass timezone-aware events in order, with no duplicate observation timestamps in
an uninterrupted segment. Unordered or naive timestamps raise `ValueError`.
Use actual reports, not repeated values inferred from silence in Recorder.
An unavailable or rejected reading is an observation with
`gallons=None`. Convert depth to gallons and reject physically impossible depth
before replay. Repeated values are fine when they are distinct reports.

Set `start` to the beginning of the settled period to exclude commissioning. If
the sensor moves or the geometry changes during replay, add a `RefillReset` and
resume observations after settling. A reset, an invalid reading, or a gap longer
than `max_gap` discards the baseline. The first stable level after an interruption
can be a new baseline, but cannot establish an increase across the interruption.
A gap can hide a delivery; the detector cannot say whether one occurred.

A candidate compares two stable windows. Each window must span `stable_for` on
actual reports and have a max-minus-min spread no larger than `max_spread_gallons`.
Every post-window report must be at least `min_rise_gallons` above the pre median.
The candidate carries both windows' bounds, the difference of their medians, and
an ID based on the last pre report and first stable post report in UTC. Those two
reports bracket the transition; neither is a delivery timestamp. The estimate is
a net measured volume change, not a receipt quantity or a correction for burn or
temperature. Replaying the same data and policy yields the same candidate, and
appending reports does not revise it. Later events are not examined after the
first candidate; use separate replay ranges to investigate subsequent rises.

With no candidate, an empty uncertainty list means no rise met the supplied policy
in the covered data. An interruption, a missing baseline at the end, or a rise
still waiting for a stable post-window is reported separately as uncertainty.

## Choosing a policy later

There are no production defaults. Use the settled history from Skift #3
([averaging window analysis](propane-window-analysis.md)) before choosing values:

- Use observed report cadence and independently verified outages for `max_gap`.
  A Recorder state held for hours is not evidence of continuous reports.
- Use repeated non-delivery intervals to bound quantization, noise, and drift in
  gallons at different tank levels. Use that evidence to choose
  `max_spread_gallons` and a minimum rise above ordinary changes.
- Compare settled segments and any independently verified deliveries to judge
  `stable_for`. It must reject brief spikes and allow the readings after a real
  fill to settle. Do not use multi-day burn windows as fill thresholds by default.

A known sensor move must be marked explicitly: measurements alone cannot reliably
distinguish it from a fill. Skift #4 retains policy calibration, live persistence
and review, and confirmation. The synthetic test policy is not a recommendation.
