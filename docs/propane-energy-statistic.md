# Estimated propane consumption for Home Assistant Energy

Use the external statistic `propane_tracker:estimated_consumption` as a gas
consumption source, measured in kWh. It records estimated whole-tank thermal
energy consumed, not purchased gallons, tank inventory, or Navien-only burn.
Conversion uses 26.81 kWh per US liquid gallon, independent of Navien's meter
calibration.

The Recorder adapter consumes the **complete, durable, append-only** ledger of
accepted intervals. It must run after durable writes and on startup to rebuild
the projection. An empty ledger publishes nothing. Gaps and unaccepted
intervals do not produce rows; missing data does not prove zero consumption.
Before supporting edits or backdating of
already projected intervals, implement an explicit Recorder rebuild that
removes obsolete rows, including the original baseline.

Each accepted total is booked at the **end of the interval**, to the UTC hour
containing its last instant. It is not spread across intermediate hours, and
multiple credits in one hour collapse to one cumulative value. Recorder shows
these increments on the credited date, not necessarily the days the propane
was actually burned. Billing and interval-rate views should use the underlying
records, not interpret those Energy bars as hourly flow.

## Cutover checklist

- Wire the adapter to accepted intervals only; examples and unvalidated replay
  do not enter the live Recorder. Compare matched intervals with Navien and
  review a normal delivery without treating it as burn.
- With approval, replace the Navien gas consumption source with the estimated
  statistic. Do not add it as a second gas meter; whole-tank usage includes
  Navien. Preserve Navien's own history and label its prompt flow as appliance
  flow, not whole-house flow.
- Do not carry the existing fixed price to the external statistic. Home
  Assistant Energy requires a separate `stat_cost` for an external source;
  invoice spend is not the cost of fuel burned. Omit Energy cost until such a
  consumption-cost statistic exists.
