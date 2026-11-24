"""Completion regressions: synthetic SDK, safe reports and observed service details.

No live model, private logs, helper, real systemd call or service modification.
"""
import asyncio
from contextlib import redirect_stdout,redirect_stderr
from dataclasses import replace
import io,json,os,sys,tempfile,unittest,subprocess
from pathlib import Path
from unittest.mock import AsyncMock,patch
import mock_mcp_server as contracts
from agent.coordinator import Report,SessionInfo,Decision
from agent.incident_report import render_markdown,ReportError
from agent.cli.report_store import read_report,save_report,StoreError
from agent.read_only import main,ReadOnlySession,terminal_summary
from agent.session_report import render_session_report
from agent.mcp_client import StdioMCPClient,StdioConfig
from src.mcp_server import service_status
from agent.diagnostics.service_snapshot import parse_snapshot
from agent.diagnostics.service_verification import validate_snapshot,assess

ROOT=Path(__file__).resolve().parents[2]


class SavedReportTests(unittest.TestCase):
    def setUp(self):
        self.old=Path.cwd();self.tmp=tempfile.TemporaryDirectory();os.chdir(self.tmp.name)
    def tearDown(self):
        os.chdir(self.old);self.tmp.cleanup()
    def test_open_report_and_version_never_construct_session_or_call_backend(self):
        save_report('safe.md','# Incident\nNo new diagnosis.\n')
        out=io.StringIO()
        with patch('agent.read_only.ReadOnlySession') as session,redirect_stdout(out):
            self.assertEqual(main(['--view-report','safe.md']),0)
            with self.assertRaises(SystemExit) as exit:main(['--version'])
            self.assertEqual(exit.exception.code,0);session.assert_not_called()
        self.assertIn('No new diagnosis',out.getvalue());self.assertIn('0.1.0',out.getvalue())
    def test_view_rejects_private_or_malicious_content_without_echo(self):
        for text in ('MYSQL_PWD=synthetic-canary','\x1b[31mterminal instruction','\u202ereversed text'):
            with self.subTest(text=text):
                name='bad-'+str(len(text))+'.md';save_report(name,text)
                out,err=io.StringIO(),io.StringIO()
                with redirect_stdout(out),redirect_stderr(err):self.assertEqual(main(['--view-report',name]),3)
                self.assertEqual(out.getvalue(),'');self.assertNotIn('synthetic-canary',err.getvalue())
    def test_no_traversal_symlink_hardlink_fifo_overwide_permissions_or_oversize(self):
        path=save_report('original.md','safe\n')
        for name in ('../original.md','/tmp/private.md','nested/file.md'):
            with self.subTest(name=name),self.assertRaises(StoreError):read_report(name)
        Path('reports/link.md').symlink_to('original.md')
        os.link(path,'reports/hard.md');os.mkfifo('reports/pipe.md',0o600)
        for name in ('link.md','hard.md','pipe.md'):
            with self.subTest(name=name),self.assertRaises(StoreError):read_report(name)
        Path('reports/hard.md').unlink()  # Test-owned link only.
        path.chmod(0o644)
        with self.assertRaises(StoreError):read_report('original.md')
        path.chmod(0o600);path.write_bytes(b'x'*70001)
        with self.assertRaises(StoreError):read_report('original.md')
    def test_report_directory_symlink_and_mixed_diagnosis_denied(self):
        other=Path('other');other.mkdir();Path('reports').symlink_to(other,target_is_directory=True)
        with self.assertRaises(StoreError):read_report('anything.md')
        for extra in (['--question','check nginx'],['--doctor'],['--report','new.md'],['--action-session','a'*32]):
            with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):main(['--view-report','a.md',*extra])


