"""Local service_status demo: real Linux MCP, explicit mock/live LLM choice."""
import asyncio
from pathlib import Path
import sys
import time

from agent.cli.report_store import save_report, valid_name, StoreError
from agent.cli.__main__ import SafeParser
from agent.cli.synthetic import write_requested
from agent.coordinator import Coordinator, Report
from agent.incident_report import render_markdown, _ANSI, _CONTROL
from agent.integration_boundary import ToolBridge, BridgeBudget
from agent.integration_demo.backends import create_adapter, Backend
from agent.integration_demo.session import DemoSession, DemoError
from agent.llm.async_adapter import AsyncOllamaAdapter
from agent.llm.decision_source import AsyncDecisionSource, run_coordinator
from agent.llm.interface import ToolDefinition
from agent.llm.redaction import _clean
from agent.mcp_client import StdioMCPClient, StdioConfig
from agent.service_binding import ServiceBinding
from agent.runtime import runtime_root

ROOT = runtime_root()


class LocalServiceSession(DemoSession):
    """Reuse the existing full-session cleanup owner; no synthetic interpretation.

    Only initialization and request wiring differ from DemoSession. Its cleanup
    methods retain model, bridge and client owners until confirmed, including
    repeated cancellation. No main/DemoSession public API is changed.
    """
    def __init__(self, *, backend='mock', seconds=120):
        if backend not in ('mock', 'ollama'):
            raise ValueError('INVALID_BACKEND')
        self.deadline = time.monotonic() + seconds
        self.client = StdioMCPClient(StdioConfig(command=sys.executable,
            args=('-B', str(ROOT/'run_mcp_server.py'), '--linux-service-status'),
            allowed_tools=frozenset({'service_status'}), request_timeout=15))
        self.binding = ServiceBinding(self.client, ROOT, redact=_clean)
        tool = self.binding.trusted_tool
        if backend == 'mock':
            self.adapter = AsyncOllamaAdapter(seconds=seconds, max_calls=2,
                _worker_command=(sys.executable, '-B', '-m', 'agent.local_service_worker'))
        else:
            # Reuse the trusted pin, per-call 45s limit and RAM gate; no URL/model flags.
            self.adapter = create_adapter(Backend.OLLAMA, seconds=seconds)
        self.model = AsyncDecisionSource(self.adapter,
            (ToolDefinition(tool.name, tool.description, tool.parameters),),
            read_only=frozenset({'service_status'}))
        self.bridge = ToolBridge(self.binding, (tool,), redact=_clean,
            budget=BridgeBudget(max_calls=1, total_seconds=seconds, request_seconds=15))
        self.coordinator = Coordinator(self.model, self.bridge,
            read_only=frozenset({'service_status'}),
            scope_allows=lambda name, args: name == 'service_status' and args == {'unit': 'nginx.service'},
            interpret=self.binding.interpret, redact=_clean,
            max_tools=1, max_turns=2, seconds=seconds)
        self._cleanup_owner = None
        self._cleanup_ok = False
        self.cleanup_seconds = 8
        self.started = False

    async def run(self, question):
        if self.started:
            raise DemoError('SINGLE_USE')
        self.started = True
        try:
            async with asyncio.timeout(max(.001, self.deadline-time.monotonic())):
                await self.client.connect()
                page = await self.client.list_tools()
            descriptors = [t for t in page.tools if t.get('name') == 'service_status']
            if (page.next_cursor is not None or len(descriptors) != 1
                    or descriptors[0].get('inputSchema') != self.binding.schemas[0]
                    or descriptors[0].get('outputSchema') != self.binding.schemas[2]):
                raise DemoError('INVALID_REGISTRY')
            remaining = self.deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            self.coordinator.seconds = remaining
            await run_coordinator(self.coordinator, question)
        except TimeoutError:
            self.coordinator.report = self.coordinator.report or Report('budget_exhausted')
        except asyncio.CancelledError:
            self.coordinator.report = self.coordinator.report or Report('cancelled')
            raise
        except Exception:
            self.coordinator.report = self.coordinator.report or Report('tool_error')
        finally:
            await self.finish_cleanup()
        return self.coordinator.report


async def drive(question, backend, *, details=None):
    session = LocalServiceSession(backend=backend)
    try:
        await session.run(question)
    except (DemoError, asyncio.CancelledError):
        pass
    finally:
        # Same retained session/cleanup owner, never a request retry.
        warned = False
        while session.cleanup_pending:
            if not warned:
                print('Cleanup pending; đang giữ owner và chờ xác nhận.', file=sys.stderr)
                warned = True
            try:
                await session.finish_cleanup()
            except (DemoError, asyncio.CancelledError):
                pass
    if session.binding.last_error:
        print('Collector result code: ' + session.binding.last_error, file=sys.stderr)
    if session.model.last_error:
        print('LLM result code: ' + session.model.last_error, file=sys.stderr)
    if details is not None:
        # Only the validated canonical result supplies state fields, never LLM prose.
        details['result'] = session.binding._result
    return session.coordinator.report


