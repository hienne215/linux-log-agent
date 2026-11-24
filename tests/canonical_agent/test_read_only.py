"""Canonical mock/SDK fixture tests. No real collectors or live LLM requests."""
import asyncio
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import mock_mcp_server as contracts
from agent.coordinator import Evidence, Report
from agent.integration_boundary import ToolMessage
from agent.llm.redaction import _clean
from agent.mcp_client import StdioMCPClient, StdioConfig, MCPClientError, ToolResult
from agent.service_binding import DiskBinding
from agent.read_only import CanonicalRouter, ReadOnlySession, main, terminal_summary
ROOT = Path(__file__).resolve().parents[2]


def response(args):
    now = contracts.current_time()
    r=contracts.make_response('disk_usage', args['trace_id'],args['logical_tool_call_id'],args['attempt_id'],
        dict(filesystem='root',mount_point='/',used_percent=37.5,available_bytes=8*1024**3,inodes_included=True),args['session_id'],
        evidence=[dict(evidence_id='ev-disk-test',source='collector.disk_usage.statvfs.v1',
            observed_at=now,unit=None,excerpt='Synthetic fixture.',redacted=True,truncated=False)])
    r['started_at']=now
    return r


class FakeClient:
    def __init__(self,mutate=lambda r:r):
        self.mutate=mutate;self.calls=[];self._owner=None
        self.disconnect=AsyncMock()
    async def call_tool(self,name,args):
        self.calls.append(deepcopy(args));r=self.mutate(response(args))
        return ToolResult(({'type':'text','text':json.dumps(r)},),r,r['status']=='error')


class DiskTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_result_and_application_owned_optional(self):
        c=FakeClient();b=DiskBinding(c,ROOT,redact=_clean)
        r=await b.call_tool('disk_usage',{'filesystem':'root'})
        self.assertIs(c.calls[0]['include_inodes'],True)
        self.assertTrue(b.validators[0].is_valid(c.calls[0]))
        o=b.interpret(ToolMessage('disk_usage',json.dumps({'structuredContent':r.structured_content}),False))
        self.assertEqual(o.evidence[0].id,'ev-disk-test')
        self.assertIsNone(json.loads(o.evidence[0].text)['unit'])
        self.assertEqual(b.evidence['ev-disk-test'],r.structured_content['evidence'][0])
    async def test_invalid_business_arguments_never_dispatch(self):
        for args in ({'filesystem':'/tmp'},{'filesystem':None},{'filesystem':'root','include_inodes':False},
                     {'filesystem':'root','session_id':'model-value'},{}):
            c=FakeClient();b=DiskBinding(c,ROOT,redact=_clean)
            with self.assertRaises(MCPClientError):await b.call_tool('disk_usage',args)
            self.assertFalse(c.calls)
    async def test_wrong_tool_never_dispatch(self):
        for name in ['restart_service','service_status','shell']:
            c=FakeClient();b=DiskBinding(c,ROOT,redact=_clean)
            with self.assertRaises(MCPClientError):await b.call_tool(name,{'filesystem':'root'})
            self.assertFalse(c.calls)
    async def test_result_validation_correlations_and_provenance(self):
        def replace(k,v):
            def mutation(r):r[k]=v;return r
            return mutation
        def field(k,v):
            def mutation(r):r['data'][k]=v;return r
            return mutation
        def ev(k,v):
            def mutation(r):r['evidence'][0][k]=v;return r
            return mutation
        for f in [replace('trace_id','forged'),replace('evidence',[]),field('mount_point','/tmp'),
                  field('filesystem','other'),field('inodes_included',False),field('used_percent',101),
                  ev('source','synthetic:forged'),ev('unit','nginx.service'),
                  ev('observed_at','2099-01-01T00:00:00Z'),ev('excerpt','x'*70000)]:
            b=DiskBinding(FakeClient(f),ROOT,redact=_clean)
            with self.assertRaises(MCPClientError):await b.call_tool('disk_usage',{'filesystem':'root'})
            self.assertFalse(b.evidence)
    async def test_partial_remains_limited(self):
        def partial(r):r['status']='partial';r['truncated']=True;return r
        b=DiskBinding(FakeClient(partial),ROOT,redact=_clean)
        r=await b.call_tool('disk_usage',{'filesystem':'root'})
        self.assertTrue(b.interpret(ToolMessage('disk_usage',json.dumps({'structuredContent':r.structured_content}),False)).limited)
    async def test_errors_are_not_success_and_timeout_is_unknown(self):
        for code in ['TIMEOUT','EXECUTION_UNKNOWN','PERMISSION_DENIED','UNSUPPORTED','OUTPUT_LIMIT']:
            def error(r):return contracts.safe_error('disk_usage',{},code,rule='worker')
            b=DiskBinding(FakeClient(error),ROOT,redact=_clean)
            with self.assertRaises(MCPClientError) as e:await b.call_tool('disk_usage',{'filesystem':'root'})
            self.assertEqual(e.exception.code,code)
            self.assertFalse(b.evidence)
            if code in {'TIMEOUT','EXECUTION_UNKNOWN'}:self.assertEqual(b.execution_state,'unknown')
    async def test_transport_failure_no_retry(self):
        for code in ['TIMEOUT','DISCONNECTED']:
            c=FakeClient();c.call_tool=AsyncMock(side_effect=MCPClientError(code));b=DiskBinding(c,ROOT,redact=_clean)
            for _ in range(2):
                with self.assertRaises(MCPClientError):await b.call_tool('disk_usage',{'filesystem':'root'})
            c.call_tool.assert_awaited_once();self.assertEqual(b.execution_state,'unknown')
    async def test_cancel_is_unknown_and_single_use(self):
        started=asyncio.Event();c=FakeClient()
        async def blocked(*a):started.set();await asyncio.Event().wait()
        c.call_tool=AsyncMock(side_effect=blocked);b=DiskBinding(c,ROOT,redact=_clean)
        t=asyncio.create_task(b.call_tool('disk_usage',{'filesystem':'root'}));await started.wait();t.cancel()
        with self.assertRaises(asyncio.CancelledError):await t
        self.assertEqual(b.execution_state,'unknown')
        with self.assertRaises(MCPClientError):await b.call_tool('disk_usage',{'filesystem':'root'})
        c.call_tool.assert_awaited_once()
    async def test_secret_redaction_and_failure_closed(self):
        secret='test-'+'canary'
        def mutation(r):r['evidence'][0]['excerpt']='MYSQL_PWD="'+secret+'"';return r
        b=DiskBinding(FakeClient(mutation),ROOT,redact=_clean)
        r=await b.call_tool('disk_usage',{'filesystem':'root'})
        self.assertTrue(secret not in json.dumps(r.structured_content),'credential survived')
        def reject(s):raise ValueError('synthetic')
        b=DiskBinding(FakeClient(),ROOT,redact=reject)
        with self.assertRaises(MCPClientError):await b.call_tool('disk_usage',{'filesystem':'root'})
        self.assertFalse(b.evidence)
    async def test_forged_projection_rejected(self):
        b=DiskBinding(FakeClient(),ROOT,redact=_clean);r=await b.call_tool('disk_usage',{'filesystem':'root'})
        r.structured_content['evidence'][0]['evidence_id']='forged'
        with self.assertRaises(MCPClientError):b.interpret(ToolMessage('disk_usage',json.dumps({'structuredContent':r.structured_content}),False))


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_config_permission_error_explains_unverified_syntax_without_raw_error(self):
        result=contracts.safe_error('check_config',{},'PERMISSION_DENIED',rule='collection')
        result['message']='raw-private-canary'  # Untrusted diagnostics must not be displayed.
        text=terminal_summary(Report('tool_error'),{'check_config':result})
        self.assertIn('Không đủ quyền hoặc sandbox',text)
        self.assertIn('Chưa thể kết luận cấu hình đúng hay sai',text)
        self.assertNotIn('raw-private-canary',text)
        self.assertNotIn('cấu hình hợp lệ',text)

    async def test_invalid_model_proposal_starts_no_mcp_process(self):
        from agent.llm.interface import Completion,ToolCall,Usage
        session=ReadOnlySession(scope='all',backend='mock')
        session.adapter.complete=AsyncMock(return_value=Completion('tool_calls','',
            (ToolCall('restart_service',{'unit':'nginx.service'}),),
            Usage(None,None,None,None,None),0,session.adapter.config.model,'stop'))
        for b in session.client.bindings.values():
            b.client.connect=AsyncMock()
        report=await session.run('Synthetic invalid proposal')
        self.assertEqual(report.tool_calls,0);self.assertFalse(report.evidence)
        self.assertNotEqual(report.status,'concluded')
        self.assertFalse(session.client._attempted);self.assertFalse(session.cleanup_pending)
        for b in session.client.bindings.values():b.client.connect.assert_not_awaited()

    async def test_startup_is_local_and_only_selected_tool_connects(self):
        from agent.mcp_client import ToolPage
        r=CanonicalRouter('all')
        for name,b in r.bindings.items():
            b.client=AsyncMock()
            b.client._owner=None
            b.client.list_tools.return_value=ToolPage((dict(name=name,inputSchema=b.schemas[0],outputSchema=b.schemas[2]),))
            b.call_tool=AsyncMock(return_value='selected-result')
        await r.connect()
        for b in r.bindings.values():b.client.connect.assert_not_awaited()
        self.assertEqual(await r.call_tool('memory_oom_events',{}),'selected-result')
        r.bindings['memory_oom_events'].client.connect.assert_awaited_once()
        r.bindings['memory_oom_events'].client.list_tools.assert_awaited_once()
        for name,b in r.bindings.items():
            if name!='memory_oom_events':b.client.connect.assert_not_awaited()
        await r.disconnect()
        with self.assertRaises(MCPClientError):await r.call_tool('memory_oom_events',{})

    async def test_invalid_or_write_tool_does_not_start_any_server(self):
        r=CanonicalRouter('all')
        for b in r.bindings.values():b.client=AsyncMock()
        await r.connect()
        for name,args in [('restart_service',{}),('service_status',{'unit':'sshd.service'}),
                          ('memory_oom_events',{'command':'shell'})]:
            with self.assertRaises(MCPClientError):await r.call_tool(name,args)
        for b in r.bindings.values():b.client.connect.assert_not_awaited()

    async def test_bad_discovery_is_not_dispatched_or_reconnected(self):
        from agent.mcp_client import ToolPage
        r=CanonicalRouter('oom');b=r.bindings['memory_oom_events']
        b.client=AsyncMock();b.client.list_tools.return_value=ToolPage(())
        b.call_tool=AsyncMock()
        await r.connect()
        for _ in range(2):
            with self.assertRaises(MCPClientError):await r.call_tool('memory_oom_events',{})
        b.client.connect.assert_awaited_once();b.call_tool.assert_not_awaited()
        await r.disconnect();b.client.disconnect.assert_awaited_once()

    async def test_cancel_during_lazy_connect_retains_owner_for_cleanup(self):
        r=CanonicalRouter('oom');b=r.bindings['memory_oom_events'];entered=asyncio.Event()
        b.client=FakeClient()
        async def connect():
            b.client._owner=object();entered.set();await asyncio.Event().wait()
        b.client.connect=connect
        async def disconnect():b.client._owner=None
        b.client.disconnect=AsyncMock(side_effect=disconnect)
        await r.connect()
        task=asyncio.create_task(r.call_tool('memory_oom_events',{}))
        await entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertIsNotNone(r._owner)
        await r.disconnect();self.assertIsNone(r._owner)
        with self.assertRaises(MCPClientError):await r.call_tool('memory_oom_events',{})

    async def test_session_ids_shared_and_no_write_tool(self):
        r=CanonicalRouter('both');s,d=r.bindings.values()
        self.assertEqual((s.session_id,s.trace_id),(d.session_id,d.trace_id))
        self.assertEqual({t.name for t in r.tools},{'service_status','disk_usage'})
        with self.assertRaises(MCPClientError):await r.call_tool('restart_service',{})
    async def test_disconnect_attempts_all_and_retains_pending_owner(self):
        r=CanonicalRouter('both');s,d=r.bindings.values()
        s.client=FakeClient();d.client=FakeClient();s.client._owner=object()
        s.client.disconnect=AsyncMock(side_effect=MCPClientError('CLEANUP_PENDING'))
        with self.assertRaises(MCPClientError):await r.disconnect()
        d.client.disconnect.assert_awaited_once();self.assertIsNotNone(r._owner)
    async def test_real_sdk_two_tools_three_turns_synthetic_only(self):
        session=ReadOnlySession(backend='mock')
        for name,b in session.client.bindings.items():
            args=('-B',str(ROOT/'tests/service_status_stdio_fixture.py')) if name=='service_status' else (
                '-B',str(ROOT/'tests/canonical_agent/fixture.py'))
            b.client=StdioMCPClient(StdioConfig(command=sys.executable,args=args,allowed_tools=frozenset({name})))
        report=await session.run('Synthetic: service and disk')
        self.assertEqual(report.status,'concluded')
        self.assertEqual((report.tool_calls,report.llm_turns),(2,3))
        self.assertEqual(session.adapter.calls_used,2)  # One native batch + one conclusion.
        self.assertEqual(len(report.evidence),2)
        self.assertEqual(set(report.citations),{e.id for e in report.evidence})
        self.assertFalse(session.cleanup_pending);self.assertIsNone(session.client._owner)
        self.assertIsNone(session._cleanup_owner);self.assertFalse(session.adapter.cleanup_pending)
    async def test_full_session_cancel_cleans_all_owners(self):
        session=ReadOnlySession(backend='mock');entered=asyncio.Event()
        async def wait():entered.set();await asyncio.Event().wait()
        session.client.connect=wait
        task=asyncio.create_task(session.run('Synthetic cancellation'));await entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(session.coordinator.report.status,'cancelled')
        self.assertFalse(session.cleanup_pending);self.assertIsNone(session._cleanup_owner)


