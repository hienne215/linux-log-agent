"""SCRUM18 coexistence with approved main service profile; offline only."""
import asyncio
import json
from pathlib import Path
import sys

import pytest
from mcp import Client, StdioServerParameters
import mock_mcp_server as contracts
import run_mcp_server as entry

ROOT = Path(__file__).resolve().parents[1]
COMMON = dict(schema_version='1.0.0', session_id='profiles-session', trace_id='profiles-trace',
              logical_tool_call_id='profiles-call', attempt_id='profiles-attempt')


@pytest.mark.parametrize('flags,tool,worker', [([], None, None),
    (['--fixtures'], 'service_status', 'fixture_worker.py'),
    (['--linux-service-status'], 'service_status', 'service_status_worker.py'),
    (['--linux-disk-usage'], 'disk_usage', 'disk_usage_worker.py')])
def test_fixed_profiles_preserve_registration_and_resource_policy(monkeypatch, flags, tool, worker):
    configs = []
    class Capture:
        def __init__(self, **kwargs):
            configs.append(kwargs)
        async def serve(self):
            pass
    monkeypatch.setattr(entry, 'ServerScaffold', Capture)
    monkeypatch.setattr(sys, 'argv', ['run_mcp_server.py', *flags])
    entry.main()
    config, = configs
    assert config['backends'] == ({} if tool is None else
        {tool: (sys.executable, '-I', str(ROOT/'src/mcp_server'/worker))})
    if flags == ['--linux-disk-usage']:
        assert config['runner'].timeout == 5.0
        assert config['runner'].stdout_limit == 65536
        assert config['runner'].stderr_limit == 4096
    else:
        # Main service/fixture/default profiles retain ServerScaffold's own runner.
        assert config['runner'] is None


@pytest.mark.parametrize('flags', [
    ['--fixtures', '--linux-service-status'],
    ['--fixtures', '--linux-disk-usage'],
    ['--linux-service-status', '--linux-disk-usage'],
    ['--fixtures', '--linux-service-status', '--linux-disk-usage'],
    ['--unknown-profile'], ['--linux-disk-usage=/tmp'], ['--linux-service-status', '/tmp']])
def test_conflicting_or_unknown_flags_reject_before_backend(monkeypatch, flags, capsys):
    def forbidden(**kwargs):
        pytest.fail('Invalid flags must not construct a backend')
    monkeypatch.setattr(entry, 'ServerScaffold', forbidden)
    monkeypatch.setattr(sys, 'argv', ['run_mcp_server.py', *flags])
    with pytest.raises(SystemExit) as caught:
        entry.main()
    assert caught.value.code == 2
    assert 'error:' in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == 'linux', reason='Real opt-in profiles must not read Linux in offline tests')
@pytest.mark.parametrize('flags', [[], ['--fixtures'], ['--linux-service-status'], ['--linux-disk-usage']])
def test_sdk_selected_profile_keeps_other_real_backend_disabled(flags, monkeypatch):
    import mcp.client.stdio as transport
    original = transport._parse_line
    frames = []
    def parsed(line):
        frame = json.loads(line)
        if 'structuredContent' in frame.get('result', {}):
            frames.append(len((line+'\n').encode('utf-8')))
        return original(line)
    monkeypatch.setattr(transport, '_parse_line', parsed)
    async def exercise():
        async with Client(StdioServerParameters(command=sys.executable,
            args=[str(ROOT/'run_mcp_server.py'), *flags])) as client:
            assert len((await client.list_tools()).tools) == 12
            for name in ('service_status', 'disk_usage'):
                args = dict(COMMON)
                if name == 'service_status':
                    args['unit'] = 'nginx.service'
                result = await client.call_tool(name, args)
                response = result.structured_content
                contracts.TOOLS[name][4].validate(response)
                assert json.loads(result.content[0].text) == response
                assert result.is_error == (response['status'] == 'error')
                assert contracts.wire_size(result, 1) <= 65536
                if flags == ['--fixtures'] and name == 'service_status':
                    assert response['status'] == 'ok'
                    for key in COMMON:
                        assert response[key] == COMMON[key]
                else:
                    assert response['error_code'] == 'UNSUPPORTED'
                    assert response['evidence'] == [] and response['session_id'] is None
    asyncio.run(asyncio.wait_for(exercise(), 30))
    assert len(frames) == 2 and max(frames) <= 65536
