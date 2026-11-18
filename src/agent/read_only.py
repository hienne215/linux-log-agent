"""Canonical read-only diagnostic Agent. Real collectors by default; no arbitrary commands."""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

from agent.cli.report_store import save_report, valid_name, StoreError
from agent.cli.synthetic import write_requested
from agent.coordinator import Coordinator, Report
from agent.incident_report import render_markdown
from agent.integration_boundary import ToolBridge, BridgeBudget
from agent.integration_demo.backends import _LiveAdapter
from agent.integration_demo.session import DemoSession, DemoError
from agent.llm.async_adapter import AsyncOllamaAdapter
from agent.llm.decision_source import AsyncDecisionSource, run_coordinator
from agent.llm.interface import ToolDefinition
from agent.llm.ollama import OllamaConfig
from agent.llm.redaction import _clean
from agent.local_service import terminal_summary as service_summary
from agent.mcp_client import StdioConfig, StdioMCPClient, MCPClientError
from agent.service_binding import ServiceBinding, DiskBinding, DiagnosticBinding

ROOT = Path(__file__).resolve().parents[2]
SCOPES = {'service': ('service_status',), 'disk': ('disk_usage',),
          'both': ('service_status', 'disk_usage'),
          'failed': ('list_failed_units',), 'ports': ('listening_ports',),
          'diagnostics': ('service_status', 'disk_usage', 'list_failed_units', 'listening_ports')}


class CanonicalRouter:
    """Own fixed separate stdio profiles; never activate an unselected backend.

    All bindings share application session/trace, with per-call logical/attempt IDs.
    A failed disconnect retains its underlying SDK owner and is joined again.
    """
    def __init__(self, scope, root=ROOT):
        if scope not in SCOPES:
            raise ValueError('INVALID_SCOPE')
        self.bindings = {}
        self.discovered = False
        session, trace = uuid4().hex, uuid4().hex
        for name in SCOPES[scope]:
            flag = '--linux-service-status' if name == 'service_status' else '--linux-disk-usage'
            args = ('-B', str(root/'run_mcp_server.py'), flag)
            if name in ('list_failed_units', 'listening_ports'):
                args = ('-B', str(root/'src/agent/diagnostic_server.py'), '--tool', name)
            client = StdioMCPClient(StdioConfig(command=sys.executable,
                args=args,
                allowed_tools=frozenset({name}), request_timeout=15))
            cls = ServiceBinding if name == 'service_status' else DiskBinding
            binding = (DiagnosticBinding(client, root, redact=_clean, tool=name)
                       if name in ('list_failed_units', 'listening_ports')
                       else cls(client, root, redact=_clean))
            binding.session_id, binding.trace_id = session, trace
            self.bindings[name] = binding

    @property
    def _owner(self):
        return self if any(b.client._owner is not None for b in self.bindings.values()) else None

    @property
    def tools(self):
        return tuple(b.trusted_tool for b in self.bindings.values())

    async def connect(self):
        if self.discovered:
            raise MCPClientError('BUSY')
        for name, binding in self.bindings.items():
            await binding.client.connect()
            page = await binding.client.list_tools()
            descriptors = [t for t in page.tools if t.get('name') == name]
            if (page.next_cursor is not None or len(descriptors) != 1
                    or descriptors[0].get('inputSchema') != binding.schemas[0]
                    or descriptors[0].get('outputSchema') != binding.schemas[2]):
                raise MCPClientError('MALFORMED_RESULT')
        self.discovered = True

    async def call_tool(self, name, arguments):
        if not self.discovered or name not in self.bindings:
            raise MCPClientError('TOOL_NOT_ALLOWED')
        return await self.bindings[name].call_tool(name, arguments)

    def interpret(self, message):
        if message.tool_name not in self.bindings:
            raise MCPClientError('TOOL_NOT_ALLOWED')
        return self.bindings[message.tool_name].interpret(message)

    async def disconnect(self):
        failed = False
        cancelled = False
        for binding in self.bindings.values():
            try:
                await binding.disconnect()
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                failed = True
        if cancelled:
            raise asyncio.CancelledError
        if failed:
            raise MCPClientError('CLEANUP_PENDING')