class CLITests(unittest.TestCase):
    def test_full_health_summary_does_not_deny_the_http_check_it_just_performed(self):
        report=Report('limited','',(),(),1,2)
        result=dict(status='partial',data=dict(checks=[
            dict(check_type='service_active',result='pass'),
            dict(check_type='http_health',result='pass'),
            dict(check_type='no_new_failures',result='unknown')]))
        text=terminal_summary(report,{'verify_fix':result})
        self.assertIn('HTTP tại 127.0.0.1:80: đạt kiểm tra này',text)
        self.assertIn('Lỗi journal mới: chưa xác minh đầy đủ',text)
        self.assertNotIn('Chưa kiểm tra: website',text)
        self.assertIn('chưa xác minh đã sửa thành công',text)

    def test_report_preserves_full_safe_ledger_without_overwrite(self):
        report=Report('concluded','Synthetic conclusion.',
            (Evidence('ev-disk-test','test','synthetic'),),('ev-disk-test',),1,2)
        result=response(dict(trace_id='t',logical_tool_call_id='c',attempt_id='a',session_id='s'))
        result['evidence'].append({**result['evidence'][0],'evidence_id':'ev-ledger-only'})
        before=os.getcwd()
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.chdir(directory)
                with patch('agent.read_only.drive',AsyncMock(return_value=(report,{'disk_usage':result}))),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                    self.assertEqual(main(['--backend','mock','--question','check disk','--report','safe.md']),0)
                    target=Path('reports/safe.md');original=target.read_bytes()
                    self.assertIn(b'ev-ledger-only',original)
                    self.assertIn(b'Full canonical ledger',original)
                    self.assertEqual(target.stat().st_mode & 0o777,0o600)
                    self.assertEqual(main(['--backend','mock','--question','check disk','--report','safe.md']),3)
                    self.assertEqual(target.read_bytes(),original)
        finally:os.chdir(before)

    def test_report_rejects_unsafe_ledger_without_persisting_secrets(self):
        report=Report('concluded','Synthetic conclusion.',(),(),1,2)
        result=response(dict(trace_id='t',logical_tool_call_id='c',attempt_id='a',session_id='s'))
        result['evidence'][0]['excerpt']='MYSQL_PWD=fixture-private-canary'
        before=os.getcwd()
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.chdir(directory);stdout=io.StringIO();stderr=io.StringIO()
                with patch('agent.read_only.drive',AsyncMock(return_value=(report,{'disk_usage':result}))),redirect_stdout(stdout),redirect_stderr(stderr):
                    self.assertEqual(main(['--backend','mock','--question','check disk','--report','unsafe.md']),3)
                self.assertFalse(Path('reports/unsafe.md').exists())
                self.assertNotIn('fixture-private-canary',stdout.getvalue()+stderr.getvalue())
        finally:os.chdir(before)

    def test_disk_summary_does_not_invent_service_status(self):
        r=response(dict(trace_id='t',logical_tool_call_id='c',attempt_id='a',session_id='s'))
        report=Report('concluded','Synthetic conclusion.',(Evidence('ev-disk-test','test','synthetic'),),('ev-disk-test',),1,2)
        text=terminal_summary(report,{'disk_usage':r})
        self.assertIn('37.50%',text);self.assertIn('8.00 GiB',text)
        self.assertNotIn('Nginx đang chạy',text)
    def test_write_and_path_denied_before_model(self):
        with patch('agent.read_only.drive') as drive,redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--question','restart nginx.service']),2)
            with self.assertRaises(SystemExit):main(['--question','check disk','--report','../escape.md'])
            drive.assert_not_called()
    def test_unknown_scope_rejected(self):
        with self.assertRaises(ValueError):CanonicalRouter('shell')



