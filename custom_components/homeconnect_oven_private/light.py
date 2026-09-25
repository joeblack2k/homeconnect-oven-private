"""Light platform for Home Connect Oven Private."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    SETTING_OVEN_LIGHT_POWER,
    STATUS_INTERIOR_ILLUMINATION_ACTIVE,
)
from .coordinator import HomeConnectPrivateOvenManager
from .entity import HomeConnectPrivateOvenEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up oven light entities."""
    manager: HomeConnectPrivateOvenManager = hass.data[DOMAIN][entry.entry_id]
    ovens = await manager.async_get_ovens()
    async_add_entities(
        OvenLight(manager, oven.private_ha_id)
        for oven in ovens
    )


class OvenLight(HomeConnectPrivateOvenEntity, LightEntity):
    """Expose the oven cavity light as a Home Assistant light."""

    _attr_should_poll = True
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_color_mode = ColorMode.ONOFF

    def __init__(self, manager: HomeConnectPrivateOvenManager, private_ha_id: str) -> None:
        super().__init__(manager, private_ha_id, "oven_light")
        self._value: bool | None = None
        self._status_value: bool | None = None
        self._last_error: str | None = None
        self._raw_setting: dict[str, Any] | None = None
        self._raw_status: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        return "Oven Light"

    @property
    def icon(self) -> str:
        return "mdi:lightbulb-on-outline" if self.is_on else "mdi:lightbulb-outline"

    @property
    def is_on(self) -> bool | None:
        if self._value is not None:
            return self._value
        return self._status_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "setting_key": SETTING_OVEN_LIGHT_POWER,
            "status_key": STATUS_INTERIOR_ILLUMINATION_ACTIVE,
            "status_value": self._status_value,
            "last_error": self._last_error,
            "raw_setting": self._raw_setting,
            "raw_status": self._raw_status,
        }

    async def async_update(self) -> None:
        await self._async_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(False)

    async def _async_refresh(self) -> None:
        oven = self._manager.get_oven(self._private_ha_id)
        try:
            setting = await self._manager.private_api.async_get_public_feature(
                self._private_ha_id,
                oven.public_ha_id if oven else None,
                "settings",
                SETTING_OVEN_LIGHT_POWER,
            )
            setting_data = (setting.get("body") or {}).get("data") or {}
            value = setting_data.get("value")
            self._value = value if isinstance(value, bool) else None
            self._raw_setting = setting_data

            status = await self._manager.private_api.async_get_public_feature(
                self._private_ha_id,
                oven.public_ha_id if oven else None,
                "status",
                STATUS_INTERIOR_ILLUMINATION_ACTIVE,
            )
            status_data = (status.get("body") or {}).get("data") or {}
            status_value = status_data.get("value")
            self._status_value = status_value if isinstance(status_value, bool) else None
            self._raw_status = status_data
            self._last_error = None
        except Exception as err:  # noqa: BLE001
            self._value = None
            self._status_value = None
            self._raw_setting = None
            self._raw_status = None
            self._last_error = str(err)

    async def _async_write(self, value: bool) -> None:
        oven = self._manager.get_oven(self._private_ha_id)
        try:
            await self._manager.private_api.async_set_public_setting(
                self._private_ha_id,
                oven.public_ha_id if oven else None,
                SETTING_OVEN_LIGHT_POWER,
                value,
            )
        except Exception as err:  # noqa: BLE001
            self._last_error = str(err)
            raise HomeAssistantError(str(err)) from err
        self._value = value
        await self._async_refresh()
