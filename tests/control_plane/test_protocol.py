"""Internal handlers over real Unix socket pairs; no deployed UID separation.

Only the operator or reader role under test uses the actual test UID. Other
roles/keys are synthetic fixtures, not a claim of production permissions.
"""
import asyncio
from datetime import datetime, timezone
import json
import os
import socket
import select
import termios
import threading
import tempfile
import time
import unittest
from unittest.mock import patch

from control_plane.approval import ApprovalCoordinator, ApprovalError, GrantCodec, Policy
from control_plane.protocol import ApprovalEndpoint, MAX_REFERENCES, Port
from control_plane.review import OperatorClient, PendingReview
from control_plane.transport import ChannelError, UnixMessageChannel
from control_plane.state import CoordinatorState


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_closed_endpoint_cannot_mint_grant(self):
        self.endpoint.close()
        with self.assertRaises(ApprovalError) as error:
            self.endpoint._dispatch(dict(version=1,op='approve',action_id=self.action.action_id),
                                    Port.CONTROL,self.operator)
        self.assertEqual(error.exception.code,'CLOSED')
        self.assertEqual(self.endpoint._grants,{})
        self.assertEqual(self.coordinator.status(self.action.action_id,peer_uid=self.operator).state,'pending')

    async def test_shutdown_during_receive_rejects_late_approval(self):
        channel=await self.factory()
        await asyncio.sleep(0)  # Handler passed its first closed check and owns receive.
        self.assertTrue(self.channels[0]._busy)
        self.endpoint.close()
        async with channel:
            await channel.send(dict(version=1,op='approve',action_id=self.action.action_id))
            response=await channel.receive()
        await self.drain()
        self.assertEqual(response,dict(version=1,ok=False,error_code='CLOSED'))
        self.assertEqual(self.endpoint._grants,{})
        self.assertEqual(self.coordinator.status(self.action.action_id,peer_uid=self.operator).state,'pending')

    def setUp(self):
        self.uid = os.geteuid()
        self.now = [time.monotonic_ns()]
        self.make_endpoint()
        self.tasks = []
        self.channels = []
        self.calls = 0

    def make_endpoint(self, *, reader=False):
        actual = os.geteuid()
        self.reader = actual if reader else 13001
        self.operator = 13003 if reader else actual
        policy = Policy('synthetic-lab', 'policy-1', self.reader, 13002, frozenset({self.operator}))
        codec = GrantCodec(policy, bytes(range(32)), 'test-epoch', clock=lambda: self.now[0])
        self.coordinator = ApprovalCoordinator(codec, clock=lambda: self.now[0],
            utc=lambda: datetime.now(timezone.utc))
        self.endpoint = ApprovalEndpoint(self.coordinator)
        self.session = self.coordinator.open_session(peer_uid=self.operator)
        self.action = self.coordinator.propose(peer_uid=self.reader, session_id=self.session,
            trace_id='trace-1', unit='nginx.service', evidence_ids=['synthetic-evidence'])

    async def drain(self, allowed=()):
        tasks, self.tasks = self.tasks, []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                self.assertIsInstance(result, ChannelError)
                self.assertIn(result.code, allowed)
        self.assertTrue(all(task.done() for task in tasks))

    async def asyncTearDown(self):
        # Every test owns/joins each server handler; no service task is detached.
        for task in self.tasks:
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        for channel in self.channels:
            self.assertTrue(channel.closed)
            self.assertFalse(channel._busy)
        self.endpoint.close()

    async def factory(self, *, port=Port.CONTROL, seconds=1):
        self.calls += 1
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        server = UnixMessageChannel(left, expected_uid=self.uid)
        client = UnixMessageChannel(right, expected_uid=self.uid, seconds=seconds)
        self.channels.extend((server, client))
        self.tasks.append(asyncio.create_task(self.endpoint.serve(server, port=port)))
        return client

    async def exchange(self, request, *, port=Port.CONTROL):
        async with await self.factory(port=port) as channel:
            await channel.send(request)
            response = await channel.receive()
        await self.drain()
        return response

    def confirmed(self, review):
        decision, output = self.tty(review, 'DONG_Y ')
        self.assertEqual(decision, 'confirmed')
        return output

    def tty(self, review, prefix):
        master, slave = os.openpty()
        original = termios.tcgetattr(slave)
        output = bytearray()
        errors = []
        def driver():
            try:
                end = time.monotonic() + 3
                while time.monotonic() < end:
                    if select.select([master], [], [], .02)[0]:
                        output.extend(os.read(master, 8192))
                        if b'nh\xe1\xba\xadp secrets' in output:
                            os.write(master, (prefix + review.action_id + '\n').encode())
                            return
                raise AssertionError('NO_PROMPT')
            except Exception as error:
                errors.append(type(error).__name__)
        driver_task = threading.Thread(target=driver)
        try:
            with os.fdopen(os.dup(slave), 'r') as stdin, os.fdopen(os.dup(slave), 'w') as stdout:
                driver_task.start()
                decision = review.confirm(stdin=stdin, stdout=stdout)
                driver_task.join(4)
                self.assertFalse(driver_task.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(termios.tcgetattr(slave), original)
                self.assertFalse(review.cleanup_pending)
            return decision, output.decode(errors='replace')
        finally:
            driver_task.join(4) if driver_task.ident is not None else None
            os.close(master)
            os.close(slave)

    async def test_tty_to_authenticated_control_approval_keeps_grant_private(self):
        client = OperatorClient(self.factory, expected_uid=self.uid)
        review = await client.pending(self.action.action_id)
        await self.drain()
        output = self.confirmed(review)
        updated = await client.submit(review)
        await self.drain()
        self.assertEqual(updated.state, 'approved')
        self.assertFalse(updated._record['execution_enabled'])
        self.assertIsNone(updated._record['executed'])
        self.assertEqual(self.calls, 2)
        self.assertEqual(len(self.endpoint._grants), 1)
        grant = self.endpoint._grants[self.action.action_id]
        self.assertNotIn(grant.wire.decode(), output)
        self.assertFalse(hasattr(updated, 'grant'))
        self.assertNotIn(json.loads(grant.wire)['mac'], repr(self.endpoint))
        self.assertEqual(self.coordinator.codec.verify(grant, peer_uid=13002)['action_id'], self.action.action_id)
        with self.assertRaises(ApprovalError):
            await client.submit(review)
        self.assertEqual(self.calls, 2)  # consumed local confirmation; no resend

    async def test_durable_tty_socket_approval_survives_restart_without_replay(self):
        with tempfile.TemporaryDirectory(prefix='approval-durable-ipc-') as folder:
            codec = self.coordinator.codec
            with CoordinatorState(folder, codec) as store:
                self.coordinator = ApprovalCoordinator(codec, clock=lambda: self.now[0], store=store)
                self.endpoint = ApprovalEndpoint(self.coordinator)
                session = self.coordinator.open_session(peer_uid=self.operator)
                action = self.coordinator.propose(peer_uid=self.reader, session_id=session,
                    trace_id='durable-trace', unit='nginx.service', evidence_ids=['synthetic-evidence'])
                client = OperatorClient(self.factory, expected_uid=self.uid)
                review = await client.pending(action.action_id)
                await self.drain()
                self.confirmed(review)
                updated = await client.submit(review)
                await self.drain()
                self.assertEqual(updated.state, 'approved')
                self.assertEqual(len(self.endpoint._grants), 1)
                self.endpoint.close()
            with CoordinatorState(folder, codec) as reopened:
                self.coordinator = ApprovalCoordinator(codec, clock=lambda: self.now[0], store=reopened)
                self.endpoint = ApprovalEndpoint(self.coordinator)
                client = OperatorClient(self.factory, expected_uid=self.uid)
                result = await client.pending(action.action_id)
                await self.drain()
                self.assertEqual((result.state, result.session_id, result.trace_id, result.evidence_ids),
                    ('approved', session, 'durable-trace', ('synthetic-evidence',)))
                self.assertEqual(result.confirm(), 'state_final')
                with self.assertRaises(ApprovalError):
                    await client.submit(result)
                self.assertEqual(self.calls, 3)  # status only after restart, no replay
                self.assertEqual(self.endpoint._grants, {})
                self.assertIsNone(result._record['executed'])
                self.assertFalse(result._record['execution_enabled'])
                self.endpoint.close()

    async def test_tty_rejection_no_grant_or_execution(self):
        client = OperatorClient(self.factory, expected_uid=self.uid)
        review = await client.pending(self.action.action_id)
        await self.drain()
        decision, _ = self.tty(review, 'TU_CHOI ')
        self.assertEqual(decision, 'rejected')
        updated = await client.submit(review)
        await self.drain()
        self.assertEqual(updated.state, 'denied')
        self.assertEqual(self.endpoint._grants, {})

    async def test_non_tty_or_no_confirmation_never_sends_approval(self):
        import io
        client = OperatorClient(self.factory, expected_uid=self.uid)
        review = await client.pending(self.action.action_id)
        await self.drain()
        for before in (True, False):
            if not before:
                self.assertEqual(review.confirm(stdin=io.StringIO(), stdout=io.StringIO()), 'non_tty')
            with self.assertRaises(ApprovalError) as error:
                await client.submit(review)
            self.assertEqual(error.exception.code, 'CONFIRMATION_REQUIRED')
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.endpoint._grants, {})

    async def test_reader_port_cannot_approve_reject_or_open_session(self):
        self.make_endpoint(reader=True)
        for op in ('approve', 'reject', 'open_session'):
            response = await self.exchange(dict(version=1, op=op, action_id=self.action.action_id),
                                           port=Port.PROPOSAL)
            self.assertEqual(response['error_code'], 'OPERATION_DENIED')
        self.assertEqual(self.endpoint._grants, {})
        self.assertEqual(self.coordinator.status(self.action.action_id, peer_uid=self.operator).state, 'pending')

    async def test_reader_cannot_use_control_socket_even_with_forged_uid(self):
        self.make_endpoint(reader=True)
        async with await self.factory() as channel:
            # Peer fails the port's role check before the body is parsed.
            try:
                await channel.send(dict(version=1, op='approve', uid=self.operator, action_id=self.action.action_id))
                await channel.receive()
            except ChannelError as error:
                self.assertEqual(error.code, 'DISCONNECTED')
        await self.drain(allowed=('PEER_DENIED',))
        self.assertEqual(self.endpoint._grants, {})

    async def test_reader_propose_and_status_preserve_correlation_not_grants(self):
        self.make_endpoint(reader=True)
        self.coordinator.reject(self.action.action_id, peer_uid=self.operator)
        response = await self.exchange(dict(version=1, op='propose', session_id=self.session,
            trace_id='trace-2', unit='nginx.service', evidence_ids=['synthetic-evidence-2']), port=Port.PROPOSAL)
        view = response['result']
        self.assertEqual((view['session_id'], view['trace_id'], view['evidence_ids']),
                         (self.session, 'trace-2', ['synthetic-evidence-2']))
        response = await self.exchange(dict(version=1, op='status', session_id='wrong-session',
            action_id=view['action_id']), port=Port.PROPOSAL)
        self.assertEqual(response['error_code'], 'CALLER_DENIED')
        response = await self.exchange(dict(version=1, op='status', session_id=self.session,
            action_id=view['action_id']), port=Port.PROPOSAL)
        self.assertEqual(response['result'], view)

    async def test_unknown_fields_commands_fake_tokens_and_versions_do_not_mint(self):
        base = dict(version=1, op='approve', action_id=self.action.action_id)
        for changes in ({'uid': self.operator}, {'approved': True}, {'grant': 'synthetic-canary'},
                        {'unit': 'ssh.service'}, {'command': 'restart'}, {'version': True}, {'version': 2}):
            response = await self.exchange({**base, **changes})
            self.assertFalse(response['ok'])
            self.assertEqual(response['error_code'], 'INVALID_REQUEST')
            self.assertNotIn('synthetic-canary', json.dumps(response))
        self.assertEqual(self.endpoint._grants, {})

    async def test_wrong_owner_action_cannot_be_approved(self):
        # The current kernel UID is an allowed operator, but not this session's owner.
        from dataclasses import replace
        old = self.coordinator.policy
        self.coordinator.policy = replace(old, operator_uids=old.operator_uids | {13004})
        session = self.coordinator.open_session(peer_uid=13004)
        self.coordinator.reject(self.action.action_id, peer_uid=self.operator)
        action = self.coordinator.propose(peer_uid=self.reader, session_id=session,
            trace_id='trace-other', unit='nginx.service', evidence_ids=['synthetic-evidence'])
        response = await self.exchange(dict(version=1, op='approve', action_id=action.action_id))
        self.assertEqual(response['error_code'], 'CALLER_DENIED')
        self.assertEqual(self.endpoint._grants, {})

    async def test_maximum_bounded_projection_fits_frame_and_refs_never_dropped(self):
        self.make_endpoint(reader=True)
        self.coordinator.reject(self.action.action_id, peer_uid=self.operator)
        refs = [str(i) + 'x' * 126 for i in range(MAX_REFERENCES)]
        response = await self.exchange(dict(version=1, op='propose', session_id=self.session,
            trace_id='t' * 128, unit='nginx.service', evidence_ids=refs), port=Port.PROPOSAL)
        self.assertEqual(response['result']['evidence_ids'], refs)
        self.assertLess(len(json.dumps(response).encode()), 4096)

    async def test_oversized_core_record_rejected_before_approval(self):
        self.coordinator.reject(self.action.action_id, peer_uid=self.operator)
        action = self.coordinator.propose(peer_uid=self.reader, session_id=self.session, trace_id='trace-2',
            unit='nginx.service', evidence_ids=['synthetic-' + str(i) for i in range(MAX_REFERENCES + 1)])
        response = await self.exchange(dict(version=1, op='approve', action_id=action.action_id))
        self.assertEqual(response['error_code'], 'OUTPUT_LIMIT')
        self.assertEqual(self.endpoint._grants, {})

    async def test_backend_expiry_after_confirmation_no_grant_or_retry(self):
        client = OperatorClient(self.factory, expected_uid=self.uid)
        review = await client.pending(self.action.action_id)
        await self.drain()
        self.confirmed(review)
        self.now[0] = self.action.expires_ns
        with self.assertRaises(ApprovalError) as error:
            await client.submit(review)
        self.assertEqual(error.exception.code, 'EXPIRED')
        await self.drain()
        self.assertEqual(self.endpoint._grants, {})
        self.assertEqual(self.calls, 2)

    async def test_actual_operator_peer_opens_session_without_uid_argument(self):
        response = await self.exchange(dict(version=1, op='open_session'))
        session = response['result']['session_id']
        self.assertRegex(session, '^[a-f0-9]{32}$')
        self.assertEqual(self.coordinator._sessions[session], self.uid)
        self.assertEqual(set(response['result']), {'session_id'})

    async def test_reader_proposal_wrong_unit_command_and_oversized_refs_fail_closed(self):
        self.make_endpoint(reader=True)
        self.coordinator.reject(self.action.action_id, peer_uid=self.operator)
        base = dict(version=1, op='propose', session_id=self.session,
                    trace_id='trace-2', unit='nginx.service', evidence_ids=['synthetic-evidence'])
        for changes in ({'unit': 'ssh.service'}, {'unit': 'nginx.service; restart'},
                        {'command': 'restart'}, {'approved': True},
                        {'evidence_ids': ['e' + str(i) for i in range(MAX_REFERENCES + 1)]}):
            response = await self.exchange({**base, **changes}, port=Port.PROPOSAL)
            self.assertFalse(response['ok'])
        self.assertEqual(len(self.coordinator._actions), 1)
        self.assertEqual(self.endpoint._grants, {})

    async def test_changed_response_binding_rejected_without_resubmission(self):
        client = OperatorClient(self.factory, expected_uid=self.uid)
        review = await client.pending(self.action.action_id)
        await self.drain()
        self.confirmed(review)
        original = self.endpoint._view
        def forged(action):
            result = original(action)
            if action.state == 'approved':
                result['evidence_ids'] = ['forged-evidence']
            return result
        with patch.object(self.endpoint, '_view', side_effect=forged):
            with self.assertRaises(ApprovalError) as error:
                await client.submit(review)
            self.assertEqual(error.exception.code, 'INVALID_RESPONSE')
        await self.drain()
        self.assertEqual(len(self.endpoint._grants), 1)
        with self.assertRaises(ApprovalError):
            await client.submit(review)
        self.assertEqual(self.calls, 2)

    async def test_timeout_after_backend_approval_retains_grant_no_resend(self):
        client = OperatorClient(self.factory, expected_uid=self.uid)
        review = await client.pending(self.action.action_id)
        await self.drain()
        self.confirmed(review)
        blocked = asyncio.Event()
        entered = asyncio.Event()
        original = UnixMessageChannel.send
        async def slow_response(channel, message):
            if 'result' in message:
                entered.set()
                await blocked.wait()
            await original(channel, message)
        async def fast_factory():
            return await self.factory(seconds=.03)
        client._factory = fast_factory
        with patch.object(UnixMessageChannel, 'send', slow_response):
            with self.assertRaises(ChannelError) as error:
                await client.submit(review)
            self.assertEqual(error.exception.code, 'TIMEOUT')
            self.assertTrue(entered.is_set())
            self.assertEqual(len(self.endpoint._grants), 1)
            self.assertEqual(review.decision, 'submitted')
            with self.assertRaises(ApprovalError):
                await client.submit(review)
            self.assertEqual(self.calls, 2)
            blocked.set()  # let the retained owner observe the closed peer
            await self.drain(allowed=('DISCONNECTED',))
        self.assertTrue(all(channel.closed for channel in self.channels))
        self.assertEqual(self.coordinator.status(self.action.action_id, peer_uid=self.operator).state, 'approved')

    async def test_caller_cancellation_after_approval_retains_both_owners(self):
        client = OperatorClient(self.factory, expected_uid=self.uid)
        review = await client.pending(self.action.action_id)
        await self.drain()
        self.confirmed(review)
        entered = asyncio.Event()
        blocker = asyncio.Event()
        original = UnixMessageChannel.send
        async def slow_response(channel, message):
            if 'result' in message:
                entered.set()
                await blocker.wait()
            await original(channel, message)
        with patch.object(UnixMessageChannel, 'send', slow_response):
            caller = asyncio.create_task(client.submit(review))
            await asyncio.wait_for(entered.wait(), 1)
            caller.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await caller
            self.assertTrue(caller.done())
            self.assertEqual(len(self.endpoint._grants), 1)
            self.assertEqual(review.decision, 'submitted')
            blocker.set()
            await self.drain(allowed=('DISCONNECTED',))
        self.assertTrue(all(channel.closed for channel in self.channels))

    async def test_confirmation_cannot_move_to_another_client(self):
        client = OperatorClient(self.factory, expected_uid=self.uid)
        review = await client.pending(self.action.action_id)
        await self.drain()
        self.confirmed(review)
        other = OperatorClient(self.factory, expected_uid=self.uid)
        with self.assertRaises(ApprovalError):
            await other.submit(review)
        self.assertEqual(self.calls, 1)

    async def test_client_pins_server_uid_before_sending(self):
        client = OperatorClient(self.factory, expected_uid=self.uid + 1)
        with self.assertRaises(ChannelError) as error:
            await client.pending(self.action.action_id)
        self.assertEqual(error.exception.code, 'PEER_DENIED')
        await self.drain(allowed=('DISCONNECTED',))
        self.assertEqual(self.endpoint._grants, {})

    async def test_malicious_response_code_not_reflected_and_no_grant_leaks(self):
        for response in (dict(version=1, ok=False, error_code='password=synthetic-canary'),
                         dict(version=True, ok=True, result={}),
                         dict(version=1, ok=True, result={}, grant='synthetic-canary')):
            async def fixture_factory():
                left, right = socket.socketpair()
                server = UnixMessageChannel(left, expected_uid=self.uid)
                client = UnixMessageChannel(right, expected_uid=self.uid)
                self.channels.extend((server, client))
                async def fixture():
                    async with server:
                        await server.receive()
                        await server.send(response)
                self.tasks.append(asyncio.create_task(fixture()))
                return client
            client = OperatorClient(fixture_factory, expected_uid=self.uid)
            with self.assertRaises(ApprovalError) as error:
                await client.pending(self.action.action_id)
            self.assertNotIn('synthetic-canary', str(error.exception))
            await self.drain()


if __name__ == '__main__':
    unittest.main()
