import asyncio
import copy
import importlib.util
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from mcp import Client, StdioServerParameters
from mcp.types import CallToolRequestParams

import mock_mcp_server as server

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('official_fixtures', ROOT/'tests/mcp_smoke/test_all_tools_stdio.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
CASES = fixtures.REQUESTS
SENTINEL = 'SYNTHETIC_SECRET_DO_NOT_REFLECT_7d19'


def arguments(name):
    return {**CASES[name], 'trace_id': 'trace-audit', 'logical_tool_call_id': 'call-audit', 'attempt_id': 'attempt-audit'}


def validator(name):
    return server.TOOLS[name][4]


def check(name, result):
    data = result.structured_content
    assert data is not None
    validator(name).validate(data)
    assert result.is_error == (data['status'] == 'error')
    assert json.loads(result.content[0].text) == data
    assert server.wire_size(result) <= server.MAX_RESPONSE_BYTES
    return data


def local(name, args):
    return asyncio.run(server.call_tool(SimpleNamespace(request_id=1), CallToolRequestParams(name=name, arguments=args)))


@pytest.mark.parametrize('name', CASES)
def test_runtime_raw_contract_matrix(name):
    args = arguments(name)
    # Required-only is distinct from null, and runtime validates raw JSON types.
    schema = server.TOOLS[name][1]
    minimum = {k: v for k, v in args.items() if k in schema['required']}
    assert not check(name, local(name, minimum))['status'] == 'error'
    for field in schema['required']:
        bad = dict(minimum); bad.pop(field)
        assert check(name, local(name, bad))['status'] == 'error'
    for field in schema['properties']:
        bad = {**minimum, field: {SENTINEL: SENTINEL}}
        result = local(name, bad)
        assert check(name, result)['status'] == 'error'
        assert SENTINEL not in result.model_dump_json()
        if not server.TOOLS[name][3].is_valid({**minimum, field: None}):
            assert local(name, {**minimum, field: None}).is_error
    assert local(name, {**minimum, SENTINEL: SENTINEL}).is_error


@pytest.mark.parametrize('name', CASES)
def test_output_validator_catches_corruption(name, monkeypatch):
    record = server.TOOLS[name]
    bad_data = {'message': 'Invalid success'} if name == 'restart_service' else {'unexpected': True}
    monkeypatch.setitem(server.TOOLS, name, (lambda **kw: server.make_response(name, 't','l','a', bad_data, 's'), *record[1:]))
    data = check(name, local(name, arguments(name)))
    assert data['error_code'] == 'EXECUTION_UNKNOWN'
    assert data['data']['rule'] == 'output_validation'


def test_service_provenance_permission_and_unique_ids(monkeypatch):
    a = check('service_status', local('service_status', arguments('service_status')))
    b = check('service_status', local('service_status', arguments('service_status')))
    ev = a['evidence'][0]
    assert ev['source'] == 'synthetic.mock.service_status'
    assert 'ActiveState='+a['data']['active_state'] in ev['excerpt']
    assert ev['evidence_id'] != b['evidence'][0]['evidence_id']
    record = server.TOOLS['service_status']
    def denied(**kw): raise PermissionError(SENTINEL)
    monkeypatch.setitem(server.TOOLS, 'service_status', (denied, *record[1:]))
    result = local('service_status', arguments('service_status'))
    assert check('service_status', result)['error_code'] == 'PERMISSION_DENIED'
    assert SENTINEL not in result.model_dump_json()


@pytest.mark.parametrize('since,until,count', [
    ('2026-11-01T00:00:00Z','2026-11-02T00:00:00Z',0),
    ('2026-11-02T00:00:00Z','2026-11-03T00:00:00Z',4),
    ('2026-11-03T00:00:00Z','2026-11-04T00:00:00Z',0),
])
def test_historical_half_open_window(since, until, count):
    data=check('detect_anomaly',local('detect_anomaly',{**arguments('detect_anomaly'),'since':since,'until':until}))
    assert len(data['data']['anomalies']) == count
    evidence={ev['evidence_id']:ev for ev in data['evidence']}
    for finding in data['data']['anomalies']:
        for ref in finding['evidence_refs']:
            ev=evidence[ref]
            assert since <= ev['observed_at'] < until
            assert ev['observed_at'] != data['completed_at']


def test_missing_event_timestamp_and_timezone(monkeypatch):
    monkeypatch.setattr(server,'ANOMALY_EVENT_AT',None)
    data=check('detect_anomaly',local('detect_anomaly',arguments('detect_anomaly')))
    assert data['status']=='partial' and data['evidence']==[]
    assert data['data']['insufficient_signals']
    assert local('detect_anomaly',{**arguments('detect_anomaly'),'since':'2026-11-01T07:00:00+07:00'}).is_error
    # Other event queries accept offsets as canonical input allows them.
    data=check('query_journal',local('query_journal',{**arguments('query_journal'),'since':'2026-11-02T16:30:00+07:00','until':'2026-11-02T16:35:00+07:00'}))
    assert len(data['data']['entries'])==1


def test_expired_modified_cross_tool_cursor():
    args={**arguments('list_failed_units'),'limit':1}
    first=check('list_failed_units',local('list_failed_units',args)); token=first['next_cursor']
    second=check('list_failed_units',local('list_failed_units',{**args,'cursor':token}))
    assert first['data']['failed_units'] != second['data']['failed_units']
    for changes in [{'cursor':token+'x'},{'cursor':token,'session_id':'other'},{'cursor':token,'limit':2}]:
        assert local('list_failed_units',{**args,**changes}).is_error
    assert local('query_journal',{**arguments('query_journal'),'cursor':token}).is_error
    server.CURSOR_STORE[token]['expires_at']=datetime.now(timezone.utc)-timedelta(seconds=1)
    assert check('list_failed_units',local('list_failed_units',{**args,'cursor':token}))['error_code']=='EXPIRED'
    assert server.validate_cursor('not-a-cursor','x','s',{})==(False,'INVALID_INPUT')


def test_cursor_cache_budget(monkeypatch):
    monkeypatch.setattr(server,'CURSOR_STORE',{})
    monkeypatch.setattr(server,'MAX_CURSOR_STATES',1)
    server.create_cursor('x','s',{},1)
    with pytest.raises(ValueError, match='budget'): server.create_cursor('x','s',{},2)
    with pytest.raises(ValueError): server.create_cursor('x','s',{},201)


@pytest.mark.parametrize('payload', ['A'*70000, 'Thủy漢🙂'*20000])
def test_cap_includes_native_envelope_and_nested_evidence(payload):
    response=server.service_status(**arguments('service_status'))
    response['evidence'][0]['excerpt']=payload
    record=server.TOOLS['service_status']
    original=record[0]
    try:
        server.TOOLS['service_status']=(lambda **kw: copy.deepcopy(response),*record[1:])
        result=local('service_status',arguments('service_status'))
        data=check('service_status',result)
        assert data['error_code']=='OUTPUT_LIMIT' and result.is_error
    finally:
        server.TOOLS['service_status']=(original,*record[1:])


def test_all_schema_syntax_and_result_composition():
    for path in (ROOT/'schemas').rglob('*.schema.json'):
        Draft202012Validator.check_schema(json.loads(path.read_text()))
    common=json.loads((ROOT/'schemas/common-response.schema.json').read_text())
    error=json.loads((ROOT/'schemas/error-data.schema.json').read_text());error.pop('$schema')
    request_common=json.loads((ROOT/'schemas/common-request.schema.json').read_text())
    manifest=json.loads((ROOT/'schemas/manifest.json').read_text())
    assert {entry['name'] for entry in manifest['tools']}==set(CASES)
    for name in CASES:
        assert all(server.TOOLS[name][1]['properties'][key]==value for key,value in request_common['properties'].items())
        result=server.TOOLS[name][2]
        assert result['properties']=={**common['properties'],'tool':{'const':name}}
        assert result['allOf'][-1]['then']['properties']['data']==error
        assert result['allOf'][-1]['else']['properties']['data']==json.loads((ROOT/f'schemas/tools/{name}.response.schema.json').read_text())


async def sdk_matrix():
    params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'mock_mcp_server.py')])
    async with Client(params) as client:
        discovery=await client.list_tools()
        assert {tool.name for tool in discovery.tools} == set(CASES)
        for tool in discovery.tools:
            assert tool.input_schema==server.TOOLS[tool.name][1]
            assert tool.output_schema==server.TOOLS[tool.name][2]
        calls=0
        for name in CASES:
            schema=server.TOOLS[name][1];args=arguments(name)
            minimum={k:v for k,v in args.items() if k in schema['required']}
            assert check(name,await client.call_tool(name,minimum))['status']!='error';calls+=1
            for field in schema['required']:
                bad=dict(minimum);bad.pop(field)
                assert check(name,await client.call_tool(name,bad))['status']=='error';calls+=1
            for field in schema['properties']:
                bad={**minimum,field:{SENTINEL:SENTINEL}}
                result=await client.call_tool(name,bad)
                assert check(name,result)['status']=='error'
                assert SENTINEL not in result.model_dump_json();calls+=1
                null={**minimum,field:None}
                if not server.TOOLS[name][3].is_valid(null):
                    assert check(name,await client.call_tool(name,null))['status']=='error';calls+=1
            for extra in [{SENTINEL:SENTINEL},{'approved':True},{'schema_version':'9.9.9'}]:
                result=await client.call_tool(name,{**minimum,**extra})
                assert check(name,result)['status']=='error';assert SENTINEL not in result.model_dump_json();calls+=1
            if 'unit' in schema['properties']:
                assert (await client.call_tool(name,{**minimum,'unit':'../../secret.service'})).is_error;calls+=1
        for name,key in [('query_journal','entries'),('search_log','matches'),('list_failed_units','failed_units')]:
            args={**arguments(name),'limit':1};pages=[];cursor=None
            for i in range(4):
                result=check(name,await client.call_tool(name,{**args,**({'cursor':cursor} if cursor else {})}))
                pages+=result['data'][key];cursor=result['next_cursor']
                if cursor is None: break
            assert len(pages)==3 and len({json.dumps(p,sort_keys=True) for p in pages})==3 and cursor is None
            first=check(name,await client.call_tool(name,args));token=first['next_cursor']
            if name in {'query_journal','search_log'}:
                equivalent={**args,'cursor':token,'since':'2026-11-01T07:00:00+07:00','until':'2026-11-05T07:00:00+07:00'}
                assert not (await client.call_tool(name,equivalent)).is_error
            async with Client(params) as other_client:
                assert (await other_client.call_tool(name,{**args,'cursor':token})).is_error
            for changed in [{'cursor':token+'x'},{'cursor':token,'session_id':'cross'},{'cursor':token,'limit':2}]:
                assert (await client.call_tool(name,{**args,**changed})).is_error
        print(f'Official SDK stdio: {calls} contract cases plus pagination passed')


