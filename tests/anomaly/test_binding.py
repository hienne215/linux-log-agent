"""Canonical synthetic responses only. No inference or diagnostic commands."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from agent.anomaly import AnomalyBinding
from agent.coordinator import Coordinator, Decision
from agent.integration_boundary import ToolBridge, BridgeBudget
from agent.mcp_client import MCPClientError, ToolResult, StdioMCPClient, StdioConfig

HERE=Path(__file__).parent
ARGS={'unit':'nginx.service','since':'2026-11-01T00:00:00Z','until':'2026-11-05T00:00:00Z'}
SCHEMAS=[json.loads((HERE/'contracts'/('detect_anomaly.'+s)).read_text())
         for s in ('schema.json','response.schema.json','result.schema.json')]


def fixture(request, status='ok'):
    """Test server fixture, never production anomaly computation."""
    return {**{k:request[k] for k in ('schema_version','session_id','trace_id','logical_tool_call_id','attempt_id')},
        'tool':'detect_anomaly','status':status,'started_at':'2026-11-15T00:00:00Z',
        'completed_at':'2026-11-15T00:00:01Z','duration_ms':1000,
        'data':{'anomalies':[{'id':'synthetic-finding-1','type':'service_failures','severity':'high',
            'summary':'SYNTHETIC finding only','observed_count':3,
            'window':{k:request[k] for k in ('since','until')},'evidence_refs':['synthetic-e1'],
            'detection_method':'rule_based'}],'analyzed_signals':['service_failures'],
            'insufficient_signals':[],'findings_truncated':False,'confidence':.8},
        'evidence':[{'evidence_id':'synthetic-e1','source':'synthetic.test.detect_anomaly',
            'observed_at':'2026-11-02T10:00:00Z','unit':'nginx.service','excerpt':'SYNTHETIC fixture only',
            'redacted':False,'truncated':False}],
        'error_code':None,'truncated':False,'next_cursor':None}


def result(data):
    return ToolResult(({'type':'text','text':json.dumps(data)},),data,data['status']=='error')


class Client:
    def __init__(self, change=lambda x: x):
        self.calls=[];self.closed=False;self.change=change
    async def call_tool(self,name,args):
        self.calls.append((name,deepcopy(args)))
        return result(self.change(fixture(args)))
    async def disconnect(self):self.closed=True


class BindingTests(unittest.IsolatedAsyncioTestCase):
    def setup_flow(self, change=lambda x:x, redact=lambda x:x):
        client=Client(change);binding=AnomalyBinding(client,*SCHEMAS,redact=redact)
        bridge=ToolBridge(binding,(binding.trusted_tool,),redact=redact,
                          budget=BridgeBudget(max_calls=1,total_seconds=10,request_seconds=1))
        return client,binding,bridge

    async def test_canonical_metadata_evidence_and_projection(self):
        client,binding,bridge=self.setup_flow()
        message=await bridge.execute('detect_anomaly',ARGS)
        observation=binding.interpret(message)
        self.assertFalse(observation.limited)
        self.assertIn('observed_count', observation.note)
        self.assertEqual(observation.evidence[0].id,'synthetic-e1')
        self.assertEqual(binding.evidence['synthetic-e1']['observed_at'],'2026-11-02T10:00:00Z')
        self.assertEqual(binding.evidence['synthetic-e1']['source'],'synthetic.test.detect_anomaly')
        self.assertEqual(client.calls[0][1]['max_findings'],5)
        self.assertEqual(len(binding.correlation),5)
        self.assertNotIn('trace_id',binding.trusted_tool.parameters['properties'])
        exposed=binding.evidence;exposed.clear()
        self.assertTrue(binding.evidence)
        await bridge.finish_cleanup()

    async def test_allowlist_metadata_injection_and_bad_input_no_dispatch(self):
        for name,args in [('restart_service',ARGS),('detect_anomaly',{**ARGS,'trace_id':'forged'}),
                          ('detect_anomaly',{**ARGS,'unit':'ssh.service'}),
                          ('detect_anomaly',{**ARGS,'since':ARGS['until']}),
                          ('detect_anomaly',{**ARGS,'max_findings':6})]:
            client,binding,bridge=self.setup_flow()
            with self.assertRaises(Exception):await bridge.execute(name,args)
            self.assertEqual(client.calls,[])
            self.assertEqual(binding.execution_state,'not_dispatched')

    async def test_partial_insufficient_or_truncated_is_limited(self):
        for field in ('partial','insufficient_signals','findings_truncated','truncated','confidence'):
            def change(data):
                if field=='partial':data['status']='partial'
                elif field=='truncated':data['truncated']=True
                elif field=='insufficient_signals':data['data'][field]=['journal_errors']
                elif field=='confidence':data['data'][field]=None
                else:data['data'][field]=True
                return data
            client,binding,bridge=self.setup_flow(change)
            obs=binding.interpret(await bridge.execute('detect_anomaly',ARGS))
            self.assertTrue(obs.limited)
            self.assertEqual(len(obs.evidence),1)

    async def test_missing_evidence_not_fabricated(self):
        def change(data):
            data['evidence']=[];data['data']['anomalies'][0]['evidence_refs']=[];return data
        client,binding,bridge=self.setup_flow(change)
        obs=binding.interpret(await bridge.execute('detect_anomaly',ARGS))
        self.assertEqual(obs.evidence,())
        self.assertTrue(obs.limited)
        self.assertEqual(binding.evidence,{})

    async def test_permission_error_placeholders_keep_local_correlation(self):
        def change(data):
            data.update(status='error',error_code='PERMISSION_DENIED',session_id=None,
                trace_id='invalid-input',logical_tool_call_id='invalid-input',attempt_id='invalid-input',
                data={'message':'Request rejected.'},evidence=[])
            return data
        client,binding,bridge=self.setup_flow(change)
        message=await bridge.execute('detect_anomaly',ARGS)
        self.assertTrue(message.is_error)
        self.assertEqual(binding.last_error,'PERMISSION_DENIED')
        self.assertNotEqual(binding.correlation['trace_id'],'invalid-input')
        self.assertEqual(binding.evidence,{})
        self.assertTrue(binding.interpret(message).limited)

    async def test_schema_correlation_references_and_window_fail_closed(self):
        for fault in ('schema','id','reference','time','approval','unit'):
            def change(data):
                if fault=='schema':data['data']['confidence']=2
                elif fault=='id':data['attempt_id']='different'
                elif fault=='reference':data['data']['anomalies'][0]['evidence_refs']=['invented']
                elif fault=='time':data['evidence'][0]['observed_at']='2026-10-01T00:00:00Z'
                elif fault=='unit':data['evidence'][0]['unit']='ssh.service'
                else:data.update(status='approval_required',error_code='APPROVAL_REQUIRED')
                return data
            client,binding,bridge=self.setup_flow(change)
            with self.assertRaises(Exception):await bridge.execute('detect_anomaly',ARGS)
            self.assertEqual(binding.last_error,'MALFORMED_RESULT')
            self.assertEqual(binding.evidence,{})
            self.assertTrue(client.closed)
            self.assertFalse(bridge.has_pending_task)

    async def test_text_structured_disagreement_or_missing_structured(self):
        for empty in (False,True):
            client,binding,bridge=self.setup_flow()
            async def malformed(name,args):
                data=fixture(args)
                return ToolResult(({'type':'text','text':'{}'},),None if empty else data,False)
            client.call_tool=malformed
            with self.assertRaises(Exception):await bridge.execute('detect_anomaly',ARGS)
            self.assertIsNone(binding.result)

    async def test_duplicate_text_json_keys_rejected(self):
        client,binding,bridge=self.setup_flow()
        async def malformed(name,args):
            data=fixture(args)
            return ToolResult(({'type':'text','text':'{"status":"ok",'+json.dumps(data)[1:]},),data,False)
        client.call_tool=malformed
        with self.assertRaises(Exception):await bridge.execute('detect_anomaly',ARGS)
        self.assertEqual(binding.evidence,{})

    async def test_redaction_safe_excerpt_preserves_provenance(self):
        def change(data):
            data['evidence'][0]['excerpt']='SYNTHETIC password=fake-secret';return data
        def redact(text):return text.replace('fake-secret','[REDACTED]')
        client,binding,bridge=self.setup_flow(change,redact)
        message=await bridge.execute('detect_anomaly',ARGS)
        self.assertNotIn('fake-secret',message.content)
        self.assertTrue(binding.local_redaction_applied)
        self.assertEqual(binding.evidence['synthetic-e1']['source'],'synthetic.test.detect_anomaly')

    async def test_redaction_failure_or_provenance_mutation_is_closed(self):
        for change in (False,True):
            def redact(text):
                if change:return text.replace('synthetic.test','altered')
                raise ValueError('password=fake-secret')
            client,binding,bridge=self.setup_flow(redact=redact)
            with self.assertRaises(Exception) as caught:await bridge.execute('detect_anomaly',ARGS)
            self.assertNotIn('fake-secret',str(caught.exception))
            self.assertEqual(binding.evidence,{})

    async def test_oversized_response_no_safe_ledger(self):
        def change(data):data['evidence'][0]['excerpt']='X'*70000;return data
        client,binding,bridge=self.setup_flow(change)
        with self.assertRaises(Exception):await bridge.execute('detect_anomaly',ARGS)
        self.assertEqual(binding.evidence,{})
        self.assertTrue(client.closed)

    async def test_timeout_eof_and_untrusted_exception_no_retry(self):
        for failure,expected in [(MCPClientError('TIMEOUT'),'TIMEOUT'),
                                 (MCPClientError('DISCONNECTED'),'DISCONNECTED'),
                                 (ValueError('password=fake-secret'),'TRANSPORT_ERROR')]:
            client,binding,bridge=self.setup_flow()
            async def broken(name,args):
                client.calls.append((name,args));raise failure
            client.call_tool=broken
            with self.assertRaises(Exception) as caught:await bridge.execute('detect_anomaly',ARGS)
            self.assertEqual(binding.last_error,expected)
            self.assertEqual(binding.execution_state,'unknown')
            self.assertTrue(binding.correlation)
            self.assertEqual(len(client.calls),1)
            self.assertNotIn('fake-secret',str(caught.exception))
            self.assertTrue(client.closed)
            self.assertFalse(bridge.has_pending_task)

    async def test_cancellation_joins_request_and_disconnects(self):
        client,binding,bridge=self.setup_flow();started=asyncio.Event();ended=asyncio.Event()
        async def slow(name,args):
            started.set()
            try:await asyncio.sleep(60)
            finally:ended.set()
        client.call_tool=slow
        task=asyncio.create_task(bridge.execute('detect_anomaly',ARGS))
        await started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(ended.is_set())
        self.assertTrue(client.closed)
        self.assertFalse(bridge.has_pending_task)
        self.assertEqual(binding.execution_state,'unknown')

    async def test_coordinator_citations_and_single_dispatch(self):
        for forged in (False,True):
            client,binding,bridge=self.setup_flow()
            class Model:
                async def choose(self,question,observations):
                    if not observations:return Decision(tool='detect_anomaly',arguments=ARGS)
                    return Decision(conclusion='Synthetic only',citations=('invented' if forged else 'synthetic-e1',))
            c=Coordinator(Model(),bridge,read_only=frozenset({'detect_anomaly'}),
                scope_allows=lambda name,args:args.get('unit')=='nginx.service',
                interpret=binding.interpret,redact=lambda text:text,max_tools=1,max_turns=2)
            try:r=await c.run('fixture')
            finally:
                await c.finish_cleanup();await binding.disconnect()
            self.assertEqual(r.status,'insufficient_evidence' if forged else 'concluded')
            self.assertFalse(r.conclusion_verified)
            self.assertEqual(len(client.calls),1)
            self.assertIsNone(c._operation)


class SDKTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(os.environ.get('SCRUM25_CANONICAL_MOCK'),'optional pinned canonical SDK fixture')
    async def test_official_stdio_canonical_result_and_owner_cleanup(self):
        import mcp.client.stdio as transport
        processes=[];original=transport._create_platform_compatible_process
        async def observe(*args,**kwargs):
            process=await original(*args,**kwargs);processes.append(process);return process
        observer=patch.object(transport,'_create_platform_compatible_process',side_effect=observe)
        observer.start()
        self.addCleanup(observer.stop)
        server=Path(os.environ['SCRUM25_CANONICAL_MOCK'])
        client=StdioMCPClient(StdioConfig(sys.executable,('-B',str(server)),frozenset({'detect_anomaly'})))
        binding=AnomalyBinding(client,*SCHEMAS,redact=lambda text:text)
        bridge=ToolBridge(binding,(binding.trusted_tool,),redact=lambda text:text)
        try:
            await client.connect()
            page=await client.list_tools()
            canonical=next(t for t in page.tools if t['name']=='detect_anomaly')
            self.assertEqual(canonical['inputSchema'],SCHEMAS[0])
            self.assertEqual(canonical['outputSchema'],SCHEMAS[2])
            obs=binding.interpret(await bridge.execute('detect_anomaly',ARGS))
            self.assertTrue(obs.evidence)
            self.assertTrue(all(e.source.startswith('synthetic.mock.') for e in obs.evidence))
        finally:
            await bridge.finish_cleanup();await client.disconnect()
        self.assertIsNone(client._owner)
        self.assertFalse(bridge.has_pending_task)
        self.assertTrue(processes)
        self.assertTrue(all(p.returncode is not None for p in processes))


if __name__=='__main__':unittest.main()
