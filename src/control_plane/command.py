"""Explicit VM operator UI; proposals collect real read-only evidence as reader.

No --yes, commands, arbitrary units/paths, endpoint/model settings or grants.
Approval and execution require separate fresh TTY confirmations. Never retries.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

from .approval import ApprovalError
from .ledger import JobResult
from .review import OperatorClient, PendingReview
from .runtime import SOCKETS, connect, roles
from .transport import ChannelError

READER_STATE=Path('/var/lib/linux-log-agent/reader')


def store_evidence(envelope,trace_id):
    """Persist the caller's validated/redacted canonical ledger before proposal.

    Reuse the existing bounded, no-follow/no-overwrite incident report writer.
    Restore the reader's working directory even when publication fails. This
    synchronous section never yields while holding a temporary directory switch.
    A snapshot is provenance data, not independent evidence authentication.
    """
    from agent.cli.report_store import save_report
    if type(trace_id) is not str or re.fullmatch('[0-9a-f]{32}',trace_id) is None:
        raise ApprovalError('INVALID_RESPONSE')
    cwd=os.open('.',os.O_RDONLY|os.O_DIRECTORY)
    fd=None
    try:
        fd=os.open(READER_STATE,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        info=os.fstat(fd)
        if info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700:
            raise ApprovalError('INVALID_DEPLOYMENT')
        os.fchdir(fd)
        data=json.dumps(envelope,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        return save_report('proposal-'+trace_id+'.md',
            '# Canonical read-only evidence snapshot\n\nUntrusted data; not proof of a verified fix.\n\n```json\n'+data+'\n```\n').absolute()
    finally:
        try:os.fchdir(cwd)
        finally:
            os.close(cwd)
            if fd is not None:os.close(fd)


def validate_attempt(result,review):
    fields={'action_id','session_id','trace_id','environment_id','unit','outcome',
            'executed','job_path','job_result','error_code','cleanup_pending'}
    if type(result) is not dict or set(result)!=fields:raise ApprovalError('INVALID_RESPONSE')
    for key in ('action_id','session_id','trace_id','environment_id'):
        if result[key]!=getattr(review,key):raise ApprovalError('INVALID_RESPONSE')
    if result['unit']!='nginx.service' or type(result['cleanup_pending']) is not bool:
        raise ApprovalError('INVALID_RESPONSE')
    if result['error_code']=='STATE_UNAVAILABLE':
        if result['job_path'] is not None or result['job_result'] is not None:raise ApprovalError('INVALID_RESPONSE')
        observed=JobResult()
    else:observed=JobResult(result['job_path'],result['job_result'],result['error_code'])
    if result['outcome']!=observed.outcome or result['executed'] is not observed.executed:
        raise ApprovalError('INVALID_RESPONSE')
    return result


async def propose(session_id):
    identities=roles()
    if os.geteuid()!=identities['reader'].pw_uid:raise ApprovalError('CALLER_DENIED')
    # Imported only after the real reader identity check. No LLM is constructed.
    from agent.runtime import runtime_root
    from agent.service_binding import ServiceBinding
    from agent.mcp_client import StdioMCPClient, StdioConfig, MCPClientError
    from agent.llm.redaction import _clean
    root=runtime_root()
    client=StdioMCPClient(StdioConfig(command=sys.executable,
        args=('-B',str(root/'run_mcp_server.py'),'--linux-service-status'),
        allowed_tools=frozenset({'service_status'}),request_timeout=15))
    binding=ServiceBinding(client,root,redact=_clean)
    binding.session_id=session_id
    cancelled=False
    try:
        await client.connect()
        page=await client.list_tools()
        tools=[t for t in page.tools if t['name']=='service_status']
        if (page.next_cursor is not None or len(tools)!=1 or tools[0].get('inputSchema')!=binding.schemas[0]
                or tools[0].get('outputSchema')!=binding.schemas[2]):raise ApprovalError('INVALID_REGISTRY')
        await binding.call_tool('service_status',{'unit':'nginx.service'})
    except asyncio.CancelledError:
        cancelled=True
    finally:
        while client._owner is not None:
            try:await client.disconnect()
            except asyncio.CancelledError:cancelled=True
            except MCPClientError as error:
                if error.code!='CLEANUP_TIMEOUT':raise
    if cancelled:raise asyncio.CancelledError()
    if not binding.evidence:raise ApprovalError('INSUFFICIENT_EVIDENCE')
    snapshot=store_evidence(binding._result,binding.trace_id)
    started=time.monotonic()
    async with await connect(SOCKETS['proposal'],identities['approval'].pw_uid) as channel:
        await channel.send(dict(version=1,op='propose',session_id=session_id,trace_id=binding.trace_id,
                                unit='nginx.service',evidence_ids=list(binding.evidence)))
        response=await channel.receive()
    if (set(response)!={'version','ok','result'} or type(response.get('version')) is not int
            or response['version']!=1 or response.get('ok') is not True):raise ApprovalError('PROPOSAL_DENIED')
    view=PendingReview(response['result'],request_started=started)
    if view.session_id!=session_id or view.trace_id!=binding.trace_id:raise ApprovalError('INVALID_RESPONSE')
    return dict(action_id=view.action_id,session_id=view.session_id,trace_id=view.trace_id,
                evidence_ids=list(view.evidence_ids),state=view.state,executed=None,
                observed_state=binding._result['data']['active_state'],evidence_snapshot=str(snapshot),
                evidence=list(binding.evidence.values()))


async def operator(operation,action_id=None):
    identities=roles()
    if os.geteuid()!=identities['operator'].pw_uid:raise ApprovalError('CALLER_DENIED')
    client=OperatorClient(lambda:connect(SOCKETS['control'],identities['approval'].pw_uid),
                          expected_uid=identities['approval'].pw_uid)
    if operation=='open-session':
        result=await client._request(dict(version=1,op='open_session'))
        if (type(result) is not dict or set(result)!={'session_id'} or type(result['session_id']) is not str
                or re.fullmatch('[0-9a-f]{32}',result['session_id']) is None):raise ApprovalError('INVALID_RESPONSE')
        return result
    view=await client.pending(action_id)
    if operation=='status':
        return dict(action_id=view.action_id,state=view.state,executed=None,
                    meaning='approval status only; not an execution result')
    if operation=='execution-status':
        result=await client._request(dict(version=1,op='execution_status',action_id=action_id))
        return validate_attempt(result,view)
    if operation=='review':
        try:
            decision=view.confirm()
            if decision not in ('confirmed','rejected'):raise ApprovalError('CONFIRMATION_REQUIRED')
            updated=await client.submit(view)
            return dict(action_id=updated.action_id,state=updated.state,executed=None,
                        meaning='approved does not mean executed; execution is separate')
        finally:finish_terminal(view)
    if operation!='execute':raise ApprovalError('INVALID_COMMAND')
    try:
        if view.confirm(execution=True)!='execution_confirmed':raise ApprovalError('CONFIRMATION_REQUIRED')
        if time.monotonic()>=view._deadline:raise ApprovalError('EXPIRED')
        view._decision='submitted'  # Consume local confirmation before one exchange.
        result=await client._request(dict(version=1,op='execute',action_id=action_id))
        return validate_attempt(result,view)
    finally:finish_terminal(view)


async def verify(action_id,session_id):
    """Explicit reader-only fresh check; does not approve, submit or retry."""
    identities=roles()
    if os.geteuid()!=identities['reader'].pw_uid:raise ApprovalError('CALLER_DENIED')
    from .verification import baseline,fresh_checks,incident,now
    import agent
    from agent.runtime import runtime_root
    from agent.service_binding import ServiceBinding
    from agent.mcp_client import StdioMCPClient,StdioConfig
    from agent.llm.redaction import _clean
    from agent.cli.report_store import save_report
    from uuid import uuid4
    # Reuse the authenticated exact-response channel client; no operator rights
    # are acquired by the reader using this transport helper.
    client=OperatorClient(lambda:connect(SOCKETS['proposal'],identities['approval'].pw_uid),
                          expected_uid=identities['approval'].pw_uid)
    started=time.monotonic()
    context=await client._request(dict(version=1,op='verification_context',action_id=action_id,session_id=session_id))
    if type(context) is not dict or set(context)!={'action','attempt'}:raise ApprovalError('INVALID_RESPONSE')
    view=PendingReview(context['action'],request_started=started)
    if view.action_id!=action_id or view.session_id!=session_id or view.state!='approved':
        raise ApprovalError('INVALID_RESPONSE')
    attempt=validate_attempt(context['attempt'],view)
    if attempt['cleanup_pending']:raise ApprovalError('CLEANUP_PENDING')
    since=now()  # Strictly after the status observation; not an invented job timestamp.
    root=runtime_root()
    schema=ServiceBinding(None,root,redact=_clean).schemas[2]
    before=baseline(READER_STATE,view,schema,_clean)
    sdk=StdioMCPClient(StdioConfig(sys.executable,('-B',str(Path(agent.__file__).with_name('diagnostic_server.py')),'--tool','verify_fix'),
                                   frozenset({'verify_fix'}),request_timeout=20))
    after=await fresh_checks(sdk,root,view,before,since=since,redact=_clean)
    content=incident(view,attempt,before,after,redact=_clean)
    # Same private directory checks as proposal persistence, before publishing.
    fd=os.open(READER_STATE,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700:
            raise ApprovalError('INVALID_DEPLOYMENT')
        os.fchdir(fd)
        path=save_report('verification-'+uuid4().hex+'.md',content)
    finally:os.close(fd)
    return dict(action_id=view.action_id,session_id=view.session_id,trace_id=view.trace_id,
                job_outcome=attempt['outcome'],executed=attempt['executed'],
                verification_status=after['status'],check_result=after['data']['overall_result'],
                repair_verified=False,cleanup_pending=False,report=str(path),
                evidence_ids=[item['evidence_id'] for item in after['evidence']],
                baseline_evidence_refs=after['data']['baseline_evidence_refs'])


def finish_terminal(view):
    warned=False
    while view.cleanup_pending:
        try:view.finish_cleanup()
        except (Exception,KeyboardInterrupt):
            if not warned:
                print('TERMINAL_CLEANUP_PENDING',file=sys.stderr);warned=True
            time.sleep(.1)  # Retain this process/fd; never proceed with execution.


class SafeParser(argparse.ArgumentParser):
    def error(self,message):
        self.exit(2,'INVALID_COMMAND\n')


def main(argv=None):
    parser=SafeParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('operation',choices=('open-session','propose','review','status','execute','execution-status','verify'))
    parser.add_argument('identifier',nargs='?')
    parser.add_argument('--session')
    args=parser.parse_args(argv)
    if args.operation!='open-session' and (args.identifier is None or re.fullmatch('[0-9a-f]{32}',args.identifier) is None):
        parser.error('INVALID_ID')
    if args.operation=='open-session' and args.identifier is not None:parser.error('INVALID_ID')
    if ((args.operation=='verify' and (args.session is None or re.fullmatch('[0-9a-f]{32}',args.session) is None))
            or (args.operation!='verify' and args.session is not None)):parser.error('INVALID_ID')
    try:
        result=asyncio.run(verify(args.identifier,args.session) if args.operation=='verify' else
                         propose(args.identifier) if args.operation=='propose' else operator(args.operation,args.identifier))
        print(json.dumps(result,ensure_ascii=False));return 0
    except KeyboardInterrupt:return 130
    except (ApprovalError,ChannelError) as error:
        # Never print arbitrary exception strings, model/log text or a grant.
        allowed={'CALLER_DENIED','CONFIRMATION_REQUIRED','EXPIRED','EXECUTION_DISABLED','EXECUTION_UNKNOWN',
                 'STATE_FINAL','REQUEST_DENIED','PROPOSAL_DENIED','INVALID_RESPONSE','TIMEOUT','DISCONNECTED',
                 'CLEANUP_PENDING','INVALID_BASELINE','INVALID_VERIFICATION','UNKNOWN_ACTION'}
        print(error.code if error.code in allowed else 'ACTION_REQUEST_FAILED',file=sys.stderr);return 1
    except Exception:print('ACTION_REQUEST_FAILED',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
