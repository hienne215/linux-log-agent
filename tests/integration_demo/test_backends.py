"""Factory/live-mode tests with mock HTTP only. No real Ollama worker may launch."""
import asyncio
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from agent.integration_demo import backends, preflight
from agent.integration_demo import session as wiring
from agent.integration_demo import __main__ as cli
from agent.llm.async_adapter import AsyncLLMError, LLMTimeout
from agent.llm.interface import Message, ToolCall, ToolDefinition
import test_demo as baseline
from test_demo import until


class FactoryTests(unittest.TestCase):
    def test_default_mock(self):
        adapter = backends.create_adapter()
        self.assertIn('agent.integration_demo.mock_worker', adapter._command)
        self.assertEqual(adapter.max_calls, 6)
        self.assertIsNone(adapter._owner)

    def test_live_uses_existing_worker_and_fixed_baseline(self):
        adapter = backends.create_adapter('ollama')
        self.assertIsInstance(adapter, backends.AsyncOllamaAdapter)
        self.assertEqual(adapter._command, (sys.executable, '-B', '-m', 'agent.llm._async_worker'))
        c = adapter.config
        self.assertEqual((c.model, c.endpoint, c.num_ctx, c.num_predict, c.temperature, c.num_thread,
                          c.timeout_seconds), ('qwen3:4b-instruct-2507-q4_K_M',
                          'http://127.0.0.1:11434', 4096, 256, 0, 4, 45))
        self.assertEqual(adapter.max_calls, 2)
        self.assertIsNone(adapter._owner)  # construction must not infer

    def test_unknown_backend_never_constructs_adapter(self):
        with patch.object(backends, 'AsyncOllamaAdapter') as mock, patch.object(backends, '_LiveAdapter') as live:
            for value in ('auto', 'OLLAMA', 'password=synthetic-secret', None, True):
                with self.assertRaises(backends.BackendError) as caught:
                    backends.create_adapter(value)
                self.assertEqual(str(caught.exception), 'INVALID_BACKEND')
            mock.assert_not_called()
            live.assert_not_called()

    def test_live_scope_does_not_expand_three_turn_scenarios(self):
        for scenario in ('port-conflict', 'permission-denied'):
            with self.assertRaisesRegex(backends.BackendError, 'LIVE_SCOPE_DENIED'):
                wiring.DemoSession(scenario, backend='ollama')

    def test_live_cannot_inject_mock_or_server_fault(self):
        for kwargs in ({'_mock_fault':'timeout'},):
            with self.assertRaises(backends.BackendError):
                backends.create_adapter('ollama', **kwargs)
        with self.assertRaises(backends.BackendError):
            wiring.DemoSession('active', backend='ollama', _server_fault='disconnect')

    def test_no_arbitrary_config_api(self):
        for name in ('model', 'endpoint', '_worker_command', 'command', 'max_calls'):
            with self.assertRaises(TypeError):
                backends.create_adapter('ollama', **{name:'not-allowed'})


class LiveModeMockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await baseline.EndToEndTests.asyncSetUp(self)
        self.fault = 'none'
        factory = backends.create_adapter
        def safe_factory(mode, **kwargs):
            adapter = factory(mode, **kwargs)
            # This is test-only: choose the real factory branch but replace its
            # command before any complete(). Production factory has no seam here.
            self.assertIs(mode, backends.Backend.OLLAMA)
            adapter._command = (sys.executable, '-B', '-m', 'agent.integration_demo.mock_worker',
                                kwargs['scenario'], self.fault)
            return adapter
        self.factory_patch = patch.object(wiring, 'create_adapter', side_effect=safe_factory)
        self.factory_mock = self.factory_patch.start()
        self.ram_patch = patch.object(backends, 'available_ram', return_value=2*1024**3)
        self.ram_mock = self.ram_patch.start()
        original = asyncio.create_subprocess_exec
        async def forbid_live_worker(*args, **kwargs):
            if 'agent.llm._async_worker' in args:
                raise AssertionError('Live worker forbidden during mocked tests')
            return await original(*args, **kwargs)
        self.spawn_patch = patch('asyncio.create_subprocess_exec', side_effect=forbid_live_worker)
        self.spawn_patch.start()

    async def asyncTearDown(self):
        try:
            await baseline.EndToEndTests.asyncTearDown(self)
        finally:
            self.spawn_patch.stop()
            self.ram_patch.stop()
            self.factory_patch.stop()

    def session(self, **kwargs):
        s = wiring.DemoSession('active', backend='ollama', **kwargs)
        self.sessions.append(s)
        return s

    async def test_native_tool_synthetic_evidence_cited_conclusion(self):
        s = self.session()
        r = await s.run('Kiểm tra trạng thái nginx.service')
        self.assertEqual((r.status, r.tool_calls, r.llm_turns, s.adapter.calls_used), ('concluded', 1, 2, 2))
        self.assertEqual(r.citations, ('active-service_status',))
        self.assertTrue(all(e.source.startswith('synthetic:') for e in r.evidence))
        self.assertFalse(r.conclusion_verified)
        self.assertFalse(s.cleanup_pending)
        self.assertEqual(s.coordinator.max_turns, 2)
        self.assertEqual(s.coordinator.max_tools, 1)

    async def test_invalid_model_output_no_dispatch_no_fallback(self):
        for fault in ('unknown', 'arguments', 'write', 'malformed'):
            self.fault = fault
            s = self.session()
            r = await s.run('fixture')
            self.assertEqual(s.bridge.calls_used, 0)
            self.assertEqual(s.adapter.calls_used, 1)
            self.assertNotEqual(r.status, 'concluded')
            self.assertEqual(self.factory_mock.call_count, len(self.sessions))

    async def test_timeout_no_retry_or_mock_fallback(self):
        self.fault = 'timeout'
        s = self.session()
        r = await s.run('fixture')
        self.assertEqual(r.status, 'budget_exhausted')
        self.assertEqual(s.adapter.calls_used, 1)
        self.assertEqual(s.bridge.calls_used, 0)
        self.assertEqual(self.factory_mock.call_count, 1)

    async def test_cancel_reaps_owner_no_late_dispatch(self):
        self.fault = 'late'
        s = self.session()
        task = asyncio.create_task(s.run('fixture'))
        await until(lambda:s.adapter.worker_pid is not None or task.done())
        self.assertFalse(task.done())
        owner = s.adapter._owner
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(owner.done())
        self.assertEqual(s.bridge.calls_used, 0)
        self.assertFalse(s.cleanup_pending)

    async def test_late_response_deadline_cannot_dispatch(self):
        self.fault = 'late'
        s = self.session()
        from dataclasses import replace
        s.adapter.config = replace(s.adapter.config, timeout_seconds=.1)  # test-only lower budget
        r = await s.run('fixture')
        self.assertEqual(r.status, 'budget_exhausted')
        self.assertEqual(s.bridge.calls_used, 0)
        self.assertFalse(s.adapter.cleanup_pending)

    async def test_ram_gate_before_each_request_stops_second(self):
        s = self.session()
        self.ram_patch.stop()
        self.ram_patch = patch.object(backends, 'available_ram', side_effect=[2*1024**3, 1024])
        self.ram_mock = self.ram_patch.start()
        r = await s.run('fixture')
        self.assertEqual(s.adapter.calls_used, 1)
        self.assertEqual(s.model.last_error, 'RAM_LOW')
        self.assertNotEqual(r.status, 'concluded')
        self.assertEqual(len(r.evidence), 1)
        self.assertFalse(s.cleanup_pending)

    async def test_low_ram_no_worker_or_tool_dispatch(self):
        s = self.session()
        self.ram_mock.return_value = 0
        r = await s.run('fixture')
        self.assertEqual(s.adapter.calls_used, 0)
        self.assertEqual(s.bridge.calls_used, 0)
        self.assertEqual(s.model.last_error, 'RAM_LOW')
        self.assertFalse(r.conclusion_verified)

    async def test_secret_removed_before_ipc(self):
        s = self.session()
        original = s.adapter._perform
        payloads = []
        async def observe(payload, abort):
            payloads.append(payload)
            return await original(payload, abort)
        with patch.object(s.adapter, '_perform', side_effect=observe):
            r = await s.run('password=synthetic-secret')
        self.assertTrue(payloads)
        self.assertTrue(all(b'synthetic-secret' not in p for p in payloads))
        text = cli.LIVE_LABEL + cli.render_markdown(r, redact=cli.redact)
        self.assertNotIn('synthetic-secret',text)

    async def test_secret_arguments_rejected_without_ipc(self):
        s = self.session()
        messages = (Message('assistant', tool_calls=(ToolCall('service_status', {'unit':'password=synthetic-secret'}),)),)
        with patch.object(s.adapter, '_perform') as spawn:
            with self.assertRaises(AsyncLLMError):
                await s.adapter.complete(messages, s.model.tools)
        spawn.assert_not_called()
        self.assertEqual(s.adapter.calls_used, 0)

    async def test_request_cap_cannot_be_increased_by_coordinator_override(self):
        s = self.session(_max_turns=6)
        self.assertEqual(s.coordinator.max_turns, 2)
        fixture = Path(__file__).resolve().parents[1]/'async_llm'/'worker_fixture.py'
        s.adapter._command = (sys.executable, '-B', str(fixture), 'tool')
        for _ in range(2):
            await s.adapter.complete((Message('user','fixture'),), s.model.tools)
        with self.assertRaises(LLMTimeout):
            await s.adapter.complete((Message('user','fixture'),), s.model.tools)
        self.assertEqual(s.adapter.calls_used, 2)

    async def test_cleanup_pending_keeps_full_session_owner(self):
        self.fault = 'late'
        s = self.session()
        release = asyncio.Event()
        original = s.adapter._stop
        async def blocked(proc):
            await release.wait()
            await original(proc)
        s.adapter._stop = blocked
        s.adapter.cleanup_seconds = s.cleanup_seconds = .01
        task = asyncio.create_task(s.run('fixture'))
        await until(lambda:s.adapter.worker_pid is not None or task.done())
        task.cancel()
        try:
            with self.assertRaises(wiring.DemoError):
                await task
            self.assertTrue(s.cleanup_pending)
            self.assertTrue(s.coordinator.report.cleanup_pending)
            self.assertIsNotNone(s.adapter._owner)
        finally:
            release.set()
            s.adapter.cleanup_seconds = s.cleanup_seconds = 8
            for _ in range(3):
                try:
                    await s.finish_cleanup()
                    break
                except wiring.DemoError:
                    pass
        self.assertFalse(s.cleanup_pending)


