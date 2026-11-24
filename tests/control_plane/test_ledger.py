"""Offline durable intent tests. Files created only in test-owned temp folders."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from control_plane.approval import ApprovalCoordinator,ApprovalError,Grant,GrantCodec,Policy
from control_plane.ledger import HelperLedger


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='approval-ledger-test-')
        self.addCleanup(self.temp.cleanup);self.folder=Path(self.temp.name)
        self.now=[10**10];clock=lambda:self.now[0]
        self.policy=Policy('synthetic-lab','policy-1',12001,12002,frozenset({12003}))
        self.codec=GrantCodec(self.policy,bytes(range(32)),'synthetic-epoch-1',clock=clock)
        self.c=ApprovalCoordinator(self.codec,clock=clock,
            utc=lambda:datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(microseconds=self.now[0]//1000))
        self.session=self.c.open_session(peer_uid=12003)
        self.action,self.grant=self.make_grant()

    def make_grant(self):
        action=self.c.propose(peer_uid=12001,session_id=self.session,trace_id='synthetic-trace',
            unit='nginx.service',evidence_ids=['synthetic-evidence'])
        return action,self.c.approve(action.action_id,peer_uid=12003)

    def count(self,table):
        self.assertIn(table,('attempts','audit'))
        with sqlite3.connect(self.folder/'ledger.sqlite') as db:return db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]

    def test_consumed_intent_persists_unknown_and_replay_cannot_be_readmitted(self):
        with HelperLedger(self.folder,self.codec) as ledger:
            result=ledger.consume(self.grant,peer_uid=12002)
            self.assertEqual(result.outcome,'execution_unknown');self.assertIsNone(result.executed)
        with HelperLedger(self.folder,self.codec) as reopened:
            self.assertEqual(reopened.status(self.action.action_id),result)
            with self.assertRaises(ApprovalError) as e:reopened.consume(self.grant,peer_uid=12002)
            self.assertEqual(e.exception.code,'REPLAY')
        self.assertEqual(self.count('attempts'),1);self.assertEqual(self.count('audit'),1)
        self.assertEqual((self.folder/'ledger.sqlite').stat().st_mode&0o777,0o600)
        raw=(self.folder/'ledger.sqlite').read_bytes()
        self.assertNotIn(json.loads(self.grant.wire)['mac'].encode(),raw)
        self.assertNotIn(bytes(range(32)),raw)
        with sqlite3.connect(self.folder/'ledger.sqlite') as db:
            row=db.execute('SELECT session_id,trace_id FROM attempts').fetchone()
            self.assertEqual(row,(self.session,'synthetic-trace'))

    def test_bad_signature_wrong_caller_and_expiry_never_consume(self):
        with HelperLedger(self.folder,self.codec) as ledger:
            for token,peer in ((Grant(b'private-canary'),12002),(self.grant,12001)):
                with self.assertRaises(ApprovalError):ledger.consume(token,peer_uid=peer)
            self.now[0]=self.action.expires_ns
            with self.assertRaises(ApprovalError) as e:ledger.consume(self.grant,peer_uid=12002)
            self.assertEqual(e.exception.code,'EXPIRED')
            self.assertEqual(self.count('attempts'),0);self.assertEqual(self.count('audit'),0)

    def test_expiry_is_rechecked_after_transaction_lock(self):
        with HelperLedger(self.folder,self.codec) as ledger:
            original=ledger._connection
            @contextmanager
            def delayed():
                with original() as db:
                    class Connection:
                        def execute(inner,sql,*args):
                            result=db.execute(sql,*args)
                            if sql=='BEGIN IMMEDIATE':self.now[0]=self.action.expires_ns
                            return result
                    yield Connection()
            with patch.object(ledger,'_connection',delayed),self.assertRaises(ApprovalError) as e:
                ledger.consume(self.grant,peer_uid=12002)
            self.assertEqual(e.exception.code,'EXPIRED');self.assertEqual(self.count('attempts'),0)

    def test_unresolved_unit_stays_locked_even_with_new_action_and_epoch(self):
        with HelperLedger(self.folder,self.codec) as ledger:ledger.consume(self.grant,peer_uid=12002)
        self.now[0]=self.action.expires_ns
        codec=GrantCodec(self.policy,bytes(range(32)),'synthetic-epoch-2',clock=lambda:self.now[0])
        c=ApprovalCoordinator(codec,clock=lambda:self.now[0],utc=self.c.utc)
        session=c.open_session(peer_uid=12003)
        other=c.propose(peer_uid=12001,session_id=session,trace_id='new-trace',unit='nginx.service',evidence_ids=['synthetic-evidence'])
        grant=c.approve(other.action_id,peer_uid=12003)
        with HelperLedger(self.folder,codec) as reopened:
            with self.assertRaises(ApprovalError) as e:reopened.consume(grant,peer_uid=12002)
            self.assertEqual(e.exception.code,'UNIT_LOCKED')
            self.assertEqual(reopened.status(self.action.action_id).outcome,'execution_unknown')
            self.assertIsNone(reopened.status(other.action_id))

    def test_concurrent_replay_has_at_most_one_durable_intent(self):
        barrier=threading.Barrier(2)
        with HelperLedger(self.folder,self.codec) as ledger:
            def attempt():
                barrier.wait()
                try:ledger.consume(self.grant,peer_uid=12002);return 'admitted'
                except ApprovalError as error:return error.code
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:attempt(),range(2)))
        self.assertEqual(results.count('admitted'),1)
        self.assertTrue(set(results)<={'admitted','REPLAY','STATE_UNAVAILABLE'})
        self.assertEqual(self.count('attempts'),1);self.assertEqual(self.count('audit'),1)

    def test_audit_failure_rolls_back_intent_and_withholds_raw_exception(self):
        with HelperLedger(self.folder,self.codec) as ledger:
            with sqlite3.connect(self.folder/'ledger.sqlite') as db:
                db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'private-canary'); END")
            with self.assertRaises(ApprovalError) as e:ledger.consume(self.grant,peer_uid=12002)
            self.assertEqual(str(e.exception),'STATE_UNAVAILABLE')
            self.assertEqual(self.count('attempts'),0);self.assertEqual(self.count('audit'),0)

    def test_unsafe_directory_symlink_database_and_hardlink_are_denied(self):
        link=self.folder/'linked-directory';link.symlink_to(self.folder,target_is_directory=True)
        with self.assertRaises(ApprovalError):HelperLedger(link,self.codec)
        db=self.folder/'ledger.sqlite';target=self.folder/'target';target.write_text('synthetic')
        db.symlink_to(target)
        with self.assertRaises(ApprovalError):HelperLedger(self.folder,self.codec)
        db.unlink()  # This test owns both temporary symlink and target.
        os.chmod(target,0o600);os.link(target,db)
        with self.assertRaises(ApprovalError):HelperLedger(self.folder,self.codec)

    def test_permissions_corruption_and_closed_owner_fail_closed(self):
        with HelperLedger(self.folder,self.codec) as ledger:
            descriptor=ledger._directory
        with self.assertRaises(OSError):os.fstat(descriptor)
        with self.assertRaises(ApprovalError):ledger.consume(self.grant,peer_uid=12002)
        db=self.folder/'ledger.sqlite';os.chmod(db,0o644)
        with self.assertRaises(ApprovalError):HelperLedger(self.folder,self.codec)
        os.chmod(db,0o600);db.write_text('private-corruption-canary')
        with self.assertRaises(ApprovalError) as e:HelperLedger(self.folder,self.codec)
        self.assertEqual(str(e.exception),'STATE_UNAVAILABLE')


if __name__=='__main__':unittest.main()
