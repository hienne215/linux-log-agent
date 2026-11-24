"""Mock D-Bus only: never constructs MessageBus or connects a system bus."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from control_plane.approval import ApprovalCoordinator, ApprovalError, Grant, GrantCodec, Policy
from control_plane.ledger import HelperLedger, JobResult
try:
    from dbus_next import Message, MessageFlag, MessageType
    from control_plane.helper import HelperRunner
    from control_plane.systemd import DBUS, DBUS_PATH, MANAGER, PATH, SERVICE, SystemdJob
    HAS_BINDING = True
except ModuleNotFoundError as error:
    if error.name != 'dbus_next': raise
    HAS_BINDING = False
    PATH = '/org/freedesktop/systemd1'


JOB = PATH + '/job/42'
OWNER = ':1.42'


async def assert_no_helper_tasks():
    await asyncio.sleep(0)
    pending = [task.get_name() for task in asyncio.all_tasks()
               if task is not asyncio.current_task() and not task.done()
               and task.get_name() in ('helper-bus-owner', 'helper-bus-cleanup')]
    if pending: raise AssertionError('unjoined helper owners: ' + ','.join(pending))


class FakeBus:
    def __init__(self, result='done'):
        self.connected = True
        self.handlers = []
        self.calls = []
        self.stopped = asyncio.Event()
        self.release_cleanup = asyncio.Event()
        self.release_cleanup.set()
        self.started = asyncio.Event()
        self.restart_hook = None
        self.call_hook = None
        self.result = result
        self.disconnects = 0

    def add_message_handler(self, handler): self.handlers.append(handler)
    def remove_message_handler(self, handler): self.handlers.remove(handler)

    def emit(self, **changes):
        fields = dict(path=PATH, interface=MANAGER, member='JobRemoved', sender=OWNER,
                      message_type=MessageType.SIGNAL, signature='uoss',
                      body=[42, JOB, 'nginx.service', self.result or 'done'])
        message = Message(**(fields | changes))
        for handler in list(self.handlers): handler(message)

    async def call(self, message):
        self.calls.append(message)
        if self.call_hook is not None:
            overridden = self.call_hook(message)
            if overridden is not None: return overridden
        signature, body = '', []
        if message.member == 'GetNameOwner': signature, body = 's', [OWNER]
        if message.member == 'RestartUnit':
            self.started.set()
            if self.restart_hook is not None: await self.restart_hook()
            elif self.result is not None: self.emit()
            signature, body = 'o', [JOB]
        return Message(message_type=MessageType.METHOD_RETURN, reply_serial=1,
                       sender=message.destination, signature=signature, body=body)

    def disconnect(self):
        self.disconnects += 1
        self.connected = False
        self.stopped.set()

    async def wait_for_disconnect(self):
        await self.stopped.wait()
        await self.release_cleanup.wait()

    @property
    def writes(self): return [c for c in self.calls if c.member == 'RestartUnit']


@unittest.skipUnless(HAS_BINDING, 'requires optional control dependency dbus-next==0.2.3')
class DriverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.addAsyncCleanup(assert_no_helper_tasks)

    async def make(self, **kwargs):
        bus = FakeBus(**kwargs)
        driver = SystemdJob(bus)
        self.addAsyncCleanup(driver.finish_cleanup)
        await driver.prepare(asyncio.get_running_loop().time() + 1)
        return bus, driver

    async def restart(self, driver, seconds=.05):
        return await driver.restart(asyncio.get_running_loop().time() + seconds, lambda: None)

    async def test_fixed_method_flags_and_early_completion(self):
        bus, driver = await self.make()
        result = await self.restart(driver)
        self.assertEqual([m.member for m in bus.calls], ['AddMatch', 'AddMatch', 'GetNameOwner', 'Subscribe', 'RestartUnit'])
        for message in bus.calls:
            self.assertEqual(message.flags, MessageFlag.NO_AUTOSTART)
        request = bus.writes[0]
        self.assertEqual((request.destination, request.path, request.interface, request.signature, request.body),
                         (OWNER, PATH, MANAGER, 'ss', ['nginx.service', 'fail']))
        self.assertEqual((result.job_path, result.outcome, result.executed), (JOB, 'succeeded', True))
        self.assertTrue(await driver.finish_cleanup())
        self.assertEqual(bus.handlers, [])
        self.assertTrue(driver._watch.done()); self.assertTrue(driver._cleanup.done())
        self.assertFalse(driver.cleanup_pending)

    async def test_job_id_alone_is_unknown_until_signal(self):
        bus, driver = await self.make(result=None)
        with self.assertRaises(ApprovalError) as e: await self.restart(driver, .01)
        self.assertEqual(e.exception.code, 'TIMEOUT')
        self.assertEqual(driver.job_path, JOB); self.assertEqual(len(bus.writes), 1)

    async def test_all_observed_results_remain_distinct(self):
        for result in ('done', 'failed', 'dependency', 'canceled', 'timeout', 'skipped'):
            with self.subTest(result=result):
                bus, driver = await self.make(result=result)
                observed = await self.restart(driver)
                expected = 'succeeded' if result == 'done' else 'failed' if result in ('failed', 'dependency') else 'execution_unknown'
                self.assertEqual(observed.outcome, expected)
                self.assertIs(observed.executed, True if result == 'done' else None)
                self.assertTrue(await driver.finish_cleanup())

    async def test_wrong_sender_path_unit_or_job_cannot_complete(self):
        for changes in ({'sender': ':1.43'}, {'path': PATH + '/other'},
                        {'body': [42, JOB, 'ssh.service', 'done']},
                        {'body': [43, PATH + '/job/43', 'nginx.service', 'done']}):
            with self.subTest(changes=changes):
                bus, driver = await self.make(result=None)
                async def emit(): bus.emit(**changes)
                bus.restart_hook = emit
                with self.assertRaises(ApprovalError) as e: await self.restart(driver, .01)
                self.assertEqual(e.exception.code, 'TIMEOUT')
                await driver.finish_cleanup()

    async def test_malformed_conflicting_and_overflow_signals_fail_closed(self):
        for mode in ('mismatch', 'signature', 'conflict', 'overflow'):
            with self.subTest(mode=mode):
                bus, driver = await self.make(result=None)
                async def emit():
                    if mode == 'mismatch': bus.emit(body=[43, JOB, 'nginx.service', 'done'])
                    elif mode == 'signature': bus.emit(signature='s', body=['private-canary'])
                    elif mode == 'conflict':
                        bus.emit(body=[42, JOB, 'nginx.service', 'done'])
                        bus.emit(body=[42, JOB, 'nginx.service', 'failed'])
                    else:
                        for i in range(1, 18): bus.emit(body=[i, PATH + '/job/' + str(i), 'nginx.service', 'done'])
                bus.restart_hook = emit
                with self.assertRaises(ApprovalError) as e: await self.restart(driver)
                self.assertEqual(str(e.exception), 'PROTOCOL_ERROR')
                await driver.finish_cleanup()

    async def test_owner_change_invalidates_pending_job(self):
        bus, driver = await self.make(result=None)
        async def changed():
            bus.emit(sender=DBUS, path=DBUS_PATH, interface=DBUS, member='NameOwnerChanged',
                     signature='sss', body=[SERVICE, OWNER, ':1.99'])
        bus.restart_hook = changed
        with self.assertRaises(ApprovalError) as e: await self.restart(driver)
        self.assertEqual(e.exception.code, 'DISCONNECTED')
        self.assertEqual(len(bus.writes), 1)

    async def test_owner_change_during_resolution_prevents_dispatch(self):
        bus = FakeBus()
        driver = SystemdJob(bus)
        self.addAsyncCleanup(driver.finish_cleanup)
        def changed(message):
            if message.member == 'GetNameOwner':
                bus.emit(sender=DBUS, path=DBUS_PATH, interface=DBUS, member='NameOwnerChanged',
                         signature='sss', body=[SERVICE, OWNER, ':1.99'])
        bus.call_hook = changed
        with self.assertRaises(ApprovalError) as e:
            await driver.prepare(asyncio.get_running_loop().time() + 1)
        self.assertEqual(e.exception.code, 'DISCONNECTED'); self.assertEqual(bus.writes, [])

    async def test_disconnect_before_dispatch_never_writes(self):
        bus, driver = await self.make()
        bus.disconnect(); await asyncio.sleep(0); await asyncio.sleep(0)
        with self.assertRaises(ApprovalError) as e: await self.restart(driver)
        self.assertEqual(e.exception.code, 'DISCONNECTED'); self.assertEqual(bus.writes, [])

    async def test_raw_error_body_is_not_exposed(self):
        bus, driver = await self.make()
        def fail(message):
            return Message(message_type=MessageType.ERROR, reply_serial=1, sender=OWNER,
                           error_name='org.example.Error', signature='s', body=['private-canary'])
        bus.call_hook = fail
        with self.assertRaises(ApprovalError) as e: await self.restart(driver)
        self.assertEqual(str(e.exception), 'PROTOCOL_ERROR')

    async def test_late_reply_cannot_be_accepted_after_deadline(self):
        bus, driver = await self.make()
        async def stubborn():
            try: await asyncio.sleep(10)
            except asyncio.CancelledError: bus.emit()
        bus.restart_hook = stubborn
        with self.assertRaises(ApprovalError) as e: await self.restart(driver, .01)
        self.assertEqual(e.exception.code, 'TIMEOUT'); self.assertEqual(len(bus.writes), 1)

    async def test_cleanup_timeout_and_repeated_cancellation_keep_owner(self):
        bus, driver = await self.make()
        bus.release_cleanup.clear()
        self.assertFalse(await driver.finish_cleanup(.01))
        owner = driver._cleanup
        for _ in range(2):
            task = asyncio.create_task(driver.finish_cleanup())
            await asyncio.sleep(0); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertIs(driver._cleanup, owner)
            self.assertTrue(driver.cleanup_pending); self.assertFalse(owner.done())
        bus.release_cleanup.set()
        self.assertTrue(await driver.finish_cleanup())
        self.assertEqual(bus.disconnects, 1); self.assertTrue(owner.done()); self.assertTrue(driver._watch.done())

    async def test_cleanup_error_is_safe_and_retry_only_closes_bus(self):
        bus, driver = await self.make()
        original = bus.disconnect
        with patch.object(bus, 'disconnect', side_effect=OSError('private-canary')):
            self.assertFalse(await driver.finish_cleanup())
        self.assertTrue(driver.cleanup_pending)
        self.assertTrue(await driver.finish_cleanup())
        self.assertEqual(bus.writes, [])

    async def test_dispatch_authorization_failure_and_second_dispatch_are_denied(self):
        bus, driver = await self.make()
        def deny(): raise ApprovalError('EXPIRED')
        with self.assertRaises(ApprovalError):
            await driver.restart(asyncio.get_running_loop().time() + 1, deny)
        self.assertEqual(bus.writes, [])
        await self.restart(driver)
        with self.assertRaises(ApprovalError): await self.restart(driver)
        self.assertEqual(len(bus.writes), 1)


@unittest.skipUnless(HAS_BINDING, 'requires optional control dependency dbus-next==0.2.3')
class HelperTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.addAsyncCleanup(assert_no_helper_tasks)
        self.temp = tempfile.TemporaryDirectory(prefix='helper-offline-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.now = [10**10]
        self.policy = Policy('synthetic-lab', 'policy-1', 12001, 12002, frozenset({12003}))
        self.codec = GrantCodec(self.policy, bytes(range(32)), 'synthetic-epoch', clock=lambda: self.now[0])
        core = ApprovalCoordinator(self.codec, clock=lambda: self.now[0], utc=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
        session = core.open_session(peer_uid=12003)
        self.action = core.propose(peer_uid=12001, session_id=session, trace_id='synthetic-trace',
                                   unit='nginx.service', evidence_ids=['synthetic-evidence'])
        self.grant = core.approve(self.action.action_id, peer_uid=12003)
        self.ledger = HelperLedger(self.directory, self.codec)
        self.addCleanup(self.ledger.close)
        self.bus = FakeBus()
        self.driver = SystemdJob(self.bus)
        self.runner = HelperRunner(self.ledger, lambda: self.driver)
        self.addAsyncCleanup(self.runner.finish_cleanup)

    async def execute(self, **kwargs):
        return await self.runner.execute(self.grant, peer_uid=12002, **kwargs)

    async def test_done_persists_full_correlation_and_never_replays(self):
        outcome = await self.execute()
        attempt = outcome.attempt
        self.assertEqual((attempt.action_id, attempt.session_id, attempt.trace_id, attempt.environment_id),
                         (self.action.action_id, self.action.session_id, 'synthetic-trace', 'synthetic-lab'))
        self.assertEqual((attempt.outcome, attempt.executed, attempt.job_result), ('succeeded', True, 'done'))
        self.assertFalse(outcome.cleanup_pending); self.assertFalse(self.runner.cleanup_pending)
        self.ledger.close()
        with HelperLedger(self.directory, self.codec) as reopened:
            self.assertEqual(reopened.status(attempt.action_id), attempt)
            with self.assertRaises(ApprovalError): reopened.consume(self.grant, peer_uid=12002)
            with self.assertRaises(ApprovalError): reopened.finish(attempt.action_id, JobResult(JOB, 'done'))
        self.assertEqual(len(self.bus.writes), 1)

    async def test_invalid_signature_uid_or_expiry_does_not_allocate_bus(self):
        for grant, uid in ((Grant(b'private-canary'), 12002), (self.grant, 12001)):
            with patch.object(self.runner, '_factory') as factory:
                with self.assertRaises(ApprovalError): await self.runner.execute(grant, peer_uid=uid)
                factory.assert_not_called()
        self.now[0] = self.action.expires_ns
        with self.assertRaises(ApprovalError): await self.execute()
        self.assertEqual(self.bus.calls, [])

    async def test_intent_audit_failure_prevents_dispatch(self):
        with sqlite3.connect(self.directory / 'ledger.sqlite') as db:
            db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'private-canary'); END")
        with self.assertRaises(ApprovalError) as e: await self.execute()
        self.assertEqual(str(e.exception), 'STATE_UNAVAILABLE')
        self.assertEqual(self.bus.writes, []); self.assertFalse(self.runner.cleanup_pending)
        self.assertIsNone(self.ledger.status(self.action.action_id))

    async def test_expiry_after_durable_intent_stays_unknown_without_dispatch(self):
        original = self.ledger.consume
        def expire(*args, **kwargs):
            attempt = original(*args, **kwargs)
            self.now[0] = self.action.expires_ns
            return attempt
        with patch.object(self.ledger, 'consume', expire): outcome = await self.execute()
        self.assertEqual(self.bus.writes, [])
        self.assertEqual((outcome.attempt.outcome, outcome.attempt.error_code), ('execution_unknown', 'EXPIRED'))
        self.assertIsNone(outcome.attempt.executed)

    async def test_timeout_records_unknown_and_separate_cleanup_completes(self):
        self.bus.result = None
        # Inject timeout at the completion wait, not a 10ms race against the
        # real intent fsync. A slower VM may legitimately expire before dispatch.
        wait_for = asyncio.wait_for
        async def completion_timeout(awaitable, timeout):
            frame = getattr(awaitable, 'cr_frame', None)
            if frame is not None and frame.f_locals.get('self') is self.driver._changed:
                awaitable.close()
                raise TimeoutError()
            return await wait_for(awaitable, timeout)
        with patch('control_plane.systemd.asyncio.wait_for', completion_timeout):
            outcome = await self.execute()
        self.assertEqual(outcome.attempt.error_code, 'TIMEOUT')
        self.assertEqual(outcome.attempt.outcome, 'execution_unknown'); self.assertIsNone(outcome.attempt.executed)
        self.assertFalse(outcome.cleanup_pending); self.assertEqual(len(self.bus.writes), 1)
        self.assertTrue(self.driver._watch.done()); self.assertTrue(self.driver._cleanup.done())

    async def test_deadline_after_intent_before_dispatch_keeps_unknown_without_write(self):
        restart = self.driver.restart
        async def expired(deadline, check):
            return await restart(asyncio.get_running_loop().time() - 1, check)
        with patch.object(self.driver, 'restart', expired):
            outcome = await self.execute()
        self.assertEqual((outcome.attempt.outcome, outcome.attempt.error_code), ('execution_unknown', 'TIMEOUT'))
        self.assertIsNone(outcome.attempt.executed)
        self.assertEqual(self.bus.writes, [])
        self.assertFalse(outcome.cleanup_pending)
        self.assertTrue(self.driver._watch.done()); self.assertTrue(self.driver._cleanup.done())

    async def test_disconnect_after_dispatch_records_unknown_without_retry(self):
        async def disconnect():
            self.bus.disconnect()
            await asyncio.sleep(0); await asyncio.sleep(0)
        self.bus.restart_hook = disconnect
        outcome = await self.execute()
        self.assertEqual((outcome.attempt.outcome, outcome.attempt.error_code), ('execution_unknown', 'DISCONNECTED'))
        self.assertIsNone(outcome.attempt.executed); self.assertEqual(len(self.bus.writes), 1)
        self.assertFalse(outcome.cleanup_pending)

    async def test_cancel_during_preparation_reaps_owner_without_consuming_intent(self):
        started = asyncio.Event()
        original = self.bus.call
        async def blocked(message):
            started.set()
            await asyncio.sleep(10)
            return await original(message)
        self.bus.call = blocked
        task = asyncio.create_task(self.execute())
        await started.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertIsNone(self.ledger.status(self.action.action_id))
        self.assertFalse(self.runner.cleanup_pending)
        self.assertEqual(self.bus.writes, []); self.assertTrue(self.driver._watch.done())

    async def test_intent_sync_uncertainty_fences_generation_without_dispatch(self):
        with patch.object(self.ledger._database, 'sync', side_effect=OSError('private-canary')):
            with self.assertRaises(ApprovalError) as e: await self.execute()
        self.assertEqual(str(e.exception), 'STATE_UNAVAILABLE')
        with self.assertRaises(ApprovalError): self.ledger.consume(self.grant, peer_uid=12002)
        self.assertEqual(self.bus.writes, []); self.assertFalse(self.runner.cleanup_pending)

    async def test_job_done_does_not_unlock_unit_for_later_grant(self):
        await self.execute()
        other = ApprovalCoordinator(self.codec, clock=lambda: self.now[0], utc=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
        session = other.open_session(peer_uid=12003)
        action = other.propose(peer_uid=12001, session_id=session, trace_id='new-trace',
                               unit='nginx.service', evidence_ids=['synthetic-evidence'])
        grant = other.approve(action.action_id, peer_uid=12003)
        with self.assertRaises(ApprovalError) as e: self.ledger.consume(grant, peer_uid=12002)
        self.assertEqual(e.exception.code, 'UNIT_LOCKED'); self.assertEqual(len(self.bus.writes), 1)

    async def test_cancellation_records_unknown_before_propagating(self):
        self.bus.result = None
        task = asyncio.create_task(self.execute())
        await self.bus.started.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        attempt = self.ledger.status(self.action.action_id)
        self.assertEqual((attempt.outcome, attempt.error_code), ('execution_unknown', 'CANCELLED'))
        self.assertIsNone(attempt.executed); self.assertFalse(self.runner.cleanup_pending)
        self.assertEqual(len(self.bus.writes), 1)

    async def test_cleanup_pending_blocks_new_request_until_owner_joined(self):
        self.bus.release_cleanup.clear()
        outcome = await self.execute(cleanup_seconds=.01)
        self.assertTrue(outcome.cleanup_pending); self.assertTrue(self.runner.cleanup_pending)
        with self.assertRaises(ApprovalError) as e: await self.execute()
        self.assertEqual(e.exception.code, 'BUSY')
        self.bus.release_cleanup.set()
        self.assertTrue(await self.runner.finish_cleanup())
        self.assertTrue(self.driver._watch.done()); self.assertTrue(self.driver._cleanup.done())
        self.assertEqual(len(self.bus.writes), 1)

    async def test_caller_cancel_during_cleanup_retains_observed_result_and_owner(self):
        self.bus.release_cleanup.clear()
        task = asyncio.create_task(self.execute())
        await self.bus.started.wait()
        while self.driver._cleanup is None: await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.runner.last_attempt.job_result, 'done')
        self.assertTrue(self.runner.cleanup_pending)
        self.bus.release_cleanup.set(); self.assertTrue(await self.runner.finish_cleanup())

    async def test_explicit_cleanup_has_one_owner_and_blocks_concurrent_operations(self):
        self.bus.release_cleanup.clear()
        await self.execute(cleanup_seconds=.01)
        join = asyncio.create_task(self.runner.finish_cleanup())
        await asyncio.sleep(0)
        with self.assertRaises(ApprovalError) as e: await self.runner.finish_cleanup()
        self.assertEqual(e.exception.code, 'BUSY')
        with self.assertRaises(ApprovalError) as e: await self.execute()
        self.assertEqual(e.exception.code, 'BUSY')
        self.bus.release_cleanup.set()
        self.assertTrue(await join); self.assertFalse(self.runner.cleanup_pending)
        self.assertEqual(len(self.bus.writes), 1)

    async def test_concurrent_request_is_refused_without_second_bus(self):
        self.bus.result = None
        task = asyncio.create_task(self.execute(seconds=.02))
        await self.bus.started.wait()
        with self.assertRaises(ApprovalError) as e: await self.execute()
        self.assertEqual(e.exception.code, 'BUSY')
        await task; self.assertEqual(len(self.bus.writes), 1)

    async def test_failed_result_persistence_never_advertises_success_or_retries(self):
        with sqlite3.connect(self.directory / 'ledger.sqlite') as db:
            db.execute("CREATE TRIGGER fail_result BEFORE INSERT ON outcomes BEGIN SELECT RAISE(ABORT,'private-canary'); END")
        with self.assertRaises(ApprovalError) as e: await self.execute()
        self.assertEqual(str(e.exception), 'STATE_UNAVAILABLE')
        self.assertEqual(self.runner.last_attempt.outcome, 'execution_unknown')
        self.assertIsNone(self.runner.last_attempt.executed)
        with self.assertRaises(ApprovalError): await self.execute()
        self.assertEqual(len(self.bus.writes), 1); self.assertFalse(self.runner.cleanup_pending)

    async def test_unknown_intent_cannot_be_finalized_by_reopened_owner(self):
        attempt = self.ledger.consume(self.grant, peer_uid=12002)
        self.ledger.close()
        with HelperLedger(self.directory, self.codec) as reopened:
            with self.assertRaises(ApprovalError) as e: reopened.finish(attempt.action_id, JobResult(JOB, 'done'))
            self.assertEqual(e.exception.code, 'STATE_FINAL')
            self.assertIsNone(reopened.status(attempt.action_id).executed)

    async def test_invalid_job_result_is_rejected(self):
        for kwargs in ({'job_path': '/tmp/42'}, {'job_path': PATH + '/job/0'},
                       {'job_path': PATH + '/job/4294967296'}, {'job_result': 'done'},
                       {'job_path': JOB, 'job_result': 'done', 'error_code': 'TIMEOUT'},
                       {'error_code': 'private-canary'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ApprovalError): JobResult(**kwargs)


if __name__ == '__main__': unittest.main()
