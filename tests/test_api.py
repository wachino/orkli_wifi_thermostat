"""Offline regression tests; neither Home Assistant nor hardware is required."""

from contextlib import contextmanager
import asyncio
import errno
from ftplib import error_perm
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch


# Import api.py directly, without running the integration's Home Assistant setup.
API_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components/orkli_wifi_thermostat/api.py"
)
SPEC = importlib.util.spec_from_file_location("orkli_api_under_test", API_PATH)
api = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = api
SPEC.loader.exec_module(api)

# Literal frames, independent of the production checksum implementation.
TEMPERATURE = bytes.fromhex("3b 01 ff 04 07 2a 35")
TARGET = bytes.fromhex("3b 01 ff 04 06 28 32")
OTHER_DESTINATION = bytes.fromhex("3b 02 ff 04 07 2a 36")
PAYLOAD_HEADERS = bytes.fromhex("3b 01 ff 04 3b 3b 7a")
CHECKSUM_HEADER = bytes.fromhex("3b 01 00 04 06 30 3b")
BAD_CHECKSUM = TEMPERATURE[:-1] + b"\x00"


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.client = api.API("offline.invalid", "user", "password")
        patcher = patch.object(api, "FTP")
        self.ftp_factory = patcher.start()
        self.addCleanup(patcher.stop)
        self.ftp = self.ftp_factory.return_value.__enter__.return_value

    def set_file(self, contents):
        self.ftp.retrbinary.side_effect = lambda command, write: write(contents)

    def test_reads_complete_utf8_installation_records(self):
        self.set_file("0\nSalón\n0\n0\n1\n2\n49\n11\n".encode())
        device, = self.client.get_initial_devices()
        self.assertEqual((device.address, device.device_id, device.type), (1, 2, 49))
        self.ftp_factory.assert_called_once_with(timeout=api.CONNECT_TIMEOUT)
        self.ftp_factory.return_value.__exit__.assert_called_once()

    def test_rejects_empty_and_partial_installation_lists(self):
        for contents in (b"", b"0\nLiving room\n"):
            with self.subTest(contents=contents):
                self.set_file(contents)
                with self.assertRaises(api.APIConnectionError):
                    self.client.get_initial_devices()

    def test_discovery_filters_ventilation_and_distinguishes_addresses(self):
        self.set_file(
            b"0\nLiving\n0\n0\n1\n2\n49\n11\n"
            b"0\nStudy\n0\n0\n2\n2\n49\n11\n"
            b"1\nLiving duplicate\n0\n0\n1\n2\n49\n11\n"
            b"0\nVent.\n0\n0\n1\n9\n49\n3\n"
            b"0\nSwitch\n0\n0\n1\n0\n0\n0\n"
        )
        living, study = self.client.get_initial_devices()
        self.assertEqual([(d.address, d.device_id) for d in (living, study)], [(1, 2), (2, 2)])
        self.assertNotEqual(living.device_unique_id, study.device_unique_id)
        self.assertNotEqual(living.device_registry_id, study.device_registry_id)

    def test_discovery_rejects_addresses_and_outputs_outside_the_protocol(self):
        for address, output in ((255, 2), (256, 2), (1, -1), (1, 64)):
            with self.subTest(address=address, output=output):
                self.set_file(f"0\nLiving\n0\n0\n{address}\n{output}\n49\n11\n".encode())
                with self.assertRaises(api.APIConnectionError):
                    self.client.get_initial_devices()

    def test_saved_devices_recover_legacy_id_collisions(self):
        first = self.client.create_device(0)
        first.address = 1
        second = {**vars(first), "address": 2}
        devices = api.configured_devices([vars(first), second], self.client.host, "entry")
        self.assertEqual(devices[0].device_unique_id, first.device_unique_id)
        self.assertEqual(devices[0].device_registry_id, "offline_invalid-0")
        self.assertEqual(devices[1].device_unique_id, "entry_2_0")
        self.assertEqual(devices[1].device_registry_id, "entry_2_0")

    def test_rejected_login_is_an_authentication_error(self):
        self.ftp.login.side_effect = error_perm("530 Login incorrect")
        with self.assertRaises(api.APIAuthError):
            self.client.get_initial_devices()
        self.ftp.retrbinary.assert_not_called()


