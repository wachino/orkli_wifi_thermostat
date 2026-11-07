"""Config flow for Orkli Wifi Thermostat integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_DEVICES,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .api import API, APIAuthError, APIConnectionError, configured_devices
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, MIN_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, description={"suggested_value": "192.168.1.184"}): str,
        vol.Required(CONF_USERNAME, description={"suggested_value": "test"}): str,
        vol.Required(CONF_PASSWORD, description={"suggested_value": "1234"}): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    api = API(data[CONF_HOST], data[CONF_USERNAME], data[CONF_PASSWORD])

    def discover_devices():
        try:
            api.connect()
        finally:
            api.disconnect()
        return api.get_initial_devices()

    try:
        devices = await hass.async_add_executor_job(discover_devices)
    except APIAuthError as err:
        raise InvalidAuth from err
    except APIConnectionError as err:
        raise CannotConnect from err
    return {
        "title": f"Orkli Wifi Thermostat Integration - {data[CONF_HOST]}",
        "devices": devices,
    }


class OrkliConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Integration."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OrkliOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        # Called when you initiate adding an integration via the UI
        errors: dict[str, str] = {}

        if user_input is not None:
            info = await self._async_validate(user_input, errors)
            if info is not None:
                await self.async_set_unique_id(info["title"])
                self._abort_if_unique_id_configured()
                data = {
                    **user_input,
                    CONF_DEVICES: [vars(device) for device in info["devices"]],
                }
                return self.async_create_entry(title=info["title"], data=data)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add reconfigure step to allow to reconfigure a config entry."""
        errors: dict[str, str] = {}
        config_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )

        if user_input is not None:
            data = {**config_entry.data, **user_input}
            info = await self._async_validate(data, errors)
            if info is not None:
                unique_id = info["title"]
                if unique_id != config_entry.unique_id:
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured()
                old_devices = {
                    (device.address, device.device_id): device
                    for device in configured_devices(
                        config_entry.data[CONF_DEVICES],
                        config_entry.data[CONF_HOST],
                        config_entry.entry_id,
                    )
                }
                for device in info["devices"]:
                    if (old_device := old_devices.get((device.address, device.device_id))):
                        device.device_unique_id = old_device.device_unique_id
                        device.device_registry_id = old_device.device_registry_id
                data[CONF_DEVICES] = [vars(device) for device in info["devices"]]
                return self.async_update_reload_and_abort(
                    config_entry,
                    unique_id=unique_id,
                    data=data,
                    reason="reconfigure_successful",
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME, default=config_entry.data[CONF_USERNAME]
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_HOST, default=config_entry.data[CONF_HOST]): str,
                }
            ),
            errors=errors,
        )

    async def _async_validate(self, data, errors):
        """Validate connection settings for either entry flow."""
        try:
            return await validate_input(self.hass, data)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:
            _LOGGER.exception("Unexpected exception validating connection settings")
            errors["base"] = "unknown"
        return None


class OrkliOptionsFlowHandler(OptionsFlow):
    """Handles the options flow."""

    async def async_step_init(self, user_input=None):
        """Handle options flow."""
        if user_input is not None:
            options = self.config_entry.options | user_input
            return self.async_create_entry(data=options)

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): (vol.All(vol.Coerce(int), vol.Clamp(min=MIN_SCAN_INTERVAL))),
            }
        )

        return self.async_show_form(step_id="init", data_schema=data_schema)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