def terminal_summary(report, details):
    """Plain terminal view; Markdown escaping stays in the report renderer.

    Model text is quoted/untrusted. Control sequences are stripped instead of
    interpreting Markdown/HTML or rendering peer-provided terminal instructions.
    """
    def clean(text):
        return _clean(_CONTROL.sub('', _ANSI.sub('', str(text))))

    statuses = {'concluded': 'Hoàn tất', 'limited': 'Dữ liệu bị giới hạn',
                'cancelled': 'Đã hủy', 'budget_exhausted': 'Hết ngân sách thời gian/lượt gọi',
                'tool_timeout': 'Tool hết thời gian chờ', 'disconnected': 'Mất kết nối',
                'policy_denied': 'Bị từ chối theo chính sách', 'cleanup_pending': 'Đang chờ cleanup',
                'tool_error': 'Lỗi công cụ', 'data_policy': 'Dữ liệu bị từ chối',
                'output_limit': 'Kết quả vượt giới hạn'}
    lines = ['', 'Kết quả: ' + statuses.get(report.status, clean(report.status))]
    result = details.get('result')
    if (isinstance(result, dict) and result.get('status') in ('ok', 'partial')
            and result.get('data', {}).get('unit') == 'nginx.service'):
        data = result['data']
        state = (data['load_state'], data['active_state'], data['sub_state'])
        if state == ('loaded', 'active', 'running'):
            explanation = 'Nginx đang chạy tại thời điểm kiểm tra.'
        elif data['active_state'] == 'failed':
            explanation = 'Systemd báo Nginx bị lỗi; chưa đủ dữ liệu để xác định nguyên nhân.'
        elif data['active_state'] == 'inactive':
            explanation = 'Nginx hiện không hoạt động; chưa xác định vì sao dịch vụ dừng.'
        else:
            explanation = 'Chưa thể kết luận Nginx đang chạy bình thường từ trạng thái nhận được.'
        if result['status'] == 'partial':
            lines.append('Dữ liệu chỉ thu thập được một phần; chưa thể kết luận đầy đủ.')
        lines.append('Trạng thái từ systemd: ' + explanation)
        lines.append('Quan sát: nginx.service — ' + clean(data['active_state']) + '/' + clean(data['sub_state']))
        load = 'đã nạp cấu hình dịch vụ' if data['load_state'] == 'loaded' else 'chưa xác nhận cấu hình đã được nạp'
        lines.append('  Load state: ' + clean(data['load_state']) + ' (' + load + ')')
        lines.append('  Nguồn: systemd trên máy này, đọc qua MCP service_status')
        for item in result.get('evidence', []):
            if item.get('observed_at'):
                lines.append('  Thời điểm ghi nhận (UTC): ' + clean(item['observed_at']))
                break
    else:
        lines.append('Chưa có trạng thái service được xác nhận trong phiên này.')
    if report.evidence:
        lines.append('Bằng chứng:')
        for e in report.evidence:
            lines.append('  ' + clean(e.id))
    if report.conclusion:
        lines.append('Diễn giải của AI (có thể sai, cần đối chiếu bằng chứng):')
        lines.extend('  | ' + line for line in clean(report.conclusion).splitlines())
    lines.append('Số lần gọi công cụ: ' + str(report.tool_calls))
    lines.append('Cleanup: ' + ('chưa hoàn tất' if report.cleanup_pending else 'hoàn tất'))
    lines.append('Phạm vi: chỉ đọc trạng thái nginx.service, không thay đổi hệ thống.')
    lines.append('Chưa kiểm tra: website có truy cập được hay không, HTTP health, cấu hình hoặc nguyên nhân lỗi.')
    lines.append('Mã bằng chứng giúp truy lại dữ liệu; không tự chứng minh mọi diễn giải của AI đều đúng.')
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--backend', choices=('mock', 'ollama'), default='ollama',
                        help='AI backend (default: ollama; mock for scripted testing)')
    parser.add_argument('--question')
    parser.add_argument('--report')
    args = parser.parse_args(argv)
    if args.report and not valid_name(args.report):
        parser.error('INVALID_REPORT_NAME')
    label = ('MOCK LLM (kịch bản cố định, không hiểu câu hỏi) + REAL LINUX MCP'
             if args.backend == 'mock' else 'LIVE OLLAMA + REAL LINUX MCP')
    print(label, file=sys.stderr)
    if not args.question:
        print('Bạn muốn kiểm tra gì? ', end='', file=sys.stderr, flush=True)
        question = sys.stdin.readline(4097)
    else:
        question = args.question
    if not question.strip() or len(question) > 4096:
        parser.error('INVALID_QUESTION')
    if write_requested(question):
        print('Từ chối restart/sudo/write/approval.', file=sys.stderr)
        return 2
    try:
        details = {}
        report = asyncio.run(drive(_clean(question), args.backend, details=details))
        text = '# ' + label + '\n\n' + render_markdown(report, redact=_clean)
        print(terminal_summary(report, details), end='')
        if args.report:
            path = save_report(args.report, text)
            print('Incident Report: ' + str(path.resolve()))
        return 130 if report.status == 'cancelled' else (0 if report.status == 'concluded' else 1)
    except StoreError:
        print('Không lưu report: từ chối overwrite/symlink/path không an toàn.', file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130
    except Exception:
        print('Không có kết luận an toàn; dừng phiên.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
