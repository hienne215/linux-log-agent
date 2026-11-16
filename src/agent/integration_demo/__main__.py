"""SYNTHETIC MCP DEMO: default mock or explicit Ollama → SDK fixture → report."""
import asyncio
import json
import sys

from agent.cli.__main__ import SafeParser, timeout_value
from agent.cli.report_store import save_report, valid_name, StoreError
from agent.cli.synthetic import write_requested, redact
from agent.incident_report import render_markdown
from agent.llm.async_adapter import SAFE_CODES
from .fixtures import SCENARIOS
from .session import DemoSession, DemoError
from .backends import Backend, BackendError, select_backend
from .preflight import check, PreflightError

LABEL = ('# SYNTHETIC DEMO\n\n'
         'MOCK LLM + SYNTHETIC MCP. Mock HTTP + official MCP SDK stdio + synthetic server. '
         'Không Ollama inference, không Linux diagnostics thật. '
         'Citations không xác minh ngữ nghĩa.\n\n')


LIVE_LABEL = ('# SYNTHETIC DEMO\n\n'
              'Backend: LIVE OLLAMA + SYNTHETIC MCP. Không tự xác nhận inference thành công. '
              'Evidence chỉ là fixture; '
              'không Linux diagnostics thật. Citations không xác minh ngữ nghĩa.\n\n')


async def drive(question, scenario, *, seconds=120, backend=Backend.MOCK):
    session = DemoSession(scenario, seconds=seconds, backend=backend)
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
    code = session.model.last_error
    if code is not None:
        # Never print exceptions/provider text, even for an unexpected code.
        safe = code if code in SAFE_CODES | {'INVALID_CONCLUSION', 'DEADLINE_EXCEEDED',
                                            'CANCELLED', 'RAM_LOW', 'RAM_UNAVAILABLE'} else 'MODEL_FAILURE'
        print('SYNTHETIC DEMO: LLM_ERROR: ' + safe, file=sys.stderr)
    return session.coordinator.report


def main(argv=None):
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--scenario', choices=SCENARIOS, required=True)
    parser.add_argument('--question')
    parser.add_argument('--backend', choices=('mock', 'ollama'), default='mock')
    parser.add_argument('--preflight-only', action='store_true',
                        help='Ollama metadata only; zero inference, no MCP; requires backend ollama')
    parser.add_argument('--report', metavar='NAME.md')
    parser.add_argument('--timeout', type=timeout_value, default=120)
    args = parser.parse_args(argv)
    if args.report is not None and not valid_name(args.report):
        parser.error('INVALID_REPORT')
    try:
        backend = select_backend(args.backend)
        if backend is Backend.OLLAMA and args.scenario != 'active':
            parser.error('LIVE_SCOPE_DENIED')
        if args.preflight_only:
            if backend is not Backend.OLLAMA or args.report is not None:
                parser.error('INVALID_PREFLIGHT')
            print(json.dumps(check(), ensure_ascii=False))
            return 0
        question = args.question if args.question is not None else sys.stdin.readline(4097)
        if not question or not question.strip() or len(question) > 4096:
            parser.error('INVALID_QUESTION')
        if write_requested(question):
            print('SYNTHETIC DEMO: từ chối write/restart/sudo/approval.', file=sys.stderr)
            return 2
        report = asyncio.run(drive(redact(question), args.scenario, seconds=args.timeout, backend=backend))
        text = (LIVE_LABEL if backend is Backend.OLLAMA else LABEL) + render_markdown(report, redact=redact)
        print(text, end='')
        if args.report:
            save_report(args.report, text)  # Exactly the same text as stdout.
        return 130 if report.status == 'cancelled' else (0 if report.status == 'concluded' else 1)
    except KeyboardInterrupt:
        print('SYNTHETIC DEMO: đã hủy; backend inference cancellation NOT VERIFIED.', file=sys.stderr)
        return 130
    except (BackendError, PreflightError) as error:
        print(json.dumps({'status': 'FAIL', 'error_code': error.code, 'live_inference': 'NOT RUN'}))
        return 1
    except StoreError:
        print('SYNTHETIC DEMO: không lưu report; không overwrite hoặc theo symlink.', file=sys.stderr)
        return 3
    except Exception:
        print('SYNTHETIC DEMO: lỗi an toàn; không có kết luận chắc chắn.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
