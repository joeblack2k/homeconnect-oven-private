"""Runtime state manager for Home Connect private oven data."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
import shutil
import time
from typing import Any, Callable

from aiohttp import ClientResponseError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .api import (
    AsyncMobilePrivateApi,
    OvenInfo,
    OvenNotification,
    PrivateApiError,
    SnapshotMedia,
    VideoProbe,
)
from .const import (
    CONF_LOCAL_TIMELAPSE_FPS,
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
        self._snapshot_retry_after: dict[str, float] = {}
        self._videos: dict[str, VideoProbe] = {}
        self._last_video_fetch: dict[str, float] = {}
        self._downloaded_videos: dict[str, dict[str, Any]] = {}
        self._notifications: dict[str, list[OvenNotification]] = {}
        self._last_notification_fetch: dict[str, float] = {}
        self._notification_retry_after: dict[str, float] = {}
        self._features: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._last_feature_fetch: dict[tuple[str, str, str], float] = {}
        self._feature_retry_after: dict[tuple[str, str, str], float] = {}
        self._local_timelapses: dict[str, dict[str, Any]] = {}
        self._local_timelapse_jobs: dict[str, Callable[[], None]] = {}
        self._ovens_lock = asyncio.Lock()
        self._snapshot_lock = asyncio.Lock()
        self._video_lock = asyncio.Lock()
        self._notification_lock = asyncio.Lock()
        self._feature_lock = asyncio.Lock()

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

        async with self._ovens_lock:
            if self._ovens and not refresh:
                return list(self._ovens.values())
            ovens = await self.private_api.async_list_ovens()
            self._ovens = {oven.private_ha_id: oven for oven in ovens}

            for oven in ovens:
                try:
                    snapshot = await self.async_get_snapshot(
                        oven.private_ha_id, min_interval=0
                    )
                except PrivateApiError:
                    snapshot = None
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
        force_refresh: bool = False,
    ) -> SnapshotMedia | None:
        """Return cached snapshot metadata."""
        interval = int(
            self.option(CONF_POLL_INTERVAL) or 5
            if min_interval is None
            else min_interval
        )
        requested_at = time.monotonic()
        now = time.monotonic()
        if (
            not force_refresh
            and private_ha_id in self._snapshots
            and (now - self._last_snapshot_fetch.get(private_ha_id, 0)) < interval
        ):
            return self._snapshots[private_ha_id]

        async with self._snapshot_lock:
            now = time.monotonic()
            last_fetch = self._last_snapshot_fetch.get(private_ha_id, 0)
            if private_ha_id in self._snapshots and (
                (force_refresh and last_fetch >= requested_at)
                or (not force_refresh and (now - last_fetch) < interval)
            ):
                return self._snapshots[private_ha_id]

            if now < self._snapshot_retry_after.get(private_ha_id, 0):
                if private_ha_id in self._snapshots:
                    return self._snapshots[private_ha_id]
                raise PrivateApiError("Snapshot refresh is temporarily backed off.")

            try:
                snapshot = await self.private_api.async_get_latest_snapshot(
                    private_ha_id,
                    force_refresh=force_refresh,
                )
            except ClientResponseError as err:
                delay = 300 if err.status == 429 else 60
                self._snapshot_retry_after[private_ha_id] = time.monotonic() + delay
                if private_ha_id in self._snapshots:
                    return self._snapshots[private_ha_id]
                raise PrivateApiError(str(err)) from err

            self._snapshots[private_ha_id] = snapshot
            self._last_snapshot_fetch[private_ha_id] = time.monotonic()
            self._snapshot_retry_after.pop(private_ha_id, None)
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

        async with self._video_lock:
            now = time.monotonic()
            if (
                not refresh
                and private_ha_id in self._videos
                and (now - self._last_video_fetch.get(private_ha_id, 0)) < interval
            ):
                return self._videos[private_ha_id]
            probe = await self.private_api.async_probe_latest_video(private_ha_id)
            self._videos[private_ha_id] = probe
            self._last_video_fetch[private_ha_id] = time.monotonic()
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

    async def async_get_notifications(
        self,
        private_ha_id: str,
        min_interval: int | None = None,
    ) -> list[OvenNotification]:
        """Return cached notification-center entries for one oven."""
        interval = max(60, int(min_interval or self.option(CONF_POLL_INTERVAL) or 5))
        now = time.monotonic()
        if (
            private_ha_id in self._notifications
            and (now - self._last_notification_fetch.get(private_ha_id, 0)) < interval
        ):
            return self._notifications[private_ha_id]

        async with self._notification_lock:
            now = time.monotonic()
            if (
                private_ha_id in self._notifications
                and (now - self._last_notification_fetch.get(private_ha_id, 0)) < interval
            ):
                return self._notifications[private_ha_id]
            if now < self._notification_retry_after.get(private_ha_id, 0):
                if private_ha_id in self._notifications:
                    return self._notifications[private_ha_id]
                raise PrivateApiError("Notification refresh is temporarily backed off.")

            language = (self.hass.config.language or "en-US").replace("_", "-")
            try:
                notifications = await self.private_api.async_get_oven_notifications(
                    private_ha_id, language
                )
            except ClientResponseError as err:
                delay = 300 if err.status == 429 else 60
                self._notification_retry_after[private_ha_id] = (
                    time.monotonic() + delay
                )
                if private_ha_id in self._notifications:
                    return self._notifications[private_ha_id]
                raise PrivateApiError(str(err)) from err

            self._notifications[private_ha_id] = notifications
            self._last_notification_fetch[private_ha_id] = time.monotonic()
            self._notification_retry_after.pop(private_ha_id, None)
            return notifications

    async def async_get_public_feature(
        self,
        private_ha_id: str,
        feature_type: str,
        key: str,
    ) -> dict[str, Any]:
        """Return a cached public camera feature."""
        cache_key = (private_ha_id, feature_type, key)
        now = time.monotonic()
        if (
            cache_key in self._features
            and (now - self._last_feature_fetch.get(cache_key, 0))
            < DEFAULT_VIDEO_PROBE_INTERVAL
        ):
            return self._features[cache_key]

        async with self._feature_lock:
            now = time.monotonic()
            if (
                cache_key in self._features
                and (now - self._last_feature_fetch.get(cache_key, 0))
                < DEFAULT_VIDEO_PROBE_INTERVAL
            ):
                return self._features[cache_key]
            if now < self._feature_retry_after.get(cache_key, 0):
                if cache_key in self._features:
                    return self._features[cache_key]
                raise PrivateApiError("Camera feature refresh is temporarily backed off.")

            oven = self.get_oven(private_ha_id)
            try:
                result = await self.private_api.async_get_public_feature(
                    private_ha_id,
                    oven.public_ha_id if oven else None,
                    feature_type,
                    key,
                )
            except PrivateApiError:
                self._feature_retry_after[cache_key] = time.monotonic() + 300
                if cache_key in self._features:
                    return self._features[cache_key]
                raise

            self._features[cache_key] = result
            self._last_feature_fetch[cache_key] = time.monotonic()
            self._feature_retry_after.pop(cache_key, None)
            return result

    def get_notifications(self, private_ha_id: str) -> list[OvenNotification]:
        """Return cached notification-center entries."""
        return self._notifications.get(private_ha_id, [])

    def get_latest_notification(self, private_ha_id: str) -> OvenNotification | None:
        """Return the newest cached notification-center entry."""
        notifications = self.get_notifications(private_ha_id)
        return notifications[0] if notifications else None

    def get_latest_notification_by_key(
        self,
        private_ha_id: str,
        keys: set[str],
    ) -> OvenNotification | None:
        """Return the newest cached notification matching one of the given keys."""
        return next(
            (
                notification
                for notification in self.get_notifications(private_ha_id)
                if notification.key in keys
            ),
            None,
        )

    async def async_start_local_timelapse(self, private_ha_id: str) -> dict[str, Any]:
        """Start a local timelapse recording built from still snapshots."""
        info = self._local_timelapses.get(private_ha_id)
        if info and info.get("recording"):
            return info

        self._remove_local_timelapse_job(private_ha_id)
        started_at = int(time.time())
        relative_dir = self.option(CONF_VIDEO_DOWNLOAD_DIR) or "www/homeconnect_oven_private"
        timelapse_root = Path(relative_dir) / "local_timelapse" / private_ha_id
        absolute_root = Path(self.hass.config.path(str(timelapse_root)))
        session_dir = timelapse_root / str(started_at)
        absolute_dir = Path(self.hass.config.path(str(session_dir)))

        def _reset_timelapse_cache() -> None:
            shutil.rmtree(absolute_root, ignore_errors=True)
            absolute_dir.mkdir(parents=True, exist_ok=True)

        await self.hass.async_add_executor_job(_reset_timelapse_cache)
        self._snapshots.pop(private_ha_id, None)
        self._last_snapshot_fetch.pop(private_ha_id, None)
        self._snapshot_retry_after.pop(private_ha_id, None)

        info = {
            "recording": True,
            "started_at": started_at,
            "stopped_at": None,
            "frame_count": 0,
            "last_frame_at": None,
            "last_snapshot_identifier": None,
            "frame_hashes": [],
            "skipped_duplicate_count": 0,
            "session_dir": str(absolute_dir),
            "relative_session_dir": str(session_dir),
            "output_path": None,
            "output_url": None,
            "last_error": None,
        }
        self._local_timelapses[private_ha_id] = info
        self._ensure_local_timelapse_job(private_ha_id)
        await self.async_capture_local_timelapse_frame(private_ha_id)
        return info

    async def async_stop_local_timelapse(self, private_ha_id: str) -> dict[str, Any]:
        """Stop the local timelapse recorder but keep the captured frames."""
        info = self._local_timelapses.setdefault(private_ha_id, {"frame_count": 0})
        info["recording"] = False
        info["stopped_at"] = int(time.time())
        self._remove_local_timelapse_job(private_ha_id)
        return info

    async def async_capture_local_timelapse_frame(self, private_ha_id: str) -> dict[str, Any]:
        """Capture one frame for an active local timelapse session."""
        info = self._local_timelapses.get(private_ha_id)
        if not info or not info.get("recording"):
            return info or {}

        try:
            snapshot = await self.async_get_snapshot(
                private_ha_id,
                min_interval=0,
                force_refresh=True,
            )
            if snapshot is None:
                info["last_error"] = "No snapshot metadata is available yet."
                return info

            image_bytes, content_type = await self.private_api.async_download_media(
                private_ha_id,
                snapshot.identifier,
                force_refresh=True,
            )
            await self.async_store_local_timelapse_frame(
                private_ha_id,
                image_bytes,
                content_type,
                snapshot,
            )
        except Exception as err:  # noqa: BLE001
            info["last_error"] = str(err)
        return info

    async def async_store_local_timelapse_frame(
        self,
        private_ha_id: str,
        image_bytes: bytes,
        content_type: str,
        snapshot: SnapshotMedia,
    ) -> dict[str, Any] | None:
        """Store already-downloaded camera bytes as a local timelapse frame."""
        info = self._local_timelapses.get(private_ha_id)
        if not info or not info.get("recording"):
            return None
        content_hash = hashlib.sha256(image_bytes).hexdigest()
        if not image_bytes.startswith(b"\xff\xd8\xff"):
            info["last_error"] = f"Local timelapse only supports JPEG frames, got {content_type}."
            return info
        program_start = snapshot.metadata.get("programStartTimeS")
        acquired_at = snapshot.metadata.get("originAcquisitionTimestampS") or int(snapshot.timestamp_ms / 1000)
        fresh_after = int(program_start or info.get("started_at") or 0) + 15
        if fresh_after and int(acquired_at) < fresh_after:
            info["last_error"] = "Skipped stale pre-program oven snapshot."
            return info
        frame_hashes = info.setdefault("frame_hashes", [])
        if content_hash in frame_hashes:
            info.update(
                {
                    "last_snapshot_identifier": snapshot.identifier,
                    "last_frame_duplicate": True,
                    "last_duplicate_at": int(time.time()),
                    "skipped_duplicate_count": int(info.get("skipped_duplicate_count") or 0) + 1,
                    "last_error": None,
                }
            )
            return info

        frame_count = int(info.get("frame_count") or 0) + 1
        frame_path = Path(info["session_dir"]) / f"frame_{frame_count:06d}.jpg"
        await self.hass.async_add_executor_job(frame_path.write_bytes, image_bytes)
        frame_hashes.append(content_hash)
        info.update(
            {
                "frame_count": frame_count,
                "last_frame_at": int(time.time()),
                "last_snapshot_identifier": snapshot.identifier,
                "last_frame_hash": content_hash,
                "last_frame_duplicate": False,
                "last_error": None,
            }
        )
        return info

    async def async_build_local_timelapse(self, private_ha_id: str) -> dict[str, Any]:
        """Build an MP4 from the currently captured local timelapse frames."""
        info = self._local_timelapses.setdefault(private_ha_id, {"frame_count": 0})
        if int(info.get("frame_count") or 0) < 2:
            info["last_error"] = "At least two captured frames are required to build a timelapse."
            return info

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is not available in the Home Assistant runtime.")

        fps = int(self.option(CONF_LOCAL_TIMELAPSE_FPS) or 4)
        output_path = Path(info["session_dir"]) / "timelapse.mp4"
        command = [
            ffmpeg,
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(Path(info["session_dir"]) / "frame_%06d.jpg"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace")[-500:]
            info["last_error"] = message
            raise RuntimeError(f"ffmpeg failed while building the local timelapse: {message}")

        info["output_path"] = str(output_path)
        info["output_url"] = _public_url_from_relative_path(str(Path(info["relative_session_dir"]) / "timelapse.mp4"))
        info["last_error"] = None
        return info

    def get_local_timelapse(self, private_ha_id: str) -> dict[str, Any]:
        """Return the current local timelapse state."""
        return self._local_timelapses.get(private_ha_id, {})

    def _ensure_local_timelapse_job(self, private_ha_id: str) -> None:
        """Ensure the periodic local frame capture job is running."""
        self._remove_local_timelapse_job(private_ha_id)
        interval = int(self.option(CONF_POLL_INTERVAL) or 5)

        async def _capture_frame(_now) -> None:
            await self.async_capture_local_timelapse_frame(private_ha_id)

        self._local_timelapse_jobs[private_ha_id] = async_track_time_interval(
            self.hass,
            _capture_frame,
            timedelta(seconds=interval),
        )

    def _remove_local_timelapse_job(self, private_ha_id: str) -> None:
        """Remove a periodic local timelapse capture job."""
        remove = self._local_timelapse_jobs.pop(private_ha_id, None)
        if remove:
            remove()

    async def async_shutdown(self) -> None:
        """Stop background work owned by this manager."""
        for private_ha_id in list(self._local_timelapse_jobs):
            self._remove_local_timelapse_job(private_ha_id)

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
            "local_timelapses": self._local_timelapses,
            "notifications": {
                key: [asdict(notification) for notification in notifications[:10]]
                for key, notifications in self._notifications.items()
            },
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


def _public_url_from_relative_path(relative_path: str) -> str:
    """Return a Home Assistant URL for files stored under /config/www."""
    if relative_path.startswith("www/"):
        return f"/local/{relative_path[4:]}"
    return relative_path
