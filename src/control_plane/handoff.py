"""Private coordinator-to-helper handoff; never a model/CLI approval API."""
import asyncio

from .approval import ApprovalError, _id, _uid
from .ledger import JobResult
from .protocol import ApprovalEndpoint
from .transport import ChannelError, UnixMessageChannel


class HelperHandoff:
    def __init__(self, endpoint, *, helper_uid):
        if type(endpoint) is not ApprovalEndpoint or not _uid(helper_uid):
            raise ApprovalError('INVALID_POLICY')
        self._endpoint = endpoint
        self._uid = helper_uid
        self._ready = False
        self._pending = {}
        self.last_error = None

    @property
    def pending_ids(self):
        return tuple(self._pending)

    async def _exchange(self, channel, request):
        if type(channel) is not UnixMessageChannel:
            raise ApprovalError('INVALID_POLICY')
        async with channel:
            if channel.peer.uid != self._uid:
                raise ApprovalError('CALLER_DENIED')
            await channel.send(request)
            response = await channel.receive()
        if (set(response) != {'version', 'ok', 'result'} or type(response.get('version')) is not int
                or response['version'] != 1 or response.get('ok') is not True
                or type(response.get('result')) is not dict):
            # Never reflect remote error text. Query is allowed; submit is not retried.
            raise ApprovalError('HELPER_REJECTED')
        return response['result']

    async def hello(self, channel):
        self._ready = False
        result = await self._exchange(channel, dict(version=1, op='hello'))
        codec = self._endpoint.coordinator.codec
        expected = dict(helper_epoch=codec.epoch, environment_id=codec.policy.environment_id,
                        policy_version=codec.policy.version)
        if result != expected:
            raise ApprovalError('INVALID_POLICY')
        self._ready = True

    def _validate(self, result, action_id,*,expected=None):
        expected = self._pending[action_id] if expected is None else expected
        fields = set(expected) | {'unit', 'outcome', 'executed', 'job_path', 'job_result', 'error_code', 'cleanup_pending'}
        if (set(result) != fields or any(result.get(key) != value for key, value in expected.items())
                or result['unit'] != 'nginx.service' or type(result['cleanup_pending']) is not bool):
            raise ApprovalError('PROTOCOL_ERROR')
        # Admission has no error; later failures expose only fixed safe codes.
        error = result['error_code']
        if error == 'STATE_UNAVAILABLE':
            if result['job_path'] is not None or result['job_result'] is not None:
                raise ApprovalError('PROTOCOL_ERROR')
            observed = JobResult()
        else:
            observed = JobResult(result['job_path'], result['job_result'], error)
        if result['outcome'] != observed.outcome or result['executed'] is not observed.executed:
            raise ApprovalError('PROTOCOL_ERROR')
        return result

    async def submit(self, channel, action_id):
        # Channel ownership transfers even for an invalid operation.
        if type(channel) is not UnixMessageChannel:
            raise ApprovalError('INVALID_POLICY')
        async with channel:
            if channel.peer.uid != self._uid:
                raise ApprovalError('CALLER_DENIED')
            if not self._ready or not _id(action_id) or action_id in self._pending:
                raise ApprovalError('STATE_FINAL')
            grant = self._endpoint._take_grant(action_id)
            payload = self._endpoint.coordinator.codec.verify(grant,
                peer_uid=self._endpoint.coordinator.policy.coordinator_uid)
            self._pending[action_id] = {key: payload[key] for key in
                ('action_id', 'session_id', 'trace_id', 'environment_id')}
            try:
                result = await self._exchange(channel, dict(version=1, op='submit', grant=grant.wire.decode('ascii')))
                return self._validate(result, action_id)
            except asyncio.CancelledError:
                raise  # Local pending correlation and consumed outbox remain owned.
            except (ChannelError, ApprovalError) as error:
                self.last_error = error.code if error.code in {
                    'TIMEOUT', 'DISCONNECTED', 'PROTOCOL_ERROR', 'HELPER_REJECTED',
                    'OUTPUT_LIMIT', 'INVALID_FRAME', 'CALLER_DENIED'} else 'PROTOCOL_ERROR'
                raise ApprovalError('EXECUTION_UNKNOWN') from None

    async def status(self, channel, action_id):
        if type(channel) is not UnixMessageChannel:
            raise ApprovalError('INVALID_POLICY')
        async with channel:
            if not _id(action_id) or action_id not in self._pending:
                raise ApprovalError('UNKNOWN_ACTION')
            result = await self._exchange(channel, dict(version=1, op='status', action_id=action_id))
            return self._validate(result, action_id)

    async def read_status(self,channel,action):
        """Read an authenticated persisted action; never recover/reissue a grant."""
        from .approval import Action
        if type(channel) is not UnixMessageChannel:raise ApprovalError('INVALID_POLICY')
        async with channel:
            policy=self._endpoint.coordinator.policy
            if (type(action) is not Action or action.state!='approved' or action.unit!='nginx.service'
                    or action.environment_id!=policy.environment_id or action.owner_uid not in policy.operator_uids):
                raise ApprovalError('CALLER_DENIED')
            expected={k:getattr(action,k) for k in ('action_id','session_id','trace_id','environment_id')}
            result=await self._exchange(channel,dict(version=1,op='status',action_id=action.action_id))
            return self._validate(result,action.action_id,expected=expected)
