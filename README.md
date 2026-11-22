# Propane Tracker for Home Assistant

Propane Tracker models a propane tank the way Home Assistant models a home
battery: a delivery is inflow, a metered appliance is outflow, and the level in
between is tracked continuously. Home Assistant has no storage concept for a
fuel source — its battery flow is hardwired to electricity — so this integration
builds one.

It is intended for propane bills that arrive days or weeks apart. Enter the
ticket date, the gallons delivered, and what the delivery cost, then press one
button.

## How the tank is tracked

Without a liquid-depth sensor, Propane Tracker uses this ledger model:

- **A fill re-anchors the model.** A delivery driver stops at 80% of water
  capacity, so a completed fill puts the tank at a known quantity. Error cannot
  accumulate past a delivery.
- **A meter drains it.** Point the integration at a `total_increasing` sensor
  measuring propane burned by one appliance — an ESPHome water heater's
  accumulated energy, for example — and its readings are converted to gallons
  and subtracted from the level.
- **Each fill reconciles the rest.** Gallons on the ticket say how empty the
  tank really was. The gap between that and the modelled level is burn from
  appliances with no meter, like a range. It is recorded as a statistic on the
  delivery date, the way an account is reconciled against a statement.

Between fills the level therefore reads slightly high, by whatever the unmetered
appliances burned. It is a floor on consumption, not a measurement, and it is
corrected at every delivery.

## Install with HACS

1. In HACS, open **Custom repositories**.
2. Add `https://github.com/martinemde/ha-propane-tracker` as an
   **Integration** repository.
3. Install **Propane Tracker** and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**, search for
   **Propane Tracker**, and describe the tank.

Setup asks for the tank's water capacity, the percentage the driver fills it to,
an optional consumption meter entity, and how many of that meter's units equal
one gallon of propane (26.81 for a kWh sensor, from 91,502 BTU per gallon).
All four are editable later under the integration's **Configure** button; the
ledger is unaffected.

The units-per-gallon value is also the calibration knob. If reconciliation
residuals are consistently negative, the meter reads high — raise it.

## Optional measured inventory

In **Configure**, select a sensor that reports **liquid depth from the bottom**
of a horizontal cylindrical tank with hemispherical ends, such as tank level
from Home Assistant's native Mopeka integration. Enter the inside diameter and
the straight cylinder length (excluding the heads), both in inches. The
sensor's own distance unit is converted automatically. Tanks with other head
shapes, vertical tanks, free-space distance, and percentage sensors do not fit
this model.

For example, a nominal 250 US gal tank with a 30 in outside diameter, 0.199 in
shell, and a 63.5 in straight section has a 29.602 in inside diameter. Using the
same radius for both heads models a 248.0 US gal water capacity (about 0.8%
below nominal). At 557 mm, it reports 199.3 US gal or 80.35% of water capacity.
The code does not rescale the reading to match the nameplate or the usual 80%
fill target.

The measured entities work immediately, without a fill or appliance meter.
They go unavailable on invalid values, a missing source, or two hours without
an HA depth report. An unchanged value reported again is fresh. The measurements
track occupied liquid volume, including temperature expansion; they are **not**
usage meters. Existing modeled entities, delivery history, and the Energy
configuration are unchanged.

If replacing a custom inventory helper, compare it to the new measured entities
on a settled reading first. Point dashboard cards and automations to the new
entities before disabling the old helper. Do not disable the depth sensor.

## Entities

The integration creates one Propane device:

