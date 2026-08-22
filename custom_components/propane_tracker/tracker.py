"""Durable propane ledger and derived Home Assistant statistics."""

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
    ATTR_DELIVERY_DATE,
    ATTR_GALLONS,
    ATTR_PRICE_PER_GALLON,
    COST_STATISTIC_ID,
    DOMAIN,
    EVENT_DELIVERY_RECORDED,
    LITERS_PER_US_GALLON,
    STORAGE_KEY,
    STORAGE_VERSION,
    USAGE_STATISTIC_ID,
)
from .ledger import Delivery, DuplicateDeliveryError, Ledger, LedgerError

_LOGGER = logging.getLogger(__name__)

type PropaneConfigEntry = ConfigEntry["PropaneTracker"]
type RemoveListener = Callable[[], None]


class PropaneTracker:
    """Own the authoritative ledger, draft controls, and derived statistics."""

    def __init__(self, hass: HomeAssistant, entry: PropaneConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._store = Store[dict[str, Any]](hass, STORAGE_VERSION, STORAGE_KEY)
        self._ledger = Ledger.empty(dt_util.now().date())
        self._listeners: set[Callable[[], None]] = set()
        self._lock = asyncio.Lock()

    @property
    def ledger(self) -> Ledger:
        """Return the current in-memory ledger snapshot."""
        return self._ledger

    async def async_initialize(self) -> None:
        """Load the ledger and rebuild its derived Recorder statistics."""
        raw = await self._store.async_load()
        if raw is not None:
            try:
                self._ledger = Ledger.from_dict(raw, today=dt_util.now().date())
            except LedgerError as err:
                raise ConfigEntryError(
                    f"The Propane Tracker ledger is invalid; refusing to overwrite it: {err}"
                ) from err
        await self.async_sync_statistics()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> RemoveListener:
        """Subscribe an entity to ledger or draft changes."""
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

    async def async_set_draft_price(self, value: float) -> None:
        """Persist the per-gallon price input."""
        await self._async_update_draft(price_per_gallon=Decimal(str(value)))

    async def _async_update_draft(self, **changes: date | Decimal) -> None:
        """Serialize draft updates so concurrent UI changes cannot clobber one another."""
        async with self._lock:
            draft = replace(self._ledger.draft, **changes)
            self._ledger = self._ledger.with_draft(draft)
            await self._store.async_save(self._ledger.as_dict())
        self._notify_listeners()

    async def async_record_draft(self) -> Delivery:
        """Record the values currently shown in the integration controls."""
        draft = self._ledger.draft
        return await self.async_record_delivery(
            delivery_date=draft.delivery_date,
            gallons=float(draft.gallons),
            price_per_gallon=float(draft.price_per_gallon),
        )

    async def async_record_delivery(
        self,
        *,
        delivery_date: date,
        gallons: float,
        price_per_gallon: float,
    ) -> Delivery:
        """Persist one delivery, then rebuild statistics as a derived view."""
        if delivery_date > dt_util.now().date():
            raise ServiceValidationError(
                f"Delivery date {delivery_date.isoformat()} is in the future"
            )

        try:
            delivery = Delivery(
                delivery_date=delivery_date,
                gallons=Decimal(str(gallons)),
                price_per_gallon=Decimal(str(price_per_gallon)),
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

            # The ledger is the source of truth. Save it before touching the
            # Recorder projection so a crash can only leave rebuildable stats,
            # never an untracked statistic row.
            await self._store.async_save(updated.as_dict())
            self._ledger = updated
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
            ATTR_PRICE_PER_GALLON: float(delivery.price_per_gallon),
            "cost": float(delivery.cost),
            "total_gallons": float(self._ledger.total_gallons),
            "total_cost": float(self._ledger.total_cost),
        }
        self.hass.bus.async_fire(EVENT_DELIVERY_RECORDED, event_data)
        _LOGGER.info(
            "Recorded %.3f gal propane at %.3f/gal on %s (cost %.2f)",
            delivery.gallons,
            delivery.price_per_gallon,
            delivery.delivery_date,
            delivery.cost,
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
        usage_sum = Decimal("0")
        cost_sum = Decimal("0")
        usage_statistics: list[StatisticData] = []
        cost_statistics: list[StatisticData] = []

        for delivery in self._ledger.deliveries:
            delivered_liters = delivery.gallons * liters_per_gallon
            usage_sum += delivered_liters
            cost_sum += delivery.cost
            start = dt_util.start_of_local_day(delivery.delivery_date)
            usage_statistics.append(
                StatisticData(
                    start=start,
                    state=float(delivered_liters),
                    sum=float(usage_sum),
                )
            )
            cost_statistics.append(
                StatisticData(
                    start=start,
                    state=float(delivery.cost),
                    sum=float(cost_sum),
                )
            )

        async_add_external_statistics(self.hass, _usage_metadata(), usage_statistics)
        async_add_external_statistics(self.hass, _cost_metadata(), cost_statistics)


def _usage_metadata() -> StatisticMetaData:
    """Return Energy-dashboard-compatible propane usage metadata."""
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name="Propane usage",
        source=DOMAIN,
        statistic_id=USAGE_STATISTIC_ID,
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
