"""Synthetic action-review UI only. No approval authority, grant or executor."""
from dataclasses import dataclass, field
from enum import Enum
import math
import os
import re
import selectors
import sys
import termios
import time
from uuid import uuid4

from .__main__ import SafeParser
from .synthetic import steps_for


class Status(str, Enum):
    PROPOSED = 'proposed'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    EXPIRED = 'expired'


class Scenario(str, Enum):
    PORT_CONFLICT = 'port-conflict'
    NO_EVIDENCE = 'no-evidence'


class Reason(str, Enum):
    CONFIRMED = 'CONFIRMED_LOCAL_DEMO_ONLY'
    REJECTED = 'USER_REJECTED'
    EXPIRED = 'EXPIRED'
    NON_TTY = 'NON_TTY'
    EOF = 'EOF'
    CANCELLED = 'CANCELLED'
    INVALID_INPUT = 'INVALID_INPUT'
    TERMINAL_ERROR = 'TERMINAL_ERROR'


@dataclass(frozen=True)
class Decision:
    status: Status
    reason: Reason
    executed: bool = field(default=False, init=False)


class Proposal:
    """Application-owned in-memory fixture; never a SCRUM-12 wire record.

    No arbitrary unit, operation, reason, evidence, identity or approval token can
    be supplied. Local APPROVED is a UI decision, never backend authorization.
    """
    def __init__(self, scenario=Scenario.PORT_CONFLICT, *, seconds=30):
        if (type(scenario) is not Scenario or type(seconds) not in (int, float)
                or not math.isfinite(seconds) or not 0 < seconds <= 60):
            raise ValueError('INVALID_CONFIG')
        self._scenario = scenario
        self._id = uuid4().hex
        self._deadline = time.monotonic() + seconds
        self._decision = None
        self._terminal = None

    @property
    def identifier(self):
        return self._id

    @property
    def status(self):
        return self._decision.status if self._decision else Status.PROPOSED

    @property
    def cleanup_pending(self):
        return self._terminal is not None

    def finish_cleanup(self):
        """Retry only terminal restoration, never the decision or an action."""
        if self._terminal is not None:
            fd, original = self._terminal
            try:
                termios.tcflush(fd, termios.TCIFLUSH)
            finally:
                termios.tcsetattr(fd, termios.TCSANOW, original)
            os.close(fd)
            self._terminal = None  # keep owner if either operation failed

    def display(self):
        # Static trusted labels; no raw model reason/log text is interpolated.
        if re.fullmatch(r'[0-9a-f]{32}', self._id) is None:
            raise ValueError('INVALID_PROPOSAL')
        if self._scenario is Scenario.PORT_CONFLICT:
            reason = 'Fixture ghi nhận xung đột cổng 80; restart có thể không giải quyết nguyên nhân.'
            refs = [f'{e.id} — {e.source}' for step in steps_for('port-conflict')
                    for e in step.observation.evidence]
        elif self._scenario is Scenario.NO_EVIDENCE:
            reason, refs = 'Chưa có evidence; không có căn cứ xác nhận đã sửa lỗi.', []
        else:
            raise ValueError('INVALID_PROPOSAL')
        return ('SYNTHETIC DEMO — XÁC NHẬN CỤC BỘ, KHÔNG CẤP QUYỀN\n'
                f'ID đề xuất: {self._id}\nTrạng thái: {self.status.value}\n'
                'Unit allowlist: nginx.service\nHành động đề xuất: khởi động lại dịch vụ\n'
                f'Lý do (fixture ứng dụng, không phải chỉ thị): {reason}\n'
                'Rủi ro: có thể gián đoạn kết nối; chưa có helper hay runtime được nghiệm thu.\n'
                'Nguồn evidence: ' + ('; '.join(refs) if refs else 'không có') + '\n'
                'Các nguồn trên chỉ là fixture synthetic có sẵn, không phải lần đọc Linux mới.\n'
                'approved KHÔNG phải executed; không gửi token/grant hoặc gọi backend.\n')


class _Expired(Exception):
    pass


