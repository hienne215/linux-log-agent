"""Offline policy tests only. No human approval, service, helper or Linux action."""
from dataclasses import FrozenInstanceError,replace
from concurrent.futures import ThreadPoolExecutor,TimeoutError as FutureTimeout
from datetime import datetime,timedelta,timezone
import json
import threading
import unittest
from unittest.mock import patch

from control_plane.approval import ApprovalCoordinator,ApprovalError,Grant,GrantCodec,Policy


class Clock:
    def __init__(self):self.ns=10**10
    def __call__(self):return self.ns
    def utc(self):return datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(microseconds=self.ns//1000)


def setup():
    clock=Clock()
    policy=Policy('synthetic-lab','policy-1',12001,12002,frozenset({12003,12004}))
    codec=GrantCodec(policy,bytes(range(32)),'synthetic-epoch-1',clock=clock)
    coordinator=ApprovalCoordinator(codec,clock=clock,utc=clock.utc)
    session=coordinator.open_session(peer_uid=12003)
    return clock,policy,codec,coordinator,session


def propose(c,session,**changes):
    request=dict(peer_uid=12001,session_id=session,trace_id='synthetic-trace',
                 unit='nginx.service',evidence_ids=['synthetic-evidence-1'])
    return c.propose(**{**request,**changes})


class ApprovalTests(unittest.TestCase):
    def test_reader_status_requires_matching_session_and_never_exposes_grant(self):
        _,_,_,c,s=setup();a=propose(c,s)
        for session in (None,'unknown'):
            with self.assertRaises(ApprovalError):c.status(a.action_id,peer_uid=12001,session_id=session)
        c.approve(a.action_id,peer_uid=12003)
        view=c.status(a.action_id,peer_uid=12001,session_id=s)
        self.assertEqual(view.state,'approved')
        self.assertFalse(hasattr(view,'wire'));self.assertFalse(hasattr(view,'grant'))
        with self.assertRaises(ApprovalError):c.reject(a.action_id,peer_uid=12001)

    def test_wall_clock_jump_cannot_extend_monotonic_grant_expiry(self):
        clock,policy,codec,c,s=setup();a=propose(c,s)
        # UTC is audit metadata; moving the wall clock must not extend TTL.
        with patch.object(c,'utc',return_value=datetime(2038,1,1,tzinfo=timezone.utc)):
            grant=c.approve(a.action_id,peer_uid=12003)
        self.assertEqual(codec.verify(grant,peer_uid=policy.coordinator_uid)['action_id'],a.action_id)
        clock.ns=a.expires_ns
        with self.assertRaises(ApprovalError) as error:codec.verify(grant,peer_uid=policy.coordinator_uid)
        self.assertEqual(error.exception.code,'EXPIRED')

    def test_expiry_between_confirmation_checks_does_not_mint(self):
        _,_,codec,c,s=setup();a=propose(c,s)
        with patch.object(c,'clock',side_effect=[a.expires_ns-1,a.expires_ns]),patch.object(codec,'_sign') as sign:
            with self.assertRaises(ApprovalError) as error:c.approve(a.action_id,peer_uid=12003)
            self.assertEqual(error.exception.code,'EXPIRED');sign.assert_not_called()
        self.assertEqual(c.status(a.action_id,peer_uid=12003).state,'expired')

    def test_session_and_action_capacity_fail_closed(self):
        clock,_,_,c,s=setup()
        for _ in range(63):c.open_session(peer_uid=12003)
        with self.assertRaises(ApprovalError) as error:c.open_session(peer_uid=12003)
        self.assertEqual(error.exception.code,'CAPACITY')
        for _ in range(128):
            action=propose(c,s);clock.ns=action.expires_ns
        with self.assertRaises(ApprovalError) as error:propose(c,s)
        self.assertEqual(error.exception.code,'CAPACITY')

    def test_concurrent_confirmation_can_mint_only_one_grant(self):
        _,_,codec,c,s=setup();a=propose(c,s)
        entered=threading.Event();release=threading.Event();original=codec._sign
        def slow_first(*args):
            if not entered.is_set():entered.set();release.wait(2)
            return original(*args)
        def approve():
            try:c.approve(a.action_id,peer_uid=12003);return 'minted'
            except ApprovalError as error:return error.code
        with patch.object(codec,'_sign',slow_first),ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(approve)
            try:
                self.assertTrue(entered.wait(1))
                second=pool.submit(approve)
                try:second.result(timeout=.1)
                except FutureTimeout:pass
            finally:release.set()
            results=[first.result(timeout=2),second.result(timeout=2)]
        self.assertEqual(results.count('minted'),1)
        self.assertIn('STATE_FINAL',results)

    def test_separate_identities_and_bounded_ttl_required(self):
        _,p,*_=setup()
        for change in (dict(reader_uid=0),dict(reader_uid=p.coordinator_uid),dict(operator_uids=frozenset({p.reader_uid})),
                       dict(operator_uids=frozenset()),dict(operator_uids=frozenset({True})),dict(ttl_seconds=61),dict(ttl_seconds=True)):
            with self.subTest(change=change),self.assertRaises(ApprovalError):replace(p,**change)

    def test_reader_cannot_open_operator_session_or_approve(self):
        _,_,_,c,session=setup()
        with self.assertRaises(ApprovalError):c.open_session(peer_uid=12001)
        a=propose(c,session)
        for caller in (12001,12002,12004,0,True):
            with self.subTest(caller=caller),self.assertRaises(ApprovalError):c.approve(a.action_id,peer_uid=caller)
        self.assertEqual(c.status(a.action_id,peer_uid=12003).state,'pending')

    def test_allowlist_references_and_unknown_fields_fail_closed(self):
        for change in (dict(unit='ssh.service'),dict(unit='nginx.service; id'),dict(evidence_ids=[]),
                       dict(evidence_ids=['a','a']),dict(evidence_ids=['secret\ncontrol']),dict(trace_id='x'*129),
                       dict(session_id='unknown'),dict(peer_uid=12003)):
            _,_,_,c,s=setup()
            with self.subTest(change=change),self.assertRaises(ApprovalError):propose(c,s,**change)
        _,_,_,c,s=setup()
        with self.assertRaises(TypeError):propose(c,s,approved=True)
        with self.assertRaises(TypeError):propose(c,s,command='restart')

    def test_single_transition_and_explicit_rejection_no_grant(self):
        _,_,codec,c,s=setup();a=propose(c,s)
        with self.assertRaises(FrozenInstanceError):a.unit='ssh.service'
        c.reject(a.action_id,peer_uid=12003)
        self.assertEqual(c.status(a.action_id,peer_uid=12003).state,'denied')
        with self.assertRaises(ApprovalError):c.approve(a.action_id,peer_uid=12003)
        self.assertNotIn('key',repr(codec).lower())

    def test_expiry_and_pending_capacity_no_implicit_approval(self):
        clock,_,_,c,s=setup();a=propose(c,s)
        with self.assertRaises(ApprovalError) as e:propose(c,s)
        self.assertEqual(e.exception.code,'PENDING_ACTION')
        clock.ns=a.expires_ns
        with self.assertRaises(ApprovalError) as e:c.approve(a.action_id,peer_uid=12003)
        self.assertEqual(e.exception.code,'EXPIRED')
        self.assertEqual(c.status(a.action_id,peer_uid=12003).state,'expired')
        self.assertNotEqual(propose(c,s).action_id,a.action_id)

    def test_grant_binds_all_fields_and_approved_is_not_executed(self):
        _,p,codec,c,s=setup();a=propose(c,s);grant=c.approve(a.action_id,peer_uid=12003)
        data=codec.verify(grant,peer_uid=p.coordinator_uid)
        for name,value in (('action_id',a.action_id),('session_id',s),('trace_id',a.trace_id),
                           ('environment_id',p.environment_id),('params',{'unit':'nginx.service'}),('approver_uid',12003)):
            self.assertEqual(data[name],value)
        self.assertEqual(c.status(a.action_id,peer_uid=12003).state,'approved')
        self.assertNotIn('executed',data)
        with self.assertRaises(ApprovalError):c.approve(a.action_id,peer_uid=12003)
        self.assertNotIn(grant.wire.decode(),repr(grant));self.assertNotIn(bytes(range(32)).hex(),repr(codec))

    def test_tamper_any_binding_or_mac_is_rejected_without_raw_data(self):
        _,p,codec,c,s=setup();grant=c.approve(propose(c,s).action_id,peer_uid=12003)
        packet=json.loads(grant.wire)
        mutations=dict(action_id='forged',session_id='forged',trace_id='forged',environment_id='forged',
            params={'unit':'ssh.service'},params_sha256='0'*64,policy_version='other',helper_epoch='other',
            operation='shell',approver_uid=12004,expires_ns=2**62,issued_at='2025-01-01T00:00:00Z',version=2)
        for key,value in mutations.items():
            bad={'payload':{**packet['payload'],key:value},'mac':packet['mac']}
            with self.subTest(key=key),self.assertRaises(ApprovalError) as error:
                codec.verify(Grant(json.dumps(bad).encode()),peer_uid=p.coordinator_uid)
            self.assertEqual(str(error.exception),'INVALID_GRANT')
        bad={**packet,'mac':'0'*64}
        with self.assertRaises(ApprovalError):codec.verify(Grant(json.dumps(bad).encode()),peer_uid=p.coordinator_uid)

    def test_malformed_duplicate_oversize_and_caller_identity(self):
        _,p,codec,c,s=setup();grant=c.approve(propose(c,s).action_id,peer_uid=12003)
        for wire in (b'{"payload":{},"payload":{},"mac":"x"}',b'private-canary',b'NaN',b'x'*4097,b'{}'):
            with self.subTest(size=len(wire)),self.assertRaises(ApprovalError) as error:
                codec.verify(Grant(wire),peer_uid=p.coordinator_uid)
            self.assertNotIn('private-canary',str(error.exception))
        for uid in (p.reader_uid,12003,0,True):
            with self.assertRaises(ApprovalError):codec.verify(grant,peer_uid=uid)

    def test_expired_future_or_new_epoch_grants_cannot_be_reused(self):
        clock,p,codec,c,s=setup();grant=c.approve(propose(c,s).action_id,peer_uid=12003)
        new=GrantCodec(p,bytes(range(32)),'synthetic-epoch-2',clock=clock)
        with self.assertRaises(ApprovalError):new.verify(grant,peer_uid=p.coordinator_uid)
        clock.ns=0
        with self.assertRaises(ApprovalError):codec.verify(grant,peer_uid=p.coordinator_uid)
        clock.ns=70*10**9
        with self.assertRaises(ApprovalError) as error:codec.verify(grant,peer_uid=p.coordinator_uid)
        self.assertEqual(error.exception.code,'EXPIRED')

    def test_restart_loses_pending_sessions_instead_of_restoring_approval(self):
        clock,_,codec,c,s=setup();a=propose(c,s)
        fresh=ApprovalCoordinator(codec,clock=clock,utc=clock.utc)
        with self.assertRaises(ApprovalError):fresh.approve(a.action_id,peer_uid=12003)
        with self.assertRaises(ApprovalError):propose(fresh,s)


if __name__=='__main__':unittest.main()
