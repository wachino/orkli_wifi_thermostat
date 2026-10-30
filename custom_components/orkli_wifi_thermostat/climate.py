"""Interfaces with the Example api sensors."""

import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import APIConnectionError, Device
from .const import DOMAIN
from .coordinator import OrkliCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Climates."""
    # This gets the data update coordinator from hass.data as specified in your __init__.py
    coordinator: OrkliCoordinator = hass.data[DOMAIN][config_entry.entry_id].coordinator

    # Enumerate all the sensors in your data value from your DataUpdateCoordinator and add an instance of your sensor class
    # to a list for each one.
    # This maybe different in your specific case, depending on how your data is structured
    climateDevices = [
        ExampleClimate(coordinator, device) for device in coordinator.data.devices
    ]

    # Create the sensors.
    async_add_entities(climateDevices)


class ExampleClimate(CoordinatorEntity, ClimateEntity, RestoreEntity):
    """Implementation of a climate entity."""

    # _unrecorded_attributes = ["target_temperature_high", "target_temperature_low"]

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return the list of supported features."""
        return (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )

    def __init__(self, coordinator: OrkliCoordinator, device: Device) -> None:
        """Initialise sensor."""
        super().__init__(coordinator)
        self.device = device
        self.device_id = device.device_id

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update sensor with latest data from coordinator."""
        # This method is called by your DataUpdateCoordinator when a successful update runs.
        self.device = self.coordinator.get_device_by_id(self.device_id)
        self.async_write_ha_state()

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Offer zone power control in the device's currently cached mode."""
        mode = self._device_hvac_mode
        return [HVACMode.OFF, mode] if mode is not None else [HVACMode.OFF]

    @property
    def _device_hvac_mode(self) -> HVACMode | None:
        """Map the existing mode interpretation without selecting a new mode."""
        return {0: HVACMode.HEAT, 1: HVACMode.COOL}.get(self.device.mode)

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the current operation mode."""
        if not self.device.on:
            return HVACMode.OFF
        return self._device_hvac_mode

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            name=f"Thermostat {self.device.name}: {self.device.device_id}",
            manufacturer="Orkli",
            model="TermoLite",
            sw_version="1.0",
            identifiers={
                (
                    DOMAIN,
                    f"{self.coordinator.data.controller_name}-{self.device.device_id}",
                )
            },
        )

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self.device.name

    @property
    def current_temperature(self) -> float | None:
        """Return the state of the entity."""
        return (
            (162.0 - float(self.device.current_temperature)) / 2.0
            if self.device.current_temperature is not None
            else None
        )

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        return (
            float(self.device.target_temperature) / 2.0
            if self.device.target_temperature is not None
            else None
        )

    @property
    def current_humidity(self) -> float | None:
        """Return the state of the entity."""
        return (
            int(float(self.device.current_humidity) / 2.55)
            if self.device.current_humidity is not None
            else None
        )

    @property
    def temperature_unit(self) -> str:
        """Return unit of temperature."""
        return UnitOfTemperature.CELSIUS

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return 15.0

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return 35.0

    @property
    def precision(self) -> float:
        """Return the precision of the system."""
        return 0.5

    @property
    def target_temperature_step(self) -> float:
        """Return the supported step of target temperature."""
        return 0.5

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Check If we have an old state
        previous_state = await self.async_get_last_state()
        if previous_state is not None:
            # Restore previous state
            self.device.current_temperature = (
                (162 - (previous_state.attributes.get("current_temperature") * 2))
                if previous_state.attributes.get("current_temperature") is not None
                else None
            )
            self.device.current_humidity = (
                (previous_state.attributes.get("current_humidity") * 2.55)
                if previous_state.attributes.get("current_humidity") is not None
                else None
            )
            self.device.target_temperature = (
                (previous_state.attributes.get("temperature") * 2)
                if previous_state.attributes.get("temperature") is not None
                else None
            )
            # Power and mode belong to coordinator data. A saved climate state
            # may describe a different season or predate a live device update.

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        await self.coordinator.async_send_temp_command(
            self.device, kwargs.get("temperature")
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Enable or disable the zone without changing its heat/cool mode."""
        if hvac_mode not in self.hvac_modes:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_hvac_mode",
                translation_placeholders={"name": self.name, "mode": str(hvac_mode)},
            )
        await self._async_set_zone_power(hvac_mode != HVACMode.OFF)

    async def async_turn_on(self) -> None:
        """Enable the zone using its existing mode, including an unknown mode."""
        await self._async_set_zone_power(True)

    async def async_turn_off(self) -> None:
        """Disable the zone without changing its stored mode."""
        await self._async_set_zone_power(False)

    async def _async_set_zone_power(self, on: bool) -> None:
        """Send the existing power command and let telemetry confirm state."""
        try:
            sent = await self.coordinator.async_send_toggle_command(self.device, on)
        except (APIConnectionError, OSError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="zone_power_failed",
                translation_placeholders={"name": self.name},
            ) from err
        if not sent:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="zone_power_failed",
                translation_placeholders={"name": self.name},
            )

    @property
    def unique_id(self) -> str:
        """Return unique id."""
        # All entities must have a unique id.  Think carefully what you want this to be as
        # changing it later will cause HA to create new entities.
        return f"{DOMAIN}-{self.device.device_unique_id}"

    @property
    def extra_state_attributes(self):
        """Return the extra state attributes."""
        # Add any additional attributes you want on your sensor.
        attrs = {}
        attrs["extra_info"] = "Extra Info"
        return attrs
