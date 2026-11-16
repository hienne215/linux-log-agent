"""PR #8 Coordinator + PR #7 Bridge, mock HTTP and in-memory MCP only."""
import asyncio
import json
from pathlib import Path
import sys
import unittest

from agent.coordinator import Coordinator, Evidence, Observation
from agent.integration_boundary import ToolBridge, TrustedTool, BridgeBudget
from agent.mcp_client.client import ToolResult
from agent.llm.async_adapter import AsyncOllamaAdapter, AsyncLLMError
from agent.llm.decision_source import AsyncDecisionSource, run_coordinator
from test_async import TOOL, wait_until


class MockClient:
    def __init__(self):
        self.calls = []
        self.closed = False

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        # Deliberately test-only data; not an SCRUM-12 envelope.
        return ToolResult((), {'fixture_number': len(self.calls)}, False)

    async def disconnect(self):
        self.closed = True


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setup_flow(self, mode='auto', **config):
        worker = (sys.executable, '-B', str(Path(__file__).with_name('worker_fixture.py')), mode)
        adapter = AsyncOllamaAdapter(_worker_command=worker, **config)
        source = AsyncDecisionSource(adapter, (TOOL,), read_only=frozenset({'service_status'}))
        client = MockClient()
        bridge = ToolBridge(client, (TrustedTool(TOOL.name, TOOL.description, TOOL.parameters, True),),
                            redact=lambda text:text, budget=BridgeBudget(max_calls=8, total_seconds=120))
        def interpret(message):
            number = json.loads(message.content)['structuredContent']['fixture_number']
            return Observation((Evidence('e'+str(number), 'synthetic:status', 'nginx active'),))
        c = Coordinator(source, bridge, read_only=frozenset({'service_status'}),
                        scope_allows=lambda name,args:args=={'unit':'nginx.service'},
                        interpret=interpret, redact=lambda text:text)
        return c, source, adapter, client

    async def test_two_llm_turns_one_mock_tool_and_cited_conclusion(self):
        c, source, adapter, client = self.setup_flow()
        report = await run_coordinator(c, 'Kiểm tra Nginx')
        self.assertEqual(report.status, 'concluded')
        self.assertEqual(report.citations, ('e1',))
        self.assertFalse(report.conclusion_verified)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(adapter.calls_used, 2)
        self.assertFalse(adapter.cleanup_pending)
        self.assertIsNone(c._operation)

    async def test_invalid_outputs_never_dispatch_mcp(self):
        for mode in ('malformed','write','bad-args','text-tool','forged-citation'):
            with self.subTest(mode=mode):
                c, source, adapter, client = self.setup_flow(mode)
                report = await run_coordinator(c, 'test')
                self.assertEqual(client.calls, [])
                self.assertNotEqual(report.status, 'concluded')
                self.assertIsNotNone(source.last_error)
                self.assertFalse(adapter.cleanup_pending)

    async def test_timeout_and_transport_model_errors_remain_distinguishable(self):
        for mode, code, status in (('timeout','TIMEOUT','budget_exhausted'),
                                   ('disconnect','CONNECTION_ERROR','data_policy'),
                                   ('missing-model','MODEL_NOT_FOUND','data_policy')):
            with self.subTest(mode=mode):
                c, source, adapter, client = self.setup_flow(mode)
                report = await run_coordinator(c, 'test')
                self.assertEqual(source.last_error, code)
                self.assertEqual(report.status, status)
                self.assertEqual(client.calls, [])
                self.assertFalse(adapter.cleanup_pending)

    async def test_coordinator_six_turn_budget(self):
        c, source, adapter, client = self.setup_flow('tool')
        report = await run_coordinator(c, 'test')
        self.assertEqual(report.status, 'budget_exhausted')
        self.assertEqual((report.llm_turns, adapter.calls_used, len(client.calls)), (6,6,6))
        self.assertFalse(adapter.cleanup_pending)

    async def test_coordinator_deadline_cancels_model_worker(self):
        c, source, adapter, client = self.setup_flow('late')
        c.seconds = .03
        report = await run_coordinator(c, 'test')
        self.assertEqual(report.status, 'budget_exhausted')
        self.assertEqual(adapter.last_error, 'CANCELLED')
        self.assertEqual(client.calls, [])
        self.assertFalse(adapter.cleanup_pending)
        self.assertIsNone(adapter._owner)

    async def test_caller_cancel_cannot_dispatch_late_tool(self):
        c, source, adapter, client = self.setup_flow('late')
        task = asyncio.create_task(run_coordinator(c, 'test'))
        await wait_until(lambda: adapter._owner is not None or task.done())
        self.assertFalse(task.done())
        owner = adapter._owner
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(c.report.status, 'cancelled')
        self.assertEqual(client.calls, [])
        self.assertTrue(owner.done())
        self.assertFalse(adapter.cleanup_pending)
        await asyncio.sleep(.01)
        self.assertEqual(client.calls, [])

    async def test_read_only_config_cannot_include_restart(self):
        with self.assertRaises(AsyncLLMError):
            AsyncDecisionSource(None, (TOOL,), read_only=frozenset({'restart_service'}))

    async def test_model_cleanup_pending_is_reflected_in_coordinator_report(self):
        c, source, adapter, client = self.setup_flow('late', cleanup_seconds=.005)
        release = asyncio.Event()
        original = adapter._stop
        async def delayed(process):
            await release.wait()
            await original(process)
        adapter._stop = delayed
        task = asyncio.create_task(run_coordinator(c, 'test'))
        await wait_until(lambda: adapter.worker_pid is not None or task.done())
        self.assertFalse(task.done())
        task.cancel()
        try:
            with self.assertRaisesRegex(AsyncLLMError, 'CLEANUP_PENDING'):
                await task
            self.assertTrue(c.report.cleanup_pending)
            self.assertTrue(adapter.cleanup_pending)
            self.assertEqual(client.calls, [])
        finally:
            release.set()
            adapter.cleanup_seconds = 2
            await source.finish_cleanup()
            await c.finish_cleanup()
        self.assertFalse(adapter.cleanup_pending)
        self.assertFalse(c.report.cleanup_pending)

    async def test_lower_bridge_budget_remains_binding(self):
        c, source, adapter, client = self.setup_flow('tool')
        c.executor = ToolBridge(client, (TrustedTool(TOOL.name, TOOL.description, TOOL.parameters, True),),
                                redact=lambda text:text)  # existing default 4 calls
        report = await run_coordinator(c, 'test')
        self.assertEqual(report.status, 'budget_exhausted')
        self.assertEqual(len(client.calls), 4)
        self.assertEqual(adapter.calls_used, 5)
        self.assertFalse(adapter.cleanup_pending)
