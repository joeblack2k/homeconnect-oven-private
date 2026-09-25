"""Private Home Connect mobile API helpers for oven media."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from aiohttp import ClientResponse, ClientResponseError, ClientSession
from homeassistant.helpers.storage import Store

from .const import (
    ENDPOINT_AUTHORIZE,
    ENDPOINT_TOKEN,
    OAUTH_BASE,
    PKCE_EXPIRY_SECONDS,
    PRIVATE_APPLIANCE_LIST_ACCEPT,
    PRIVATE_APPLIANCE_LIST_ENDPOINT,
    PRIVATE_API_HOST,
    PRIVATE_CLIENT_ID,
    PRIVATE_PROBE_STORE_KEY,
    PRIVATE_PROBE_STORE_VERSION,
    PRIVATE_REDIRECT_URI,
    PRIVATE_SCOPES,
    PRIVATE_TOKEN_STORE_KEY,
    PRIVATE_TOKEN_STORE_VERSION,
    PROBE_ROUTE_DEFINITIONS,
)


class PrivateApiError(RuntimeError):
    """Base exception for private mobile API failures."""


class PrivateAuthRequiredError(PrivateApiError):
    """Raised when private auth is missing."""


class PrivateAuthExpiredError(PrivateApiError):
    """Raised when private auth can no longer be refreshed."""


@dataclass(slots=True)
class OvenInfo:
    """Known oven account/device information."""

    private_ha_id: str
    public_ha_id: str | None = None
    model: str | None = None
    name: str | None = None
    has_camera: bool = True


@dataclass(slots=True)
class SnapshotMedia:
    """Latest still snapshot metadata."""

    private_ha_id: str
    content_type: str
    hash_value: str | None
    identifier: str
    media_type: str
    object_detection_identifier: str | None
    preview_identifier: str | None
    timestamp_ms: int
    upload_status: str | None
    position: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VideoProbe:
    """Outcome of probing private oven video support."""

    private_ha_id: str
    available: bool
    content_type: str | None = None
    identifier: str | None = None
    media_type: str | None = None
    timestamp_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    route_statuses: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class OvenNotification:
    """Notification-center item emitted for an oven."""

    private_ha_id: str
    identifier: str
    key: str
    title: str | None
    description: str | None
    created_at: str | None
    state: str | None
    level: str | None
    category: str | None
    channels: list[str] = field(default_factory=list)
    read: bool | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class MobilePrivateAuth:
    """Manage PKCE auth and token persistence for the private mobile API."""

    def __init__(self, hass, entry_id: str, websession: ClientSession) -> None:
        self._websession = websession
        self._token_store = Store(
            hass,
            version=PRIVATE_TOKEN_STORE_VERSION,
            key=f"{PRIVATE_TOKEN_STORE_KEY}_{entry_id}",
            private=True,
        )
        self._probe_store = Store(
            hass,
            version=PRIVATE_PROBE_STORE_VERSION,
            key=f"{PRIVATE_PROBE_STORE_KEY}_{entry_id}",
            private=True,
        )
        self._data: dict[str, Any] = {}
        self._probe_data: dict[str, Any] = {}
        self._refresh_lock = asyncio.Lock()
        self._refresh_retry_after = 0.0

    async def async_initialize(self) -> None:
        """Load token and probe state."""
        self._data = await self._token_store.async_load() or {}
        self._probe_data = await self._probe_store.async_load() or {}

    def export_state(self) -> dict[str, Any]:
        """Return the raw persisted state for migration."""
        return dict(self._data)

    async def async_import_state(self, state: dict[str, Any]) -> None:
        """Replace the current persisted token state."""
        self._data = dict(state)
        await self._async_save_token_store()

    @property
    def is_configured(self) -> bool:
        """Return whether a refresh token exists."""
        token = self._data.get("token") or {}
        return bool(token.get("refresh_token"))

    def debug_state(self) -> dict[str, Any]:
        """Return a redacted auth state for diagnostics."""
        token = self._data.get("token") or {}
        pkce = self._data.get("pkce") or {}
        return {
            "has_access_token": bool(token.get("access_token")),
            "has_refresh_token": bool(token.get("refresh_token")),
            "expires_at": token.get("expires_at"),
            "has_pending_pkce": bool(pkce.get("state")),
            "pending_pkce_created_at": pkce.get("created_at"),
        }

    async def async_build_authorize_url(self) -> str:
        """Build a fresh mobile PKCE authorize URL."""
        verifier = _generate_code_verifier()
        challenge = _generate_code_challenge(verifier)
        state = f"hcapp-{secrets.token_urlsafe(18)}"
        self._data["pkce"] = {
            "code_verifier": verifier,
            "created_at": int(time.time()),
            "state": state,
        }
        await self._async_save_token_store()

        params = {
            "client_id": PRIVATE_CLIENT_ID,
            "redirect_uri": PRIVATE_REDIRECT_URI,
            "response_type": "code",
            "scope": PRIVATE_SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{OAUTH_BASE}{ENDPOINT_AUTHORIZE}?{urlencode(params)}"

    async def async_exchange_callback_url(self, callback_url: str) -> None:
        """Exchange the QR callback URL into a refresh token."""
        code, returned_state = _extract_code_and_state(callback_url)
        pkce = self._data.get("pkce") or {}
        created_at = int(pkce.get("created_at", 0) or 0)
        if not pkce.get("code_verifier") or not pkce.get("state"):
            raise PrivateApiError("No pending private auth session found.")
        if created_at and (time.time() - created_at) > PKCE_EXPIRY_SECONDS:
            raise PrivateApiError("The private auth session expired. Start it again.")
        if returned_state and returned_state != pkce["state"]:
            raise PrivateApiError("The private auth callback state does not match.")

        payload = await self._async_form_post(
            f"{OAUTH_BASE}{ENDPOINT_TOKEN}",
            {
                "grant_type": "authorization_code",
                "client_id": PRIVATE_CLIENT_ID,
                "code": code,
                "redirect_uri": PRIVATE_REDIRECT_URI,
                "code_verifier": pkce["code_verifier"],
            },
        )
        self._data["token"] = _normalize_token_payload(payload)
        self._data.pop("pkce", None)
        await self._async_save_token_store()

    async def async_get_access_token(self) -> str:
        """Return a valid access token with one refresh retry."""
        if time.monotonic() < self._refresh_retry_after:
            raise PrivateAuthExpiredError("Private oven auth refresh is temporarily backed off.")

        token = self._data.get("token") or {}
        refresh_token = token.get("refresh_token")
        access_token = token.get("access_token")
        expires_at = int(token.get("expires_at", 0) or 0)

        if not refresh_token:
            raise PrivateAuthRequiredError("Private oven auth is not configured.")

        if access_token and (expires_at - time.time()) >= 120:
            return access_token

        async with self._refresh_lock:
            token = self._data.get("token") or {}
            access_token = token.get("access_token")
            expires_at = int(token.get("expires_at", 0) or 0)
            if access_token and (expires_at - time.time()) >= 120:
                return access_token

            last_error: Exception | None = None
            for _ in range(2):
                try:
                    payload = await self._async_form_post(
                        f"{OAUTH_BASE}{ENDPOINT_TOKEN}",
                        {
                            "grant_type": "refresh_token",
                            "client_id": PRIVATE_CLIENT_ID,
                            "refresh_token": refresh_token,
                        },
                    )
                except Exception as err:  # noqa: BLE001
                    last_error = err
                    self._data.get("token", {}).pop("access_token", None)
                    continue

                token = _normalize_token_payload(payload)
                token.setdefault("refresh_token", refresh_token)
                access_token = token.get("access_token")
                if not access_token:
                    last_error = PrivateApiError(
                        "Private token refresh returned no access token."
                    )
                    continue

                self._data["token"] = token
                self._refresh_retry_after = 0.0
                await self._async_save_token_store()
                return access_token

            self._refresh_retry_after = time.monotonic() + 300
            raise PrivateAuthExpiredError("Private oven auth refresh failed.") from last_error

    async def async_save_probe_results(self, data: dict[str, Any]) -> None:
        """Persist probe information for diagnostics."""
        self._probe_data = data
        await self._probe_store.async_save(data)

    def get_probe_results(self) -> dict[str, Any]:
        """Return the most recently saved probe results."""
        return self._probe_data

    async def _async_form_post(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        """POST form data and decode JSON."""
        async with self._websession.post(url, data=data) as response:
            response.raise_for_status()
            return await response.json()

    async def _async_save_token_store(self) -> None:
        """Persist token data."""
        await self._token_store.async_save(self._data)


class AsyncMobilePrivateApi:
    """Wrapper around the private Home Connect mobile API."""

    def __init__(self, auth: MobilePrivateAuth, websession: ClientSession) -> None:
        self._auth = auth
        self._websession = websession

    @property
    def is_configured(self) -> bool:
        """Return whether the private token is available."""
        return self._auth.is_configured

    def debug_state(self) -> dict[str, Any]:
        """Return auth state for diagnostics."""
        return self._auth.debug_state()

    def get_probe_results(self) -> dict[str, Any]:
        """Return saved probe results."""
        return self._auth.get_probe_results()

    async def async_build_authorize_url(self) -> str:
        """Build a PKCE login URL."""
        return await self._auth.async_build_authorize_url()

    async def async_exchange_callback_url(self, callback_url: str) -> None:
        """Exchange the final QR callback URL."""
        await self._auth.async_exchange_callback_url(callback_url)

    async def async_request(
        self,
        method: str,
        endpoint: str,
        *,
        base_url: str = PRIVATE_API_HOST,
        **kwargs: Any,
    ) -> ClientResponse:
        """Make an authenticated request to the private API host."""
        token = await self._auth.async_get_access_token()
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {token}"
        headers.setdefault("User-Agent", "HomeConnect-appStoreNA/12.15.0")
        return await self._websession.request(
            method,
            f"{base_url}{endpoint}",
            headers=headers,
            **kwargs,
        )

    async def async_list_ovens(self) -> list[OvenInfo]:
        """Discover ovens from the account appliance list.

        The old private BFF route /account/camera stopped working (404), so the
        public /api/homeappliances list is used with the same mobile token.
        """
        response = await self.async_request(
            "GET",
            PRIVATE_APPLIANCE_LIST_ENDPOINT,
            base_url=OAUTH_BASE,
            headers={"Accept": PRIVATE_APPLIANCE_LIST_ACCEPT},
        )
        response.raise_for_status()
        payload = await response.json()
        response.close()

        ovens: list[OvenInfo] = []
        for item in (payload.get("data") or {}).get("homeappliances") or []:
            if str(item.get("type") or "").lower() != "oven":
                continue
            serial = str(item.get("serialnumber") or "")
            public_ha_id = str(item.get("haId") or "")
            private_ha_id = serial or public_ha_id.split("-")[0]
            if not private_ha_id:
                continue
            ovens.append(
                OvenInfo(
                    private_ha_id=private_ha_id,
                    public_ha_id=public_ha_id or f"{private_ha_id}-001",
                    model=item.get("vib"),
                    name=item.get("name") or f"Home Connect Oven {private_ha_id[-6:]}",
                )
            )
        return ovens

    async def async_get_latest_snapshot(
        self,
        private_ha_id: str,
        *,
        force_refresh: bool = False,
    ) -> SnapshotMedia | None:
        """Return the latest still snapshot metadata for an oven."""
        response = await self.async_request(
            "GET",
            PROBE_ROUTE_DEFINITIONS["snapshot_latest_image"].format(ha_id=private_ha_id),
            headers={
                "Accept": "application/vnd.bsh.hca.v1+json",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        response.raise_for_status()
        payload = await response.json()
        response.close()
        items = payload.get("items") or []
        if not items:
            return None

        latest = max(items, key=lambda item: int(item.get("timestamp", 0) or 0))
        hash_data = latest.get("hash") or {}
        return SnapshotMedia(
            private_ha_id=private_ha_id,
            content_type=str(latest.get("mediaType") or "image/jpeg"),
            hash_value=hash_data.get("value"),
            identifier=str(latest["identifier"]),
            media_type=str(latest.get("type") or "image"),
            object_detection_identifier=latest.get("objectDetectionImageId"),
            preview_identifier=latest.get("previewImageIdentifier"),
            timestamp_ms=int(latest.get("timestamp", 0) or 0),
            upload_status=latest.get("uploadStatus"),
            position=latest.get("position"),
            metadata=_metadata_list_to_dict(latest.get("metaData") or []),
        )

    async def async_probe_latest_video(self, private_ha_id: str) -> VideoProbe:
        """Probe candidate private routes for oven video media."""
        route_statuses: dict[str, dict[str, Any]] = {}
        for key in ("snapshot_latest_video", "snapshot_gallery", "media_latest_video", "media_latest"):
            endpoint = PROBE_ROUTE_DEFINITIONS[key].format(ha_id=private_ha_id)
            try:
                response = await self.async_request("GET", endpoint)
                status = response.status
                content_type = response.headers.get("Content-Type")
                route_statuses[key] = {"status": status, "content_type": content_type}
                if status >= 400:
                    body = await response.text()
                    route_statuses[key]["body"] = body[:500]
                    response.close()
                    continue

                if content_type and "json" in content_type:
                    payload = await response.json()
                    response.close()
                    route_statuses[key]["body"] = payload
                    items = payload.get("items") or []
                    if items:
                        latest = max(items, key=lambda item: int(item.get("timestamp", 0) or 0))
                        probe = VideoProbe(
                            private_ha_id=private_ha_id,
                            available=True,
                            content_type=str(latest.get("mediaType") or content_type),
                            identifier=latest.get("identifier"),
                            media_type=str(latest.get("type") or "video"),
                            timestamp_ms=int(latest.get("timestamp", 0) or 0),
                            metadata=_metadata_list_to_dict(latest.get("metaData") or []),
                            route_statuses=route_statuses,
                        )
                        await self._auth.async_save_probe_results(
                            {"video_probe": {private_ha_id: probe.route_statuses}}
                        )
                        return probe
                    continue

                data = await response.read()
                response.close()
                route_statuses[key]["binary_length"] = len(data)
                if data:
                    probe = VideoProbe(
                        private_ha_id=private_ha_id,
                        available=True,
                        content_type=content_type,
                        media_type="video",
                        route_statuses=route_statuses,
                    )
                    await self._auth.async_save_probe_results(
                        {"video_probe": {private_ha_id: probe.route_statuses}}
                    )
                    return probe
            except ClientResponseError as err:
                route_statuses[key] = {"status": err.status, "error": str(err)}
            except Exception as err:  # noqa: BLE001
                route_statuses[key] = {"status": None, "error": str(err)}

        await self._auth.async_save_probe_results(
            {"video_probe": {private_ha_id: route_statuses}}
        )
        return VideoProbe(private_ha_id=private_ha_id, available=False, route_statuses=route_statuses)

    async def async_download_media(
        self,
        private_ha_id: str,
        media_identifier: str,
        *,
        force_refresh: bool = False,
    ) -> tuple[bytes, str]:
        """Download a private media object."""
        response = await self.async_request(
            "GET",
            f"/api/media/v1/{private_ha_id}/media/{media_identifier}",
            headers={
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        response.raise_for_status()
        data = await response.read()
        content_type = response.headers.get("Content-Type", "application/octet-stream")
        response.close()
        return data, content_type

    async def async_execute_command(
        self,
        private_ha_id: str,
        public_ha_id: str | None,
        command_key: str,
        value: Any = True,
    ) -> dict[str, Any]:
        """Execute a Home Connect command using the mobile token."""
        payload = {"data": {"key": command_key, "value": value}}
        ha_ids = [ha_id for ha_id in (public_ha_id, private_ha_id) if ha_id]
        attempts: list[dict[str, Any]] = []

        for base_url in (OAUTH_BASE, PRIVATE_API_HOST):
            for ha_id in dict.fromkeys(ha_ids):
                endpoint = f"/api/homeappliances/{ha_id}/commands/{command_key}"
                response = await self.async_request(
                    "PUT",
                    endpoint,
                    base_url=base_url,
                    json=payload,
                    headers={
                        "Accept": "application/vnd.bsh.sdk.v1+json",
                        "Content-Type": "application/vnd.bsh.sdk.v1+json",
                    },
                )
                status = response.status
                content_type = response.headers.get("Content-Type")
                body: Any = None
                if status >= 400:
                    body = await response.text()
                    attempts.append(
                        {
                            "base_url": base_url,
                            "ha_id": ha_id,
                            "endpoint": endpoint,
                            "status": status,
                            "content_type": content_type,
                            "body": body[:500],
                        }
                    )
                    response.close()
                    continue

                if content_type and "json" in content_type:
                    body = await response.json()
                else:
                    body = await response.text()
                response.close()
                return {
                    "base_url": base_url,
                    "ha_id": ha_id,
                    "endpoint": endpoint,
                    "status": status,
                    "content_type": content_type,
                    "body": body,
                    "attempts": attempts,
                }

        raise PrivateApiError(
            f"Command {command_key} failed for all candidate appliance ids: {attempts}"
        )

    async def async_get_public_feature(
        self,
        private_ha_id: str,
        public_ha_id: str | None,
        feature_type: str,
        key: str,
    ) -> dict[str, Any]:
        """Read a public Home Connect status/setting/command feature."""
        ha_ids = [ha_id for ha_id in (public_ha_id, private_ha_id) if ha_id]
        attempts: list[dict[str, Any]] = []
        for ha_id in dict.fromkeys(ha_ids):
            endpoint = f"/api/homeappliances/{ha_id}/{feature_type}/{key}"
            response = await self.async_request(
                "GET",
                endpoint,
                base_url=OAUTH_BASE,
                headers={"Accept": "application/vnd.bsh.sdk.v1+json"},
            )
            status = response.status
            content_type = response.headers.get("Content-Type")
            if status >= 400:
                body = await response.text()
                attempts.append(
                    {
                        "ha_id": ha_id,
                        "endpoint": endpoint,
                        "status": status,
                        "content_type": content_type,
                        "body": body[:500],
                    }
                )
                response.close()
                continue
            payload = await response.json()
            response.close()
            return {
                "ha_id": ha_id,
                "endpoint": endpoint,
                "status": status,
                "body": payload,
                "attempts": attempts,
            }

        raise PrivateApiError(f"Feature {feature_type}/{key} failed: {attempts}")

    async def async_set_public_setting(
        self,
        private_ha_id: str,
        public_ha_id: str | None,
        key: str,
        value: Any,
    ) -> dict[str, Any]:
        """Write a public Home Connect setting feature."""
        payload = {"data": {"key": key, "value": value}}
        ha_ids = [ha_id for ha_id in (public_ha_id, private_ha_id) if ha_id]
        attempts: list[dict[str, Any]] = []
        for ha_id in dict.fromkeys(ha_ids):
            endpoint = f"/api/homeappliances/{ha_id}/settings/{key}"
            response = await self.async_request(
                "PUT",
                endpoint,
                base_url=OAUTH_BASE,
                json=payload,
                headers={
                    "Accept": "application/vnd.bsh.sdk.v1+json",
                    "Content-Type": "application/vnd.bsh.sdk.v1+json",
                },
            )
            status = response.status
            content_type = response.headers.get("Content-Type")
            if status >= 400:
                body = await response.text()
                attempts.append(
                    {
                        "ha_id": ha_id,
                        "endpoint": endpoint,
                        "status": status,
                        "content_type": content_type,
                        "body": body[:500],
                    }
                )
                response.close()
                continue
            body = await response.text()
            response.close()
            return {
                "ha_id": ha_id,
                "endpoint": endpoint,
                "status": status,
                "content_type": content_type,
                "body": body,
                "attempts": attempts,
            }

        raise PrivateApiError(f"Setting {key} write failed: {attempts}")

    async def async_get_oven_notifications(
        self,
        private_ha_id: str,
        accept_language: str,
    ) -> list[OvenNotification]:
        """Return notification-center entries for one oven.

        The Android app uses these appliance-event notifications for user-facing
        push messages such as "turn the dish". FCM is only the delivery channel;
        this endpoint is the reproducible source of the localized content.
        """
        response = await self.async_request(
            "GET",
            "/accounts/self/notifications?channel=center",
            headers={
                "Accept": "application/json",
                "Accept-Language": accept_language,
            },
        )
        response.raise_for_status()
        payload = await response.json()
        response.close()

        notifications: list[OvenNotification] = []
        items = payload.get("data") if isinstance(payload, dict) else payload
        for item in items or []:
            if not isinstance(item, dict):
                continue
            appliance = item.get("appliance") or {}
            if str(appliance.get("haId") or "") != private_ha_id:
                continue
            notifications.append(_notification_from_item(private_ha_id, item))
        return sorted(
            notifications,
            key=lambda notification: notification.created_at or "",
            reverse=True,
        )


def _generate_code_verifier() -> str:
    raw = secrets.token_bytes(48)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _extract_code_and_state(callback_url: str) -> tuple[str, str | None]:
    parsed = urlparse(callback_url)
    params = parse_qs(parsed.query)
    error = params.get("error", [None])[0]
    if error:
        description = params.get("error_description", [""])[0]
        raise PrivateApiError(f"Private authorization failed: {error} {description}".strip())
    code = params.get("code", [None])[0]
    state = params.get("state", [None])[0]
    if not code:
        raise PrivateApiError("No authorization code found in callback URL.")
    return code, state


def _normalize_token_payload(payload: dict[str, Any]) -> dict[str, Any]:
    fetched_at = int(time.time())
    normalized = dict(payload)
    normalized["fetched_at"] = fetched_at
    if "expires_in" in payload:
        normalized["expires_at"] = fetched_at + int(payload["expires_in"])
    return normalized


def _metadata_list_to_dict(metadata: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        item["key"]: item.get("value")
        for item in metadata
        if isinstance(item, dict) and item.get("key")
    }


def _notification_from_item(private_ha_id: str, item: dict[str, Any]) -> OvenNotification:
    """Normalize the current notification-center response shape."""
    return OvenNotification(
        private_ha_id=private_ha_id,
        identifier=str(item.get("id") or ""),
        key=str(item.get("key") or ""),
        title=item.get("title"),
        description=item.get("description"),
        created_at=item.get("creationDate"),
        state=item.get("state") or item.get("status"),
        level=item.get("level"),
        category=item.get("category"),
        channels=list(item.get("channels") or []),
        read=item.get("read"),
        actions=list(item.get("actions") or []),
        raw=item,
    )
