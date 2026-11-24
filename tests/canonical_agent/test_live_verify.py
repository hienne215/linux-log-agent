"""Bounded verification tests: synthetic systemctl, real SDK, no repair/real logs."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

from agent.diagnostics.live_verify_binding import LiveVerifyBinding
from agent.diagnostics.journal_policy import window
from agent.diagnostics.service_verification import assess,authorized
from agent.llm.redaction import _clean
from agent.mcp_client import StdioConfig,StdioMCPClient,MCPClientError,ToolResult
from agent.integration_boundary import ToolMessage,ToolBridge,BridgeBudget
from agent.read_only import ReadOnlySession,CanonicalRouter,terminal_summary

ROOT=Path(__file__).resolve().parents[2]
FIXTURE=ROOT/'tests/canonical_agent/verify_fixture.py'


def request():
    return dict(schema_version='1.0.0',session_id='synthetic-session',trace_id='trace',
        logical_tool_call_id='call',attempt_id='attempt',unit='nginx.service',since=window()['since'],
        until=None,check_types=['service_active'],action_id=None,baseline_evidence_refs=[])


class PolicyTests(unittest.TestCase):
    def test_assessment_is_service_state_not_remediation_or_http_health(self):
        for active,sub,result in [('active','running','pass'),('inactive','dead','fail'),
                                  ('failed','failed','fail'),('activating','start','unknown'),('active','exited','unknown')]:
            with self.subTest(active=active,sub=sub):
                outcome,summary=assess(f'unit=nginx.service; load=loaded; active={active}; sub={sub}')
                self.assertEqual(outcome,result);self.assertIn('no remediation or HTTP health verified',summary)
        for text in ('MYSQL_PWD=synthetic-private-canary','unit=sshd.service; load=loaded; active=active; sub=running',
                     'unit=nginx.service; load=loaded; active=active; sub=synthetic-private-canary'):
            with self.assertRaises(ValueError):assess(text)

    def test_application_owns_check_types_metadata_window_action_and_baseline(self):
        a=request();self.assertTrue(authorized(a))
        for change in [dict(unit='sshd.service'),dict(check_types=['port_listening']),
                       dict(action_id='forged'),dict(baseline_evidence_refs=['invented']),
                       dict(since='2026-01-01T00:00:00Z'),dict(until=window()['until'])]:
            self.assertFalse(authorized({**a,**change}))
        router=CanonicalRouter('verify')
        self.assertEqual(tuple(router.bindings),('verify_fix',))
        s=ReadOnlySession(scope='all',backend='mock')
        self.assertEqual(len(s.client.bindings),11);self.assertEqual(s.coordinator.max_tools,5)
        self.assertEqual(s.adapter.max_calls,2)

    def test_cli_package_imports_from_outside_repository(self):
        env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'PYTHONDONTWRITEBYTECODE':'1'}
        p=subprocess.run((sys.executable,'-B','-c',
            'from agent.read_only import ReadOnlySession;s=ReadOnlySession(scope="verify",backend="mock");print(tuple(s.client.bindings))'),
            cwd='/',env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
        self.assertEqual(p.returncode,0,p.stderr.decode());self.assertIn(b'verify_fix',p.stdout)


class VerifyTests(unittest.IsolatedAsyncioTestCase):
    async def connect(self,fault='active',extra=()):
        c=StdioMCPClient(StdioConfig(command=sys.executable,args=('-B',str(FIXTURE),fault,*extra),allowed_tools=frozenset({'verify_fix'})))
        await c.connect();await c.list_tools();return c

    async def test_official_sdk_canonical_discovery_metadata_evidence_and_nullable_fields(self):
        c=await self.connect();b=LiveVerifyBinding(c,ROOT,redact=_clean)
        try:
            page=await c.list_tools();tool=next(t for t in page.tools if t['name']=='verify_fix')
            self.assertEqual(tool['inputSchema'],b.schemas[0]);self.assertEqual(tool['outputSchema'],b.schemas[2])
            r=await b.call_tool('verify_fix',{'unit':'nginx.service'});e=r.structured_content
            b._output.validate(e);self.assertFalse(r.is_error);self.assertEqual(json.loads(r.content[0]['text']),e)
            self.assertEqual(e['status'],'partial');self.assertEqual(e['data']['overall_result'],'pass')
            self.assertIsNone(e['data']['action_id']);self.assertEqual(e['data']['baseline_evidence_refs'],[])
            self.assertFalse(b.checks_passed)
            for k,v in b.correlation.items():self.assertEqual(e[k],v)
            ev=e['evidence'][0];self.assertEqual(ev['source'],'collector.service_status.systemctl.v1')
            self.assertEqual(e['data']['checks'][0]['evidence_refs'],[ev['evidence_id']])
            self.assertEqual(b.evidence[ev['evidence_id']],ev)
            observation=b.interpret(ToolMessage('verify_fix',json.dumps({'structuredContent':e}),False))
            self.assertTrue(observation.limited);self.assertEqual(observation.evidence[0].id,ev['evidence_id'])
            self.assertIn('observed_at',observation.evidence[0].text);self.assertIn('baseline_evidence_refs',observation.note)
            with self.assertRaises(MCPClientError):await b.call_tool('verify_fix',{'unit':'nginx.service'})
        finally:await c.disconnect()
        self.assertIsNone(c._owner)

    async def test_inactive_failed_transition_never_become_verified_repair(self):
        for fault,expected in [('inactive','fail'),('failed','fail'),('transition','unknown')]:
            c=await self.connect(fault);b=LiveVerifyBinding(c,ROOT,redact=_clean)
            try:
                r=await b.call_tool('verify_fix',{'unit':'nginx.service'})
                self.assertEqual(r.structured_content['data']['overall_result'],expected)
                self.assertEqual(r.structured_content['status'],'partial');self.assertFalse(b.checks_passed)
            finally:await c.disconnect()
            self.assertIsNone(c._owner)

    async def test_collection_errors_do_not_fabricate_evidence_or_leak_stderr(self):
        for fault,code in [('permission','PERMISSION_DENIED'),('timeout','TIMEOUT'),
                           ('missing','NOT_FOUND'),('malformed','EXECUTION_UNKNOWN'),('oversized','OUTPUT_LIMIT')]:
            c=await self.connect(fault);b=LiveVerifyBinding(c,ROOT,redact=_clean)
            try:
                with self.assertRaises(MCPClientError) as e:await b.call_tool('verify_fix',{'unit':'nginx.service'})
                self.assertEqual(e.exception.code,code);self.assertEqual(str(e.exception),code)
                self.assertFalse(b.evidence)
                if code in ('TIMEOUT','EXECUTION_UNKNOWN'):self.assertEqual(b.execution_state,'unknown')
            finally:await c.disconnect()
            self.assertIsNone(c._owner)

    async def test_model_cannot_supply_action_baseline_checks_or_write_operation(self):
        for name,args in [('restart_service',{'unit':'nginx.service'}),('verify_fix',{'unit':'sshd.service'}),
            *[('verify_fix',{'unit':'nginx.service',**extra}) for extra in
               [dict(action_id='model'),dict(baseline_evidence_refs=['fake']),dict(check_types=['port_listening']),
                dict(since=window()['since']),dict(trace_id='model'),dict(approved=True)]]]:
            c=AsyncMock();b=LiveVerifyBinding(c,ROOT,redact=_clean)
            with self.assertRaises(MCPClientError):await b.call_tool(name,args)
            c.call_tool.assert_not_awaited()

    async def test_timeout_disconnect_cancellation_are_unknown_and_not_retried(self):
        for cause in (MCPClientError('TIMEOUT'),MCPClientError('DISCONNECTED'),asyncio.CancelledError()):
            c=AsyncMock();c.call_tool.side_effect=cause;b=LiveVerifyBinding(c,ROOT,redact=_clean)
            with self.assertRaises(type(cause)):await b.call_tool('verify_fix',{'unit':'nginx.service'})
            self.assertEqual(b.execution_state,'unknown')
            with self.assertRaises(MCPClientError):await b.call_tool('verify_fix',{'unit':'nginx.service'})
            c.call_tool.assert_awaited_once()

    async def test_forged_evidence_result_correlation_and_privacy_fail_closed(self):
        c=await self.connect();b=LiveVerifyBinding(c,ROOT,redact=_clean)
        try:base=(await b.call_tool('verify_fix',{'unit':'nginx.service'})).structured_content
        finally:await c.disconnect()
        for fault in ('source','summary','baseline','action','reference','correlation','future','state','secret','status','text','oversized'):
            fake=AsyncMock();b=LiveVerifyBinding(fake,ROOT,redact=_clean)
            async def reply(name,a):
                r=deepcopy(base)
                for k in b.correlation:r[k]=a[k]
                if fault=='source':r['evidence'][0]['source']='forged'
                if fault=='summary':r['data']['checks'][0]['summary']='Repair succeeded'
                if fault=='baseline':r['data']['baseline_evidence_refs']=['invented']
                if fault=='action':r['data']['action_id']='invented'
                if fault=='reference':r['data']['checks'][0]['evidence_refs']=['invented']
                if fault=='correlation':r['trace_id']='invented'
                if fault=='future':r['evidence'][0]['observed_at']='2099-01-01T00:00:00Z'
                if fault=='state':r['data']['checks'][0]['result']='fail';r['data']['overall_result']='fail'
                if fault=='secret':r['evidence'][0]['excerpt']='MYSQL_PWD=synthetic-private-canary'
                if fault=='status':r['status']='ok'
                if fault=='oversized':r['evidence'][0]['excerpt']='x'*65537
                return ToolResult(({'type':'text','text':'{}' if fault=='text' else json.dumps(r)},),r,False)
            fake.call_tool.side_effect=reply
            with self.subTest(fault=fault):
                with self.assertRaises(MCPClientError) as e:await b.call_tool('verify_fix',{'unit':'nginx.service'})
                self.assertNotIn('synthetic-private-canary',str(e.exception));self.assertFalse(b.evidence)

    async def test_coordinator_mock_http_native_call_citation_partial_report_cleanup(self):
        s=ReadOnlySession(scope='verify',backend='mock')
        s.client.bindings['verify_fix'].client=StdioMCPClient(StdioConfig(command=sys.executable,args=('-B',str(FIXTURE),'active'),allowed_tools=frozenset({'verify_fix'})))
        r=await s.run('Synthetic read-only recheck, no action performed')
        self.assertEqual(r.status,'limited');self.assertEqual(s.adapter.calls_used,2);self.assertEqual(r.tool_calls,1)
        self.assertTrue(r.citations);self.assertTrue(set(r.citations)<={e.id for e in r.evidence})
        self.assertFalse(r.cleanup_pending);self.assertIsNone(s.adapter._owner);self.assertIsNone(s.client._owner)
        self.assertIsNone(s.coordinator._operation);self.assertFalse(s.bridge.has_pending_task)
        text=terminal_summary(r,{'verify_fix':s.client.bindings['verify_fix']._result})
        self.assertIn('Không có action/baseline sửa lỗi',text);self.assertIn('đang chạy tại thời điểm kiểm tra',text)

    async def test_current_recheck_does_not_reuse_aged_session_journal_window(self):
        # Simulate an older analysis window. A current status check has no
        # historical baseline, so its since belongs to dispatch, not that window.
        with patch('agent.read_only.window',return_value=dict(
                since='2026-01-01T00:00:00Z',until='2026-01-01T00:15:00Z')):
            s=ReadOnlySession(scope='verify',backend='mock')
        s.client.bindings['verify_fix'].client=StdioMCPClient(StdioConfig(command=sys.executable,
            args=('-B',str(FIXTURE),'active'),allowed_tools=frozenset({'verify_fix'})))
        r=await s.run('Synthetic fresh recheck after prior analysis')
        self.assertEqual(r.status,'limited');self.assertEqual(r.tool_calls,1)
        self.assertFalse(r.cleanup_pending);self.assertIsNone(s.client._owner)

    async def test_cancel_during_fixed_child_read_reaps_owned_subprocess(self):
        with tempfile.TemporaryDirectory(prefix='verify-child-') as folder:
            pidfile=Path(folder)/'pid';c=await self.connect('child',(str(pidfile),))
            b=LiveVerifyBinding(c,ROOT,redact=_clean)
            task=asyncio.create_task(b.call_tool('verify_fix',{'unit':'nginx.service'}))
            try:
                async with asyncio.timeout(5):
                    while not pidfile.exists():await asyncio.sleep(.02)
                pid=int(pidfile.read_text());task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
                self.assertEqual(b.execution_state,'unknown')
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True);await c.disconnect()
            self.assertIsNone(c._owner)
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)
