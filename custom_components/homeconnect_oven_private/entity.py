"""Shared entity helpers for Home Connect Oven Private."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN
from .coordinator import HomeConnectPrivateOvenManager


class HomeConnectPrivateOvenEntity(Entity):
    """Base entity for a private oven capability."""

    _attr_has_entity_name = True

    def __init__(
        self,
        manager: HomeConnectPrivateOvenManager,
        private_ha_id: str,
        suffix: str,
    ) -> None:
        self._manager = manager
        self._private_ha_id = private_ha_id
        self._suffix = suffix
        self._attr_unique_id = f"{private_ha_id}_{suffix}"

    @property
    def oven(self):
        """Return the latest known oven info."""
        return self._manager.get_oven(self._private_ha_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device metadata for the oven."""
        oven = self.oven
        name = oven.name if oven else f"Home Connect Oven {self._private_ha_id[-6:]}"
        model = oven.model if oven else None
        return DeviceInfo(
            identifiers={(DOMAIN, self._private_ha_id)},
            manufacturer="BSH / Siemens",
            model=model,
            name=name,
        )

    @property
    def available(self) -> bool:
        """Return whether auth is configured for the entry."""
        return self._manager.is_configured

