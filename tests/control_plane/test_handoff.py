"""Private handoff over real test-owned sockets; systemd remains entirely fake.

Same-UID sockets prove framing/peer checks, NOT separated privileged deployment.
Operator/reader role IDs in policy calls are synthetic; no approval is genuine.
"""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from control_plane.approval import ApprovalCoordinator, ApprovalError, GrantCodec, Policy
from control_plane.ledger import HelperLedger
from control_plane.protocol import ApprovalEndpoint, Port
from control_plane.transport import ChannelError, UnixMessageChannel
try:
    from control_plane.handoff import HelperHandoff
    from control_plane.helper import HelperRunner
    from control_plane.service import HelperService
    from control_plane.systemd import SystemdJob
    try:
        from .test_helper import FakeBus, assert_no_helper_tasks
    except ImportError:
        from test_helper import FakeBus, assert_no_helper_tasks
    HAS_BINDING = True
except ModuleNotFoundError as error:
    if error.name != 'dbus_next': raise
    HAS_BINDING = False


@unittest.skipUnless(HAS_BINDING, 'requires optional control dependency dbus-next==0.2.3')
class HandoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.addAsyncCleanup(self.no_owners)
        temp = tempfile.TemporaryDirectory(prefix='handoff-offline-')
        self.addCleanup(temp.cleanup)
        self.uid = os.getuid()
        self.now = [10**10]
        policy = Policy('synthetic-lab', 'policy-1', self.uid + 1, self.uid, frozenset({self.uid + 2}))
        self.codec = GrantCodec(policy, bytes(range(32)), 'synthetic-epoch', clock=lambda: self.now[0])
        self.core = ApprovalCoordinator(self.codec, clock=lambda: self.now[0], utc=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.endpoint = ApprovalEndpoint(self.core)
        self.ledger = HelperLedger(Path(temp.name), self.codec)
        self.addCleanup(self.ledger.close)
        self.bus = FakeBus()
        self.driver = SystemdJob(self.bus)
        self.runner = HelperRunner(self.ledger, lambda: self.driver)
        self.service = HelperService(self.runner)
        self.addAsyncCleanup(self.service.shutdown)
        self.handoff = HelperHandoff(self.endpoint, helper_uid=self.uid)
        self.session = self.core.open_session(peer_uid=self.uid + 2)
        view = self.endpoint._dispatch(dict(version=1, op='propose', session_id=self.session,
            trace_id='test-trace', unit='nginx.service', evidence_ids=['synthetic-evidence']), Port.PROPOSAL, self.uid + 1)
        self.action_id = view['action_id']

    async def no_owners(self):
        await assert_no_helper_tasks()
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()
                   and t.get_name() == 'helper-service-owner']
        self.assertEqual(pending, [])

    def channels(self):
        left, right = socket.socketpair()
        return (UnixMessageChannel(left, expected_uid=self.uid),
                UnixMessageChannel(right, expected_uid=self.uid))

    async def rpc(self, operation, *, server=None):
        client, peer = self.channels()
        task = asyncio.create_task((server or self.service.serve)(peer))
        try:
            return await operation(client)
        finally:
            client.close()
            try: await asyncio.wait_for(task, 2)
            except ChannelError: pass  # A deliberately rejected/lost request closes its peer.

    def approve(self):
        return self.endpoint._dispatch(dict(version=1, op='approve', action_id=self.action_id),
                                       Port.CONTROL, self.uid + 2)

    async def ready(self):
        await self.rpc(self.handoff.hello)

    async def submit(self):
        return await self.rpc(lambda channel: self.handoff.submit(channel, self.action_id))

    async def test_hello_private_handoff_observation_and_status_end_to_end(self):
        view = self.approve()
        self.assertFalse(view['execution_enabled']); self.assertIsNone(view['executed'])
        self.assertNotIn('grant', json.dumps(view))
        grant = self.endpoint._grants[self.action_id]
        await self.ready()
        admitted = await self.submit()
        self.assertEqual(admitted['outcome'], 'execution_unknown'); self.assertIsNone(admitted['executed'])
        self.assertNotIn(self.action_id, self.endpoint._grants)
        self.assertTrue(await self.service.finish_cleanup())
        result = await self.rpc(lambda c: self.handoff.status(c, self.action_id))
        self.assertEqual((result['job_result'], result['executed']), ('done', True))
        self.assertEqual(result['trace_id'], 'test-trace'); self.assertEqual(result['session_id'], self.session)
        self.assertEqual(result['environment_id'], 'synthetic-lab')
        self.assertFalse(result['cleanup_pending']); self.assertFalse(self.service.cleanup_pending)
        self.assertNotIn(grant.wire.decode(), repr(self.handoff))
        self.assertNotIn(grant.wire.decode(), json.dumps(result))
        self.assertEqual(len(self.bus.writes), 1)
        # Proposal/control API remains disabled and cannot grant itself execution.
        status = self.endpoint._dispatch(dict(version=1, op='status', action_id=self.action_id), Port.CONTROL, self.uid + 2)
        self.assertEqual(status['state'], 'approved'); self.assertIsNone(status['executed'])

    async def test_pending_or_rejected_action_cannot_be_handed_off(self):
        await self.ready()
        with self.assertRaises(ApprovalError): await self.submit()
        self.endpoint._dispatch(dict(version=1, op='reject', action_id=self.action_id), Port.CONTROL, self.uid + 2)
        with self.assertRaises(ApprovalError): await self.submit()
        self.assertEqual(self.bus.calls, []); self.assertFalse(self.service.cleanup_pending)

    async def test_handoff_requires_epoch_handshake(self):
        self.approve()
        with self.assertRaises(ApprovalError): await self.submit()
        self.assertIn(self.action_id, self.endpoint._grants)
        await self.ready()
        self.assertEqual(self.bus.calls, [])

    async def test_changed_helper_epoch_or_policy_denies_handshake(self):
        original = self.service.codec
        for epoch, env in (('new-epoch', 'synthetic-lab'), ('synthetic-epoch', 'other-lab')):
            self.service.codec = GrantCodec(Policy(env, 'policy-1', self.uid + 1, self.uid, frozenset({self.uid + 2})),
                                           bytes(range(32)), epoch, clock=lambda: self.now[0])
            with self.assertRaises(ApprovalError) as e: await self.ready()
            self.assertEqual(e.exception.code, 'INVALID_POLICY')
        self.service.codec = original
        self.assertEqual(self.bus.calls, [])

    async def test_expired_grant_consumes_outbox_but_does_not_dispatch(self):
        self.approve(); await self.ready()
        self.now[0] += 61 * 10**9
        with self.assertRaises(ApprovalError) as e: await self.submit()
        self.assertEqual(e.exception.code, 'EXPIRED')
        self.assertNotIn(self.action_id, self.endpoint._grants)
        self.assertEqual(self.bus.calls, [])

    async def test_lost_submit_response_keeps_local_correlation_and_does_not_retry(self):
        self.approve(); await self.ready()
        async def drop(channel):
            async with channel:
                request = await channel.receive()
                self.service._request(request, channel.peer.uid)
        with self.assertRaises(ApprovalError) as e:
            await self.rpc(lambda c: self.handoff.submit(c, self.action_id), server=drop)
        self.assertEqual(e.exception.code, 'EXECUTION_UNKNOWN')
        self.assertEqual(self.handoff.pending_ids, (self.action_id,))
        self.assertTrue(await self.service.finish_cleanup())
        result = await self.rpc(lambda c: self.handoff.status(c, self.action_id))
        self.assertEqual(result['job_result'], 'done')
        with self.assertRaises(ApprovalError): await self.submit()
        self.assertEqual(len(self.bus.writes), 1)

    async def test_admission_survives_handler_cancellation_with_owned_job(self):
        self.approve(); await self.ready()
        self.bus.result = None
        client, peer = self.channels()
        admitted = asyncio.Event()
        async def server():
            async with peer:
                self.service._request(await peer.receive(), peer.peer.uid)
                admitted.set()
                await asyncio.Event().wait()
        handler = asyncio.create_task(server())
        sender = asyncio.create_task(self.handoff.submit(client, self.action_id))
        await admitted.wait(); handler.cancel()
        with self.assertRaises(asyncio.CancelledError): await handler
        with self.assertRaises(ApprovalError): await sender
        self.assertTrue(self.service.cleanup_pending)
        await self.bus.started.wait()
        self.bus.emit()
        self.assertTrue(await self.service.finish_cleanup())
        self.assertEqual(len(self.bus.writes), 1)

    async def test_shutdown_timeout_retains_owner_and_stops_new_admission(self):
        self.approve(); await self.ready(); self.bus.result = None
        await self.submit(); await self.bus.started.wait()
        owner = self.service._task
        self.assertFalse(await self.service.shutdown(.01))
        self.assertIs(self.service._task, owner); self.assertFalse(owner.done())
        with self.assertRaises(ApprovalError): self.service._request({'version': 1, 'op': 'hello'}, self.uid)
        self.bus.emit()
        self.assertTrue(await self.service.shutdown())
        self.assertTrue(owner.done()); self.assertFalse(self.service.cleanup_pending)

    async def test_caller_cancellation_during_shutdown_retains_service_owner(self):
        self.approve(); await self.ready(); self.bus.result = None
        await self.submit(); await self.bus.started.wait()
        owner = self.service._task
        task = asyncio.create_task(self.service.shutdown())
        await asyncio.sleep(0); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertIs(self.service._task, owner); self.assertFalse(owner.done())
        self.bus.emit(); self.assertTrue(await self.service.shutdown())

    async def test_replay_and_json_caller_privilege_fields_are_denied(self):
        self.approve(); await self.ready()
        grant = self.endpoint._grants[self.action_id]
        await self.submit(); self.assertTrue(await self.service.finish_cleanup())
        for request in ({'version': 1, 'op': 'submit', 'grant': grant.wire.decode()},
                        {'version': 1, 'op': 'submit', 'grant': grant.wire.decode(), 'uid': self.uid},
                        {'version': 1, 'op': 'execute', 'unit': 'ssh.service'},
                        {'version': 1, 'op': 'submit', 'grant': 'private-canary'}):
            with self.subTest(op=request['op']), self.assertRaises(ApprovalError) as e:
                self.service._request(request, self.uid)
            self.assertNotIn('private-canary', str(e.exception))
        self.assertEqual(len(self.bus.writes), 1)

    async def test_wrong_kernel_peer_cannot_submit_even_valid_signed_grant(self):
        self.approve()
        client, peer = self.channels()
        other = GrantCodec(Policy('synthetic-lab', 'policy-1', self.uid + 3, self.uid + 4, frozenset({self.uid + 2})),
                          bytes(range(32)), 'synthetic-epoch')
        self.service.codec = other
        handler = asyncio.create_task(self.service.serve(peer))
        with self.assertRaises(ChannelError) as e: await handler
        self.assertEqual(e.exception.code, 'PEER_DENIED')
        client.close(); self.assertTrue(peer.closed)
        self.assertEqual(self.bus.calls, [])

    async def test_wrong_helper_uid_cannot_consume_approved_outbox(self):
        self.approve(); await self.ready()
        self.handoff._uid = self.uid + 10
        with self.assertRaises(ApprovalError) as e: await self.submit()
        self.assertEqual(e.exception.code, 'CALLER_DENIED')
        self.assertIn(self.action_id, self.endpoint._grants)
        self.assertEqual(self.handoff.pending_ids, ()); self.assertEqual(self.bus.calls, [])

    async def test_forged_result_correlation_or_job_state_is_rejected(self):
        self.approve(); await self.ready()
        await self.submit(); self.assertTrue(await self.service.finish_cleanup())
        baseline = self.service._status(self.action_id)
        for changes in ({'trace_id': 'forged'}, {'unit': 'ssh.service'}, {'executed': False},
                        {'outcome': 'succeeded', 'job_result': None}, {'job_path': '/tmp/fake'},
                        {'cleanup_pending': 'false'}, {'secret': 'private-canary'}):
            with self.subTest(changes=changes), self.assertRaises(ApprovalError):
                self.handoff._validate(baseline | changes, self.action_id)

    async def test_result_storage_failure_remains_unknown(self):
        self.approve(); await self.ready()
        with patch.object(self.ledger, 'finish', side_effect=OSError('private-canary')):
            await self.submit(); self.assertTrue(await self.service.finish_cleanup())
        result = await self.rpc(lambda c: self.handoff.status(c, self.action_id))
        self.assertEqual(result['outcome'], 'execution_unknown'); self.assertIsNone(result['executed'])
        self.assertEqual(result['error_code'], 'STATE_UNAVAILABLE')
        self.assertNotIn('private-canary', json.dumps(result))

    async def test_status_query_unknown_action_does_not_dispatch(self):
        await self.ready()
        with self.assertRaises(ApprovalError):
            await self.rpc(lambda c: self.handoff.status(c, 'not-owned'))
        self.assertEqual(self.bus.calls, [])

    async def test_persisted_job_status_after_new_owner_never_recovers_grant_or_dispatches(self):
        self.approve();await self.ready();await self.submit();await self.service.finish_cleanup()
        before=list(self.bus.writes)
        fresh=HelperService(HelperRunner(self.ledger,lambda:self.driver))
        self.addAsyncCleanup(fresh.shutdown)
        query=HelperHandoff(self.endpoint,helper_uid=self.uid)
        action=self.core.status(self.action_id,peer_uid=self.uid+2)
        result=await self.rpc(lambda c:query.read_status(c,action),server=fresh.serve)
        self.assertEqual(result['job_result'],'done');self.assertIs(result['executed'],True)
        self.assertEqual(self.bus.writes,before);self.assertEqual(query.pending_ids,())
        self.assertEqual(fresh._records,{})

    async def test_persisted_intent_without_outcome_stays_unknown_after_new_owner(self):
        self.approve();grant=self.endpoint._take_grant(self.action_id)
        self.ledger.consume(grant,peer_uid=self.uid)
        fresh=HelperService(HelperRunner(self.ledger,lambda:self.driver));self.addAsyncCleanup(fresh.shutdown)
        query=HelperHandoff(self.endpoint,helper_uid=self.uid)
        action=self.core.status(self.action_id,peer_uid=self.uid+2)
        result=await self.rpc(lambda c:query.read_status(c,action),server=fresh.serve)
        self.assertEqual(result['outcome'],'execution_unknown');self.assertIsNone(result['executed'])
        self.assertEqual(self.bus.calls,[]);self.assertEqual(query.pending_ids,())

    async def test_cleanup_pending_is_preserved_across_status(self):
        self.approve(); await self.ready(); self.bus.release_cleanup.clear()
        await self.submit(); await self.bus.started.wait()
        self.assertFalse(await self.service.finish_cleanup(.01))
        result = await self.rpc(lambda c: self.handoff.status(c, self.action_id))
        self.assertTrue(result['cleanup_pending'])
        self.bus.release_cleanup.set()
        self.assertTrue(await self.service.finish_cleanup())


if __name__ == '__main__': unittest.main()
