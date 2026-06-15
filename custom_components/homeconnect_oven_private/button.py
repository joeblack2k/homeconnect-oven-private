"""Buttons for Home Connect Oven Private."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HomeConnectPrivateOvenManager
from .entity import HomeConnectPrivateOvenEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities."""
    manager: HomeConnectPrivateOvenManager = hass.data[DOMAIN][entry.entry_id]
    ovens = await manager.async_get_ovens()
    async_add_entities(
        [DownloadLatestTimelapseButton(manager, oven.private_ha_id) for oven in ovens]
    )


class DownloadLatestTimelapseButton(HomeConnectPrivateOvenEntity, ButtonEntity):
    """Download the latest available timelapse video to a HA-served file."""

    def __init__(self, manager: HomeConnectPrivateOvenManager, private_ha_id: str) -> None:
        super().__init__(manager, private_ha_id, "download_latest_timelapse")

    @property
    def name(self) -> str:
        return "Download Latest Timelapse"

    @property
    def icon(self) -> str:
        return "mdi:download"

    async def async_press(self) -> None:
        try:
            await self._manager.async_download_latest_video(self._private_ha_id)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(str(err)) from err