class ReadOnlySession(DemoSession):
    """Reuse full-session model/executor/client cleanup; bounded dispatches/turns selected by trusted scope."""
    def __init__(self, *, scope='both', backend='ollama', seconds=120):
        if scope not in SCOPES or backend not in ('mock', 'ollama'):
            raise ValueError('INVALID_CONFIG')
        self.deadline = time.monotonic() + seconds
        self.client = CanonicalRouter(scope)
        names = frozenset(self.client.bindings)
        turns = len(names) + 1
        if backend == 'ollama':
            self.adapter = _LiveAdapter(OllamaConfig(max_tool_calls=len(names)), seconds=seconds, max_calls=2)
        else:
            self.adapter = AsyncOllamaAdapter(OllamaConfig(max_tool_calls=len(names)), seconds=seconds, max_calls=2,
                _worker_command=(sys.executable, '-B', '-m', 'agent.read_only_mock_worker'))
        tools = self.client.tools
        self.model = AsyncDecisionSource(self.adapter,
            tuple(ToolDefinition(t.name, t.description, t.parameters) for t in tools), read_only=names)
        self.bridge = ToolBridge(self.client, tools, redact=_clean,
            budget=BridgeBudget(max_calls=len(names), total_seconds=seconds, request_seconds=15))
        self.coordinator = Coordinator(self.model, self.bridge, read_only=names,
            scope_allows=lambda name, args: name in self.client.bindings
                and self.client.bindings[name].business_validator.is_valid(args),
            interpret=self.client.interpret, redact=_clean,
            max_tools=len(names), max_turns=turns, seconds=seconds)
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
            self.coordinator.seconds = max(.001, self.deadline-time.monotonic())
            if time.monotonic() >= self.deadline:
                raise TimeoutError
            await run_coordinator(self.coordinator, _clean(question))
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


def terminal_summary(report, results):
    text = service_summary(report, {'result': results.get('service_status')})
    disk = results.get('disk_usage')
    if isinstance(disk, dict) and disk.get('status') in ('ok', 'partial'):
        data = disk['data']
        lines = ['Ổ đĩa / trong namespace của collector:',
                 f"  Đã sử dụng: {data['used_percent']:.2f}%",
                 f"  Còn khả dụng cho user: {data['available_bytes'] / (1024**3):.2f} GiB",
                 '  Nguồn: os.statvfs qua MCP disk_usage']
        if disk['status'] == 'partial' or disk['truncated']:
            lines.append('  Dữ liệu bị giới hạn; không coi đây là quan sát đầy đủ.')
        text = text.replace('Bằng chứng:', '\n'.join(lines)+'\nBằng chứng:', 1)
        if 'service_status' not in results:
            text = text.replace('Chưa có trạng thái service được xác nhận trong phiên này.\n', '')
    extra = []
    failed = results.get('list_failed_units')
    if isinstance(failed, dict) and failed.get('status') in ('ok', 'partial'):
        data = failed['data']
        extra.append('Dịch vụ/unit đang failed: '+str(data['count']))
        extra.extend('  '+r['unit']+' — failed' for r in data['failed_units'])
        extra.append('  Nguồn: systemctl; mô tả được giữ kín; không tự khởi động lại.')
    ports = results.get('listening_ports')
    if isinstance(ports, dict) and ports.get('status') in ('ok', 'partial'):
        data = ports['data']
        extra.append('Socket TCP lắng nghe cổng 80: '+str(data['count']))
        extra.extend('  '+('['+r['address']+']' if ':' in r['address'] else r['address'])+':80'
                     for r in data['ports'])
        extra.append('  Không xác định tiến trình sở hữu, không chứng minh HTTP hoạt động.')
    if extra:
        if 'service_status' not in results:
            text = text.replace('Chưa có trạng thái service được xác nhận trong phiên này.\n', '')
        text = text.replace('Bằng chứng:', '\n'.join(extra)+'\nBằng chứng:', 1)
    text = text.replace('Phạm vi: chỉ đọc trạng thái nginx.service, không thay đổi hệ thống.',
                        'Phạm vi: chỉ đọc các tool được cấp quyền, không thay đổi hệ thống.')
    return text


