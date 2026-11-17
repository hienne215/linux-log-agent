"""P03-03 own binding checks, synthetic sources only; never log in to a host."""
import asyncio
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import Client, StdioServerParameters
from mcp.types import CallToolRequestParams

import mock_mcp_server as contracts
from test_p03_03_failed_logins import PRIVACY, REQ, snapshot
from test_p03_03_owner_policy import owner_fixture
from src.mcp_server.failed_login_events import Privacy
from src.mcp_server.failed_logins_owner import FailedLoginsOwner
from src.mcp_server.failed_logins_server import FailedLoginsServer, KeyEpochPolicy
from src.mcp_server import failed_logins_server as binding

ROOT=Path(__file__).resolve().parents[1]
PRINCIPAL=('peer-uid',129)


def call(app,req):
    return app.call_tool(SimpleNamespace(request_id=1),CallToolRequestParams(name='failed_logins',arguments=req))


def test_binding_preserves_persistent_owner_and_other_tools_fail_closed():
    async def exercise():
        owner,reads=owner_fixture()
        app=FailedLoginsServer(owner,PRINCIPAL)
        first=(await call(app,{**REQ,'limit':1})).structured_content
        second=(await call(app,{**REQ,'limit':1,'cursor':first['next_cursor']})).structured_content
        assert len(reads)==1 and second['data']['events']!=first['data']['events']
        other=await app.call_tool(SimpleNamespace(request_id=2),CallToolRequestParams(
            name='service_status',arguments={**{k:v for k,v in REQ.items() if k not in ('since','until')},'unit':'nginx.service'}))
        assert other.structured_content['error_code']=='UNSUPPORTED'
        assert await app.close() and app.pending is None
        assert not owner._adapter._states
    asyncio.run(exercise())


def test_schema_rejection_never_runs_access_check_or_source():
    def never(): pytest.fail('Invalid input reached trusted access callback')
    owner,_=owner_fixture(never)
    async def exercise():
        app=FailedLoginsServer(owner,PRINCIPAL)
        result=(await call(app,{**REQ,'path':'PRIVATE'})).structured_content
        assert result['error_code']=='INVALID_INPUT' and 'PRIVATE' not in str(result)
        await app.close()
    asyncio.run(exercise())


def test_wrong_configured_principal_is_not_adopted_from_request():
    owner,reads=owner_fixture()
    async def exercise():
        app=FailedLoginsServer(owner,('peer-uid',130))
        result=(await call(app,REQ)).structured_content
        assert result['error_code']=='INVALID_INPUT' and not reads
        await app.close()
    asyncio.run(exercise())


def test_cancellation_retains_acquisition_and_waits_before_cleanup():
    async def exercise():
        entered,release=threading.Event(),threading.Event()
        def source(*_):
            entered.set()
            release.wait(5)
            return snapshot()
        owner=FailedLoginsOwner(PRINCIPAL,'session-test',PRIVACY,lambda _:source,access_check=lambda:True)
        app=FailedLoginsServer(owner,PRINCIPAL)
        task=asyncio.create_task(call(app,{**REQ,'limit':1}))
        try:
            assert await asyncio.to_thread(entered.wait,2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            assert app.closing and app.pending is not None and not app.pending.done()
            assert (await call(app,REQ)).structured_content['error_code']=='POLICY_DENIED'
            cleanup=asyncio.create_task(app.close())
            await asyncio.sleep(0)
            assert not cleanup.done()
            release.set()
            assert await asyncio.wait_for(cleanup,5)
            assert app.pending is None and not owner._adapter._states
        finally:
            release.set()
            await app.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('change',['key','missing','uid'])
def test_key_or_identity_revocation_latches_until_restart(monkeypatch,change):
    current=[PRIVACY]
    uid=[129]
    def load(*_):
        if current[0] is None: raise FileNotFoundError('PRIVATE')
        return current[0]
    monkeypatch.setattr(binding,'load_privacy',load)
    monkeypatch.setattr(binding,'os',SimpleNamespace(geteuid=lambda:uid[0]))
    policy=KeyEpochPolicy(129,135,'synthetic-eval',PRIVACY)
    assert policy()
    if change=='key': current[0]=Privacy(b'new-synthetic-test-key-material!!','synthetic-eval')
    if change=='missing': current[0]=None
    if change=='uid': uid[0]=130
    assert not policy()
    current[0],uid[0]=PRIVACY,129
    assert not policy() and policy.revoked


def test_default_cli_does_not_enable_source_or_read_key(monkeypatch):
    import run_failed_logins_server as entry
    monkeypatch.setattr(binding,'create_live_server',lambda *_:pytest.fail('Default must not read key'))
    args=entry.build_parser().parse_args([])
    app=entry.build_server(args)
    assert not isinstance(app,FailedLoginsServer) and app.backends=={}
    with pytest.raises(ValueError):
        entry.build_server(entry.build_parser().parse_args(['--linux-failed-logins']))


def test_cli_rejects_arbitrary_key_path_without_echo(capsys):
    import run_failed_logins_server as entry
    with pytest.raises(SystemExit):
        entry.build_parser().parse_args(['--key','PRIVATE'])
    assert 'PRIVATE' not in capsys.readouterr().err


def test_official_sdk_paging_redaction_and_native_consistency():
    async def exercise():
        params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'tests/p03_03_stdio_server.py')])
        async with Client(params,read_timeout_seconds=30) as client:
            assert len((await client.list_tools()).tools)==12
            req={**REQ,'limit':200}
            ids=[]
            while True:
                result=await client.call_tool('failed_logins',req)
                data=result.structured_content
                contracts.TOOLS['failed_logins'][4].validate(data)
                assert not result.is_error and json.loads(result.content[0].text)==data
                assert data['data']['count']<=200
                for event in data['data']['events']:
                    assert event['user']=='[REDACTED]' and event['source_ip'].startswith('ip-')
                    ids.append(event['evidence_id'])
                assert '192.0.2.10' not in json.dumps(data)
                if data['next_cursor'] is None: break
                req={**req,'cursor':data['next_cursor']}
            assert len(ids)==len(set(ids))==450
            bad=await client.call_tool('failed_logins',{**REQ,'cursor':'forged'})
            assert bad.is_error and bad.structured_content['error_code']=='INVALID_INPUT'
    asyncio.run(exercise())