class InteractiveTests(unittest.TestCase):
    def test_two_explicit_questions_then_exit_do_not_replay_or_add_requests(self):
        questions=[]
        async def fake_drive(question,backend,scope):
            questions.append(question)
            return Report('concluded','Synthetic conclusion.',(Evidence('ev-test','synthetic:test','Synthetic'),),('ev-test',),1,2),{}
        stream=io.StringIO('Nginx có chạy không?\nỔ đĩa còn trống không?\nthoát\n')
        stream.isatty=lambda:True
        with patch('agent.read_only.drive',fake_drive),patch('sys.stdin',stream),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--backend','mock']),0)
        self.assertEqual(questions,['Nginx có chạy không?\n','Ổ đĩa còn trống không?\n'])
    def test_eof_does_not_start_a_model(self):
        stream=io.StringIO('');stream.isatty=lambda:True
        with patch('agent.read_only.drive') as drive,patch('sys.stdin',stream),redirect_stderr(io.StringIO()):
            self.assertEqual(main([]),0)
            drive.assert_not_called()
    def test_write_rejection_and_quit_do_not_dispatch(self):
        stream=io.StringIO('restart nginx.service\nthoát\n');stream.isatty=lambda:True
        with patch('agent.read_only.drive') as drive,patch('sys.stdin',stream),redirect_stderr(io.StringIO()):
            self.assertEqual(main([]),2)
            drive.assert_not_called()


if __name__=='__main__':unittest.main()