| Entity | What it is |
| --- | --- |
| `date.propane_delivery_date` | Ticket date input |
| `number.propane_gallons_delivered` | Gallons input |
| `number.propane_invoice_total` | What the delivery cost |
| `button.propane_record_delivery` | Commit the ticket |
| `sensor.propane_remaining` | Measured liquid inventory (optional) |
| `sensor.propane_measured_tank_level` | Measured percent of geometric water capacity (optional) |
| `sensor.propane_tank_level` | Modeled gallons in the tank, distinct from measured inventory |
| `sensor.propane_tank_gauge` | The same level as the tank's own gauge reads it |
| `sensor.propane_metered_burn` | Cumulative gallons the meter has seen |
| `sensor.propane_days_remaining` | Days left at the last fill-cycle burn rate (no depth sensor configured) |
| `sensor.propane_total_delivered` | Lifetime gallons delivered |
| `sensor.propane_total_spend` | Lifetime commodity spend |
| `sensor.propane_last_delivery` | Newest ticket, with its details as attributes |

Without a depth sensor, `sensor.propane_days_remaining` divides the modeled level
by gallons per day across the most recent fill cycle. This estimate uses two
consecutive tickets and includes unmetered burn. If a depth sensor is configured,
the estimate is unavailable because receipts do not establish fill cycles.

## Record a delivery

Set the date, gallons, and invoice total, then press **Record delivery**. The
per-gallon price is derived and shown as an attribute.

For automations or historical imports, call `propane_tracker.record_delivery`
with `delivery_date`, `gallons`, and either `cost` or `price_per_gallon`.

Both methods save the receipt to delivery history and update purchase volume and
cost statistics. If a depth sensor is configured, a receipt neither records a
physical fill nor resets the modeled level, even when the sensor is unavailable.
Without a depth sensor, only the newest receipt re-anchors the modeled tank.

You can enter older receipts after newer ones. The ledger sorts them by date and
recalculates later cumulative statistics. Only one receipt per calendar date is
accepted, which also protects against double taps.

## Energy dashboard

The integration publishes three external long-term statistics, each dated on
the delivery ticket:

- `propane_tracker:propane_delivered` — cumulative volume delivered (inflow)
- `propane_tracker:propane_cost` — cumulative commodity spend
- `propane_tracker:propane_unmetered` — volume reconciled at each fill

**Do not add `propane_delivered` as a gas source.** It is propane going into the
tank, not propane burned, and the Energy dashboard would show a several hundred
gallon consumption spike on delivery day and nothing in between. Use your
appliance's own energy sensor as the gas source instead, and add
`propane_tracker:propane_unmetered` as a second gas source if you want the
unmetered remainder counted too.

Volume statistics are stored in liters because Home Assistant does not accept
US gallons for a volume statistic. Entry controls and sensors stay in gallons.

### Upgrading from 0.1.x

0.1.x published deliveries as consumption under `propane_tracker:propane_usage`.
That series is inflow mislabeled as outflow, so it is cleared during migration
and rebuilt from the same ledger under `propane_tracker:propane_delivered`. If
it was configured as your Energy dashboard gas source, replace it as described
above. Ledger data is untouched, and per-gallon prices already recorded are
converted to invoice totals on load.

## Where the history lives

The delivery history is integration-owned data in Home Assistant's `.storage`
directory under the key `propane_tracker.ledger`, alongside the tank's meter
totals and fill anchor. Recorder statistics are a derived projection, not the
only copy. On startup the integration rebuilds them from the ledger, including
after a Recorder reset or a historical backfill.

Meter readings are batched to disk rather than written on every reading. A hard
restart can lose up to a minute of them, which costs a fraction of a gallon of
accuracy and is erased by the next fill.

The ledger survives restarts, integration upgrades, and HACS reinstalls because
HACS replaces code under `custom_components`, not Home Assistant's `.storage`
data. It is still local data: include the Home Assistant configuration directory
in normal backups. Removing the integration does not intentionally erase the
ledger, but deleting or restoring `.storage` can affect it.

There is no edit/delete UI. If an incorrect fill is recorded, restore the ledger
from a backup rather than editing `.storage` by hand.

## Development

The integration targets Home Assistant 2026.2 or newer.

```sh
uv run --python 3.13 pytest -q
uv run --python 3.13 ruff check .
uv run --python 3.13 ruff format --check .
```

## License

[MIT](LICENSE)
