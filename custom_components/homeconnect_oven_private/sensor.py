"""Sensor platform for Home Connect Oven Private."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PrivateApiError, PrivateAuthRequiredError
from .const import CONF_DIAGNOSTIC_SENSORS, DOMAIN
from .coordinator import HomeConnectPrivateOvenManager
from .entity import HomeConnectPrivateOvenEntity

SNAPSHOT_SENSOR_TYPES = {
    "last_snapshot_time": {"name": "Last Snapshot Time", "device_class": "timestamp"},
    "snapshot_age": {"name": "Snapshot Age", "unit": "s"},
    "upload_status": {"name": "Upload Status"},
    "image_counter": {"name": "Image Counter"},
    "image_resolution": {"name": "Image Resolution"},
    "image_size": {"name": "Image Size", "unit": "B"},
    "camera_temperature": {"name": "Camera Temperature", "device_class": "temperature", "unit": UnitOfTemperature.CELSIUS},
    "camera_door_state": {"name": "Camera Door State"},
    "object_detection_image_id": {"name": "Object Detection Image ID"},
    "program_start_time": {"name": "Program Start Time", "device_class": "timestamp"},
    "image_acquisition_time": {"name": "Image Acquisition Time", "device_class": "timestamp"},
    "latest_timelapse_time": {"name": "Latest Timelapse Time", "device_class": "timestamp"},
    "latest_timelapse_file": {"name": "Latest Timelapse File"},
}

DIAGNOSTIC_SENSOR_TYPES = {
    "camera_fw_version": "Camera Firmware Version",
    "smm_sw_version": "SMM Software Version",
    "snapshot_hash": "Snapshot Hash",
    "sequence_id": "Sequence ID",
    "exposure_time": "Exposure Time",
    "gain": "Gain",
    "white_balance_matrix": "White Balance Matrix",
    "creation_scope": "Creation Scope",
    "selected_program_id": "Selected Program ID",
    "camera_position": "Camera Position",
    "vib": "VIB",
    "device_type": "Device Type",
    "latest_timelapse_media_id": "Latest Timelapse Media ID",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up snapshot metadata sensors."""
    manager: HomeConnectPrivateOvenManager = hass.data[DOMAIN][entry.entry_id]
    ovens = await manager.async_get_ovens()
    entities: list[SensorEntity] = []
    for oven in ovens:
        entities.extend(
            SnapshotSensor(manager, oven.private_ha_id, key)
            for key in SNAPSHOT_SENSOR_TYPES
        )
        if entry.options.get(CONF_DIAGNOSTIC_SENSORS):
            entities.extend(
                DiagnosticSensor(manager, oven.private_ha_id, key)
                for key in DIAGNOSTIC_SENSOR_TYPES
            )
    async_add_entities(entities)


