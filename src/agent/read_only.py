"""Canonical read-only diagnostic Agent. Real collectors by default; no arbitrary commands."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

from agent.cli.report_store import save_report, read_report, valid_name, StoreError
from agent.session_report import render_session_report
from agent.cli.__main__ import SafeParser
from agent.cli.synthetic import write_requested
from agent.coordinator import Coordinator, Report, SessionInfo
from datetime import datetime, timezone
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
from agent.service_binding import ServiceBinding, DiskBinding, DiagnosticBinding, inode_snapshot, memory_snapshot
from agent.diagnostics.journal_policy import window
from agent.diagnostics.live_anomaly_binding import LiveAnomalyBinding
from agent.diagnostics.live_verify_binding import LiveVerifyBinding
from agent.diagnostics.health_binding import HealthBinding
from agent.runtime import runtime_root

ROOT = runtime_root()
SCOPES = {'service': ('service_status',), 'disk': ('disk_usage',),
          'both': ('service_status', 'disk_usage'),
          'failed': ('list_failed_units',), 'ports': ('listening_ports',),
          'diagnostics': ('service_status', 'disk_usage', 'list_failed_units', 'listening_ports'),
          'journal': ('query_journal',), 'search': ('search_log',),
          'logs': ('query_journal','search_log'),
          'investigate': ('service_status','disk_usage','listening_ports','query_journal','search_log'),
          'anomaly': ('detect_anomaly',),
          'verify': ('verify_fix',),
          'health': ('verify_fix',),
          'oom': ('memory_oom_events',), 'auth': ('failed_logins',), 'config': ('check_config',),
          'all': ('service_status','disk_usage','list_failed_units','listening_ports','query_journal','search_log','detect_anomaly','verify_fix','memory_oom_events','failed_logins','check_config')}

# Compact model hints; authorization remains in the full trusted binding/schema.
# No keyword routing or fabricated tool proposals: the model sees every tool.
MODEL_HINTS = {
    'service_status': 'Read systemd service state; not HTTP health.',
    'disk_usage': 'Read root filesystem bytes and inodes; filesystem=root.',
    'list_failed_units': 'List failed systemd units; no arguments.',
    'listening_ports': 'Read listening sockets; optional tcp/udp and port, omit for all.',
    'query_journal': 'Read nginx journal; priority<=N, lookback_minutes, defaults 3/15.',
    'search_log': 'Literal query in nginx_journal or nginx_error; default last 15 min.',
    'detect_anomaly': 'Compare nginx error rates in two 15-minute windows; partial.',
    'verify_fix': 'Read nginx service, fixed loopback HTTP and recent errors; no repair.',
    'memory_oom_events': 'Read available RAM and recent kernel OOM events; no arguments.',
    'failed_logins': 'Read recent failed authentications; pseudonymized, no arguments.',
    'check_config': 'Check nginx syntax in read-only sandbox; profile=nginx; no reload.',
}
ACTION_TOOLS=('service_status','restart_service')


class CanonicalRouter:
    """Own fixed separate stdio profiles; never activate an unselected backend.

    All bindings share application session/trace, with per-call logical/attempt IDs.
    A failed disconnect retains its underlying SDK owner and is joined again.
    """
    def __init__(self, scope, root=ROOT,*,action_session=None):
        if scope not in SCOPES and scope!='actions':
            raise ValueError('INVALID_SCOPE')
        self.bindings = {}
        self.discovered = False
        self._closed = False
        self._attempted = set()
        self._ready = set()
        self._active = False
        if scope=='actions' and action_session is None:raise ValueError('OPERATOR_SESSION_REQUIRED')
        if action_session is not None and (scope not in ('all','actions') or __import__('re').fullmatch('[0-9a-f]{32}',action_session) is None):
            raise ValueError('INVALID_ACTION_SESSION')
        session, trace = action_session or uuid4().hex, uuid4().hex
        self.session_id,self.trace_id=session,trace
        names=ACTION_TOOLS if scope=='actions' else SCOPES[scope]+(('restart_service',) if scope=='all' and action_session else ())
        journal_window = window()
        for name in names:
            flag = '--linux-service-status' if name == 'service_status' else '--linux-disk-usage'
            args = ('-B', str(root/'run_mcp_server.py'), flag)
            if name in ('list_failed_units', 'listening_ports', 'query_journal', 'search_log', 'detect_anomaly', 'verify_fix','memory_oom_events','failed_logins','check_config'):
                args = ('-B', str(Path(__file__).with_name('diagnostic_server.py')), '--tool', name)
            if name=='restart_service':args=('-B',str(Path(__file__).with_name('proposal_server.py')))
            client = StdioMCPClient(StdioConfig(command=sys.executable,
                args=args,
                allowed_tools=frozenset({name}), request_timeout=15))
            cls = ServiceBinding if name == 'service_status' else DiskBinding
            from agent.restart_proposal import RestartBinding
            if name=='restart_service':binding=RestartBinding(client,root,redact=_clean,evidence=self.proposal_evidence)
            elif name=='verify_fix':binding=(HealthBinding(client,root,redact=_clean) if scope in ('all','health') else LiveVerifyBinding(client,root,redact=_clean))
            elif name=='detect_anomaly':binding=LiveAnomalyBinding(client,root,redact=_clean)
            elif name in ('list_failed_units','listening_ports','query_journal','search_log','memory_oom_events','failed_logins','check_config'):
                binding=DiagnosticBinding(client,root,redact=_clean,tool=name)
            else:binding=cls(client,root,redact=_clean)
            binding.session_id, binding.trace_id = session, trace
            if name in ('query_journal','search_log','detect_anomaly'):
                binding.window = dict(journal_window)
            self.bindings[name] = binding
        self._snapshot_stored=False

    def proposal_evidence(self):
        from agent.diagnostics.service_verification import validate_snapshot
        from agent.anomaly import _time
        import mock_mcp_server as contracts
        b=self.bindings['service_status'];r=b._result
        if r is None or r['status']!='ok' or r['truncated']:raise MCPClientError('INSUFFICIENT_EVIDENCE')
        validate_snapshot(r,'nginx.service')
        if not 0<=(_time(contracts.current_time())-_time(r['completed_at'])).total_seconds()<=60:
            raise MCPClientError('INSUFFICIENT_EVIDENCE')
        if not self._snapshot_stored:
            from control_plane.command import store_evidence
            store_evidence(r,b.trace_id);self._snapshot_stored=True
        return list(b.evidence)

    @property
    def _owner(self):
        return self if any(b.client._owner is not None for b in self.bindings.values()) else None

    @property
    def tools(self):
        return tuple(b.trusted_tool for b in self.bindings.values())

    async def connect(self):
        # Advertise application-owned schemas without starting eleven servers.
        # The selected peer must still pass discovery before any tools/call.
        if self.discovered or self._closed:
            raise MCPClientError('BUSY')
        self.discovered = True

    async def call_tool(self, name, arguments):
        if self._closed or not self.discovered or name not in self.bindings:
            raise MCPClientError('TOOL_NOT_ALLOWED')
        binding = self.bindings[name]
        if not binding.business_validator.is_valid(arguments):
            raise MCPClientError('TOOL_NOT_ALLOWED')
        if self._active:
            raise MCPClientError('BUSY')
        self._active = True
        try:
            if name not in self._ready:
                if name in self._attempted:
                    raise MCPClientError('INVALID_STATE')  # No reconnect after failure/cancellation.
                self._attempted.add(name)
                await binding.client.connect()
                page = await binding.client.list_tools()
                descriptors = [t for t in page.tools if t.get('name') == name]
                if (page.next_cursor is not None or len(descriptors) != 1
                        or descriptors[0].get('inputSchema') != binding.schemas[0]
                        or descriptors[0].get('outputSchema') != binding.schemas[2]):
                    raise MCPClientError('MALFORMED_RESULT')
                self._ready.add(name)
            return await binding.call_tool(name, arguments)
        finally:
            self._active = False

    def interpret(self, message):
        if message.tool_name not in self.bindings:
            raise MCPClientError('TOOL_NOT_ALLOWED')
        return self.bindings[message.tool_name].interpret(message)

    async def disconnect(self):
        self._closed = True
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
    def __init__(self, *, scope='both', backend='ollama', seconds=120,action_session=None):
        if (scope not in SCOPES and scope!='actions') or backend not in ('mock', 'ollama'):
            raise ValueError('INVALID_CONFIG')
        self.deadline = time.monotonic() + seconds
        self.client = CanonicalRouter(scope,action_session=action_session)
        names = frozenset(self.client.bindings)
        proposals=frozenset({'restart_service'})&names
        reads=names-proposals
        max_dispatches = min(len(names), 5)  # Preserve the existing six-turn ceiling.
        turns = max_dispatches + 1
        if backend == 'ollama':
            self.adapter = _LiveAdapter(OllamaConfig(max_tool_calls=max_dispatches,structured_conclusion=True), seconds=seconds, max_calls=2)
        else:
            self.adapter = AsyncOllamaAdapter(OllamaConfig(max_tool_calls=max_dispatches,structured_conclusion=True), seconds=seconds, max_calls=2,
                _worker_command=(sys.executable, '-B', '-m', 'agent.read_only_mock_worker'))
        tools = self.client.tools
        self.model = AsyncDecisionSource(self.adapter,
            tuple(ToolDefinition(t.name, MODEL_HINTS.get(t.name,t.description), t.parameters) for t in tools),
            read_only=reads, conclude_on_last_request=not proposals,proposal_only=proposals)
        self.bridge = ToolBridge(self.client, tools, redact=_clean,
            budget=BridgeBudget(max_calls=max_dispatches, total_seconds=seconds, request_seconds=15),proposal_only=proposals)
        self.coordinator = Coordinator(self.model, self.bridge, read_only=reads,proposal_only=proposals,
            scope_allows=lambda name, args: name in self.client.bindings
                and self.client.bindings[name].business_validator.is_valid(args),
            interpret=self.client.interpret, redact=_clean,
            max_tools=max_dispatches, max_turns=turns, seconds=seconds)
        self._cleanup_owner = None
        self._cleanup_ok = False
        self.cleanup_seconds = 8
        self.started = False
        self.backend=backend

    async def run(self, question):
        if self.started:
            raise DemoError('SINGLE_USE')
        self.started = True
        started_at=datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
        started_tick=time.monotonic()
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
            completed_at=datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
            elapsed_ms=max(0,int((time.monotonic()-started_tick)*1000))
            try:
                await self.finish_cleanup()
            finally:
                if self.coordinator.report is not None:
                    self.coordinator.report=replace(self.coordinator.report,
                        state_trace=self.coordinator.state_history,
                        session=SessionInfo(self.client.session_id,self.client.trace_id,
                                            started_at,completed_at,elapsed_ms,self.backend))
        return self.coordinator.report


def terminal_summary(report, results, *, show_model_text=False):
    # Verified collector fields are the default answer. Keep original model text
    # and citations in the report; never silently rewrite its semantic meaning.
    view = report if show_model_text else replace(report, conclusion='')
    text = service_summary(view, {'result': results.get('service_status')})
    if report.status=='approval_required':text=text.replace('Kết quả: approval_required','Kết quả: Chờ duyệt đề xuất — chưa thực thi')
    if report.conclusion and not show_model_text:
        text += 'Diễn giải AI chưa được xác minh; dùng --show-model-text để xem riêng.\n'
    service=results.get('service_status')
    if isinstance(service,dict) and service.get('status')=='ok':
        from agent.diagnostics.service_snapshot import parse_snapshot
        _,details=parse_snapshot(service['evidence'][0]['excerpt'],service['data']['unit'])
        if details is not None:
            status=details['exec_main_status'];code=details['exec_main_code']
            lines=[]
            if code==1 and status is not None:lines.append('  Exit status tiến trình chính: '+str(status)+' (lần kết thúc gần nhất; không phải HTTP health)')
            elif code in (2,3) and status is not None:lines.append('  Tiến trình chính kết thúc bởi signal: '+str(status))
            else:lines.append('  Exit status tiến trình chính: chưa có lần kết thúc được xác nhận.')
            if details['restart_count'] is not None:lines.append('  Số tự động restart do systemd ghi nhận: '+str(details['restart_count'])+' (không phải tổng restart thủ công)')
            anchor = 'Bằng chứng:' if 'Bằng chứng:' in text else 'Số lần gọi công cụ:'
            text=text.replace(anchor,'\n'.join(lines)+'\n'+anchor,1)
    disk = results.get('disk_usage')
    if isinstance(disk, dict) and disk.get('status') in ('ok', 'partial'):
        data = disk['data']
        lines = ['Ổ đĩa / trong namespace của collector:',
                 f"  Đã sử dụng: {data['used_percent']:.2f}%",
                 f"  Còn khả dụng cho user: {data['available_bytes'] / (1024**3):.2f} GiB",
                 '  Nguồn: os.statvfs qua MCP disk_usage']
        inodes=inode_snapshot(disk)
        if inodes is not None:
            lines.append('  Inode còn khả dụng: '+str(inodes['available'])+' / '+str(inodes['total']))
            if inodes['used_percent'] is not None:lines.append(f"  Inode đã sử dụng: {inodes['used_percent']:.2f}% (khác dung lượng byte)")
        if disk['status'] == 'partial' or disk['truncated']:
            lines.append('  Dữ liệu bị giới hạn; không coi đây là quan sát đầy đủ.')
        text = text.replace('Bằng chứng:', '\n'.join(lines)+'\nBằng chứng:', 1)
        if 'service_status' not in results:
            text = text.replace('Chưa có trạng thái service được xác nhận trong phiên này.\n', '')
    extra = []
    proposal=results.get('restart_service')
    if isinstance(proposal,dict) and proposal.get('status') in ('ok','approval_required'):
        d=proposal['data'];extra.extend(['Đề xuất restart nginx.service: '+d['proposal_status'],
            '  Action ID: '+d['action_id'],'  Hết hạn (UTC): '+d['expires_at'],
            '  MCP không duyệt hoặc thực thi restart; cần operator xác nhận riêng trên TTY.'])
        if d['executed'] is None:extra.append('  Kết quả thực thi chưa xác định; không retry.')
        elif d['executed']:extra.append('  Job đã hoàn tất; chưa có nghĩa đã sửa được nguyên nhân lỗi.')
        else:extra.append('  Đề xuất này chưa thực hiện restart.')
    # Fixed application text only; never print an OS diagnostic or peer message.
    error_text={
        'PERMISSION_DENIED':'Không đủ quyền hoặc sandbox không cho phép kiểm tra.',
        'UNSUPPORTED':'Nguồn hoặc runtime hiện chưa hỗ trợ phép kiểm tra này.',
        'OUTPUT_LIMIT':'Dữ liệu vượt giới hạn; chưa có kết quả đầy đủ.',
        'TIMEOUT':'Hết thời gian chờ; chưa xác định được kết quả thực thi.',
        'EXECUTION_UNKNOWN':'Chưa xác định được kết quả thực thi.',
    }
    for name,result in results.items():
        if isinstance(result,dict) and result.get('status')=='error':
            code=result.get('error_code')
            extra.append(name+': '+error_text.get(code,'Phép kiểm tra bị từ chối; chưa có kết quả xác minh.'))
            if name=='check_config':
                extra.append('  Chưa thể kết luận cấu hình đúng hay sai; không chạy lại ngoài sandbox.')
    service=results.get('service_status')
    if isinstance(service,dict) and service.get('status')=='ok' and service['data']['unit']!='nginx.service':
        d=service['data'];extra.append('Dịch vụ '+d['unit']+': '+d['active_state']+'/'+d['sub_state']+'; load='+d['load_state'])
        extra.append('  Nguồn systemd; chưa xác minh ứng dụng hoạt động tốt hay nguyên nhân lỗi.')
    for name,label in (('memory_oom_events','Sự kiện OOM hệ thống'),('failed_logins','Đăng nhập thất bại hệ thống')):
        result=results.get(name)
        if isinstance(result,dict) and result.get('status')=='partial':
            extra.append(label+': '+str(result['data']['count'])+' bản ghi nhìn thấy.')
            extra.extend('  Nguồn '+c['source']+': '+c['status'] for c in result['data']['coverage'])
            extra.append('  Phạm vi nguồn đọc bị giới hạn; 0 bản ghi không chứng minh không có sự kiện. Danh tính/IP gốc không được xuất.')
            if name=='memory_oom_events':
                ram=memory_snapshot(result)
                extra.append(f"  RAM khả dụng: {ram['MemAvailable']/(1024**3):.2f} / {ram['MemTotal']/(1024**3):.2f} GiB")
    config=results.get('check_config')
    if isinstance(config,dict) and config.get('status')=='ok':
        extra.append('Kiểm tra cú pháp Nginx: '+('hợp lệ' if config['data']['valid'] else 'không hợp lệ')+'.')
        extra.append('  nginx -t trong sandbox chỉ đọc; không reload/restart, không thay cấu hình.')
    failed = results.get('list_failed_units')
    if isinstance(failed, dict) and failed.get('status') in ('ok', 'partial'):
        data = failed['data']
        extra.append('Dịch vụ/unit đang failed: '+str(data['count']))
        extra.extend('  '+r['unit']+' — failed' for r in data['failed_units'])
        extra.append('  Nguồn: systemctl; mô tả được giữ kín; không tự khởi động lại.')
    ports = results.get('listening_ports')
    if isinstance(ports, dict) and ports.get('status') in ('ok', 'partial'):
        data = ports['data']
        extra.append('Socket đang lắng nghe nhìn thấy: '+str(data['count']))
        extra.extend('  '+r['protocol'].upper()+' '+('['+r['address']+']' if ':' in r['address'] else r['address'])+':'+str(r['port'])
                     for r in data['ports'])
        extra.append('  Không xác định tiến trình sở hữu, không chứng minh HTTP hoạt động.')
    for tool in ('query_journal','search_log'):
        result = results.get(tool)
        if isinstance(result,dict) and result.get('status')=='partial':
            data = result['data']
            records = data['entries' if tool=='query_journal' else 'matches']
            extra.append(('Journal Nginx (cửa sổ/mức lọc trong evidence)' if tool=='query_journal'
                          else 'Tìm literal trong '+data['source_id']+': '+data['query'])+': '+str(data['count'])+' bản ghi nhìn thấy')
            extra.extend('  '+e['timestamp']+' — '+e['message'] for e in records)
            extra.append('  Nội dung log gốc được giữ kín. Chưa xác nhận đủ quyền/lịch sử; 0 bản ghi không chứng minh không có lỗi.')
    anomaly=results.get('detect_anomaly')
    if isinstance(anomaly,dict) and anomaly.get('status')=='partial':
        data=anomaly['data'];events=anomaly['evidence']
        extra.append('So sánh tần suất lỗi journal Nginx — hai cửa sổ 15 phút:')
        if data['insufficient_signals']:
            extra.append('  Chưa đủ baseline để so sánh; không kết luận có hoặc không có bất thường.')
        elif data['anomalies']:
            extra.extend('  Dấu hiệu tăng theo quy tắc local; lỗi nhìn thấy ở cửa sổ mới: '+str(f['observed_count'])
                         for f in data['anomalies'])
        else:
            extra.append('  Chưa chạm ngưỡng trên các bản ghi nhìn thấy; không chứng minh hệ thống bình thường.')
        extra.append('  Ngưỡng local chưa hiệu chuẩn: ít nhất 5 bản ghi lỗi và ít nhất 2 lần baseline khác 0.')
        extra.append('  Chưa xác nhận đủ lịch sử/quyền; không kết luận nguyên nhân hoặc đã sửa lỗi.')
    verification=results.get('verify_fix')
    if isinstance(verification,dict) and verification.get('status')=='partial':
        check=next(c for c in verification['data']['checks'] if c['check_type']=='service_active')
        label={'pass':'đang chạy tại thời điểm kiểm tra',
               'fail':'không hoạt động theo tiêu chí kiểm tra',
               'unknown':'chưa xác định được trạng thái ổn định'}[check['result']]
        extra.append('Kiểm tra lại Nginx (service_active): '+label+'.')
        extra.append('  Nguồn: systemd qua collector service_status; một lần đọc mới, không polling/retry.')
        extra.append('  Không có action/baseline sửa lỗi; chưa xác minh đã sửa thành công.')
        if len(verification['data']['checks'])==1:
            extra.append('  Chưa kiểm tra HTTP health hoặc lỗi mới trong profile này.')
        for item in verification['data']['checks']:
            if item['check_type']!='service_active':
                kind={'http_health':'HTTP tại 127.0.0.1:80','no_new_failures':'Lỗi journal mới'}[item['check_type']]
                state={'pass':'đạt kiểm tra này','fail':'không đạt','unknown':'chưa xác minh đầy đủ'}[item['result']]
                extra.append('  '+kind+': '+state+'.')
                if item['check_type']=='http_health':
                    extra.append('    Chỉ HEAD / trên loopback; không kiểm tra truy cập từ Internet hoặc nội dung ứng dụng.')
                else:
                    extra.append('    Chỉ các bản ghi trong cửa sổ đọc; không bảo đảm đủ quyền/lịch sử.')
    if extra:
        if 'service_status' not in results:
            text = text.replace('Chưa có trạng thái service được xác nhận trong phiên này.\n', '')
        # Empty observations have no evidence heading. Keep the explanation of
        # incomplete visibility instead of silently dropping it with the heading.
        anchor = 'Bằng chứng:' if 'Bằng chứng:' in text else 'Số lần gọi công cụ:'
        text = text.replace(anchor, '\n'.join(extra)+'\n'+anchor, 1)
    text = text.replace('Phạm vi: chỉ đọc trạng thái nginx.service, không thay đổi hệ thống.',
                        'Phạm vi: chỉ đọc các tool được cấp quyền, không thay đổi hệ thống.')
    if isinstance(proposal,dict) and proposal.get('status') in ('ok','approval_required'):
        text=text.replace('Phạm vi: chỉ đọc các tool được cấp quyền, không thay đổi hệ thống.',
                          'Phạm vi: đọc dữ liệu và quản lý đề xuất; MCP không thực thi thay đổi dịch vụ.')
    if isinstance(verification,dict) and verification.get('status')=='partial' and any(
            c['check_type']=='http_health' for c in verification['data']['checks']):
        text=text.replace('Chưa kiểm tra: website có truy cập được hay không, HTTP health, cấu hình hoặc nguyên nhân lỗi.',
                          'Giới hạn: chưa xác minh HTTP từ bên ngoài, nội dung ứng dụng hoặc nguyên nhân lỗi.')
    elif 'service_status' not in results:
        text=text.replace('Chưa kiểm tra: website có truy cập được hay không, HTTP health, cấu hình hoặc nguyên nhân lỗi.',
                          'Giới hạn: kết quả chỉ phản ánh nguồn và thời điểm đọc, không chứng minh toàn hệ thống khỏe.')
    return text.replace('Kết quả: insufficient_evidence', 'Kết quả: Chưa đủ bằng chứng')


async def drive(question, backend, scope,*,action_session=None):
    session = ReadOnlySession(scope=scope, backend=backend,action_session=action_session)
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


async def action_status(session_id,action_id):
    """One read-only canonical status query, no model or action dispatch."""
    client=CanonicalRouter('actions',action_session=session_id)
    binding=client.bindings['restart_service'];binding.action_id=action_id
    cancelled=False
    try:
        await client.connect()
        await client.call_tool('restart_service',dict(unit='nginx.service',mode='status'))
    finally:
        while client._owner is not None:
            try:await client.disconnect()
            except asyncio.CancelledError:cancelled=True
            except MCPClientError as e:
                if e.code!='CLEANUP_PENDING':raise
        if cancelled:raise asyncio.CancelledError()
    return Report('limited',tool_calls=1,execution_state='response_received'),{'restart_service':binding._result}


def main(argv=None):
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--version',action='version',version='Linux Log Agent 0.1.0 (canonical schema 1.0.0)')
    parser.add_argument('--view-report',help='Read a private saved report by basename; no model or MCP')
    parser.add_argument('--show-trace',action='store_true',help='Show application state trace and session IDs')
    parser.add_argument('--backend', choices=('mock', 'ollama'), default='ollama')
    parser.add_argument('--scope', choices=tuple(SCOPES)+('actions',), default='all')
    parser.add_argument('--show-model-text', action='store_true',
                        help='Show unverified model prose in addition to collector facts')
    parser.add_argument('--question')
    parser.add_argument('--report', help='Export one question to a new Markdown report')
    parser.add_argument('--action-session',help='VM operator-opened session: permit non-executing restart proposals')
    parser.add_argument('--action-status',help='Read one existing Action ID using --action-session; no inference')
    parser.add_argument('--once', action='store_true', help='Exit after one question')
    parser.add_argument('--doctor', action='store_true', help='Check runtime/model metadata without inference or MCP startup')
    args = parser.parse_args(argv)
    if args.view_report is not None:
        if args.question is not None or args.report or args.doctor or args.action_session or args.action_status or args.show_trace or args.scope!='all':
            parser.error('REPORT_VIEW_DOES_NOT_DIAGNOSE')
        try:
            text=read_report(args.view_report)
            if _clean(text)!=text:raise StoreError('UNSAFE_REPORT_CONTENT')
            print(text,end='')
            return 0
        except Exception:
            print('Không mở report: tên/path/quyền hoặc nội dung không an toàn.',file=sys.stderr)
            return 3
    if args.action_session is not None or args.scope=='actions':
        try:
            from agent.diagnostics import read_gateway
            import re
            if (args.action_session is None or re.fullmatch('[0-9a-f]{32}',args.action_session) is None
                    or args.scope not in ('all','actions') or not read_gateway.enabled()):raise ValueError()
        except Exception:parser.error('VM_READER_AND_OPERATOR_SESSION_REQUIRED')
    if args.action_status is not None:
        import re
        if (args.scope!='actions' or args.action_session is None or args.question is not None or args.doctor
                or re.fullmatch('[0-9a-f]{32}',args.action_status) is None):parser.error('INVALID_ACTION_STATUS')
    if args.doctor:
        if args.question is not None or args.report or args.action_session:
            parser.error('DOCTOR_DOES_NOT_DIAGNOSE')
        from agent.runtime import doctor
        text, code = doctor(args.backend, tools=SCOPES[args.scope])
        print(text,end='')
        return code
    if args.report and not valid_name(args.report):
        parser.error('INVALID_REPORT_NAME')
    label = ('NO MODEL — canonical status read' if args.action_status else
             'LIVE OLLAMA' if args.backend == 'ollama' else 'MOCK LLM — scripted, không hiểu câu hỏi')+' + REAL LINUX MCP'
    print(label, file=sys.stderr)
    shown=ACTION_TOOLS if args.scope=='actions' else SCOPES[args.scope]+(('restart_service',) if args.action_session else ())
    print('Phạm vi: '+', '.join(shown), file=sys.stderr)
    if len(shown)>5:
        print(f'Có {len(shown)} tool; mỗi câu hỏi tối đa 5 lượt tool, 2 request AI, 120 giây.', file=sys.stderr)
    interactive = args.question is None and sys.stdin.isatty() and not args.once and not args.report and not args.action_status
    last_code = 0
    while True:
        if args.action_status:
            question='Application-owned status read'
        elif args.question is None:
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
        if write_requested(question) and args.action_session is None:
            print('Từ chối thao tác ghi/restart hoặc cấp approval.', file=sys.stderr)
            if interactive:
                last_code = 2
                continue
            return 2
        try:
            options={'action_session':args.action_session} if args.action_session else {}
            report, results = asyncio.run(action_status(args.action_session,args.action_status) if args.action_status else
                                         drive(_clean(question), args.backend, args.scope,**options))
            print(terminal_summary(report, results, show_model_text=args.show_model_text), end='')
            if args.show_trace:
                print('Trace: '+' → '.join(report.state_trace) if report.state_trace else 'Trace: chưa có state transition được quan sát.')
                if report.session:
                    print('Session: '+report.session.session_id+'; Trace ID: '+report.session.trace_id)
                    print('Thời gian chẩn đoán: '+format(report.session.elapsed_ms/1000,'.3f')+' giây; cleanup tính riêng.')
            if args.report:
                # Model gets a small projection. Persist the full already-validated
                # safe canonical ledger, including omitted events/provenance.
                content=render_session_report(report,results,label)
                print('Incident Report: '+str(save_report(args.report, content).resolve()))
            # A successful status read says nothing about whether the job succeeded.
            status_read = args.action_status and results.get('restart_service',{}).get('status')=='ok'
            last_code = 130 if report.status == 'cancelled' else (0 if status_read or report.status in ('concluded','approval_required') else 1)
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
