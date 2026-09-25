"""Binary sensors for Home Connect Oven Private."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, TURN_FOOD_NOTIFICATION_KEYS
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
    entities: list[BinarySensorEntity] = []
    for oven in ovens:
        entities.append(LatestTimelapseAvailableBinarySensor(manager, oven.private_ha_id))
        entities.append(LocalTimelapseRecordingBinarySensor(manager, oven.private_ha_id))
        entities.append(LatestNotificationPresentBinarySensor(manager, oven.private_ha_id))
        entities.append(TurnFoodNowPresentBinarySensor(manager, oven.private_ha_id))
    async_add_entities(entities)


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


class LatestNotificationPresentBinarySensor(HomeConnectPrivateOvenEntity, BinarySensorEntity):
    """Expose whether the newest oven notification is still active."""

    _attr_should_poll = True

    def __init__(self, manager: HomeConnectPrivateOvenManager, private_ha_id: str) -> None:
        super().__init__(manager, private_ha_id, "latest_notification_present")

    @property
    def name(self) -> str:
        return "Latest Notification Present"

    @property
    def is_on(self) -> bool:
        notification = self._manager.get_latest_notification(self._private_ha_id)
        return bool(notification and notification.state == "present")

    async def async_update(self) -> None:
        await self._manager.async_get_notifications(self._private_ha_id)


class LocalTimelapseRecordingBinarySensor(HomeConnectPrivateOvenEntity, BinarySensorEntity):
    """Expose whether the local still-frame timelapse recorder is active."""

    _attr_should_poll = True

    def __init__(self, manager: HomeConnectPrivateOvenManager, private_ha_id: str) -> None:
        super().__init__(manager, private_ha_id, "local_timelapse_recording")

    @property
    def name(self) -> str:
        return "Local Timelapse Recording"

    @property
    def icon(self) -> str:
        return "mdi:record-rec"

    @property
    def is_on(self) -> bool:
        return bool(self._manager.get_local_timelapse(self._private_ha_id).get("recording"))

    @property
    def extra_state_attributes(self):
        info = self._manager.get_local_timelapse(self._private_ha_id)
        return {
            "frame_count": info.get("frame_count"),
            "last_frame_at": info.get("last_frame_at"),
            "output_url": info.get("output_url"),
            "last_error": info.get("last_error"),
        }


class TurnFoodNowPresentBinarySensor(HomeConnectPrivateOvenEntity, BinarySensorEntity):
    """Expose the live state of the private 'turn food' notification."""

    _attr_should_poll = True

    def __init__(self, manager: HomeConnectPrivateOvenManager, private_ha_id: str) -> None:
        super().__init__(manager, private_ha_id, "turn_food_now_present")

    @property
    def name(self) -> str:
        return "Turn Food Now Present"

    @property
    def is_on(self) -> bool:
        notification = self._manager.get_latest_notification_by_key(
            self._private_ha_id,
            TURN_FOOD_NOTIFICATION_KEYS,
        )
        return bool(notification and notification.state == "present")

    @property
    def extra_state_attributes(self):
        notification = self._manager.get_latest_notification_by_key(
            self._private_ha_id,
            TURN_FOOD_NOTIFICATION_KEYS,
        )
        if not notification:
            return {}
        return {
            "notification_key": notification.key,
            "notification_title": notification.title,
            "notification_description": notification.description,
            "notification_time": notification.created_at,
            "notification_state": notification.state,
        }

    async def async_update(self) -> None:
        await self._manager.async_get_notifications(self._private_ha_id)
