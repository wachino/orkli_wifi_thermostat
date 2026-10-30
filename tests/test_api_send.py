"""Write regressions with fake I/O and an in-process Unix socket pair."""

import asyncio
import socket
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_api import TARGET, TEMPERATURE, api


class SendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = api.PushAPI("offline.invalid", "", "")
        self.transport = self.client.socket = Mock()
        self.transport.send.return_value = 7
        self.client.connected = True
        self.tasks = []
        self.sendall = AsyncMock()
        patcher = patch.object(asyncio.get_running_loop(), "sock_sendall", self.sendall)
        patcher.start()
        self.addCleanup(patcher.stop)
        for target in ("socket.socket", "FTP"):
            patcher = patch(f"{api.__name__}.{target}",
                            side_effect=AssertionError("No TCP/FTP allowed"))
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.client.async_disconnect()

    def start(self, message=TEMPERATURE):
        task = asyncio.create_task(self.client.async_send_command(api.Packet(message)))
        self.tasks.append(task)
        return task

    async def until(self, event):
        await asyncio.wait_for(event.wait(), timeout=1)

    def hold_write(self):
        entered, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def send(sock, message):
            entered.set()
            try:
                await release.wait()
            finally:
                cleaned.set()

        self.sendall.side_effect = send
        return entered, release, cleaned

    async def test_success_sends_exact_bytes_with_sock_sendall(self):
        self.assertTrue(await self.client.async_send_command(api.Packet(TEMPERATURE)))
        self.sendall.assert_awaited_once_with(self.transport, TEMPERATURE)
        self.transport.send.assert_not_called()
        self.transport.close.assert_not_called()

    async def test_concurrent_commands_are_serialized(self):
        entered, release, _ = self.hold_write()
        first = self.start()
        await self.until(entered)
        second = self.start(TARGET)
        await asyncio.sleep(0)
        self.assertEqual(self.sendall.await_count, 1)
        release.set()
        self.assertEqual(await asyncio.gather(first, second), [True, True])
        self.assertEqual([call.args[1] for call in self.sendall.await_args_list],
                         [TEMPERATURE, TARGET])

    async def test_cancelled_waiter_does_not_close_active_socket(self):
        entered, release, _ = self.hold_write()
        first = self.start()
        await self.until(entered)
        waiter = self.start(TARGET)
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.transport.close.assert_not_called()
        self.assertTrue(self.client.connected)
        release.set()
        self.assertTrue(await first)
        self.assertEqual(self.sendall.await_count, 1)

    async def test_cancelling_active_send_closes_uncertain_stream(self):
        entered, _, cleaned = self.hold_write()
        sender = self.start()
        await self.until(entered)
        sender.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await sender
        self.assertTrue(cleaned.is_set())
        self.transport.close.assert_called_once()
        self.assertFalse(self.client.connected)
        self.assertEqual(self.sendall.await_count, 1)

    async def test_disconnect_finishes_active_send_as_failure(self):
        entered, _, cleaned = self.hold_write()
        sender = self.start()
        await self.until(entered)
        self.client.disconnect()
        self.assertFalse(await asyncio.wait_for(sender, timeout=1))
        self.assertTrue(cleaned.is_set())
        self.transport.close.assert_called_once()

    async def test_shutdown_awaits_pending_write_cleanup(self):
        entered, _, cleaned = self.hold_write()
        sender = self.start()
        await self.until(entered)
        waiter = self.start(TARGET)
        await asyncio.sleep(0)
        await asyncio.wait_for(self.client.async_disconnect(), timeout=1)
        self.assertTrue(cleaned.is_set())
        self.assertFalse(await sender)
        self.assertFalse(await waiter)
        self.assertIsNone(self.client.socket)

    async def test_disconnect_before_writer_starts_is_not_caller_cancellation(self):
        sender = self.start()
        asyncio.get_running_loop().call_soon(self.client.disconnect)
        self.assertFalse(await sender)
        self.sendall.assert_not_awaited()
        self.transport.close.assert_called_once()

    async def test_caller_cancelled_before_writer_starts_is_propagated(self):
        sender = self.start()
        asyncio.get_running_loop().call_soon(sender.cancel)
        with self.assertRaises(asyncio.CancelledError):
            await sender
        self.assertFalse(self.client.connected)
        self.transport.close.assert_called_once()

    async def test_queued_command_is_not_replayed_after_disconnect(self):
        entered, _, _ = self.hold_write()
        first = self.start()
        await self.until(entered)
        second = self.start(TARGET)
        await asyncio.sleep(0)
        self.client.disconnect()
        self.assertEqual(await asyncio.gather(first, second), [False, False])
        self.assertEqual(self.sendall.await_count, 1)

    async def test_write_timeout_cancels_io_and_closes_without_retry(self):
        _, _, cleaned = self.hold_write()
        with patch.object(api, "SEND_TIMEOUT", 0.001):
            self.assertFalse(await self.client.async_send_command(api.Packet(TEMPERATURE)))
        self.assertTrue(cleaned.is_set())
        self.assertFalse(self.client.connected)
        self.transport.close.assert_called_once()
        self.assertEqual(self.sendall.await_count, 1)

    async def test_write_errors_return_failure_without_replaying(self):
        for error in (ConnectionResetError(), BrokenPipeError(), OSError()):
            with self.subTest(error=type(error).__name__):
                transport = self.client.socket = Mock()
                self.client.connected = True
                self.sendall.reset_mock()
                self.sendall.side_effect = error
                self.assertFalse(await self.client.async_send_command(api.Packet(TEMPERATURE)))
                self.sendall.assert_awaited_once_with(transport, TEMPERATURE)
                transport.close.assert_called_once()
                self.assertFalse(self.client.connected)

    async def test_connect_before_sending_uses_established_socket(self):
        self.client.connected = False
        self.client.socket = None

        async def connect():
            self.client.socket = self.transport
            self.client.connected = True
            return True

        with patch.object(self.client, "async_connect", side_effect=connect) as connect_mock:
            self.assertTrue(await self.client.async_send_command(api.Packet(TARGET)))
        connect_mock.assert_awaited_once()
        self.sendall.assert_awaited_once_with(self.transport, TARGET)

    async def test_connect_failure_keeps_existing_exception_contract(self):
        self.client.connected = False
        with patch.object(self.client, "async_connect", side_effect=api.APIConnectionError):
            with self.assertRaises(api.APIConnectionError):
                await self.client.async_send_command(api.Packet(TEMPERATURE))
        self.sendall.assert_not_awaited()

    async def test_old_send_does_not_close_replacement_socket(self):
        entered, _, _ = self.hold_write()
        sender = self.start()
        await self.until(entered)
        self.client.disconnect()
        replacement = self.client.socket = Mock()
        self.client.connected = True
        self.assertFalse(await sender)
        replacement.close.assert_not_called()
        self.assertTrue(self.client.connected)

    async def test_reconnection_waits_for_write_io_cleanup(self):
        entered, cleanup_entered, release_cleanup = (
            asyncio.Event(), asyncio.Event(), asyncio.Event()
        )
        never = asyncio.Event()

        async def send(sock, message):
            entered.set()
            try:
                await never.wait()
            finally:
                cleanup_entered.set()
                await release_cleanup.wait()

        self.sendall.side_effect = send
        sender = self.start()
        await self.until(entered)
        self.client.disconnect()
        await self.until(cleanup_entered)
        replacement = Mock()
        with patch.object(api.socket, "socket", return_value=replacement) as factory:
            with patch.object(asyncio.get_running_loop(), "sock_connect", new_callable=AsyncMock):
                reconnect = asyncio.create_task(self.client.async_connect())
                self.tasks.append(reconnect)
                # Let the opener reach the write lock while cleanup is held.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                factory.assert_not_called()
                release_cleanup.set()
                self.assertFalse(await sender)
                self.assertTrue(await asyncio.wait_for(reconnect, timeout=1))
        self.assertIs(self.client.socket, replacement)


