"""Synthetic OOM paging candidate tests; no Linux reads or service operations."""
from datetime import timedelta

import pytest

import mock_mcp_server as contracts
from src.mcp_server import oom_events as oom
from src.mcp_server.oom_pagination import OomPaginator, TOOL
from test_p03_02_oom_events import REQ, go, jline, ok, fake, NO_FILE, BOOT, BOOT_WALL, QUIET_OLDEST, QUIET_DMESG


def response(req,n=5):
    start=oom.parse_ts(req['since'])
    events=[]
    evidence=[]
    for i in range(n):
        stamp=oom.fmt_ts(start+timedelta(seconds=i+1))
        ident='oom-synthetic-%06d'%i
        msg='Out of memory: Killed process %d (lab-oom)'%(i+1)
        events.append(dict(timestamp=stamp,pid=i+1,process='lab-oom',message=msg,
                           anon_rss_kb=1,total_vm_kb=2,evidence_id=ident))
        evidence.append(dict(evidence_id=ident,observed_at=stamp,source='journal.kernel',
                             unit=None,excerpt=msg,redacted=False,truncated=False))
    coverage=[dict(source=s,status='ok',oldest_available_at=req['since']) for s in oom.SOURCES]
    return oom._envelope(req,'ok',dict(events=events,count=n,limit=req.get('limit',100),coverage=coverage),evidence)


def checked(result,request_id=0):
    contracts.TOOLS[TOOL][4].validate(result)
    assert contracts.wire_size(contracts.native_result(result),request_id)<=65536
    return result


def test_snapshot_acquisition_retains_more_than_wire_limit_without_fake_cursor():
    lines=[jline('2026-11-14T09:00:%02dZ'%i,
        'Out of memory: Killed process %d (lab-oom) total-vm:1kB'%(i+1)) for i in range(5)]
    result=oom.collect({**REQ,'limit':1},run=fake(ok('\n'.join(lines)),QUIET_OLDEST,QUIET_DMESG),
                       find=lambda names:names[0],lacks_access=lambda:False,allow_file=NO_FILE,
                       boot_id=lambda:BOOT,boot_wall=lambda:BOOT_WALL,snapshot=True)
    assert result['data']['count']==5 and not result['truncated'] and result['next_cursor'] is None
    legacy=go(req={'limit':1},journal=ok('\n'.join(lines)),oldest=QUIET_OLDEST,dmesg=QUIET_DMESG)
    assert legacy['data']['count']==1 and legacy['truncated']


def test_more_than_200_events_pages_without_reacquisition_or_gaps():
    calls=[]
    def acquire(req):
        calls.append(1)
        return response(req,450)
    pager=OomPaginator(acquire,'s',lambda:True)
    req={**REQ,'limit':200}
    ids=[]
    while True:
        result=checked(pager.call(req))
        assert result['data']['count']<=200 and result['status']=='ok'
        ids.extend(e['evidence_id'] for e in result['data']['events'])
        if result['next_cursor'] is None: break
        req={**req,'cursor':result['next_cursor']}
    assert len(calls)==1 and len(ids)==len(set(ids))==450


@pytest.mark.parametrize('change',[dict(session_id='other'),dict(limit=2),dict(until='2026-11-14T23:59:59Z')])
def test_mismatched_cursor_never_resets(change):
    pager=OomPaginator(response,'s',lambda:True)
    first=pager.call({**REQ,'limit':1})
    result=checked(pager.call({**REQ,'limit':1,'cursor':first['next_cursor'],**change}))
    assert result['error_code']=='INVALID_INPUT'


def test_expiry_and_close_drop_tokens():
    ticks=[0]
    pager=OomPaginator(response,'s',lambda:True,clock=lambda:ticks[0])
    token=pager.call({**REQ,'limit':1})['next_cursor']
    ticks[0]=300
    assert checked(pager.call({**REQ,'limit':1,'cursor':token}))['error_code']=='EXPIRED'
    pager.close()
    assert pager.call(REQ)['error_code']=='INVALID_INPUT' and not pager._tokens


