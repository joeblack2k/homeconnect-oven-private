"""Home Connect Oven Private integration."""

from __future__ import annotations

from aiohttp import ClientResponseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import aiohttp_client

from .api import AsyncMobilePrivateApi, MobilePrivateAuth, PrivateAuthExpiredError
from .const import DOMAIN, PLATFORMS
from .coordinator import HomeConnectPrivateOvenManager


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration domain."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry."""
    websession = aiohttp_client.async_get_clientsession(hass)
    auth = MobilePrivateAuth(hass, entry.entry_id, websession)
    await auth.async_initialize()
    if not auth.is_configured and entry.data.get("import_store_id"):
        temp_auth = MobilePrivateAuth(hass, entry.data["import_store_id"], websession)
        await temp_auth.async_initialize()
        if temp_auth.is_configured:
            await auth.async_import_state(temp_auth.export_state())
    private_api = AsyncMobilePrivateApi(auth, websession)
    manager = HomeConnectPrivateOvenManager(hass, entry, private_api)

    try:
        await manager.async_get_ovens(refresh=True)
    except PrivateAuthExpiredError as err:
        raise ConfigEntryAuthFailed("Private oven token refresh failed") from err
    except ClientResponseError as err:
        if err.status in (401, 403):
            raise ConfigEntryAuthFailed("Private oven auth is no longer valid") from err
        raise ConfigEntryNotReady from err
    except Exception as err:  # noqa: BLE001
        raise ConfigEntryNotReady from err

    hass.data[DOMAIN][entry.entry_id] = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        manager = hass.data[DOMAIN].pop(entry.entry_id, None)
        if manager:
            await manager.async_shutdown()
    return unload_ok
