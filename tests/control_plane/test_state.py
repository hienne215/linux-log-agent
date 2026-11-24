"""Private temporary SQLite state; no execution or deployment acceptance.

Synthetic roles/keys only. Crash tests kill/exit only a test-owned Python child.
They do not simulate physical power loss or prove storage hardware durability.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from control_plane.approval import ApprovalCoordinator, ApprovalError, GrantCodec, Policy
from control_plane.protocol import ApprovalEndpoint
from control_plane.state import CoordinatorState


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='approval-state-test-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.now = [10**10]
        self.policy = Policy('synthetic-lab', 'policy-1', 12001, 12002, frozenset({12003}))
        self.codec = GrantCodec(self.policy, bytes(range(32)), 'synthetic-epoch', clock=lambda: self.now[0])
        self.make_coordinator()
        self.session = self.c.open_session(peer_uid=12003)
        self.action = self.propose()

    def make_coordinator(self, codec=None):
        self.codec = codec or self.codec
        self.store = CoordinatorState(self.folder, self.codec)
        self.addCleanup(self.store.close)
        self.c = ApprovalCoordinator(self.codec, clock=lambda: self.now[0],
            utc=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc), store=self.store)

    def propose(self, session=None):
        return self.c.propose(peer_uid=12001, session_id=session or self.session,
            trace_id='synthetic-trace', unit='nginx.service', evidence_ids=['synthetic-evidence'])

    def rows(self, sql, parameters=()):
        with sqlite3.connect(self.folder / 'coordinator.sqlite') as db:
            return db.execute(sql, parameters).fetchall()

    def action_record(self, identifier=None):
        return json.loads(self.rows('SELECT record FROM actions WHERE action_id=?',
                                   (identifier or self.action.action_id,))[0][0])

    def assert_no_sign(self, operation, code):
        with patch.object(self.codec, '_sign') as sign, self.assertRaises(ApprovalError) as error:
            operation()
        sign.assert_not_called()
        self.assertEqual(str(error.exception), code)

    def test_approval_and_audit_are_committed_before_grant_is_released(self):
        original = self.codec._sign
        def sign(*args):
            self.assertEqual(self.action_record()['state'], 'approved')
            self.assertEqual(len(self.rows("SELECT 1 FROM audit WHERE event='approved'")), 1)
            return original(*args)
        with patch.object(self.codec, '_sign', side_effect=sign):
            grant = self.c.approve(self.action.action_id, peer_uid=12003)
        data = self.action_record()
        self.assertEqual((data['session_id'], data['trace_id'], data['evidence_ids']),
                         (self.session, 'synthetic-trace', ['synthetic-evidence']))
        self.assertEqual(data['created_at'], self.action.created_at)
        audit = [json.loads(row[0]) for row in self.rows('SELECT record FROM audit')]
        decision = next(event for event in audit if event['event'] == 'approved')
        self.assertEqual((decision['action_id'], decision['peer_uid'], decision['helper_epoch']),
                         (self.action.action_id, 12003, 'synthetic-epoch'))
        raw = (self.folder / 'coordinator.sqlite').read_bytes()
        self.assertNotIn(grant.wire, raw)
        self.assertNotIn(json.loads(grant.wire)['mac'].encode(), raw)
        self.assertNotIn(bytes(range(32)), raw)
        self.assertNotIn('grant', repr(self.store).replace('no grant recovery', ''))

    def test_reopen_expires_pending_preserves_refs_and_invalidates_sessions(self):
        generation = self.store.generation
        self.store.close()
        self.make_coordinator()
        self.assertNotEqual(generation, self.store.generation)
        current = self.c.status(self.action.action_id, peer_uid=12003)
        self.assertEqual(current, replace(self.action, state='expired'))
        self.assert_no_sign(lambda: self.c.approve(self.action.action_id, peer_uid=12003), 'EXPIRED')
        with self.assertRaises(ApprovalError):
            self.propose(self.session)
        self.assertEqual(len(self.rows("SELECT 1 FROM audit WHERE event='expired'")), 1)
        new_session = self.c.open_session(peer_uid=12003)
        self.assertNotEqual(self.propose(new_session).action_id, self.action.action_id)

    def test_approved_decision_remains_locked_after_reopen_and_new_epoch(self):
        self.c.approve(self.action.action_id, peer_uid=12003)
        self.store.close()
        self.now[0] = 1  # new boot's monotonic clock must not revive a decision
        codec = GrantCodec(self.policy, b'x' * 32, 'new-helper-epoch', clock=lambda: self.now[0])
        self.make_coordinator(codec)
        self.assert_no_sign(lambda: self.c.approve(self.action.action_id, peer_uid=12003), 'STATE_FINAL')
        session = self.c.open_session(peer_uid=12003)
        with self.assertRaises(ApprovalError) as error:
            self.propose(session)
        self.assertEqual(error.exception.code, 'UNIT_LOCKED')
        endpoint = ApprovalEndpoint(self.c)
        view = endpoint._view(self.c.status(self.action.action_id, peer_uid=12003))
        self.assertEqual(view['state'], 'approved')
        self.assertIsNone(view['executed'])
        self.assertFalse(view['execution_enabled'])
        self.assertEqual(endpoint._grants, {})

    def test_denied_decision_stays_terminal_and_is_not_restored_as_pending(self):
        self.c.reject(self.action.action_id, peer_uid=12003)
        self.store.close()
        self.make_coordinator()
        self.assertEqual(self.c.status(self.action.action_id, peer_uid=12003).state, 'denied')
        self.assert_no_sign(lambda: self.c.approve(self.action.action_id, peer_uid=12003), 'STATE_FINAL')

    def test_audit_failure_rolls_back_decision_fences_generation_and_never_signs(self):
        with sqlite3.connect(self.folder / 'coordinator.sqlite') as db:
            db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit WHEN NEW.event='approved' "
                       "BEGIN SELECT RAISE(ABORT,'synthetic-private-canary'); END")
        self.assert_no_sign(lambda: self.c.approve(self.action.action_id, peer_uid=12003), 'STATE_UNAVAILABLE')
        self.assertEqual(self.action_record()['state'], 'pending')
        self.assertEqual(self.rows("SELECT 1 FROM audit WHERE event='approved'"), [])
        with self.assertRaises(ApprovalError):
            self.c.status(self.action.action_id, peer_uid=12003)
        self.store.close()
        self.make_coordinator()
        self.assertEqual(self.c.status(self.action.action_id, peer_uid=12003).state, 'expired')

    def test_fsync_failure_after_commit_is_unknown_and_never_reissues_grant(self):
        with patch.object(self.store._database, 'sync', side_effect=OSError('synthetic-private-canary')):
            self.assert_no_sign(lambda: self.c.approve(self.action.action_id, peer_uid=12003), 'STATE_UNAVAILABLE')
        self.assertEqual(self.action_record()['state'], 'approved')
        with self.assertRaises(ApprovalError):
            self.c.approve(self.action.action_id, peer_uid=12003)
        self.store.close()
        self.make_coordinator()
        self.assert_no_sign(lambda: self.c.approve(self.action.action_id, peer_uid=12003), 'STATE_FINAL')

    def test_expiry_after_database_lock_rolls_back_approval_and_persists_expired(self):
        original = self.store._database.connection
        @contextmanager
        def delayed():
            with original() as db:
                class Connection:
                    def execute(inner, statement, *args):
                        result = db.execute(statement, *args)
                        if statement == 'BEGIN IMMEDIATE':
                            self.now[0] = self.action.expires_ns
                        return result
                yield Connection()
        with patch.object(self.store._database, 'connection', delayed):
            self.assert_no_sign(lambda: self.c.approve(self.action.action_id, peer_uid=12003), 'EXPIRED')
        self.assertEqual(self.action_record()['state'], 'expired')
        self.assertEqual(self.rows("SELECT 1 FROM audit WHERE event='approved'"), [])

    def test_expiry_during_fsync_withholds_grant_without_reverting_committed_decision(self):
        original = self.store._database.sync
        def delayed():
            original()
            self.now[0] = self.action.expires_ns
        with patch.object(self.store._database, 'sync', side_effect=delayed):
            self.assert_no_sign(lambda: self.c.approve(self.action.action_id, peer_uid=12003), 'EXPIRED')
        self.assertEqual(self.action_record()['state'], 'approved')
        self.assert_no_sign(lambda: self.c.approve(self.action.action_id, peer_uid=12003), 'STATE_FINAL')

    def test_expiry_during_signing_withholds_the_already_committed_grant(self):
        original = self.codec._sign
        def delayed(*args):
            grant = original(*args)
            self.now[0] = self.action.expires_ns
            return grant
        with patch.object(self.codec, '_sign', side_effect=delayed) as sign:
            with self.assertRaises(ApprovalError) as error:
                self.c.approve(self.action.action_id, peer_uid=12003)
            self.assertEqual(error.exception.code, 'EXPIRED')
            self.assertEqual(sign.call_count, 1)
        self.assertEqual(self.action_record()['state'], 'approved')
        self.assert_no_sign(lambda: self.c.approve(self.action.action_id, peer_uid=12003), 'STATE_FINAL')

    def test_concurrent_confirmation_records_only_one_approval_and_one_grant(self):
        def confirm():
            try:
                self.c.approve(self.action.action_id, peer_uid=12003)
                return 'minted'
            except ApprovalError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: confirm(), range(2)))
        self.assertEqual(results.count('minted'), 1)
        self.assertEqual(results.count('STATE_FINAL'), 1)
        self.assertEqual(len(self.rows("SELECT 1 FROM audit WHERE event='approved'")), 1)

    def test_second_live_owner_or_second_core_cannot_invalidate_first(self):
        generation = self.store.generation
        with self.assertRaises(ApprovalError):
            CoordinatorState(self.folder, self.codec)
        self.assertEqual(self.rows('SELECT generation FROM metadata')[0][0], generation)
        with self.assertRaises(ApprovalError):
            ApprovalCoordinator(self.codec, store=self.store)
        self.c.approve(self.action.action_id, peer_uid=12003)
        self.assertEqual(self.action_record()['state'], 'approved')

    def test_permissions_or_file_replacement_before_approval_fail_closed(self):
        db = self.folder / 'coordinator.sqlite'
        os.chmod(db, 0o644)
        self.assert_no_sign(lambda: self.c.approve(self.action.action_id, peer_uid=12003), 'STATE_UNAVAILABLE')
        self.assertEqual(self.action_record()['state'], 'pending')
        os.chmod(db, 0o600)

    def test_replaced_database_path_is_not_followed_and_no_grant_is_minted(self):
        db = self.folder / 'coordinator.sqlite'
        saved = self.folder / 'saved.sqlite'
        db.rename(saved)
        target = self.folder / 'private-target'
        target.write_bytes(b'synthetic-canary')
        db.symlink_to(target)
        self.assert_no_sign(lambda: self.c.approve(self.action.action_id, peer_uid=12003), 'STATE_UNAVAILABLE')
        self.assertEqual(target.read_bytes(), b'synthetic-canary')

    def test_another_process_cannot_open_or_invalidate_live_state(self):
        generation = self.store.generation
        code = '''
import sys
from control_plane.approval import Policy,GrantCodec,ApprovalError
from control_plane.state import CoordinatorState
p=Policy('synthetic-lab','policy-1',12001,12002,frozenset({12003}))
try:
    state=CoordinatorState(sys.argv[1],GrantCodec(p,bytes(range(32)),'child-epoch'))
except ApprovalError as error:
    print(error.code)
    raise SystemExit(0 if error.code=='STATE_UNAVAILABLE' else 2)
state.close()
raise SystemExit(3)
'''
        result = subprocess.run([sys.executable, '-B', '-c', code, str(self.folder)],
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), 'STATE_UNAVAILABLE')
        self.assertEqual(result.stderr, '')
        self.assertEqual(self.rows('SELECT generation FROM metadata')[0][0], generation)
        self.c.approve(self.action.action_id, peer_uid=12003)

    def test_changed_policy_or_schema_is_rejected_without_migration(self):
        self.store.close()
        old = self.rows('SELECT policy_hash,generation FROM metadata')
        other = GrantCodec(replace(self.policy, ttl_seconds=30), bytes(range(32)), 'synthetic-epoch')
        with self.assertRaises(ApprovalError):
            CoordinatorState(self.folder, other)
        self.assertEqual(self.rows('SELECT policy_hash,generation FROM metadata'), old)
        with sqlite3.connect(self.folder / 'coordinator.sqlite') as db:
            db.execute('PRAGMA user_version=999')
        with self.assertRaises(ApprovalError):
            CoordinatorState(self.folder, self.codec)
        self.assertEqual(self.rows('PRAGMA user_version'), [(999,)])

    def test_invalid_persisted_action_is_not_loaded_as_trusted_state(self):
        self.store.close()
        value = self.action_record()
        value['evidence_ids'] = ['forged', 'forged']
        with sqlite3.connect(self.folder / 'coordinator.sqlite') as db:
            db.execute('UPDATE actions SET record=?', (json.dumps(value),))
        with self.assertRaises(ApprovalError) as error:
            CoordinatorState(self.folder, self.codec)
        self.assertEqual(str(error.exception), 'STATE_UNAVAILABLE')

    def test_missing_approval_audit_cannot_be_loaded_as_verified_decision(self):
        self.c.approve(self.action.action_id, peer_uid=12003)
        self.store.close()
        with sqlite3.connect(self.folder / 'coordinator.sqlite') as db:
            db.execute("DELETE FROM audit WHERE event='approved'")
        with self.assertRaises(ApprovalError) as error:
            CoordinatorState(self.folder, self.codec)
        self.assertEqual(str(error.exception), 'STATE_UNAVAILABLE')

    def test_well_formed_but_changed_evidence_cannot_diverge_from_audit(self):
        self.store.close()
        value = self.action_record()
        value['evidence_ids'] = ['different-valid-id']
        with sqlite3.connect(self.folder / 'coordinator.sqlite') as db:
            db.execute('UPDATE actions SET record=?', (json.dumps(value),))
        with self.assertRaises(ApprovalError) as error:
            CoordinatorState(self.folder, self.codec)
        self.assertEqual(str(error.exception), 'STATE_UNAVAILABLE')

    def test_event_capacity_does_not_delete_history_or_create_approval(self):
        count = self.rows('SELECT COUNT(*) FROM audit')[0][0]
        with patch.object(self.store, 'MAX_EVENTS', count):
            self.assert_no_sign(lambda: self.c.approve(self.action.action_id, peer_uid=12003), 'CAPACITY')
            sessions = dict(self.c._sessions)
            with self.assertRaises(ApprovalError):
                self.c.open_session(peer_uid=12003)
            self.assertEqual(self.c._sessions, sessions)
        self.assertEqual(self.rows('SELECT COUNT(*) FROM audit')[0][0], count)
        self.assertEqual(self.action_record()['state'], 'pending')

    def test_store_closed_or_wrong_codec_cannot_fall_back_to_memory(self):
        other = GrantCodec(self.policy, b'x' * 32, 'other-epoch')
        with self.assertRaises(ApprovalError):
            ApprovalCoordinator(other, store=self.store)
        self.store.close()
        self.assert_no_sign(lambda: self.c.approve(self.action.action_id, peer_uid=12003), 'STATE_UNAVAILABLE')
        with self.assertRaises(ApprovalError):
            self.c.open_session(peer_uid=12003)

    def test_expiry_read_is_audited_once_and_reader_needs_matching_session(self):
        self.now[0] = self.action.expires_ns
        with self.assertRaises(ApprovalError):
            self.c.status(self.action.action_id, peer_uid=12001, session_id='wrong')
        self.assertEqual(self.action_record()['state'], 'pending')
        for _ in range(2):
            self.assertEqual(self.c.status(self.action.action_id, peer_uid=12001,
                session_id=self.session).state, 'expired')
        self.assertEqual(len(self.rows("SELECT 1 FROM audit WHERE event='expired'")), 1)

    def test_process_crash_after_approval_retains_terminal_decision_without_grant_recovery(self):
        self.store.close()
        code = '''
import json,sys,time
from pathlib import Path
from control_plane.approval import Policy,GrantCodec,ApprovalCoordinator
from control_plane.state import CoordinatorState
import os
p=Policy('synthetic-lab','policy-1',12001,12002,frozenset({12003}))
codec=GrantCodec(p,bytes(range(32)),'child-epoch')
store=CoordinatorState(sys.argv[1],codec)
c=ApprovalCoordinator(codec,store=store)
s=c.open_session(peer_uid=12003)
a=c.propose(peer_uid=12001,session_id=s,trace_id='crash-trace',unit='nginx.service',evidence_ids=['synthetic-evidence'])
c.approve(a.action_id,peer_uid=12003)
Path(sys.argv[1],'crash.json').write_text(json.dumps({'action_id':a.action_id,'pid':os.getpid()}))
os._exit(37)
'''
        child = subprocess.Popen([sys.executable, '-B', '-c', code, str(self.folder)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            _, stderr = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 37, stderr.decode(errors='replace'))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=2)
        with self.assertRaises(ProcessLookupError):
            os.kill(child.pid, 0)
        identifier = json.loads((self.folder / 'crash.json').read_text())['action_id']
        self.make_coordinator()
        self.assertEqual(self.c.status(identifier, peer_uid=12003).state, 'approved')
        self.assert_no_sign(lambda: self.c.approve(identifier, peer_uid=12003), 'STATE_FINAL')
        self.assertEqual(len(self.rows("SELECT 1 FROM audit WHERE event='approved' AND action_id=?", (identifier,))), 1)


if __name__ == '__main__':
    unittest.main()
