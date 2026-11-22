# Experimental propane depth averaging windows

**No window recommendation yet.** The first Recorder snapshot contains 1 h 51 min 53 s of post-settle depth. The 24 h, 3 d, and 7 d windows are all too short on data. Use the shorter windows to characterize noise, not to estimate range burn or set a quality or delivery threshold.

## First snapshot: 2026-11-22 01:26:31 UTC

Exclude commissioning before **2026-11-21 23:34:37.728686 UTC**. In the startup interval the sensor temperature fell from a peak of 62.6 to 42.8 °F as depth moved into the 21.9 in band. It also recorded 0% quality, missing depth, and a physically invalid 40.394 in depth. The exclusion ends at the observed stable band, not after a fixed startup delay. No delivery is indicated. The planned Core restart around 01:30 UTC is outside this snapshot; verify its actual bounds before analyzing later data.

| Window ending at cutoff | Numeric depth rows | Mean depth (in) | Median depth (in) | Time at 33% quality | Navien kWh | Navien US gal |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Available post-settle (111m53s) | 7 | 21.94585 | 21.92913 | 3.4% | 0.72729 | 0.02713 |
| 90 min | 6 | 21.94991 | 21.96850 | 4.2% | 0.09607 | 0.00358 |
| 60 min | 4 | 21.95557 | 21.96850 | 6.3% | 0 recorded change | 0 recorded change |
| 30 min | 1 | 21.96806 | 21.96850 | 12.5% | 0 recorded change | 0 recorded change |
| 15 min (held value only) | 0 | 21.96850 | 21.96850 | 25.1% | 0 recorded change | 0 recorded change |

- The two depths were 557 and 558 mm (21.929–21.969 in), with a **0.370 US gal** modeled difference at this fill level. The already-recorded `propane_remaining` template showed 199.26 and 199.63 US gal. This confirms the scale of a local depth step, not absolute level accuracy. One step was about **14 times** the entire post-settle Navien burn. There was one 0.001521 s `unavailable` depth interval; it is missing, not zero.
- The first and second nonoverlapping halves averaged 21.93536 and 21.95633 in: an apparent **rise** of 0.021 in, smaller than one step. The counter advanced by only 0.00358 US gal between the two half-window centers. The full-span 0.02713 US gal is on different boundaries and is not a residual against those averages.
- Recorded numeric sensor temperature held at 42.8 °F after settling. That is neither a measurement of bulk liquid temperature nor evidence for a temperature correction. Quality was mostly 67%; the 33% reading occupied 226 s of the settled span and was still present at cutoff. Neither value is a validated filter.
- Without a gap or restart, the first duration-complete windows would end at 23:34:37.728686 UTC on Nov 22, Nov 24, and Nov 28 respectively. This is elapsed time only, not evidence that any of those windows is suitable.

## Rerun the first snapshot

Run from the repository root. `uv` loads the locked development environment so the script can use the integration's own tank geometry. The initial notes and raw history are local artifacts (`/tmp/propane-initial-windows.md`, `/tmp/propane-initial-ha-history.json`); keep raw exports private and out of version control.

```sh
umask 077
base=sensor.master_patio_pro_check_universal
uv run --locked python -m scripts.compare_propane_windows \
  --json /tmp/propane-initial-ha-history.json \
  --start 2026-11-21T23:00:00Z --end 2026-11-22T01:26:31Z \
  --settled-from 2026-11-21T23:34:37.728686Z \
  --depth "${base}_tank_level" --temperature "${base}_temperature" \
  --quality "${base}_reading_quality" --meter sensor.navien_corrected_propane_energy_kwh \
  --diameter-in 29.602 --straight-in 63.5 --step-mm 1 \
  --window 90m --window 60m --window 30m --window 15m \
  --interval 2026-11-21T23:34:37.728686Z/2026-11-22T00:30:34.364343Z \
  --interval 2026-11-22T00:30:34.364343Z/2026-11-22T01:26:31Z \
  --interval 2026-11-22T00:02:36.046514Z/2026-11-22T00:58:32.682171Z \
  > /tmp/propane-initial-window-rerun.md
```

### Supported inputs