def test_official_sdk_stdio_contract_matrix(capfd):
    asyncio.run(sdk_matrix())
    captured=capfd.readouterr()
    assert SENTINEL not in captured.out+captured.err

@pytest.mark.parametrize('mode', ['huge','unicode','nested','near','expired','slow','disconnect','partial'])
def test_sdk_faults_and_cleanup(mode, monkeypatch):
    import mcp.client.stdio as transport
    processes=[];frames=[]
    spawn=transport._create_platform_compatible_process
    parse=transport._parse_line
    async def tracked_spawn(*args,**kwargs):
        process=await spawn(*args,**kwargs);processes.append(process);return process
    def tracked_parse(line):
        parsed=json.loads(line)
        if 'structuredContent' in parsed.get('result',{}): frames.append(len((line+'\n').encode('utf-8')))
        return parse(line)
    monkeypatch.setattr(transport,'_create_platform_compatible_process',tracked_spawn)
    monkeypatch.setattr(transport,'_parse_line',tracked_parse)
    async def exercise():
        params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'tests/mcp_smoke/adversarial_server.py'),mode])
        async with Client(params,read_timeout_seconds=3) as client:
            if mode=='partial':
                data=check('query_journal',await client.call_tool('query_journal',arguments('query_journal')))
                assert data['status']=='partial' and data['error_code']=='OUTPUT_LIMIT'
                assert 0 < data['data']['count'] < 100
                assert all(entry['message']=='whole-record-'+'漢'*300 for entry in data['data']['entries'])
            elif mode=='expired':
                args={**arguments('list_failed_units'),'limit':1}
                page=check('list_failed_units',await client.call_tool('list_failed_units',args))
                await asyncio.sleep(0.05)
                error=check('list_failed_units',await client.call_tool('list_failed_units',{**args,'cursor':page['next_cursor']}))
                assert error['error_code']=='EXPIRED'
            elif mode=='slow':
                with pytest.raises(Exception):
                    await client.call_tool('service_status',arguments('service_status'),read_timeout_seconds=0.05)
                # A timeout cancels only this call; connection remains usable.
                assert len((await client.list_tools()).tools)==12
                task=asyncio.create_task(client.call_tool('service_status',arguments('service_status')))
                await asyncio.sleep(0.05);task.cancel()
                with pytest.raises(asyncio.CancelledError): await task
                assert len((await client.list_tools()).tools)==12
            elif mode=='disconnect':
                with pytest.raises(Exception): await client.call_tool('service_status',arguments('service_status'))
            else:
                result=await client.call_tool('service_status',arguments('service_status'))
                data=check('service_status',result)
                assert data['status']==('ok' if mode=='near' else 'error')
                if mode=='near': assert server.wire_size(result)>65500
                else: assert data['error_code']=='OUTPUT_LIMIT'
    asyncio.run(exercise())
    assert processes and all(p.returncode is not None for p in processes), 'SDK subprocess leaked'
    assert all(size <= 65536 for size in frames), frames


