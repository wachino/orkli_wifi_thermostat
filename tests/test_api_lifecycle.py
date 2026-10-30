"""Connection lifecycle regressions using simulated sockets and connection waits."""

import asyncio
import errno
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_api import TARGET, TEMPERATURE, api, receiver_execution_budget


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = api.PushAPI("offline.invalid", "", "", AsyncMock())
        self.sockets = []

        def new_socket(*args):
            sock = Mock()
            sock.recv.side_effect = BlockingIOError(errno.EAGAIN, "Would block")
            self.sockets.append(sock)
            return sock

        for target, kwargs in (
            ("socket.socket", {"side_effect": new_socket}),
            ("FTP", {"side_effect": AssertionError("No FTP allowed")}),
        ):
            patcher = patch(f"{api.__name__}.{target}", **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.connect_mock = AsyncMock()
        patcher = patch.object(asyncio.get_running_loop(), "sock_connect", self.connect_mock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sendall_mock = AsyncMock()
        patcher = patch.object(asyncio.get_running_loop(), "sock_sendall", self.sendall_mock)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        # Also clean up legacy receivers after a deliberately failing regression.
        if self.client.socket is None and not hasattr(self.client, "_stopping"):
            return
        await self.client.async_disconnect()

    async def until(self, event):
        await asyncio.wait_for(event.wait(), timeout=1)

    def reconnect_after(self, first_result):
        reconnected = asyncio.Event()

        async def connect(sock, address):
            if len(self.sockets) == 1:
                sock.recv.side_effect = (
                    first_result if isinstance(first_result, Exception) else [first_result]
                )
            else:
                reconnected.set()

        self.connect_mock.side_effect = connect
        return reconnected

    async def test_disconnect_before_connect_is_idempotent(self):
        self.assertTrue(await self.client.async_disconnect())
        self.assertTrue(await self.client.async_disconnect())
        self.assertIsNone(self.client.socket)
        self.assertFalse(self.client.connected)

    async def test_concurrent_connects_share_socket_and_receiver(self):
        await asyncio.gather(*(self.client.async_connect() for _ in range(8)))
        self.assertEqual(len(self.sockets), 1)
        self.assertEqual(self.connect_mock.await_count, 1)
        receiver = self.client._task
        await self.client.async_connect()
        self.assertIs(self.client._task, receiver)

    async def test_shutdown_waits_for_reader_and_does_not_reopen(self):
        await self.client.async_connect()
        receiver = self.client._task
        await self.client.async_disconnect()
        self.assertTrue(receiver.done())
        self.assertIsNone(self.client._task)
        self.assertIsNone(self.client.socket)
        self.assertFalse(self.client.connected)
        with self.assertRaises(api.APIConnectionError):
            await self.client.async_connect()
        self.assertEqual(len(self.sockets), 1)
        self.sockets[0].close.assert_called_once()

    async def test_connection_without_callback_starts_no_reader(self):
        self.client.message_callback = None
        self.assertTrue(await self.client.async_connect())
        self.assertTrue(self.client.connected)
        self.assertIsNone(self.client._task)
        self.sockets[0].setblocking.assert_called_once_with(False)
        self.sockets[0].connect.assert_not_called()

    async def test_eof_reconnects_in_the_same_receiver(self):
        reconnected = self.reconnect_after(b"")
        with patch.object(api, "RECONNECT_DELAY", 0):
            await self.client.async_connect()
            receiver = self.client._task
            await self.until(reconnected)
            await self.client.async_connect()
        self.assertIs(self.client._task, receiver)
        self.assertEqual(len(self.sockets), 2)
        self.sockets[0].close.assert_called_once()

    async def test_connection_reset_without_arguments_recovers(self):
        reconnected = self.reconnect_after(ConnectionResetError())
        with patch.object(api, "RECONNECT_DELAY", 0):
            await self.client.async_connect()
            receiver = self.client._task
            await self.until(reconnected)
            await self.client.async_connect()
        self.assertIs(self.client._task, receiver)
        self.assertTrue(self.client.connected)

    async def test_reconnect_failures_retry_with_delay(self):
        ready = asyncio.Event()
        idle = asyncio.Event()
        delays = []
        real_sleep = asyncio.sleep

        async def connect(sock, address):
            count = len(self.sockets)
            if count == 1:
                sock.recv.side_effect = [b""]
            elif count in (2, 3):
                raise ConnectionRefusedError()
            else:
                ready.set()

        async def pause(delay):
            if delay == api.RECONNECT_DELAY:
                delays.append(delay)
                await real_sleep(0)
            elif delay == api.POLL_INTERVAL:
                await idle.wait()
            else:
                await real_sleep(0)

        self.connect_mock.side_effect = connect
        with patch.object(api.asyncio, "sleep", side_effect=pause):
            await self.client.async_connect()
            receiver = self.client._task
            await self.until(ready)
            await self.client.async_connect()
        self.assertEqual(delays, [api.RECONNECT_DELAY] * 3)
        self.assertGreater(api.RECONNECT_DELAY, 0)
        self.assertIs(self.client._task, receiver)
        for sock in self.sockets[:3]:
            sock.close.assert_called_once()

    async def test_shutdown_during_retry_wait_prevents_reconnection(self):
        waiting = asyncio.Event()
        never = asyncio.Event()
        self.reconnect_after(b"")

        async def pause(delay):
            waiting.set()
            await never.wait()

        with patch.object(api.asyncio, "sleep", side_effect=pause):
            await self.client.async_connect()
            receiver = self.client._task
            await self.until(waiting)
            await self.client.async_disconnect()
        self.assertTrue(receiver.done())
        self.assertEqual(len(self.sockets), 1)

    async def test_shutdown_cancels_pending_connection(self):
        entered = asyncio.Event()
        never = asyncio.Event()

        async def connect(sock, address):
            entered.set()
            await never.wait()

        self.connect_mock.side_effect = connect
        caller = asyncio.create_task(self.client.async_connect())
        await self.until(entered)
        await self.client.async_disconnect()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.sockets[0].close.assert_called_once()
        self.assertFalse(self.client.connected)
        self.assertIsNone(self.client._task)

    async def test_caller_cancellation_closes_unpublished_socket(self):
        entered = asyncio.Event()
        never = asyncio.Event()

        async def connect(sock, address):
            entered.set()
            await never.wait()

        self.connect_mock.side_effect = connect
        caller = asyncio.create_task(self.client.async_connect())
        await self.until(entered)
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.sockets[0].close.assert_called_once()
        self.assertIsNone(self.client.socket)
        self.assertFalse(self.client.connected)

    async def test_cancellation_at_publication_closes_socket(self):
        original_open = self.client._async_open_connection

        async def publish_then_cancel():
            await original_open()
            caller.cancel()

        self.client._async_open_connection = publish_then_cancel
        caller = asyncio.create_task(self.client.async_connect())
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertFalse(self.client.connected)
        self.assertIsNone(self.client.socket)
        self.sockets[0].close.assert_called_once()

    async def test_shutdown_rejects_queued_connection_callers(self):
        entered = asyncio.Event()
        never = asyncio.Event()

        async def connect(sock, address):
            entered.set()
            await never.wait()

        self.connect_mock.side_effect = connect
        callers = [asyncio.create_task(self.client.async_connect()) for _ in range(2)]
        await self.until(entered)
        await self.client.async_disconnect()
        results = await asyncio.gather(*callers, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertIsInstance(results[1], api.APIConnectionError)
        self.assertEqual(len(self.sockets), 1)

    async def test_connection_timeout_closes_socket(self):
        never = asyncio.Event()

        async def connect(sock, address):
            await never.wait()

        self.connect_mock.side_effect = connect
        with patch.object(api, "CONNECT_TIMEOUT", 0.001):
            with self.assertRaises(api.APIConnectionError) as caught:
                await self.client.async_connect()
        self.assertIsInstance(caught.exception.__cause__, asyncio.TimeoutError)
        self.sockets[0].close.assert_called_once()
        self.assertIsNone(self.client._task)

    async def test_transport_failure_is_not_reported_as_authentication(self):
        self.connect_mock.side_effect = ConnectionRefusedError()
        with self.assertRaises(api.APIConnectionError):
            await self.client.async_connect()
        self.sockets[0].close.assert_called_once()

    async def test_callback_errors_do_not_reconnect_or_drop_next_packet(self):
        with patch.object(api, "POLL_INTERVAL", 0):
            for error in (ValueError(), OSError()):
                await self.check_callback_error(error)

    async def check_callback_error(self, error):
        with self.subTest(error=type(error).__name__):
            received = asyncio.Event()
            messages = []

            async def callback(packet):
                messages.append(packet.message)
                if len(messages) == 1:
                    raise error
                received.set()

            self.client.message_callback = callback
            if not self.client.connected:
                await self.client.async_connect()
            chunks = [TEMPERATURE + TARGET]

            def receive(size):
                if chunks:
                    return chunks.pop(0)
                raise BlockingIOError(errno.EAGAIN, "Would block")

            self.client.socket.recv.side_effect = receive
            with self.assertLogs(api._LOGGER, level="ERROR"):
                await self.until(received)
            self.assertEqual(messages, [TEMPERATURE, TARGET])
            self.assertEqual(len(self.sockets), 1)
            self.assertTrue(self.client.connected)

    async def test_shutdown_cancels_callback_and_closes_socket(self):
        entered = asyncio.Event()
        never = asyncio.Event()

        async def callback(packet):
            entered.set()
            await never.wait()

        async def connect(sock, address):
            sock.recv.side_effect = [TEMPERATURE]

        self.client.message_callback = callback
        self.connect_mock.side_effect = connect
        await self.client.async_connect()
        receiver = self.client._task
        await self.until(entered)
        await self.client.async_disconnect()
        self.assertTrue(receiver.cancelled())
        self.assertFalse(self.client.connected)

    async def test_failed_send_reconnects_without_replacing_receiver(self):
        reconnected = self.reconnect_after(BlockingIOError(errno.EAGAIN, "Would block"))
        with patch.object(api, "POLL_INTERVAL", 0):
            await self.client.async_connect()
            receiver = self.client._task
            self.sendall_mock.side_effect = ConnectionResetError()
            self.assertFalse(await self.client.async_send_command(api.Packet(TEMPERATURE)))
            await self.until(reconnected)
            await self.client.async_connect()
        self.sendall_mock.assert_awaited_once_with(self.sockets[0], TEMPERATURE)
        self.sockets[0].send.assert_not_called()
        self.assertIs(self.client._task, receiver)
        self.assertTrue(self.client.connected)

    async def test_shutdown_cleans_up_a_write_awaited_by_receiver_callback(self):
        entered, cleaned, never = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def send(sock, message):
            entered.set()
            try:
                await never.wait()
            finally:
                cleaned.set()

        async def connect(sock, address):
            sock.recv.side_effect = [TEMPERATURE]

        async def callback(packet):
            await self.client.async_send_command(api.Packet(TARGET))

        self.client.message_callback = callback
        self.connect_mock.side_effect = connect
        self.sendall_mock.side_effect = send
        await self.client.async_connect()
        receiver = self.client._task
        await self.until(entered)
        await asyncio.wait_for(self.client.async_disconnect(), timeout=1)
        self.assertTrue(cleaned.is_set())
        self.assertTrue(receiver.cancelled())
        self.sockets[0].close.assert_called_once()

    async def test_reconnection_discards_fragment_from_previous_socket(self):
        received = asyncio.Event()
        messages = []

        async def callback(packet):
            messages.append(packet.message)
            received.set()

        async def connect(sock, address):
            chunks = ([TEMPERATURE[:4], b""] if len(self.sockets) == 1
                      else [TEMPERATURE[4:] + TARGET])

            def receive(size):
                if chunks:
                    return chunks.pop(0)
                raise BlockingIOError(errno.EAGAIN, "Would block")

            sock.recv.side_effect = receive

        self.client.message_callback = callback
        self.connect_mock.side_effect = connect
        with patch.object(api, "RECONNECT_DELAY", 0):
            await self.client.async_connect()
            await self.until(received)
        self.assertEqual(messages, [TARGET])

    async def test_continuous_data_yields_so_shutdown_can_run(self):
        received = asyncio.Event()
        original_callback = self.client.message_callback

        async def callback(packet):
            await original_callback(packet)
            received.set()

        self.client.message_callback = callback
        await self.client.async_connect()
        self.sockets[0].recv.side_effect = None
        self.sockets[0].recv.return_value = TARGET
        with receiver_execution_budget():
            await self.until(received)
            await asyncio.wait_for(self.client.async_disconnect(), timeout=1)
        self.assertGreater(original_callback.await_count, 0)
        self.assertFalse(self.client.connected)

    async def test_socket_close_failure_preserves_connection_error(self):
        async def connect(sock, address):
            sock.close.side_effect = OSError("Already closed")
            raise ConnectionRefusedError()

        self.connect_mock.side_effect = connect
        with self.assertRaises(api.APIConnectionError) as caught:
            await self.client.async_connect()
        self.assertIsInstance(caught.exception.__cause__, ConnectionRefusedError)


if __name__ == "__main__":
    unittest.main()
