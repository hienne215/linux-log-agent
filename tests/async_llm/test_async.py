import asyncio
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import time
import unittest

from agent.llm.async_adapter import AsyncOllamaAdapter, AsyncLLMError, LLMTimeout
from agent.llm.interface import Message, ToolDefinition
from agent.llm.ollama import OllamaConfig

TOOL = ToolDefinition('service_status', 'Read-only fixture', {'type': 'object',
    'properties': {'unit': {'const': 'nginx.service'}}, 'required': ['unit'], 'additionalProperties': False})


async def wait_until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.001)


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self, mode='tool', **kwargs):
        command = (sys.executable, '-B', str(Path(__file__).with_name('worker_fixture.py')), mode)
        return AsyncOllamaAdapter(_worker_command=command, **kwargs)

    async def call(self, adapter):
        return await adapter.complete((Message('user', 'test'),), (TOOL,))

    def assert_clean(self, adapter):
        self.assertFalse(adapter.cleanup_pending)
        self.assertIsNone(adapter.worker_pid)
        self.assertIsNone(adapter._owner)

    async def test_native_tool_and_usage(self):
        adapter = self.adapter()
        result = await self.call(adapter)
        self.assertEqual(result.tool_calls[0].name, 'service_status')
        self.assertEqual(result.tool_calls[0].arguments, {'unit': 'nginx.service'})
        self.assertEqual(result.usage.output_tokens, 12)
        self.assert_clean(adapter)

    async def test_text_conclusion(self):
        adapter = self.adapter('conclude')
        result = await self.call(adapter)
        self.assertEqual(result.kind, 'text')
        self.assertIn('citations', result.text)
        self.assert_clean(adapter)

    async def test_error_classification(self):
        for mode, code, layer in (('malformed','MALFORMED_RESPONSE','protocol'),
                                 ('timeout','TIMEOUT','timeout'), ('disconnect','CONNECTION_ERROR','transport'),
                                 ('missing-model','MODEL_NOT_FOUND','model'),
                                 ('write','INVALID_ARGUMENTS','protocol'), ('bad-args','INVALID_ARGUMENTS','protocol'),
                                 ('crash','WORKER_FAILURE','transport'), ('flood','OUTPUT_LIMIT','protocol')):
            with self.subTest(mode=mode):
                adapter = self.adapter(mode)
                with self.assertRaises(AsyncLLMError) as caught:
                    await self.call(adapter)
                self.assertEqual((caught.exception.code, caught.exception.layer), (code,layer))
                self.assertNotIn('synthetic-secret', str(caught.exception))
                self.assert_clean(adapter)

    async def test_deadline_before_dispatch(self):
        adapter = self.adapter(seconds=.001)
        await asyncio.sleep(.005)
        with self.assertRaises(LLMTimeout):
            await self.call(adapter)
        self.assertEqual(adapter.calls_used, 0)
        self.assertIsNone(adapter._owner)

    async def test_late_result_cannot_be_accepted_or_retried(self):
        adapter = self.adapter('late', config=replace(OllamaConfig(), timeout_seconds=.6))
        with self.assertRaises(LLMTimeout):
            await self.call(adapter)
        self.assert_clean(adapter)
        with self.assertRaisesRegex(AsyncLLMError, 'STOPPED'):
            await self.call(adapter)
        self.assertEqual(adapter.calls_used, 1)

    async def test_cancellation_during_startup_keeps_owner_until_joined(self):
        adapter = self.adapter('late')
        task = asyncio.create_task(self.call(adapter))
        await wait_until(lambda: adapter._owner is not None or task.done())
        self.assertFalse(task.done())
        owner = adapter._owner
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(owner.done())
        self.assert_clean(adapter)
        self.assertEqual(adapter.last_error, 'CANCELLED')

    async def test_stubborn_worker_is_killed_and_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory)/'ready'
            adapter = self.adapter('stubborn', terminate_seconds=.02)
            adapter._command += (str(ready),)
            task = asyncio.create_task(self.call(adapter))
            await wait_until(lambda: ready.exists() or task.done())
            self.assertTrue(ready.exists())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(adapter.last_returncode, -9)
            self.assert_clean(adapter)

    async def test_cleanup_budget_expiry_retains_owner_then_rejoins(self):
        adapter = self.adapter('late', cleanup_seconds=.01)
        release = asyncio.Event()
        original = adapter._stop
        async def delayed(process):
            await release.wait()
            await original(process)
        adapter._stop = delayed
        task = asyncio.create_task(self.call(adapter))
        await wait_until(lambda: adapter.worker_pid is not None or task.done())
        self.assertFalse(task.done())
        owner = adapter._owner
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        try:
            self.assertTrue(adapter.cleanup_pending)
            self.assertIs(adapter._owner, owner)
            self.assertFalse(owner.done())
            with self.assertRaisesRegex(AsyncLLMError, 'BUSY'):
                await self.call(adapter)
        finally:
            release.set()
            adapter.cleanup_seconds = 2
            await adapter.finish_cleanup()
        self.assertTrue(owner.done())
        self.assert_clean(adapter)

    async def test_six_requests_budget(self):
        adapter = self.adapter()
        for _ in range(6):
            await self.call(adapter)
        with self.assertRaises(LLMTimeout):
            await self.call(adapter)
        self.assertEqual(adapter.calls_used, 6)
        self.assert_clean(adapter)

    async def test_event_loop_remains_responsive(self):
        adapter = self.adapter('late')
        task = asyncio.create_task(self.call(adapter))
        start = time.monotonic()
        for _ in range(5):
            await asyncio.sleep(.01)
        self.assertLess(time.monotonic()-start, .3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_clean(adapter)

    async def test_late_completion_is_discarded_not_normalized(self):
        from unittest.mock import patch
        adapter = self.adapter(config=replace(OllamaConfig(), timeout_seconds=.01))
        async def late(payload, abort):
            await asyncio.sleep(.03)  # synthetic owner ignores abort until completion
            return b'not-to-be-accepted'
        adapter._perform = late
        with patch('agent.llm.async_adapter._completion', side_effect=AssertionError('late result used')) as decode:
            with self.assertRaises(LLMTimeout):
                await self.call(adapter)
            decode.assert_not_called()
        self.assertFalse(adapter.cleanup_pending)
        self.assertEqual(adapter.calls_used, 1)
        self.assertEqual(adapter.last_error, 'TIMEOUT')

    async def test_repeat_cancellation_never_cancels_owner(self):
        adapter = self.adapter('late', cleanup_seconds=1)
        release, started = asyncio.Event(), asyncio.Event()
        original = adapter._stop
        async def delayed(process):
            started.set()
            await release.wait()
            await original(process)
        adapter._stop = delayed
        task = asyncio.create_task(self.call(adapter))
        await wait_until(lambda: adapter.worker_pid is not None or task.done())
        self.assertFalse(task.done())
        owner = adapter._owner
        task.cancel()
        await started.wait()
        task.cancel()
        await asyncio.sleep(.01)
        self.assertEqual(owner.cancelling(), 0)
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(owner.done())
        self.assert_clean(adapter)

    async def test_invalid_parent_input_does_not_start_worker(self):
        adapter = self.adapter()
        with self.assertRaises(AsyncLLMError):
            await adapter.complete((), (TOOL,))
        self.assertEqual(adapter.calls_used, 0)
        self.assertIsNone(adapter._owner)

    async def test_redaction_and_minimal_child_environment(self):
        from unittest.mock import patch
        adapter = self.adapter('assert-redacted')
        with patch.dict('os.environ', {'LLA_TEST_SECRET': 'synthetic-secret'}):
            result = await adapter.complete((Message('user', 'password=synthetic-secret'),), (TOOL,))
        self.assertEqual(result.kind, 'tool_calls')
        self.assert_clean(adapter)

    async def test_require_tool_rejects_text_without_fallback(self):
        adapter = self.adapter('conclude')
        with self.assertRaisesRegex(AsyncLLMError, 'OUTPUT_POLICY'):
            await adapter.complete((Message('user', 'test'),), (TOOL,), require_tool=True)
        self.assert_clean(adapter)

    async def test_deadline_expiring_during_normalization_discards_result(self):
        from unittest.mock import patch
        from agent.llm.async_adapter import _completion
        adapter = self.adapter()
        def slow_decode(raw, tools, config):
            adapter.deadline = time.monotonic() - 1  # controlled clock boundary
            return _completion(raw, tools, config)
        with patch('agent.llm.async_adapter._completion', side_effect=slow_decode):
            with self.assertRaises(LLMTimeout):
                await self.call(adapter)
        self.assert_clean(adapter)

    async def test_secret_history_arguments_rejected_before_ipc(self):
        from unittest.mock import patch
        from agent.llm.interface import ToolCall
        adapter = self.adapter()
        tool = TOOL
        history = (Message('assistant', tool_calls=(ToolCall('service_status',
                    {'unit':'password=synthetic-secret'}),)),)
        sent = []
        async def intercept(payload, abort):
            sent.append(b'synthetic-secret' in payload)
            return b'{}'
        with patch.object(adapter, '_perform', side_effect=intercept) as launch:
            with self.assertRaises(AsyncLLMError):
                await adapter.complete(history, (tool,))
            launch.assert_not_called()
        self.assertEqual(sent, [])
        self.assertEqual(adapter.calls_used, 0)

    async def test_cleanup_os_error_retains_process_until_explicit_rejoin(self):
        adapter = self.adapter('late', cleanup_seconds=.01)
        original = adapter._stop
        attempts = 0
        async def fail_once(process):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError('password=synthetic-secret')
            await original(process)
        adapter._stop = fail_once
        task = asyncio.create_task(self.call(adapter))
        await wait_until(lambda: adapter.worker_pid is not None or task.done())
        process, owner = adapter._process, adapter._owner
        self.assertIsNotNone(process)
        task.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(adapter.cleanup_pending)
            self.assertIs(adapter._process, process)
            self.assertFalse(owner.done())
            adapter.cleanup_seconds = 2
            await adapter.finish_cleanup()
            self.assertTrue(owner.done())
            self.assert_clean(adapter)
        finally:
            # Also safely reap the test's child when reproducing the old defect.
            await original(process)