def _read_choice(proposal, stdin, stdout):
    """One bounded TTY read. Own/restore terminal and selector in this task.

    No thread, async owner, MCP/LLM session or detached timeout task is created.
    Do not use input() or a fallback pipe. TTY is a guard, not human authentication.
    """
    # Own the restoration descriptor independently of the caller's stdin.
    fd = os.dup(stdin.fileno())
    try:
        original = termios.tcgetattr(fd)
    except BaseException:
        os.close(fd)
        raise
    proposal._terminal = (fd, original)
    attrs = list(original)
    attrs[3] = (attrs[3] | termios.ICANON | termios.ISIG) & ~(termios.ECHO | termios.ECHONL)
    try:
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIFLUSH)  # no queued/type-ahead confirmation
        stdout.write(proposal.display())
        stdout.write(f'Gõ DONG_Y {proposal.identifier} hoặc TU_CHOI {proposal.identifier}\n'
                     '(không hiện ký tự nhập; chỉ một lần, không nhập secrets):\n')
        stdout.flush()
        remaining = proposal._deadline - time.monotonic()
        if remaining <= 0:
            raise _Expired()
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_READ)
            if not selector.select(remaining):
                raise _Expired()
            raw = os.read(fd, 129)  # canonical input; maximum accepted line 128 bytes
        if time.monotonic() >= proposal._deadline:
            raise _Expired()
        if len(raw) > 128:
            raise ValueError('INVALID_INPUT')
        return raw.decode('ascii')  # only fixed ASCII decision phrases are valid
    finally:
        # Even on Ctrl+C/EOF/timeout/failure, restore before committing a decision.
        # A restoration error suppresses approval and becomes TERMINAL_ERROR.
        proposal.finish_cleanup()


def confirm(proposal, *, stdin=None, stdout=None):
    """Only explicit interactive input may produce a local approved decision."""
    if type(proposal) is not Proposal:
        raise ValueError('INVALID_PROPOSAL')
    if proposal._decision is not None:
        return proposal._decision  # terminal state; never prompt/transition twice
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        if time.monotonic() >= proposal._deadline:
            decision = Decision(Status.EXPIRED, Reason.EXPIRED)
        elif (not stdin.isatty() or not stdout.isatty()
              or not os.isatty(stdin.fileno()) or not os.isatty(stdout.fileno())
              or os.ttyname(stdin.fileno()) != os.ttyname(stdout.fileno())):
            decision = Decision(Status.REJECTED, Reason.NON_TTY)
        else:
            choice = _read_choice(proposal, stdin, stdout)
            if time.monotonic() >= proposal._deadline:
                decision = Decision(Status.EXPIRED, Reason.EXPIRED)
            elif choice == 'DONG_Y ' + proposal.identifier + '\n':
                decision = Decision(Status.APPROVED, Reason.CONFIRMED)
            elif choice == 'TU_CHOI ' + proposal.identifier + '\n':
                decision = Decision(Status.REJECTED, Reason.REJECTED)
            else:
                decision = Decision(Status.REJECTED, Reason.EOF if not choice else Reason.INVALID_INPUT)
    except KeyboardInterrupt:
        decision = Decision(Status.REJECTED, Reason.CANCELLED)
    except _Expired:
        decision = Decision(Status.EXPIRED, Reason.EXPIRED)
    except (ValueError, UnicodeError):
        decision = Decision(Status.REJECTED, Reason.INVALID_INPUT)
    except Exception:
        decision = Decision(Status.REJECTED, Reason.TERMINAL_ERROR)
    if proposal.cleanup_pending:
        decision = Decision(Status.REJECTED, Reason.TERMINAL_ERROR)
    proposal._decision = decision
    return decision


def _seconds(value):
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 60:
        raise ValueError('INVALID_TIMEOUT')
    return number


def main(argv=None):
    parser = SafeParser(description='SYNTHETIC DEMO — xem và xác nhận cục bộ; không thực thi.',
                        allow_abbrev=False)
    parser.add_argument('--scenario', choices=[s.value for s in Scenario], default='port-conflict')
    parser.add_argument('--timeout', type=_seconds, default=30, help='0 < giây <= 60; mặc định 30.')
    args = parser.parse_args(argv)
    try:
        proposal = Proposal(Scenario(args.scenario), seconds=args.timeout)
        decision = confirm(proposal)
        if proposal.cleanup_pending:
            try:
                proposal.finish_cleanup()
            except (Exception, KeyboardInterrupt):
                pass  # retain owner and report pending; never approve after restoration failure
        print(f'SYNTHETIC DEMO: {decision.status.value}; {decision.reason.value}; executed=false; '
              f'cleanup_pending={str(proposal.cleanup_pending).lower()}.\n'
              'Không cấp quyền, không thực thi và không ghi approval vào Incident Report/audit.')
        return 130 if decision.reason is Reason.CANCELLED else (0 if decision.status is Status.APPROVED else 1)
    except KeyboardInterrupt:
        print('SYNTHETIC DEMO: rejected; CANCELLED; executed=false.', file=sys.stderr)
        return 130
    except Exception:
        print('SYNTHETIC DEMO: rejected; TERMINAL_ERROR; executed=false.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
