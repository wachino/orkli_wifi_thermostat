"""Exercise connection validation and reconfiguration without Home Assistant."""

import ast
from dataclasses import replace
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_climate import COMPONENT, HomeAssistantError
from test_api import api


class ConfigFlow:
    def __init_subclass__(cls, *, domain):
        super().__init_subclass__()


path = COMPONENT / "config_flow.py"
tree = ast.parse(path.read_text())
names = {"validate_input", "OrkliConfigFlow", "CannotConnect", "InvalidAuth"}
namespace = {
    "API": api.API,
    "APIAuthError": api.APIAuthError,
    "APIConnectionError": api.APIConnectionError,
    "configured_devices": api.configured_devices,
    "ConfigFlow": ConfigFlow,
    "HomeAssistantError": HomeAssistantError,
    "callback": lambda func: func,
    "DOMAIN": "orkli_wifi_thermostat",
    "CONF_DEVICES": "devices",
    "CONF_HOST": "host",
    "CONF_PASSWORD": "password",
    "CONF_USERNAME": "username",
    "vol": SimpleNamespace(Schema=lambda schema: schema,
                          Required=lambda field, **kwargs: field),
    "_LOGGER": logging.getLogger(__name__),
}
future = ast.ImportFrom(module="__future__", level=0, names=[ast.alias(name="annotations")])
nodes = [node for node in tree.body if getattr(node, "name", None) in names]
module = ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[]))
exec(compile(module, str(path), "exec"), namespace)


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old = api.API("old.invalid", "", "").create_device(0)
        self.match = replace(self.old, name="Updated", device_unique_id="new_0")
        self.new = replace(self.old, device_id=1, output=1, device_unique_id="new_1")
        self.entry = SimpleNamespace(unique_id="old", entry_id="test", data={
            "host": "old.invalid", "username": "user", "password": "old",
            "devices": [vars(self.old)],
        })
        self.input = {"host": "new.invalid", "username": "user", "password": "new"}
        self.client = Mock(spec=api.API)
        self.client.get_initial_devices.return_value = [self.match, self.new]
        self.api_patch = patch.dict(namespace, {"API": Mock(return_value=self.client)})
        self.api_patch.start()
        self.addCleanup(self.api_patch.stop)

        async def run_job(func):
            return func()

        self.flow = namespace["OrkliConfigFlow"]()
        self.flow.context = {"entry_id": "test"}
        self.flow.hass = SimpleNamespace(
            async_add_executor_job=AsyncMock(side_effect=run_job),
            config_entries=SimpleNamespace(async_get_entry=Mock(return_value=self.entry)),
        )
        self.flow.async_set_unique_id = AsyncMock()
        self.flow._abort_if_unique_id_configured = Mock()
        self.flow.async_show_form = Mock()
        self.flow.async_update_reload_and_abort = Mock()

    async def test_reconfigure_uses_new_zone_list_and_preserves_matching_id(self):
        await self.flow.async_step_reconfigure(self.input)
        data = self.flow.async_update_reload_and_abort.call_args.kwargs["data"]
        self.assertEqual(data["host"], "new.invalid")
        self.flow._abort_if_unique_id_configured.assert_called_once()
        self.assertEqual(data["devices"][0]["name"], "Updated")
        self.assertEqual(data["devices"][0]["device_unique_id"], self.old.device_unique_id)
        self.assertEqual(data["devices"][0]["device_registry_id"], "old_invalid-0")
        self.assertEqual(data["devices"][1]["device_unique_id"], "new_1")
        self.assertEqual(
            [call[0] for call in self.client.method_calls],
            ["connect", "disconnect", "get_initial_devices"],
        )
        self.flow.hass.async_add_executor_job.assert_awaited_once()

    async def test_failed_tcp_connection_keeps_entry_and_skips_ftp(self):
        self.client.connect.side_effect = api.APIConnectionError("unreachable")
        await self.flow.async_step_reconfigure(self.input)
        self.client.disconnect.assert_called_once()
        self.client.get_initial_devices.assert_not_called()
        self.flow.async_update_reload_and_abort.assert_not_called()
        self.assertEqual(self.flow.async_show_form.call_args.kwargs["errors"],
                         {"base": "cannot_connect"})

    async def test_rejected_ftp_login_keeps_entry(self):
        self.client.get_initial_devices.side_effect = api.APIAuthError("rejected")
        await self.flow.async_step_reconfigure(self.input)
        self.flow.async_update_reload_and_abort.assert_not_called()
        self.assertEqual(self.flow.async_show_form.call_args.kwargs["errors"],
                         {"base": "invalid_auth"})
