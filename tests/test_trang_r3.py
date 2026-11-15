"""R3: accepted-request correlation, unknown dispatch, TTL and provenance."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import sys

import pytest
from mcp import Client, StdioServerParameters

import mock_mcp_server as server
from test_contract_audit import arguments, check, local
from test_trang_r2 import ROOT

def at(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


@pytest.mark.parametrize('name', server.LOG_SOURCES)
@pytest.mark.parametrize('state,code', [('permission_denied','PERMISSION_DENIED'),('unavailable','UNSUPPORTED')])
def test_accepted_coverage_correlation(name, state, code, monkeypatch):
    monkeypatch.setattr(server, 'log_coverage', lambda n: [dict(source=server.LOG_SOURCES[n][0],status=state,oldest_available_at=None)])
    args = arguments(name)
    result = check(name, local(name, args))
    assert result['error_code'] == code
    for key in ['session_id','trace_id','logical_tool_call_id','attempt_id']:
        assert result[key] == args[key]
    assert result['data']['message'] == server.LOG_SOURCES[name][0] + (': permission denied' if state == 'permission_denied' else ': source unavailable')
    assert not {'field','rule'} & result['data'].keys()
    # Invalid semantic input must not switch to accepted-request metadata handling.
    bad = local(name, {**args, 'cursor':'INVALID_CURSOR_SECRET', 'trace_id':'INVALID_CURSOR_SECRET'})
    assert 'INVALID_CURSOR_SECRET' not in bad.model_dump_json()


@pytest.mark.parametrize('name', server.LOG_SOURCES)
def test_evidence_links_source_enum_and_retention(name, monkeypatch):
    args = arguments(name)
    value = check(name, local(name,args))
    evidence = {item['evidence_id']:item for item in value['evidence']}
    assert len(evidence) == len(value['data']['events']) == 2
    for event in value['data']['events']:
        item = evidence[event['evidence_id']]
        assert item['source'] == server.LOG_SOURCES[name][0]
        assert item['observed_at'] == event['timestamp']
        assert item['excerpt'] == event['message'] and item['redacted']
    for branch in ['ok','error']:
        corrupt = json.loads(json.dumps(value))
        if branch == 'error':
            corrupt.update(status='error',error_code='PERMISSION_DENIED')
            corrupt['data']={'message':'Denied','coverage':value['data']['coverage']}
        corrupt['data']['coverage'][0]['source'] = 'auth.log' if name == 'memory_oom_events' else 'dmesg'
        assert not server.TOOLS[name][4].is_valid(corrupt)
    monkeypatch.setattr(server, 'log_coverage', lambda n: [dict(source=server.LOG_SOURCES[n][0],status='ok',oldest_available_at='2026-11-02T10:00:00Z')])
    value = check(name, local(name,args))
    assert value['status'] == 'partial' and not value['truncated']
    assert value['data']['events'] == [] and value['evidence'] == []


def test_restart_ttl_unit_unknown_and_expiry(monkeypatch):
    args = {**arguments('restart_service'), 'unit':'other-lab.service','reason':'UNTRUSTED_REASON_SECRET'}
    value = check('restart_service',local('restart_service',args))
    assert 59 < (at(value['data']['expires_at'])-at(value['started_at'])).total_seconds() <= 61
    assert value['data']['unit']=='other-lab.service'
    assert value['data']['reason']=='[REDACTED]' and 'UNTRUSTED_REASON_SECRET' not in json.dumps(value)
    status = {k:v for k,v in args.items() if k not in {'unit','reason','evidence_ids'}}
    status.update(mode='status',action_id=value['data']['action_id'])
    read = check('restart_service',local('restart_service',{**status,'unit':'wrong.service'}))
    assert read['data']['unit']=='other-lab.service'
    for state in server.RESTART_STATES:
        response = check('restart_service',local('restart_service',{**status,'action_id':'mock-fixture-'+state}))
        assert response['data']['unit']=='nginx.service'
        assert (at(response['data']['expires_at']) < at(response['started_at'])) == (state == 'expired')
        if state == 'execution_unknown':
            assert response['data']['executed'] is None
            response['data']['executed']=False
            assert not server.TOOLS['restart_service'][4].is_valid(response)
    key=(args['session_id'],status['action_id'])
    monkeypatch.setitem(server.PROPOSALS,key,dict(unit='other-lab.service',expires_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()))
    assert check('restart_service',local('restart_service',status))['error_code']=='NOT_FOUND'


@pytest.mark.parametrize('profile', ['coverage-denied','coverage-unavailable'])
def test_stdio_accepted_ids(profile, capfd):
    async def run():
        async with Client(StdioServerParameters(command=sys.executable,args=[str(ROOT/'tests/mcp_smoke/adversarial_server.py'),profile])) as client:
            for name in server.LOG_SOURCES:
                args=arguments(name)
                native=await client.call_tool(name,args)
                result=check(name,native)
                for key in ['session_id','trace_id','logical_tool_call_id','attempt_id']:
                    assert result[key]==args[key]
                assert 'Request rejected' not in native.content[0].text
    asyncio.run(run())
    assert 'Traceback' not in capfd.readouterr().err
