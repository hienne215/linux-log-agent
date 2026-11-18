"""Bounded native batches, sequential application dispatch and no approval path."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from agent.coordinator import Observation,Evidence
from agent.llm.interface import ToolCall,ToolDefinition,Completion,Usage,AdapterError
from agent.llm.ollama import OllamaAdapter,OllamaConfig
from agent.llm.async_adapter import AsyncLLMError
from agent.llm.decision_source import AsyncDecisionSource

TOOLS=tuple(ToolDefinition(name,'Synthetic read-only tool',{'type':'object','properties':{},'additionalProperties':False})
    for name in ['read_a','read_b'])
CALLS=(ToolCall('read_a',{}),ToolCall('read_b',{}))
COMPLETION=Completion('tool_calls','',CALLS,Usage(None,None,None,None,None),0,OllamaConfig().model,'stop')


def source(calls=CALLS):
    adapter=SimpleNamespace(config=OllamaConfig(max_tool_calls=2),cleanup_pending=False,
        complete=AsyncMock(return_value=replace(COMPLETION,tool_calls=calls)),finish_cleanup=AsyncMock())
    return AsyncDecisionSource(adapter,TOOLS,read_only=frozenset(t.name for t in TOOLS))


class NativeBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_does_not_make_an_extra_model_request(self):
        s=source();a=await s.choose('Synthetic question',())
        b=await s.choose('Synthetic question',(Observation((Evidence('ev-a','synthetic:test','data'),)),))
        self.assertEqual((a.tool,b.tool),('read_a','read_b'))
        s.adapter.complete.assert_awaited_once()
        self.assertFalse(s._pending)
    async def test_invalid_second_call_rejects_entire_batch(self):
        for bad in [ToolCall('restart_service',{}),ToolCall('read_b',{'unexpected':True}),CALLS[0]]:
            s=source((CALLS[0],bad))
            with self.assertRaises(AsyncLLMError):await s.choose('Synthetic',())
            self.assertFalse(s._pending)
    async def test_pending_cannot_be_used_with_another_question_or_without_observation(self):
        for question,observations in [('Other', (Observation(),)),('Synthetic',())]:
            s=source();await s.choose('Synthetic',())
            with self.assertRaises(AsyncLLMError):await s.choose(question,observations)
            self.assertFalse(s._pending)
    async def test_cleanup_discards_pending_proposals_even_if_adapter_close_fails(self):
        s=source();await s.choose('Synthetic',())
        s.adapter.finish_cleanup.side_effect=AsyncLLMError('CLEANUP_PENDING')
        with self.assertRaises(AsyncLLMError):await s.finish_cleanup()
        self.assertFalse(s._pending)
    async def test_default_single_call_policy_preserved(self):
        s=source();s.adapter.config=OllamaConfig()
        with self.assertRaises(AsyncLLMError):await s.choose('Synthetic',())
        self.assertFalse(s._pending)
    async def test_third_native_call_rejected(self):
        s=source(CALLS+(ToolCall('read_a',{}),))
        with self.assertRaises(AsyncLLMError):await s.choose('Synthetic',())
        self.assertFalse(s._pending)


class SyncBatchAdmissionTests(unittest.TestCase):
    def test_two_calls_require_explicit_application_cap(self):
        native=[{'function':{'name':t.name,'arguments':{}}} for t in TOOLS]
        raw=dict(model=OllamaConfig().model,done=True,done_reason='stop',message={'role':'assistant','content':'','tool_calls':native})
        validators={t.name:__import__('jsonschema').Draft202012Validator(t.parameters) for t in TOOLS}
        with self.assertRaises(AdapterError):OllamaAdapter()._normalize(raw,validators,True,0,False)
        r=OllamaAdapter(OllamaConfig(max_tool_calls=2))._normalize(raw,validators,True,0,False)
        self.assertEqual([c.name for c in r.tool_calls],['read_a','read_b'])


if __name__=='__main__':unittest.main()