class ServiceDetailTests(unittest.IsolatedAsyncioTestCase):
    def test_fixed_service_worker_imports_in_isolated_python_without_pythonpath(self):
        worker=ROOT/'src/mcp_server/service_status_worker.py'
        probe='import runpy;runpy.run_path('+repr(str(worker))+',run_name="read_only_import_probe");print("IMPORT_OK")'
        result=subprocess.run((sys.executable,'-I','-B','-c',probe),cwd='/',
            env={'PATH':os.defpath,'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode());self.assertIn(b'IMPORT_OK',result.stdout)
    def request(self):
        return dict(schema_version='1.0.0',session_id='a'*32,trace_id='b'*32,logical_tool_call_id='c'*32,attempt_id='d'*32,unit='nginx.service')
    async def collect(self,extra):
        raw=b'Id=nginx.service\nLoadState=loaded\nActiveState=failed\nSubState=failed\n'+extra
        with patch.object(service_status,'command',return_value=('synthetic-fixed-systemctl',)):
            return await service_status.collect(self.request(),execute=AsyncMock(return_value=(0,raw,b'')),platform_name='linux')
    async def test_exit_and_restart_counts_in_existing_evidence_not_new_contract_fields(self):
        r=await self.collect(b'ExecMainCode=1\nExecMainStatus=1\nNRestarts=4\n')
        contracts.TOOLS['service_status'][4].validate(r);self.assertEqual(r['status'],'ok')
        ev=validate_snapshot(r,'nginx.service');data,details=parse_snapshot(ev['excerpt'],'nginx.service')
        self.assertEqual(details,dict(exec_main_code=1,exec_main_status=1,restart_count=4))
        self.assertEqual(set(r['data']),{'unit','load_state','active_state','sub_state','description'})
        self.assertEqual(assess(ev['excerpt'])[0],'fail')
        text=terminal_summary(Report('limited'),{'service_status':r})
        self.assertIn('Exit status tiến trình chính: 1',text);self.assertIn('systemd ghi nhận: 4',text)
    async def test_missing_or_empty_details_are_unknown_not_invented_zero(self):
        r=await self.collect(b'');self.assertEqual(r['status'],'ok')
        self.assertIsNone(parse_snapshot(validate_snapshot(r,'nginx.service')['excerpt'],'nginx.service')[1])
        r=await self.collect(b'ExecMainCode=\nExecMainStatus=\nNRestarts=\n')
        details=parse_snapshot(validate_snapshot(r,'nginx.service')['excerpt'],'nginx.service')[1]
        self.assertEqual(set(details.values()),{None})
        self.assertNotIn('systemd ghi nhận: 0',terminal_summary(Report('limited'),{'service_status':r}))
    async def test_malformed_details_fail_closed_and_never_reflect_secret(self):
        for raw in (b'ExecMainStatus=-1\n',b'NRestarts=4294967296\n',b'ExecMainCode=99\n',
                    b'NRestarts=MYSQL_PWD=synthetic-canary\n',b'NRestarts=1\nNRestarts=2\n'):
            with self.subTest(raw=raw):
                r=await self.collect(raw)
                self.assertEqual(r['error_code'],'EXECUTION_UNKNOWN');self.assertEqual(r['evidence'],[])
                self.assertNotIn('synthetic-canary',json.dumps(r))
    async def test_signal_is_not_presented_as_exit_status_or_successful_repair(self):
        r=await self.collect(b'ExecMainCode=2\nExecMainStatus=9\nNRestarts=0\n')
        text=terminal_summary(Report('limited'),{'service_status':r})
        self.assertIn('kết thúc bởi signal: 9',text);self.assertNotIn('Exit status tiến trình chính: 9',text)


class SessionArtifactTests(unittest.IsolatedAsyncioTestCase):
    def session(self):
        s=ReadOnlySession(scope='service',backend='mock')
        s.client.bindings['service_status'].client=StdioMCPClient(StdioConfig(sys.executable,
            ('-B',str(ROOT/'tests/service_status_stdio_fixture.py')),frozenset({'service_status'}),request_timeout=10))
        self.addAsyncCleanup(s.finish_cleanup)
        return s
    async def test_sdk_report_has_actual_session_trace_correlation_and_complete_ledger(self):
        s=self.session();r=await s.run('Synthetic read')
        self.assertEqual(r.status,'concluded');self.assertFalse(r.cleanup_pending)
        self.assertEqual(r.state_trace,s.coordinator.state_history)
        self.assertEqual(r.state_trace,('RECEIVED','ANALYZING','COLLECTING','ANALYZING','CONCLUDED','REPORTED'))
        result=s.client.bindings['service_status']._result
        self.assertEqual(r.session.session_id,result['session_id']);self.assertEqual(r.session.trace_id,result['trace_id'])
        text=render_session_report(r,{'service_status':result},'MOCK LLM — scripted, không hiểu câu hỏi + REAL LINUX MCP')
        self.assertIn(result['attempt_id'],text);self.assertIn(result['logical_tool_call_id'],text)
        self.assertIn(result['evidence'][0]['evidence_id'],text);self.assertIn('cleanup excluded',text)
        self.assertIn('Application state trace',text);self.assertIn('Full canonical ledger',text)
        self.assertIsNone(s.client._owner);self.assertFalse(s.model.cleanup_pending)
    async def test_cancel_keeps_observed_evidence_trace_metadata_and_joins_all_owners(self):
        s=self.session();waiting=asyncio.Event()
        async def choose(question,observations):
            if not observations:return Decision(tool='service_status',arguments={'unit':'nginx.service'})
            waiting.set();await asyncio.Event().wait()
        s.model.choose=choose
        task=asyncio.create_task(s.run('Synthetic cancellable read'))
        await asyncio.wait_for(waiting.wait(),15);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        r=s.coordinator.report
        self.assertEqual(r.status,'cancelled');self.assertTrue(r.evidence)
        self.assertIn('CANCELLED',r.state_trace);self.assertIsNotNone(r.session)
        self.assertFalse(s.cleanup_pending);self.assertIsNone(s.client._owner)
        self.assertIsNone(s.coordinator._operation);self.assertFalse(s.bridge.has_pending_task)
    async def test_report_correlation_secret_and_output_caps_fail_closed(self):
        s=self.session();r=await s.run('Synthetic read');original=s.client.bindings['service_status']._result
        for mutate in ('correlation','secret','oversized'):
            result=json.loads(json.dumps(original))
            if mutate=='correlation':result['trace_id']='e'*32
            if mutate=='secret':result['evidence'][0]['excerpt']='MYSQL_PWD=synthetic-canary'
            if mutate=='oversized':result['evidence'][0]['excerpt']='x'*70001
            with self.subTest(mutate=mutate),self.assertRaises(StoreError):
                render_session_report(r,{'service_status':result},'MOCK LLM — scripted, không hiểu câu hỏi + REAL LINUX MCP')
    async def test_invalid_session_and_state_trace_are_not_printed(self):
        for report in (Report('limited',state_trace=('FORGED SYSTEM INSTRUCTION',)),
                       Report('limited',session=SessionInfo('secret','b'*32,'bad','bad',1,'mock'))):
            with self.subTest(report=report),self.assertRaises(ReportError):render_markdown(report,redact=lambda s:s)


if __name__=='__main__':unittest.main()