def test_sdk_optional_values_and_protocol_negatives():
    optional={
        'query_journal':{'priority':3,'limit':1},'search_log':{'limit':1},
        'list_failed_units':{'limit':1},'disk_usage':{'filesystem':'/dev/mock','include_inodes':False},
        'listening_ports':{'port':8080,'protocol':'udp','unit':'nginx.service'},
        'check_config':{'unit':'sshd.service'},'memory_oom_events':{'limit':1},'failed_logins':{'limit':1},
        'detect_anomaly':{'unit':'nginx.service','signals':['oom_events'],'max_findings':1},
        'verify_fix':{'until':None,'action_id':None,'baseline_evidence_refs':None},
        'restart_service':{'evidence_ids':['ev-001'],'action_id':'mock-proposal-001'},'service_status':{},
    }
    async def exercise():
        async with Client(StdioServerParameters(command=sys.executable,args=[str(ROOT/'mock_mcp_server.py')])) as client:
            for name,values in optional.items():
                assert not (await client.call_tool(name,{**arguments(name),**values})).is_error
            status=check('restart_service',await client.call_tool('restart_service',{**arguments('restart_service'),'mode':'status','action_id':'mock-proposal-001'}))
            assert status['data']['executed'] is False
            with pytest.raises(Exception): await client.call_tool('unknown_tool',{})
            # Invalid discovery cursor is separate from business pagination.
            with pytest.raises(Exception): await client.list_tools(cursor='invalid-discovery')
    asyncio.run(exercise())

