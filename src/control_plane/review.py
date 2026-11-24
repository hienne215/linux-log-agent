"""Trusted operator-side pending-action review; no executor or grant access.

Not installed as a command until a protected control service is accepted. This
module never imports Agent/model code. A TTY check is a guard, not authentication
of human presence; malicious code under an operator UID is outside the TCB.
"""
from datetime import datetime
from types import MappingProxyType
import math
import os
import re
import selectors
import sys
import termios
import time

from .approval import ApprovalError, _id, _uid
from .protocol import ACTION_FIELDS, MAX_REFERENCES
from .transport import ChannelError, UnixMessageChannel


REMOTE_ERRORS = frozenset(('CALLER_DENIED', 'INVALID_REQUEST', 'INVALID_PROPOSAL',
    'OPERATION_DENIED', 'PENDING_ACTION', 'UNIT_LOCKED', 'CAPACITY', 'STATE_FINAL', 'EXPIRED',
    'OUTPUT_LIMIT', 'STATE_UNAVAILABLE', 'EXECUTION_DISABLED', 'EXECUTION_UNKNOWN',
    'CONFIRMATION_REQUIRED', 'UNKNOWN_ACTION', 'CLOSED'))


class PendingReview:
    """Validated backend projection; no reason/log/model text is displayed."""

    def __init__(self, record, *, request_started):
        now = time.monotonic()
        try:
            if (type(request_started) not in (int, float) or not math.isfinite(request_started)
                    or not 0 <= request_started <= now
                    or type(record) is not dict or set(record) != ACTION_FIELDS):
                raise ValueError()
            for name in ('action_id', 'session_id'):
                if type(record[name]) is not str or re.fullmatch('[0-9a-f]{32}', record[name]) is None:
                    raise ValueError()
            if not all(_id(record[name]) for name in ('trace_id', 'environment_id')):
                raise ValueError()
            refs = record['evidence_ids']
            if (type(refs) is not list or not 1 <= len(refs) <= MAX_REFERENCES
                    or not all(_id(ref) for ref in refs) or len(set(refs)) != len(refs)
                    or record['unit'] != 'nginx.service'
                    or type(record['state']) is not str
                    or record['state'] not in ('pending', 'approved', 'denied', 'expired')
                    or record['execution_enabled'] is not False or record['executed'] is not None
                    or type(record['remaining_ms']) is not int or not 0 <= record['remaining_ms'] <= 60000):
                raise ValueError()
            stamp = record['expires_at']
            if (type(stamp) is not str
                    or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z', stamp) is None):
                raise ValueError()
            datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        except Exception:
            raise ApprovalError('INVALID_RESPONSE') from None
        self._record = MappingProxyType({**record, 'evidence_ids': tuple(refs)})
        # Count network/display time conservatively; never start a fresh TTL
        # after the prompt. Backend monotonic expiry is rechecked on approve.
        self._deadline = request_started + record['remaining_ms'] / 1000
        self._terminal = None
        self._decision = None
        self._client = None

    @property
    def action_id(self): return self._record['action_id']

    @property
    def session_id(self): return self._record['session_id']

    @property
    def trace_id(self): return self._record['trace_id']

    @property
    def environment_id(self): return self._record['environment_id']

    @property
    def evidence_ids(self): return self._record['evidence_ids']

    @property
    def state(self): return self._record['state']

    @property
    def expires_at(self): return self._record['expires_at']

    @property
    def decision(self): return self._decision

    @property
    def cleanup_pending(self):
        return self._terminal is not None

    def finish_cleanup(self):
        if self._terminal is not None:
            fd, original = self._terminal
            try:
                termios.tcflush(fd, termios.TCIFLUSH)
            finally:
                termios.tcsetattr(fd, termios.TCSANOW, original)
            os.close(fd)
            self._terminal = None

    def display(self, *, execution=False):
        return (('LAB VM — XÁC NHẬN THỰC THI RESTART\n' if execution else
                 'XÁC NHẬN ĐỀ XUẤT — KHÔNG TỰ THỰC THI\n') +
                f'Action ID: {self.action_id}\nMôi trường: {self.environment_id}\n'
                'Hành động: đề xuất khởi động lại nginx.service\n'
                f'Trạng thái backend: {self.state}; hết hạn (UTC): {self.expires_at}\n'
                'Rủi ro: có thể gián đoạn kết nối; restart có thể không sửa được nguyên nhân.\n'
                'Tham chiếu evidence (chưa xác minh ngữ nghĩa): ' + ', '.join(self.evidence_ids) + '\n'
                + ('Xác nhận này cho phép gửi một restart; không retry. Không bảo đảm sửa được lỗi.\n'
                   if execution else 'Chỉ xác nhận đề xuất này; approved không có nghĩa executed.\n'))

    def confirm(self, *, stdin=None, stdout=None, execution=False):
        if self.decision is not None:
            return self.decision
        stdin = sys.stdin if stdin is None else stdin
        stdout = sys.stdout if stdout is None else stdout
        result = 'rejected'
        try:
            if type(execution) is not bool or self.state != ('approved' if execution else 'pending'):
                result = 'state_final'
            elif time.monotonic() >= self._deadline:
                result = 'expired'
            elif (not stdin.isatty() or not stdout.isatty()
                    or not os.isatty(stdin.fileno()) or not os.isatty(stdout.fileno())
                    or os.ttyname(stdin.fileno()) != os.ttyname(stdout.fileno())):
                result = 'non_tty'
            else:
                # Retain an owned fd if restoration must be retried after the
                # caller closes its stdin wrapper. Never retain a borrowed fd.
                fd = os.dup(stdin.fileno())
                try:
                    original = termios.tcgetattr(fd)
                except BaseException:
                    os.close(fd)
                    raise
                self._terminal = (fd, original)
                attrs = list(original)
                attrs[3] = (attrs[3] | termios.ICANON | termios.ISIG) & ~(termios.ECHO | termios.ECHONL)
                try:
                    termios.tcsetattr(fd, termios.TCSANOW, attrs)
                    termios.tcflush(fd, termios.TCIFLUSH)  # no queued confirmation
                    word = 'THUC_THI' if execution else 'DONG_Y'
                    stdout.write(self.display(execution=execution))
                    stdout.write(f'Gõ {word} {self.action_id} hoặc TU_CHOI {self.action_id}\n'
                                 '(không hiện ký tự nhập; không nhập secrets):\n')
                    stdout.flush()
                    remaining = self._deadline - time.monotonic()
                    if remaining <= 0:
                        result = 'expired'
                    else:
                        with selectors.DefaultSelector() as selector:
                            selector.register(fd, selectors.EVENT_READ)
                            ready = selector.select(remaining)
                            raw = os.read(fd, 129) if ready else b''
                        if not ready or time.monotonic() >= self._deadline:
                            result = 'expired'
                        elif raw == (word + ' ' + self.action_id + '\n').encode('ascii'):
                            result = 'execution_confirmed' if execution else 'confirmed'
                        elif raw == ('TU_CHOI ' + self.action_id + '\n').encode('ascii'):
                            result = 'rejected'
                        else:
                            result = 'eof' if not raw else 'invalid_input'
                finally:
                    self.finish_cleanup()
        except KeyboardInterrupt:
            result = 'cancelled'
        except Exception:
            result = 'terminal_error'
        if self.cleanup_pending:
            result = 'terminal_error'
        self._decision = result
        return result


class OperatorClient:
    """Application-configured channel factory and pinned coordinator UID.

    No socket path/model/URL/command is accepted from a prompt. Each exchange
    owns one fresh connection. Errors propagate without resend/reconnect. The
    caller retains server/service owners; this class starts no background task.
    """

    def __init__(self, channel_factory, *, expected_uid):
        if not callable(channel_factory) or not _uid(expected_uid):
            raise ApprovalError('INVALID_POLICY')
        self._factory = channel_factory
        self._expected_uid = expected_uid

    async def _request(self, request):
        channel = await self._factory()
        if type(channel) is not UnixMessageChannel:
            raise ChannelError('INVALID_CONFIG')
        async with channel:
            if channel.peer.uid != self._expected_uid:
                raise ChannelError('PEER_DENIED')
            await channel.send(request)
            response = await channel.receive()
        if (type(response.get('version')) is not int or response['version'] != 1
                or type(response.get('ok')) is not bool):
            raise ApprovalError('INVALID_RESPONSE')
        if not response['ok']:
            # Do not reflect an untrusted error_code from the server in logs.
            if set(response) != {'version', 'ok', 'error_code'}:
                raise ApprovalError('INVALID_RESPONSE')
            code = response['error_code']
            raise ApprovalError(code if type(code) is str and code in REMOTE_ERRORS else 'REQUEST_DENIED')
        if set(response) != {'version', 'ok', 'result'}:
            raise ApprovalError('INVALID_RESPONSE')
        return response['result']

    async def pending(self, action_id):
        if type(action_id) is not str or re.fullmatch('[0-9a-f]{32}', action_id) is None:
            raise ApprovalError('INVALID_PROPOSAL')
        started = time.monotonic()
        result = await self._request(dict(version=1, op='status', action_id=action_id))
        review = PendingReview(result, request_started=started)
        if review.action_id != action_id:
            raise ApprovalError('INVALID_RESPONSE')
        review._client = self
        return review

    async def submit(self, review):
        if (type(review) is not PendingReview or review.cleanup_pending
                or review._client is not self
                or review.decision not in ('confirmed', 'rejected')
                or review.state != 'pending'):
            raise ApprovalError('CONFIRMATION_REQUIRED')
        if time.monotonic() >= review._deadline:
            raise ApprovalError('EXPIRED')
        # Consume this local decision before I/O. If response is lost, query
        # status explicitly; never send the approval again automatically.
        op = 'approve' if review.decision == 'confirmed' else 'reject'
        review._decision = 'submitted'
        started = time.monotonic()
        result = await self._request(dict(version=1, op=op, action_id=review.action_id))
        updated = PendingReview(result, request_started=started)
        if (updated.action_id, updated.session_id, updated.trace_id, updated.environment_id,
                updated.evidence_ids, updated.expires_at) != (review.action_id, review.session_id, review.trace_id,
                review.environment_id, review.evidence_ids, review.expires_at):
            raise ApprovalError('INVALID_RESPONSE')
        if updated.state != ('approved' if op == 'approve' else 'denied'):
            raise ApprovalError('INVALID_RESPONSE')
        return updated
