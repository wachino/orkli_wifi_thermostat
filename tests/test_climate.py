"""Test the real entity and coordinator with small Home Assistant stand-ins.

Only class definitions are loaded, avoiding integration setup and hardware access.
This verifies our hooks and byte sequences, not Home Assistant's service registry.
"""

import ast
import asyncio
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import Enum, IntFlag, auto
import json
import logging
from math import isfinite
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
    available = True

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.async_write_ha_state = Mock()

    async def async_added_to_hass(self):
        pass


class RestoreEntity:
    async def async_get_last_state(self):
        return None

    async def async_get_last_extra_data(self):
        return None


class UnitOfTemperature:
    CELSIUS = "°C"
    FAHRENHEIT = "°F"


class TemperatureConverter:
    @staticmethod
    def convert(value, from_unit, to_unit):
        assert to_unit == UnitOfTemperature.CELSIUS
        if from_unit == UnitOfTemperature.FAHRENHEIT:
            return (value - 32) / 1.8
        assert from_unit == UnitOfTemperature.CELSIUS
        return value


class DataUpdateCoordinator:
    def __init__(self, hass, logger, **kwargs):
        self.data = None
        self.listeners = []
        self.last_update_success = True

    def async_set_updated_data(self, data):
        self.data = data
        for listener in self.listeners:
            listener()


def load_definitions(filename, names, namespace):
    tree = ast.parse((COMPONENT / filename).read_text())
    definitions = [node for node in tree.body
                   if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef))
                   and node.name in names]
    module = ast.Module(body=[ast.ImportFrom(module="__future__", level=0,
                            names=[ast.alias(name="annotations")]), *definitions],
                        type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(COMPONENT / filename), "exec"), namespace)


coordinator_namespace = {
    "dataclass": dataclass, "replace": replace, "DataUpdateCoordinator": DataUpdateCoordinator,
    "Device": api.Device, "Packet": api.Packet,
    "PushAPI": api.PushAPI, "timedelta": timedelta,
    "configured_devices": api.configured_devices,
    "CONF_HOST": "host", "CONF_USERNAME": "username", "CONF_PASSWORD": "password",
    "CONF_DEVICES": "devices", "CONF_SCAN_INTERVAL": "scan_interval",
    "DEFAULT_SCAN_INTERVAL": 15, "DOMAIN": DOMAIN, "MIN_ZONE_FEEDBACK_TIMEOUT": 3600,
    "FEEDBACK_FIELDS": ("power", "mode", "setpoint", "temperature", "humidity"),
    "monotonic": Mock(return_value=100), "APIConnectionError": api.APIConnectionError,
    "UpdateFailed": type("UpdateFailed", (Exception,), {}),
    "_LOGGER": logging.getLogger("orkli_coordinator_under_test"),
}
load_definitions(
    "coordinator.py", {"FeedbackStats", "OrkliAPIData", "OrkliCoordinator"},
    coordinator_namespace,
)
OrkliCoordinator = coordinator_namespace["OrkliCoordinator"]
OrkliAPIData = coordinator_namespace["OrkliAPIData"]

climate_namespace = {
    "ClimateEntity": ClimateEntity, "ClimateEntityFeature": ClimateEntityFeature,
    "HVACMode": HVACMode, "CoordinatorEntity": CoordinatorEntity,
    "RestoreEntity": RestoreEntity, "HomeAssistantError": HomeAssistantError,
    "ExtraStoredData": object, "dataclass": dataclass, "isfinite": isfinite,
    "UnitOfTemperature": UnitOfTemperature, "TemperatureConverter": TemperatureConverter,
    "ServiceValidationError": ServiceValidationError,
    "APIConnectionError": api.APIConnectionError,
    "callback": lambda func: func, "DOMAIN": DOMAIN,
    "_LOGGER": logging.getLogger("orkli_climate_under_test"),
}
load_definitions("climate.py", {"ExampleClimate", "OrkliStoredTemperatures"}, climate_namespace)
ExampleClimate = climate_namespace["ExampleClimate"]

