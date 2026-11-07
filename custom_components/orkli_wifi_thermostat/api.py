import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import errno
import logging
import socket
import io
from ftplib import FTP, all_errors, error_perm
from uuid import uuid4

_LOGGER = logging.getLogger(__name__)
CONNECT_TIMEOUT = 10.0
SEND_TIMEOUT = 10.0
RECONNECT_DELAY = 5.0
POLL_INTERVAL = 1.0


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


def _is_valid_message(message: bytes) -> bool:
    """Validate the existing seven-byte local wire format."""
    if len(message) != 7:
        return False
    if message[0] != 0x3B:
        return False
    if message[-1] != (sum(message[1:-1]) & 0xFF):
        return False
    _LOGGER.debug("Valid message: %s", message)
    return True


class PacketStreamParser:
    """Extract fixed-length packets from one TCP connection's byte stream."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def reset(self) -> None:
        """Discard bytes belonging to a closed or previous connection."""
        self._buffer.clear()

    def feed(self, data: bytes) -> list[Packet]:
        """Consume complete frames and retain at most six incomplete bytes."""
        self._buffer.extend(data)
        packets = []
        while self._buffer:
            start = self._buffer.find(b"\x3b")
            if start == -1:
                self._buffer.clear()
                break
            if start:
                del self._buffer[:start]
            if len(self._buffer) < 7:
                break

            message = bytes(self._buffer[:7])
            if not _is_valid_message(message):
                _LOGGER.debug("Invalid message: %s", message)
                # A real header may occur inside this invalid candidate.
                del self._buffer[0]
                continue

            del self._buffer[:7]
            packets.append(Packet(message))
        return packets


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
    current_temperature: int | float | None
    target_temperature: int | float | None
    current_humidity: int | None
    mode: int | None
    on: bool | None
    device_registry_id: str | None = None

    @property
    def is_thermostat(self) -> bool:
        """Type 49 output 9 is ventilation in TERMOWIFI installations."""
        return self.type == 49 and self.device_id != 9

    @property
    def has_valid_zone_address(self) -> bool:
        """Check that the address and all zone registers fit in a frame."""
        return 0 <= self.address < 255 and 0 <= self.device_id <= 63


def configured_devices(records: list[dict], host: str, entry_id: str) -> list[Device]:
    """Load usable saved zones while preserving existing registry identifiers."""
    devices = {}
    entity_ids = set()
    registry_ids = set()
    for record in records:
        device = Device(**record)
        if not device.is_thermostat:
            continue
        if not device.has_valid_zone_address:
            _LOGGER.warning("Skipping saved Orkli zone with an invalid address or output")
            continue
        key = (device.address, device.device_id)
        if key in devices:
            continue

        fallback_id = f"{entry_id}_{device.address}_{device.device_id}"
        if device.device_unique_id in entity_ids:
            device.device_unique_id = fallback_id
        if device.device_registry_id is None:
            device.device_registry_id = f"{host.replace('.', '_')}-{device.device_id}"
        if device.device_registry_id in registry_ids:
            device.device_registry_id = fallback_id

        entity_ids.add(device.device_unique_id)
        registry_ids.add(device.device_registry_id)
        devices[key] = device
    return list(devices.values())


class API:
    """Class for example API."""

    def __init__(self, host: str, user: str, pwd: str) -> None:
        """Initialise."""
        self.host = host
        self.user = user
        self.pwd = pwd
        self.connected: bool = False
        self.socket = None
        self._packet_parser = PacketStreamParser()

    @property
    def controller_name(self) -> str:
        """Return the name of the controller."""
        return self.host.replace(".", "_")

    def connect(self) -> bool:
        """Connect to api."""
        if self.connected:
            return True
        try:
            self._packet_parser.reset()
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(CONNECT_TIMEOUT)
            self.socket.connect((self.host, 12345))
            self.socket.setblocking(0)
            self.connected = True
            return True
        except OSError as ex:
            self.disconnect()
            raise APIConnectionError(f"Error connecting to {self.host}:12345") from ex

    def disconnect(self) -> bool:
        """Disconnect from api."""
        self.connected = False
        self._packet_parser.reset()
        sock, self.socket = self.socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                _LOGGER.debug("Error closing socket", exc_info=True)
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
            mode=None,
            on=None,
        )

    def get_initial_devices(self) -> list[Device]:
        """Download and parse the installation's device list."""
        devices: dict[tuple[int, int], Device] = {}
        file_data = io.BytesIO()
        try:
            with FTP(timeout=CONNECT_TIMEOUT) as ftp:
                ftp.connect("85.152.52.212", 21)
                try:
                    ftp.login(self.user, self.pwd)
                except error_perm as err:
                    if str(err).startswith("530"):
                        raise APIAuthError("Installation FTP login rejected") from err
                    raise
                ftp.retrbinary("RETR Instal.dat", file_data.write)
        except all_errors as err:
            raise APIConnectionError("Could not download Instal.dat") from err

        try:
            lines = file_data.getvalue().decode("utf-8-sig").splitlines()
            if not lines or len(lines) % 8:
                raise ValueError("Expected complete eight-line device records")
            installation_id = uuid4().hex
            for offset in range(0, len(lines), 8):
                map_id, name, x, y, address, output, device_type, icon = lines[
                    offset : offset + 8
                ]
                if int(device_type) != 49 or int(output) == 9:
                    continue
                device = self.create_device(int(map_id))
                device.name = self.get_device_name(name)
                device.pos_x = int(x)
                device.pos_y = int(y)
                device.address = int(address)
                device.output = device.device_id = int(output)
                device.icon = int(icon)
                if not device.has_valid_zone_address:
                    raise ValueError("Thermostat address or output is out of range")
                zone_id = f"{installation_id}_{device.address}_{device.device_id}"
                device.device_unique_id = device.device_registry_id = zone_id
                devices.setdefault((device.address, device.device_id), device)
            if not devices:
                raise ValueError("No supported thermostat zones found")

        except ValueError as err:
            raise APIConnectionError("Invalid Instal.dat device list") from err
        return list(devices.values())

    def isValidMessage(self, message: bytes) -> bool:
        """Check if message is valid."""
        return _is_valid_message(message)

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
        *,
        destinations: set[int] | None = None,
    ) -> None:
        """Initialise."""
        super().__init__(host, user, pwd)
        self.message_callback = message_callback
        self._destinations = frozenset({1} if destinations is None else destinations)
        self._task: asyncio.Task | None = None
        self._connect_task: asyncio.Task | None = None
        self._connect_lock = asyncio.Lock()
        self._send_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._stopping = False

    def _cancel_pending_send(self) -> None:
        """Cancel a write once, leaving its caller to await I/O cleanup."""
        task, self._send_task = self._send_task, None
        if task is not None:
            task.cancel()

    def disconnect(self) -> bool:
        """Abort any write belonging to the connection being closed."""
        self._cancel_pending_send()
        return super().disconnect()

    async def async_connect(self) -> bool:
        """Share a connection attempt and keep exactly one receiver alive."""
        async with self._connect_lock:
            if self._stopping:
                raise APIConnectionError("API has been shut down")
            if not self.connected:
                self._connect_task = asyncio.create_task(self._async_open_connection())
                try:
                    await self._connect_task
                except asyncio.CancelledError:
                    # Cancellation may arrive just after the socket was published.
                    self.disconnect()
                    raise
                finally:
                    self._connect_task = None
            if self._stopping:
                self.disconnect()
                raise APIConnectionError("API has been shut down")
            if self.message_callback and (self._task is None or self._task.done()):
                self._task = asyncio.create_task(self.async_update_devices())
        return True

    async def _async_open_connection(self) -> None:
        """Open a nonblocking socket; publish it only after connection succeeds."""
        # Finish the old write's I/O cleanup before an OS socket fd can be reused.
        # Senders connect before acquiring this lock, avoiding lock inversion.
        async with self._send_lock:
            if self._stopping:
                raise APIConnectionError("API has been shut down")
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setblocking(False)
                loop = asyncio.get_running_loop()
                await asyncio.wait_for(
                    loop.sock_connect(sock, (self.host, 12345)), timeout=CONNECT_TIMEOUT
                )
                if self._stopping:
                    raise APIConnectionError("API has been shut down")
                self._packet_parser.reset()
                self.socket = sock
                self.connected = True
            except (OSError, asyncio.TimeoutError) as ex:
                raise APIConnectionError(f"Error connecting to {self.host}:12345") from ex
            finally:
                if sock is not None and self.socket is not sock:
                    try:
                        sock.close()
                    except OSError:
                        _LOGGER.debug("Error closing unpublished socket", exc_info=True)

    async def async_disconnect(self) -> bool:
        """Stop permanently, cancel pending work, and wait for its cleanup."""
        self._stopping = True
        receiver = self._task
        writer = self._send_task
        current = asyncio.current_task()
        pending = [
            task for task in (self._connect_task, receiver)
            if task is not None and task is not current
        ]
        for task in pending:
            task.cancel()
        self.disconnect()
        if writer is not None and writer is not current:
            pending.append(writer)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        async with self._send_lock:
            pass  # Drain the active sender and reject waiters on the old socket.
        if self._task is receiver:
            self._task = None
        return True

    async def async_get_initial_devices(self) -> list[Device]:
        """Async version of get_initial_devices."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_initial_devices)

    async def async_update_devices(self) -> None:
        """Receive and reconnect in the same task until explicitly stopped."""
        try:
            while not self._stopping:
                if not self.connected:
                    try:
                        await self.async_connect()
                    except APIConnectionError as ex:
                        _LOGGER.warning("Connection failed; retrying: %s", ex)
                        await asyncio.sleep(RECONNECT_DELAY)
                        continue

                sock = self.socket
                try:
                    data = sock.recv(1024)
                except OSError as ex:
                    if ex.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                        await asyncio.sleep(POLL_INTERVAL)
                        continue
                    _LOGGER.warning("Receive failed; reconnecting: %s", ex)
                    self.disconnect()
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue

                if not data:
                    _LOGGER.debug("Connection closed by peer; reconnecting")
                    self.disconnect()
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue

                for packet in self._packet_parser.feed(data):
                    if self._stopping or self.socket is not sock:
                        break
                    _LOGGER.debug("Received valid message: %s", packet)
                    if packet.dst in self._destinations and self.message_callback is not None:
                        try:
                            await self.message_callback(packet)
                        except Exception:
                            _LOGGER.exception("Error handling received packet: %s", packet)
                    else:
                        _LOGGER.debug("Invalid destination: %s", packet.dst)
                # Even a continuous stream with immediate callbacks must yield.
                await asyncio.sleep(0)
        finally:
            self.disconnect()
            if self._task is asyncio.current_task():
                self._task = None

    async def _async_send_bytes(self, sock: socket.socket, message: bytes) -> None:
        """Bound the write; success means socket delivery, not device execution."""
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(loop.sock_sendall(sock, message), timeout=SEND_TIMEOUT)

    async def async_send_command(self, command: Packet) -> bool:
        """Serialize a complete frame on one connection without replaying it."""
        if not self.connected:
            await self.async_connect()
        sock = self.socket
        async with self._send_lock:
            if self._stopping or not self.connected or self.socket is not sock:
                return False
            _LOGGER.debug("Sending command: %s", command)
            writer = asyncio.create_task(self._async_send_bytes(sock, command.message))
            self._send_task = writer
            finished = asyncio.get_running_loop().create_future()
            writer.add_done_callback(finished.set_result)
            try:
                # Internal disconnect cancels the writer, not the action's caller.
                # Shielding this completion signal preserves caller cancellation.
                await asyncio.shield(finished)
                sent = not writer.cancelled()
                if sent:
                    writer.result()
                if not sent or self._stopping or not self.connected or self.socket is not sock:
                    if self.socket is sock:
                        self.disconnect()
                    return False
                return True
            except asyncio.CancelledError:
                if self._send_task is writer:
                    self._cancel_pending_send()
                if self.socket is sock:
                    self.disconnect()
                await asyncio.gather(writer, return_exceptions=True)
                raise
            except Exception as err:
                _LOGGER.error("Error sending command (%s): %s", type(err).__name__, err)
                if self.socket is sock:
                    self.disconnect()
                return False
            finally:
                if self._send_task is writer:
                    self._send_task = None


class APIAuthError(Exception):
    """Exception class for auth error."""


class APIConnectionError(Exception):
    """Exception class for connection error."""
