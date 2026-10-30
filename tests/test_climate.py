"""Test the real entity and coordinator with small Home Assistant stand-ins.

Only class definitions are loaded, avoiding integration setup and hardware access.
This verifies our hooks and byte sequences, not Home Assistant's service registry.
"""

import ast
import asyncio
from dataclasses import dataclass, replace
from enum import Enum, IntFlag, auto
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_api import api


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/orkli_wifi_thermostat"
DOMAIN = "orkli_wifi_thermostat"


class HVACMode(str, Enum):
    OFF = "off"
    HEAT = "heat"
    COOL = "cool"
    AUTO = "auto"
    HEAT_COOL = "heat_cool"

    def __str__(self):
        return self.value


class ClimateEntityFeature(IntFlag):
    TARGET_TEMPERATURE = auto()
    TURN_ON = auto()
    TURN_OFF = auto()


class HomeAssistantError(Exception):
    def __init__(self, *args, translation_domain=None, translation_key=None,
                 translation_placeholders=None):
        super().__init__(*args)
        self.translation_domain = translation_domain
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders


class ServiceValidationError(HomeAssistantError):
    pass


class ClimateEntity:
    async def async_set_hvac_mode(self, hvac_mode):
        raise NotImplementedError

    async def async_handle_set_hvac_mode_service(self, hvac_mode):
        # The documented base-class contract: validate before dispatching.
        if hvac_mode not in self.hvac_modes:
            raise ServiceValidationError("Unsupported mode")
        await self.async_set_hvac_mode(hvac_mode)


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.async_write_ha_state = Mock()

    async def async_added_to_hass(self):
        pass


class RestoreEntity:
    async def async_get_last_state(self):
        return None


class DataUpdateCoordinator:
    def async_set_updated_data(self, data):
        self.data = data
        for listener in self.listeners:
            listener()


def load_classes(filename, names, namespace):
    tree = ast.parse((COMPONENT / filename).read_text())
    classes = [node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name in names]
    module = ast.Module(body=[ast.ImportFrom(module="__future__", level=0,
                            names=[ast.alias(name="annotations")]), *classes],
                        type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(COMPONENT / filename), "exec"), namespace)


coordinator_namespace = {
    "dataclass": dataclass, "DataUpdateCoordinator": DataUpdateCoordinator,
    "Device": api.Device, "Packet": api.Packet,
    "_LOGGER": logging.getLogger("orkli_coordinator_under_test"),
}
load_classes("coordinator.py", {"OrkliAPIData", "OrkliCoordinator"}, coordinator_namespace)
OrkliCoordinator = coordinator_namespace["OrkliCoordinator"]
OrkliAPIData = coordinator_namespace["OrkliAPIData"]

climate_namespace = {
    "ClimateEntity": ClimateEntity, "ClimateEntityFeature": ClimateEntityFeature,
    "HVACMode": HVACMode, "CoordinatorEntity": CoordinatorEntity,
    "RestoreEntity": RestoreEntity, "HomeAssistantError": HomeAssistantError,
    "ServiceValidationError": ServiceValidationError,
    "APIConnectionError": api.APIConnectionError,
    "callback": lambda func: func, "DOMAIN": DOMAIN,
    "_LOGGER": logging.getLogger("orkli_climate_under_test"),
}
load_classes("climate.py", {"ExampleClimate"}, climate_namespace)
ExampleClimate = climate_namespace["ExampleClimate"]


class ClimateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for target in ("socket.socket", "FTP"):
            patcher = patch(f"{api.__name__}.{target}",
                            side_effect=AssertionError("No network allowed"))
            patcher.start()
            self.addCleanup(patcher.stop)
        template = api.API("offline.invalid", "", "").create_device(0)
        self.device = replace(template, device_id=2, address=8, mode=1, on=False,
                              name="Living room")
        self.coordinator = OrkliCoordinator.__new__(OrkliCoordinator)
        self.coordinator.api = SimpleNamespace(
            controller_name="offline", async_send_command=AsyncMock(return_value=True)
        )
        self.coordinator.data = OrkliAPIData("offline", [self.device])
        self.entity = ExampleClimate(self.coordinator, self.device)
        self.coordinator.listeners = [self.entity._handle_coordinator_update]

    @property
    def send(self):
        return self.coordinator.api.async_send_command

    def sent_bytes(self):
        return [call.args[0].message for call in self.send.await_args_list]

    def test_implements_entity_hook_and_inherits_service_validation(self):
        self.assertIn("async_set_hvac_mode", ExampleClimate.__dict__)
        self.assertNotIn("async_handle_set_hvac_mode_service", ExampleClimate.__dict__)

    def test_advertises_power_and_temperature_features(self):
        features = self.entity.supported_features
        for feature in (ClimateEntityFeature.TARGET_TEMPERATURE,
                        ClimateEntityFeature.TURN_ON, ClimateEntityFeature.TURN_OFF):
            self.assertTrue(features & feature)

    def test_available_modes_follow_cached_device_mode(self):
        for mode, expected in ((0, [HVACMode.OFF, HVACMode.HEAT]),
                               (1, [HVACMode.OFF, HVACMode.COOL]),
                               (None, [HVACMode.OFF]), (9, [HVACMode.OFF])):
            with self.subTest(mode=mode):
                self.device.mode = mode
                self.assertEqual(self.entity.hvac_modes, expected)

    def test_reported_mode_follows_power_and_cached_mode(self):
        self.assertEqual(self.entity.hvac_mode, HVACMode.OFF)
        self.device.on = True
        for mode, expected in ((0, HVACMode.HEAT), (1, HVACMode.COOL), (None, None)):
            with self.subTest(mode=mode):
                self.device.mode = mode
                self.assertEqual(self.entity.hvac_mode, expected)

    async def test_turn_on_sends_only_existing_zone_on_frame(self):
        await self.entity.async_turn_on()
        self.assertEqual(self.sent_bytes(), [bytes.fromhex("3b 08 ff 04 08 03 16")])
        self.assertEqual(self.device.mode, 1)

    async def test_turn_off_sends_only_existing_zone_off_frame(self):
        self.device.on = True
        await self.entity.async_turn_off()
        self.assertEqual(self.sent_bytes(), [bytes.fromhex("3b 08 ff 04 08 02 15")])
        self.assertEqual(self.device.mode, 1)

    async def test_turn_on_never_selects_a_mode_even_when_unknown(self):
        self.device.mode = None
        await self.entity.async_turn_on()
        self.assertIsNone(self.device.mode)
        self.assertEqual(self.sent_bytes(), [bytes.fromhex("3b 08 ff 04 08 03 16")])

    async def test_matching_heat_or_cool_request_sends_zone_on(self):
        for mode, request in ((0, HVACMode.HEAT), (1, HVACMode.COOL)):
            with self.subTest(mode=mode):
                self.send.reset_mock()
                self.device.mode = mode
                await self.entity.async_set_hvac_mode(request)
                self.assertEqual(self.sent_bytes(), [bytes.fromhex("3b 08 ff 04 08 03 16")])
                self.assertEqual(self.device.mode, mode)

    async def test_off_request_sends_zone_off(self):
        await self.entity.async_set_hvac_mode(HVACMode.OFF)
        self.assertEqual(self.sent_bytes(), [bytes.fromhex("3b 08 ff 04 08 02 15")])

    async def test_service_handler_delegates_to_entity_hook(self):
        await self.entity.async_handle_set_hvac_mode_service(HVACMode.COOL)
        self.assertEqual(self.sent_bytes(), [bytes.fromhex("3b 08 ff 04 08 03 16")])

    async def test_service_handler_rejects_opposite_mode(self):
        with self.assertRaises(ServiceValidationError):
            await self.entity.async_handle_set_hvac_mode_service(HVACMode.HEAT)
        self.send.assert_not_awaited()

    async def test_direct_hook_rejects_unsupported_or_opposite_modes(self):
        for mode, requested in ((1, HVACMode.HEAT), (0, HVACMode.COOL),
                                (1, HVACMode.AUTO), (1, "invalid"),
                                (None, HVACMode.HEAT), (None, HVACMode.COOL)):
            with self.subTest(mode=mode, requested=requested):
                self.device.mode = mode
                with self.assertRaises(ServiceValidationError):
                    await self.entity.async_set_hvac_mode(requested)
                self.assertEqual(self.device.mode, mode)
                self.send.assert_not_awaited()

    async def test_failed_send_raises_without_changing_state(self):
        self.send.return_value = False
        for action in (self.entity.async_turn_on, self.entity.async_turn_off):
            with self.subTest(action=action.__name__):
                with self.assertRaises(HomeAssistantError) as caught:
                    await action()
                self.assertEqual(caught.exception.translation_domain, DOMAIN)
                self.assertEqual(caught.exception.translation_key, "zone_power_failed")
                self.assertFalse(self.device.on)
                self.assertEqual(self.device.mode, 1)
                self.entity.async_write_ha_state.assert_not_called()

    async def test_connection_exception_is_reported_as_action_failure(self):
        for error in (api.APIConnectionError("offline"), OSError("offline")):
            with self.subTest(error=type(error).__name__):
                self.send.side_effect = error
                with self.assertRaises(HomeAssistantError) as caught:
                    await self.entity.async_turn_on()
                self.assertIs(caught.exception.__cause__, error)
                self.assertFalse(self.device.on)

    async def test_cancellation_is_not_translated_or_swallowed(self):
        self.send.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.entity.async_turn_on()
        self.assertFalse(self.device.on)

    async def test_sent_command_waits_for_device_feedback_to_change_state(self):
        await self.entity.async_turn_on()
        self.assertEqual(self.entity.hvac_mode, HVACMode.OFF)
        self.entity.async_write_ha_state.assert_not_called()
        await self.coordinator.devices_update_callback(
            api.Packet(bytes.fromhex("3b 01 ff 04 08 03 0f"))
        )
        self.assertEqual(self.entity.hvac_mode, HVACMode.COOL)
        self.entity.async_write_ha_state.assert_called_once()

    async def test_turn_off_waits_for_device_feedback(self):
        self.device.on = True
        await self.entity.async_turn_off()
        self.assertEqual(self.entity.hvac_mode, HVACMode.COOL)
        await self.coordinator.devices_update_callback(
            api.Packet(bytes.fromhex("3b 01 ff 04 08 02 0e"))
        )
        self.assertEqual(self.entity.hvac_mode, HVACMode.OFF)

    async def test_feedback_mode_change_updates_available_modes(self):
        await self.coordinator.devices_update_callback(
            api.Packet(bytes.fromhex("3b 01 ff 04 09 02 0f"))
        )
        self.assertEqual(self.entity.hvac_modes, [HVACMode.OFF, HVACMode.HEAT])
        with self.assertRaises(ServiceValidationError):
            await self.entity.async_set_hvac_mode(HVACMode.COOL)
        self.send.assert_not_awaited()

    async def test_saved_state_does_not_override_coordinator_power_or_mode(self):
        for saved_state in ("off", "heat", "cool", "unknown", "unavailable"):
            with self.subTest(saved_state=saved_state):
                self.device.on = True
                self.device.mode = 1
                self.entity.async_get_last_state = AsyncMock(
                    return_value=SimpleNamespace(state=saved_state, attributes={})
                )
                await self.entity.async_added_to_hass()
                self.assertTrue(self.device.on)
                self.assertEqual(self.device.mode, 1)
        self.send.assert_not_awaited()

    async def test_unchanged_temperature_command_bytes(self):
        await self.entity.async_set_temperature(temperature=21.5)
        self.assertEqual(self.sent_bytes(), [bytes.fromhex("3b 08 ff 04 0a 2b 40"),
                                            bytes.fromhex("3b 08 ff 04 0b 00 16")])

    def test_new_errors_have_english_and_spanish_translations(self):
        for filename in ("strings.json", "translations/en.json", "translations/es.json"):
            with self.subTest(filename=filename):
                exceptions = json.loads((COMPONENT / filename).read_text())["exceptions"]
                for key in ("zone_power_failed", "unsupported_hvac_mode"):
                    self.assertIn("{name}", exceptions[key]["message"])


if __name__ == "__main__":
    unittest.main()
