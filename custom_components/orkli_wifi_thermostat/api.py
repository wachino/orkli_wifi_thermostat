import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import errno
import logging
import socket
import io
from ftplib import FTP

_LOGGER = logging.getLogger(__name__)


@dataclass
class Packet:
    """API packet."""

    message: bytes

    def __post_init__(self):
        """Initialise."""
        self.start = self.message[0]
        self.dst = self.message[1]
        self.ori = self.message[2]
        self.cmd = self.message[3]
        self.data1 = self.message[4]
        self.data2 = self.message[5]
        self.end = self.message[6]

    def __repr__(self):
        """Return string representation."""
        return f"Packet: dst: {self.dst} ori: {self.ori} cmd:{self.cmd} data1:{self.data1} data2:{self.data2}"


@dataclass
class Device:
    """API device."""

    device_id: int
    device_unique_id: str
    name: str
    map: int
    pos_x: int
    pos_y: int
    address: int
    output: int
    type: int
    icon: int
    dato1: int
    dato2: int
    current_temperature: int | None
    target_temperature: int | None
    current_humidity: int | None
    mode: int
    on: bool


class API:
    """Class for example API."""

    def __init__(self, host: str, user: str, pwd: str) -> None:
        """Initialise."""
        self.host = host
        self.user = user
        self.pwd = pwd
        self.connected: bool = False
        self.socket = None

    @property
    def controller_name(self) -> str:
        """Return the name of the controller."""
        return self.host.replace(".", "_")

    def connect(self) -> bool:
        """Connect to api."""
        # if self.user == "test" and self.pwd == "1234":
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, 12345))
            self.socket.setblocking(0)
            self.connected = True
            return True
        except Exception as ex:
            self.disconnect()
            raise APIAuthError("Error connecting to api. Invalid username or password.")

    def disconnect(self) -> bool:
        """Disconnect from api."""
        self.connected = False
        self.socket.close()
        return True

    def create_device(self, map: int) -> Device:
        return Device(
            device_id=0,
            device_unique_id=self.get_device_unique_id(0),
            name="",
            map=map,
            pos_x=0,
            pos_y=0,
            address=0,
            output=0,
            type=49,
            icon=0,
            dato1=2,
            dato2=0,
            current_temperature=None,
            current_humidity=None,
            target_temperature=None,
            mode=0,
            on=False,
        )

    def get_initial_devices(self) -> list[Device]:
        ("""Get devices on api.""",)
        newDevice: Device
        devices: list[Device] = []
        ftpServer = "85.152.52.212"
        ftpPort = 21
        # ftpServer = "192.168.1.130"
        # ftpPort = 1021
        try:
            ftp = FTP()
            ftp.connect(ftpServer, ftpPort)
            ftp.login(self.user, self.pwd)
            file_data = io.BytesIO()
            ftp.retrbinary("RETR Instal.dat", file_data.write)
            file_data.seek(0)
            for idx, value in enumerate(file_data.read().decode("utf-8").splitlines()):
                match idx % 8:
                    case 0:
                        newDevice = self.create_device(int(value))
                        continue
                    case 1:
                        newDevice.name = self.get_device_name(value)
                        continue
                    case 2:
                        newDevice.pos_x = int(value)
                        continue
                    case 3:
                        newDevice.pos_y = int(value)
                        continue
                    case 4:
                        newDevice.address = int(value)
                        continue
                    case 5:
                        newDevice.output = int(value)
                        newDevice.device_id = newDevice.output
                        newDevice.device_unique_id = self.get_device_unique_id(
                            newDevice.output
                        )
                        continue
                    case 6:
                        newDevice.type = int(value)
                        continue
                    case 7:
                        newDevice.icon = int(value)
                        devices.append(newDevice)
                        continue

            ftp.quit
        except Exception as ex:
            _LOGGER.error("Error reading file: %s", ex)
        return devices

    def isValidMessage(self, message: bytes) -> bool:
        """Check if message is valid."""
        if len(message) != 7:
            return False
        if message[0] != 0x3B:
            return False
        if message[-1] != (sum(message[1:-1]) & 0xFF):
            return False
        _LOGGER.debug("Valid message: %s", message)
        return True

    def get_updated_devices(self, packet: Packet) -> Packet:
        """Get devices on api."""
        return packet

    def get_device_unique_id(self, device_id: str) -> str:
        """Return a unique device id."""
        return f"{self.controller_name}_{device_id}"

    def get_device_name(self, label: str) -> str:
        """Return the device name."""
        return f"Climate {label}"


class PushAPI(API):
    """Mimic for a push api."""

    def __init__(
        self,
        host: str,
        user: str,
        pwd: str,
        message_callback: Callable | None = None,
    ) -> None:
        """Initialise."""
        super().__init__(host, user, pwd)
        self.message_callback = message_callback
        self._task: asyncio.Task = None

    async def async_connect(self) -> bool:
        """Connect tothe api.

        In this case we will create a task to add the device update function call
        to the event loop and return.
        """
        if super().connect():
            if self.message_callback:
                loop = asyncio.get_running_loop()
                self._task = loop.create_task(self.async_update_devices())
        return True

    async def async_disconnect(self) -> bool:
        """Disconnect from api."""
        if self._task:
            self._task.cancel()
        super().disconnect()
        return True

    async def async_get_initial_devices(self) -> list[Device]:
        """Async version of get_initial_devices."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_initial_devices)

    async def async_update_devices(self) -> None:
        """Loop to send updated device data every 15s."""
        while self.connected:
            try:
                data = self.socket.recv(1024)
                arr_data = bytearray(data)
                while len(arr_data) > 0:
                    try:
                        idx = arr_data.index(b"\x3b")
                        message = arr_data[idx : idx + 7]
                        if not self.isValidMessage(message):
                            _LOGGER.debug("Invalid message: %s", message)
                            continue
                        packet = Packet(message)
                        _LOGGER.debug("Received valid message: %s", packet)
                        if packet.dst == 1:
                            await self.message_callback(packet)

                        else:  # si destino != direccion no actualizar valores, investigar qué es
                            _LOGGER.debug("Invalid destination: %s", packet.dst)

                        arr_data = arr_data[idx + 7 :]
                    except ValueError:
                        if len(arr_data) > 1:
                            _LOGGER.error("Byte not found: %s", arr_data)
                        break

                if not data:
                    self.disconnect()
                    break  # Connection closed

            except Exception as e:
                err = e.args[0]
                if err == errno.EAGAIN or err == errno.EWOULDBLOCK:
                    await asyncio.sleep(1)
                    # _LOGGER.debug("No data available")
                    continue
                _LOGGER.error("Connection reset by peer. Reconnecting... ")
                self.disconnect()
                await self.async_connect()
        await self.async_connect()

    async def async_send_command(self, command: Packet) -> bool:
        """Send a command to a device."""

        if not self.connected:
            await self.async_connect()
        try:
            _LOGGER.debug("Sending command: %s", command)
            s = self.socket.send(command.message)
            if s != 7:
                _LOGGER.error("Length error sending command: %s", command)
                self.disconnect()
                return False
            return True
        except Exception as e:
            _LOGGER.error("Error sending command: %s", e)
            self.disconnect()
            return False


class APIAuthError(Exception):
    """Exception class for auth error."""


class APIConnectionError(Exception):
    """Exception class for connection error."""