diagnostics_namespace = {"DOMAIN": DOMAIN}
load_definitions("diagnostics.py", {"async_get_config_entry_diagnostics"}, diagnostics_namespace)
get_diagnostics = diagnostics_namespace["async_get_config_entry_diagnostics"]


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
            controller_name="offline", async_send_command=AsyncMock(return_value=True),
            socket=object(),
        )
        self.coordinator.poll_interval = 15
        self.coordinator._feedback = {}
        self.coordinator._poll_batches_completed = 0
        self.coordinator._poll_batches_failed = 0
        self.coordinator.devices = [self.device]
        self.coordinator.last_update_success = True
        coordinator_namespace["monotonic"].return_value = 100
        self.coordinator.api.connected = True
        self.coordinator.data = OrkliAPIData("offline", [self.device])
        self.entity = ExampleClimate(self.coordinator, self.device)
        self.entity.hass = SimpleNamespace(
            config=SimpleNamespace(
                units=SimpleNamespace(temperature_unit=UnitOfTemperature.CELSIUS)
            )
        )
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

    async def test_legacy_config_starts_unknown_and_applies_partial_feedback(self):
        entry = SimpleNamespace(
            data={"host": "offline.invalid", "username": "", "password": "",
                  "devices": [vars(replace(self.device, mode=0, on=False))]},
            options={}, unique_id="offline", entry_id="test",
        )
        coordinator = OrkliCoordinator(self.entity.hass, entry)
        device, = coordinator.devices
        self.assertIsNone(device.on)
        self.assertIsNone(device.mode)
        entity = ExampleClimate(coordinator, device)
        coordinator.listeners = [entity._handle_coordinator_update]

        async def feedback(register, value):
            await coordinator.devices_update_callback(
                coordinator.create_packet(8, 8, 4, register, value)
            )

        await feedback(11, 120)
        self.assertTrue(entity.available)
        self.assertIsNone(entity.hvac_mode)
        self.assertEqual(entity.hvac_modes, [HVACMode.OFF])
        await feedback(8, 0)
        self.assertEqual(entity.hvac_mode, HVACMode.OFF)
        self.assertIsNone(entity.device.mode)
        await feedback(8, 3)
        self.assertIsNone(entity.hvac_mode)
        await feedback(9, 1)
        self.assertEqual(entity.hvac_mode, HVACMode.COOL)
        await feedback(8, 0)
        self.assertEqual(entity.hvac_mode, HVACMode.OFF)

    async def test_mode_feedback_alone_keeps_power_unknown(self):
        self.device.on = None
        self.device.mode = None
        await self.coordinator.devices_update_callback(
            self.coordinator.create_packet(8, 8, 4, 9, 2)
        )
        self.assertIsNone(self.entity.hvac_mode)
        self.assertEqual(self.entity.hvac_modes, [HVACMode.OFF, HVACMode.HEAT])

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
            api.Packet(bytes.fromhex("3b 08 ff 04 08 03 16"))
        )
        self.assertEqual(self.entity.hvac_mode, HVACMode.COOL)
        self.entity.async_write_ha_state.assert_called_once()

    async def test_turn_off_waits_for_device_feedback(self):
        self.device.on = True
        await self.entity.async_turn_off()
        self.assertEqual(self.entity.hvac_mode, HVACMode.COOL)
        await self.coordinator.devices_update_callback(
            api.Packet(bytes.fromhex("3b 08 ff 04 08 02 15"))
        )
        self.assertEqual(self.entity.hvac_mode, HVACMode.OFF)

    async def test_feedback_mode_change_updates_available_modes(self):
        await self.coordinator.devices_update_callback(
            api.Packet(bytes.fromhex("3b 08 ff 04 09 02 16"))
        )
        self.assertEqual(self.entity.hvac_modes, [HVACMode.OFF, HVACMode.HEAT])
        with self.assertRaises(ServiceValidationError):
            await self.entity.async_set_hvac_mode(HVACMode.COOL)
        self.send.assert_not_awaited()

    async def test_feedback_masks_power_and_handles_zero(self):
        for value, expected in ((0x83, True), (0, False)):
            await self.coordinator.devices_update_callback(
                self.coordinator.create_packet(8, 255, 4, 8, value)
            )
            self.assertEqual(self.entity.device.on, expected)

    async def test_feedback_clears_unknown_mode(self):
        await self.coordinator.devices_update_callback(
            self.coordinator.create_packet(8, 255, 4, 9, 0)
        )
        self.assertEqual(self.entity.hvac_modes, [HVACMode.OFF])

    async def test_feedback_ignores_other_addresses_and_commands(self):
        for address, command in ((1, 4), (8, 10)):
            await self.coordinator.devices_update_callback(
                self.coordinator.create_packet(address, 255, command, 8, 3)
            )
        self.assertFalse(self.entity.device.on)
        self.entity.async_write_ha_state.assert_not_called()

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

    async def test_failed_temperature_write_raises_without_a_read(self):
        for result in (False, api.APIConnectionError("offline")):
            with self.subTest(result=result):
                self.send.reset_mock()
                self.send.side_effect = [result]
                with self.assertRaises(HomeAssistantError) as caught:
                    await self.entity.async_set_temperature(temperature=21.5)
                self.assertEqual(caught.exception.translation_key, "target_temperature_failed")
                self.send.assert_awaited_once()
                self.assertIsNone(self.entity.device.target_temperature)

    async def test_failed_follow_up_does_not_fail_temperature_write(self):
        for result in (False, api.APIConnectionError("offline")):
            with self.subTest(result=result):
                self.send.reset_mock()
                self.send.side_effect = [True, result]
                await self.entity.async_set_temperature(temperature=21.5)
                self.assertEqual(self.send.await_count, 2)
                self.assertIsNone(self.entity.device.target_temperature)

    async def test_zone_availability_expires_and_recovers_on_feedback(self):
        self.assertFalse(self.entity.available)
        feedback = self.coordinator.create_packet(8, 255, 4, 11, 120)
        await self.coordinator.devices_update_callback(feedback)
        self.assertTrue(self.entity.available)
        self.assertFalse(self.coordinator.has_recent_feedback(replace(self.device, address=9)))

        coordinator_namespace["monotonic"].return_value = 3699
        self.assertTrue(self.entity.available)
        coordinator_namespace["monotonic"].return_value = 3700
        self.assertFalse(self.entity.available)
        await self.coordinator.devices_update_callback(
            self.coordinator.create_packet(8, 255, 4, 11, 0)
        )
        self.assertFalse(self.entity.available)
        await self.coordinator.devices_update_callback(feedback)
        self.assertTrue(self.entity.available)

    async def test_zone_timeout_scales_with_longer_scan_interval(self):
        self.coordinator.poll_interval = 1800
        await self.coordinator.devices_update_callback(
            self.coordinator.create_packet(8, 255, 4, 11, 120)
        )
        coordinator_namespace["monotonic"].return_value = 5499
        self.assertTrue(self.entity.available)
        coordinator_namespace["monotonic"].return_value = 5500
        self.assertFalse(self.entity.available)

    async def test_diagnostics_counts_repeated_feedback_per_zone_and_field(self):
        other = replace(self.device, address=9, name="Private name")
        self.coordinator.devices.append(other)
        self.coordinator.data.devices.append(other)
        self.coordinator.host = "private-host"
        entry = SimpleNamespace(entry_id="test", data={"password": "secret"})
        hass = SimpleNamespace(data={DOMAIN: {"test": SimpleNamespace(
            coordinator=self.coordinator
        )}})

        humidity = self.coordinator.create_packet(8, 8, 4, 102, 150)
        await self.coordinator.devices_update_callback(humidity)
        coordinator_namespace["monotonic"].return_value = 175
        await self.coordinator.devices_update_callback(humidity)
        coordinator_namespace["monotonic"].return_value = 180
        await self.coordinator.devices_update_callback(
            self.coordinator.create_packet(8, 8, 4, 102, 0)
        )
        await self.coordinator.devices_update_callback(
            self.coordinator.create_packet(9, 9, 4, 11, 120)
        )
        diagnostics = await get_diagnostics(hass, entry)
        self.assertTrue(diagnostics["loaded"])
        self.assertTrue(diagnostics["connected"])
        self.assertEqual(diagnostics["zone_feedback_timeout_seconds"], 3600)
        first, second = diagnostics["zones"]
        self.assertEqual(first["fields"]["humidity"], {
            "reports": 2, "seconds_since_last_report": 5, "longest_gap_seconds": 75,
        })
        self.assertEqual(second["fields"]["humidity"], {
            "reports": 0, "seconds_since_last_report": None, "longest_gap_seconds": None,
        })
        self.assertEqual(second["fields"]["temperature"], {
            "reports": 1, "seconds_since_last_report": 0, "longest_gap_seconds": None,
        })
        serialized = json.dumps(diagnostics)
        for private in ("private-host", "secret", "Living room", "Private name"):
            self.assertNotIn(private, serialized)

    async def test_diagnostics_when_the_entry_is_unloaded(self):
        self.assertEqual(
            await get_diagnostics(SimpleNamespace(data={}), SimpleNamespace(entry_id="test")),
            {"loaded": False},
        )

    async def test_poll_stops_at_first_failed_write_and_can_recover(self):
        self.send.side_effect = [True, False]
        with self.assertRaises(coordinator_namespace["UpdateFailed"]):
            await self.coordinator.async_update_data()
        self.assertEqual(self.send.await_count, 2)
        self.assertEqual(self.coordinator._poll_batches_failed, 1)
        self.assertEqual(self.coordinator._poll_batches_completed, 0)
        self.send.side_effect = None
        await self.coordinator.async_update_data()
        self.assertEqual(self.send.await_count, 6)
        self.assertEqual(self.coordinator._poll_batches_completed, 1)

    async def test_poll_does_not_continue_on_a_replacement_socket(self):
        async def replace_connection(packet):
            self.coordinator.api.socket = object()
            return True

        self.send.side_effect = replace_connection
        with self.assertRaises(coordinator_namespace["UpdateFailed"]):
            await self.coordinator.async_update_data()
        self.send.assert_awaited_once()

    async def test_poll_keeps_existing_frames_and_feedback_received_during_send(self):
        self.coordinator.data.devices.append(replace(self.device, device_id=3))

        async def send_and_receive(packet):
            await self.coordinator.devices_update_callback(
                self.coordinator.create_packet(8, 8, 4, 11, 120)
            )
            return True

        self.send.side_effect = send_and_receive
        updated = await self.coordinator.async_update_data()
        common = [bytes.fromhex(frame) for frame in (
            "3b 01 fe 04 23 00 26", "3b 01 ff 0a 00 00 0a", "3b ff ff 0a 00 00 08"
        )]
        self.assertEqual(self.sent_bytes(), [
            *common, bytes.fromhex("3b 08 ff 04 0b 00 16"),
            *common, bytes.fromhex("3b 08 ff 04 0f 00 1a"),
        ])
        self.assertEqual(updated.devices[0].current_temperature, 120)

    async def test_restoration_fills_missing_values_only(self):
        self.device.current_temperature = 120
        self.device.current_humidity = 128
        self.entity.async_get_last_state = AsyncMock(return_value=SimpleNamespace(
            attributes={"current_temperature": 19, "current_humidity": 30, "temperature": 21.5}
        ))
        await self.entity.async_added_to_hass()
        self.assertEqual(self.device.current_temperature, 120)
        self.assertEqual(self.device.current_humidity, 128)
        self.assertEqual(self.device.target_temperature, 43)

    async def test_legacy_temperatures_are_converted_from_fahrenheit(self):
        self.entity.async_get_last_state = AsyncMock(return_value=SimpleNamespace(
            attributes={"current_temperature": 70, "temperature": 72,
                        "temperature_unit": UnitOfTemperature.FAHRENHEIT}
        ))
        await self.entity.async_added_to_hass()
        self.assertAlmostEqual(self.entity.native_current_temperature, 190 / 9)
        self.assertAlmostEqual(self.entity.native_target_temperature, 200 / 9)
        self.send.assert_not_awaited()

    async def test_legacy_state_without_unit_uses_configured_display_unit(self):
        self.entity.hass.config.units.temperature_unit = UnitOfTemperature.FAHRENHEIT
        self.entity.async_get_last_state = AsyncMock(return_value=SimpleNamespace(
            attributes={"current_temperature": 68, "temperature": 77}
        ))
        await self.entity.async_added_to_hass()
        self.assertEqual(self.entity.native_current_temperature, 20)
        self.assertEqual(self.entity.native_target_temperature, 25)

    async def test_restores_native_temperatures_across_display_unit_changes(self):
        self.device.current_temperature = 121
        self.device.target_temperature = 43
        saved = json.loads(json.dumps(self.entity.extra_restore_state_data.as_dict()))
        self.device.current_temperature = None
        self.device.target_temperature = 46  # New device feedback takes precedence.
        self.entity.hass.config.units.temperature_unit = UnitOfTemperature.FAHRENHEIT
        self.entity.async_get_last_extra_data = AsyncMock(
            return_value=SimpleNamespace(as_dict=lambda: saved)
        )
        self.entity.async_get_last_state = AsyncMock(return_value=SimpleNamespace(
            attributes={"current_temperature": 69, "temperature": 71}
        ))
        await self.entity.async_added_to_hass()
        self.assertEqual(self.entity.native_current_temperature, 20.5)
        self.assertEqual(self.entity.native_target_temperature, 23)
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