def test_rotation_retention_changes_do_not_modify_frozen_pages():
    raw=response(REQ)
    pager=OomPaginator(lambda req:raw,'s',lambda:True)
    first=pager.call({**REQ,'limit':1})
    raw['data']['events'].clear()
    req={**REQ,'limit':1,'cursor':first['next_cursor']}
    page=checked(pager.call(req))
    retry=checked(pager.call(req))
    assert page['data']==retry['data'] and page['next_cursor']==retry['next_cursor']
    assert page['data']['events'][0]['pid']==2


def test_permission_revocation_between_pages_clears_cache():
    allowed=[True]
    pager=OomPaginator(response,'s',lambda:allowed[0])
    token=pager.call({**REQ,'limit':1})['next_cursor']
    allowed[0]=False
    assert checked(pager.call({**REQ,'limit':1,'cursor':token}))['error_code']=='POLICY_DENIED'
    assert not pager._tokens


@pytest.mark.parametrize('change',[dict(cursor='forged'),dict(since='PRIVATE'),dict(limit=True),dict(approved=True)])
def test_invalid_requests_never_acquire_or_echo(change):
    def never(_): pytest.fail('Invalid input reached source')
    result=checked(OomPaginator(never,'s',lambda:True).call({**REQ,**change}))
    assert result['error_code']=='INVALID_INPUT' and 'PRIVATE' not in str(result)


def test_malformed_output_and_ids_reject():
    def bad(req):
        result=response(req)
        result['trace_id']='wrong'
        return result
    assert checked(OomPaginator(bad,'s',lambda:True).call(REQ))['error_code']=='EXECUTION_UNKNOWN'


def test_source_cut_has_no_fake_continuation():
    def cut(req):
        result=response(req)
        result.update(status='partial',truncated=True,error_code='OUTPUT_LIMIT')
        return result
    result=checked(OomPaginator(cut,'s',lambda:True).call({**REQ,'limit':1}))
    assert result['truncated'] and result['next_cursor'] is None


def test_retention_gap_preserved_across_pages():
    def partial(req):
        result=response(req)
        result['status']='partial'
        result['data']['coverage'][1]['oldest_available_at']=None
        return result
    pager=OomPaginator(partial,'s',lambda:True)
    first=checked(pager.call({**REQ,'limit':1}))
    second=checked(pager.call({**REQ,'limit':1,'cursor':first['next_cursor']}))
    assert second['status']=='partial' and second['data']['coverage'][1]['oldest_available_at'] is None


def test_native_sizing_uses_supplied_builder_and_admitted_id():
    seen=[]
    def native(result):
        seen.append(1)
        return contracts.native_result(result)
    result=checked(OomPaginator(response,'s',lambda:True,native_builder=native).call(REQ,'x'*126),'x'*126)
    assert seen and result['data']['count']==5


def test_source_exception_and_cache_budget_fail_closed():
    def bad(_): raise RuntimeError('PRIVATE')
    assert checked(OomPaginator(bad,'s',lambda:True).call(REQ))['error_code']=='EXECUTION_UNKNOWN'
    pager=OomPaginator(response,'s',lambda:True)
    pager.MAX_TOKENS=0
    assert checked(pager.call({**REQ,'limit':1}))['error_code']=='OUTPUT_LIMIT'


@pytest.mark.parametrize('status,code',[('permission_denied','PERMISSION_DENIED'),('unavailable','UNSUPPORTED')])
def test_no_readable_source_preserves_ids_and_reconstructs_safe_coverage(status,code):
    coverage=[dict(source=s,status=status,oldest_available_at=None) for s in oom.SOURCES]
    def denied(req):
        return oom._envelope(req,'error',dict(message='PRIVATE',coverage=coverage),error_code=code)
    result=checked(OomPaginator(denied,'s',lambda:True).call(REQ))
    assert result['error_code']==code and result['session_id']=='s'
    assert result['data']['coverage']==coverage and 'PRIVATE' not in str(result)