- **History JSON:** a flat array of rows or the grouped array from `/api/history/period`. Each row needs `entity_id`, `state`, and offset-aware `last_updated`. `last_changed` and attributes aren't needed. Do not request minimal or significant-change-only history. Use a lead-in before the earliest comparison because HA can synthesize a baseline at the request start. JSON does not supply report endpoints to this analysis.
- **SQLite:** in the command, replace `--json /tmp/propane-initial-ha-history.json` with `--sqlite /path/to/local-recorder-snapshot.sqlite`. Use an existing, consistent snapshot that includes committed WAL data, or a subset export with the same schema and a lead-in row for each entity. Do not copy only the main file of a running WAL database. The script opens the local file read-only and selects four entity IDs, rows with `last_updated_ts` inside `--start`/`--end`, and at most one earlier row per entity. Required columns: `states_meta(metadata_id, entity_id)` and `states(state_id, metadata_id, state, last_updated_ts)`. Optional: `states.last_reported_ts`. Timestamps are Unix seconds. Databases without Recorder metadata IDs and Recorder statistics exports are not supported.
- Both inputs assume liquid depth in inches, sensor temperature in °F, quality in percent, and a corrected cumulative Navien counter in kWh. Confirm units and geometry before analysis; the script does not read or repair HA setup. Supply actual snapshot bounds; the absence of later state changes cannot establish the export end. Run synthetic fixture checks with `uv run --locked pytest -q tests/test_compare_propane_windows.py`.

To collect a later JSON export, use the documented HA SSH add-on shell access. Run a bounded GET in the remote shell and save stdout locally with private permissions. Do not log the token or enable shell tracing. Replace `start` and `end` with the export bounds.

```sh
set -o pipefail
. /etc/profile.d/homeassistant.sh
start=2026-11-21T23:00:00Z
end=2026-11-22T01:26:31Z
base=sensor.master_patio_pro_check_universal
ids="${base}_tank_level,${base}_temperature,${base}_reading_quality,sensor.navien_corrected_propane_energy_kwh"
curl --fail --silent --show-error --get \
  -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
  --data-urlencode "filter_entity_id=$ids" --data-urlencode "end_time=$end" \
  "http://supervisor/core/api/history/period/$start" \
  | jq -c 'map([.[] | {entity_id,state,last_updated}])'
```

## How to compare later windows

1. Keep 24 h, 3 d, and 7 d as candidates. Set `--settled-from` to the observed end of commissioning. Use repeatable `--exclude START/END` for verified restarts, outages, deliveries and subsequent settling, or changes to setup. A window crossing an exclusion is not compared; later windows do not carry pre-exclusion baselines. Bounds outside the available post-settle export are marked insufficient rather than clipped. Report and keep any exclusions with the snapshot.
2. Run with `--blocks 3` (or the number available) to see nonoverlapping blocks **within** each duration. The three durations still overlap one another, as do the short trailing checks. Save exports before Recorder purges the raw history; if needed, analyze successive snapshots independently with each cutoff aligned to the block boundaries.
3. Check coverage and counts before comparing averages. Depth uses recorded states held over `[start,end)`, weighting by numeric duration. Unknown, unavailable, non-finite, physically impossible depths, and time without a baseline are gaps, not zero. Rows count events in that time range, not a carried baseline; adjacent numeric changes do not bridge an invalid row. The silence metric is the longest time inside a window without a row or stored report endpoint, including the boundary tails. It does not prove a gap in live monitoring. Recorder can store `last_reported_ts` as the last repeat-report timestamp for a row's interval; it is not a state transition or an independent sample and does not show the intervening report cadence. When an endpoint is later than the analysis cutoff it is omitted, not moved to the cutoff. On the live Mopeka path, quality or temperature traffic does not imply a new HA depth report. Check availability independently before trusting long held periods.
4. Use the corrected Navien counter on the same UTC boundaries: last state at or before each, with no interpolation. A reset, invalid state, or missing baseline suppresses the delta. A clean delta uses 26.81 kWh/US gal by default; no recorded change does not prove no gas was used. To compare changes between nonoverlapping depth summaries, use consistent representative times, e.g. the centers of well-covered equal blocks, and query that same center-to-center meter interval with `--interval START/END`. Never subtract full-block burn from a depth change measured on different boundaries.
5. Compare depth first. Check temperature and quality ranges, the local gallons per depth step, and repeatability over several separate windows and actual depletion. Depth measures occupied volume and moves with temperature even without gas use. A delivery must be corroborated separately. Do not choose a default, infer a range burner residual, or derive a temperature correction or cutoff from this startup snapshot.
