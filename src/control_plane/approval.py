"""Internal approval bindings; no MCP wire changes, sockets, shell or executor.

peer_uid must come from a trusted Unix transport's kernel credentials, never a
request field. This core alone cannot prove a human used a TTY. No Agent entry
point imports it; deployment requires separate identities, control transport,
protected keys/state and independent security review.
"""
from dataclasses import dataclass,field,replace
from datetime import datetime,timedelta,timezone
from functools import wraps
import hashlib
import hmac
import json
import re
import time
from threading import RLock
from uuid import uuid4


class ApprovalError(Exception):
    def __init__(self,code):self.code=code;super().__init__(code)


def _id(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',value) is not None


def _uid(value):return type(value) is int and 0<=value<2**31


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


def _unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError()
        result[key]=value
    return result


def _utc():return datetime.now(timezone.utc)


def _stamp(value):return value.isoformat(timespec='microseconds').replace('+00:00','Z')


def _serialized(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        with self._lock:
            if not self._healthy or (self._store is not None and self._store.failed):
                raise ApprovalError('STATE_UNAVAILABLE')
            return method(self,*args,**kwargs)
    return call


@dataclass(frozen=True)
class Policy:
    environment_id:str
    version:str
    reader_uid:int
    coordinator_uid:int
    operator_uids:frozenset[int]
    ttl_seconds:int=60

    def __post_init__(self):
        if (not _id(self.environment_id) or not _id(self.version)
                or not _uid(self.reader_uid) or self.reader_uid==0
                or not _uid(self.coordinator_uid) or self.coordinator_uid==0
                or self.reader_uid==self.coordinator_uid
                or type(self.operator_uids) is not frozenset or not self.operator_uids
                or not all(_uid(uid) and uid not in (0,self.reader_uid,self.coordinator_uid) for uid in self.operator_uids)
                or type(self.ttl_seconds) is not int or not 1<=self.ttl_seconds<=60):
            raise ApprovalError('INVALID_POLICY')


@dataclass(frozen=True)
class Grant:
    wire:bytes=field(repr=False)

    def __repr__(self):return '<Grant private; do not log or send to Agent/CLI>'


@dataclass(frozen=True)
class Action:
    action_id:str
    session_id:str
    trace_id:str
    environment_id:str
    unit:str
    evidence_ids:tuple[str,...]
    owner_uid:int
    created_ns:int
    expires_ns:int
    created_at:str
    expires_at:str
    state:str='pending'


class GrantCodec:
    """Private coordinator/helper codec. Epoch must change at helper startup.

    UTC timestamps are audit fields; monotonic deadlines decide admission on the
    same Linux host. A key's length is checked, not its entropy or OS protection.
    """
    FIELDS=frozenset(('version','action_id','session_id','trace_id','environment_id',
        'operation','params','params_sha256','policy_version','helper_epoch',
        'approver_uid','issued_ns','expires_ns','issued_at','expires_at'))

    def __init__(self,policy,key,helper_epoch,*,clock=time.monotonic_ns):
        if type(policy) is not Policy or type(key) is not bytes or len(key)!=32 or not _id(helper_epoch):
            raise ApprovalError('INVALID_POLICY')
        self.policy,self._key,self.epoch,self.clock=policy,key,helper_epoch,clock

    def __repr__(self):return '<GrantCodec private>'

    def _sign(self,action,approver_uid,issued_ns,issued_at):
        params={'unit':action.unit}
        payload=dict(version=1,action_id=action.action_id,session_id=action.session_id,
            trace_id=action.trace_id,environment_id=action.environment_id,operation='restart_service',
            params=params,params_sha256=hashlib.sha256(_json(params)).hexdigest(),
            policy_version=self.policy.version,helper_epoch=self.epoch,approver_uid=approver_uid,
            issued_ns=issued_ns,expires_ns=action.expires_ns,issued_at=issued_at,expires_at=action.expires_at)
        mac=hmac.new(self._key,_json(payload),hashlib.sha256).hexdigest()
        return Grant(_json({'payload':payload,'mac':mac}))

    def verify(self,grant,*,peer_uid):
        if not _uid(peer_uid) or peer_uid!=self.policy.coordinator_uid:raise ApprovalError('CALLER_DENIED')
        try:
            if type(grant) is not Grant or type(grant.wire) is not bytes or len(grant.wire)>4096:raise ValueError()
            packet=json.loads(grant.wire.decode('ascii'),object_pairs_hook=_unique,
                parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            if type(packet) is not dict or set(packet)!={'payload','mac'}:raise ValueError()
            payload,mac=packet['payload'],packet['mac']
            if type(payload) is not dict or set(payload)!=self.FIELDS or type(mac) is not str or re.fullmatch('[0-9a-f]{64}',mac) is None:
                raise ValueError()
            expected=hmac.new(self._key,_json(payload),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(mac,expected):raise ValueError()
            if type(payload['version']) is not int or payload['version']!=1:raise ValueError()
            for name in ('action_id','session_id','trace_id','environment_id','policy_version','helper_epoch'):
                if not _id(payload[name]):raise ValueError()
            if (payload['environment_id']!=self.policy.environment_id or payload['policy_version']!=self.policy.version
                    or payload['helper_epoch']!=self.epoch or payload['operation']!='restart_service'
                    or payload['params']!={'unit':'nginx.service'}):raise ValueError()
            digest=hashlib.sha256(_json(payload['params'])).hexdigest()
            if payload['params_sha256']!=digest:raise ValueError()
            if not _uid(payload['approver_uid']) or payload['approver_uid'] not in self.policy.operator_uids:raise ValueError()
            start,end=payload['issued_ns'],payload['expires_ns']
            if (type(start) is not int or type(end) is not int or not 0<=start<end<2**63
                    or end-start>self.policy.ttl_seconds*10**9):raise ValueError()
            for name in ('issued_at','expires_at'):
                value=payload[name]
                if type(value) is not str or not value.endswith('Z'):raise ValueError()
                datetime.fromisoformat(value.replace('Z','+00:00'))
            # Wall time may jump. It is bound audit metadata, not the TTL clock.
            now=self.clock()
            if now<start:raise ValueError()
            if now>=end:raise ApprovalError('EXPIRED')
            return payload
        except ApprovalError:raise
        except Exception:raise ApprovalError('INVALID_GRANT') from None


class ApprovalCoordinator:
    """Bounded pending policy, optionally with durable decision/audit storage.

    A trusted control service must authenticate operator UID and TTY workflow
    before approve(). No boolean or token in an MCP request can call this API.
    Sessions and grants never recover. With a store, pending actions expire on
    restart and approved actions stay terminal/locked; execution remains unknown.
    The default in-memory mode remains an offline test candidate.
    """
    def __init__(self,codec,*,clock=time.monotonic_ns,utc=_utc,store=None):
        if type(codec) is not GrantCodec:raise ApprovalError('INVALID_POLICY')
        if store is not None:
            from .state import CoordinatorState
            if type(store) is not CoordinatorState:raise ApprovalError('INVALID_POLICY')
        self.codec,self.policy,self.clock,self.utc=codec,codec.policy,clock,utc
        self._lock=RLock()
        self._store=store;self._healthy=True
        self._sessions={};self._actions=store.attach(codec) if store is not None else {}

    def _save(self,action,event,peer_uid,*,before_commit=None):
        if self._store is not None:
            try:self._store.save(action,event,peer_uid=peer_uid,before_commit=before_commit)
            except ApprovalError as error:
                if error.code not in ('EXPIRED','CAPACITY','CALLER_DENIED'):self._healthy=False
                raise
        self._actions[action.action_id]=action

    @_serialized
    def open_session(self,*,peer_uid):
        if not _uid(peer_uid) or peer_uid not in self.policy.operator_uids:raise ApprovalError('CALLER_DENIED')
        if len(self._sessions)>=64:raise ApprovalError('CAPACITY')
        identifier=uuid4().hex
        if self._store is not None:
            self._store.session_opened(identifier,peer_uid=peer_uid)
        self._sessions[identifier]=peer_uid
        return identifier

    @_serialized
    def propose(self,*,peer_uid,session_id,trace_id,unit,evidence_ids):
        if not _uid(peer_uid) or peer_uid!=self.policy.reader_uid:raise ApprovalError('CALLER_DENIED')
        if (not _id(session_id) or session_id not in self._sessions or not _id(trace_id)
                or unit!='nginx.service' or type(evidence_ids) not in (list,tuple)
                or not 1<=len(evidence_ids)<=32 or not all(_id(ref) for ref in evidence_ids)
                or len(set(evidence_ids))!=len(evidence_ids)):raise ApprovalError('INVALID_PROPOSAL')
        now=self.clock()
        if len(self._actions)>=128:raise ApprovalError('CAPACITY')
        if any(a.unit==unit and a.state=='approved' for a in self._actions.values()):
            raise ApprovalError('UNIT_LOCKED')
        if any(a.unit==unit and a.state=='pending' and now<a.expires_ns for a in self._actions.values()):
            raise ApprovalError('PENDING_ACTION')
        at=self.utc()
        action=Action(uuid4().hex,session_id,trace_id,self.policy.environment_id,unit,tuple(evidence_ids),
            self._sessions[session_id],now,now+self.policy.ttl_seconds*10**9,
            _stamp(at),_stamp(at+timedelta(seconds=self.policy.ttl_seconds)))
        self._save(action,'proposed',peer_uid)
        return action

    def _pending(self,action_id,peer_uid):
        if not _id(action_id):raise ApprovalError('INVALID_PROPOSAL')
        action=self._actions.get(action_id)
        if action is None or not _uid(peer_uid) or peer_uid!=action.owner_uid:raise ApprovalError('CALLER_DENIED')
        if action.state=='expired':raise ApprovalError('EXPIRED')
        if action.state!='pending':raise ApprovalError('STATE_FINAL')
        if self.clock()>=action.expires_ns:
            self._save(replace(action,state='expired'),'expired',peer_uid)
            raise ApprovalError('EXPIRED')
        return action

    @_serialized
    def approve(self,action_id,*,peer_uid):
        action=self._pending(action_id,peer_uid)
        now=self.clock()
        if now>=action.expires_ns:
            self._save(replace(action,state='expired'),'expired',peer_uid)
            raise ApprovalError('EXPIRED')
        def check_expiry():
            if self.clock()>=action.expires_ns:raise ApprovalError('EXPIRED')
        try:
            self._save(replace(action,state='approved'),'approved',peer_uid,before_commit=check_expiry)
        except ApprovalError as error:
            if error.code=='EXPIRED':
                self._save(replace(action,state='expired'),'expired',peer_uid)
            raise
        if self._store is not None:
            # Disk I/O can exceed the decision TTL. A committed approval remains
            # terminal even if its grant is withheld; never revert/reissue it.
            now=self.clock()
            if now>=action.expires_ns:raise ApprovalError('EXPIRED')
        grant=self.codec._sign(action,peer_uid,now,_stamp(self.utc()))
        if self.clock()>=action.expires_ns:raise ApprovalError('EXPIRED')
        return grant

    @_serialized
    def reject(self,action_id,*,peer_uid):
        action=self._pending(action_id,peer_uid)
        self._save(replace(action,state='denied'),'denied',peer_uid)

    @_serialized
    def status(self,action_id,*,peer_uid,session_id=None):
        action=self._actions.get(action_id) if _id(action_id) else None
        if action is None or not _uid(peer_uid):raise ApprovalError('CALLER_DENIED')
        operator=peer_uid==action.owner_uid
        reader=peer_uid==self.policy.reader_uid and _id(session_id) and session_id==action.session_id
        if not operator and not reader:raise ApprovalError('CALLER_DENIED')
        if action.state=='pending' and self.clock()>=action.expires_ns:
            action=replace(action,state='expired');self._save(action,'expired',peer_uid)
        return action