class LoopBudgetExceeded(BaseException):
    """Escape even the legacy receiver's broad Exception handler."""


@contextmanager
def receiver_execution_budget(limit=20_000):
    """Fail a non-yielding receiver deterministically instead of hanging CI.

    An asyncio timeout alone cannot interrupt the original synchronous loop.
    Only lines in api.py count; the test runner is not subject to the budget.
    """
    previous_trace = sys.gettrace()
    remaining = limit

    def trace(frame, event, arg):
        nonlocal remaining
        if event == "line" and frame.f_code.co_filename == str(API_PATH):
            remaining -= 1
            if remaining <= 0:
                raise LoopBudgetExceeded("Receiver did not make progress")
        return trace

    sys.settrace(trace)
    try:
        yield
    finally:
        sys.settrace(previous_trace)


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        # Fail closed if a test accidentally attempts a real TCP/FTP connection.
        for target in ("socket.socket", "FTP"):
            patcher = patch(f"{api.__name__}.{target}")
            mocked = patcher.start()
            mocked.side_effect = AssertionError("Network access is forbidden in tests")
            self.addCleanup(patcher.stop)


class ValidationTests(OfflineTestCase):
    def test_existing_wire_format(self):
        client = api.API("offline.invalid", "", "")
        for frame in (TEMPERATURE, TARGET, PAYLOAD_HEADERS, CHECKSUM_HEADER):
            with self.subTest(frame=frame):
                self.assertTrue(client.isValidMessage(frame))
        for frame in (b"", TEMPERATURE[:6], TEMPERATURE + b"\x00", BAD_CHECKSUM,
                      b"\x00" + TEMPERATURE[1:]):
            with self.subTest(frame=frame):
                self.assertFalse(client.isValidMessage(frame))


class ParserTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.parser = api.PacketStreamParser()

    def feed(self, chunk):
        return [packet.message for packet in self.parser.feed(chunk)]

    def test_every_partition_of_one_frame(self):
        # All 64 ways to split the six gaps in a seven-byte frame.
        for mask in range(64):
            with self.subTest(mask=mask):
                self.parser.reset()
                messages = []
                start = 0
                for boundary in range(1, 7):
                    if mask & (1 << (boundary - 1)):
                        messages.extend(self.feed(TEMPERATURE[start:boundary]))
                        start = boundary
                messages.extend(self.feed(TEMPERATURE[start:]))
                self.assertEqual(messages, [TEMPERATURE])

    def test_every_split_in_concatenated_frames(self):
        stream = TEMPERATURE + TARGET + PAYLOAD_HEADERS + CHECKSUM_HEADER
        for split in range(len(stream) + 1):
            with self.subTest(split=split):
                self.parser.reset()
                self.assertEqual(
                    self.feed(stream[:split]) + self.feed(stream[split:]),
                    [TEMPERATURE, TARGET, PAYLOAD_HEADERS, CHECKSUM_HEADER],
                )

    def test_noise_and_invalid_checksum_do_not_hide_following_frames(self):
        self.assertEqual(
            self.feed(b"noise" + BAD_CHECKSUM + TEMPERATURE + b"junk" + TARGET),
            [TEMPERATURE, TARGET],
        )

    def test_resynchronizes_one_byte_at_a_time(self):
        # The real header is inside the first invalid seven-byte candidate.
        self.assertEqual(self.feed(b"\x3b\x00" + TEMPERATURE), [TEMPERATURE])

    def test_complete_frame_followed_by_partial_frame(self):
        self.assertEqual(self.feed(TEMPERATURE + TARGET[:4]), [TEMPERATURE])
        self.assertEqual(self.feed(b""), [])
        self.assertEqual(self.feed(TARGET[4:]), [TARGET])

    def test_header_bytes_in_payload_and_checksum_are_not_delimiters(self):
        self.assertEqual(self.feed(PAYLOAD_HEADERS + CHECKSUM_HEADER),
                         [PAYLOAD_HEADERS, CHECKSUM_HEADER])

    def test_buffer_remains_bounded_after_adversarial_input(self):
        for chunk in (b"\x3b" * 10_000, b"x" * 10_000, BAD_CHECKSUM * 1_000):
            with self.subTest(first_byte=chunk[:1]):
                self.parser.reset()
                with receiver_execution_budget(limit=1_000_000):
                    self.assertEqual(self.feed(chunk), [])
                self.assertLessEqual(len(self.parser._buffer), 6)
                self.assertEqual(self.feed(TEMPERATURE), [TEMPERATURE])

    def test_reset_drops_incomplete_frame(self):
        self.assertEqual(self.feed(TEMPERATURE[:4]), [])
        self.parser.reset()
        self.assertEqual(self.feed(TEMPERATURE[4:]), [])
        self.assertEqual(self.feed(TARGET), [TARGET])


class ReceiverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.network_guard = patch(f"{api.__name__}.socket.socket",
                                   side_effect=AssertionError("No network allowed"))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)

    async def receive(self, chunks):
        callback = AsyncMock()
        client = api.PushAPI("offline.invalid", "", "", callback)
        client.connected = True
        transport = client.socket = Mock()
        transport.recv.side_effect = [*chunks, b""]
        client.async_connect = AsyncMock(return_value=True)

        async def pause(delay):
            if delay == api.RECONNECT_DELAY:
                raise asyncio.CancelledError

        with patch.object(api.asyncio, "sleep", side_effect=pause):
            with receiver_execution_budget():
                with self.assertRaises(asyncio.CancelledError):
                    await client.async_update_devices()
        transport.send.assert_not_called()
        transport.connect.assert_not_called()
        return [call.args[0].message for call in callback.await_args_list]

    async def test_complete_concatenated_frames(self):
        self.assertEqual(await self.receive([TEMPERATURE + TARGET]),
                         [TEMPERATURE, TARGET])

    async def test_every_fragment_boundary(self):
        for split in range(1, 7):
            with self.subTest(split=split):
                self.assertEqual(
                    await self.receive([TEMPERATURE[:split], TEMPERATURE[split:]]),
                    [TEMPERATURE],
                )

    async def test_byte_at_a_time(self):
        self.assertEqual(await self.receive([bytes([b]) for b in TEMPERATURE]),
                         [TEMPERATURE])

    async def test_invalid_frame_then_valid_frame(self):
        self.assertEqual(await self.receive([BAD_CHECKSUM + TARGET]), [TARGET])

    async def test_valid_header_inside_invalid_candidate(self):
        self.assertEqual(await self.receive([b"\x3b\x00" + TEMPERATURE]),
                         [TEMPERATURE])

    async def test_noise_and_other_destination_are_consumed(self):
        self.assertEqual(
            await self.receive([b"noise", OTHER_DESTINATION + TARGET]), [TARGET]
        )

    async def test_partial_tail_survives_next_receive(self):
        self.assertEqual(await self.receive([TEMPERATURE + TARGET[:3], TARGET[3:]]),
                         [TEMPERATURE, TARGET])

    async def test_partial_frame_survives_would_block(self):
        self.assertEqual(
            await self.receive([TEMPERATURE[:4],
                                BlockingIOError(errno.EAGAIN, "Would block"),
                                TEMPERATURE[4:]]),
            [TEMPERATURE],
        )


class ConnectionBoundaryTests(OfflineTestCase):
    def test_disconnect_discards_pending_bytes(self):
        client = api.PushAPI("offline.invalid", "", "")
        client.socket = Mock()
        client._packet_parser.feed(TEMPERATURE[:4])
        client.disconnect()
        self.assertEqual(client._packet_parser.feed(TEMPERATURE[4:]), [])
        self.assertFalse(client.connected)

    def test_new_connection_discards_pending_bytes(self):
        client = api.PushAPI("offline.invalid", "", "")
        client._packet_parser.feed(TEMPERATURE[:4])
        with patch.object(api.socket, "socket", return_value=Mock()):
            client.connect()
        self.assertEqual(client._packet_parser.feed(TEMPERATURE[4:]), [])
        self.assertEqual([p.message for p in client._packet_parser.feed(TARGET)],
                         [TARGET])


if __name__ == "__main__":
    unittest.main()
