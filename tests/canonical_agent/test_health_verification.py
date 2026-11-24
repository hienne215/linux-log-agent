"""Original verify AC: active is not health, old logs and unknown fail closed."""
import asyncio,json
import os
from copy import deepcopy
from pathlib import Path
import sys,unittest
from unittest.mock import AsyncMock,patch
from agent.diagnostics.health_binding import HealthBinding
from agent.diagnostics.health_verification import http_health
from agent.diagnostics.journal_policy import window,parse_stamp,stamp
from agent.llm.redaction import _clean
from agent.mcp_client import StdioMCPClient,StdioConfig,MCPClientError,ToolResult
from agent.read_only import ReadOnlySession
from agent.diagnostics import health_verification as health
ROOT=Path(__file__).resolve().parents[2];FIXTURE=ROOT/'tests/canonical_agent/health_fixture.py'


class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_check_rejects_disagreement_instead_of_reporting_pass(self):
        import mock_mcp_server as contracts
        from src.mcp_server import service_status
        arguments=dict(schema_version='1.0.0',session_id='consistency-session',trace_id='consistency-trace',
            logical_tool_call_id='consistency-call',attempt_id='consistency-attempt',unit='nginx.service')
        now=contracts.current_time()
        response=contracts.make_response('service_status',arguments['trace_id'],arguments['logical_tool_call_id'],
            arguments['attempt_id'],dict(unit='nginx.service',active_state='failed',sub_state='failed',
            load_state='loaded',description='[REDACTED]'),arguments['session_id'],evidence=[dict(
                evidence_id='ev-consistency',source=service_status.SOURCE,observed_at=now,unit='nginx.service',
                excerpt='unit=nginx.service; load=loaded; active=active; sub=running',redacted=True,truncated=False)])
        response['started_at']=now
        with patch.object(service_status,'collect',AsyncMock(return_value=response)) as collect:
            with self.assertRaises(ValueError):await health.service_check(arguments)
        collect.assert_awaited_once_with(arguments)

    async def connect(self,fault='active'):
        c=StdioMCPClient(StdioConfig(sys.executable,('-B',str(FIXTURE),fault),frozenset({'verify_fix'})))
        await c.connect();await c.list_tools();return c
    async def test_active_http_error_old_new_logs_permission_and_timeout(self):
        for fault,overall in [('active','unknown'),('http_500','fail'),('service_failed','fail'),('new_log','fail'),
                              ('old_log','unknown'),('permission','unknown'),('http_timeout','unknown'),('http_malformed','unknown')]:
            c=await self.connect(fault);b=HealthBinding(c,ROOT,redact=_clean)
            try:
                r=await b.call_tool('verify_fix',{'unit':'nginx.service'});e=r.structured_content
                self.assertEqual(e['status'],'partial');self.assertEqual(e['data']['overall_result'],overall)
                self.assertFalse(b.checks_passed);self.assertFalse(r.is_error)
                self.assertEqual(json.loads(r.content[0]['text']),e);b._output.validate(e)
                self.assertNotIn('fixture-private-canary',json.dumps(e));self.assertNotIn('Set-Cookie',json.dumps(e))
                checks={x['check_type']:x for x in e['data']['checks']}
                if fault=='http_500':self.assertEqual(checks['service_active']['result'],'pass');self.assertEqual(checks['http_health']['result'],'fail')
                if fault=='old_log':self.assertEqual(checks['no_new_failures']['result'],'unknown');self.assertFalse(checks['no_new_failures']['evidence_refs'])
                if fault=='active':self.assertEqual(checks['http_health']['result'],'pass');self.assertEqual(checks['no_new_failures']['result'],'unknown')
                ids={v['evidence_id'] for v in e['evidence']}
                self.assertEqual(ids,{ref for check in checks.values() for ref in check['evidence_refs']})
            finally:await c.disconnect()
            self.assertIsNone(c._owner)
    async def test_external_baseline_mock_action_context_preserved_not_fresh_evidence_or_execution(self):
        from datetime import timedelta
        since=window()['since']
        before=dict(evidence_id='synthetic-baseline-before',source='synthetic.baseline',
            observed_at=stamp(parse_stamp(since)-timedelta(seconds=1)),unit='nginx.service',excerpt='Synthetic before mock action',redacted=True,truncated=False)
        c=await self.connect();b=HealthBinding(c,ROOT,redact=_clean,baseline_evidence=(before,),action_id='synthetic-mock-action',since=since)
        try:
            e=(await b.call_tool('verify_fix',{'unit':'nginx.service'})).structured_content
            self.assertEqual(e['data']['baseline_evidence_refs'],[before['evidence_id']]);self.assertEqual(e['data']['action_id'],'synthetic-mock-action')
            self.assertEqual(b.evidence[before['evidence_id']],before)
            self.assertNotIn(before['evidence_id'],[x['evidence_id'] for x in e['evidence']]);self.assertFalse(b.checks_passed)
        finally:await c.disconnect()
    async def test_mock_http_coordinator_full_health_scoped_session(self):
        s=ReadOnlySession(scope='health',backend='mock')
        s.client.bindings['verify_fix'].client=StdioMCPClient(StdioConfig(sys.executable,('-B',str(FIXTURE),'http_500'),frozenset({'verify_fix'})))
        r=await s.run('Synthetic active service but HTTP 500; no action executed')
        self.assertEqual(r.status,'limited');self.assertFalse(r.conclusion_verified)
        self.assertEqual(s.adapter.calls_used,2);self.assertEqual(r.tool_calls,1)
        self.assertTrue(r.citations);self.assertTrue(set(r.citations)<={e.id for e in r.evidence})
        self.assertFalse(s.cleanup_pending);self.assertIsNone(s.client._owner);self.assertIsNone(s.adapter._owner)
    async def test_model_cannot_choose_url_action_context_or_forge_approval(self):
        for extra in (dict(endpoint='http://evil.invalid'),dict(action_id='model'),dict(approved=True),dict(baseline_evidence_refs=['fake'])):
            c=AsyncMock();b=HealthBinding(c,ROOT,redact=_clean)
            with self.assertRaises(MCPClientError):await b.call_tool('verify_fix',{'unit':'nginx.service',**extra})
            c.call_tool.assert_not_awaited()
    async def test_fixed_http_socket_is_closed_on_cancellation(self):
        gate=asyncio.Event()
        class Reader:
            async def readuntil(self,separator):gate.set();await asyncio.sleep(60)
        writer=AsyncMock();writer.write=lambda value:None;writer.close=lambda:setattr(writer,'closed',True)
        with patch('agent.diagnostics.health_verification.asyncio.open_connection',AsyncMock(return_value=(Reader(),writer))) as open_socket:
            task=asyncio.create_task(http_health());await gate.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(writer.closed);writer.wait_closed.assert_awaited_once()
        self.assertEqual(open_socket.call_args.args,('127.0.0.1',80))


class HealthOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_check_cancels_and_joins_siblings_before_return(self):
        """A failed producer cannot leave journal/socket owners behind."""
        for failing in ('service_check','http_health','log_check'):
            with self.subTest(failing=failing):
                entered={name:asyncio.Event() for name in ('service_check','http_health','log_check')}
                closing=asyncio.Event();release=asyncio.Event();owners=[];closed=[];calls=[]
                async def check(name):
                    owners.append(asyncio.current_task());calls.append(name);entered[name].set()
                    if name==failing:
                        await asyncio.gather(*(e.wait() for e in entered.values()))
                        raise ValueError('synthetic-private-canary')
                    try:
                        await asyncio.Event().wait()
                    finally:
                        closing.set();await release.wait();closed.append(name)
                async def service(a):return await check('service_check')
                async def http():return await check('http_health')
                async def log(a):return await check('log_check')
                with patch.object(health,'service_check',service),patch.object(health,'http_health',http),patch.object(health,'log_check',log):
                    owner=asyncio.create_task(health.collect({}))
                    try:
                        for _ in range(30):await asyncio.sleep(0)
                        self.assertFalse(owner.done(),'failed check returned before sibling cleanup')
                        self.assertTrue(closing.is_set(),'siblings were not cancelled')
                        release.set()
                        with self.assertRaises((ValueError,ExceptionGroup)):
                            await owner
                        self.assertEqual(set(closed),set(entered)-{failing})
                        self.assertTrue(all(t.done() for t in owners))
                        self.assertEqual(sorted(calls),sorted(entered))
                    finally:
                        release.set()
                        for task in owners:
                            if not task.done():task.cancel()
                        await asyncio.gather(owner,*owners,return_exceptions=True)

    async def test_repeated_cancel_cannot_drop_http_close_owner(self):
        reading=asyncio.Event();closing=asyncio.Event();release=asyncio.Event();joined=asyncio.Event()
        class Reader:
            async def readuntil(self,separator):
                reading.set();await asyncio.Event().wait()
        class Writer:
            closed=False
            def write(self,data):pass
            async def drain(self):pass
            def close(self):self.closed=True
            async def wait_closed(self):
                closing.set();await release.wait();joined.set()
        writer=Writer()
        with patch.object(health.asyncio,'open_connection',AsyncMock(return_value=(Reader(),writer))):
            owner=asyncio.create_task(health.http_health())
            try:
                await reading.wait();owner.cancel();await closing.wait();owner.cancel()
                for _ in range(10):await asyncio.sleep(0)
                self.assertFalse(owner.done(),'HTTP close owner released by second cancellation')
                self.assertTrue(writer.closed);self.assertFalse(joined.is_set())
                release.set()
                with self.assertRaises(asyncio.CancelledError):await owner
                self.assertTrue(joined.is_set())
                self.assertFalse(any(t.get_name()=='health-http-cleanup' and not t.done() for t in asyncio.all_tasks()))
            finally:
                release.set();await asyncio.gather(owner,return_exceptions=True)

    async def test_collect_cancellation_waits_for_every_check_owner(self):
        entered=[asyncio.Event() for _ in range(3)];release=asyncio.Event();owners=[];closed=[]
        async def check(index):
            owners.append(asyncio.current_task());entered[index].set()
            try:await asyncio.Event().wait()
            finally:
                await release.wait();closed.append(index)
        with patch.object(health,'service_check',lambda a:check(0)),patch.object(
                health,'http_health',lambda:check(1)),patch.object(health,'log_check',lambda a:check(2)):
            owner=asyncio.create_task(health.collect({}))
            try:
                await asyncio.gather(*(e.wait() for e in entered));owner.cancel()
                for _ in range(10):await asyncio.sleep(0)
                owner.cancel()
                for _ in range(10):await asyncio.sleep(0)
                self.assertFalse(owner.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):await owner
                self.assertEqual(sorted(closed),[0,1,2]);self.assertTrue(all(t.done() for t in owners))
            finally:
                release.set();await asyncio.gather(owner,return_exceptions=True)

    async def test_failed_check_reaps_actual_synthetic_journal_child(self):
        from agent.diagnostics import journal_reader
        original=asyncio.create_subprocess_exec
        started=asyncio.Event();process=None;http_closed=asyncio.Event()
        async def spawn(*args,**kwargs):
            nonlocal process
            process=await original(*args,**kwargs);started.set();return process
        async def service(a):
            await started.wait();raise ValueError('synthetic-private-canary')
        async def http():
            try:await asyncio.Event().wait()
            finally:http_closed.set()
        async def log(a):
            return await journal_reader.execute((sys.executable,'-I','-B','-c','import time; time.sleep(60)'))
        with patch.object(journal_reader.asyncio,'create_subprocess_exec',spawn),patch.object(
                health,'service_check',service),patch.object(health,'http_health',http),patch.object(health,'log_check',log):
            owner=asyncio.create_task(health.collect({}))
            try:
                with self.assertRaises(ExceptionGroup):await asyncio.wait_for(owner,5)
                self.assertTrue(http_closed.is_set());self.assertIsNotNone(process.returncode)
                with self.assertRaises(ProcessLookupError):os.kill(process.pid,0)
                self.assertFalse(any(not t.done() and (t.get_name().startswith('health-') or
                    t.get_name()=='collector-process-cleanup') for t in asyncio.all_tasks()))
            finally:
                await asyncio.gather(owner,return_exceptions=True)
                if process is not None and process.returncode is None:
                    process.kill();await process.communicate()

    async def test_group_error_is_fixed_code_without_secret_or_fabricated_evidence(self):
        from agent import diagnostic_worker
        arguments=dict(schema_version='1.0.0',session_id='health-owner-session',trace_id='health-owner-trace',
            logical_tool_call_id='health-owner-call',attempt_id='health-owner-attempt',unit='nginx.service',
            check_types=list(health.CHECKS),since=window()['since'],until=None,action_id=None,baseline_evidence_refs=[])
        failure=ExceptionGroup('synthetic-private-canary',[ValueError('MYSQL_PWD=synthetic-private-canary')])
        with patch.object(health,'collect',AsyncMock(side_effect=failure)) as collect:
            response=await diagnostic_worker.collect_response('verify_fix',arguments)
        self.assertEqual(response['status'],'error');self.assertEqual(response['error_code'],'EXECUTION_UNKNOWN')
        self.assertEqual(response['data'],dict(message='Request rejected.',field='',rule='collection'))
        self.assertEqual(response['evidence'],[])
        self.assertNotIn('synthetic-private-canary',json.dumps(response));collect.assert_awaited_once()
