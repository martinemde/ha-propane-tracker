"""Project the accepted usage ledger to Home Assistant Energy, not from raw depth."""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.util.unit_conversion import EnergyConverter

from .const import DOMAIN
from .usage import AcceptedInterval, replay_usage

ESTIMATED_CONSUMPTION_STATISTIC_ID = f"{DOMAIN}:estimated_consumption"


@callback
def async_publish_estimated_consumption(
    hass: HomeAssistant, intervals: Iterable[AcceptedInterval]
) -> None:
    """Project the complete, durable, chronologically appended interval history.

    Call only after accepting and persisting intervals. Book usage to the last
    hour of each interval; there is no basis for an hourly burn profile. Same-hour
    intervals share a row. Replaying the same ledger replaces the same rows.
    """
    by_hour: dict[datetime, float] = {}
    for point in replay_usage(intervals):
        last_instant = point.end.astimezone(UTC) - timedelta(microseconds=1)
        hour = last_instant.replace(minute=0, second=0, microsecond=0)
        by_hour[hour] = point.cumulative_kwh

    if not by_hour:
        return

    # Recorder computes usage from changes in sum. The zero is the accounting
    # origin, not a measured zero-use hour, and preserves the first increment.
    origin = next(iter(by_hour)) - timedelta(hours=1)
    rows = [StatisticData(start=origin, state=0.0, sum=0.0)]
    rows.extend(
        StatisticData(start=hour, state=total, sum=total) for hour, total in by_hour.items()
    )
    async_add_external_statistics(
        hass,
        StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name="Propane estimated consumption",
            source=DOMAIN,
            statistic_id=ESTIMATED_CONSUMPTION_STATISTIC_ID,
            unit_class=EnergyConverter.UNIT_CLASS,
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ),
        rows,
    )
