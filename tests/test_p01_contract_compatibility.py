"""Offline audit of scaffold 656f8c3 with canonical schemas 2502d44.

Requires the documented isolated contract overlay; no production/Linux reader.
"""
import asyncio
import json
import sys
from types import SimpleNamespace

import pytest
from mcp import Client, StdioServerParameters
from mcp.types import CallToolRequestParams

import mock_mcp_server as contracts
from src.mcp_server.runner import WorkerRunner
from src.mcp_server.server import ServerScaffold
from test_contract_audit import arguments
from test_p01_scaffold import ARGS, ROOT, SECRET, argv, validate

UNIT_TOOLS = ('query_journal', 'service_status', 'listening_ports', 'check_config',
              'detect_anomaly', 'verify_fix', 'restart_service')
INVALID = [(name, {'unit': 'nginx.service\n'}) for name in UNIT_TOOLS] + [
    ('search_log', {'source_id': value}) for value in
    ('/var/log/auth.log', '..', 'synthetic.fixture.only', '-option', 'auth\n')
] + [('disk_usage', {'filesystem': value}) for value in
     ('/mnt/../private', '../root', '--help', '/root\n', '/mnt//data')] + [
    ('verify_fix', {'action_id': 'x' * 129}),
    ('verify_fix', {'baseline_evidence_refs': ['x' * 129]}),
    ('verify_fix', {'baseline_evidence_refs': [str(i) for i in range(33)]}),
    ('verify_fix', {'check_types': ['service_active'] * 4}),
    ('detect_anomaly', {'signals': ['oom_events'] * 5}),
]


@pytest.mark.parametrize('name,change', INVALID)
def test_new_schema_rejections_precede_worker_and_suppress_metadata(name, change):
    class NeverRun:
        async def run(self, *args):
            pytest.fail('invalid request reached worker')
    async def exercise():
        app = ServerScaffold(runner=NeverRun(), backends={name: argv('small')}
                             if name != 'restart_service' else {})
        result = await app.call_tool(SimpleNamespace(request_id=1), CallToolRequestParams(
            name=name, arguments={**arguments(name), **change, 'trace_id': SECRET}))
        response = validate(result, name)
        assert response['error_code'] == 'INVALID_INPUT'
        assert response['session_id'] is None and response['trace_id'] == 'invalid-input'
        assert response['evidence'] == []
    asyncio.run(exercise())


@pytest.mark.parametrize('name,change', [
    ('search_log', {'source_id': 'mock-source'}),
    ('search_log', {'source_id': 'journal_auth'}),
    ('disk_usage', {'filesystem': 'root'}),
    ('disk_usage', {'filesystem': '/'}),
    ('disk_usage', {'filesystem': '/dev/mock'}),
])
def test_valid_selector_syntax_does_not_enable_a_backend(name, change):
    async def exercise():
        result = await ServerScaffold().call_tool(SimpleNamespace(request_id=1),
            CallToolRequestParams(name=name, arguments={**arguments(name), **change}))
        assert validate(result, name)['error_code'] == 'UNSUPPORTED'
    asyncio.run(exercise())


@pytest.mark.parametrize('name,valid_output', [
    ('service_status', True), ('service_status', False),
    ('list_failed_units', True), ('list_failed_units', False),
    ('listening_ports', True), ('listening_ports', False),
    ('check_config', True), ('check_config', False),
])
def test_response_bounds_at_worker_boundary(name, valid_output):
    class FixtureRunner:
        async def run(self, profile, args):
            response = contracts.TOOLS[name][0](**args)
            data = response['data']
            if name == 'service_status':
                data['description'] = 'x' * (2048 if valid_output else 2049)
            elif name == 'list_failed_units':
                data['failed_units'][0]['unit'] = 'a' * 248 + '.socket'
                data['failed_units'][0]['description'] = 'x' * (2048 if valid_output else 2049)
            elif name == 'listening_ports':
                row = data['ports'][0]
                row.update(address=('ffff:' * 7 + 'ffff%' + 's' * 64) if valid_output else 'x' * 129,
                           process='', unit=None)
            else:
                data['issues'] = [dict(code='FIXTURE', severity='warning', message='Synthetic only.')
                                  for _ in range(32 if valid_output else 33)]
            return json.dumps(response).encode('utf-8')
    async def exercise():
        app = ServerScaffold(backends={name: argv('small')}, runner=FixtureRunner())
        result = await app.call_tool(SimpleNamespace(request_id=1),
            CallToolRequestParams(name=name, arguments=arguments(name)))
        response = validate(result, name)
        if valid_output:
            assert not result.is_error and response['trace_id'] == arguments(name)['trace_id']
        else:
            assert response['error_code'] == 'EXECUTION_UNKNOWN'
            assert response['session_id'] is None and response['evidence'] == []
    asyncio.run(exercise())


def test_maximum_external_baselines_still_need_no_observed_evidence():
    class FixtureRunner:
        async def run(self, profile, args):
            return json.dumps(contracts.verify_fix(**args)).encode('utf-8')
    async def exercise():
        refs = [f'{i:02}' + 'x' * 126 for i in range(32)]
        args = {**arguments('verify_fix'), 'baseline_evidence_refs': refs}
        app = ServerScaffold(backends={'verify_fix': argv('small')}, runner=FixtureRunner())
        response = validate(await app.call_tool(SimpleNamespace(request_id=1),
            CallToolRequestParams(name='verify_fix', arguments=args)), 'verify_fix')
        assert response['status'] == 'partial' and response['data']['baseline_evidence_refs'] == refs
        assert response['evidence'] == []
    asyncio.run(exercise())


def test_large_atomic_fixture_is_schema_valid_before_transport_bounding():
    async def exercise():
        runner = WorkerRunner()
        raw = await runner.run(argv('large_atomic'), ARGS)
        response = json.loads(raw.decode('utf-8'))
        contracts.TOOLS['service_status'][4].validate(response)
        assert len(raw) <= 65536
        assert contracts.wire_size(ServerScaffold().native(response), 1) > 65536
        assert runner.process is None and runner.spawn is None
    asyncio.run(exercise())


def test_new_input_rejections_and_discovery_over_official_sdk_stdio(capfd):
    async def exercise():
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / 'run_mcp_server.py')])
        async with Client(params, read_timeout_seconds=15) as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 12
            for tool in tools:
                assert tool.input_schema == contracts.TOOLS[tool.name][1]
                assert tool.output_schema == contracts.TOOLS[tool.name][2]
            for name, change in INVALID:
                result = await client.call_tool(name, {**arguments(name), **change, 'trace_id': SECRET})
                assert validate(result, name)['error_code'] == 'INVALID_INPUT'
    asyncio.run(exercise())
    output = capfd.readouterr()
    assert SECRET not in output.out + output.err
