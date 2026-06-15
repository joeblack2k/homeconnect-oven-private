"""Binary sensors for Home Connect Oven Private."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HomeConnectPrivateOvenManager
from .entity import HomeConnectPrivateOvenEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    manager: HomeConnectPrivateOvenManager = hass.data[DOMAIN][entry.entry_id]
    ovens = await manager.async_get_ovens()
    async_add_entities(
        [LatestTimelapseAvailableBinarySensor(manager, oven.private_ha_id) for oven in ovens]
    )


class LatestTimelapseAvailableBinarySensor(HomeConnectPrivateOvenEntity, BinarySensorEntity):
    """Expose whether a timelapse video probe currently resolves to media."""

    _attr_should_poll = True

    def __init__(self, manager: HomeConnectPrivateOvenManager, private_ha_id: str) -> None:
        super().__init__(manager, private_ha_id, "latest_timelapse_available")

    @property
    def name(self) -> str:
        return "Latest Timelapse Available"

    @property
    def is_on(self) -> bool:
        probe = self._manager.get_video_probe(self._private_ha_id)
        return bool(probe and probe.available)

    async def async_update(self) -> None:
        await self._manager.async_get_video_probe(self._private_ha_id)
