"""Switch platform for Home Connect Oven Private.

Read/write controls are only added after a live endpoint contract is proven.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities.

    No write-safe oven-only switches are shipped until the private write contract
    is proven against live responses.
    """
    async_add_entities([])
