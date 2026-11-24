"""No real auth logs, OOM injection, config mutation or inference."""
import asyncio,json
from datetime import timedelta
from pathlib import Path
import sys,unittest
from unittest.mock import AsyncMock,patch
from agent.diagnostics import system_diagnostics as source
from agent.diagnostics.journal_policy import window,parse_stamp,stamp
from agent.llm.redaction import _clean
from agent.mcp_client import StdioMCPClient,StdioConfig,MCPClientError
from agent.service_binding import DiagnosticBinding
from agent.read_only import ReadOnlySession
ROOT=Path(__file__).resolve().parents[2];FIXTURE=ROOT/'tests/canonical_agent/system_fixture.py'
TOOLS={'memory_oom_events':('oom',{}),'failed_logins':('auth',{}),'check_config':('config',{'profile':'nginx'})}


class ParserTests(unittest.TestCase):
    def test_empty_success_is_distinct_from_permission_or_process_failure(self):
        source.checked(0,b'',b'')
        for code,out,err,expected in ((1,b'',b'','UNSUPPORTED'),(1,b'',b'Permission denied','PERMISSION_DENIED'),
                                    (2,b'',b'','UNSUPPORTED'),(1,b'not-json',b'','UNSUPPORTED')):
            with self.assertRaises(source.SourceError) as e:source.checked(code,out,err)
            self.assertEqual(e.exception.code,expected)

    def test_common_oom_process_and_pid_preserved_private_process_name_withheld(self):
        at=window()['since']
        events,_=source.extract([(at,'kernel','Out of memory: Killed process 456 (nginx) total-vm:2048kB, anon-rss:1024kB')],'memory_oom_events',b'fixture-key')
        self.assertEqual(events[0]['process'],'nginx');self.assertEqual(events[0]['pid'],456)

    def test_fixed_commands_and_read_only_config_sandbox(self):
        a={**window(),'limit':100}
        for tool in ('memory_oom_events','failed_logins'):
            c=source.journal_command(tool,a)
            self.assertEqual(c[0],'/usr/bin/journalctl');self.assertNotIn('sudo',c)
            self.assertEqual(len([s for s in c if s.startswith('--grep=')]),1)
            self.assertIn('--case-sensitive=no',c)
            pattern=next(s for s in c if s.startswith('--grep='))
            self.assertIn('Killed process' if tool=='memory_oom_events' else 'Failed',pattern)
        self.assertIn('--ro-bind',source.CONFIG_COMMAND);self.assertIn('--unshare-pid',source.CONFIG_COMMAND)
        self.assertIn('-t',source.CONFIG_COMMAND);self.assertNotIn('-s',source.CONFIG_COMMAND)
        self.assertNotIn('-T',source.CONFIG_COMMAND)
        with self.assertRaises(source.SourceError):source.journal_command('failed_logins',{**a,'cursor':'fake'})
    def test_user_and_ip_pseudonyms_stable_only_inside_run_without_exposing_raw_data(self):
        at=window()['since'];items=[(at,'sshd','Failed password for invalid user private-canary from 192.0.2.23 port 5522 ssh2')]*2
        events,evidence=source.extract(items,'failed_logins',b'fixture-key')
        self.assertEqual(events[0]['source_ip'],events[1]['source_ip']);self.assertEqual(events[0]['user'],events[1]['user'])
        for raw in ('private-canary','192.0.2.23','fixture-key'):
            self.assertNotIn(raw,json.dumps((events,evidence)))
        other,_=source.extract(items,'failed_logins',b'other-fixture-key')
        self.assertNotEqual(events[0]['source_ip'],other[0]['source_ip'])
    def test_source_timestamps_deduplication_and_forged_metadata(self):
        a=window();at=parse_stamp(a['since'])+timedelta(seconds=1)
        r=dict(__REALTIME_TIMESTAMP=str(int(at.timestamp()*1000000)),PRIORITY='3',MESSAGE='fixture',SYSLOG_IDENTIFIER='sshd',_UID='0',__CURSOR='cursor-1')
        payload=lambda rows: ('\n'.join(json.dumps(x) for x in rows)+'\n').encode()
        self.assertEqual(len(source.records(payload([r,r]),a,'failed_logins')),1)
        with self.assertRaises(source.SourceError):source.records(payload([{**r,'SYSLOG_IDENTIFIER':'unapproved'}]),a,'failed_logins')
        with self.assertRaises(source.SourceError):source.records(payload([{**r,'_UID':'1000'}]),a,'failed_logins')
        with self.assertRaises(source.SourceError):source.records(payload([{**r,'_TRANSPORT':'stdout'}]),a,'memory_oom_events')


class SDKTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_invalid_ram_cannot_reach_model_or_leave_process_owner(self):
        session=ReadOnlySession(scope='oom',backend='mock')
        client=StdioMCPClient(StdioConfig(sys.executable,('-B',str(FIXTURE),'memory_oom_events','invalid_ram'),
            frozenset({'memory_oom_events'})))
        session.client.bindings['memory_oom_events'].client=client
        report=await session.run('Synthetic invalid RAM response; not a Linux diagnosis')
        binding=session.client.bindings['memory_oom_events']
        self.assertEqual(binding.last_error,'MALFORMED_RESULT')
        self.assertFalse(binding.evidence);self.assertIsNone(binding._result)
        self.assertFalse(report.evidence);self.assertFalse(report.citations)
        self.assertNotEqual(report.status,'concluded');self.assertEqual(session.adapter.calls_used,1)
        self.assertEqual(report.tool_calls,1);self.assertFalse(session.cleanup_pending)
        self.assertIsNone(client._owner);self.assertIsNone(session.adapter._owner)

    async def test_memory_snapshot_malformed_values_cannot_become_collector_facts(self):
        import mock_mcp_server as contracts
        from agent.mcp_client import ToolResult
        for fault in ('reversed','negative','boolean','string','extra_field','multiple','missing','not_json'):
            async def response(name,a):
                memory=dict(MemTotal=8*1024**3,MemAvailable=2*1024**3)
                if fault=='reversed':memory['MemAvailable']=memory['MemTotal']+1
                if fault=='negative':memory['MemAvailable']=-1
                if fault=='boolean':memory['MemTotal']=True
                if fault=='string':memory['MemAvailable']='2'
                if fault=='extra_field':memory['unapproved']='synthetic-private-canary'
                now=contracts.current_time()
                evidence=[dict(evidence_id='ev-ram',source='collector.memory_oom_events.meminfo.v1',
                    observed_at=now,unit=None,excerpt='not-json' if fault=='not_json' else json.dumps(memory),
                    redacted=True,truncated=False)]
                if fault=='multiple':evidence.append({**evidence[0],'evidence_id':'ev-ram-other'})
                if fault=='missing':evidence[0].update(source='collector.memory_oom_events.journal.kernel.v1',excerpt='Bounded coverage only')
                envelope=contracts.make_response(name,a['trace_id'],a['logical_tool_call_id'],a['attempt_id'],
                    dict(events=[],count=0,limit=100,coverage=[dict(source='journal.kernel',status='unavailable',oldest_available_at=None)]),
                    a['session_id'],status='partial',evidence=evidence)
                envelope['started_at']=now
                return ToolResult(({'type':'text','text':json.dumps(envelope)},),envelope,False)
            with self.subTest(fault=fault):
                client=AsyncMock();client.call_tool.side_effect=response
                binding=DiagnosticBinding(client,ROOT,redact=_clean,tool='memory_oom_events')
                with self.assertRaises(MCPClientError) as error:await binding.call_tool('memory_oom_events',{})
                self.assertEqual(error.exception.code,'MALFORMED_RESULT');self.assertFalse(binding.evidence)
                self.assertIsNone(binding._result)
                with self.assertRaises(MCPClientError):await binding.call_tool('memory_oom_events',{})
                client.call_tool.assert_awaited_once()

    async def test_config_runtime_failure_is_not_a_syntax_failure(self):
        for diagnostic in (b'[emerg] chown() failed (22: Invalid argument)',
                           b'[emerg] bind() failed; configuration file test failed'):
            with patch.object(source,'run',AsyncMock(return_value=(1,b'',diagnostic))):
                with self.assertRaises(source.SourceError) as error:
                    await source.check_config({'profile':'nginx'})
                self.assertEqual(error.exception.code,'UNSUPPORTED')
        with patch.object(source,'run',AsyncMock(return_value=(1,b'',b'[emerg] unknown directive private-canary'))):
            result=await source.check_config({'profile':'nginx'})
        self.assertFalse(result['valid']);self.assertNotIn('private-canary',json.dumps(result))

    async def test_large_source_ledger_preserved_while_model_projection_is_bounded(self):
        import mock_mcp_server as contracts
        from agent.integration_boundary import ToolMessage
        from datetime import timedelta,datetime,timezone
        c=AsyncMock();b=DiagnosticBinding(c,ROOT,redact=_clean,tool='failed_logins')
        async def response(name,a):
            start=parse_stamp(a['since'])
            items=[(stamp(start+timedelta(seconds=i+1)),'sshd','Failed password for root from 192.0.2.23 port 5522 ssh2') for i in range(20)]
            events,evidence=source.extract(items,'failed_logins',b'synthetic-key')
            data=dict(events=events,count=20,limit=100,coverage=[dict(source='journal.auth',status='ok',oldest_available_at=None)])
            e=contracts.make_response(name,a['trace_id'],a['logical_tool_call_id'],a['attempt_id'],data,a['session_id'],status='partial',evidence=evidence)
            from agent.mcp_client import ToolResult
            return ToolResult(({'type':'text','text':json.dumps(e)},),e,False)
        c.call_tool.side_effect=response
        r=await b.call_tool('failed_logins',{})
        o=b.interpret(ToolMessage('failed_logins',json.dumps({'structuredContent':r.structured_content}),False))
        self.assertEqual(len(b.evidence),20);self.assertEqual(len(o.evidence),3)
        self.assertEqual(json.loads(o.note)['data']['count'],20)
        self.assertTrue(json.loads(o.note)['data']['event_sample_limited'])
        self.assertLess(len(o.note),2500)

    async def connect(self,tool,fault='normal'):
        c=StdioMCPClient(StdioConfig(sys.executable,('-B',str(FIXTURE),tool,fault),frozenset({tool})))
        await c.connect();await c.list_tools();return c
    async def test_discovery_full_schema_correlation_privacy_provenance_and_cleanup(self):
        for tool,(_,args) in TOOLS.items():
            c=await self.connect(tool);b=DiagnosticBinding(c,ROOT,redact=_clean,tool=tool)
            try:
                page=await c.list_tools();d=next(t for t in page.tools if t['name']==tool)
                self.assertEqual(d['inputSchema'],b.schemas[0]);self.assertEqual(d['outputSchema'],b.schemas[2])
                r=await b.call_tool(tool,args);e=r.structured_content
                self.assertFalse(r.is_error);self.assertEqual(json.loads(r.content[0]['text']),e)
                b.validators[2].validate(e)
                for k,v in b.correlation.items():self.assertEqual(e[k],v)
                for bad in ('private-canary','192.0.2.23','ignore previous instructions'):self.assertNotIn(bad,json.dumps(e))
                self.assertTrue(e['evidence'])
                if tool!='check_config':
                    self.assertEqual(e['status'],'partial');self.assertEqual(e['data']['count'],1)
                    self.assertIn(e['data']['events'][0]['evidence_id'],b.evidence)
            finally:await c.disconnect()
            self.assertIsNone(c._owner)
    async def test_empty_vs_permission_denied_source_coverage_not_false_normal(self):
        for tool in ('memory_oom_events','failed_logins'):
            for fault in ('empty','permission'):
                c=await self.connect(tool,fault);b=DiagnosticBinding(c,ROOT,redact=_clean,tool=tool)
                try:
                    if tool=='failed_logins' and fault=='permission':
                        with self.assertRaises(MCPClientError) as e:await b.call_tool(tool,{})
                        self.assertEqual(e.exception.code,'PERMISSION_DENIED');self.assertFalse(b.evidence)
                    else:
                        e=(await b.call_tool(tool,{})).structured_content
                        self.assertEqual(e['data']['count'],0);self.assertEqual(e['status'],'partial')
                        self.assertEqual(e['data']['coverage'][0]['status'],'permission_denied' if fault=='permission' else 'ok')
                finally:await c.disconnect()
    async def test_duplicate_and_error_paths_keep_unknown_no_retry(self):
        for tool in ('memory_oom_events','failed_logins'):
            for fault,code in [('duplicate',None),('timeout','TIMEOUT'),('oversized','OUTPUT_LIMIT'),('malformed','EXECUTION_UNKNOWN'),('wrong_source','EXECUTION_UNKNOWN'),('exit','EXECUTION_UNKNOWN')]:
                c=await self.connect(tool,fault);b=DiagnosticBinding(c,ROOT,redact=_clean,tool=tool)
                try:
                    if code:
                        with self.assertRaises(MCPClientError) as e:await b.call_tool(tool,{})
                        self.assertEqual(e.exception.code,code);self.assertFalse(b.evidence)
                        if code in ('TIMEOUT','EXECUTION_UNKNOWN'):self.assertEqual(b.execution_state,'unknown')
                    else:self.assertEqual((await b.call_tool(tool,{})).structured_content['data']['count'],1)
                    with self.assertRaises(MCPClientError):await b.call_tool(tool,{})
                finally:await c.disconnect()
                self.assertIsNone(c._owner)
    async def test_invalid_config_is_distinct_from_permission_and_no_raw_stderr(self):
        for fault in ('invalid','permission'):
            c=await self.connect('check_config',fault);b=DiagnosticBinding(c,ROOT,redact=_clean,tool='check_config')
            try:
                if fault=='permission':
                    with self.assertRaises(MCPClientError) as e:await b.call_tool('check_config',{'profile':'nginx'})
                    self.assertEqual(e.exception.code,'PERMISSION_DENIED')
                else:
                    e=(await b.call_tool('check_config',{'profile':'nginx'})).structured_content
                    self.assertFalse(e['data']['valid']);self.assertNotIn('private-canary',json.dumps(e))
            finally:await c.disconnect()
    async def test_coordinator_mock_http_and_real_sdk_scoped_paths(self):
        for tool,(scope,args) in TOOLS.items():
            s=ReadOnlySession(scope=scope,backend='mock')
            s.client.bindings[tool].client=StdioMCPClient(StdioConfig(sys.executable,('-B',str(FIXTURE),tool,'normal'),frozenset({tool})))
            r=await s.run('Synthetic source test, not a Linux diagnosis')
            self.assertEqual(r.status,'concluded' if tool=='check_config' else 'limited')
            self.assertTrue(r.citations);self.assertTrue(set(r.citations)<={e.id for e in r.evidence})
            self.assertEqual(r.tool_calls,1);self.assertEqual(s.adapter.calls_used,2)
            self.assertFalse(r.cleanup_pending);self.assertIsNone(s.adapter._owner);self.assertIsNone(s.client._owner)
    async def test_model_cannot_choose_source_path_command_window_or_approval(self):
        for tool,(_,args) in TOOLS.items():
            for extra in (dict(command='sudo'),dict(path='/etc/shadow'),dict(since=window()['since']),dict(approved=True)):
                c=AsyncMock();b=DiagnosticBinding(c,ROOT,redact=_clean,tool=tool)
                with self.assertRaises(MCPClientError):await b.call_tool(tool,{**args,**extra})
                c.call_tool.assert_not_awaited()