class FragmentedSocket:
    """Force the real asyncio implementation to handle short sends and EAGAIN."""

    def __init__(self, sock):
        self.sock = sock
        self.calls = 0

    def send(self, data):
        self.calls += 1
        if self.calls == 1:
            raise BlockingIOError
        return self.sock.send(data[:2])

    def __getattr__(self, name):
        return getattr(self.sock, name)


class LocalSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # AF_UNIX sockets stay inside this process's host; no TCP/device traffic.
        left, self.peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        left.setblocking(False)
        self.peer.setblocking(False)
        self.addCleanup(left.close)
        self.addCleanup(self.peer.close)
        self.client = api.PushAPI("offline.invalid", "", "")
        self.client.socket = FragmentedSocket(left)
        self.client.connected = True
        for target in ("socket.socket", "FTP"):
            patcher = patch(f"{api.__name__}.{target}",
                            side_effect=AssertionError("No TCP/FTP allowed"))
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.client.async_disconnect()

    async def read(self, size):
        async def collect():
            result = b""
            while len(result) < size:
                chunk = await asyncio.get_running_loop().sock_recv(self.peer, size - len(result))
                if not chunk:
                    break
                result += chunk
            return result

        return await asyncio.wait_for(collect(), timeout=1)

    async def test_real_sendall_completes_partial_writes_after_would_block(self):
        self.assertTrue(await self.client.async_send_command(api.Packet(TEMPERATURE)))
        self.assertEqual(await self.read(7), TEMPERATURE)
        self.assertGreater(self.client.socket.calls, 1)

    async def test_real_concurrent_sends_preserve_whole_frame_order(self):
        results = await asyncio.gather(
            self.client.async_send_command(api.Packet(TEMPERATURE)),
            self.client.async_send_command(api.Packet(TARGET)),
        )
        self.assertEqual(results, [True, True])
        self.assertEqual(await self.read(14), TEMPERATURE + TARGET)


if __name__ == "__main__":
    unittest.main()
