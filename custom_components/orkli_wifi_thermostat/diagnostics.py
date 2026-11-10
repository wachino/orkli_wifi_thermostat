"""Downloadable feedback diagnostics for Orkli Wifi Thermostat."""

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return the feedback summary for the selected installation."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is None:
        return {"loaded": False}
    return {"loaded": True, **runtime.coordinator.feedback_diagnostics()}
