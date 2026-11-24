"""Canonical proposal-only integration, mock HTTP + official SDK stdio.

No genuine human approval, real helper dispatch or Linux collector is used.
"""
import asyncio
from copy import deepcopy
from contextlib import redirect_stdout,redirect_stderr
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

import mock_mcp_server as contracts
from agent import proposal_server as server
from agent.restart_proposal import RestartBinding,BUSINESS,REASON
from agent.coordinator import Coordinator,Decision,Report
from agent.integration_boundary import ToolBridge,BoundaryError,ToolMessage,TrustedTool
from agent.llm.redaction import _clean
from agent.mcp_client import StdioMCPClient,StdioConfig,MCPClientError,ToolResult
from agent.read_only import CanonicalRouter,ReadOnlySession,main,terminal_summary

ROOT=Path(__file__).resolve().parents[2]


def args():
    return dict(schema_version='1.0.0',session_id='a'*32,trace_id='c'*32,logical_tool_call_id='d'*32,
        attempt_id='e'*32,mode='propose',unit='nginx.service',reason=REASON,evidence_ids=['synthetic-before'])


def view(state='pending'):
    a=args()
    return dict(action_id='b'*32,session_id=a['session_id'],trace_id=a['trace_id'],environment_id='synthetic-lab',
        unit='nginx.service',evidence_ids=a['evidence_ids'],state=state,expires_at='2099-01-01T00:00:00.000000Z',
        remaining_ms=60000,execution_enabled=False,executed=None)


async def envelope():
    with patch.object(server,'exchange',AsyncMock(return_value=view())):
        return await server.ProposalServer().proposal(args())


def native(r):return ToolResult(({'type':'text','text':json.dumps(r)},),r,r['status']=='error')