@pytest.mark.parametrize('name', CASES)
def test_all_tools_bounded_error_schema(name, monkeypatch):
    record=server.TOOLS[name]
    def oversized(**kwargs):
        return server.make_response(name,'t','l','a',{'message':'🙂'*70000},'s')
    monkeypatch.setitem(server.TOOLS,name,(oversized,*record[1:]))
    data=check(name,local(name,arguments(name)))
    assert data['status']=='error' and data['error_code']=='OUTPUT_LIMIT'


def test_raw_protocol_error_does_not_reflect_invalid_arguments():
    import subprocess
    import queue
    import threading
    output=[];errors=[];inbox=queue.Queue()
    process=subprocess.Popen([sys.executable,str(ROOT/'mock_mcp_server.py')],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    def read_stdout():
        for line in process.stdout:
            output.append(line);inbox.put(json.loads(line))
    def read_stderr(): errors.append(process.stderr.read())
    thread=threading.Thread(target=read_stdout,daemon=True);thread.start()
    err_thread=threading.Thread(target=read_stderr,daemon=True);err_thread.start()
    def send(message): process.stdin.write(json.dumps(message)+'\n');process.stdin.flush()
    try:
        send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'audit','version':'1'}}})
        assert inbox.get(timeout=15)['id']==1
        send({'jsonrpc':'2.0','method':'notifications/initialized'})
        send({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'service_status','arguments':SENTINEL}})
        reply=inbox.get(timeout=15)
        assert reply['error']['code']==-32602
        assert SENTINEL not in json.dumps(reply)
        send({'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'service_status','arguments':{**arguments('service_status'),SENTINEL:SENTINEL}}})
        reply=inbox.get(timeout=15)
        assert reply['result']['isError'] is True
    finally:
        process.stdin.close()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill();process.wait(timeout=3)
        thread.join(timeout=2);err_thread.join(timeout=2)
    assert SENTINEL not in ''.join(output+errors)
    assert process.returncode is not None