class SnapshotSensor(HomeConnectPrivateOvenEntity, SensorEntity):
    """Sensor backed by snapshot or timelapse metadata."""

    _attr_should_poll = True

    def __init__(self, manager: HomeConnectPrivateOvenManager, private_ha_id: str, key: str) -> None:
        super().__init__(manager, private_ha_id, key)
        self._key = key
        self._last_error: str | None = None

    @property
    def name(self) -> str:
        return SNAPSHOT_SENSOR_TYPES[self._key]["name"]

    @property
    def device_class(self) -> str | None:
        return SNAPSHOT_SENSOR_TYPES[self._key].get("device_class")

    @property
    def native_unit_of_measurement(self) -> str | None:
        return SNAPSHOT_SENSOR_TYPES[self._key].get("unit")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_error": self._last_error}

    @property
    def native_value(self):
        snapshot = self._manager._snapshots.get(self._private_ha_id)
        video = self._manager.get_video_probe(self._private_ha_id)
        downloaded_video = self._manager.get_downloaded_video(self._private_ha_id)

        if self._key == "last_snapshot_time":
            return _as_timestamp(snapshot.timestamp_ms if snapshot else None)
        if self._key == "snapshot_age":
            if not snapshot or not snapshot.timestamp_ms:
                return None
            now_ts = int(datetime.now(timezone.utc).timestamp())
            return max(0, now_ts - int(snapshot.timestamp_ms / 1000))
        if self._key == "upload_status":
            return snapshot.upload_status if snapshot else None
        if self._key == "image_counter":
            return _as_int(snapshot.metadata.get("imageCounter") if snapshot else None)
        if self._key == "image_resolution":
            return snapshot.metadata.get("resolution") if snapshot else None
        if self._key == "image_size":
            return _as_int(snapshot.metadata.get("length") if snapshot else None)
        if self._key == "camera_temperature":
            return _as_float(snapshot.metadata.get("currentCavTempCelsius") if snapshot else None)
        if self._key == "camera_door_state":
            return snapshot.metadata.get("doorState") if snapshot else None
        if self._key == "object_detection_image_id":
            return snapshot.object_detection_identifier if snapshot else None
        if self._key == "program_start_time":
            return _as_timestamp(_as_int(snapshot.metadata.get("programStartTimeS") if snapshot else None), seconds=True)
        if self._key == "image_acquisition_time":
            return _as_timestamp(_as_int(snapshot.metadata.get("originAcquisitionTimestampS") if snapshot else None), seconds=True)
        if self._key == "latest_timelapse_time":
            return _as_timestamp(video.timestamp_ms if video else None)
        if self._key == "latest_timelapse_file":
            return downloaded_video.get("url") if downloaded_video else None
        return None

    async def async_update(self) -> None:
        """Refresh snapshot and video probe caches."""
        try:
            await self._manager.async_get_snapshot(self._private_ha_id)
            await self._manager.async_get_video_probe(self._private_ha_id)
            self._last_error = None
        except (PrivateAuthRequiredError, PrivateApiError) as err:
            self._last_error = str(err)


class DiagnosticSensor(HomeConnectPrivateOvenEntity, SensorEntity):
    """Optional diagnostic sensor."""

    _attr_should_poll = True
    _attr_entity_registry_enabled_default = False

    def __init__(self, manager: HomeConnectPrivateOvenManager, private_ha_id: str, key: str) -> None:
        super().__init__(manager, private_ha_id, key)
        self._key = key

    @property
    def name(self) -> str:
        return DIAGNOSTIC_SENSOR_TYPES[self._key]

    @property
    def native_value(self):
        snapshot = self._manager._snapshots.get(self._private_ha_id)
        video = self._manager.get_video_probe(self._private_ha_id)
        if self._key == "camera_fw_version":
            return snapshot.metadata.get("cameraFWVersion") if snapshot else None
        if self._key == "smm_sw_version":
            return snapshot.metadata.get("smmSwVersion") if snapshot else None
        if self._key == "snapshot_hash":
            return snapshot.hash_value if snapshot else None
        if self._key == "sequence_id":
            return snapshot.metadata.get("sequenceId") if snapshot else None
        if self._key == "exposure_time":
            return snapshot.metadata.get("exposureTime") if snapshot else None
        if self._key == "gain":
            return snapshot.metadata.get("gain") if snapshot else None
        if self._key == "white_balance_matrix":
            return snapshot.metadata.get("whiteBalanceMatrix") if snapshot else None
        if self._key == "creation_scope":
            return snapshot.metadata.get("creationScope") if snapshot else None
        if self._key == "selected_program_id":
            return snapshot.metadata.get("selProgram") if snapshot else None
        if self._key == "camera_position":
            return snapshot.position if snapshot else None
        if self._key == "vib":
            return snapshot.metadata.get("vib") if snapshot else None
        if self._key == "device_type":
            return snapshot.metadata.get("deviceType") if snapshot else None
        if self._key == "latest_timelapse_media_id":
            return video.identifier if video else None
        return None

    async def async_update(self) -> None:
        await self._manager.async_get_snapshot(self._private_ha_id)
        await self._manager.async_get_video_probe(self._private_ha_id)


def _as_timestamp(value: int | None, seconds: bool = False):
    if value is None:
        return None
    if not seconds:
        value = int(value / 1000)
    return datetime.fromtimestamp(value, timezone.utc)


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
