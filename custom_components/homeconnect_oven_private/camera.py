"""Camera platform for Home Connect Oven Private."""

from __future__ import annotations

import time
from typing import Any

from aiohttp import ClientResponseError
from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PrivateApiError, PrivateAuthRequiredError, SnapshotMedia
from .const import CONF_POLL_INTERVAL, DOMAIN, MIN_POLL_INTERVAL
from .coordinator import HomeConnectPrivateOvenManager
from .entity import HomeConnectPrivateOvenEntity

PLACEHOLDER_IMAGE = b"""<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
<rect width="1280" height="720" fill="#181818"/>
<rect x="70" y="70" width="1140" height="580" rx="42" fill="#242424" stroke="#d7aa58" stroke-width="6"/>
<text x="640" y="250" fill="#f8f1df" font-family="Verdana, sans-serif" font-size="52" text-anchor="middle">Home Connect Oven Camera</text>
<text x="640" y="340" fill="#f8f1df" font-family="Verdana, sans-serif" font-size="34" text-anchor="middle">No private oven snapshot is available yet.</text>
</svg>"""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up camera entities for each discovered oven."""
    manager: HomeConnectPrivateOvenManager = hass.data[DOMAIN][entry.entry_id]
    ovens = await manager.async_get_ovens()
    async_add_entities(
        [HomeConnectPrivateOvenCamera(manager, oven.private_ha_id) for oven in ovens]
    )


class HomeConnectPrivateOvenCamera(HomeConnectPrivateOvenEntity, Camera):
    """Still snapshot camera backed by the private mobile media API."""

    def __init__(
        self,
        manager: HomeConnectPrivateOvenManager,
        private_ha_id: str,
    ) -> None:
        HomeConnectPrivateOvenEntity.__init__(self, manager, private_ha_id, "camera")
        Camera.__init__(self)
        self._content_type = "image/svg+xml"
        self._downloaded_identifier: str | None = None
        self._image: bytes | None = None
        self._last_error: str | None = None
        self._last_fetch: float = 0.0
        self._last_snapshot: SnapshotMedia | None = None
        self._refreshing = False

    @property
    def name(self) -> str:
        return "Camera"

    @property
    def icon(self) -> str:
        return "mdi:camera"

    @property
    def is_streaming(self) -> bool:
        return False

    @property
    def content_type(self) -> str:
        return self._content_type

    @content_type.setter
    def content_type(self, value: str) -> None:
        """Allow Home Assistant's Camera base class to initialize content type."""
        self._content_type = value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = {
            "downloaded_identifier": self._downloaded_identifier,
            "last_error": self._last_error,
            "private_ha_id": self._private_ha_id,
        }
        if self._last_snapshot:
            attrs.update(
                {
                    "last_snapshot_id": self._last_snapshot.identifier,
                    "last_snapshot_timestamp_ms": self._last_snapshot.timestamp_ms,
                    "upload_status": self._last_snapshot.upload_status,
                    "image_resolution": self._last_snapshot.metadata.get("resolution"),
                    "image_size": self._last_snapshot.metadata.get("length"),
                    "camera_temperature": self._last_snapshot.metadata.get("currentCavTempCelsius"),
                    "camera_door_state": self._last_snapshot.metadata.get("doorState"),
                    "object_detection_image_id": self._last_snapshot.object_detection_identifier,
                    "program_start_time_s": self._last_snapshot.metadata.get("programStartTimeS"),
                    "image_acquisition_time_s": self._last_snapshot.metadata.get("originAcquisitionTimestampS"),
                    "camera_fw_version": self._last_snapshot.metadata.get("cameraFWVersion"),
                    "smm_sw_version": self._last_snapshot.metadata.get("smmSwVersion"),
                }
            )
        return attrs

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        """Return the latest oven snapshot."""
        interval = max(
            MIN_POLL_INTERVAL,
            int(self._manager.option(CONF_POLL_INTERVAL) or MIN_POLL_INTERVAL),
        )
        if (time.monotonic() - self._last_fetch) >= interval:
            await self._async_refresh_snapshot()
        return self._image or PLACEHOLDER_IMAGE

    async def async_update(self) -> None:
        """Refresh the snapshot on polling."""
        await self._async_refresh_snapshot()

    async def _async_refresh_snapshot(self) -> None:
        if self._refreshing:
            return

        self._refreshing = True
        try:
            snapshot = await self._manager.async_get_snapshot(self._private_ha_id)
            self._last_fetch = time.monotonic()
            if snapshot is None:
                self._last_error = "Private media backend returned no snapshot items"
                return

            self._last_snapshot = snapshot
            if snapshot.identifier == self._downloaded_identifier and self._image:
                self._last_error = None
                return

            image_bytes, content_type, downloaded_identifier = await self._async_download_snapshot(snapshot)
            self._image = image_bytes
            self._content_type = content_type
            self._downloaded_identifier = downloaded_identifier
            self._last_error = None
        except PrivateAuthRequiredError:
            self._last_error = "Private oven auth is not configured"
        except (ClientResponseError, PrivateApiError) as err:
            self._last_error = str(err)
        finally:
            self._refreshing = False

    async def _async_download_snapshot(
        self,
        snapshot: SnapshotMedia,
    ) -> tuple[bytes, str, str]:
        try:
            image_bytes, content_type = await self._manager.private_api.async_download_media(
                self._private_ha_id,
                snapshot.identifier,
            )
            return image_bytes, _guess_content_type(image_bytes, content_type), snapshot.identifier
        except ClientResponseError:
            if not snapshot.preview_identifier:
                raise

        image_bytes, content_type = await self._manager.private_api.async_download_media(
            self._private_ha_id,
            snapshot.preview_identifier,
        )
        return (
            image_bytes,
            _guess_content_type(image_bytes, content_type),
            snapshot.preview_identifier,
        )


def _guess_content_type(image_bytes: bytes, response_content_type: str) -> str:
    """Prefer signature-based detection over backend headers."""
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return response_content_type or "application/octet-stream"
