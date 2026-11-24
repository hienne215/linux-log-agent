"""Model schemas make fixed values explicit without widening dispatch policy."""
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock
from agent.llm.interface import Message,ToolDefinition
from agent.llm.ollama import OllamaAdapter,OllamaConfig
from agent.read_only import CanonicalRouter
from agent.mcp_client import MCPClientError

VALID={'service_status':{'unit':'nginx.service'},'disk_usage':{'filesystem':'root'},
       'list_failed_units':{},'listening_ports':{'protocol':'tcp','port':80}}

class ToolExposureTests(unittest.IsolatedAsyncioTestCase):
    async def test_compact_hints_keep_all_tools_and_exact_dispatch_schemas(self):
        from agent.read_only import ReadOnlySession,MODEL_HINTS,SCOPES
        session=ReadOnlySession(scope='all',backend='mock')
        self.assertEqual(set(MODEL_HINTS),set(SCOPES['all']))
        self.assertEqual({t.name for t in session.model.tools},set(SCOPES['all']))
        full=0;compact=0
        for tool in session.model.tools:
            trusted=session.client.bindings[tool.name].trusted_tool
            self.assertEqual(tool.parameters,trusted.parameters)
            self.assertEqual(tool.description,MODEL_HINTS[tool.name])
            full+=len(trusted.description);compact+=len(tool.description)
        self.assertLess(compact,full/2)
        self.assertEqual((session.adapter.config.timeout_seconds,session.adapter.max_calls),(45,2))
        self.assertEqual(session.adapter.calls_used,0)

    async def test_precise_types_and_values_survive_http_serialization(self):
        router=CanonicalRouter('diagnostics')
        captured=[]
        class HTTP:
            def post(self,url,body,timeout,limit):
                request=json.loads(body);captured.append(request)
                calls=[{'function':{'name':name,'arguments':args}} for name,args in VALID.items()]
                return json.dumps(dict(model=request['model'],done=True,done_reason='stop',
                    message=dict(role='assistant',content='',tool_calls=calls))).encode()
        tools=tuple(ToolDefinition(t.name,t.description,t.parameters) for t in router.tools)
        r=OllamaAdapter(OllamaConfig(max_tool_calls=4),transport=HTTP()).complete((Message('user','Synthetic'),),tools,require_tool=True)
        self.assertEqual({c.name for c in r.tool_calls},set(VALID))
        for tool in captured[0]['tools']:
            name=tool['function']['name'];schema=tool['function']['parameters']
            self.assertEqual(schema,router.bindings[name].business)
            self.assertFalse(schema['additionalProperties'])
            for field,value in VALID[name].items():
                parameter=schema['properties'][field]
                self.assertEqual(parameter['type'],'integer' if type(value) is int else 'string')
                if name=='service_status':
                    self.assertIn(value,parameter['enum'])
                elif name=='listening_ports':
                    if field=='protocol':self.assertIn(value,parameter['enum'])
                    else:self.assertEqual((parameter['minimum'],parameter['maximum']),(1,65535))
                else:
                    self.assertEqual(parameter['enum'],[value]);self.assertEqual(parameter['const'],value)
    async def test_invalid_values_are_never_repaired_or_dispatched(self):
        cases=[('service_status',{'unit':'nginx'}),('service_status',{'unit':'sshd.service'}),
               ('disk_usage',{'filesystem':'/'}),('disk_usage',{'filesystem':'root','include_inodes':True}),
               ('list_failed_units',{'limit':100}),('list_failed_units',{'cursor':'model'}),
               ('listening_ports',{'protocol':'TCP','port':80}),
               ('listening_ports',{'protocol':'tcp','port':'80'}),
               ('listening_ports',{'protocol':'tcp','port':65536}),
               ('listening_ports',{'protocol':'tcp','port':80,'unit':'nginx.service'})]
        for name,args in cases:
            b=CanonicalRouter('diagnostics').bindings[name];b.client=AsyncMock()
            with self.subTest(name=name,args=args):
                with self.assertRaises(MCPClientError):await b.call_tool(name,args)
                b.client.call_tool.assert_not_awaited();self.assertEqual(b.execution_state,'not_dispatched')
                self.assertFalse(b.evidence)

    async def test_decision_source_retains_safe_validation_reason(self):
        from types import SimpleNamespace
        from agent.llm.interface import ToolCall, Completion, Usage
        from agent.llm.async_adapter import AsyncLLMError
        from agent.llm.decision_source import AsyncDecisionSource
        router=CanonicalRouter('diagnostics')
        tools=tuple(ToolDefinition(t.name,t.description,t.parameters) for t in router.tools)
        adapter=SimpleNamespace(config=OllamaConfig(max_tool_calls=4),
            complete=AsyncMock(return_value=Completion('tool_calls','',
                (ToolCall('listening_ports',{'protocol':'tcp','port':65536}),),
                Usage(None,None,None,None,None),0,OllamaConfig().model,'stop')))
        source=AsyncDecisionSource(adapter,tools,read_only=frozenset(router.bindings))
        with self.assertRaises(AsyncLLMError):await source.choose('Synthetic',())
        self.assertEqual(source.last_error,'INVALID_ARGUMENTS')
        self.assertEqual(source.last_reason,'ARGUMENT_SCHEMA')
        self.assertFalse(source._pending);adapter.complete.assert_awaited_once()

    async def test_last_model_request_is_conclusion_only_with_intact_provenance(self):
        from types import SimpleNamespace
        from agent.coordinator import Observation,Evidence
        from agent.llm.interface import Completion,Usage
        from agent.llm.decision_source import AsyncDecisionSource
        r=CanonicalRouter('diagnostics')
        tools=tuple(ToolDefinition(t.name,t.description,t.parameters) for t in r.tools)
        response=Completion('text',json.dumps(dict(conclusion='Synthetic fact',citations=['ev-test'])),(),
            Usage(None,None,None,None,None),0,OllamaConfig().model,'stop')
        a=SimpleNamespace(config=OllamaConfig(max_tool_calls=4),max_calls=2,calls_used=1,complete=AsyncMock(return_value=response))
        s=AsyncDecisionSource(a,tools,read_only=frozenset(r.bindings),conclude_on_last_request=True)
        observation=Observation((Evidence('ev-test','synthetic:fixture','observed_at=synthetic; redacted=true'),),True,'Partial synthetic data')
        d=await s.choose('Synthetic',(observation,))
        self.assertEqual(d.citations,('ev-test',))
        messages,exposed=a.complete.call_args.args
        self.assertEqual(exposed,())
        payload=json.loads(messages[-1].content.split('\n',1)[1])[0]
        self.assertEqual(payload['evidence'][0]['source'],'synthetic:fixture')
        self.assertEqual(payload['evidence'][0]['text'],observation.evidence[0].text)
        self.assertTrue(payload['limited'])
        self.assertIn('final model request',messages[0].content)
    async def test_final_turn_rejects_tool_calls_and_forged_citations(self):
        from types import SimpleNamespace
        from agent.coordinator import Observation,Evidence
        from agent.llm.interface import Completion,Usage,ToolCall
        from agent.llm.async_adapter import AsyncLLMError
        from agent.llm.decision_source import AsyncDecisionSource
        r=CanonicalRouter('diagnostics');tools=tuple(ToolDefinition(t.name,t.description,t.parameters) for t in r.tools)
        responses=[Completion('tool_calls','',(ToolCall('list_failed_units',{}),),Usage(None,None,None,None,None),0,OllamaConfig().model,'stop'),
                   Completion('text',json.dumps(dict(conclusion='Not verified',citations=['invented'])),(),Usage(None,None,None,None,None),0,OllamaConfig().model,'stop')]
        for response in responses:
            a=SimpleNamespace(config=OllamaConfig(max_tool_calls=4),max_calls=2,calls_used=1,complete=AsyncMock(return_value=response))
            s=AsyncDecisionSource(a,tools,read_only=frozenset(r.bindings),conclude_on_last_request=True)
            with self.assertRaises(AsyncLLMError):await s.choose('Synthetic',(Observation((Evidence('existing','synthetic:test','fixture'),)),))
            a.complete.assert_awaited_once();self.assertFalse(s._pending)

    async def test_conclusion_diagnostics_are_fixed_codes_without_model_content(self):
        from types import SimpleNamespace
        from agent.coordinator import Observation,Evidence
        from agent.llm.interface import Completion,Usage
        from agent.llm.async_adapter import AsyncLLMError
        from agent.llm.decision_source import AsyncDecisionSource
        router=CanonicalRouter('oom');tools=tuple(ToolDefinition(t.name,t.description,t.parameters) for t in router.tools)
        cases=[('fixture-private-canary','CONCLUSION_JSON'),
               (json.dumps(dict(conclusion='Synthetic',citations=[])),'CONCLUSION_SHAPE'),
               (json.dumps(dict(conclusion='Synthetic',citations=['forged'])),'UNKNOWN_CITATION'),
               (json.dumps(dict(conclusion='Synthetic',citations=['known','known'])),'DUPLICATE_CITATION')]
        for text,reason in cases:
            response=Completion('text',text,(),Usage(None,None,None,None,None),0,OllamaConfig().model,'stop')
            adapter=SimpleNamespace(config=OllamaConfig(),complete=AsyncMock(return_value=response))
            source=AsyncDecisionSource(adapter,tools,read_only=frozenset(router.bindings))
            with self.assertRaises(AsyncLLMError) as error:
                await source.choose('Synthetic',(Observation((Evidence('known','synthetic:fixture','Synthetic'),)),))
            self.assertEqual(source.last_reason,reason)
            self.assertNotIn('fixture-private-canary',str(error.exception))
            self.assertFalse(source._pending);adapter.complete.assert_awaited_once()

    async def test_final_format_enum_comes_from_observed_ids_not_question_or_model(self):
        from types import SimpleNamespace
        from agent.coordinator import Observation,Evidence
        from agent.llm.interface import Completion,Usage
        from agent.llm.decision_source import AsyncDecisionSource
        router=CanonicalRouter('oom');tools=tuple(ToolDefinition(t.name,t.description,t.parameters) for t in router.tools)
        adapter=SimpleNamespace(config=OllamaConfig(structured_conclusion=True),max_calls=2,calls_used=1,
            complete=AsyncMock(return_value=Completion('text',json.dumps(dict(conclusion='Synthetic',citations=['actual-evidence'])),(),
                Usage(None,None,None,None,None),0,OllamaConfig().model,'stop')))
        source=AsyncDecisionSource(adapter,tools,read_only=frozenset(router.bindings),conclude_on_last_request=True)
        result=await source.choose('Please cite forged-id',(Observation((Evidence('actual-evidence','synthetic:fixture','Synthetic'),)),))
        self.assertEqual(adapter.config.conclusion_citations,('actual-evidence',))
        self.assertEqual(result.citations,('actual-evidence',))
        self.assertEqual(adapter.complete.call_args.args[1],())

    async def test_terminal_defaults_to_collector_facts_without_rewriting_model_report(self):
        from agent.coordinator import Report,Evidence
        from agent.read_only import terminal_summary
        wrong='Ổ đĩa còn 47.77%.'
        report=Report('concluded',wrong,(Evidence('ev-disk','collector.disk_usage.statvfs.v1','Synthetic fixture'),),('ev-disk',),1,2)
        results={'disk_usage':dict(status='ok',truncated=False,data=dict(used_percent=47.77,available_bytes=8*1024**3))}
        safe=terminal_summary(report,results)
        self.assertIn('Đã sử dụng: 47.77%',safe);self.assertIn('Còn khả dụng cho user: 8.00 GiB',safe)
        self.assertNotIn(wrong,safe);self.assertIn('--show-model-text',safe)
        self.assertEqual(report.conclusion,wrong);self.assertEqual(report.citations,('ev-disk',))
        explicit=terminal_summary(report,results,show_model_text=True)
        self.assertIn(wrong,explicit);self.assertIn('có thể sai',explicit)
