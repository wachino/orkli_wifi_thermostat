"""Exercise the integration's real unload hook with Home Assistant stand-ins."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


INIT_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components/orkli_wifi_thermostat/__init__.py"
)
# Load the actual hook without importing Home Assistant or unrelated platforms.
tree = ast.parse(INIT_PATH.read_text())
hook = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "async_unload_entry")
namespace = {
    "HomeAssistant": object, "ConfigEntry": object,
    "DOMAIN": "orkli_wifi_thermostat", "PLATFORMS": ["climate"],
}
exec(compile(ast.Module(body=[hook], type_ignores=[]), str(INIT_PATH), "exec"), namespace)
unload_entry = namespace["async_unload_entry"]


class UnloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = SimpleNamespace(
            coordinator=SimpleNamespace(async_shutdown=AsyncMock()),
            cancel_update_listener=Mock(),
        )
        self.entry = SimpleNamespace(entry_id="entry")
        self.hass = SimpleNamespace(
            data={"orkli_wifi_thermostat": {"entry": self.runtime}},
            config_entries=SimpleNamespace(async_unload_platforms=AsyncMock(return_value=True)),
        )

    async def test_successful_unload_closes_coordinator_before_removing_runtime(self):
        async def shutdown():
            self.assertIs(self.hass.data["orkli_wifi_thermostat"]["entry"], self.runtime)
            self.hass.config_entries.async_unload_platforms.assert_awaited_once()

        self.runtime.coordinator.async_shutdown.side_effect = shutdown
        self.assertTrue(await unload_entry(self.hass, self.entry))
        self.runtime.coordinator.async_shutdown.assert_awaited_once()
        self.runtime.cancel_update_listener.assert_called_once()
        self.assertNotIn("entry", self.hass.data["orkli_wifi_thermostat"])

    async def test_failed_platform_unload_preserves_listener_and_connection(self):
        self.hass.config_entries.async_unload_platforms.return_value = False
        self.assertFalse(await unload_entry(self.hass, self.entry))
        self.runtime.coordinator.async_shutdown.assert_not_awaited()
        self.runtime.cancel_update_listener.assert_not_called()
        self.assertIs(self.hass.data["orkli_wifi_thermostat"]["entry"], self.runtime)
