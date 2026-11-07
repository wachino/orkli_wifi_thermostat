"""Example integration using DataUpdateCoordinator."""

from dataclasses import dataclass, replace
from datetime import timedelta
import logging
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DEVICES,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import APIConnectionError, Device, Packet, PushAPI, configured_devices
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, MIN_ZONE_FEEDBACK_TIMEOUT

_LOGGER = logging.getLogger(__name__)

FEEDBACK_FIELDS = ("power", "mode", "setpoint", "temperature", "humidity")


@dataclass(slots=True)
class FeedbackStats:
    """Track the arrival of reports for one field since setup."""

    last_seen: float
    reports: int = 1
    longest_gap: float | None = None

    def record(self, received_at: float) -> None:
        """Record a report even if its value did not change."""
        self.longest_gap = max(self.longest_gap or 0, received_at - self.last_seen)
        self.last_seen = received_at
        self.reports += 1


@dataclass
class OrkliAPIData:
    """Class to hold api data."""

    controller_name: str
    devices: list[Device]


class OrkliCoordinator(DataUpdateCoordinator):
    """My orkli wifi thermostat coordinator."""

    data: OrkliAPIData

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize coordinator."""

        # Set variables from values entered in config flow setup
        self.host = config_entry.data[CONF_HOST]
        self.user = config_entry.data[CONF_USERNAME]
        self.pwd = config_entry.data[CONF_PASSWORD]
        # Older entries saved discovery defaults for power and mode.
        self.devices = configured_devices(
            config_entry.data[CONF_DEVICES], self.host, config_entry.entry_id
        )
        for device in self.devices:
            device.on = device.mode = None

        # set variables from options.  You need a default here incase options have not been set
        self.poll_interval = config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

        self._feedback: dict[tuple[int, int], dict[str, FeedbackStats]] = {}
        self._poll_batches_completed = 0
        self._poll_batches_failed = 0

        # Initialise DataUpdateCoordinator
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({config_entry.unique_id})",
            # Set update method to get devices on first load.
            update_method=self.async_update_data,
            # Do not set a polling interval as data will be pushed.
            # You can remove this line but left here for explanatory purposes.
            update_interval=timedelta(seconds=self.poll_interval),
        )

        # Initialise your api here
        self.api = PushAPI(
            host=self.host,
            user=self.user,
            pwd=self.pwd,
            message_callback=self.devices_update_callback,
            destinations={device.address for device in self.devices},
        )

    async def devices_update_callback(self, packet: Packet):
        """Receive callback from api with device update."""
        _LOGGER.debug("Received packet: %s", packet)
        if packet.cmd != 4:
            return

        devices = list(self.data.devices if self.data is not None else self.devices)
        matched = False
        received_at = monotonic()
        for index, device in enumerate(devices):
            if packet.dst != device.address:
                continue

            register = packet.data1 - device.device_id * 4
            value = packet.data2
            if register == 0:
                field = "power"
                devices[index] = replace(device, on=(value & 3) == 3)
            elif register == 1:
                field = "mode"
                mode = value & 15
                if mode in (1, 3, 5):
                    devices[index] = replace(device, mode=1)
                elif mode in (2, 4, 6):
                    devices[index] = replace(device, mode=0)
                else:
                    devices[index] = replace(device, mode=None)
            elif register == 2:
                field = "setpoint"
                devices[index] = replace(device, target_temperature=value)
            elif register == 3 and value != 0:
                field = "temperature"
                devices[index] = replace(device, current_temperature=value)
            elif packet.data1 == device.device_id + 100 and value != 0:
                field = "humidity"
                devices[index] = replace(device, current_humidity=value)
            else:
                continue
            feedback = self._feedback.setdefault((device.address, device.device_id), {})
            if (stats := feedback.get(field)) is None:
                feedback[field] = FeedbackStats(received_at)
            else:
                stats.record(received_at)
            matched = True

        if matched:
            self.async_set_updated_data(OrkliAPIData(self.api.controller_name, devices))

    @property
    def feedback_timeout(self) -> int:
        """Return the effective zone feedback timeout in seconds."""
        return max(MIN_ZONE_FEEDBACK_TIMEOUT, 3 * self.poll_interval)

    def has_recent_feedback(self, device: Device) -> bool:
        """Check whether any field for the zone has reported recently."""
        feedback = self._feedback.get((device.address, device.device_id), {})
        now = monotonic()
        timeout = self.feedback_timeout
        return any(now - stats.last_seen < timeout for stats in feedback.values())

    def feedback_diagnostics(self) -> dict[str, Any]:
        """Summarize per-zone packet timing without including private data."""
        now = monotonic()
        zones = []
        for device in self.devices:
            feedback = self._feedback.get((device.address, device.device_id), {})
            fields = {}
            for field in FEEDBACK_FIELDS:
                stats = feedback.get(field)
                fields[field] = {
                    "reports": stats.reports if stats else 0,
                    "seconds_since_last_report": round(now - stats.last_seen, 1)
                    if stats
                    else None,
                    "longest_gap_seconds": round(stats.longest_gap, 1)
                    if stats and stats.longest_gap is not None
                    else None,
                }
            zones.append(
                {
                    "bus_address": device.address,
                    "output": device.device_id,
                    "fields": fields,
                }
            )
        return {
            "connected": self.api.connected,
            "last_update_success": self.last_update_success,
            "scan_interval_seconds": self.poll_interval,
            "zone_feedback_timeout_seconds": self.feedback_timeout,
            "poll_batches_completed": self._poll_batches_completed,
            "poll_batches_failed": self._poll_batches_failed,
            "zones": zones,
        }

    async def connect_api(self):
        """Connect to api."""
        await self.api.async_connect()

    async def disconnect_api(self):
        """Disconnect form api."""
        await self.api.async_disconnect()

    async def async_send_toggle_command(
        self,
        device: Device,
        on: bool,
    ) -> bool:
        """Send a toggle command to a device."""
        cmd = self.create_packet(
            device.address, 255, 4, device.device_id * 4, 3 if on else 2
        )
        return await self.async_send_command(cmd)

    async def async_send_temp_command(
        self,
        device: Device,
        temp: float,
    ) -> bool:
        """Send a temperature command to a device."""
        cmd = self.create_packet(
            device.address, 255, 4, device.device_id * 4 + 2, int(temp * 2)
        )
        if not await self.async_send_command(cmd):
            return False

        try:
            if not await self.async_send_read_command(device):
                _LOGGER.debug("Could not request temperature feedback for %s", device.name)
        except (APIConnectionError, OSError):
            _LOGGER.debug("Follow-up temperature read failed", exc_info=True)
        return True

    async def async_send_read_command(self, device: Device) -> bool:
        """Send a read command to a device."""
        return await self.async_send_command(self.create_read_packet(device))

    def create_read_packet(self, device: Device) -> Packet:
        """Build the existing zone telemetry request."""
        return self.create_packet(device.address, 255, 4, device.device_id * 4 + 3, 0)

    async def async_send_command(self, command: Packet) -> bool:
        """Send command to device."""
        return await self.api.async_send_command(command)

    async def async_update_data(self) -> OrkliAPIData:
        """Request fresh telemetry and return the latest received state."""
        devices = self.data.devices if self.data is not None else self.devices
        if not devices:
            raise UpdateFailed("No supported thermostat zones are configured")
        try:
            if not self.api.connected:
                await self.connect_api()
            connection = self.api.socket

            for device in devices:
                for packet in (
                    self.create_packet(1, 254, 4, 35, 0),
                    self.create_packet(1, 255, 10, 0, 0),
                    self.create_packet(255, 255, 10, 0, 0),
                    self.create_read_packet(device),
                ):
                    if not self.api.connected or self.api.socket is not connection:
                        raise APIConnectionError("Connection changed during poll")
                    if not await self.async_send_command(packet):
                        raise APIConnectionError(
                            f"Poll write failed for address {device.address}, "
                            f"output {device.device_id}"
                        )
        except (APIConnectionError, OSError) as err:
            self._poll_batches_failed += 1
            raise UpdateFailed(f"Error communicating with API: {err}") from err

        self._poll_batches_completed += 1
        # Feedback may have arrived while the polling commands were being sent.
        if self.data is not None:
            return self.data
        return OrkliAPIData(self.api.controller_name, devices)

    async def async_shutdown(self) -> None:
        """Run shutdown clean up."""
        await super().async_shutdown()
        await self.disconnect_api()

    def get_device_by_id(self, device_id: int, address: int) -> Device | None:
        """Return a zone by its output and controller address."""
        return next(
            (
                device for device in self.data.devices
                if device.device_id == device_id and device.address == address
            ),
            None,
        )

    def create_packet(
        self, dst: int, ori: int, cmd: int, data1: int, data2: int
    ) -> Packet:
        """Create a message."""
        return Packet(
            bytes(
                [
                    0x3B, # starting byte
                    dst,
                    ori,
                    cmd,
                    data1,
                    data2,
                    sum([dst, ori, cmd, data1, data2]) & 0xFF, # checksum byte
                ]
            )
        )
