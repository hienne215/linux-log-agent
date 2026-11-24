"""Explicit administrator reconciliation, synthetic private state only."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from control_plane.approval import ApprovalCoordinator,ApprovalError,GrantCodec,Policy
from control_plane.state import CoordinatorState
from control_plane import reconcile


class ReconcileTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
  self.policy=Policy('linux-log-lab-hien','vm-candidate-v1',12001,12002,frozenset({12003}))
  self.codec=GrantCodec(self.policy,bytes(range(32)),'synthetic-epoch',clock=lambda:10**9)
  self.store=CoordinatorState(self.path,self.codec);self.addCleanup(self.store.close)
  self.core=ApprovalCoordinator(self.codec,clock=lambda:10**9,utc=lambda:datetime(2026,1,1,tzinfo=timezone.utc),store=self.store)
  self.action=self.propose();self.core.approve(self.action.action_id,peer_uid=12003)
  self.approved=replace(self.action,state='approved')

 def propose(self):
  session=self.core.open_session(peer_uid=12003)
  return self.core.propose(peer_uid=12001,session_id=session,trace_id='synthetic-reconcile',unit='nginx.service',evidence_ids=['synthetic-evidence'])

 def proof(self):
  return dict(version=1,action_id=self.action.action_id,action_sha256=reconcile.action_hash(self.action),
   environment_id=self.policy.environment_id,policy_version=self.policy.version,expires_at=self.action.expires_at,
   observed_at='2026-01-02T00:00:00.000000Z',helper_sha256='a'*64,reason='EXPIRED_WITHOUT_HELPER_INTENT')

 def test_any_intent_or_unexpired_nonapproved_wrong_unit_blocks(self):
  empty=dict(attempts=0,audit=0,outcomes=0);now=datetime(2026,1,2,tzinfo=timezone.utc)
  reconcile.eligibility(self.approved,empty,now)
  for table in empty:
   with self.subTest(table=table),self.assertRaises(ApprovalError):reconcile.eligibility(self.approved,empty|{table:1},now)
  for a,t in ((self.action,now),(replace(self.approved,unit='ssh.service'),now),(self.approved,now-timedelta(days=2))):
   with self.assertRaises(ApprovalError):reconcile.eligibility(a,empty,t)

 def test_proof_binds_immutable_action_and_cannot_claim_execution(self):
  proof=self.proof();self.assertTrue(reconcile.valid_proof(proof,self.approved,self.policy))
  for change in ({'version':True},{'action_id':'a'*32},{'helper_sha256':'invalid'},{'executed':True},
    {'reason':'UNLOCK'},{'observed_at':self.action.created_at},{'action_sha256':'b'*64}):
   with self.subTest(change=change):self.assertFalse(reconcile.valid_proof(proof|change,self.approved,self.policy))

 def test_missing_proof_preserves_lock_nonroot_and_invalid_files_denied(self):
  with patch.object(reconcile.os,'open',side_effect=AssertionError('No admin path for unrelated policy')):
   self.assertFalse(reconcile.attested(self.approved,replace(self.policy,environment_id='synthetic-lab')))
  with patch.object(reconcile,'PROOFS',self.path/'missing'):self.assertFalse(reconcile.attested(self.approved,self.policy))
  with patch.object(reconcile.os,'geteuid',return_value=12001):self.assertEqual(reconcile.main([self.action.action_id]),1)
  target=self.path/(self.action.action_id+'.json');target.symlink_to(self.path/'absent')
  with patch.object(reconcile,'PROOFS',self.path),self.assertRaises(OSError):reconcile.attested(self.approved,self.policy)

 def test_reopen_without_root_attestation_stays_locked(self):
  self.store.close()
  with patch.object(reconcile,'attested',return_value=False):
   self.store=CoordinatorState(self.path,self.codec);self.addCleanup(self.store.close)
   self.core=ApprovalCoordinator(self.codec,store=self.store)
   with self.assertRaises(ApprovalError) as e:self.propose()
   self.assertEqual(e.exception.code,'UNIT_LOCKED')

 def test_explicit_attestation_expires_only_old_decision_keeps_audit_no_grant(self):
  self.store.close()
  with patch.object(reconcile,'attested',side_effect=lambda a,p:a.action_id==self.action.action_id):
   self.store=CoordinatorState(self.path,self.codec);self.addCleanup(self.store.close)
   self.core=ApprovalCoordinator(self.codec,store=self.store)
   self.assertEqual(self.core.status(self.action.action_id,peer_uid=12003).state,'expired')
   with self.assertRaises(ApprovalError):self.core.approve(self.action.action_id,peer_uid=12003)
   fresh=self.propose();self.assertNotEqual(fresh.action_id,self.action.action_id)
   self.assertEqual(fresh.state,'pending')
   with sqlite3.connect(self.path/'coordinator.sqlite') as db:
    events=db.execute('select event from audit where action_id=?',(self.action.action_id,)).fetchall()
   self.assertEqual(events,[('proposed',),('approved',),('reconciled_no_intent',)])
   self.store.close()
   self.store=CoordinatorState(self.path,self.codec);self.addCleanup(self.store.close)
  self.store.close()
  # Removing the administrator proof cannot silently accept stored reconciliation.
  with patch.object(reconcile,'attested',return_value=False),self.assertRaises(ApprovalError):CoordinatorState(self.path,self.codec)


if __name__=='__main__':unittest.main()
