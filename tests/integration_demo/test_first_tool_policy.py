"""First-turn contract with actual request builder and mock HTTP; no live backend."""
import asyncio
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import unittest
from unittest.mock import patch

from agent.coordinator import Evidence, Observation
from agent.llm.async_adapter import AsyncLLMError
from agent.llm.decision_source import AsyncDecisionSource
from agent.llm.interface import AdapterError, ToolDefinition
from agent.llm.ollama import OllamaAdapter
from agent.integration_demo.fixtures import PARAMETERS
from agent.integration_demo import __main__ as cli
import test_backends as backend_tests

TOOL = ToolDefinition('service_status', 'Read synthetic service state', PARAMETERS)


class HTTP:
    def __init__(self, messages):
        self.messages = list(messages)
        self.requests = []

    def post(self, url, body, timeout, limit):
        self.requests.append(json.loads(body))
        return json.dumps({'model': self.requests[-1]['model'], 'done': True,
                           'done_reason': 'stop', 'message': self.messages.pop(0)}).encode()


class MockAsyncHTTPAdapter:
    """Real sync payload/normalization, immediate in-memory transport only."""
    def __init__(self, messages):
        self.http = HTTP(messages)
        self.require_tool = []

    async def complete(self, messages, tools, *, require_tool=False):
        self.require_tool.append(require_tool)
        try:
            return OllamaAdapter(transport=self.http).complete(messages, tools, require_tool=require_tool)
        except AdapterError as error:
            raise AsyncLLMError(error.code) from None


def native(name='service_status', arguments=None):
    return {'role': 'assistant', 'content': '', 'tool_calls': [
        {'function': {'name': name, 'arguments': arguments or {'unit': 'nginx.service'}}}]}


def text(value):
    return {'role': 'assistant', 'content': value}


class RequestPolicyTests(unittest.IsolatedAsyncioTestCase):
    def flow(self, messages):
        adapter = MockAsyncHTTPAdapter(messages)
        source = AsyncDecisionSource(adapter, (TOOL,), read_only=frozenset({'service_status'}))
        return source, adapter

    async def test_first_text_is_output_policy_without_parse_or_retry(self):
        for value in ('synthetic prose', '{"conclusion":"fixture", "citations":[]}',
                      '{"conclusion":"fixture", "citations":["invented"]}',
                      '{"function":{"name":"service_status","arguments":{"unit":"nginx.service"}}}'):
            with self.subTest(format='synthetic text'):
                source, adapter = self.flow([text(value)])
                with self.assertRaises(AsyncLLMError) as caught:
                    await source.choose('Kiểm tra trạng thái nginx.service', ())
                self.assertEqual(caught.exception.code, 'OUTPUT_POLICY')
                self.assertEqual(source.last_error, 'OUTPUT_POLICY')
                self.assertEqual(adapter.require_tool, [True])
                self.assertEqual(len(adapter.http.requests), 1)

    async def test_first_native_and_actual_payload_exposure(self):
        source, adapter = self.flow([native()])
        d = await source.choose('Kiểm tra trạng thái nginx.service', ())
        self.assertEqual((d.tool, d.arguments), ('service_status', {'unit': 'nginx.service'}))
        body, = adapter.http.requests
        self.assertEqual(adapter.require_tool, [True])
        self.assertEqual([t['function']['name'] for t in body['tools']], ['service_status'])
        self.assertEqual(body['tools'][0]['function']['parameters'], PARAMETERS)
        self.assertEqual(body['options'], {'num_ctx':4096, 'num_predict':256, 'temperature':0, 'num_thread':4})
        self.assertFalse(body['stream'])
        self.assertNotIn('think', body)
        self.assertNotIn('tool_choice', body)
        self.assertNotIn('require_tool', body)  # local validator, not a native API field
        self.assertIn('Before any evidence', body['messages'][0]['content'])
        self.assertEqual([m['role'] for m in body['messages']], ['system', 'user', 'user'])
        self.assertEqual(body['messages'][-1]['content'].split('\n',1)[1], '[]')

    async def test_evidence_allows_valid_conclusion_without_forcing_tool(self):
        source, adapter = self.flow([native(), text(json.dumps({'conclusion':'Synthetic active only', 'citations':['e1']}))])
        await source.choose('fixture', ())
        d = await source.choose('fixture', (Observation((Evidence('e1','synthetic:test','fixture active'),)),))
        self.assertEqual(d.citations, ('e1',))
        self.assertEqual(adapter.require_tool, [True, False])
        self.assertEqual(len(adapter.http.requests), 2)
        self.assertIn('UNTRUSTED OBSERVATIONS', adapter.http.requests[-1]['messages'][-1]['content'])

    async def test_empty_observation_does_not_allow_uncited_conclusion(self):
        source, adapter = self.flow([text('{"conclusion":"fixture","citations":[]}')])
        with self.assertRaises(AsyncLLMError) as caught:
            await source.choose('fixture', (Observation(()),))
        self.assertEqual(caught.exception.code, 'OUTPUT_POLICY')
        self.assertEqual(adapter.require_tool, [True])

    async def test_conclusion_missing_or_forged_citations_after_evidence(self):
        for citations in (None, [], ['invented']):
            payload = {'conclusion':'fixture'}
            if citations is not None: payload['citations'] = citations
            source, adapter = self.flow([text(json.dumps(payload))])
            with self.assertRaises(AsyncLLMError) as caught:
                await source.choose('fixture', (Observation((Evidence('e1','synthetic:test','active'),)),))
            self.assertEqual(caught.exception.code, 'INVALID_CONCLUSION')
            self.assertEqual(len(adapter.http.requests), 1)

    async def test_malformed_wrong_tool_wrong_unit_and_write_fail_closed(self):
        cases = [({'role':'assistant','content':'','tool_calls':'invalid'}, 'MALFORMED_RESPONSE'),
                 (native('not_in_registry'),'INVALID_ARGUMENTS'),
                 (native(arguments={'unit':'ssh.service'}),'INVALID_ARGUMENTS'),
                 (native('restart_service'),'INVALID_ARGUMENTS')]
        for message, expected in cases:
            source, adapter = self.flow([message])
            with self.assertRaises(AsyncLLMError) as caught:
                await source.choose('fixture', ())
            self.assertEqual(caught.exception.code, expected)
            self.assertEqual(source.last_error, expected)
            self.assertEqual(len(adapter.http.requests), 1)

    async def test_tool_exposure_mismatch_rejected_before_http(self):
        with self.assertRaises(AsyncLLMError):
            AsyncDecisionSource(None, (TOOL,), read_only=frozenset({'query_journal'}))


class FirstTurnSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await backend_tests.LiveModeMockTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await backend_tests.LiveModeMockTests.asyncTearDown(self)

    def session(self, **kw):
        return backend_tests.LiveModeMockTests.session(self, **kw)

    async def test_text_only_process_cleanup_no_tool_no_second_request(self):
        self.fault = 'text-only'
        s = self.session()
        report = await s.run('fixture')
        self.assertEqual(s.model.last_error, 'OUTPUT_POLICY')
        self.assertEqual((s.adapter.calls_used, s.bridge.calls_used, report.llm_turns), (1,0,1))
        self.assertEqual(report.execution_state, 'not_dispatched')
        self.assertEqual(report.evidence, ())
        self.assertFalse(s.cleanup_pending)
        self.assertIsNone(s.adapter._owner)
        self.assertIsNone(s.client._owner)
        self.assertIsNone(s.coordinator._operation)
        self.assertFalse(s.bridge.has_pending_task)

    async def test_bad_citation_keeps_synthetic_evidence_and_two_request_cap(self):
        self.fault = 'bad-citation'
        s = self.session()
        report = await s.run('fixture')
        self.assertEqual(s.model.last_error, 'INVALID_CONCLUSION')
        self.assertEqual((s.adapter.calls_used, s.bridge.calls_used), (2,1))
        self.assertEqual(len(report.evidence), 1)
        self.assertEqual(report.citations, ())
        self.assertFalse(s.cleanup_pending)

    async def test_cli_safe_diagnostic_does_not_echo_text_or_prompt(self):
        self.fault = 'text-only'
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            report = await cli.drive('password=synthetic-secret', 'active', backend='ollama')
        self.assertEqual(report.status, 'data_policy')
        self.assertIn('LLM_ERROR: OUTPUT_POLICY', stderr.getvalue())
        self.assertNotIn('synthetic-secret', stdout.getvalue()+stderr.getvalue())
        self.assertNotIn('prose sentinel', stdout.getvalue()+stderr.getvalue())

    async def test_discovered_tools_do_not_expand_active_exposure(self):
        s = self.session()
        report = await s.run('fixture')
        self.assertEqual(report.status, 'concluded')
        self.assertEqual({t.name for t in s.model.tools}, {'service_status'})
        self.assertEqual(s.client.config.allowed_tools, frozenset({'service_status'}))
        self.assertIn('query_journal', s.discovered)
        self.assertEqual((report.tool_calls, report.llm_turns), (1,2))
