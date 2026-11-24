"""Real Linux Unix sockets; no privileged service, Agent or approval dispatch."""
import asyncio
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import unittest

from control_plane.transport import ChannelError, MAX_MESSAGE_BYTES, UnixMessageChannel


def frame(wire):
    return struct.pack('!I', len(wire)) + wire


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def pair(self, *, seconds=1):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        right.setblocking(False)
        channel = UnixMessageChannel(left, expected_uid=os.geteuid(), seconds=seconds)
        return channel, right

    async def assert_code(self, code, operation):
        with self.assertRaises(ChannelError) as error:
            await operation
        self.assertEqual(str(error.exception), code)

    async def test_kernel_credentials_not_uid_from_json(self):
        channel, peer = self.pair()
        self.assertEqual(channel.peer.uid, os.geteuid())
        self.assertEqual(channel.peer.pid, os.getpid())
        self.assertEqual(channel.peer.gid, os.getegid())
        async with channel:
            await asyncio.get_running_loop().sock_sendall(peer, frame(b'{"uid":0,"approved":true}'))
            data = await channel.receive()
            self.assertEqual(data['uid'], 0)  # untrusted body is not authorization
            self.assertEqual(channel.peer.uid, os.geteuid())
            self.assertFalse(hasattr(channel, 'approve'))
        self.assertTrue(channel.closed)

    async def test_wrong_expected_uid_closes_before_read(self):
        left, right = socket.socketpair()
        self.addCleanup(right.close)
        with self.assertRaises(ChannelError) as error:
            UnixMessageChannel(left, expected_uid=os.geteuid() + 1)
        self.assertEqual(str(error.exception), 'PEER_DENIED')
        self.assertEqual(left.fileno(), -1)

    async def test_rejects_unconnected_and_non_unix_sockets(self):
        for family in (socket.AF_UNIX, socket.AF_INET):
            with self.subTest(family=family):
                connection = socket.socket(family, socket.SOCK_STREAM)
                with self.assertRaises(ChannelError):
                    UnixMessageChannel(connection, expected_uid=os.geteuid())
                self.assertEqual(connection.fileno(), -1)

    async def test_invalid_uid_and_budgets_fail_closed(self):
        for options in ({'expected_uid': True}, {'seconds': 0}, {'seconds': 6},
                        {'seconds': float('nan')}, {'seconds': True}):
            left, right = socket.socketpair()
            self.addCleanup(right.close)
            with self.subTest(options=options), self.assertRaises(ChannelError):
                UnixMessageChannel(left, **{'expected_uid': os.geteuid(), **options})
            self.assertEqual(left.fileno(), -1)

    async def test_fragmented_header_and_body_complete_one_message(self):
        channel, peer = self.pair()
        wire = frame('{"message":"đề xuất"}'.encode())
        async def fragments():
            for byte in wire:
                await asyncio.get_running_loop().sock_sendall(peer, bytes([byte]))
                await asyncio.sleep(0)
        task = asyncio.create_task(fragments())
        try:
            async with channel:
                self.assertEqual(await channel.receive(), {'message': 'đề xuất'})
                self.assertTrue(channel.received)
                await self.assert_code('STATE_FINAL', channel.receive())
        finally:
            await task

    async def test_one_reply_no_repeat_send_no_execution_claim(self):
        channel, peer = self.pair()
        async with channel:
            await channel.send({'status': 'pending'})
            wire = await asyncio.get_running_loop().sock_recv(peer, 4096)
            self.assertEqual(struct.unpack('!I', wire[:4])[0], len(wire) - 4)
            self.assertEqual(json.loads(wire[4:]), {'status': 'pending'})
            await self.assert_code('STATE_FINAL', channel.send({'status': 'approved'}))
            self.assertTrue(channel.delivery_unknown)
            self.assertFalse(hasattr(channel, 'executed'))

    async def test_size_is_utf8_bytes_and_header_limit_checked_before_body(self):
        for size in (0, MAX_MESSAGE_BYTES + 1, 2**32 - 1):
            channel, peer = self.pair()
            await asyncio.get_running_loop().sock_sendall(peer, struct.pack('!I', size))
            await self.assert_code('OUTPUT_LIMIT', channel.receive())
            self.assertFalse(channel.received)
            self.assertTrue(channel.closed)
        channel, peer = self.pair()
        wire = b'{"x":"' + b'a' * (MAX_MESSAGE_BYTES - 8) + b'"}'
        self.assertEqual(len(wire), MAX_MESSAGE_BYTES)
        await asyncio.get_running_loop().sock_sendall(peer, frame(wire))
        async with channel:
            self.assertEqual(len((await channel.receive())['x']), MAX_MESSAGE_BYTES - 8)

    async def test_malformed_duplicate_keys_nonfinite_and_bad_utf8_hide_payload(self):
        for wire in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'[]',
                     b'{"private-canary":', b'{"x":"\xff"}'):
            channel, peer = self.pair()
            await asyncio.get_running_loop().sock_sendall(peer, frame(wire))
            with self.subTest(wire=wire):
                await self.assert_code('INVALID_FRAME', channel.receive())
            self.assertTrue(channel.closed)
            self.assertFalse(channel.received)
            self.assertNotIn('private-canary', repr(channel))

    async def test_invalid_or_oversized_outbound_never_starts_send(self):
        for value, code in (([], 'INVALID_FRAME'), ({'secret': float('inf')}, 'INVALID_FRAME'),
                            ({'secret': 'x' * MAX_MESSAGE_BYTES}, 'OUTPUT_LIMIT')):
            channel, _ = self.pair()
            await self.assert_code(code, channel.send(value))
            self.assertFalse(channel.send_started)
            self.assertFalse(channel.delivery_unknown)
            self.assertTrue(channel.closed)

    async def test_eof_header_and_partial_body_close_without_success(self):
        for wire in (b'', b'\x00\x00', struct.pack('!I', 10) + b'{}'):
            channel, peer = self.pair()
            await asyncio.get_running_loop().sock_sendall(peer, wire)
            peer.close()
            await self.assert_code('DISCONNECTED', channel.receive())
            self.assertFalse(channel.received)
            self.assertTrue(channel.closed)

    async def test_total_deadline_does_not_reset_between_fragments(self):
        channel, peer = self.pair(seconds=.08)
        await asyncio.get_running_loop().sock_sendall(peer, struct.pack('!I', 20))
        await asyncio.sleep(.05)
        await asyncio.get_running_loop().sock_sendall(peer, b'{')
        started = asyncio.get_running_loop().time()
        await self.assert_code('TIMEOUT', channel.receive())
        self.assertLess(asyncio.get_running_loop().time() - started, .07)
        self.assertTrue(channel.closed)

    async def test_expired_deadline_never_starts_late_send(self):
        channel, _ = self.pair(seconds=.01)
        await asyncio.sleep(.02)
        await self.assert_code('TIMEOUT', channel.send({'status': 'pending'}))
        self.assertFalse(channel.send_started)

    async def test_deadline_shared_by_send_and_receive(self):
        channel, peer = self.pair(seconds=.04)
        await channel.send({'action_id': 'synthetic'})
        await asyncio.sleep(.05)
        await asyncio.get_running_loop().sock_sendall(peer, frame(b'{"status":"pending"}'))
        await self.assert_code('TIMEOUT', channel.receive())
        self.assertTrue(channel.delivery_unknown)
        self.assertFalse(channel.received)

    async def test_cancellation_partial_receive_closes_fd_and_joins_owner(self):
        channel, peer = self.pair()
        await asyncio.get_running_loop().sock_sendall(peer, struct.pack('!I', 100) + b'{')
        tasks = set(asyncio.all_tasks())
        owner = asyncio.create_task(channel.receive())
        await asyncio.sleep(0)
        owner.cancel()
        owner.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await owner
        self.assertTrue(owner.done())
        self.assertTrue(channel.closed)
        self.assertEqual(channel._socket.fileno(), -1)
        self.assertFalse(channel.received)
        self.assertFalse(channel._busy)
        self.assertEqual(set(asyncio.all_tasks()), tasks)
        await self.assert_code('CLOSED', channel.receive())

    async def test_no_concurrent_operations_or_close_can_drop_owner(self):
        channel, _ = self.pair()
        owner = asyncio.create_task(channel.receive())
        try:
            await asyncio.sleep(0)
            await self.assert_code('BUSY', channel.receive())
            await self.assert_code('BUSY', channel.send({}))
            with self.assertRaises(ChannelError) as error:
                channel.close()
            self.assertEqual(str(error.exception), 'BUSY')
            self.assertFalse(channel.closed)
        finally:
            owner.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await owner
        self.assertTrue(channel.closed)

    async def test_send_to_disconnected_peer_is_unknown_no_retry(self):
        channel, peer = self.pair()
        peer.close()
        await self.assert_code('DISCONNECTED', channel.send({'action_id': 'synthetic'}))
        self.assertTrue(channel.send_started)
        self.assertFalse(channel.sent)
        self.assertTrue(channel.delivery_unknown)
        await self.assert_code('CLOSED', channel.send({}))

    def fill_send_buffer(self, channel):
        channel._socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
        for _ in range(64):
            try:
                channel._socket.send(b'x' * 4096)
            except BlockingIOError:
                return
        self.fail('Test could not create bounded local socket backpressure')

    async def test_blocked_send_timeout_is_unknown_and_fd_is_closed(self):
        channel, _ = self.pair(seconds=.03)
        self.fill_send_buffer(channel)
        await self.assert_code('TIMEOUT', channel.send({'action_id': 'synthetic'}))
        self.assertTrue(channel.delivery_unknown)
        self.assertFalse(channel.sent)
        self.assertTrue(channel.closed)
        self.assertFalse(channel._busy)

    async def test_blocked_send_cancellation_joins_owner_and_cannot_send_later(self):
        channel, _ = self.pair()
        self.fill_send_buffer(channel)
        tasks = set(asyncio.all_tasks())
        owner = asyncio.create_task(channel.send({'action_id': 'synthetic'}))
        await asyncio.sleep(0)
        self.assertTrue(channel.send_started)
        owner.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await owner
        self.assertTrue(channel.closed)
        self.assertFalse(channel.sent)
        self.assertTrue(channel.delivery_unknown)
        self.assertEqual(set(asyncio.all_tasks()), tasks)
        await self.assert_code('CLOSED', channel.send({}))

    async def test_context_closes_after_handler_exception_without_detached_task(self):
        channel, _ = self.pair()
        with self.assertRaises(ValueError):
            async with channel:
                raise ValueError('synthetic handler failure')
        self.assertTrue(channel.closed)
        self.assertFalse(channel._busy)

    async def test_real_filesystem_socket_authenticates_child_pid_and_reaps(self):
        with tempfile.TemporaryDirectory(prefix='approval-ipc-test-') as folder:
            endpoint = str(Path(folder) / 'control.sock')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(endpoint)
                listener.listen(1)
                listener.setblocking(False)
                code = ('import socket,struct,sys; s=socket.socket(socket.AF_UNIX); '
                        's.connect(sys.argv[1]); b=b\'{"synthetic":true}\'; '
                        's.sendall(struct.pack("!I",len(b))+b); s.close()')
                child = await asyncio.create_subprocess_exec(sys.executable, '-B', '-c', code, endpoint,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                try:
                    connection, _ = await asyncio.wait_for(asyncio.get_running_loop().sock_accept(listener), 2)
                    async with UnixMessageChannel(connection, expected_uid=os.geteuid()) as channel:
                        self.assertEqual(channel.peer.pid, child.pid)
                        self.assertEqual(await channel.receive(), {'synthetic': True})
                    self.assertEqual(await asyncio.wait_for(child.wait(), 2), 0)
                finally:
                    if child.returncode is None:
                        child.kill()
                        await child.wait()
                self.assertIsNotNone(child.returncode)
                with self.assertRaises(ProcessLookupError):
                    os.kill(child.pid, 0)
                self.assertTrue(channel.closed)


if __name__ == '__main__':
    unittest.main()
