"""Internal proposal/control handlers, not an installed service or MCP API.

No listener or helper operation is exposed to reader/control requests. Approved
grants stay private; only an application-owned HelperHandoff can consume them
once. They never go to CLI/Agent. The coordinator can persist decisions/audit
with CoordinatorState. Deployment acceptance is still required; no outbox
recovery or automatic redelivery is implemented.
"""
from enum import Enum

from .approval import ApprovalCoordinator, ApprovalError, _id
from .transport import ChannelError, UnixMessageChannel


class Port(str, Enum):
    PROPOSAL = 'proposal'
    CONTROL = 'control'


ACTION_FIELDS = frozenset(('action_id', 'session_id', 'trace_id', 'environment_id',
    'unit', 'evidence_ids', 'state', 'expires_at', 'remaining_ms',
    'execution_enabled', 'executed'))
MAX_REFERENCES = 16  # bounded internal UI projection; not a canonical MCP limit


class ApprovalEndpoint:
    """Single-message handler; a future service owns accept/quota/shutdown.

    The application chooses the port, never the request. Kernel peer UID is
    checked against the port's role before parsing. SO_PEERCRED is process
    authentication, not proof of a human or of independent deployed identities.
    """

    def __init__(self, coordinator):
        if type(coordinator) is not ApprovalCoordinator:
            raise ApprovalError('INVALID_POLICY')
        self.coordinator = coordinator
        self._grants = {}  # private, bounded by the core's 128-action capacity
        self._closed = False

    def __repr__(self):
        return '<ApprovalEndpoint private; execution disabled>'

    def close(self):
        # Caller must stop accepting and join all handlers first. No transfer or
        # retry is attempted here. The core retains terminal approved states.
        self._closed = True
        self._grants.clear()

    def _take_grant(self, action_id):
        """Trusted service handoff only, never a request/CLI operation.

        Remove before network I/O; a lost delivery cannot resend the grant.
        Approved state remains terminal even if no delivery can be confirmed.
        """
        if self._closed:
            raise ApprovalError('CLOSED')
        if not _id(action_id):
            raise ApprovalError('INVALID_REQUEST')
        grant = self._grants.pop(action_id, None)
        if grant is None:
            raise ApprovalError('STATE_FINAL')
        return grant

    def _view(self, action):
        if not 1 <= len(action.evidence_ids) <= MAX_REFERENCES:
            raise ApprovalError('OUTPUT_LIMIT')
        remaining = max(0, (action.expires_ns - self.coordinator.clock() + 999999) // 1000000)
        return dict(action_id=action.action_id, session_id=action.session_id,
            trace_id=action.trace_id, environment_id=action.environment_id,
            unit=action.unit, evidence_ids=list(action.evidence_ids), state=action.state,
            expires_at=action.expires_at, remaining_ms=min(60000, remaining),
            execution_enabled=False, executed=None)

    def _dispatch(self, request, port, uid):
        # Recheck after receive: shutdown may occur while a connected peer waits.
        # Never mint or publish a new grant after close cleared the outbox.
        if self._closed:
            raise ApprovalError('CLOSED')
        if (type(request) is not dict or type(request.get('version')) is not int
                or request['version'] != 1 or type(request.get('op')) is not str):
            raise ApprovalError('INVALID_REQUEST')
        op = request['op']
        fields = {
            (Port.CONTROL, 'open_session'): {'version', 'op'},
            (Port.CONTROL, 'status'): {'version', 'op', 'action_id'},
            (Port.CONTROL, 'approve'): {'version', 'op', 'action_id'},
            (Port.CONTROL, 'reject'): {'version', 'op', 'action_id'},
            (Port.PROPOSAL, 'propose'): {'version', 'op', 'session_id', 'trace_id', 'unit', 'evidence_ids'},
            (Port.PROPOSAL, 'status'): {'version', 'op', 'session_id', 'action_id'},
        }.get((port, op))
        if fields is None:
            raise ApprovalError('OPERATION_DENIED')
        if set(request) != fields:
            raise ApprovalError('INVALID_REQUEST')
        if op == 'open_session':
            return {'session_id': self.coordinator.open_session(peer_uid=uid)}
        if op == 'propose':
            refs = request['evidence_ids']
            if type(refs) is not list or not 1 <= len(refs) <= MAX_REFERENCES:
                raise ApprovalError('INVALID_PROPOSAL')
            return self._view(self.coordinator.propose(peer_uid=uid,
                session_id=request['session_id'], trace_id=request['trace_id'],
                unit=request['unit'], evidence_ids=refs))
        if not _id(request['action_id']):
            raise ApprovalError('INVALID_REQUEST')
        status_args = dict(peer_uid=uid)
        if port is Port.PROPOSAL:
            status_args['session_id'] = request['session_id']
        # Authorize ownership before any state mutation or grant minting.
        action = self.coordinator.status(request['action_id'], **status_args)
        self._view(action)  # reject an oversized/unreviewable action before minting
        if op in ('approve', 'reject') and action.state == 'expired':
            raise ApprovalError('EXPIRED')
        if op == 'approve':
            grant = self.coordinator.approve(action.action_id, peer_uid=uid)
            self._grants[action.action_id] = grant
        elif op == 'reject':
            self.coordinator.reject(action.action_id, peer_uid=uid)
        return self._view(self.coordinator.status(action.action_id, **status_args))

    async def serve(self, channel, *, port):
        if type(channel) is not UnixMessageChannel:
            raise ChannelError('INVALID_CONFIG')
        async with channel:
            if self._closed:
                raise ChannelError('CLOSED')
            if type(port) is not Port:
                raise ChannelError('INVALID_CONFIG')
            uid = channel.peer.uid
            policy = self.coordinator.policy
            allowed = uid == policy.reader_uid if port is Port.PROPOSAL else uid in policy.operator_uids
            if not allowed:
                raise ChannelError('PEER_DENIED')
            request = await channel.receive()
            try:
                result = self._dispatch(request, port, uid)
                response = dict(version=1, ok=True, result=result)
            except ApprovalError as error:
                response = dict(version=1, ok=False, error_code=error.code)
            except Exception:
                response = dict(version=1, ok=False, error_code='STATE_UNAVAILABLE')
            # Network failure after approve does not undo approval. The private
            # grant remains owned here; no background delivery/retry is started.
            await channel.send(response)
