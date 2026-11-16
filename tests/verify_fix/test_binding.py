"""Read-only synthetic fixtures; no inference, commands, remediation or real logs."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from agent.verify_fix import VerifyFixBinding
from agent.coordinator import Coordinator, Decision
from agent.integration_boundary import ToolBridge, BridgeBudget
from agent.mcp_client import MCPClientError, ToolResult, StdioMCPClient, StdioConfig

HERE = Path(__file__).parent
SCHEMAS = [json.loads((HERE/'contracts'/('verify_fix.'+suffix)).read_text())
           for suffix in ('schema.json','response.schema.json','result.schema.json')]
ARGS = dict(unit='nginx.service',since='2026-11-01T00:00:00Z',check_types=['service_active'])
BASELINE = dict(evidence_id='synthetic-before',source='synthetic.baseline',
    observed_at='2026-10-31T00:00:00Z',unit='nginx.service',excerpt='SYNTHETIC before',redacted=True,truncated=False)


def fixture(request):
    return {**{k:request[k] for k in ('schema_version','session_id','trace_id','logical_tool_call_id','attempt_id')},
        'tool':'verify_fix','status':'ok','started_at':'2026-11-15T00:00:00Z',
        'completed_at':'2026-11-15T00:00:01Z','duration_ms':1000,
        'data':{'action_id':request.get('action_id'),'unit':request['unit'],'overall_result':'pass',
            'checks':[dict(check_type=c,result='pass',summary='SYNTHETIC received check only',
                           evidence_refs=['synthetic-after']) for c in request['check_types']],
            'baseline_evidence_refs':request.get('baseline_evidence_refs') or [],
            'verified_at':'2026-11-15T00:00:01Z'},
        'evidence':[dict(evidence_id='synthetic-after',source='synthetic.test.verify',
            observed_at='2026-11-02T00:00:00Z',unit='nginx.service',excerpt='SYNTHETIC after',redacted=True,truncated=False)],
        'error_code':None,'truncated':False,'next_cursor':None}


def tool_result(r):
    return ToolResult(({'type':'text','text':json.dumps(r)},),r,r['status']=='error')


class Client:
    def __init__(self, change=lambda x:x):
        self.calls=[]; self.closed=False; self.change=change
    async def call_tool(self,name,args):
        self.calls.append((name,deepcopy(args)))
        return tool_result(self.change(fixture(args)))
    async def disconnect(self): self.closed=True


class BindingTests(unittest.IsolatedAsyncioTestCase):
    def flow(self, change=lambda x:x, redact=lambda x:x):
        client=Client(change)
        binding=VerifyFixBinding(client,*SCHEMAS,redact=redact,baseline_evidence=(BASELINE,),
                                 action_ids=frozenset({'synthetic-action'}))
        bridge=ToolBridge(binding,(binding.trusted_tool,),redact=redact,
            budget=BridgeBudget(max_calls=1,total_seconds=10,request_seconds=1))
        return client,binding,bridge

    async def test_ok_metadata_and_baseline_provenance(self):
        client,binding,bridge=self.flow()
        args={**ARGS,'baseline_evidence_refs':['synthetic-before'],'action_id':'synthetic-action'}
        observation=binding.interpret(await bridge.execute('verify_fix',args))
        self.assertTrue(binding.checks_passed)
        self.assertFalse(observation.limited)
        self.assertEqual([e.id for e in observation.evidence],['synthetic-after'])
        self.assertEqual(binding.evidence['synthetic-before'],BASELINE)
        self.assertEqual(binding.result['data']['baseline_evidence_refs'],['synthetic-before'])
        self.assertNotIn('trace_id',binding.trusted_tool.parameters['properties'])
        self.assertEqual(len(binding.correlation),5)
        self.assertEqual(client.calls[0][1]['session_id'],binding.session_id)
        binding.evidence.clear();self.assertEqual(len(binding.evidence),2)
        await bridge.finish_cleanup();await binding.disconnect()
        self.assertTrue(client.closed)

    async def test_nullable_fields_absent_null_and_explicit_values(self):
        for extra in ({},{'until':None,'action_id':None,'baseline_evidence_refs':None},
                      {'until':'2026-11-03T00:00:00Z','action_id':'synthetic-action',
                       'baseline_evidence_refs':['synthetic-before']}):
            client,binding,bridge=self.flow()
            binding.interpret(await bridge.execute('verify_fix',{**ARGS,**extra}))
            for key,value in extra.items():self.assertEqual(client.calls[0][1][key],value)
            self.assertEqual(binding.result['data']['action_id'],extra.get('action_id'))
            await binding.disconnect()

    async def test_allowlist_metadata_unknown_baseline_action_and_bad_nullable(self):
        bad=[('restart_service',ARGS),('verify_fix',{**ARGS,'unit':'ssh.service'}),
             ('verify_fix',{**ARGS,'trace_id':'forged'}),('verify_fix',{**ARGS,'until':False}),
             ('verify_fix',{**ARGS,'until':ARGS['since']}),('verify_fix',{**ARGS,'action_id':'forged'}),
             ('verify_fix',{**ARGS,'baseline_evidence_refs':['invented']})]
        for name,args in bad:
            client,binding,bridge=self.flow()
            with self.assertRaises(Exception):await bridge.execute(name,args)
            self.assertEqual(client.calls,[])
            self.assertEqual(binding.execution_state,'not_dispatched')

    async def test_partial_unknown_and_missing_evidence_never_passed(self):
        for fault in ('partial','unknown','missing','truncated','evidence_truncated'):
            def change(r):
                if fault=='partial':r['status']='partial'
                elif fault=='unknown':r['data']['overall_result']='unknown';r['data']['checks'][0]['result']='unknown'
                elif fault=='missing':r['evidence']=[];r['data']['checks'][0]['evidence_refs']=[]
                elif fault=='truncated':r['truncated']=True
                else:r['evidence'][0]['truncated']=True
                return r
            client,binding,bridge=self.flow(change)
            self.assertTrue(binding.interpret(await bridge.execute('verify_fix',ARGS)).limited)
            self.assertFalse(binding.checks_passed)

    async def test_fail_is_not_pass(self):
        def change(r):r['data']['overall_result']='fail';r['data']['checks'][0]['result']='fail';return r
        client,binding,bridge=self.flow(change)
        self.assertTrue(binding.interpret(await bridge.execute('verify_fix',ARGS)).limited)
        self.assertFalse(binding.checks_passed)

    async def test_permission_denied_and_execution_unknown(self):
        for code in ('PERMISSION_DENIED','EXECUTION_UNKNOWN'):
            def change(r):
                r.update(status='error',error_code=code,data={'message':'Synthetic request unavailable.'},evidence=[],
                         session_id=None,trace_id='invalid-input',logical_tool_call_id='invalid-input',attempt_id='invalid-input')
                return r
            client,binding,bridge=self.flow(change)
            message=await bridge.execute('verify_fix',ARGS)
            self.assertTrue(message.is_error)
            self.assertTrue(binding.interpret(message).limited)
            self.assertEqual(binding.last_error,code)
            self.assertNotEqual(binding.correlation['attempt_id'],'invalid-input')
            self.assertFalse(binding.checks_passed)

    async def test_malformed_and_semantic_mismatch_fail_closed(self):
        for fault in ('schema','null_output','unit','action','baseline','check','reference','aggregate','time','metadata','approval'):
            def change(r):
                if fault=='schema':r['data']['extra']='bad'
                elif fault=='null_output':r['data']['baseline_evidence_refs']=None
                elif fault=='unit':r['data']['unit']='ssh.service'
                elif fault=='action':r['data']['action_id']='forged'
                elif fault=='baseline':r['data']['baseline_evidence_refs']=['invented']
                elif fault=='check':r['data']['checks']=[]
                elif fault=='reference':r['data']['checks'][0]['evidence_refs']=['synthetic-before']
                elif fault=='aggregate':r['data']['overall_result']='unknown'
                elif fault=='time':r['evidence'][0]['observed_at']='2026-10-01T00:00:00Z'
                elif fault=='metadata':r['attempt_id']='forged'
                else:r.update(status='approval_required',error_code='APPROVAL_REQUIRED')
                return r
            client,binding,bridge=self.flow(change)
            with self.assertRaises(Exception):await bridge.execute('verify_fix',ARGS)
            self.assertIsNone(binding.result)
            self.assertEqual(binding.last_error,'MALFORMED_RESULT')
            self.assertEqual(binding.evidence,{'synthetic-before':BASELINE})
            self.assertTrue(client.closed);self.assertFalse(bridge.has_pending_task)

    async def test_future_evidence_cannot_verify_received_checks(self):
        def change(r):r['evidence'][0]['observed_at']='2027-01-01T00:00:00Z';return r
        client,binding,bridge=self.flow(change)
        with self.assertRaises(Exception):await bridge.execute('verify_fix',ARGS)
        self.assertFalse(binding.checks_passed)

    async def test_text_structured_mismatch_duplicate_keys_and_missing_native(self):
        for kind in ('mismatch','duplicate','missing'):
            client,binding,bridge=self.flow()
            async def broken(name,args):
                r=fixture(args)
                text='{}' if kind=='mismatch' else '{"status":"ok",'+json.dumps(r)[1:]
                return ToolResult(({'type':'text','text':text},),None if kind=='missing' else r,False)
            client.call_tool=broken
            with self.assertRaises(Exception):await bridge.execute('verify_fix',ARGS)
            self.assertIsNone(binding.result)

    async def test_redaction_and_no_raw_errors(self):
        def change(r):r['evidence'][0]['excerpt']='password=fake-secret';return r
        client,binding,bridge=self.flow(change,lambda x:x.replace('fake-secret','[REDACTED]'))
        message=await bridge.execute('verify_fix',ARGS)
        self.assertNotIn('fake-secret',message.content)
        self.assertTrue(binding.local_redaction_applied)
        self.assertEqual(binding.evidence['synthetic-after']['source'],'synthetic.test.verify')
        for mode in ('raise','provenance'):
            def redact(text):
                if mode=='raise' and text=='synthetic.test.verify':raise ValueError('password=fake-secret')
                return text.replace('synthetic.test.verify','rewritten')
            client,binding,bridge=self.flow(redact=redact)
            with self.assertRaises(Exception) as caught:await bridge.execute('verify_fix',ARGS)
            self.assertNotIn('fake-secret',str(caught.exception));self.assertIsNone(binding.result)

    async def test_oversized_output(self):
        def change(r):r['evidence'][0]['excerpt']='X'*70000;return r
        client,binding,bridge=self.flow(change)
        with self.assertRaises(Exception):await bridge.execute('verify_fix',ARGS)
        self.assertIsNone(binding.result);self.assertTrue(client.closed)

    async def test_pass_without_fresh_references_is_limited(self):
        def change(r):r['data']['checks'][0]['evidence_refs']=[];return r
        client,binding,bridge=self.flow(change)
        self.assertTrue(binding.interpret(await bridge.execute('verify_fix',ARGS)).limited)
        self.assertFalse(binding.checks_passed)

    async def test_is_error_mapping_and_baseline_config_fail_closed(self):
        client,binding,bridge=self.flow()
        async def mismatch(name,args):
            r=fixture(args);return ToolResult(({'type':'text','text':json.dumps(r)},),r,True)
        client.call_tool=mismatch
        with self.assertRaises(Exception):await bridge.execute('verify_fix',ARGS)
        self.assertIsNone(binding.result)
        with self.assertRaises(MCPClientError) as caught:
            VerifyFixBinding(Client(),*SCHEMAS,redact=lambda x:x.replace('SYNTHETIC before','changed'),
                             baseline_evidence=(BASELINE,))
        self.assertEqual(caught.exception.code,'INVALID_CONFIG')

    async def test_timeout_disconnect_and_untrusted_exception_no_retry(self):
        for failure,code in ((MCPClientError('TIMEOUT'),'TIMEOUT'),
                             (MCPClientError('DISCONNECTED'),'DISCONNECTED'),
                             (ValueError('password=fake-secret'),'TRANSPORT_ERROR')):
            client,binding,bridge=self.flow()
            async def broken(name,args):client.calls.append((name,args));raise failure
            client.call_tool=broken
            with self.assertRaises(Exception) as caught:await bridge.execute('verify_fix',ARGS)
            self.assertEqual(binding.last_error,code);self.assertEqual(binding.execution_state,'unknown')
            self.assertEqual(len(client.calls),1);self.assertTrue(binding.correlation)
            self.assertNotIn('fake-secret',str(caught.exception))
            self.assertTrue(client.closed);self.assertFalse(bridge.has_pending_task)

    async def test_cancellation_owns_cleanup(self):
        client,binding,bridge=self.flow();started=asyncio.Event();finished=asyncio.Event()
        async def slow(name,args):
            started.set()
            try:await asyncio.sleep(60)
            finally:finished.set()
        client.call_tool=slow
        task=asyncio.create_task(bridge.execute('verify_fix',ARGS));await started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(finished.is_set());self.assertTrue(client.closed)
        self.assertFalse(bridge.has_pending_task);self.assertEqual(binding.execution_state,'unknown')

    async def test_coordinator_citation_validation_and_cleanup(self):
        for forged in (False,True):
            client,binding,bridge=self.flow()
            class Model:
                async def choose(self,question,observations):
                    if not observations:return Decision(tool='verify_fix',arguments=ARGS)
                    return Decision(conclusion='Synthetic checks only; no repair performed.',
                                    citations=('invented' if forged else 'synthetic-after',))
            coordinator=Coordinator(Model(),bridge,read_only=frozenset({'verify_fix'}),
                scope_allows=lambda name,args:args.get('unit')=='nginx.service',interpret=binding.interpret,
                redact=lambda x:x,max_tools=1,max_turns=2)
            try:r=await coordinator.run('Synthetic verification')
            finally:await coordinator.finish_cleanup();await binding.disconnect()
            self.assertEqual(r.status,'insufficient_evidence' if forged else 'concluded')
            self.assertFalse(r.conclusion_verified);self.assertIsNone(coordinator._operation)
            self.assertEqual(len(client.calls),1);self.assertTrue(client.closed)


class SDKTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(os.environ.get('SCRUM26_CANONICAL_MOCK'),'optional pinned canonical SDK fixture')
    async def test_canonical_mock_partial_unknown_and_process_cleanup(self):
        import mcp.client.stdio as transport
        handles=[];original=transport._create_platform_compatible_process
        async def observe(*a,**kw):
            p=await original(*a,**kw);handles.append(p);return p
        with patch.object(transport,'_create_platform_compatible_process',side_effect=observe):
            client=StdioMCPClient(StdioConfig(sys.executable,('-B',os.environ['SCRUM26_CANONICAL_MOCK']),frozenset({'verify_fix'})))
            binding=VerifyFixBinding(client,*SCHEMAS,redact=lambda x:x)
            bridge=ToolBridge(binding,(binding.trusted_tool,),redact=lambda x:x)
            try:
                await client.connect();page=await client.list_tools()
                tool=next(t for t in page.tools if t['name']=='verify_fix')
                self.assertEqual(tool['inputSchema'],SCHEMAS[0]);self.assertEqual(tool['outputSchema'],SCHEMAS[2])
                observation=binding.interpret(await bridge.execute('verify_fix',{**ARGS,'until':None,
                    'action_id':None,'baseline_evidence_refs':None}))
                self.assertTrue(observation.limited);self.assertFalse(binding.checks_passed)
                self.assertEqual(binding.result['status'],'partial')
                self.assertEqual(binding.result['data']['overall_result'],'unknown')
                self.assertEqual(observation.evidence,())
            finally:await bridge.finish_cleanup();await client.disconnect()
            self.assertIsNone(client._owner);self.assertFalse(bridge.has_pending_task)
            self.assertTrue(handles);self.assertTrue(all(p.returncode is not None for p in handles))
