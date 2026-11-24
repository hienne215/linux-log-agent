"""Original S1/S2/S4 OFFLINE orchestration cases; never live diagnostic accuracy."""
import unittest
from agent.integration_demo.session import DemoSession
from agent.integration_demo.backends import BackendError
from agent.integration_demo.fixtures import steps
from agent.incident_report import render_markdown
from agent.llm.redaction import _clean


class OriginalScenarioTests(unittest.IsolatedAsyncioTestCase):
    async def test_s1_crash_loop_via_mock_http_and_sdk_stdio(self):
        await self.check_scenario('crash-loop',2,'S1')
    async def test_s2_config_error_via_mock_http_and_sdk_stdio(self):
        await self.check_scenario('config-error',2,'S2')
    async def test_s4_active_but_http_502_via_mock_http_and_sdk_stdio(self):
        await self.check_scenario('backend-502',3,'502')
    async def check_scenario(self,name,count,needle):
        s=DemoSession(name)
        try:r=await s.run('Read the explicitly synthetic '+name+' fixture')
        finally:await s.finish_cleanup()
        self.assertEqual(r.status,'concluded');self.assertFalse(r.conclusion_verified)
        self.assertIn(needle,r.conclusion);self.assertEqual(r.tool_calls,count)
        self.assertEqual(r.llm_turns,count+1);self.assertEqual(s.adapter.calls_used,count+1)
        self.assertEqual(set(r.citations),{e.id for e in r.evidence})
        self.assertEqual(len(r.evidence),count)
        self.assertTrue(all(e.source.startswith('synthetic:integration-demo-v1/') for e in r.evidence))
        self.assertEqual(r.state_trace,s.coordinator.state_history)
        text=render_markdown(r,redact=_clean);self.assertIn('Application state trace',text)
        self.assertFalse(s.cleanup_pending);self.assertIsNone(s.client._owner)
        self.assertIsNone(s.adapter._owner);self.assertIsNone(s.coordinator._operation)
        self.assertFalse(s.bridge.has_pending_task);self.assertEqual(s.adapter.last_returncode,0)
    async def test_unknown_citation_stops_without_fabricated_evidence_or_retry(self):
        s=DemoSession('backend-502',_model_fault='bad-citation')
        try:r=await s.run('Synthetic failure path')
        finally:await s.finish_cleanup()
        self.assertNotEqual(r.status,'concluded');self.assertEqual(s.model.last_error,'INVALID_CONCLUSION')
        self.assertEqual(r.tool_calls,3);self.assertEqual(s.adapter.calls_used,4)
        self.assertFalse(s.cleanup_pending)
    def test_new_scenarios_are_not_authorized_for_live_ollama(self):
        for scenario in ('crash-loop','config-error','backend-502'):
            with self.subTest(scenario=scenario),self.assertRaises(BackendError):DemoSession(scenario,backend='ollama')
