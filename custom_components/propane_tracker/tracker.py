"""Durable propane ledger, tank model, and derived Home Assistant statistics."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from decimal import Decimal
import logging
from typing import Any

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, ServiceValidationError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from .const import (
    ATTR_COST,
    ATTR_DELIVERY_DATE,
    ATTR_GALLONS,
    CONF_FILL_PERCENT,
    CONF_METER_ENTITY,
    CONF_METER_UNITS_PER_GALLON,
    CONF_TANK_CAPACITY,
    COST_STATISTIC_ID,
    DEFAULT_FILL_PERCENT,
    DEFAULT_METER_UNITS_PER_GALLON,
    DEFAULT_TANK_CAPACITY,
    DELIVERED_STATISTIC_ID,
    DOMAIN,
    EVENT_DELIVERY_RECORDED,
    LITERS_PER_US_GALLON,
    STORAGE_KEY,
    STORAGE_VERSION,
    UNMETERED_STATISTIC_ID,
)
from .ledger import Delivery, DuplicateDeliveryError, Ledger, LedgerError
from .meter import ConsumptionMeter
from .tank import TankConfig, TankState, ZERO, burn_rate

_LOGGER = logging.getLogger(__name__)

type PropaneConfigEntry = ConfigEntry["PropaneTracker"]
type RemoveListener = Callable[[], None]

SAVE_DELAY = 60


class PropaneStore(Store[dict[str, Any]]):
    """Storage that accepts the version 1 payload without rewriting it."""

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Upgrade a stored payload.

        Version 1 held a per-gallon price and no tank state. Both the ledger
        and the tank read that shape directly, so the upgrade happens as the
        dataclasses load and is written back on the next save.
        """
        return old_data


