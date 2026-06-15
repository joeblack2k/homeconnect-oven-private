"""Diagnostics for Home Connect Oven Private."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict:
    """Return diagnostics for a config entry."""
    manager = hass.data[DOMAIN][entry.entry_id]
    diagnostics = await manager.async_diagnostics()
    diagnostics["config_entry"] = {
        "entry_id": entry.entry_id,
        "options": dict(entry.options),
        "data": dict(entry.data),
    }
    return diagnostics
