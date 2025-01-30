"""Example integration using DataUpdateCoordinator."""

from dataclasses import dataclass
from datetime import timedelta
import logging

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

from .api import APIAuthError, Device, Packet, PushAPI
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


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
        self.devices = [Device(**device) for device in config_entry.data[CONF_DEVICES]]

        # set variables from options.  You need a default here incase options have not been set
        self.poll_interval = config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

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
        )

    async def devices_update_callback(self, packet: Packet):
        """Receive callback from api with device update."""
        _LOGGER.debug("Received packet: %s", packet)
        devices = [
            Device(
                device_id=device.device_id,
                device_unique_id=device.device_unique_id,
                name=device.name,
                map=device.map,
                pos_x=device.pos_x,
                pos_y=device.pos_y,
                address=device.address,
                output=device.device_id,
                type=device.type,
                icon=device.icon,
                dato1=device.dato1,
                dato2=device.dato2,
                current_temperature=packet.data2
                if packet.data1 == device.device_id * 4 + 3 and packet.data2 != 0
                else device.current_temperature,
                current_humidity=packet.data2
                if packet.data1 == device.device_id + 100 and packet.data2 != 0
                else device.current_humidity,
                target_temperature=packet.data2
                if packet.data1 == device.device_id * 4 + 2 and packet.data2 != 0
                else device.target_temperature,
                mode=(packet.data2 & 15) % 2
                if packet.data1 == device.device_id * 4 + 1 and packet.data2 != 0
                else device.mode,
                on=packet.data2 == 3
                if packet.data1 == device.device_id * 4 and packet.data2 != 0
                else device.on,
            )
            for device in self.data.devices
        ]
        self.async_set_updated_data(OrkliAPIData(self.api.controller_name, devices))

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
        return await self.async_send_command(cmd) and await self.async_send_command(
            self.create_packet(device.address, 255, 4, device.device_id * 4 + 3, 0)
        )

    async def async_send_read_command(self, device: Device) -> bool:
        """Send a read command to a device."""
        cmd = self.create_packet(device.address, 255, 4, device.device_id * 4 + 3, 0)
        return await self.async_send_command(cmd)

    async def async_send_command(self, command: Packet) -> bool:
        """Send command to device."""
        return await self.api.async_send_command(command)

    async def async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        try:
            if not self.api.connected:
                await self.connect_api()
            # devices = await self.api.async_get_initial_devices()
        except APIAuthError as err:
            _LOGGER.error(err)
            raise UpdateFailed(err) from err
        except Exception as err:
            # This will show entities as unavailable by raising UpdateFailed exception
            raise UpdateFailed(f"Error communicating with API: {err}") from err

        devices = (
            self.data.devices
            if self.data is not None and self.data.devices is not None
            else self.devices
        )

        for device in devices:
            await self.async_send_command(self.create_packet(1, 254, 4, 35, 0))
            await self.async_send_command(self.create_packet(1, 255, 10, 0, 0))
            await self.async_send_command(self.create_packet(255, 255, 10, 0, 0))
            await self.async_send_read_command(device)
            # await asyncio.sleep(0.2)

        # What is returned here is stored in self.data by the DataUpdateCoordinator
        return OrkliAPIData(self.api.controller_name, devices)

    async def async_shutdown(self) -> None:
        """Run shutdown clean up."""
        await super().async_shutdown()
        await self.disconnect_api()

    def get_device_by_id(self, device_id: int) -> Device | None:
        """Return device by device id."""
        # Called by the binary sensors and sensors to get their updated data from self.data
        try:
            return [
                device for device in self.data.devices if device.device_id == device_id
            ][0]
        except IndexError:
            return None

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
