"""Owned helper request handlers; no listener, identities or privileges installed.

An accepted service supplies authenticated connected channels and owns this
object until finish_cleanup succeeds. A disconnected coordinator cannot discard
an admitted operation. Only one operation/cleanup owner exists at a time.
"""
import asyncio

from .approval import ApprovalError, Grant, _id
from .helper import HelperRunner
from .transport import ChannelError, UnixMessageChannel


def attempt_view(payload, attempt=None, *, cleanup_pending=False):
    result = {key: payload[key] for key in ('action_id', 'session_id', 'trace_id', 'environment_id')}
    result.update(unit='nginx.service', outcome='execution_unknown', executed=None,
                  job_path=None, job_result=None, error_code=None, cleanup_pending=cleanup_pending)
    if attempt is not None:
        result.update(outcome=attempt.outcome, executed=attempt.executed, job_path=attempt.job_path,
                      job_result=attempt.job_result, error_code=attempt.error_code)
    return result


class HelperService:
    def __init__(self, runner):
        if type(runner) is not HelperRunner:
            raise ApprovalError('INVALID_POLICY')
        self.runner = runner
        self.codec = runner.ledger.codec
        self._task = None
        self._records = {}
        self._error = None
        self._closing = self._joining = False

    @property
    def cleanup_pending(self):
        return self._task is not None or self.runner.cleanup_pending

    async def _execute(self, grant, uid):
        try:
            await self.runner.execute(grant, peer_uid=uid)
        except asyncio.CancelledError:
            self._error = 'CANCELLED'
            raise
        except Exception:
            self._error = 'STATE_UNAVAILABLE'

    def _status(self, action_id):
        if not _id(action_id):
            raise ApprovalError('UNKNOWN_ACTION')
        attempt = self.runner.ledger.status(action_id)
        record=self._records.get(action_id)
        if record is None:
            if attempt is None:raise ApprovalError('UNKNOWN_ACTION')
            if attempt.environment_id!=self.codec.policy.environment_id:raise ApprovalError('INVALID_POLICY')
            # A read of persisted history does not recreate a grant, task,
            # reservation or execution owner. Unknown intents remain unknown.
            record={key:getattr(attempt,key) for key in ('action_id','session_id','trace_id','environment_id')}
        view = attempt_view(record, attempt,
                            cleanup_pending=self.cleanup_pending)
        if self._error:
            # An uncertain storage outcome must never advertise ephemeral success.
            view.update(outcome='execution_unknown', executed=None, job_path=None,
                        job_result=None, error_code=self._error)
        return view

    def _request(self, request, uid):
        if self._closing:
            raise ApprovalError('CLOSED')
        if (type(request) is not dict or type(request.get('version')) is not int
                or request['version'] != 1 or type(request.get('op')) is not str):
            raise ApprovalError('INVALID_REQUEST')
        op = request['op']
        if op == 'hello' and set(request) == {'version', 'op'}:
            return dict(helper_epoch=self.codec.epoch, environment_id=self.codec.policy.environment_id,
                        policy_version=self.codec.policy.version)
        if op == 'status' and set(request) == {'version', 'op', 'action_id'}:
            return self._status(request['action_id'])
        if op != 'submit' or set(request) != {'version', 'op', 'grant'}:
            raise ApprovalError('INVALID_REQUEST')
        if type(request['grant']) is not str or len(request['grant']) > 4096:
            raise ApprovalError('INVALID_GRANT')
        try:
            grant = Grant(request['grant'].encode('ascii'))
        except UnicodeError:
            raise ApprovalError('INVALID_GRANT') from None
        payload = self.codec.verify(grant, peer_uid=uid)
        action_id = payload['action_id']
        if action_id in self._records or self.runner.ledger.status(action_id) is not None:
            raise ApprovalError('REPLAY')
        if self.cleanup_pending or self._error:
            raise ApprovalError('BUSY')
        if len(self._records) >= 128:
            raise ApprovalError('CAPACITY')
        # Keep no grant/key in status records. Admission isn't job completion or
        # a durable consumed intent; the runner must still check and persist it.
        self._records[action_id] = {key: payload[key] for key in
            ('action_id', 'session_id', 'trace_id', 'environment_id')}
        self._task = asyncio.create_task(self._execute(grant, uid), name='helper-service-owner')
        return self._status(action_id)

    async def serve(self, channel):
        if type(channel) is not UnixMessageChannel:
            raise ChannelError('INVALID_CONFIG')
        async with channel:
            if channel.peer.uid != self.codec.policy.coordinator_uid:
                raise ChannelError('PEER_DENIED')
            request = await channel.receive()
            try:
                result = self._request(request, channel.peer.uid)
                response = dict(version=1, ok=True, result=result)
            except ApprovalError as error:
                response = dict(version=1, ok=False, error_code=error.code)
            except Exception:
                response = dict(version=1, ok=False, error_code='STATE_UNAVAILABLE')
            await channel.send(response)

    async def finish_cleanup(self, seconds=1):
        if type(seconds) not in (int, float) or not 0 < seconds <= 5:
            raise ApprovalError('INVALID_POLICY')
        if self._joining:
            raise ApprovalError('BUSY')
        self._joining = True
        try:
            if self._task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(self._task), seconds)
                except TimeoutError:
                    return False  # Still owned; never cancel/reissue an admitted operation.
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling() or not self._task.cancelled():
                        raise
                    self._error = 'CANCELLED'
                self._task = None
            return await self.runner.finish_cleanup(seconds)
        finally:
            self._joining = False

    async def shutdown(self, seconds=1):
        self._closing = True
        return await self.finish_cleanup(seconds)
