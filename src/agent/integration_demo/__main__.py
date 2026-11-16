"""SYNTHETIC DEMO: mock HTTP → real SDK stdio fixture → Incident Report."""
import asyncio
import sys

from agent.cli.__main__ import SafeParser, timeout_value
from agent.cli.report_store import save_report, valid_name, StoreError
from agent.cli.synthetic import write_requested, redact
from agent.incident_report import render_markdown
from .fixtures import SCENARIOS
from .session import DemoSession, DemoError

LABEL = ('# SYNTHETIC DEMO\n\n'
         'Mock HTTP LLM + official MCP SDK stdio + synthetic server. '
         'Không Ollama inference, không Linux diagnostics thật. '
         'Citations không xác minh ngữ nghĩa.\n\n')


async def drive(question, scenario, *, seconds=120):
    session = DemoSession(scenario, seconds=seconds)
    try:
        await session.run(question)
    except asyncio.CancelledError:
        pass  # Coordinator/report preserve cancellation; do not restart reasoning.
    except DemoError:
        pass
    finally:
        warned = False
        # Keep event loop and owners while cleanup is unconfirmed. This is not
        # an inference retry or a hard wall SLA; never silently abandon a child.
        while session.cleanup_pending:
            if not warned:
                print('SYNTHETIC DEMO: cleanup_pending; giữ owners và chờ cleanup.', file=sys.stderr)
                warned = True
            try:
                await session.finish_cleanup()
            except (DemoError, asyncio.CancelledError):
                # A repeated cancellation must not interrupt the pending-owner
                # recovery path at a backoff await either.
                pause = asyncio.create_task(asyncio.sleep(.05))
                while not pause.done():
                    try:
                        await asyncio.wait({pause})
                    except asyncio.CancelledError:
                        pass
    return session.coordinator.report


def main(argv=None):
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--scenario', choices=SCENARIOS, required=True)
    parser.add_argument('--question')
    parser.add_argument('--report', metavar='NAME.md')
    parser.add_argument('--timeout', type=timeout_value, default=120)
    args = parser.parse_args(argv)
    if args.report is not None and not valid_name(args.report):
        parser.error('INVALID_REPORT')
    try:
        question = args.question if args.question is not None else sys.stdin.readline(4097)
        if not question or not question.strip() or len(question) > 4096:
            parser.error('INVALID_QUESTION')
        if write_requested(question):
            print('SYNTHETIC DEMO: từ chối write/restart/sudo/approval.', file=sys.stderr)
            return 2
        report = asyncio.run(drive(redact(question), args.scenario, seconds=args.timeout))
        text = LABEL + render_markdown(report, redact=redact)
        print(text, end='')
        if args.report:
            save_report(args.report, text)  # Exactly the same text as stdout.
        return 130 if report.status == 'cancelled' else (0 if report.status == 'concluded' else 1)
    except KeyboardInterrupt:
        print('SYNTHETIC DEMO: đã hủy; backend inference cancellation NOT VERIFIED.', file=sys.stderr)
        return 130
    except StoreError:
        print('SYNTHETIC DEMO: không lưu report; không overwrite hoặc theo symlink.', file=sys.stderr)
        return 3
    except Exception:
        print('SYNTHETIC DEMO: lỗi an toàn; không có kết luận chắc chắn.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
