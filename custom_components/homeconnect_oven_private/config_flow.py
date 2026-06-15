"""Config flow for Home Connect Oven Private."""

from __future__ import annotations

import logging
from typing import Any, Mapping

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import aiohttp_client, config_validation as cv

from .api import MobilePrivateAuth, PrivateApiError
from .const import (
    CONF_CALLBACK_URL,
    CONF_DIAGNOSTIC_SENSORS,
    CONF_POLL_INTERVAL,
    CONF_VIDEO_DOWNLOAD_DIR,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_VIDEO_DOWNLOAD_DIR,
    DOMAIN,
    MIN_POLL_INTERVAL,
    NAME,
)

_LOGGER = logging.getLogger(__name__)


class HomeConnectOvenPrivateConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Home Connect Oven Private config flow."""

    VERSION = 1
    reauth_entry: ConfigEntry | None = None

    @property
    def _temporary_store_id(self) -> str:
        """Return the temporary store id used before the entry exists."""
        return "flow"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Start the private mobile auth flow."""
        if self._async_current_entries() and not self.reauth_entry:
            return self.async_abort(reason="single_instance_allowed")

        auth = MobilePrivateAuth(
            self.hass,
            self.reauth_entry.entry_id if self.reauth_entry else self._temporary_store_id,
            aiohttp_client.async_get_clientsession(self.hass),
        )
        await auth.async_initialize()

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await auth.async_exchange_callback_url(user_input[CONF_CALLBACK_URL])
            except PrivateApiError as err:
                _LOGGER.debug("Private auth callback exchange failed", exc_info=True)
                errors["base"] = "private_auth_failed"
            else:
                if self.reauth_entry:
                    await self.hass.config_entries.async_reload(self.reauth_entry.entry_id)
                    return self.async_abort(reason="reauth_successful")
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=NAME,
                    data={"import_store_id": self._temporary_store_id},
                )

        auth_url = await auth.async_build_authorize_url()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_CALLBACK_URL): cv.string}),
            errors=errors,
            description_placeholders={"auth_url": auth_url},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Re-authenticate an existing entry."""
        self.reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_user()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        return HomeConnectOvenPrivateOptionsFlow(config_entry)


class HomeConnectOvenPrivateOptionsFlow(config_entries.OptionsFlowWithConfigEntry):
    """Handle options for Home Connect Oven Private."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the integration options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        defaults = {
            CONF_POLL_INTERVAL: self.config_entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
            CONF_DIAGNOSTIC_SENSORS: self.config_entry.options.get(CONF_DIAGNOSTIC_SENSORS, False),
            CONF_VIDEO_DOWNLOAD_DIR: self.config_entry.options.get(CONF_VIDEO_DOWNLOAD_DIR, DEFAULT_VIDEO_DOWNLOAD_DIR),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_POLL_INTERVAL): vol.All(
                            int,
                            vol.Range(min=MIN_POLL_INTERVAL, max=3600),
                        ),
                        vol.Required(CONF_DIAGNOSTIC_SENSORS): cv.boolean,
                        vol.Required(CONF_VIDEO_DOWNLOAD_DIR): cv.string,
                    }
                ),
                defaults,
            ),
        )
