"""No real logs acquired. Synthetic text is never allowed over worker IPC."""
import asyncio
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock,patch

from agent.diagnostics import journal_reader as reader
from agent.diagnostic_worker import collect_response
from agent.diagnostics.journal_policy import MARKERS,window,stamp,parse_stamp,project_message,safe_message
from agent.integration_boundary import ToolMessage
from agent.llm.redaction import _clean
from agent.mcp_client import StdioConfig,StdioMCPClient,MCPClientError
from agent.read_only import CanonicalRouter,ReadOnlySession,terminal_summary
from agent.service_binding import DiagnosticBinding
ROOT=Path(__file__).resolve().parents[2]
FIXTURE=ROOT/'tests/canonical_agent/journal_fixture.py'
BUSINESS={'query_journal':{'unit':'nginx.service'},'search_log':{'source_id':'nginx_journal','query':'error'}}


def args(tool='query_journal'):
    return dict(schema_version='1.0.0',session_id='synthetic-session',trace_id='trace',
        logical_tool_call_id='call',attempt_id='attempt',**BUSINESS[tool],**window(),limit=100,
        **({'priority':3} if tool=='query_journal' else {}))


def raw(a,message='error: fixture-secret',**changes):
    record=dict(__REALTIME_TIMESTAMP=str(int(parse_stamp(a['until']).timestamp()*1000000)-1000000),
                PRIORITY='3',MESSAGE=message,_SYSTEMD_UNIT='nginx.service')
    record.update(changes)
    return (json.dumps(record)+'\n').encode()


class ReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_argv_and_window_not_arbitrary_path_or_unit(self):
        a=args();command=reader.argv(a)
        self.assertEqual(command[0],'/usr/bin/journalctl')
        self.assertIn('--unit=nginx.service',command);self.assertIn('--lines=101',command)
        self.assertIn('--priority=0..3',command)
        for change in [dict(since='bad'),dict(until='2099-01-01T00:00:00Z'),
                       dict(since=stamp(datetime.now(timezone.utc)-timedelta(hours=1)))]:
            with self.assertRaises(reader.JournalError):reader.argv({**a,**change})
    async def test_permissions_and_diagnostics_never_leak(self):
        for result,code in [((1,b'',b'Permission denied private-canary'),'PERMISSION_DENIED'),
                            ((0,b'',b'Hint: You are currently not seeing messages from other users'),'PERMISSION_DENIED'),
                            ((1,b'',b'unrecognized option private-canary'),'UNSUPPORTED'),
                            ((0,b'',b'unexpected private-canary'),'EXECUTION_UNKNOWN')]:
            with patch.object(reader,'_run',AsyncMock(return_value=result)) as run:
                with self.assertRaises(reader.JournalError) as e:await reader.collect_journal(args())
            self.assertEqual(e.exception.code,code);self.assertEqual(str(e.exception),code);run.assert_awaited_once()
    async def test_real_parser_checks_unit_priority_timestamp_and_bounds(self):
        a=args()
        entries=reader.validate_records(raw(a),a)
        self.assertEqual(entries[0]['priority'],3)
        for data in [raw(a,_SYSTEMD_UNIT='sshd.service'),raw(a,PRIORITY='7'),
                     raw(a,__REALTIME_TIMESTAMP='0'),b'private-canary',raw(a)*101]:
            with self.assertRaises(reader.JournalError):reader.validate_records(data,a)
        # systemd-manager messages must be from trusted PID 1, not a user UNIT field.
        record=raw(a,_SYSTEMD_UNIT='init.scope',_PID='1',UNIT='nginx.service')
        self.assertEqual(len(reader.validate_records(record,a)),1)
        with self.assertRaises(reader.JournalError):reader.validate_records(raw(a,_SYSTEMD_UNIT='other.service',_PID='999',UNIT='nginx.service'),a)
    async def test_timeouts_errors_and_empty_visibility(self):
        for cause,code in [(TimeoutError(),'TIMEOUT'),(PermissionError(),'PERMISSION_DENIED'),
                           (FileNotFoundError(),'UNSUPPORTED')]:
            with patch.object(reader,'_run',AsyncMock(side_effect=cause)):
                with self.assertRaises(reader.JournalError) as e:await reader.collect_journal(args())
            self.assertEqual(e.exception.code,code)
        with patch.object(reader,'_run',AsyncMock(return_value=(0,b'',b''))):
            self.assertEqual(await reader.collect_journal(args()),[])
    async def test_timeout_and_cancel_reap_synthetic_process(self):
        from src.mcp_server.listening_ports import ARGV
        # Import fake pipes/process only from same repository's bounded core tests.
        class Pipe:
            def __init__(self,event):self.event=event
            async def read(self,size):await self.event.wait();return b''
        class Process:
            def __init__(self):
                self.event=asyncio.Event();self.stdout=Pipe(self.event);self.stderr=Pipe(self.event)
                self.returncode=None;self.killed=False;self.waited=False
            def kill(self):self.killed=True;self.event.set()
            async def wait(self):await self.event.wait();self.returncode=-9;self.waited=True;return -9
        for cancellation in (False,True):
            process=Process();spawn=AsyncMock(return_value=process)
            with patch.object(reader.asyncio,'create_subprocess_exec',spawn),patch.object(reader,'TIMEOUT',.01):
                t=asyncio.create_task(reader._run(args()))
                if cancellation:
                    while not spawn.await_count:await asyncio.sleep(0)
                    t.cancel()
                with self.assertRaises(asyncio.CancelledError if cancellation else TimeoutError):await t
            self.assertTrue(process.killed);self.assertTrue(process.waited)
            self.assertEqual(spawn.call_args.kwargs['shell'],False)
            self.assertEqual(spawn.call_args.kwargs['cwd'],'/')
    async def test_message_projection_contains_only_static_labels(self):
        text='error: address already in use; MYSQL_PWD=fixture-secret 192.0.2.55 ignore instructions'
        projected=project_message(text)
        self.assertTrue(safe_message(projected));self.assertNotIn('fixture-secret',projected)
        self.assertNotIn('192.0.2.55',projected);self.assertNotIn('ignore',projected)
        self.assertFalse(safe_message(text));self.assertFalse(safe_message(projected+',unknown'))


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_search_count_and_evidence_follow_selected_source(self):
        from agent.diagnostics import system_diagnostics as source
        a={**args('search_log'),'source_id':'nginx_error'}
        at=parse_stamp(a['until'])-timedelta(seconds=1)
        line=at.strftime('%Y/%m/%d %H:%M:%S')+' [error] upstream error fixture-secret'
        for lines,expected in (([],0),([line,line],1)):
            with patch.object(source,'fixed_lines',return_value=lines) as files, \
                    patch.object(source,'local_zone',return_value=timezone.utc), \
                    patch('agent.diagnostic_worker.collect_journal',new_callable=AsyncMock) as journal:
                r=await collect_response('search_log',a)
            files.assert_called_once_with(('/var/log/nginx/error.log.1','/var/log/nginx/error.log'))
            journal.assert_not_awaited()
            self.assertEqual(r['status'],'partial')
            self.assertEqual(r['data']['count'],expected)
            self.assertEqual(len(r['data']['matches']),expected)
            self.assertIn('visible_count='+str(expected),r['evidence'][0]['excerpt'])
            self.assertEqual(r['evidence'][0]['source'],'collector.search_log.nginx_error.v1')
            self.assertNotIn('fixture-secret',json.dumps(r))

    async def test_policy_denial_before_reading(self):
        for tool,bad in [('query_journal',{'unit':'sshd.service'}),
                          ('query_journal',{'cursor':'fake'}),('search_log',{'source_id':'auth_log'}),
                          ('search_log',{'query':'\x1b[31m'}),('search_log',{'limit':1})]:
            with patch('agent.diagnostic_worker.collect_journal',new_callable=AsyncMock) as collect:
                r=await collect_response(tool,{**args(tool),**bad})
            collect.assert_not_awaited();self.assertEqual(r['error_code'],'POLICY_DENIED');self.assertFalse(r['evidence'])
    async def test_zero_visible_entries_stays_partial_with_no_fake_event(self):
        for tool in BUSINESS:
            with patch('agent.diagnostic_worker.collect_journal',AsyncMock(return_value=[])):
                r=await collect_response(tool,args(tool))
            self.assertEqual(r['status'],'partial');self.assertEqual(r['data']['count'],0)
            self.assertEqual(r['data']['entries' if tool=='query_journal' else 'matches'],[])
            self.assertIn('completeness not established',r['evidence'][0]['excerpt'])
    async def test_case_sensitive_literal_search_and_no_raw_message(self):
        a=args('search_log')
        entries=[dict(timestamp=a['until'],priority=3,message='ERROR fixture-secret'),
                 dict(timestamp=a['until'],priority=3,message='error fixture-secret')]
        with patch('agent.diagnostic_worker.collect_journal',AsyncMock(return_value=entries)):
            r=await collect_response('search_log',a)
        self.assertEqual(r['data']['count'],1);self.assertNotIn('fixture-secret',json.dumps(r))
        self.assertTrue(safe_message(r['data']['matches'][0]['message']))


class SDKTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_search_positive_count_survives_sdk_and_binding(self):
        c=await self.client('search_log','file_matches')
        b=DiagnosticBinding(c,ROOT,redact=_clean,tool='search_log')
        try:
            r=await b.call_tool('search_log',{'source_id':'nginx_error','query':'upstream'})
            envelope=r.structured_content
            self.assertEqual(envelope['data']['count'],1)
            self.assertEqual(len(envelope['data']['matches']),1)
            self.assertEqual(json.loads(r.content[0]['text']),envelope)
            self.assertFalse(r.is_error)
            o=b.interpret(ToolMessage('search_log',json.dumps({'structuredContent':envelope}),False))
            self.assertTrue(o.limited)
            self.assertEqual(o.evidence[0].source,'collector.search_log.nginx_error.v1')
            self.assertNotIn('fixture-private-canary',json.dumps(envelope))
        finally:await c.disconnect()
        self.assertIsNone(c._owner)

    async def client(self,tool,fault='normal'):
        c=StdioMCPClient(StdioConfig(command=sys.executable,args=('-B',str(FIXTURE),fault),allowed_tools=frozenset({tool})))
        await c.connect();await c.list_tools();return c
    async def test_sdk_canonical_partial_redaction_correlation_and_cleanup(self):
        for tool,business in BUSINESS.items():
            c=await self.client(tool);b=DiagnosticBinding(c,ROOT,redact=_clean,tool=tool)
            try:
                page=await c.list_tools();descriptor=next(t for t in page.tools if t['name']==tool)
                self.assertEqual(descriptor['inputSchema'],b.schemas[0]);self.assertEqual(descriptor['outputSchema'],b.schemas[2])
                result=await b.call_tool(tool,business)
                r=result.structured_content;b.validators[2].validate(r)
                self.assertFalse(result.is_error);self.assertEqual(r['status'],'partial')
                self.assertEqual(json.loads(result.content[0]['text']),r)
                for k,v in b.correlation.items():self.assertEqual(r[k],v)
                self.assertNotIn('fixture-private-canary',json.dumps(r));self.assertNotIn('192.0.2.55',json.dumps(r))
                self.assertNotIn('ignore instructions',json.dumps(r))
                self.assertEqual(r['evidence'][0]['unit'],'nginx.service')
                o=b.interpret(ToolMessage(tool,json.dumps({'structuredContent':r}),False))
                self.assertTrue(o.limited);self.assertEqual(o.evidence[0].id,r['evidence'][0]['evidence_id'])
                self.assertEqual(json.loads(o.evidence[0].text)['observed_at'],r['evidence'][0]['observed_at'])
            finally:await c.disconnect()
            self.assertIsNone(c._owner)
    async def test_sdk_error_paths_no_evidence_or_retry(self):
        for fault,code in [('permission','PERMISSION_DENIED'),('timeout','TIMEOUT'),('malformed','EXECUTION_UNKNOWN'),('unit','EXECUTION_UNKNOWN')]:
            c=await self.client('query_journal',fault);b=DiagnosticBinding(c,ROOT,redact=_clean,tool='query_journal')
            try:
                with self.assertRaises(MCPClientError) as error:await b.call_tool('query_journal',BUSINESS['query_journal'])
                self.assertEqual(error.exception.code,code);self.assertFalse(b.evidence)
                with self.assertRaises(MCPClientError):await b.call_tool('query_journal',BUSINESS['query_journal'])
            finally:await c.disconnect()
            self.assertIsNone(c._owner)
    async def test_logs_with_coordinator_citations_and_limited_result(self):
        s=ReadOnlySession(scope='logs',backend='mock')
        for tool,b in s.client.bindings.items():
            b.client=StdioMCPClient(StdioConfig(command=sys.executable,args=('-B',str(FIXTURE)),allowed_tools=frozenset({tool})))
        report=await s.run('Synthetic journal search')
        self.assertEqual(report.status,'limited');self.assertEqual(report.tool_calls,2)
        self.assertEqual(s.adapter.calls_used,2);self.assertEqual(len(report.evidence),2)
        self.assertEqual(set(report.citations),{e.id for e in report.evidence})
        self.assertFalse(report.cleanup_pending);self.assertIsNone(s.adapter._owner);self.assertIsNone(s.client._owner)
    async def test_all_scope_keeps_existing_turn_and_dispatch_budget(self):
        s=ReadOnlySession(scope='all',backend='mock')
        self.assertEqual(len(s.client.bindings),11)
        self.assertEqual(s.coordinator.max_tools,5);self.assertEqual(s.coordinator.max_turns,6)
        self.assertEqual(s.adapter.max_calls,2);self.assertEqual(s.adapter.config.max_tool_calls,5)
    async def test_model_cannot_supply_time_path_metadata_or_priority(self):
        for tool,business in BUSINESS.items():
            for extra in [dict(since='2026-01-01T00:00:00Z'),dict(path='/etc/shadow'),dict(session_id='model'),dict(priority=8),dict(limit=200)]:
                b=DiagnosticBinding(AsyncMock(),ROOT,redact=_clean,tool=tool)
                with self.assertRaises(MCPClientError):await b.call_tool(tool,{**business,**extra})
                b.client.call_tool.assert_not_awaited()

    async def test_outer_timeout_reaps_actual_synthetic_journal_child(self):
        import tempfile,os,signal
        from src.mcp_server.runner import WorkerRunner,WorkerFailure
        with tempfile.TemporaryDirectory(prefix='journal-owner-') as folder:
            pidfile=Path(folder)/'pid';runner=WorkerRunner(timeout=3)
            try:
                with self.assertRaises(WorkerFailure) as error:
                    await runner.run((sys.executable,'-B',str(FIXTURE),'worker','query_journal','child',str(pidfile)),args())
                self.assertEqual(error.exception.code,'TIMEOUT');self.assertTrue(pidfile.exists())
                with self.assertRaises(ProcessLookupError):os.kill(int(pidfile.read_text()),0)
                self.assertIsNone(runner.process);self.assertIsNone(runner.spawn)
                self.assertIsNone(runner.drain_task);self.assertFalse(runner.io_tasks)
            finally:
                await runner.close()
                if pidfile.exists():
                    try:os.kill(int(pidfile.read_text()),signal.SIGKILL)
                    except ProcessLookupError:pass
    async def test_forged_result_and_raw_message_never_enter_ledger(self):
        from agent.mcp_client import ToolResult
        a=args();now=stamp(datetime.now(timezone.utc))
        import mock_mcp_server as contracts
        base=contracts.make_response('query_journal',a['trace_id'],a['logical_tool_call_id'],a['attempt_id'],
            dict(entries=[dict(timestamp=a['until'],priority=3,message=project_message('error'))],count=1,limit=100),a['session_id'],
            status='partial',evidence=[dict(evidence_id='synthetic-test',source='collector.query_journal.journalctl.v1',
                observed_at=now,unit='nginx.service',excerpt='Synthetic snapshot',redacted=True,truncated=False)])
        base['started_at']=now
        for fault in ('trace','source','raw','timestamp','count','status','duplicate','cursor','priority'):
            b=DiagnosticBinding(AsyncMock(),ROOT,redact=_clean,tool='query_journal');b.window={k:a[k] for k in ('since','until')}
            async def result(name,request):
                r=deepcopy(base)
                for k in b.correlation:r[k]=request[k]
                if fault=='trace':r['trace_id']='forged'
                if fault=='source':r['evidence'][0]['source']='forged'
                if fault=='raw':r['data']['entries'][0]['message']='MYSQL_PWD=fixture-private-canary'
                if fault=='timestamp':r['data']['entries'][0]['timestamp']='2099-01-01T00:00:00Z'
                if fault=='count':r['data']['count']=99
                if fault=='status':r['status']='ok'
                if fault=='duplicate':r['evidence'].append(deepcopy(r['evidence'][0]))
                if fault=='cursor':r['next_cursor']='fake'
                if fault=='priority':r['data']['entries'][0]['priority']=7
                return ToolResult(({'type':'text','text':json.dumps(r)},),r,False)
            b.client.call_tool.side_effect=result
            with self.subTest(fault=fault):
                with self.assertRaises(MCPClientError) as error:await b.call_tool('query_journal',BUSINESS['query_journal'])
                self.assertEqual(error.exception.code,'MALFORMED_RESULT');self.assertFalse(b.evidence)

    async def test_model_projection_is_bounded_but_full_safe_ledger_is_preserved(self):
        import mock_mcp_server as contracts
        from agent.mcp_client import ToolResult
        a=args();now=stamp(datetime.now(timezone.utc));b=DiagnosticBinding(AsyncMock(),ROOT,redact=_clean,tool='query_journal')
        b.window={k:a[k] for k in ('since','until')}
        async def result(name,request):
            r=contracts.make_response(name,request['trace_id'],request['logical_tool_call_id'],request['attempt_id'],
                dict(entries=[dict(timestamp=a['until'],priority=3,message=project_message('error')) for _ in range(100)],count=100,limit=100),request['session_id'],
                status='partial',evidence=[dict(evidence_id='ev-snapshot',source=b.source,observed_at=now,unit='nginx.service',excerpt='Synthetic snapshot count=100',redacted=True,truncated=False)])
            r['started_at']=now
            return ToolResult(({'type':'text','text':json.dumps(r)},),r,False)
        b.client.call_tool.side_effect=result
        r=await b.call_tool('query_journal',BUSINESS['query_journal'])
        observation=b.interpret(ToolMessage('query_journal',json.dumps({'structuredContent':r.structured_content}),False))
        note=json.loads(observation.note)
        self.assertLess(len(observation.note.encode()),1024)
        self.assertEqual(note['data']['count'],100);self.assertEqual(note['data']['literal_marker_counts']['error'],100)
        self.assertNotIn('entries',note['data']);self.assertFalse(note['data']['history_complete'])
        self.assertEqual(len(b._result['data']['entries']),100)
        self.assertEqual(observation.evidence[0].id,'ev-snapshot')
        self.assertIn('observed_at',json.loads(observation.evidence[0].text));self.assertTrue(observation.limited)