class PreflightTests(unittest.TestCase):
    def test_metadata_only_no_inference_endpoints(self):
        values = [json.dumps({'version':'0.40.13'}).encode(), json.dumps({'models':[{
            'name':'qwen3:4b-instruct-2507-q4_K_M','digest':preflight.EXPECTED_DIGEST}]}).encode(),
            json.dumps({'details':{'quantization_level':'Q4_K_M'},'capabilities':['tools','thinking']}).encode()]
        calls = []
        class Response(io.BytesIO):
            pass
        class Opener:
            def open(self, request, timeout):
                calls.append((request.full_url, request.data))
                return Response(values.pop(0))
        with patch.object(preflight, 'build_opener', return_value=Opener()), patch.object(preflight, 'available_ram',return_value=2*1024**3):
            r = preflight.check()
        self.assertEqual(r['inference_attempts'], 0)
        self.assertEqual([x[0].split(':11434')[1] for x in calls], ['/api/version','/api/tags','/api/show'])
        self.assertIn('NOT VERIFIED',r['thinking'])

    def test_low_ram_no_metadata_http(self):
        with patch.object(preflight,'available_ram',return_value=0), patch.object(preflight,'build_opener') as opener:
            with self.assertRaisesRegex(preflight.PreflightError,'RAM_LOW'):
                preflight.check()
        opener.assert_not_called()

    def test_metadata_failure_never_echoes_secret(self):
        with patch.object(preflight,'available_ram',return_value=2*1024**3), patch.object(preflight,'build_opener',side_effect=OSError('password=synthetic-secret')):
            with self.assertRaises(preflight.PreflightError) as caught:
                preflight.check()
        self.assertEqual(str(caught.exception),'METADATA_UNAVAILABLE')

    def test_metadata_mismatch_and_wrong_capability_type_fail_closed(self):
        baseline = [ {'version':'0.40.13'}, {'models':[{'name':'qwen3:4b-instruct-2507-q4_K_M',
            'digest':preflight.EXPECTED_DIGEST}]},
            {'details':{'quantization_level':'Q4_K_M'},'capabilities':['tools']} ]
        for fault in ('version','digest','capabilities'):
            data = json.loads(json.dumps(baseline))
            if fault == 'version': data[0]['version'] = '0.0.0'
            if fault == 'digest': data[1]['models'][0]['digest'] = 'a'*64
            if fault == 'capabilities': data[2]['capabilities'] = 'tools'
            class Opener:
                def open(self, request, timeout):
                    return io.BytesIO(json.dumps(data.pop(0)).encode())
            with patch.object(preflight,'available_ram',return_value=2*1024**3), patch.object(preflight,'build_opener',return_value=Opener()):
                with self.assertRaises(preflight.PreflightError):
                    preflight.check()

    def test_sdk_mismatch_never_queries_ollama(self):
        with patch.object(preflight,'version',return_value='2.2.0'), patch.object(preflight,'build_opener') as opener:
            with self.assertRaisesRegex(preflight.PreflightError,'RUNTIME_MISMATCH'):
                preflight.check()
        opener.assert_not_called()