class PropaneTracker:
    """Own the authoritative ledger, the tank model, and derived statistics."""

    def __init__(self, hass: HomeAssistant, entry: PropaneConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._store = PropaneStore(hass, STORAGE_VERSION, STORAGE_KEY)
        self._ledger = Ledger.empty(dt_util.now().date())
        self._tank = TankState()
        self._listeners: set[Callable[[], None]] = set()
        self._lock = asyncio.Lock()
        self._unsubscribe_meter: RemoveListener | None = None

    @property
    def ledger(self) -> Ledger:
        """Return the current in-memory ledger snapshot."""
        return self._ledger

    @property
    def tank(self) -> TankState:
        """Return the current in-memory tank snapshot."""
        return self._tank

    @property
    def config(self) -> TankConfig:
        """Return the tank configuration, with options overriding setup values."""
        settings = {**self.entry.data, **self.entry.options}
        meter_entity = settings.get(CONF_METER_ENTITY) or None
        return TankConfig(
            capacity=Decimal(str(settings.get(CONF_TANK_CAPACITY, DEFAULT_TANK_CAPACITY))),
            fill_fraction=Decimal(str(settings.get(CONF_FILL_PERCENT, DEFAULT_FILL_PERCENT))) / 100,
            meter_units_per_gallon=Decimal(
                str(settings.get(CONF_METER_UNITS_PER_GALLON, DEFAULT_METER_UNITS_PER_GALLON))
            ),
            meter_entity=meter_entity,
        )

    @property
    def burn_rate(self) -> Decimal | None:
        """Return measured gallons per day over the most recent fill cycle."""
        return burn_rate(self._ledger.deliveries)

    @property
    def days_remaining(self) -> Decimal | None:
        """Return days of propane left at the most recently measured burn rate."""
        level = self._tank.level
        rate = self.burn_rate
        if level is None or rate is None or rate <= 0:
            return None
        return level / rate

    async def async_initialize(self) -> None:
        """Load durable state and rebuild its derived Recorder statistics."""
        raw = await self._store.async_load()
        if raw is not None:
            try:
                self._ledger = Ledger.from_dict(raw, today=dt_util.now().date())
                self._tank = TankState.from_dict(raw.get("tank", {}))
            except LedgerError as err:
                raise ConfigEntryError(
                    f"The Propane Tracker ledger is invalid; refusing to overwrite it: {err}"
                ) from err
        await self.async_sync_statistics()

    @callback
    def async_start_meter(self) -> None:
        """Begin following the configured consumption meter, if there is one."""
        entity_id = self.config.meter_entity
        if entity_id is None:
            return
        meter = ConsumptionMeter(self.hass, entity_id, self._async_apply_reading)
        self._unsubscribe_meter = meter.async_start()

    @callback
    def async_shutdown(self) -> None:
        """Stop following the consumption meter."""
        if self._unsubscribe_meter is not None:
            self._unsubscribe_meter()
            self._unsubscribe_meter = None

    @callback
    def _async_apply_reading(self, reading: Decimal) -> None:
        """Subtract one meter reading from the tank.

        Meter readings arrive far more often than deliveries, so this stays
        synchronous and lets Store batch the writes. Losing the last minute of
        readings to a hard restart costs a fraction of a gallon of accuracy;
        the next fill re-anchors the level regardless.
        """
        config = self.config
        updated = self._tank.with_meter_reading(reading, config.meter_units_per_gallon)
        if updated == self._tank:
            return
        self._tank = updated
        self._store.async_delay_save(self._data, SAVE_DELAY)
        self._notify_listeners()

    @callback
    def _data(self) -> dict[str, Any]:
        """Return the complete durable payload."""
        return {**self._ledger.as_dict(), "tank": self._tank.as_dict()}

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> RemoveListener:
        """Subscribe an entity to ledger, tank, or draft changes."""
        self._listeners.add(listener)

        @callback
        def remove_listener() -> None:
            self._listeners.discard(listener)

        return remove_listener

    @callback
    def _notify_listeners(self) -> None:
        """Tell every entity to publish its new state."""
        for listener in self._listeners:
            listener()

    async def async_set_draft_date(self, value: date) -> None:
        """Persist the date input."""
        await self._async_update_draft(delivery_date=value)

    async def async_set_draft_gallons(self, value: float) -> None:
        """Persist the gallons input."""
        await self._async_update_draft(gallons=Decimal(str(value)))

    async def async_set_draft_cost(self, value: float) -> None:
        """Persist the invoice-total input."""
        await self._async_update_draft(cost=Decimal(str(value)))

    async def _async_update_draft(self, **changes: date | Decimal) -> None:
        """Serialize draft updates so concurrent UI changes cannot clobber one another."""
        async with self._lock:
            draft = replace(self._ledger.draft, **changes)
            self._ledger = self._ledger.with_draft(draft)
            await self._store.async_save(self._data())
        self._notify_listeners()

    async def async_record_draft(self) -> Delivery:
        """Record the values currently shown in the integration controls."""
        draft = self._ledger.draft
        return await self.async_record_delivery(
            delivery_date=draft.delivery_date,
            gallons=float(draft.gallons),
            cost=float(draft.cost),
        )

    async def async_record_delivery(
        self,
        *,
        delivery_date: date,
        gallons: float,
        cost: float,
    ) -> Delivery:
        """Persist one delivery, refill the tank, then rebuild statistics."""
        if delivery_date > dt_util.now().date():
            raise ServiceValidationError(
                f"Delivery date {delivery_date.isoformat()} is in the future"
            )

        try:
            delivery = Delivery(
                delivery_date=delivery_date,
                gallons=Decimal(str(gallons)),
                cost=Decimal(str(cost)),
            )
        except LedgerError as err:
            raise ServiceValidationError(str(err)) from err

        async with self._lock:
            try:
                updated = self._ledger.with_delivery(delivery)
            except DuplicateDeliveryError as err:
                raise ServiceValidationError(
                    f"{err}. This also prevents a double tap from recording the same bill twice."
                ) from err

            # Only the newest ticket describes the tank as it stands now. A
            # backdated bill is history for the statistics, not a refill.
            tank = self._tank
            if self._ledger.is_newest(delivery.delivery_date):
                tank = tank.with_fill(delivery, self.config)

            # The ledger is the source of truth. Save it before touching the
            # Recorder projection so a crash can only leave rebuildable stats,
            # never an untracked statistic row.
            self._ledger = updated
            self._tank = tank
            await self._store.async_save(self._data())
            try:
                await self.async_sync_statistics()
            except Exception:  # noqa: BLE001 - the durable write already succeeded
                # A Recorder failure must not encourage a retry that would be
                # rejected as a duplicate. Startup rebuilds this projection.
                _LOGGER.exception(
                    "Saved propane delivery, but could not refresh Recorder statistics; "
                    "they will be rebuilt when the integration next loads"
                )

        self._notify_listeners()
        event_data = {
            ATTR_DELIVERY_DATE: delivery.delivery_date.isoformat(),
            ATTR_GALLONS: float(delivery.gallons),
            ATTR_COST: float(delivery.cost),
            "price_per_gallon": float(delivery.price_per_gallon),
            "total_gallons": float(self._ledger.total_gallons),
            "total_cost": float(self._ledger.total_cost),
        }
        self.hass.bus.async_fire(EVENT_DELIVERY_RECORDED, event_data)
        _LOGGER.info(
            "Recorded %.3f gal propane for %.2f on %s (%.3f/gal)",
            delivery.gallons,
            delivery.cost,
            delivery.delivery_date,
            delivery.price_per_gallon,
        )
        return delivery

    async def async_sync_statistics(self) -> None:
        """Rebuild every statistic row from the authoritative ledger.

        Re-importing all rows is deliberate: adding an older historical bill
        changes the cumulative sum on every newer row. The external-statistics
        API replaces matching timestamps, so this also repairs the projection
        after a Recorder reset or an interrupted prior write.
        """
        if not self._ledger.deliveries:
            return

        liters_per_gallon = Decimal(str(LITERS_PER_US_GALLON))
        unmetered_by_date = {
            item.delivery_date: item.unmetered_gallons for item in self._tank.reconciliations
        }
        delivered_sum = Decimal("0")
        cost_sum = Decimal("0")
        unmetered_sum = Decimal("0")
        delivered_statistics: list[StatisticData] = []
        cost_statistics: list[StatisticData] = []
        unmetered_statistics: list[StatisticData] = []

        for delivery in self._ledger.deliveries:
            start = dt_util.start_of_local_day(delivery.delivery_date)
            delivered_liters = delivery.gallons * liters_per_gallon
            delivered_sum += delivered_liters
            cost_sum += delivery.cost
            delivered_statistics.append(
                StatisticData(
                    start=start,
                    state=float(delivered_liters),
                    sum=float(delivered_sum),
                )
            )
            cost_statistics.append(
                StatisticData(
                    start=start,
                    state=float(delivery.cost),
                    sum=float(cost_sum),
                )
            )
            # A negative residual means the meter reads high rather than that
            # propane flowed back into the tank, so the series floors at zero.
            unmetered = unmetered_by_date.get(delivery.delivery_date)
            if unmetered is None:
                continue
            unmetered_liters = max(ZERO, unmetered) * liters_per_gallon
            unmetered_sum += unmetered_liters
            unmetered_statistics.append(
                StatisticData(
                    start=start,
                    state=float(unmetered_liters),
                    sum=float(unmetered_sum),
                )
            )

        async_add_external_statistics(
            self.hass,
            _volume_metadata(DELIVERED_STATISTIC_ID, "Propane delivered"),
            delivered_statistics,
        )
        async_add_external_statistics(self.hass, _cost_metadata(), cost_statistics)
        if unmetered_statistics:
            async_add_external_statistics(
                self.hass,
                _volume_metadata(UNMETERED_STATISTIC_ID, "Propane unmetered burn"),
                unmetered_statistics,
            )


def _volume_metadata(statistic_id: str, name: str) -> StatisticMetaData:
    """Return Energy-dashboard-compatible propane volume metadata.

    Home Assistant does not accept US gallons for a volume statistic, so the
    durable series is stored in liters with conversion metadata and displayed
    in whatever unit the user prefers.
    """
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_class=VolumeConverter.UNIT_CLASS,
        unit_of_measurement=UnitOfVolume.LITERS,
    )


def _cost_metadata() -> StatisticMetaData:
    """Return cost metadata in the delayed-utility import format."""
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name="Propane cost",
        source=DOMAIN,
        statistic_id=COST_STATISTIC_ID,
        unit_class=None,
        unit_of_measurement=None,
    )