async def drive(question, backend, scope):
    session = ReadOnlySession(scope=scope, backend=backend)
    try:
        await session.run(question)
    except asyncio.CancelledError:
        pass
    finally:
        warned = False
        while session.cleanup_pending:
            if not warned:
                print('Cleanup chưa xác nhận; giữ owner để tiếp tục đóng.', file=sys.stderr)
                warned = True
            try:
                await session.finish_cleanup()
            except (DemoError, asyncio.CancelledError):
                pass
    report = session.coordinator.report
    errors = [b.last_error for b in session.client.bindings.values() if b.last_error]
    if session.model.last_error:
        errors.append(session.model.last_error)
        if session.model.last_reason:
            print('Chi tiết policy: '+session.model.last_reason, file=sys.stderr)
    for code in errors:
        print('Mã lỗi an toàn: '+code, file=sys.stderr)
    return report, {n: b._result for n,b in session.client.bindings.items() if b._result is not None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--backend', choices=('mock', 'ollama'), default='ollama')
    parser.add_argument('--scope', choices=tuple(SCOPES), default='both')
    parser.add_argument('--question')
    parser.add_argument('--report', help='Export one question to a new Markdown report')
    parser.add_argument('--once', action='store_true', help='Exit after one question')
    args = parser.parse_args(argv)
    if args.report and not valid_name(args.report):
        parser.error('INVALID_REPORT_NAME')
    label = ('LIVE OLLAMA' if args.backend == 'ollama' else 'MOCK LLM — scripted, không hiểu câu hỏi')+' + REAL LINUX MCP'
    print(label, file=sys.stderr)
    print('Phạm vi: '+', '.join(SCOPES[args.scope]), file=sys.stderr)
    interactive = args.question is None and sys.stdin.isatty() and not args.once and not args.report
    last_code = 0
    while True:
        if args.question is None:
            print('Bạn muốn kiểm tra gì? ' + ('(gõ thoát để kết thúc) ' if interactive else ''),
                  end='', file=sys.stderr, flush=True)
            try:
                question = sys.stdin.readline(4097)
            except KeyboardInterrupt:
                return 130
            if not question:
                return last_code
        else:
            question = args.question
        if interactive and question.strip().casefold() in {'thoát', 'thoat', 'exit', 'quit', 'q'}:
            return last_code
        if not question.strip() or len(question) > 4096:
            parser.error('INVALID_QUESTION')
        if write_requested(question):
            print('Từ chối thao tác ghi/restart hoặc cấp approval.', file=sys.stderr)
            if interactive:
                last_code = 2
                continue
            return 2
        try:
            report, results = asyncio.run(drive(_clean(question), args.backend, args.scope))
            print(terminal_summary(report, results), end='')
            if args.report:
                print('Incident Report: '+str(save_report(args.report, '# '+label+'\n\n'+render_markdown(report, redact=_clean)).resolve()))
            last_code = 130 if report.status == 'cancelled' else (0 if report.status == 'concluded' else 1)
            if not interactive or report.cleanup_pending or report.status == 'cancelled':
                return last_code
        except StoreError:
            print('Không lưu report: tên/path/symlink/overwrite không an toàn.', file=sys.stderr)
            return 3
        except KeyboardInterrupt:
            return 130
        except Exception:
            print('Không có kết luận an toàn; dừng phiên.', file=sys.stderr)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
