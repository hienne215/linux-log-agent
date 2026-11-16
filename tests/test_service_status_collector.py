import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest
from mcp import Client, StdioServerParameters
import mock_mcp_server as contracts
from src.mcp_server import service_status as svc

ROOT = Path(__file__).resolve().parents[1]
ARGS = dict(schema_version='1.0.0',session_id='service-test',trace_id='trace',
            logical_tool_call_id='logical',attempt_id='attempt',unit='nginx.service')
MARKER = 'SENSITIVE_VALUE_DO_NOT_FORWARD'

def output(active='active', sub='running', load='loaded', unit='nginx.service'):
    return f'Id={unit}\nLoadState={load}\nActiveState={active}\nSubState={sub}\n'.encode()


def validate(response):
    contracts.TOOLS['service_status'][4].validate(response)
    assert MARKER not in json.dumps(response)
    assert contracts.wire_size(contracts.native_result(response)) <= 65536
    return response


@pytest.fixture
def executable(monkeypatch):
    monkeypatch.setattr(svc.Path, 'is_file', lambda path: path.as_posix() in svc.EXECUTABLES)
    monkeypatch.setattr(svc.os, 'access', lambda path, mode: True)


@pytest.mark.parametrize('active,sub,load', [('active','running','loaded'),('inactive','dead','loaded'),
    ('failed','failed','loaded'),('inactive','dead','masked')])
def test_states_are_observed_not_synthetic(active,sub,load,executable):
    calls=[]
    async def execute(argv):
        calls.append(argv)
        return 0,output(active,sub,load),b''
    value=validate(asyncio.run(svc.collect(ARGS,execute=execute,platform_name='linux')))
    assert value['status']=='ok'
    assert value['data']==dict(unit='nginx.service',active_state=active,sub_state=sub,load_state=load,description='[REDACTED]')
    assert calls==[('/usr/bin/systemctl','--no-pager','--no-ask-password','show',
                    '--property=Id,LoadState,ActiveState,SubState','--','nginx.service')]
    evidence=value['evidence'][0]
    assert evidence['source']==svc.SOURCE and evidence['unit']=='nginx.service'
    assert 'synthetic' not in evidence['source'] and evidence['redacted'] and not evidence['truncated']
    assert f'active={active}' in evidence['excerpt']
    assert value['started_at'] <= evidence['observed_at'] <= value['completed_at']
    for key in ['session_id','trace_id','logical_tool_call_id','attempt_id']:assert value[key]==ARGS[key]


@pytest.mark.parametrize('unit,code', [('sshd.service','POLICY_DENIED'),('nginx.service;id','INVALID_INPUT'),
    ('../nginx.service','INVALID_INPUT'),('--system.service','INVALID_INPUT'),('evil@x.service','INVALID_INPUT')])
def test_disallowed_or_malformed_unit_never_executes(unit,code):
    execute=AsyncMock(side_effect=AssertionError('must not execute'))
    value=validate(asyncio.run(svc.collect({**ARGS,'unit':unit},execute=execute,platform_name='linux')))
    assert value['error_code']==code and value['evidence']==[]
    execute.assert_not_awaited()


@pytest.mark.parametrize('rc', [0,4])
def test_nonexistent_allowlisted_service(rc,executable):
    execute=AsyncMock(return_value=(rc,output('inactive','dead','not-found'),b''))
    value=validate(asyncio.run(svc.collect(ARGS,execute=execute,platform_name='linux')))
    assert value['error_code']=='NOT_FOUND' and value['evidence']==[]


@pytest.mark.parametrize('rc,raw,err,code', [
    (1,b'',b'Permission denied '+MARKER.encode(),'PERMISSION_DENIED'),
    (1,b'',b'Interactive authentication required '+MARKER.encode(),'PERMISSION_DENIED'),
    (1,b'',b'Failed to connect to bus '+MARKER.encode(),'UNSUPPORTED'),
    (1,output(),b'failure','UNSUPPORTED'),
    (0,b'garbage '+MARKER.encode(),b'','EXECUTION_UNKNOWN'),
    (0,output(unit='alias.service'),b'','EXECUTION_UNKNOWN'),
    (0,output(active=MARKER),b'','EXECUTION_UNKNOWN'),
    (0,output()+b'Id=nginx.service\n',b'','EXECUTION_UNKNOWN'),
    (0,output()+b'Description='+MARKER.encode(),b'','EXECUTION_UNKNOWN'),
    (0,b'x'*8193,b'','OUTPUT_LIMIT'),(1,b'',b'x'*4097,'OUTPUT_LIMIT')])
def test_safe_errors(rc,raw,err,code,executable,capfd):
    value=validate(asyncio.run(svc.collect({**ARGS,'trace_id':MARKER},
        execute=AsyncMock(return_value=(rc,raw,err)),platform_name='linux')))
    assert value['error_code']==code and value['status']=='error'
    assert value['trace_id']=='invalid-input' and value['session_id'] is None and not value['truncated']
    captured=capfd.readouterr();assert MARKER not in captured.out+captured.err


