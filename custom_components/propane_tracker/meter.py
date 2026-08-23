"""Follow an appliance's fuel meter as tank outflow."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
import logging

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

_LOGGER = logging.getLogger(__name__)

type RemoveListener = Callable[[], None]


class ConsumptionMeter:
    """Report every new total-increasing reading of one meter entity.

    Only the readings are handled here. Turning them into gallons, and coping
    with a counter that resets, belongs to the tank model.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        on_reading: Callable[[Decimal], None],
    ) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self._on_reading = on_reading

    @callback
    def async_start(self) -> RemoveListener:
        """Seed from the meter's current state, then follow its changes."""
        self._async_read(self.hass.states.get(self.entity_id))
        return async_track_state_change_event(
            self.hass, [self.entity_id], self._async_handle_state_change
        )

    @callback
    def _async_handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle one meter state change."""
        self._async_read(event.data["new_state"])

    @callback
    def _async_read(self, state) -> None:
        """Pass a usable numeric reading through to the tank."""
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return
        try:
            reading = Decimal(state.state)
        except (InvalidOperation, TypeError, ValueError):
            _LOGGER.debug(
                "Ignoring non-numeric reading %r from propane meter %s",
                state.state,
                self.entity_id,
            )
            return
        if not reading.is_finite():
            return
        self._on_reading(reading)
