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
    entities: list[ButtonEntity] = []
    for oven in ovens:
        entities.extend(
            [
                StartLocalTimelapseRecordingButton(manager, oven.private_ha_id),
                StopLocalTimelapseRecordingButton(manager, oven.private_ha_id),
                BuildLocalTimelapseButton(manager, oven.private_ha_id),
                DownloadLatestTimelapseButton(manager, oven.private_ha_id),
            ]
        )
    async_add_entities(entities)


class StartLocalTimelapseRecordingButton(HomeConnectPrivateOvenEntity, ButtonEntity):
    """Start recording a local timelapse from still camera snapshots."""

    def __init__(self, manager: HomeConnectPrivateOvenManager, private_ha_id: str) -> None:
        super().__init__(manager, private_ha_id, "start_local_timelapse_recording")

    @property
    def name(self) -> str:
        return "Start Local Timelapse Recording"

    @property
    def icon(self) -> str:
        return "mdi:record-circle"

    async def async_press(self) -> None:
        await self._manager.async_start_local_timelapse(self._private_ha_id)


class StopLocalTimelapseRecordingButton(HomeConnectPrivateOvenEntity, ButtonEntity):
    """Stop recording local timelapse frames."""

    def __init__(self, manager: HomeConnectPrivateOvenManager, private_ha_id: str) -> None:
        super().__init__(manager, private_ha_id, "stop_local_timelapse_recording")

    @property
    def name(self) -> str:
        return "Stop Local Timelapse Recording"

    @property
    def icon(self) -> str:
        return "mdi:stop-circle"

    async def async_press(self) -> None:
        await self._manager.async_stop_local_timelapse(self._private_ha_id)


class BuildLocalTimelapseButton(HomeConnectPrivateOvenEntity, ButtonEntity):
    """Build a local MP4 from recorded still snapshot frames."""

    def __init__(self, manager: HomeConnectPrivateOvenManager, private_ha_id: str) -> None:
        super().__init__(manager, private_ha_id, "build_local_timelapse")

    @property
    def name(self) -> str:
        return "Build Local Timelapse"

    @property
    def icon(self) -> str:
        return "mdi:movie-open-play"

    async def async_press(self) -> None:
        try:
            await self._manager.async_build_local_timelapse(self._private_ha_id)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(str(err)) from err


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