class BackendCLITests(unittest.TestCase):
    def invoke(self,*args):
        stdout,stderr=io.StringIO(),io.StringIO()
        with redirect_stdout(stdout),redirect_stderr(stderr):
            try:
                code=cli.main(list(args))
            except SystemExit as error:
                code=error.code
        return code,stdout.getvalue(),stderr.getvalue()

    def test_default_stays_mock_and_labels_data(self):
        from agent.coordinator import Report
        async def outcome(*a,**kwargs):
            self.assertIs(kwargs['backend'],backends.Backend.MOCK)
            return Report('insufficient_evidence')
        with patch.object(cli,'drive',side_effect=outcome):
            code,text,_=self.invoke('--scenario','active','--question','fixture')
        self.assertEqual(code,1)
        self.assertIn('MOCK LLM + SYNTHETIC MCP',text)
        self.assertNotIn('LIVE OLLAMA',text)

    def test_live_label_does_not_claim_real_linux(self):
        from agent.coordinator import Report
        async def outcome(*a,**kwargs):
            self.assertIs(kwargs['backend'],backends.Backend.OLLAMA)
            return Report('insufficient_evidence')
        with patch.object(cli,'drive',side_effect=outcome):
            _,text,_=self.invoke('--backend','ollama','--scenario','active','--question','fixture')
        self.assertIn('LIVE OLLAMA + SYNTHETIC MCP',text)
        self.assertIn('không Linux diagnostics thật',text)
        self.assertNotIn('Không Ollama inference',text)

    def test_unknown_backend_rejected_without_session_or_secret_echo(self):
        with patch.object(cli,'DemoSession') as create:
            code,text,err=self.invoke('--backend','password=synthetic-secret','--scenario','active')
        self.assertEqual(code,2)
        create.assert_not_called()
        self.assertNotIn('synthetic-secret',text+err)

    def test_live_other_scenarios_rejected_before_dispatch(self):
        with patch.object(cli,'DemoSession') as create:
            code,_,_=self.invoke('--backend','ollama','--scenario','port-conflict','--question','fixture')
        self.assertEqual(code,2)
        create.assert_not_called()

    def test_preflight_only_no_session_model_or_tool(self):
        with patch.object(cli,'check',return_value={'preflight':'PASS','inference_attempts':0}) as check, patch.object(cli,'DemoSession') as create:
            code,text,_=self.invoke('--backend','ollama','--scenario','active','--preflight-only')
        self.assertEqual(code,0)
        check.assert_called_once()
        create.assert_not_called()
        self.assertIn('"inference_attempts": 0',text)
