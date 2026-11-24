"""Read-only service + fixed loopback HTTP + new-log checks. No action execution."""
import asyncio
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import re,secrets,time

from agent.diagnostics.journal_policy import parse_stamp,stamp,UNIT

CHECKS=['service_active','http_health','no_new_failures']
HTTP_SOURCE='collector.verify_fix.loopback_http.v1'
JOURNAL_SOURCE='collector.verify_fix.journalctl.v1'
HTTP_SUMMARY='Loopback HEAD / returned HTTP '


def authorized(a):
    try:
        now=datetime.now(timezone.utc);start=parse_stamp(a['since'])
        return (a['unit']==UNIT and a['check_types']==CHECKS and a.get('until','absent') is None
            and now-timedelta(minutes=16)<=start<=now
            and (a.get('action_id') is None or isinstance(a.get('action_id'),str))
            and isinstance(a.get('baseline_evidence_refs'),list))
    except (KeyError,TypeError,ValueError,OverflowError):return False


def evidence(source,text,*,unit=UNIT,at=None):
    return dict(evidence_id='verify-'+secrets.token_hex(16),source=source,
        observed_at=at or stamp(datetime.now(timezone.utc)),unit=unit,excerpt=text,redacted=True,truncated=False)


async def _close_http(writer):
    try:
        await writer.wait_closed()
    except OSError:
        pass  # Socket already closed; never expose an OS diagnostic.


async def http_health():
    """Fixed address/path, no redirects, proxies, body, cookies or credentials."""
    writer=None
    try:
        async with asyncio.timeout(3):
            reader,writer=await asyncio.open_connection('127.0.0.1',80,limit=4096)
            writer.write(b'HEAD / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
            await writer.drain()
            head=await reader.readuntil(b'\r\n\r\n')
            if len(head)>4096:raise ValueError()
            match=re.fullmatch(rb'HTTP/1\.[01] ([1-5][0-9]{2})(?: [\x20-\x7e]*)?',head.split(b'\r\n',1)[0])
            if match is None:raise ValueError()
            status=int(match[1]);result='pass' if 200<=status<300 else 'fail' if status>=400 else 'unknown'
            ev=evidence(HTTP_SOURCE,HTTP_SUMMARY+str(status)+'; fixed 127.0.0.1:80; no redirect followed; no body retained')
            return dict(check_type='http_health',result=result,summary=ev['excerpt'],evidence_refs=[ev['evidence_id']]),[ev]
    except ConnectionRefusedError:
        # A failed connection has no HTTP response or invented health evidence.
        return dict(check_type='http_health',result='unknown',summary='Loopback HTTP connection refused; no HTTP response received.',evidence_refs=[]),[]
    except (TimeoutError,OSError,ValueError,asyncio.IncompleteReadError,asyncio.LimitOverrunError):
        return dict(check_type='http_health',result='unknown',summary='Loopback HTTP response unavailable, malformed or timed out; no body retained.',evidence_refs=[]),[]
    finally:
        if writer is not None:
            writer.close()
            cleanup=asyncio.create_task(_close_http(writer),name='health-http-cleanup')
            cancelled=False
            while True:
                try:
                    await asyncio.shield(cleanup)
                    break
                except asyncio.CancelledError:
                    if cleanup.cancelled():raise
                    cancelled=True
            # Keep the owner until close is joined, even after repeated cancel.
            # This join has no hard wall guarantee; never detach it on expiry.
            if cancelled:raise asyncio.CancelledError


async def service_check(a):
    import mock_mcp_server as contracts
    from src.mcp_server import service_status
    from agent.diagnostics.service_verification import assess,validate_snapshot
    request={k:a[k] for k in ('schema_version','session_id','trace_id','logical_tool_call_id','attempt_id','unit')}
    r=await service_status.collect(request)
    if not contracts.TOOLS['service_status'][4].is_valid(r):raise ValueError()
    if r['status']=='error':
        return dict(check_type='service_active',result='unknown',summary='Service read unavailable: '+r['error_code']+'; no repair claim.',evidence_refs=[]),[]
    if r['status']!='ok' or any(r[k]!=v for k,v in request.items() if k!='unit'):raise ValueError()
    if r['truncated'] or r['next_cursor'] is not None:raise ValueError()
    ev=[deepcopy(validate_snapshot(r,a['unit']))]
    result,summary=assess(ev[0]['excerpt'],action_context=a.get('action_id') is not None)
    return dict(check_type='service_active',result=result,summary=summary,evidence_refs=[ev[0]['evidence_id']]),ev


async def log_check(a):
    from agent.diagnostics.journal_reader import collect_journal,JournalError
    try:
        end=stamp(datetime.now(timezone.utc))
        # Keep the requested since exactly. A tiny dispatch-time elapsed part is
        # admitted by a bounded 30-minute reader, not by dropping old records.
        from agent.diagnostics import read_gateway
        try:
            entries=(await read_gateway.journal_entries({'since':a['since'],'until':end})
                if read_gateway.enabled() else await collect_journal({'since':a['since'],'until':end},minutes=30))
        except read_gateway.ReadError as error:raise JournalError(error.code) from None
        count=len(entries)
        ev=evidence(JOURNAL_SOURCE,'New nginx priority 0..3 journal records visible since='+a['since']+
            '; until='+end+'; count='+str(count)+'; history/reader coverage not independently complete')
        result='fail' if count else 'unknown'
        summary=('New error-priority journal records were observed in the requested window.' if count else
                 'No new error records visible; incomplete source coverage prevents a verified pass.')
        return dict(check_type='no_new_failures',result=result,summary=summary,evidence_refs=[ev['evidence_id']]),[ev]
    except JournalError as e:
        return dict(check_type='no_new_failures',result='unknown',summary='New-log check unavailable: '+e.code+'; no absence-of-errors claim.',evidence_refs=[]),[]


async def collect(a):
    import mock_mcp_server as contracts
    start,tick=contracts.current_time(),time.monotonic()
    # A producer failure cancels and joins siblings (including their cleanup)
    # before the worker can publish an error or exit. gather() alone does not.
    async with asyncio.TaskGroup() as group:
        tasks=(group.create_task(service_check(a),name='health-service-check'),
               group.create_task(http_health(),name='health-http-check'),
               group.create_task(log_check(a),name='health-log-check'))
    results=[task.result() for task in tasks]
    checks=[item[0] for item in results];ev=[e for _,items in results for e in items]
    states={c['result'] for c in checks}
    aggregate='fail' if 'fail' in states else 'unknown' if 'unknown' in states else 'pass'
    # Caller references are external baseline context, not fresh evidence and
    # not an assertion that the action was approved or actually executed.
    data=dict(unit=UNIT,action_id=a.get('action_id'),baseline_evidence_refs=list(a.get('baseline_evidence_refs') or []),checks=checks,overall_result=aggregate,
        verified_at=contracts.current_time())
    r=contracts.make_response('verify_fix',a['trace_id'],a['logical_tool_call_id'],a['attempt_id'],data,a['session_id'],status='partial',evidence=ev)
    r.update(started_at=start,completed_at=contracts.current_time(),duration_ms=int((time.monotonic()-tick)*1000))
    return r
