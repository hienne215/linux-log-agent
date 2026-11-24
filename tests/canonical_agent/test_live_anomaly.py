"""Pure rule and SDK fixture verification. No Linux/inference or invented experiment runs."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock,patch

from agent.diagnostics.journal_anomaly import evaluate,MIN_CURRENT,RATE_MULTIPLIER,RULE,SIGNAL,authorized
from agent.diagnostics.journal_policy import window,parse_stamp,stamp
from agent.diagnostics.live_anomaly_binding import LiveAnomalyBinding
from agent.llm.redaction import _clean
from agent.integration_boundary import ToolMessage
from agent.mcp_client import MCPClientError,StdioConfig,StdioMCPClient,ToolResult
from agent.read_only import ReadOnlySession
ROOT=Path(__file__).resolve().parents[2]
FIXTURE=ROOT/'tests/canonical_agent/anomaly_fixture.py'


def request():
    w=window()
    return dict(schema_version='1.0.0',session_id='synthetic-session',trace_id='trace',logical_tool_call_id='call',attempt_id='attempt',
        unit='nginx.service',since=stamp(parse_stamp(w['since'])-timedelta(minutes=15)),until=w['until'],signals=[SIGNAL],max_findings=1)


def entries(a,baseline,current):
    start,end=parse_stamp(a['since']),parse_stamp(a['until']);mid=start+(end-start)/2
    return [dict(timestamp=stamp(base+timedelta(seconds=i+1)),priority=3,message='error fixture-canary')
            for count,base in [(baseline,start),(current,mid)] for i in range(count)]


class RuleTests(unittest.TestCase):
    def test_empty_evidence_terminal_explains_missing_baseline(self):
        from agent.coordinator import Report
        from agent.read_only import terminal_summary
        a=request();data,evidence=evaluate(a,[])
        text=terminal_summary(Report('insufficient_evidence',tool_calls=1),
            {'detect_anomaly':dict(status='partial',data=data,evidence=evidence)})
        self.assertIn('Chưa đủ baseline để so sánh',text)
        self.assertIn('không kết luận có hoặc không có bất thường',text)
        self.assertEqual(text.count('Số lần gọi công cụ:'),1)

    def test_increase_requires_both_absolute_and_relative_thresholds(self):
        a=request()
        for baseline,current,trigger in [(2,5,True),(4,8,True),(1,4,False),(4,5,False),(5,5,False),(5,0,False)]:
            with self.subTest(baseline=baseline,current=current):
                data,evidence=evaluate(a,entries(a,baseline,current))
                self.assertEqual(len(data['anomalies']),int(trigger));self.assertIsNone(data['confidence'])
                self.assertEqual(len(evidence),baseline+current)
                if trigger:
                    f=data['anomalies'][0];self.assertEqual(f['observed_count'],current)
                    self.assertEqual(f['window'],{k:a[k] for k in ('since','until')})
                    self.assertIn('current>=5',f['summary']);self.assertIn('2*baseline',f['summary'])
                    self.assertTrue(set(f['evidence_refs'])<={e['evidence_id'] for e in evidence})
                self.assertNotIn('fixture-canary',json.dumps(evidence))
                for item in evidence:self.assertEqual(_clean(item['excerpt']),item['excerpt'])
                for finding in data['anomalies']:self.assertEqual(_clean(finding['summary']),finding['summary'])
    def test_zero_baseline_and_empty_source_never_become_normal_or_infinite_rate(self):
        a=request()
        for current in (0,5,10):
            data,evidence=evaluate(a,entries(a,0,current))
            self.assertEqual(data['anomalies'],[]);self.assertIsNone(data['confidence'])
            self.assertEqual(data['insufficient_signals'],[SIGNAL]);self.assertEqual(data['analyzed_signals'],[])
            self.assertEqual(len(evidence),current)
    def test_half_open_boundaries_and_actual_event_timestamps(self):
        a=request();start,end=parse_stamp(a['since']),parse_stamp(a['until']);mid=start+(end-start)/2
        events=[dict(timestamp=stamp(start),priority=3,message='error'),dict(timestamp=stamp(mid),priority=3,message='error')]
        data,evidence=evaluate(a,events)
        self.assertEqual([e['observed_at'] for e in evidence],[e['timestamp'] for e in events])
        self.assertIn('baseline_visible_count=1',evidence[0]['excerpt']);self.assertIn('current_visible_count=1',evidence[0]['excerpt'])
        with self.assertRaises(ValueError):evaluate(a,events+[dict(timestamp=stamp(end),priority=3,message='error')])
    def test_invalid_scope_windows_signals_and_bounds(self):
        a=request()
        for bad in [dict(unit='sshd.service'),dict(signals=['failed_logins']),dict(max_findings=5),
                    dict(since='2026-01-01T00:00:00Z'),dict(until=a['since'])]:
            self.assertFalse(authorized({**a,**bad}))
        with self.assertRaises(ValueError):evaluate(a,entries(a,50,51))
    def test_citation_cap_preserves_full_real_event_ledger(self):
        a=request();data,evidence=evaluate(a,entries(a,10,70))
        self.assertEqual(len(evidence),80);self.assertEqual(data['anomalies'][0]['observed_count'],70)
        self.assertEqual(len(data['anomalies'][0]['evidence_refs']),8)
        self.assertEqual(len({e['evidence_id'] for e in evidence}),80)


class BindingTests(unittest.IsolatedAsyncioTestCase):
    async def connect(self,fault='spike'):
        c=StdioMCPClient(StdioConfig(command=sys.executable,args=('-B',str(FIXTURE),fault),allowed_tools=frozenset({'detect_anomaly'})))
        await c.connect();await c.list_tools();return c
    async def test_sdk_full_schema_ids_references_privacy_and_provenance(self):
        c=await self.connect();b=LiveAnomalyBinding(c,ROOT,redact=_clean)
        try:
            page=await c.list_tools();t=next(t for t in page.tools if t['name']=='detect_anomaly')
            self.assertEqual(t['inputSchema'],b.schemas[0]);self.assertEqual(t['outputSchema'],b.schemas[2])
            r=await b.call_tool('detect_anomaly',{'unit':'nginx.service'});e=r.structured_content
            b._result_validator.validate(e);self.assertEqual(e['status'],'partial');self.assertFalse(r.is_error)
            self.assertEqual(json.loads(r.content[0]['text']),e)
            for k,v in b.correlation.items():self.assertEqual(e[k],v)
            self.assertEqual(e['data']['anomalies'][0]['observed_count'],5)
            self.assertEqual(b.counts,dict(baseline_visible_count=2,current_visible_count=5))
            self.assertNotIn('synthetic-private-canary',json.dumps(e));self.assertNotIn('192.0.2.55',json.dumps(e))
            o=b.interpret(ToolMessage('detect_anomaly',json.dumps({'structuredContent':e}),False))
            self.assertTrue(o.limited);self.assertEqual(len(o.evidence),7)
            self.assertIn('observed_at',json.loads(o.evidence[0].text))
            with self.assertRaises(MCPClientError):await b.call_tool('detect_anomaly',{'unit':'nginx.service'})
        finally:await c.disconnect()
        self.assertIsNone(c._owner)
    async def test_stable_zero_empty_and_errors_do_not_become_confident_success(self):
        for fault in ('stable','zero','empty','permission','timeout'):
            c=await self.connect(fault);b=LiveAnomalyBinding(c,ROOT,redact=_clean)
            try:
                if fault in ('permission','timeout'):
                    with self.assertRaises(MCPClientError) as error:await b.call_tool('detect_anomaly',{'unit':'nginx.service'})
                    self.assertEqual(error.exception.code,'TIMEOUT' if fault=='timeout' else 'PERMISSION_DENIED')
                    self.assertFalse(b.evidence)
                    if fault=='timeout':self.assertEqual(b.execution_state,'unknown')
                else:
                    r=await b.call_tool('detect_anomaly',{'unit':'nginx.service'})
                    self.assertEqual(r.structured_content['status'],'partial');self.assertEqual(r.structured_content['data']['anomalies'],[])
                    if fault in ('zero','empty'):self.assertEqual(r.structured_content['data']['insufficient_signals'],[SIGNAL])
            finally:await c.disconnect()
            self.assertIsNone(c._owner)
    async def test_model_never_supplies_ids_thresholds_signals_or_time(self):
        for extra in [dict(since='2026-01-01T00:00:00Z'),dict(signals=['oom_events']),dict(max_findings=50),
                      dict(threshold=0),dict(trace_id='model'),dict(unit='sshd.service')]:
            c=AsyncMock();b=LiveAnomalyBinding(c,ROOT,redact=_clean)
            with self.assertRaises(MCPClientError):await b.call_tool('detect_anomaly',{'unit':'nginx.service',**extra})
            c.call_tool.assert_not_awaited()
    async def test_cancel_and_disconnect_are_unknown_single_dispatch(self):
        for cause in (asyncio.CancelledError(),MCPClientError('DISCONNECTED')):
            c=AsyncMock();c.call_tool.side_effect=cause;b=LiveAnomalyBinding(c,ROOT,redact=_clean)
            with self.assertRaises(type(cause)):await b.call_tool('detect_anomaly',{'unit':'nginx.service'})
            self.assertEqual(b.execution_state,'unknown')
            with self.assertRaises(MCPClientError):await b.call_tool('detect_anomaly',{'unit':'nginx.service'})
            c.call_tool.assert_awaited_once()
    async def test_coordinator_mock_http_sdk_citations_and_cleanup(self):
        s=ReadOnlySession(scope='anomaly',backend='mock')
        s.client.bindings['detect_anomaly'].client=StdioMCPClient(StdioConfig(command=sys.executable,args=('-B',str(FIXTURE)),allowed_tools=frozenset({'detect_anomaly'})))
        report=await s.run('Synthetic rate comparison')
        self.assertEqual(report.status,'limited');self.assertEqual(report.tool_calls,1);self.assertEqual(s.adapter.calls_used,2)
        self.assertTrue(set(report.citations)<={e.id for e in report.evidence});self.assertTrue(report.citations)
        self.assertFalse(report.cleanup_pending);self.assertIsNone(s.adapter._owner);self.assertIsNone(s.client._owner)
    async def test_forged_rule_counts_refs_and_raw_excerpts_are_rejected(self):
        import mock_mcp_server as contracts
        for fault in ('source','count','threshold','reference','raw','confidence'):
            c=AsyncMock();b=LiveAnomalyBinding(c,ROOT,redact=_clean)
            async def result(name,arguments):
                data,evidence=evaluate(arguments,entries(arguments,2,5))
                r=contracts.make_response(name,arguments['trace_id'],arguments['logical_tool_call_id'],arguments['attempt_id'],data,arguments['session_id'],status='partial',evidence=evidence)
                if fault=='source':r['evidence'][0]['source']='forged'
                if fault=='count':data['anomalies'][0]['observed_count']=99
                if fault=='threshold':data['anomalies'][0]['summary']='Rule threshold=0'
                if fault=='reference':data['anomalies'][0]['evidence_refs']=['invented']
                if fault=='raw':r['evidence'][-1]['excerpt']='MYSQL_PWD=synthetic-private-canary'
                if fault=='confidence':data['confidence']=1
                return ToolResult(({'type':'text','text':json.dumps(r)},),r,False)
            c.call_tool.side_effect=result
            with self.subTest(fault=fault):
                with self.assertRaises(MCPClientError):await b.call_tool('detect_anomaly',{'unit':'nginx.service'})
                self.assertFalse(b.evidence);self.assertIsNone(b._result)

    async def test_empty_source_stops_without_second_model_request_or_fake_evidence(self):
        s=ReadOnlySession(scope='anomaly',backend='mock')
        s.client.bindings['detect_anomaly'].client=StdioMCPClient(StdioConfig(command=sys.executable,args=('-B',str(FIXTURE),'empty'),allowed_tools=frozenset({'detect_anomaly'})))
        report=await s.run('Synthetic empty baseline')
        self.assertEqual(report.status,'insufficient_evidence');self.assertEqual(s.adapter.calls_used,1)
        self.assertEqual(report.tool_calls,1);self.assertFalse(report.evidence);self.assertFalse(report.citations)
        self.assertFalse(report.cleanup_pending);self.assertIsNone(s.adapter._owner);self.assertIsNone(s.client._owner)
    async def test_full_event_ledger_survives_bounded_model_projection(self):
        import mock_mcp_server as contracts
        b=LiveAnomalyBinding(AsyncMock(),ROOT,redact=_clean)
        async def result(name,a):
            data,evidence=evaluate(a,entries(a,10,70))
            r=contracts.make_response(name,a['trace_id'],a['logical_tool_call_id'],a['attempt_id'],data,a['session_id'],status='partial',evidence=evidence)
            return ToolResult(({'type':'text','text':json.dumps(r)},),r,False)
        b.client.call_tool.side_effect=result
        r=await b.call_tool('detect_anomaly',{'unit':'nginx.service'})
        o=b.interpret(ToolMessage('detect_anomaly',json.dumps({'structuredContent':r.structured_content}),False))
        self.assertEqual(len(b.evidence),80);self.assertEqual(len(o.evidence),8)
        self.assertLess(len(o.note.encode()),2048)
        self.assertTrue(set(r.structured_content['data']['anomalies'][0]['evidence_refs'])<={e.id for e in o.evidence})
