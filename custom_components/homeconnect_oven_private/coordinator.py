"""Runtime state manager for Home Connect private oven data."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import time
from typing import Any

from homeassistant.core import HomeAssistant

from .api import AsyncMobilePrivateApi, OvenInfo, SnapshotMedia, VideoProbe
from .const import (
    CONF_POLL_INTERVAL,
    CONF_VIDEO_DOWNLOAD_DIR,
    DEFAULT_VIDEO_PROBE_INTERVAL,
)


class HomeConnectPrivateOvenManager:
    """Cache ovens, snapshots and video probe results for one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry,
        private_api: AsyncMobilePrivateApi,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.private_api = private_api
        self._ovens: dict[str, OvenInfo] = {}
        self._snapshots: dict[str, SnapshotMedia | None] = {}
        self._last_snapshot_fetch: dict[str, float] = {}
        self._videos: dict[str, VideoProbe] = {}
        self._last_video_fetch: dict[str, float] = {}
        self._downloaded_videos: dict[str, dict[str, Any]] = {}

    @property
    def is_configured(self) -> bool:
        """Return whether the auth token exists."""
        return self.private_api.is_configured

    def option(self, key: str):
        """Return an option with constant defaults already merged by HA."""
        return self.entry.options.get(key)

    async def async_get_ovens(self, refresh: bool = False) -> list[OvenInfo]:
        """Return known ovens and enrich them with snapshot metadata when possible."""
        if self._ovens and not refresh:
            return list(self._ovens.values())

        ovens = await self.private_api.async_list_ovens()
        self._ovens = {oven.private_ha_id: oven for oven in ovens}

        for oven in ovens:
            snapshot = await self.async_get_snapshot(oven.private_ha_id, min_interval=0)
            if snapshot and snapshot.metadata.get("vib"):
                current = self._ovens[oven.private_ha_id]
                current.model = snapshot.metadata.get("vib")
                current.name = snapshot.metadata.get("vib")

        return list(self._ovens.values())

    def get_oven(self, private_ha_id: str) -> OvenInfo | None:
        """Return a cached oven."""
        return self._ovens.get(private_ha_id)

    async def async_get_snapshot(
        self,
        private_ha_id: str,
        min_interval: int | None = None,
    ) -> SnapshotMedia | None:
        """Return cached snapshot metadata."""
        interval = int(min_interval or self.option(CONF_POLL_INTERVAL) or 5)
        now = time.monotonic()
        if private_ha_id in self._snapshots and (now - self._last_snapshot_fetch.get(private_ha_id, 0)) < interval:
            return self._snapshots[private_ha_id]

        snapshot = await self.private_api.async_get_latest_snapshot(private_ha_id)
        self._snapshots[private_ha_id] = snapshot
        self._last_snapshot_fetch[private_ha_id] = now
        return snapshot

    async def async_get_video_probe(self, private_ha_id: str, refresh: bool = False) -> VideoProbe:
        """Return cached video probe information."""
        interval = DEFAULT_VIDEO_PROBE_INTERVAL
        now = time.monotonic()
        if (
            not refresh
            and private_ha_id in self._videos
            and (now - self._last_video_fetch.get(private_ha_id, 0)) < interval
        ):
            return self._videos[private_ha_id]

        probe = await self.private_api.async_probe_latest_video(private_ha_id)
        self._videos[private_ha_id] = probe
        self._last_video_fetch[private_ha_id] = now
        return probe

    def get_video_probe(self, private_ha_id: str) -> VideoProbe | None:
        """Return a cached video probe."""
        return self._videos.get(private_ha_id)

    async def async_download_latest_video(self, private_ha_id: str) -> dict[str, Any]:
        """Download the latest probed video to a local HA-served file."""
        probe = await self.async_get_video_probe(private_ha_id, refresh=True)
        if not probe.available or not probe.identifier:
            raise RuntimeError("No downloadable timelapse video is available for this oven.")

        data, content_type = await self.private_api.async_download_media(
            private_ha_id,
            probe.identifier,
        )
        extension = _extension_from_content_type(content_type)
        relative_dir = self.option(CONF_VIDEO_DOWNLOAD_DIR) or "www/homeconnect_oven_private"
        directory = Path(self.hass.config.path(relative_dir))
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{private_ha_id}_{probe.identifier}{extension}"
        filepath = directory / filename
        filepath.write_bytes(data)

        public_dir = relative_dir
        if public_dir.startswith("www/"):
            public_url = f"/local/{public_dir[4:]}/{filename}"
        else:
            public_url = str(filepath)

        info = {
            "path": str(filepath),
            "url": public_url,
            "content_type": content_type,
            "identifier": probe.identifier,
            "downloaded_at": int(time.time()),
        }
        self._downloaded_videos[private_ha_id] = info
        return info

    def get_downloaded_video(self, private_ha_id: str) -> dict[str, Any] | None:
        """Return metadata for the latest downloaded video file."""
        return self._downloaded_videos.get(private_ha_id)

    async def async_diagnostics(self) -> dict[str, Any]:
        """Build a diagnostics snapshot."""
        return {
            "auth": self.private_api.debug_state(),
            "ovens": [asdict(oven) for oven in self._ovens.values()],
            "snapshots": {
                key: asdict(snapshot) if snapshot else None
                for key, snapshot in self._snapshots.items()
            },
            "videos": {
                key: asdict(video)
                for key, video in self._videos.items()
            },
            "downloaded_videos": self._downloaded_videos,
            "saved_probe_results": self.private_api.get_probe_results(),
        }


def _extension_from_content_type(content_type: str | None) -> str:
    """Map content types to file extensions."""
    if not content_type:
        return ".bin"
    if "mp4" in content_type:
        return ".mp4"
    if "quicktime" in content_type:
        return ".mov"
    if "jpeg" in content_type:
        return ".jpg"
    if "png" in content_type:
        return ".png"
    return ".bin"
