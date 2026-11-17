"""Offline gate support: existing mock HTTP decisions + real SDK synthetic stdio.

These assertions test orchestration, not live model reasoning or Linux accuracy.
"""
import hashlib
import unittest
from unittest.mock import patch

from agent.integration_demo.backends import Backend
from agent.integration_demo.fixtures import steps
from agent.integration_demo.session import DemoSession
from agent.integration_demo.__main__ import LABEL
from agent.incident_report import render_markdown
from agent.cli.synthetic import redact

RECORDS = []


class GateTraceTests(unittest.IsolatedAsyncioTestCase):
    async def check_trace(self, scenario, *, tool_cap=None):
        import anyio
        handles=[]; original=anyio.open_process
        async def observe(*args,**kwargs):
            process=await original(*args,**kwargs);handles.append(process);return process
        session=DemoSession(scenario,backend=Backend.MOCK)
        if tool_cap is not None:
            session.coordinator.max_tools=tool_cap  # trusted application limit, never tool permissions
        with patch('anyio.open_process',side_effect=observe):
            try:
                report=await session.run('Kiểm tra fixture Nginx; không dữ liệu Linux thật.')
            finally:
                await session.finish_cleanup()
        trace=list(session.coordinator.state_history)
        if tool_cap is None:
            count=len(steps(scenario))
            expected=['RECEIVED']
            for _ in range(count):expected+=['ANALYZING','COLLECTING']
            expected+=['ANALYZING','CONCLUDED','REPORTED']
            self.assertEqual(trace,expected)
            self.assertEqual(report.status,'limited' if scenario=='permission-denied' else 'concluded')
            self.assertEqual((report.tool_calls,report.llm_turns,session.adapter.calls_used),(count,count+1,count+1))
            self.assertEqual(set(report.citations),{e.id for e in report.evidence})
            if scenario=='permission-denied':
                self.assertIn('không đủ bằng chứng',report.conclusion)
                self.assertIn('PERMISSION_DENIED',report.evidence[-1].text)
        else:
            self.assertEqual(trace,['RECEIVED','ANALYZING','COLLECTING','ANALYZING','BUDGET_EXHAUSTED','REPORTED'])
            self.assertEqual(report.status,'budget_exhausted')
            self.assertEqual((report.tool_calls,report.llm_turns,session.adapter.calls_used),(1,2,2))
            self.assertEqual(report.conclusion,'')
            self.assertEqual(len(report.evidence),1)
        self.assertFalse(report.conclusion_verified)
        self.assertTrue(all(e.source.startswith('synthetic:integration-demo-v1/') for e in report.evidence))
        self.assertNotIn('restart_service',session.discovered)
        self.assertEqual(session.bridge.calls_used,report.tool_calls)
        self.assertFalse(session.cleanup_pending)
        self.assertIsNone(session._cleanup_owner)
        self.assertIsNone(session.model.adapter._owner)
        self.assertIsNone(session.client._owner)
        self.assertIsNone(session.coordinator._operation)
        self.assertFalse(session.bridge.has_pending_task)
        self.assertIsNotNone(session.adapter.last_returncode)
        self.assertTrue(handles)
        self.assertTrue(all(handle.returncode is not None for handle in handles))
        rendered=LABEL+render_markdown(report,redact=redact)
        self.assertIn('SYNTHETIC DEMO',rendered)
        self.assertIn('Không Ollama inference',rendered)
        RECORDS.append({'scenario':scenario,'tool_cap':tool_cap,'mode':'mock_http_synthetic_mcp',
            'status':report.status,'trace':trace,'tool_dispatches':report.tool_calls,
            'mock_llm_requests':session.adapter.calls_used,'llm_turns':report.llm_turns,
            'evidence':[{'id':e.id,'source':e.source} for e in report.evidence],
            'citations':list(report.citations),'conclusion_verified':report.conclusion_verified,
            'cleanup_pending':session.cleanup_pending,'all_owners_cleared':True,
            'worker_returncode':session.adapter.last_returncode,
            'mcp_returncodes':[handle.returncode for handle in handles],
            'rendered_report_sha256':hashlib.sha256(rendered.encode()).hexdigest()})

    async def test_active_exact_state_machine(self):await self.check_trace('active')
    async def test_port_conflict_exact_multiturn_state_machine(self):await self.check_trace('port-conflict')
    async def test_permission_denied_terminal_uncertainty(self):await self.check_trace('permission-denied')
    async def test_tool_budget_preserves_evidence_without_second_dispatch(self):
        await self.check_trace('port-conflict',tool_cap=1)


class RunnerStatusTests(unittest.TestCase):
    """Runner success must not promote skipped/failed/unexecuted tests to PASS."""
    def test_completed_run_is_accepted(self):
        from run_tests import complete
        result=unittest.TestResult()
        case=unittest.FunctionTestCase(lambda:None)
        result.startTest(case);result.addSuccess(case);result.stopTest(case)
        self.assertTrue(complete(result))

    def test_skipped_run_is_not_complete(self):
        from run_tests import complete
        result=unittest.TestResult()
        case=unittest.FunctionTestCase(lambda:None)
        result.startTest(case);result.addSkip(case,'synthetic prerequisite missing');result.stopTest(case)
        self.assertTrue(result.wasSuccessful())
        self.assertFalse(complete(result))

    def test_failed_or_errored_run_is_not_complete(self):
        from run_tests import complete
        for method in ('addFailure','addError'):
            result=unittest.TestResult()
            case=unittest.FunctionTestCase(lambda:None)
            result.startTest(case)
            getattr(result,method)(case,(AssertionError,AssertionError('synthetic failure'),None))
            result.stopTest(case)
            self.assertFalse(complete(result))

    def test_no_executed_tests_is_not_complete(self):
        from run_tests import complete
        self.assertFalse(complete(unittest.TestResult()))
