"""Driver tests only: fake adapters, no metadata network or live inference."""
from contextlib import redirect_stdout
import io
import unittest
from unittest.mock import patch

from agent.llm.interface import Completion, ToolCall, Usage
from agent.llm.async_adapter import AsyncLLMError
import live_smoke as smoke


class FakeAdapter:
    instances=[]
    def __init__(self,*_,**kwargs):
        self.kwargs=kwargs;self.calls_used=0;self.cleanup_pending=False
        self.worker_pid=None;self.last_returncode=0
        type(self).instances.append(self)
    async def complete(self,messages,tools=(),*,require_tool=False):
        self.calls_used+=1
        if self.calls_used==1:
            return Completion('tool_calls','',(ToolCall('service_status',{'unit':'nginx.service'}),),
                              Usage(10,10,.1,.2,100),.2,smoke.MODEL,'stop')
        return Completion('text','{"conclusion":"fixture only","citations":["smoke-e1"]}',(),
                          Usage(10,10,.1,.2,100),.2,smoke.MODEL,'stop')
    async def finish_cleanup(self):
        self.cleanup_pending=False


class SmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_requests_and_structural_metrics_only(self):
        with patch.object(smoke,'available_ram',return_value=2*1024**3):
            report=await smoke.run_live({},adapter_factory=FakeAdapter)
        self.assertEqual(report['status'],'PASS')
        self.assertEqual(report['inference_attempts'],2)
        self.assertTrue(report['cleanup_confirmed'])
        self.assertNotIn('fixture only',str(report))
        self.assertEqual(FakeAdapter.instances[-1].kwargs['max_calls'],2)

    async def test_low_ram_prevents_any_request(self):
        with patch.object(smoke,'available_ram',return_value=1024):
            report=await smoke.run_live({},adapter_factory=FakeAdapter)
        self.assertEqual(report['inference_attempts'],0)
        self.assertEqual(report['error_code'],'RAM_LOW')

    async def test_first_failure_stops_without_retry_or_second_request(self):
        class Fails(FakeAdapter):
            async def complete(self,*args,**kwargs):
                self.calls_used+=1
                raise AsyncLLMError('TIMEOUT')
        with patch.object(smoke,'available_ram',return_value=2*1024**3):
            report=await smoke.run_live({},adapter_factory=Fails)
        self.assertEqual(report['inference_attempts'],1)
        self.assertEqual(report['status'],'FAIL')
        self.assertEqual(report['cases'],[])

    async def test_pending_cleanup_is_rejoined_before_return(self):
        class Pending(FakeAdapter):
            def __init__(self,*a,**kw):
                super().__init__(*a,**kw);self.joins=0
            async def complete(self,*a,**kw):
                self.calls_used+=1;self.cleanup_pending=True
                raise AsyncLLMError('CLEANUP_PENDING')
            async def finish_cleanup(self):
                self.joins+=1
                if self.joins==1:raise AsyncLLMError('CLEANUP_PENDING')
                self.cleanup_pending=False
        with patch.object(smoke,'available_ram',return_value=2*1024**3), patch('sys.stderr',io.StringIO()):
            report=await smoke.run_live({},adapter_factory=Pending)
        self.assertTrue(report['cleanup_confirmed'])
        self.assertEqual(report['inference_attempts'],1)
        self.assertEqual(Pending.instances[-1].joins,2)


class GateTests(unittest.TestCase):
    def test_default_does_not_query_or_infer(self):
        with patch.object(smoke,'metadata') as query,redirect_stdout(io.StringIO()) as out:
            self.assertEqual(smoke.main([]),0)
        query.assert_not_called()
        self.assertIn('NOT RUN',out.getvalue())

    def test_preflight_only_does_not_construct_adapter(self):
        with patch.object(smoke,'metadata',return_value={}),patch.object(smoke,'available_ram',return_value=2*1024**3),\
             patch.object(smoke,'run_live') as run,redirect_stdout(io.StringIO()) as out:
            self.assertEqual(smoke.main(['--preflight-only']),0)
        run.assert_not_called()
        self.assertIn('"inference_attempts": 0',out.getvalue())
