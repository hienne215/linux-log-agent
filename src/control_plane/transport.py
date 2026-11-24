"""Bounded Linux Unix-stream messages, with kernel peer authentication.

Internal control-plane transport candidate, not MCP framing or an approval API.
The caller supplies an already-connected, exclusively owned socket. No listener,
key loading, UID switching, executor, reconnect or background task is created.
SO_PEERCRED proves a process identity, not human presence or safe deployment.
"""
import asyncio
from dataclasses import dataclass
import json
import math
import socket
import struct

from .approval import _uid, _unique


MAX_MESSAGE_BYTES = 4096


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError()
    return number


class ChannelError(Exception):
    """Fixed safe code only; never include wire data or OS error text."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Peer:
    pid: int
    uid: int
    gid: int


class UnixMessageChannel:
    """One send and one receive under a single connection deadline.

    This object takes socket ownership even when authentication fails. Use an
    async context manager in the same task that performs I/O. Cancel and join an
    outstanding operation before closing; concurrent operations/close are denied.
    Timeout/cancellation closes the owned fd, with no detached cleanup task.
    Once sending starts, delivery is unknown until a higher-level authenticated
    response establishes an outcome. A completed send does not prove execution.
    """

    def __init__(self, connection, *, expected_uid, seconds=2, max_bytes=MAX_MESSAGE_BYTES):
        self._socket = connection
        self._busy = False
        self.sent = False
        self.received = False
        self.send_started = False
        self.closed = False
        try:
            if (type(connection) is not socket.socket or not _uid(expected_uid)
                    or type(seconds) not in (int, float) or not math.isfinite(seconds)
                    or type(max_bytes) is not int or max_bytes not in (4096,65536)
                    or not 0 < seconds <= (5 if max_bytes==4096 else 10)):
                raise ChannelError('INVALID_CONFIG')
            if (connection.family != socket.AF_UNIX
                    or connection.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM):
                raise ChannelError('INVALID_SOCKET')
            connection.getpeername()  # reject unconnected/listening sockets
            raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
            pid, uid, gid = struct.unpack('3i', raw)
            if pid <= 0 or uid != expected_uid or not _uid(gid):
                raise ChannelError('PEER_DENIED')
            self.peer = Peer(pid, uid, gid)
            self._max_bytes = max_bytes  # Trusted caller only; never a wire option.
            self._loop = asyncio.get_running_loop()
            self._deadline = self._loop.time() + seconds
            connection.setblocking(False)
        except ChannelError:
            self._close()
            raise
        except Exception:
            self._close()
            raise ChannelError('PEER_UNAVAILABLE') from None

    def __repr__(self):
        return '<UnixMessageChannel private; no wire data>'

    @property
    def delivery_unknown(self):
        # Only an application-level response can resolve this conservative state.
        return self.send_started

    def _close(self):
        try:
            if type(self._socket) is socket.socket:
                self._socket.close()
        finally:
            self.closed = True

    def close(self):
        if self._busy:
            raise ChannelError('BUSY')  # caller must retain/join the I/O owner
        self._close()

    async def __aenter__(self):
        if self.closed:
            raise ChannelError('CLOSED')
        return self

    async def __aexit__(self, *args):
        self.close()

    def _start(self, used):
        if self._busy:
            raise ChannelError('BUSY')
        if self.closed:
            raise ChannelError('CLOSED')
        if asyncio.get_running_loop() is not self._loop or used:
            raise ChannelError('STATE_FINAL')
        self._busy = True

    async def _read_exact(self, size):
        parts = bytearray()
        while len(parts) < size:
            chunk = await self._loop.sock_recv(self._socket, size - len(parts))
            if not chunk:
                raise ChannelError('DISCONNECTED')
            parts.extend(chunk)
        return bytes(parts)

    async def _perform(self, operation):
        try:
            async with asyncio.timeout_at(self._deadline):
                # Explicit check also covers immediately-ready I/O after expiry.
                if self._loop.time() >= self._deadline:
                    raise TimeoutError()
                return await operation()
        except asyncio.CancelledError:
            self._close()
            raise
        except TimeoutError:
            self._close()
            raise ChannelError('TIMEOUT') from None
        except ChannelError:
            self._close()
            raise
        except (OSError, ConnectionError):
            self._close()
            raise ChannelError('DISCONNECTED') from None
        except Exception:
            self._close()
            raise ChannelError('INVALID_FRAME') from None
        finally:
            self._busy = False

    async def receive(self):
        self._start(self.received)

        async def read():
            size = struct.unpack('!I', await self._read_exact(4))[0]
            if not 0 < size <= self._max_bytes:
                raise ChannelError('OUTPUT_LIMIT')
            wire = await self._read_exact(size)
            try:
                value = json.loads(wire.decode('utf-8'), object_pairs_hook=_unique,
                    parse_float=_finite_float,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if type(value) is not dict:
                    raise ValueError()
            except Exception:
                raise ChannelError('INVALID_FRAME') from None
            self.received = True
            return value  # still untrusted; handler must validate exact schema

        return await self._perform(read)

    async def send(self, message):
        self._start(self.sent)

        async def write():
            try:
                if type(message) is not dict:
                    raise ValueError()
                wire = json.dumps(message, ensure_ascii=True, allow_nan=False,
                                  separators=(',', ':')).encode('ascii')
            except Exception:
                raise ChannelError('INVALID_FRAME') from None
            if not 0 < len(wire) <= self._max_bytes:
                raise ChannelError('OUTPUT_LIMIT')
            # Expiry during encoding must not initiate a late write.
            if self._loop.time() >= self._deadline:
                raise TimeoutError()
            self.send_started = True
            await self._loop.sock_sendall(self._socket, struct.pack('!I', len(wire)) + wire)
            self.sent = True

        await self._perform(write)