class ProposalTests(unittest.IsolatedAsyncioTestCase):
    async def test_canonical_propose_no_execution_no_fabricated_evidence(self):
        with patch.object(server,'exchange',AsyncMock(return_value=view())) as rpc:
            r=await server.ProposalServer().proposal(args())
        contracts.TOOLS['restart_service'][4].validate(r)
        self.assertEqual(r['status'],'approval_required');self.assertEqual(r['error_code'],'APPROVAL_REQUIRED')
        self.assertIs(r['data']['executed'],False);self.assertEqual(r['evidence'],[])
        self.assertEqual(r['data']['evidence_ids'],['synthetic-before'])
        self.assertEqual(rpc.await_count,1);self.assertEqual(rpc.call_args.args[0]['op'],'propose')
        self.assertNotIn('reason',rpc.call_args.args[0]);self.assertNotIn('grant',json.dumps(r))

    async def test_execute_approval_wrong_unit_and_secret_fields_rejected_before_rpc(self):
        with patch.object(server,'exchange',AsyncMock()) as rpc:
            for change in ({'mode':'execute'},{'mode':'approve'},{'unit':'ssh.service'},
                {'reason':'MYSQL_PWD=synthetic-canary'},{'evidence_ids':[]},{'approval_token':'synthetic-canary'},
                {'session_id':'synthetic-canary'},{'command':'restart nginx'}):
                with self.subTest(change=change):
                    r=await server.ProposalServer().proposal(args()|change)
                    self.assertEqual(r['status'],'error');self.assertEqual(r['evidence'],[])
                    self.assertNotIn('synthetic-canary',json.dumps(r))
            rpc.assert_not_awaited()

    async def test_status_preserves_unknown_pending_cleanup_and_observed_success(self):
        a=args();a={k:v for k,v in a.items() if k not in ('unit','reason','evidence_ids')};a.update(mode='status',action_id='b'*32)
        for state,attempt in [('pending',None),('denied',None),('expired',None),('approved',None),
                ('approved',dict(action_id='b'*32,session_id='a'*32,trace_id='c'*32,environment_id='synthetic-lab',
                   unit='nginx.service',outcome='succeeded',executed=True,job_path='/org/freedesktop/systemd1/job/12',
                   job_result='done',error_code=None,cleanup_pending=False))]:
            with self.subTest(state=state,attempt=attempt),patch.object(server,'exchange',AsyncMock(return_value=dict(action=view(state),attempt=attempt))):
                r=await server.ProposalServer().proposal(a);contracts.TOOLS['restart_service'][4].validate(r)
                self.assertEqual(r['status'],'ok')
                self.assertIs(r['data']['executed'],True if attempt else None if state=='approved' else False)
                if state=='approved' and attempt is None:self.assertEqual(r['data']['proposal_status'],'execution_unknown')

    async def test_lost_response_is_unknown_never_resends(self):
        from control_plane.transport import ChannelError
        with patch.object(server,'exchange',AsyncMock(side_effect=ChannelError('DISCONNECTED'))) as rpc:
            r=await server.ProposalServer().proposal(args())
        self.assertEqual(r['error_code'],'EXECUTION_UNKNOWN');self.assertEqual(rpc.await_count,1)

    async def test_binding_generates_metadata_references_and_rejects_forged_model_privileges(self):
        client=SimpleNamespace(call_tool=AsyncMock(return_value=native(await envelope())),disconnect=AsyncMock())
        b=RestartBinding(client,ROOT,redact=_clean,evidence=lambda:['synthetic-before']);b.session_id='a'*32;b.trace_id='c'*32
        # Copy application-generated correlation into the mock peer response.
        async def reply(name,a):
            with patch.object(server,'exchange',AsyncMock(return_value=view())):return native(await server.ProposalServer().proposal(a))
        client.call_tool.side_effect=reply
        for change in ({'approval_token':'x'},{'evidence_ids':['forged']},{'mode':'execute'},{'unit':'ssh.service'}):
            with self.assertRaises(MCPClientError):await b.call_tool('restart_service',dict(unit='nginx.service',mode='propose')|change)
        client.call_tool.assert_not_awaited()
        r=await b.call_tool('restart_service',dict(unit='nginx.service',mode='propose'))
        a=client.call_tool.call_args.args[1]
        self.assertEqual(a['reason'],REASON);self.assertEqual(a['evidence_ids'],['synthetic-before'])
        self.assertEqual(len(a['attempt_id']),32);self.assertEqual(b.action_id,'b'*32)
        self.assertEqual(b.interpret(ToolMessage('restart_service',json.dumps({'structuredContent':b._result}),False)).evidence,())
        with self.assertRaises(MCPClientError):b.interpret(ToolMessage('restart_service','{}',False))
        with self.assertRaises(MCPClientError):await b.call_tool('restart_service',dict(unit='nginx.service',mode='propose'))
        self.assertEqual(client.call_tool.await_count,1)

    async def test_no_evidence_timeout_cancel_and_malformed_result_fail_closed(self):
        for fault in ('missing','timeout','cancel','correlation','secret','oversized'):
            c=SimpleNamespace(call_tool=AsyncMock(),disconnect=AsyncMock())
            b=RestartBinding(c,ROOT,redact=_clean,evidence=lambda:[] if fault=='missing' else ['synthetic-before'])
            b.session_id='a'*32;b.trace_id='c'*32
            async def reply(name,a):
                if fault=='timeout':raise MCPClientError('TIMEOUT')
                if fault=='cancel':raise asyncio.CancelledError
                with patch.object(server,'exchange',AsyncMock(return_value=view())):r=await server.ProposalServer().proposal(a)
                if fault=='correlation':r['attempt_id']='forged'
                if fault=='secret':r['data']['message']='MYSQL_PWD=synthetic-private'
                if fault=='oversized':r['data']['message']='x'*70000
                return native(r)
            c.call_tool.side_effect=reply
            with self.subTest(fault=fault),self.assertRaises((MCPClientError,asyncio.CancelledError)):
                await b.call_tool('restart_service',dict(unit='nginx.service',mode='propose'))
            self.assertIsNone(b._result);self.assertEqual(b.evidence,{})
            if fault=='missing':c.call_tool.assert_not_awaited()
            else:self.assertEqual(c.call_tool.await_count,1)

    async def test_default_readonly_policy_still_blocks_and_explicit_proposal_stops_loop(self):
        c=SimpleNamespace(call_tool=AsyncMock(return_value=native(await envelope())),disconnect=AsyncMock())
        tool=TrustedTool('restart_service','Proposal only',BUSINESS,False)
        bridge=ToolBridge(c,(tool,),redact=_clean)
        with self.assertRaises(BoundaryError):await bridge.execute('restart_service',dict(unit='nginx.service',mode='propose'))
        c.call_tool.assert_not_awaited()
        model=SimpleNamespace(choose=AsyncMock(return_value=Decision(tool='restart_service',arguments=dict(unit='nginx.service',mode='propose'))))
        bridge=ToolBridge(c,(tool,),redact=_clean,proposal_only=frozenset({'restart_service'}))
        coordinator=Coordinator(model,bridge,read_only=frozenset({'service_status'}),proposal_only=frozenset({'restart_service'}),
            scope_allows=lambda n,a:True,interpret=lambda r:__import__('agent.coordinator',fromlist=['Observation']).Observation((),True,'Proposal only'),redact=_clean)
        r=await coordinator.run('Synthetic explicit proposal');await coordinator.finish_cleanup()
        self.assertEqual(r.status,'approval_required');self.assertEqual(r.tool_calls,1);self.assertEqual(model.choose.await_count,1)

    async def test_twelve_tools_only_with_explicit_operator_context(self):
        self.assertEqual(len(CanonicalRouter('all').tools),11)
        r=CanonicalRouter('all',action_session='a'*32);self.assertEqual(len(r.tools),12)
        self.assertFalse(r.bindings['restart_service'].trusted_tool.read_only)
        with self.assertRaises(ValueError):CanonicalRouter('actions')
        with self.assertRaises(MCPClientError):r.proposal_evidence()
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            main(['--scope','actions','--action-session','a'*32,'--backend','mock','--question','restart nginx'])

    async def test_sdk_discovery_native_proposal_and_model_worker_to_coordinator(self):
        s=ReadOnlySession(scope='actions',backend='mock',action_session='a'*32)
        s.client.bindings['service_status'].client=StdioMCPClient(StdioConfig(sys.executable,
            ('-B',str(ROOT/'tests/service_status_stdio_fixture.py')),frozenset({'service_status'}),request_timeout=10))
        b=s.client.bindings['restart_service']
        b.client=StdioMCPClient(StdioConfig(sys.executable,('-B',str(ROOT/'tests/canonical_agent/restart_fixture.py')),
            frozenset({'restart_service'}),request_timeout=10))
        # Only persistence is redirected; real binding validation and current
        # canonical source, native SDK and worker lifecycle remain in use.
        with patch('control_plane.command.store_evidence') as stored:
            r=await s.run('Synthetic explicit operator request: restart nginx')
        self.assertEqual(r.status,'approval_required');self.assertEqual(r.tool_calls,2)
        self.assertEqual(s.adapter.calls_used,1);self.assertEqual(len(r.evidence),1)
        self.assertEqual(b._result['data']['evidence_ids'],[r.evidence[0].id]);stored.assert_called_once()
        self.assertIs(b._result['data']['executed'],False);self.assertFalse(s.cleanup_pending)
        self.assertIsNone(s.client._owner);self.assertFalse(s.model.cleanup_pending)
        text=terminal_summary(r,{'restart_service':b._result})
        self.assertIn('chưa thực hiện restart',text)
        self.assertIn('Chờ duyệt đề xuất',text)
        self.assertIn('MCP không thực thi thay đổi dịch vụ',text)
        self.assertIn('AWAITING_APPROVAL',s.coordinator.state_history)
        from agent.incident_report import render_markdown
        markdown=render_markdown(r,redact=_clean)
        self.assertIn('approval_required',markdown)
        self.assertIn(r.evidence[0].id.replace('-','\\-').replace('_','\\_'),markdown)


if __name__=='__main__':unittest.main()
