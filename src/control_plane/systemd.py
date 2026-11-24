"""Candidate job observer over an application-owned, already-connected D-Bus.

Never connects a bus or provisions privileges. No Agent/MCP entry point uses it.
Only the accepted helper service may supply a bus and dispatch authorization.
"""
import asyncio
import re

from dbus_next import Message, MessageFlag, MessageType

from .approval import ApprovalError
from .ledger import JobResult, job_id


MANAGER = 'org.freedesktop.systemd1.Manager'
PATH = '/org/freedesktop/systemd1'
DBUS = 'org.freedesktop.DBus'
DBUS_PATH = '/org/freedesktop/DBus'
SERVICE = 'org.freedesktop.systemd1'
UNIT = 'nginx.service'
RESULTS = {'done', 'failed', 'dependency', 'canceled', 'timeout', 'skipped'}
MATCHES = (
    "type='signal',interface='org.freedesktop.systemd1.Manager',member='JobRemoved',path='/org/freedesktop/systemd1'",
    "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='org.freedesktop.systemd1'",
)


class SystemdJob:
    """One job, one bus owner; signals are not a service-health assessment."""

    def __init__(self, bus):
        self.bus = bus
        self.owner = None
        self.job_path = None
        self._ready = self._started = self._closing = self._closed = False
        self._fault = None
        self._results = {}
        self._changed = asyncio.Event()
        self._watch = self._cleanup = None
        self._handler_installed = False

    @property
    def cleanup_pending(self):
        return not self._closed

    def _check(self, deadline):
        if asyncio.get_running_loop().time() >= deadline:
            raise ApprovalError('TIMEOUT')
        if self._fault:
            raise ApprovalError(self._fault)

    async def _call(self, deadline, *, destination, path, interface, member,
                    signature='', body=()):
        self._check(deadline)
        message = Message(destination=destination, path=path, interface=interface,
                          member=member, signature=signature, body=list(body),
                          flags=MessageFlag.NO_AUTOSTART)
        try:
            reply = await asyncio.wait_for(self.bus.call(message), deadline - asyncio.get_running_loop().time())
        except TimeoutError:
            raise ApprovalError('TIMEOUT') from None
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ApprovalError('TRANSPORT_ERROR') from None
        self._check(deadline)  # Reject a late reply from a cancellation-resistant binding.
        if not isinstance(reply, Message) or reply.message_type != MessageType.METHOD_RETURN:
            raise ApprovalError('PROTOCOL_ERROR')  # Do not reflect D-Bus error bodies.
        if reply.sender != destination:
            raise ApprovalError('PROTOCOL_ERROR')
        return reply

    async def prepare(self, deadline):
        if self._watch is not None or self._closing:
            raise ApprovalError('STATE_FINAL')
        self.bus.add_message_handler(self._signal)
        self._handler_installed = True
        self._watch = asyncio.create_task(self.bus.wait_for_disconnect(), name='helper-bus-owner')
        self._watch.add_done_callback(self._disconnected)
        for rule in MATCHES:
            reply = await self._call(deadline, destination=DBUS, path=DBUS_PATH,
                                     interface=DBUS, member='AddMatch', signature='s', body=[rule])
            if reply.signature or reply.body:
                raise ApprovalError('PROTOCOL_ERROR')
        reply = await self._call(deadline, destination=DBUS, path=DBUS_PATH,
                                 interface=DBUS, member='GetNameOwner', signature='s', body=[SERVICE])
        if (reply.signature != 's' or len(reply.body) != 1 or type(reply.body[0]) is not str
                or re.fullmatch(r':[0-9]+\.[0-9]+', reply.body[0]) is None or len(reply.body[0]) > 64):
            raise ApprovalError('PROTOCOL_ERROR')
        self.owner = reply.body[0]
        reply = await self._call(deadline, destination=self.owner, path=PATH,
                                 interface=MANAGER, member='Subscribe')
        if reply.signature or reply.body:
            raise ApprovalError('PROTOCOL_ERROR')
        self._ready = True

    def _disconnected(self, task):
        try:
            task.exception()
        except asyncio.CancelledError:
            pass
        if not self._closing:
            self._fault = 'DISCONNECTED'
            self._changed.set()

    def _signal(self, message):
        if self._closing or message.message_type != MessageType.SIGNAL:
            return
        if (message.sender == DBUS and message.path == DBUS_PATH and message.interface == DBUS
                and message.member == 'NameOwnerChanged'):
            if message.signature != 'sss' or len(message.body) != 3:
                self._fault = 'PROTOCOL_ERROR'
            elif message.body[0] == SERVICE and (self.owner is None or message.body[2] != self.owner):
                self._fault = 'DISCONNECTED'
            self._changed.set()
            return
        if (not self._started or message.sender != self.owner or message.path != PATH
                or message.interface != MANAGER or message.member != 'JobRemoved'):
            return
        try:
            if message.signature != 'uoss' or len(message.body) != 4:
                raise ValueError()
            number, path, unit, result = message.body
            if unit != UNIT:
                return
            if type(number) is not int or number != job_id(path) or result not in RESULTS:
                raise ValueError()
            previous = self._results.get(path)
            if (previous is not None and previous != result) or (previous is None and len(self._results) >= 16):
                raise ValueError()
            self._results[path] = result
        except (ValueError, TypeError, ApprovalError):
            self._fault = 'PROTOCOL_ERROR'
        self._changed.set()

    async def restart(self, deadline, before_dispatch):
        if not self._ready or self._started or self._closing:
            raise ApprovalError('STATE_FINAL')
        self._check(deadline)
        before_dispatch()  # Synchronous, application-owned policy/expiry recheck.
        self._check(deadline)
        self._started = True  # Own signals before the method reply: early completion is possible.
        reply = await self._call(deadline, destination=self.owner, path=PATH,
                                 interface=MANAGER, member='RestartUnit',
                                 signature='ss', body=[UNIT, 'fail'])
        if reply.signature != 'o' or len(reply.body) != 1:
            raise ApprovalError('PROTOCOL_ERROR')
        job_id(reply.body[0])
        self.job_path = reply.body[0]
        while True:
            self._check(deadline)
            if self.job_path in self._results:
                return JobResult(self.job_path, self._results[self.job_path])
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), deadline - asyncio.get_running_loop().time())
            except TimeoutError:
                raise ApprovalError('TIMEOUT') from None

    async def _close(self):
        self._closing = True
        if self._handler_installed:
            self.bus.remove_message_handler(self._signal)
            self._handler_installed = False
        # Disconnect our connection only. Do not CancelJob or infer backend cancellation.
        self.bus.disconnect()
        if self._watch is None:
            self._watch = asyncio.create_task(self.bus.wait_for_disconnect(), name='helper-bus-owner')
        try:
            await asyncio.shield(self._watch)
        except asyncio.CancelledError:
            raise
        except Exception:
            if self.bus.connected:
                raise ApprovalError('CLEANUP_PENDING') from None
        if self.bus.connected:
            raise ApprovalError('CLEANUP_PENDING')
        self._closed = True

    async def finish_cleanup(self, seconds=1):
        if type(seconds) not in (int, float) or not 0 < seconds <= 5:
            raise ApprovalError('INVALID_POLICY')
        if self._closed:
            return True
        self._closing = True
        if self._cleanup is None or self._cleanup.done():
            if self._cleanup is not None:
                try:
                    self._cleanup.result()
                except Exception:
                    pass
            self._cleanup = asyncio.create_task(self._close(), name='helper-bus-cleanup')
        try:
            await asyncio.wait_for(asyncio.shield(self._cleanup), seconds)
        except asyncio.CancelledError:
            raise  # Retain the cleanup owner for a subsequent join.
        except Exception:
            return False
        return self._closed
