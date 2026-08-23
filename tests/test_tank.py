"""Tests for the tank storage model and its reconciliation at each fill."""

from datetime import date
from decimal import Decimal

import pytest

from custom_components.propane_tracker.ledger import Delivery, LedgerError
from custom_components.propane_tracker.tank import TankConfig, TankState, burn_rate

CONFIG = TankConfig(
    capacity=Decimal("250"),
    fill_fraction=Decimal("0.8"),
    meter_units_per_gallon=Decimal("26.81"),
)


def _delivery(day: int, gallons: str) -> Delivery:
    return Delivery(
        delivery_date=date(2026, 1, day),
        gallons=Decimal(gallons),
        cost=Decimal("500"),
    )


def test_a_full_tank_holds_the_fill_fraction_of_water_capacity() -> None:
    """The driver stops at 80%, so a 250 gallon tank takes 200 gallons."""
    assert CONFIG.full == Decimal("200.0")


def test_the_gauge_reads_the_fill_fraction_when_full() -> None:
    """The tank's own gauge is a percentage of water capacity, not of usable propane."""
    assert CONFIG.gauge_percent(Decimal("200")) == Decimal("80.000")
    assert CONFIG.gauge_percent(Decimal("100")) == Decimal("40.000")


def test_level_is_unknown_until_a_fill_anchors_it() -> None:
    """Nothing can be said about a tank that has never been filled on record."""
    assert TankState().level is None


def test_metered_burn_lowers_the_level_from_the_last_fill() -> None:
    """Outflow behaves like battery discharge against a known starting charge."""
    tank = TankState().with_fill(_delivery(1, "150"), CONFIG)
    tank = tank.with_meter_reading(Decimal("1000"), CONFIG.meter_units_per_gallon)
    tank = tank.with_meter_reading(Decimal("1268.1"), CONFIG.meter_units_per_gallon)

    assert tank.metered_since_fill == pytest.approx(Decimal("10"))
    assert tank.level == pytest.approx(Decimal("190"))


def test_the_first_reading_only_sets_a_baseline() -> None:
    """A lifetime odometer must not be charged to the tank the moment it is seen."""
    tank = TankState().with_meter_reading(Decimal("50000"), CONFIG.meter_units_per_gallon)
    assert tank.consumed_gallons == Decimal("0")
    assert tank.meter_reading == Decimal("50000")


def test_a_meter_reset_counts_from_zero_instead_of_going_backward() -> None:
    """A replaced or rebooted meter must not credit propane back into the tank."""
    tank = TankState().with_meter_reading(Decimal("1000"), CONFIG.meter_units_per_gallon)
    tank = tank.with_meter_reading(Decimal("26.81"), CONFIG.meter_units_per_gallon)

    assert tank.consumed_gallons == pytest.approx(Decimal("1"))


def test_a_fill_reveals_the_burn_no_meter_measured() -> None:
    """The ticket says how empty the tank was, so the gap is the unmetered range."""
    tank = TankState().with_fill(_delivery(1, "150"), CONFIG)
    # The water heater metered 40 gallons over the cycle.
    tank = tank.with_meter_reading(Decimal("0"), CONFIG.meter_units_per_gallon)
    tank = tank.with_meter_reading(Decimal("1072.4"), CONFIG.meter_units_per_gallon)
    assert tank.level == pytest.approx(Decimal("160"))

    # The next fill took 50 gallons, so the tank was really down to 150.
    tank = tank.with_fill(_delivery(31, "50"), CONFIG)

    reconciliation = tank.reconciliations[-1]
    assert reconciliation.actual_gallons == Decimal("150.0")
    assert reconciliation.unmetered_gallons == pytest.approx(Decimal("10"))
    assert tank.level == Decimal("200.0")
    assert tank.metered_since_fill == Decimal("0")


def test_a_meter_that_reads_high_produces_a_negative_residual() -> None:
    """Over-reporting shows up as a calibration signal, not as silent drift."""
    tank = TankState().with_fill(_delivery(1, "150"), CONFIG)
    tank = tank.with_meter_reading(Decimal("0"), CONFIG.meter_units_per_gallon)
    tank = tank.with_meter_reading(Decimal("1608.6"), CONFIG.meter_units_per_gallon)

    tank = tank.with_fill(_delivery(31, "50"), CONFIG)

    assert tank.reconciliations[-1].unmetered_gallons == pytest.approx(Decimal("-10"))


def test_the_first_fill_on_record_has_nothing_to_reconcile() -> None:
    """A model with no prior level cannot claim to have discovered unmetered burn."""
    tank = TankState().with_fill(_delivery(1, "150"), CONFIG)
    assert tank.reconciliations == ()


def test_the_level_never_reads_below_empty() -> None:
    """Unmetered burn makes the model read high, never negative."""
    tank = TankState().with_fill(_delivery(1, "150"), CONFIG)
    tank = tank.with_meter_reading(Decimal("0"), CONFIG.meter_units_per_gallon)
    tank = tank.with_meter_reading(Decimal("999999"), CONFIG.meter_units_per_gallon)

    assert tank.level == Decimal("0")


def test_tank_state_round_trips_through_storage() -> None:
    """Durable tank state survives a restart with its anchor intact."""
    tank = TankState().with_fill(_delivery(1, "150"), CONFIG)
    tank = tank.with_meter_reading(Decimal("100"), CONFIG.meter_units_per_gallon)
    tank = tank.with_meter_reading(Decimal("200"), CONFIG.meter_units_per_gallon)
    tank = tank.with_fill(_delivery(31, "50"), CONFIG)

    assert TankState.from_dict(tank.as_dict()) == tank


def test_an_empty_payload_loads_as_an_unanchored_tank() -> None:
    """A ledger written before the tank existed still loads."""
    assert TankState.from_dict({}) == TankState()


def test_a_meaningless_conversion_factor_is_rejected() -> None:
    """Dividing by zero units per gallon would silently destroy the level."""
    with pytest.raises(LedgerError, match="meter_units_per_gallon"):
        TankState().with_meter_reading(Decimal("1"), Decimal("0"))


def test_burn_rate_measures_a_whole_fill_cycle() -> None:
    """Two consecutive tickets measure real whole-house burn, metered or not."""
    deliveries = (_delivery(1, "150"), _delivery(31, "60"))
    assert burn_rate(deliveries) == Decimal("2")


def test_burn_rate_needs_two_deliveries() -> None:
    """One ticket says nothing about how fast the tank empties."""
    assert burn_rate((_delivery(1, "150"),)) is None
    assert burn_rate(()) is None
