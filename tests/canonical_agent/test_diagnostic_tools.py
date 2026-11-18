"""Independent fixtures; no inference, systemctl, ss, service mutation or private logs."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import mock_mcp_server as contracts
from agent.diagnostic_worker import collect_response
from agent.integration_boundary import ToolMessage
from agent.llm.redaction import _clean
from agent.mcp_client import MCPClientError, StdioConfig, StdioMCPClient, ToolResult
from agent.read_only import CanonicalRouter, ReadOnlySession, terminal_summary
from agent.service_binding import DiagnosticBinding
from agent.coordinator import Report
ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT/'tests/canonical_agent/diagnostic_fixture.py'
TOOLS = {'list_failed_units': {}, 'listening_ports': {'protocol': 'tcp', 'port': 80}}


def arguments(tool):
    return dict(schema_version='1.0.0', session_id='synthetic-session',trace_id='trace',
        logical_tool_call_id='call',attempt_id='attempt',**TOOLS[tool])


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_policy_denies_before_collection(self):
        for tool, bad in [('list_failed_units', {'limit': 1}),('list_failed_units',{'cursor':'x'}),
                          ('listening_ports',{'protocol':'tcp','port':22}),
                          ('listening_ports',{'protocol':'tcp','port':80,'unit':'nginx.service'})]:
            with patch('agent.diagnostic_worker.collect_failed_units',new_callable=AsyncMock) as failed,patch(
                    'agent.diagnostic_worker.collect_listening_ports',new_callable=AsyncMock) as ports:
                r=await collect_response(tool,{**arguments(tool),**bad})
                self.assertEqual(r['error_code'],'POLICY_DENIED')
                self.assertFalse(r['evidence']);failed.assert_not_awaited();ports.assert_not_awaited()
    async def test_malformed_request_never_collects(self):
        with patch('agent.diagnostic_worker.collect_listening_ports',new_callable=AsyncMock) as core:
            r=await collect_response('listening_ports',{**arguments('listening_ports'),'command':'sudo'})
            self.assertEqual(r['error_code'],'INVALID_INPUT');core.assert_not_awaited()
    async def test_descriptions_are_dropped_and_empty_observation_is_real(self):
        payload=dict(failed_units=[dict(unit='example.service',active_state='failed',description='fixture-credential')],count=1,limit=100)
        with patch('agent.diagnostic_worker.collect_failed_units',AsyncMock(return_value=payload)):
            r=await collect_response('list_failed_units',arguments('list_failed_units'))
        self.assertEqual(r['status'],'ok');self.assertNotIn('fixture-credential',json.dumps(r))
        self.assertEqual(r['data']['failed_units'][0]['description'],'[REDACTED]')
        with patch('agent.diagnostic_worker.collect_failed_units',AsyncMock(return_value=dict(failed_units=[],count=0,limit=100))):
            r=await collect_response('list_failed_units',arguments('list_failed_units'))
        self.assertEqual(r['status'],'ok');self.assertEqual(len(r['evidence']),1)
    async def test_core_errors_no_evidence_no_retry(self):
        from src.mcp_server.listening_ports import ListeningPortsError
        for code in ['TIMEOUT','PERMISSION_DENIED','OUTPUT_LIMIT','UNSUPPORTED','EXECUTION_UNKNOWN']:
            with patch('agent.diagnostic_worker.collect_listening_ports',AsyncMock(side_effect=ListeningPortsError(code))) as core:
                r=await collect_response('listening_ports',arguments('listening_ports'))
            core.assert_awaited_once();self.assertEqual(r['error_code'],code);self.assertFalse(r['evidence'])


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_sdk_stdio_redaction_correlation_and_cleanup(self):
        for tool, business in TOOLS.items():
            client=StdioMCPClient(StdioConfig(command=sys.executable,args=('-B',str(FIXTURE)),allowed_tools=frozenset({tool})))
            binding=DiagnosticBinding(client,ROOT,redact=_clean,tool=tool)
            try:
                await client.connect()
                page=await client.list_tools()
                descriptor=next(t for t in page.tools if t['name']==tool)
                self.assertEqual(descriptor['inputSchema'],binding.schemas[0])
                self.assertEqual(descriptor['outputSchema'],binding.schemas[2])
                result=await binding.call_tool(tool,business)
                envelope=result.structured_content
                binding.validators[2].validate(envelope)
                for key,value in binding.correlation.items():self.assertEqual(envelope[key],value)
                self.assertFalse(result.is_error)
                self.assertEqual(json.loads(result.content[0]['text']),envelope)
                self.assertNotIn('fixture-canary',json.dumps(envelope));self.assertNotIn('192.0.2.55',json.dumps(envelope))
                obs=binding.interpret(ToolMessage(tool,json.dumps({'structuredContent':envelope}),False))
                self.assertEqual(obs.evidence[0].id,envelope['evidence'][0]['evidence_id'])
                self.assertEqual(obs.evidence[0].source,binding.source)
                self.assertIn('observed_at',json.loads(obs.evidence[0].text))
                with self.assertRaises(MCPClientError):await binding.call_tool(tool,business)
            finally:await client.disconnect()
            self.assertIsNone(client._owner)
    async def test_all_four_tools_reuse_coordinator_two_mock_http_requests(self):
        session=ReadOnlySession(scope='diagnostics',backend='mock')
        for name,b in session.client.bindings.items():
            fixture=ROOT/'tests/service_status_stdio_fixture.py' if name=='service_status' else (
                ROOT/'tests/canonical_agent/fixture.py' if name=='disk_usage' else FIXTURE)
            b.client=StdioMCPClient(StdioConfig(command=sys.executable,args=('-B',str(fixture)),allowed_tools=frozenset({name})))
        report=await session.run('Synthetic four-tool inspection')
        self.assertEqual(report.status,'concluded')
        self.assertEqual((report.tool_calls,report.llm_turns,session.adapter.calls_used),(4,5,2))
        self.assertEqual(len(report.evidence),4)
        self.assertEqual(session.adapter.max_calls,2)
        self.assertEqual(set(report.citations),{e.id for e in report.evidence})
        self.assertFalse(report.cleanup_pending);self.assertIsNone(session.client._owner)
        self.assertIsNone(session.adapter._owner)
    async def test_forged_or_unbounded_output_is_rejected(self):
        for tool,business in TOOLS.items():
            data=(dict(failed_units=[],count=0,limit=100) if tool=='list_failed_units' else dict(ports=[],count=0))
            async def response(name,args):
                now=contracts.current_time()
                r=contracts.make_response(tool,args['trace_id'],args['logical_tool_call_id'],args['attempt_id'],deepcopy(data),args['session_id'],
                    evidence=[dict(evidence_id='test',source='forged',observed_at=now,unit=None,excerpt='synthetic',redacted=True,truncated=False)])
                r['started_at']=now
                return ToolResult(({'type':'text','text':json.dumps(r)},),r,False)
            client=AsyncMock();client.call_tool.side_effect=response
            b=DiagnosticBinding(client,ROOT,redact=_clean,tool=tool)
            with self.assertRaises(MCPClientError):await b.call_tool(tool,business)
            self.assertFalse(b.evidence);client.call_tool.assert_awaited_once()
    async def test_scope_never_grants_write_tool(self):
        r=CanonicalRouter('diagnostics')
        self.assertEqual(set(r.bindings),{'service_status','disk_usage',*TOOLS})
        with self.assertRaises(MCPClientError):await r.call_tool('restart_service',{'unit':'nginx.service'})

    async def test_outer_timeout_reaps_nested_synthetic_child(self):
        from src.mcp_server.runner import WorkerRunner, WorkerFailure
        with tempfile.TemporaryDirectory(prefix='diagnostic-timeout-') as folder:
            pidfile=Path(folder)/'pid'
            runner=WorkerRunner(timeout=3)
            try:
                with self.assertRaises(WorkerFailure) as error:
                    await runner.run((sys.executable,'-B',str(FIXTURE),'worker','listening_ports',str(pidfile)),
                                     arguments('listening_ports'))
                self.assertEqual(error.exception.code,'TIMEOUT')
                self.assertTrue(pidfile.exists())
                with self.assertRaises(ProcessLookupError):os.kill(int(pidfile.read_text()),0)
                self.assertIsNone(runner.process);self.assertIsNone(runner.spawn)
                self.assertIsNone(runner.drain_task);self.assertFalse(runner.io_tasks)
            finally:
                await runner.close()
                if pidfile.exists():
                    try:os.kill(int(pidfile.read_text()),signal.SIGKILL)
                    except ProcessLookupError:pass


class ProcessTests(unittest.TestCase):
    def test_sigterm_cleans_worker_and_real_synthetic_child(self):
        import subprocess
        with tempfile.TemporaryDirectory(prefix='diagnostic-child-') as folder:
            pidfile=Path(folder)/'pid'
            process=subprocess.Popen([sys.executable,'-B',str(FIXTURE),'worker','listening_ports',str(pidfile)],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            child=None
            try:
                process.stdin.write(json.dumps(arguments('listening_ports')).encode());process.stdin.close()
                import time
                deadline=time.monotonic()+10
                while not pidfile.exists() and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(pidfile.exists(),'synthetic child never started')
                child=int(pidfile.read_text());process.send_signal(signal.SIGTERM)
                process.wait(timeout=5)
                self.assertEqual(process.returncode,2)
                with self.assertRaises(ProcessLookupError):os.kill(child,0)
                self.assertEqual(process.stdout.read(),b'');self.assertEqual(process.stderr.read(),b'')
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=5)
                process.stdout.close();process.stderr.close()
                if child is not None:
                    try:os.kill(child,signal.SIGKILL)
                    except ProcessLookupError:pass
