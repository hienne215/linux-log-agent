"""Non-root SDK stdio proposal/status adapter; no execute, approval or shell API."""
import asyncio
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import mock_mcp_server as contracts
from src.mcp_server.server import ServerScaffold
from agent.restart_proposal import TOOL,REASON


async def exchange(request):
    import os
    from control_plane.runtime import roles,connect,SOCKETS
    from control_plane.review import OperatorClient
    from control_plane.approval import ApprovalError
    ids=roles()
    if os.geteuid()!=ids['reader'].pw_uid:raise ApprovalError('CALLER_DENIED')
    # No grant/key/operator connection or identity change is acquired here.
    client=OperatorClient(lambda:connect(SOCKETS['proposal'],ids['approval'].pw_uid),expected_uid=ids['approval'].pw_uid)
    return await client._request(request)


class ProposalServer(ServerScaffold):
    def __init__(self):super().__init__()

    async def proposal(self,a):
        if (not contracts.TOOLS[TOOL][3].is_valid(a)
                or any(re.fullmatch('[0-9a-f]{32}',a[k]) is None for k in ('session_id','trace_id','logical_tool_call_id','attempt_id'))):
            return contracts.safe_error(TOOL,{},'INVALID_INPUT',rule='canonical_request')
        dispatched=False
        try:
            if a['mode']=='propose':
                if a['unit']!='nginx.service' or a['reason']!=REASON or len(a['evidence_ids'])>16:raise ValueError()
                dispatched=True
                action=await exchange(dict(version=1,op='propose',session_id=a['session_id'],trace_id=a['trace_id'],
                    unit='nginx.service',evidence_ids=a['evidence_ids']))
                from control_plane.review import PendingReview
                view=PendingReview(action,request_started=time.monotonic())
                if (view.session_id!=a['session_id'] or view.trace_id!=a['trace_id'] or view.state!='pending'
                        or list(view.evidence_ids)!=a['evidence_ids']):raise ValueError()
                data=dict(action_id=view.action_id,proposal_status='pending_approval',reason=REASON,
                    evidence_ids=a['evidence_ids'],executed=False,message='Proposal only; separate human approval and execution are required.',
                    expires_at=view.expires_at,unit='nginx.service')
                status,code='approval_required','APPROVAL_REQUIRED'
            else:
                if set(a)!={'schema_version','session_id','trace_id','logical_tool_call_id','attempt_id','mode','action_id'}:raise ValueError()
                dispatched=True
                context=await exchange(dict(version=1,op='proposal_context',session_id=a['session_id'],action_id=a['action_id']))
                from control_plane.review import PendingReview
                from control_plane.command import validate_attempt
                if type(context) is not dict or set(context)!={'action','attempt'}:raise ValueError()
                view=PendingReview(context['action'],request_started=time.monotonic())
                if view.action_id!=a['action_id'] or view.session_id!=a['session_id']:raise ValueError()
                attempt=context['attempt']
                state={'pending':'pending_approval','denied':'denied','expired':'expired'}.get(view.state,'execution_unknown')
                executed=False if view.state!='approved' else None
                if attempt is not None:
                    if view.state!='approved':raise ValueError()
                    attempt=validate_attempt(attempt,view)
                    state=attempt['outcome'];executed=attempt['executed']
                    if state=='failed':state='execution_unknown'  # canonical failed requires boolean; do not invent False.
                    if attempt['cleanup_pending']:state,executed='execution_unknown',None
                data=dict(action_id=view.action_id,proposal_status=state,executed=executed,expires_at=view.expires_at,
                    unit='nginx.service',message='Read-only status; approval is not execution or verified repair.')
                status,code='ok',None
            result=contracts.make_response(TOOL,a['trace_id'],a['logical_tool_call_id'],a['attempt_id'],data,a['session_id'],status=status,evidence=[])
            result['error_code']=code
            return result
        except asyncio.CancelledError:raise
        except Exception as error:
            code=getattr(error,'code','EXECUTION_UNKNOWN' if dispatched else 'POLICY_DENIED')
            mapping={'CALLER_DENIED':'PERMISSION_DENIED','TIMEOUT':'TIMEOUT','DISCONNECTED':'EXECUTION_UNKNOWN',
                'EXPIRED':'EXPIRED','UNIT_LOCKED':'POLICY_DENIED','HELPER_REJECTED':'HELPER_UNAVAILABLE',
                'OUTPUT_LIMIT':'OUTPUT_LIMIT'}
            return contracts.safe_error(TOOL,{},mapping.get(code,'EXECUTION_UNKNOWN' if dispatched else 'POLICY_DENIED'),rule='proposal_unavailable')

    async def call_tool(self,ctx,params):
        if params.name!=TOOL:return await super().call_tool(ctx,params)
        result=await self.proposal(params.arguments or {})
        if not contracts.TOOLS[TOOL][4].is_valid(result):result=contracts.safe_error(TOOL,{},'EXECUTION_UNKNOWN',rule='proposal_validation')
        result=contracts.bound_response(result,ctx.request_id or 0,native_builder=self.native)
        return self.native(result)


def main():
    if len(sys.argv)!=1:return 2
    asyncio.run(ProposalServer().serve());return 0


if __name__=='__main__':raise SystemExit(main())
