"""Review R2: source coverage, restart modes, generation and SDK wire privacy."""
import asyncio
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

import mock_mcp_server as server
from test_contract_audit import arguments, check, local

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('name', ['memory_oom_events', 'failed_logins'])
def test_coverage_distinguishes_empty_and_denied(name, monkeypatch):
    args = {**arguments(name), 'since': '2026-11-03T00:00:00Z', 'until': '2026-11-04T00:00:00Z'}
    value = check(name, local(name, args))
    assert value['status'] == 'ok' and value['data']['events'] == []
    monkeypatch.setattr(server, 'log_coverage', lambda _: [dict(source='journal', status='permission_denied', oldest_available_at=None)])
    value = check(name, local(name, args))
    assert value['status'] == 'error' and value['error_code'] == 'PERMISSION_DENIED'
    assert value['data']['coverage'][0]['status'] == 'permission_denied'
    monkeypatch.setattr(server, 'log_coverage', lambda _: [dict(source='journal', status='ok', oldest_available_at=None), dict(source='dmesg', status='unavailable', oldest_available_at=None)])
    value = check(name, local(name, args))
    assert value['status'] == 'partial' and not value['truncated']


@pytest.mark.parametrize('name', ['memory_oom_events', 'failed_logins'])
def test_event_cursor_ownership_and_nullable_fields(name):
    args = {**arguments(name), 'limit': 1}
    first = check(name, local(name, args))
    assert first['next_cursor'] and first['status'] == 'partial'
    second = check(name, local(name, {**args, 'cursor': first['next_cursor']}))
    assert second['next_cursor'] is None and second['data']['events'] != first['data']['events']
    assert local(name, {**args, 'cursor': first['next_cursor'], 'session_id': 'other'}).is_error
    event = second['data']['events'][0]
    if name == 'memory_oom_events':
        assert event['process'] is None and event['pid'] is None
    else:
        assert event['source_ip'] is None and event['user'] == '[REDACTED]'


@pytest.mark.parametrize('name', ['query_journal', 'search_log', 'memory_oom_events', 'failed_logins', 'detect_anomaly', 'verify_fix'])
@pytest.mark.parametrize('start,end', [('2026-11-02T00:00:00Z','2026-11-02T00:00:00Z'), ('2026-11-03T00:00:00Z','2026-11-02T00:00:00Z'), ('2026-11-02T00:00:00Z','2026-11-03T00:00:01Z'), ('not-a-date','2026-11-03T00:00:00Z'), ('2026-11-02T25:00:00Z','2026-11-03T00:00:00Z')])
def test_windows(name, start, end):
    result = local(name, {**arguments(name), 'since': start, 'until': end, 'trace_id': 'PRIVATE_MARKER'})
    assert result.is_error
    assert 'PRIVATE_MARKER' not in result.model_dump_json()


def test_restart_modes_and_ownership():
    args = arguments('restart_service')
    proposed = check('restart_service', local('restart_service', args))
    action = proposed['data']['action_id']
    status = {k: v for k, v in args.items() if k not in {'unit','reason','evidence_ids'}}
    status.update(mode='status', action_id=action)
    assert not local('restart_service', status).is_error
    assert local('restart_service', {**status, 'session_id': 'other'}).is_error
    assert local('restart_service', {**args, 'action_id': action}).is_error
    for key in ['unit','reason','evidence_ids']:
        bad = dict(args); bad.pop(key)
        assert local('restart_service', bad).is_error
    for change in [{'reason':'x'*513}, {'evidence_ids':['x']*33}, {'evidence_ids':['x'*129]}, {'evidence_ids':[]}]:
        assert local('restart_service', {**args, **change}).is_error


@pytest.mark.parametrize('state', server.RESTART_STATES)
def test_restart_synthetic_states(state):
    args = {**arguments('restart_service'), 'mode':'status', 'action_id':'mock-fixture-'+state}
    result = check('restart_service', local('restart_service', args))
    assert result['data']['proposal_status'] == state
    assert result['status'] == 'ok'
    assert isinstance(result['data']['executed'], bool)
    if state in {'denied','expired','pending_approval','approved'}:
        assert result['data']['executed'] is False


def test_generation_and_status_invariants():
    subprocess.run([sys.executable, str(ROOT/'generate_result_schemas.py'), '--check'], check=True)
    for name, record in server.TOOLS.items():
        response = check(name, local(name, arguments(name)))
        bad = copy.deepcopy(response); bad.update(truncated=True, status='ok', error_code=None)
        assert not record[4].is_valid(bad)
        if name != 'restart_service':
            bad = copy.deepcopy(response); bad.update(status='approval_required', error_code='APPROVAL_REQUIRED')
            assert not record[4].is_valid(bad)


def test_published_examples_and_bounded_optional_end(monkeypatch):
    for example in json.loads((ROOT/'docs/examples/review-r2.json').read_text(encoding='utf-8')):
        name = example['tool']
        server.TOOLS[name][3].validate(example['input'])
        server.TOOLS[name][4].validate(example['structuredContent'])
        assert example['isError'] == (example['structuredContent']['status'] == 'error')
    record = server.TOOLS['verify_fix']; observed = []
    def capture(**kwargs):
        observed.append(kwargs['until'])
        return record[0](**kwargs)
    monkeypatch.setitem(server.TOOLS, 'verify_fix', (capture, *record[1:]))
    args = arguments('verify_fix'); args.pop('until')
    assert not local('verify_fix', args).is_error
    assert observed == ['2026-11-03T00:00:00Z']


def test_stdio_examples_and_privacy(capfd):
    async def run():
        async with Client(StdioServerParameters(command=sys.executable, args=[str(ROOT/'mock_mcp_server.py')])) as client:
            for name in ['memory_oom_events', 'failed_logins']:
                result = check(name, await client.call_tool(name, arguments(name)))
                assert len(result['data']['events']) == 2
                empty = check(name, await client.call_tool(name, {**arguments(name), 'since':'2026-11-03T00:00:00Z', 'until':'2026-11-04T00:00:00Z'}))
                assert empty['data']['events'] == []
                rejected = await client.call_tool(name, {**arguments(name), 'cursor':'PRIVATE_R2_MARKER', 'trace_id':'PRIVATE_R2_MARKER'})
                assert rejected.is_error and 'PRIVATE_R2_MARKER' not in rejected.model_dump_json()
            args = {**arguments('restart_service'), 'mode':'status', 'action_id':'mock-fixture-denied'}
            result = check('restart_service', await client.call_tool('restart_service', args))
            assert result['data']['proposal_status'] == 'denied' and not result['data']['executed']
    asyncio.run(run())
    output = capfd.readouterr()
    assert 'PRIVATE_R2_MARKER' not in output.out + output.err


@pytest.mark.parametrize('profile,status,code', [('coverage-denied','error','PERMISSION_DENIED'), ('coverage-partial','partial',None), ('coverage-unavailable','error','UNSUPPORTED')])
def test_stdio_coverage(profile, status, code, capfd):
    async def run():
        async with Client(StdioServerParameters(command=sys.executable, args=[str(ROOT/'tests/mcp_smoke/adversarial_server.py'), profile])) as client:
            for name in ['memory_oom_events','failed_logins']:
                args = {**arguments(name), 'since':'2026-11-03T00:00:00Z', 'until':'2026-11-04T00:00:00Z'}
                result = check(name, await client.call_tool(name, args))
                assert result['status'] == status and result['error_code'] == code
                assert result['data']['coverage']
    asyncio.run(run())
    assert 'Traceback' not in capfd.readouterr().err
