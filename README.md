# Propane Tracker for Home Assistant

Propane Tracker records manual tank deliveries as a durable local ledger and
publishes backdated usage and cost statistics for Home Assistant's Energy
dashboard. It is intended for propane bills that arrive days or weeks apart:
enter the ticket date, gallons delivered, and price per gallon, then press one
button to record the fill.

## Install with HACS

1. In HACS, open **Custom repositories**.
2. Add `https://github.com/martinemde/ha-propane-tracker` as an
   **Integration** repository.
3. Install **Propane Tracker** and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**, search for
   **Propane Tracker**, and confirm setup.

## Record a delivery

The integration creates one Propane device with these controls and summaries:

- `date.propane_delivery_date`
- `number.propane_gallons_delivered`
- `number.propane_price_per_gallon`
- `button.propane_record_delivery`
- `sensor.propane_total_usage`
- `sensor.propane_total_spend`
- `sensor.propane_last_delivery`

Set the date, gallons, and per-gallon price, then press **Record delivery**.
The calculated delivery cost is `gallons × price per gallon`, rounded to cents.
Taxes, delivery fees, credits, and other invoice adjustments are not included.

For automations or historical imports, call
`propane_tracker.record_delivery` with `delivery_date`, `gallons`, and
`price_per_gallon`. Older fills may be entered after newer fills; the ledger is
sorted and all later cumulative statistics are recalculated. Only one delivery
per calendar date is accepted, which also protects against double taps.

## Energy dashboard

Every delivery produces two external long-term statistics dated on the
delivery ticket:

- `propane_tracker:propane_usage` — cumulative propane volume
- `propane_tracker:propane_cost` — cumulative commodity cost

In **Settings → Dashboards → Energy**, add the usage statistic as a gas source
and select the cost statistic when Home Assistant asks for an external cost.
Home Assistant's Energy dashboard uses liters internally for gas statistics;
the entry control and summary sensor remain in US gallons.

## Where the history lives

The delivery history is integration-owned data in Home Assistant's `.storage`
directory under the key `propane_tracker.ledger`. Recorder statistics are a
derived projection, not the only copy. On startup the integration rebuilds
them from the ledger, including after a Recorder reset or a historical backfill.

The ledger survives restarts, integration upgrades, and HACS reinstalls because
HACS replaces code under `custom_components`, not Home Assistant's `.storage`
data. It is still local data: include the Home Assistant configuration directory
in normal backups. Removing the integration does not intentionally erase the
ledger, but deleting or restoring `.storage` can affect it.

There is no edit/delete UI in the initial release. If an incorrect fill is
recorded, restore the ledger from a backup rather than editing `.storage` by
hand.

## Development

The integration targets Home Assistant 2026.2 or newer.

```sh
uv run --python 3.13 pytest -q
uv run --python 3.13 ruff check .
uv run --python 3.13 ruff format --check .
```

## License

[MIT](LICENSE)
