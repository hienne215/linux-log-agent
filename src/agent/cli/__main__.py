"""Run `python -m agent.cli --help`. Synthetic only; no real backend selection."""
import argparse
import asyncio
from dataclasses import dataclass
import math
import re
import sys

from agent.coordinator import Report
from agent.incident_report import render_markdown
from .report_store import save_report, valid_name, StoreError
from .synthetic import SCENARIOS, READ_ONLY, ToolEvent, make_coordinator, redact, write_requested

LABEL = 'SYNTHETIC DEMO'
_STATUS = {
    'concluded': 'Hoàn tất kịch bản giả lập; nhận định chưa xác minh thực tế.',
    'limited': 'Không đủ bằng chứng; thiếu quyền hoặc dữ liệu.',
    'budget_exhausted': 'Đã hết ngân sách; không tiếp tục chẩn đoán.',
    'cancelled': 'Đã hủy; không thực hiện bước mới.',
    'tool_timeout': 'Bước đọc giả lập hết thời gian; kết quả thực thi chưa xác định.',
    'disconnected': 'Mất kết nối giả lập; kết quả thực thi chưa xác định.',
    'cleanup_pending': 'Cleanup chưa được xác nhận.',
    'insufficient_evidence': 'Không đủ bằng chứng để kết luận.',
    'policy_denied': 'Yêu cầu ngoài phạm vi read-only bị từ chối.',
}
_STATES = frozenset({'RECEIVED', 'ANALYZING', 'COLLECTING', 'CONCLUDED', 'CANCELLED',
                     'BUDGET_EXHAUSTED', 'ERROR', 'REPORTED'})


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default can echo raw arguments containing credentials.
        self.exit(2, 'Tham số không hợp lệ. Dùng --help để xem hướng dẫn.\n')


def timeout_value(value):
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 120:
        raise argparse.ArgumentTypeError('INVALID_TIMEOUT')
    return number


def parser():
    result = SafeParser(description='SYNTHETIC DEMO — không chẩn đoán Linux thật.',
                        allow_abbrev=False)
    result.add_argument('--scenario', choices=SCENARIOS, required=True,
                        help='active / port-conflict / permission-denied')
    result.add_argument('--question', help='Câu hỏi; nếu thiếu, nhập một dòng từ stdin.')
    result.add_argument('--timeout', type=timeout_value, default=120,
                        help='Ngân sách chẩn đoán 0 < giây <= 120; không phải hard wall deadline.')
    result.add_argument('--report', metavar='NAME.md',
                        help='Lưu vào reports/NAME.md; không path, không symlink, không ghi đè.')
    return result


@dataclass(frozen=True)
class DemoOutcome:
    report: Report
    trace: tuple[str, ...]
    tool_trace: tuple[ToolEvent, ...] = ()


async def run_demo(scenario, question, *, seconds=120, coordinator=None):
    c = coordinator if coordinator is not None else make_coordinator(scenario, seconds=seconds)
    try:
        report = await c.run(question)
    except asyncio.CancelledError:
        # The synthetic executor is cooperative and has no subprocess resources.
        # Do not detach a cleanup task or resume reasoning after cancellation.
        report = c.report
    finally:
        await c.finish_cleanup()
    report = c.report
    if report is None:
        raise ValueError('MISSING_OUTCOME')
    return DemoOutcome(report, c.state_history, tuple(getattr(c.executor, 'events', ())))


def format_outcome(outcome):
    if (type(outcome.trace) is not tuple or len(outcome.trace) > 24
            or any(type(state) is not str or state not in _STATES for state in outcome.trace)):
        raise ValueError('INVALID_TRACE')
    rendered = render_markdown(outcome.report, redact=redact)
    trace = ' → '.join(outcome.trace) or 'Chưa có trace.'
    evidence_ids = {item.id for item in outcome.report.evidence}
    events = []
    if type(outcome.tool_trace) is not tuple or len(outcome.tool_trace) > 8:
        raise ValueError('INVALID_TRACE')
    for index, event in enumerate(outcome.tool_trace, 1):
        if (type(event) is not ToolEvent or event.tool not in READ_ONLY
                or event.status not in {'ok', 'permission_denied'}
                or type(event.evidence_ids) is not tuple
                or len(event.evidence_ids) > 8
                or any(type(identifier) is not str
                       or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identifier) is None
                       or identifier not in evidence_ids for identifier in event.evidence_ids)):
            raise ValueError('INVALID_TRACE')
        status = ('Không đủ quyền đọc journal (PERMISSION_DENIED, giả lập)'
                  if event.status == 'permission_denied' else 'Đã đọc dữ liệu giả lập')
        refs = ', '.join(event.evidence_ids) or 'không có evidence'
        events.append(f'- Bước {index}: `{event.tool}` — {status}; refs: {refs}.')
    tool_trace = '\n'.join(events) or 'Không có bước đọc giả lập đã hoàn tất.'
    return (f'# {LABEL}\n\n'
            'Toàn bộ evidence bên dưới là dữ liệu giả lập. Không gọi MCP, Ollama hoặc Linux tools.\n\n'
            f'Trạng thái: {_STATUS.get(outcome.report.status, "Lỗi; không thể kết luận chắc chắn.")}\n\n'
            f'Trace ứng dụng: {trace}\n\n{tool_trace}\n\n' + rendered)


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if args.report is not None and not valid_name(args.report):
        p.error('INVALID_REPORT_NAME')
    question = args.question
    if question is None:
        if sys.stdin.isatty():
            print('Nhập câu hỏi (chỉ dùng kịch bản giả lập):', file=sys.stderr)
        try:
            question = sys.stdin.readline(4097)
        except KeyboardInterrupt:
            print(f'{LABEL}: đã hủy trước khi chẩn đoán.', file=sys.stderr)
            return 130
    if not question or not question.strip() or len(question) > 4096:
        p.error('INVALID_QUESTION')
    if write_requested(question):
        print(f'{LABEL}: chỉ đọc giả lập; từ chối restart/sudo/approval.', file=sys.stderr)
        return 2
    try:
        outcome = asyncio.run(run_demo(args.scenario, redact(question), seconds=args.timeout))
        text = format_outcome(outcome)
        print(text, end='')
        code = 130 if outcome.report.status == 'cancelled' else (0 if outcome.report.status == 'concluded' else 1)
        if args.report:
            try:
                path = save_report(args.report, text)
                print(f'Đã lưu báo cáo giả lập: {path}', file=sys.stderr)
            except StoreError:
                print('Không lưu được báo cáo; không ghi đè hoặc đi theo symlink. Dữ liệu chưa được xác nhận lưu bền vững.', file=sys.stderr)
                return 130 if code == 130 else 3
        return code
    except KeyboardInterrupt:
        print(f'{LABEL}: đã hủy; không xác nhận có báo cáo bền vững.', file=sys.stderr)
        return 130
    except Exception:
        # Never echo raw exceptions, question, payloads or local filesystem paths.
        print(f'{LABEL}: lỗi an toàn; không có kết luận được xác nhận.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
