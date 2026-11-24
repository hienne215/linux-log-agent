"""Durable coordinator decisions/audit, with no grant recovery or executor.

One live owner per private file. Pending sessions never survive a new service
generation. Approved decisions stay terminal/locked; execution remains unknown.
Keys, full grants, raw reasons and log text are never stored here.
"""
from dataclasses import asdict, fields, replace
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import uuid4

from .approval import Action, ApprovalError, GrantCodec, _id, _json, _uid, _unique
from .storage import PrivateDatabase


class CoordinatorState:
    MAX_ACTIONS = 128
    MAX_EVENTS = 8192
    APPLICATION_ID = 0x4C4C4143
    AUDIT_FIELDS = frozenset(('version', 'event', 'environment_id', 'policy_version',
        'helper_epoch', 'generation', 'peer_uid', 'occurred_at', 'session_id',
        'trace_id', 'action_id', 'unit', 'state', 'params_sha256', 'action_sha256'))

    def __init__(self, directory, codec):
        self._database = None
        self.failed = False
        self._attached = False
        if type(codec) is not GrantCodec:
            raise ApprovalError('INVALID_POLICY')
        self.codec = codec
        self.generation = uuid4().hex
        policy = codec.policy
        config = dict(environment_id=policy.environment_id, version=policy.version,
            reader_uid=policy.reader_uid, coordinator_uid=policy.coordinator_uid,
            operator_uids=sorted(policy.operator_uids), ttl_seconds=policy.ttl_seconds)
        self._policy_hash = hashlib.sha256(_json(config)).hexdigest()
        try:
            self._database = PrivateDatabase(directory, 'coordinator.sqlite', exclusive=True)
            with self._database.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                tables = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if not tables:
                    db.execute('CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), '
                        'policy_hash TEXT NOT NULL, generation TEXT NOT NULL, events INTEGER NOT NULL)')
                    db.execute('CREATE TABLE actions (action_id TEXT PRIMARY KEY, record TEXT NOT NULL)')
                    db.execute('CREATE TABLE audit (sequence INTEGER PRIMARY KEY, event TEXT NOT NULL, '
                        'generation TEXT NOT NULL, action_id TEXT, record TEXT NOT NULL)')
                    db.execute('PRAGMA user_version=1')
                    db.execute('PRAGMA application_id=' + str(self.APPLICATION_ID))
                    db.execute('INSERT INTO metadata VALUES (1,?,?,0)', (self._policy_hash, self.generation))
                    previous_generation = None
                    expected_events = 0
                else:
                    if (tables != {'metadata', 'actions', 'audit'}
                            or db.execute('PRAGMA user_version').fetchone()[0] != 1
                            or db.execute('PRAGMA application_id').fetchone()[0] != self.APPLICATION_ID
                            or db.execute('PRAGMA quick_check').fetchall() != [('ok',)]):
                        raise ValueError()
                    metadata = db.execute('SELECT id,policy_hash,generation,events FROM metadata').fetchall()
                    if (len(metadata) != 1 or metadata[0][0] != 1 or metadata[0][1] != self._policy_hash
                            or re.fullmatch('[0-9a-f]{32}', metadata[0][2]) is None):
                        raise ValueError()
                    previous_generation, expected_events = metadata[0][2:]
                records = db.execute('SELECT action_id,record FROM actions LIMIT 129').fetchall()
                if len(records) > self.MAX_ACTIONS:
                    raise ValueError()
                self._actions = {}
                for identifier, wire in records:
                    action = self._decode(wire)
                    if identifier != action.action_id:
                        raise ValueError()
                    self._actions[identifier] = action
                self._check_audit(db, previous_generation, expected_events)
                db.execute('UPDATE metadata SET generation=? WHERE id=1', (self.generation,))
                self._event(db, 'generation_started', peer_uid=policy.coordinator_uid)
                for identifier, action in list(self._actions.items()):
                    if action.state == 'approved':
                        from .reconcile import attested
                        if attested(action,policy):
                            expired=replace(action,state='expired')
                            self._write(db,expired,'reconciled_no_intent',0)
                            self._actions[identifier]=expired
                    elif action.state == 'pending':
                        expired = replace(action, state='expired')
                        self._write(db, expired, 'expired', policy.coordinator_uid)
                        self._actions[identifier] = expired
                db.execute('COMMIT')
            self._database.sync()
        except Exception:
            self.close()
            raise ApprovalError('STATE_UNAVAILABLE') from None

    def __repr__(self):
        return '<CoordinatorState private; no grant recovery>'

    def close(self):
        self.failed = True
        if self._database is not None:
            self._database.close()
            self._database = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def attach(self, codec):
        if self.failed or self._attached or codec is not self.codec:
            raise ApprovalError('STATE_UNAVAILABLE')
        self._attached = True
        return dict(self._actions)

    def _validate(self, action):
        if type(action) is not Action:
            raise ValueError()
        p = self.codec.policy
        for value in (action.action_id, action.session_id):
            if type(value) is not str or re.fullmatch('[0-9a-f]{32}', value) is None:
                raise ValueError()
        if (not _id(action.trace_id) or action.environment_id != p.environment_id
                or action.unit != 'nginx.service' or not _uid(action.owner_uid)
                or action.owner_uid not in p.operator_uids or type(action.evidence_ids) is not tuple
                or not 1 <= len(action.evidence_ids) <= 32
                or not all(_id(ref) for ref in action.evidence_ids)
                or len(set(action.evidence_ids)) != len(action.evidence_ids)
                or type(action.created_ns) is not int or type(action.expires_ns) is not int
                or not 0 <= action.created_ns < action.expires_ns < 2**63
                or action.expires_ns - action.created_ns > p.ttl_seconds * 10**9
                or action.state not in ('pending', 'approved', 'denied', 'expired')):
            raise ValueError()
        for value in (action.created_at, action.expires_at):
            if (type(value) is not str
                    or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z', value) is None):
                raise ValueError()
            datetime.fromisoformat(value.replace('Z', '+00:00'))

    def _decode(self, wire):
        if type(wire) is not str or len(wire.encode('utf-8')) > 16384:
            raise ValueError()
        value = json.loads(wire, object_pairs_hook=_unique)
        if type(value) is not dict or set(value) != {f.name for f in fields(Action)}:
            raise ValueError()
        if type(value['evidence_ids']) is not list:
            raise ValueError()
        value['evidence_ids'] = tuple(value['evidence_ids'])
        action = Action(**value)
        self._validate(action)
        return action

    @staticmethod
    def _action_hash(action):
        record = asdict(action)
        del record['state']
        return hashlib.sha256(_json(record)).hexdigest()

    def _check_audit(self, db, previous_generation, expected_events):
        """Check local consistency, not authenticity against same-UID/root edits.

        A self-stored hash is not reviewer approval, ground truth or a tamper-
        proof audit. It catches an accidental state/reference/history mismatch.
        """
        rows = db.execute('SELECT sequence,event,generation,action_id,record FROM audit '
                          'ORDER BY sequence LIMIT 8193').fetchall()
        if (type(expected_events) is not int or len(rows) != expected_events
                or len(rows) > self.MAX_EVENTS):
            raise ValueError()
        p = self.codec.policy
        latest = None
        generations, sessions, states = {}, {}, {}
        for index, (sequence, event, generation, identifier, wire) in enumerate(rows, 1):
            if sequence != index or type(wire) is not str or len(wire.encode()) > 4096:
                raise ValueError()
            item = json.loads(wire, object_pairs_hook=_unique)
            if (type(item) is not dict or set(item) != self.AUDIT_FIELDS
                    or type(item['version']) is not int or item['version'] != 1
                    or (item['event'], item['generation'], item['action_id']) != (event, generation, identifier)
                    or item['environment_id'] != p.environment_id or item['policy_version'] != p.version
                    or type(generation) is not str or re.fullmatch('[a-f0-9]{32}', generation) is None
                    or not _id(item['helper_epoch']) or not _uid(item['peer_uid'])
                    or type(item['occurred_at']) is not str
                    or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z', item['occurred_at']) is None):
                raise ValueError()
            datetime.fromisoformat(item['occurred_at'].replace('Z', '+00:00'))
            if event == 'generation_started':
                if generation in generations or item['peer_uid'] != p.coordinator_uid:
                    raise ValueError()
                generations[generation] = item['helper_epoch']
                latest = generation
            if generation != latest or item['helper_epoch'] != generations[latest]:
                raise ValueError()
            if event in ('generation_started', 'session_opened'):
                if any(item[name] is not None for name in
                       ('action_id', 'trace_id', 'unit', 'state', 'params_sha256', 'action_sha256')):
                    raise ValueError()
                if event == 'generation_started':
                    if item['session_id'] is not None:
                        raise ValueError()
                else:
                    session = item['session_id']
                    if (type(session) is not str or re.fullmatch('[a-f0-9]{32}', session) is None
                            or item['peer_uid'] not in p.operator_uids or (generation, session) in sessions):
                        raise ValueError()
                    sessions[generation, session] = item['peer_uid']
                continue
            action = self._actions.get(identifier)
            target = {'proposed': 'pending', 'approved': 'approved', 'denied': 'denied', 'expired': 'expired',
                      'reconciled_no_intent':'expired'}.get(event)
            if (action is None or target is None or item['state'] != target
                    or (item['session_id'], item['trace_id'], item['unit']) !=
                       (action.session_id, action.trace_id, action.unit)
                    or item['params_sha256'] != hashlib.sha256(_json({'unit': action.unit})).hexdigest()
                    or item['action_sha256'] != self._action_hash(action)):
                raise ValueError()
            allowed = {0} if event=='reconciled_no_intent' else {p.reader_uid} if event == 'proposed' else {action.owner_uid} if event in ('approved', 'denied') else {action.owner_uid, p.reader_uid, p.coordinator_uid}
            if item['peer_uid'] not in allowed:
                raise ValueError()
            if event == 'proposed':
                if identifier in states or sessions.get((generation, action.session_id)) != action.owner_uid:
                    raise ValueError()
            elif event=='reconciled_no_intent':
                from .reconcile import attested
                if states.get(identifier)!='approved' or not attested(action,p):raise ValueError()
            elif states.get(identifier) != 'pending':
                raise ValueError()
            states[identifier] = target
        if latest != previous_generation or states != {key: action.state for key, action in self._actions.items()}:
            raise ValueError()

    def _event(self, db, event, *, peer_uid, action=None, session_id=None):
        if db.execute('SELECT COUNT(*) FROM audit').fetchone()[0] >= self.MAX_EVENTS:
            raise ApprovalError('CAPACITY')
        p = self.codec.policy
        record = dict(version=1, event=event, environment_id=p.environment_id,
            policy_version=p.version, helper_epoch=self.codec.epoch, generation=self.generation,
            peer_uid=peer_uid, occurred_at=datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z'),
            session_id=action.session_id if action else session_id,
            trace_id=action.trace_id if action else None,
            action_id=action.action_id if action else None,
            unit=action.unit if action else None, state=action.state if action else None,
            params_sha256=hashlib.sha256(_json({'unit': action.unit})).hexdigest() if action else None,
            action_sha256=self._action_hash(action) if action else None)
        db.execute('INSERT INTO audit(event,generation,action_id,record) VALUES (?,?,?,?)',
            (event, self.generation, record['action_id'], _json(record).decode('ascii')))
        db.execute('UPDATE metadata SET events=events+1 WHERE id=1')

    def _write(self, db, action, event, peer_uid):
        self._validate(action)
        expected = {'proposed': 'pending', 'approved': 'approved', 'denied': 'denied', 'expired': 'expired',
                    'reconciled_no_intent':'expired'}
        if event not in expected or action.state != expected[event]:
            raise ValueError()
        p = self.codec.policy
        permitted = {0} if event=='reconciled_no_intent' else {p.reader_uid} if event == 'proposed' else {action.owner_uid} if event in ('approved', 'denied') else {action.owner_uid, p.reader_uid, p.coordinator_uid}
        if not _uid(peer_uid) or peer_uid not in permitted:
            raise ApprovalError('CALLER_DENIED')
        previous = db.execute('SELECT record FROM actions WHERE action_id=?', (action.action_id,)).fetchone()
        if previous is None:
            if event != 'proposed' or db.execute('SELECT COUNT(*) FROM actions').fetchone()[0] >= self.MAX_ACTIONS:
                raise ApprovalError('CAPACITY')
            db.execute('INSERT INTO actions VALUES (?,?)', (action.action_id, _json(asdict(action)).decode('ascii')))
        else:
            old = self._decode(previous[0])
            if (event == 'proposed' or old.state != ('approved' if event=='reconciled_no_intent' else 'pending')
                    or replace(old, state=action.state) != action):
                raise ApprovalError('STATE_FINAL')
            if event=='reconciled_no_intent':
                from .reconcile import attested
                if not attested(old,p):raise ApprovalError('STATE_UNAVAILABLE')
            db.execute('UPDATE actions SET record=? WHERE action_id=?', (_json(asdict(action)).decode('ascii'), action.action_id))
        self._event(db, event, peer_uid=peer_uid, action=action)

    def _transaction(self, operation, *, before_commit=None):
        if self.failed or self._database is None:
            raise ApprovalError('STATE_UNAVAILABLE')
        try:
            with self._database.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                operation(db)
                if before_commit is not None:
                    before_commit()  # recheck TTL after acquiring the database lock
                db.execute('COMMIT')
            self._database.sync()
        except ApprovalError as error:
            if error.code not in ('EXPIRED', 'CAPACITY', 'CALLER_DENIED'):
                self.failed = True
            raise
        except Exception:
            # COMMIT/fsync failure can be ambiguous. Fence this generation;
            # never fall back to in-memory approval or attempt an automatic retry.
            self.failed = True
            raise ApprovalError('STATE_UNAVAILABLE') from None

    def save(self, action, event, *, peer_uid, before_commit=None):
        self._transaction(lambda db: self._write(db, action, event, peer_uid), before_commit=before_commit)
        self._actions[action.action_id] = action

    def session_opened(self, session_id, *, peer_uid):
        if (type(session_id) is not str or re.fullmatch('[0-9a-f]{32}', session_id) is None
                or not _uid(peer_uid) or peer_uid not in self.codec.policy.operator_uids):
            raise ApprovalError('CALLER_DENIED')
        self._transaction(lambda db: self._event(db, 'session_opened', peer_uid=peer_uid, session_id=session_id))