@pytest.mark.parametrize('error,code', [(PermissionError(MARKER),'PERMISSION_DENIED'),
    (FileNotFoundError(MARKER),'UNSUPPORTED'),(svc.CollectionFailure('TIMEOUT'),'TIMEOUT'),
    (RuntimeError(MARKER),'EXECUTION_UNKNOWN')])
def test_safe_exceptions(error,code,executable):
    value=validate(asyncio.run(svc.collect(ARGS,execute=AsyncMock(side_effect=error),platform_name='linux')))
    assert value['error_code']==code


def test_non_linux_and_missing_executable_fail_closed(monkeypatch):
    execute=AsyncMock(side_effect=AssertionError('must not execute'))
    assert asyncio.run(svc.collect(ARGS,execute=execute,platform_name='win32'))['error_code']=='UNSUPPORTED'
    monkeypatch.setattr(svc.Path,'is_file',lambda p:False)
    assert asyncio.run(svc.collect(ARGS,execute=execute,platform_name='linux'))['error_code']=='UNSUPPORTED'
    execute.assert_not_awaited()


@pytest.mark.parametrize('fault', ['ok','timeout','stdout','stderr','cancel','cancel_spawn'])
def test_subprocess_bounds_cleanup_and_environment(fault,monkeypatch):
    async def exercise():
        class Process:
            def __init__(self):
                self.stdout=asyncio.StreamReader();self.stderr=asyncio.StreamReader()
                self.returncode=None;self.killed=False;self.done=asyncio.Event()
                if fault=='ok':
                    self.stdout.feed_data(output());self.returncode=0;self.done.set()
                    self.stdout.feed_eof();self.stderr.feed_eof()
                if fault=='stdout':self.stdout.feed_data(b'x'*8193)
                if fault=='stderr':self.stderr.feed_data(b'x'*4097)
            async def wait(self):
                await self.done.wait();return self.returncode
            def kill(self):
                self.killed=True;self.returncode=-9;self.done.set()
                self.stdout.feed_eof();self.stderr.feed_eof()
        process=Process();seen={}
        started=asyncio.Event();release=asyncio.Event()
        async def spawn(*argv,**kwargs):
            seen.update(kwargs);started.set()
            if fault=='cancel_spawn':await release.wait()
            return process
        monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
        monkeypatch.setattr(svc,'TIMEOUT',.02)
        task=asyncio.create_task(svc.run(('/usr/bin/systemctl','show')))
        if fault=='cancel_spawn':
            await started.wait();task.cancel();await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):await task
        elif fault=='cancel':
            await asyncio.sleep(0);task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
        elif fault=='ok':
            assert (await task)[1]==output()
        else:
            with pytest.raises(svc.CollectionFailure) as error:await task
            assert error.value.code==('TIMEOUT' if fault=='timeout' else 'OUTPUT_LIMIT')
        assert process.returncode is not None and process.stdout.at_eof() and process.stderr.at_eof()
        assert process.killed is (fault!='ok')
        assert seen['stdin']==asyncio.subprocess.DEVNULL and 'shell' not in seen
        assert seen['cwd']=='/' and set(seen['env'])=={'LC_ALL','LANG','SYSTEMD_COLORS','SYSTEMD_PAGER'}
    asyncio.run(exercise())


def test_official_sdk_stdio_simulated_linux_backend(capfd):
    async def exercise():
        params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'tests/service_status_stdio_fixture.py')])
        async with Client(params) as client:
            assert len((await client.list_tools()).tools)==12
            native=await client.call_tool('service_status',ARGS)
            value=validate(native.structured_content)
            assert not native.is_error and value['data']['active_state']=='active'
            assert json.loads(native.content[0].text)==value
            bad=await client.call_tool('service_status',{**ARGS,'unit':'sshd.service'})
            assert bad.is_error and bad.structured_content['error_code']=='POLICY_DENIED'
    asyncio.run(exercise())
    assert MARKER not in capfd.readouterr().err


def test_real_backend_is_opt_in_and_fixture_mode_is_exclusive():
    async def exercise():
        for args in [[],['--linux-service-status']]:
            async with Client(StdioServerParameters(command=sys.executable,args=[str(ROOT/'run_mcp_server.py'),*args])) as client:
                value=validate((await client.call_tool('service_status',ARGS)).structured_content)
                assert value['error_code']=='UNSUPPORTED'  # Host is Windows; not Linux smoke.
                assert value['evidence']==[]
    if sys.platform!='linux':asyncio.run(exercise())
    import subprocess
    result=subprocess.run([sys.executable,str(ROOT/'run_mcp_server.py'),'--fixtures','--linux-service-status'],capture_output=True)
    assert result.returncode==2
