"""Review follow-up: authoritative registry paths and unambiguous mock outcomes."""
import asyncio
import json
from pathlib import Path
import sys

from mcp import Client, StdioServerParameters

import mock_mcp_server as server

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_paths_match_registered_contracts():
    schemas = ROOT / 'schemas'
    manifest = json.loads((schemas / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['schema_version'] == '1.0.0'
    assert manifest['sdk'] == 'mcp==2.3.0' and manifest['python'] == '3.12'
    assert manifest['transport'] == 'stdio'
    assert len(manifest['tools']) == len(server.TOOLS) == 12
    assert {item['name'] for item in manifest['tools']} == set(server.TOOLS)
    for item in manifest['tools']:
        record = server.TOOLS[item['name']]
        loaded = {}
        for key in ('input', 'data', 'output'):
            path = (schemas / item[key]).resolve()
            assert path.is_relative_to(schemas.resolve())
            loaded[key] = json.loads(path.read_text(encoding='utf-8'))
        assert loaded['input'] == record[1]
        assert loaded['output'] == record[2]
        assert loaded['output']['properties']['tool'] == {'const': item['name']}
        assert loaded['output']['allOf'][-1]['else']['properties']['data'] == loaded['data']


def test_restart_status_wording_and_examples_stdio():
    async def exercise():
        async with Client(StdioServerParameters(command=sys.executable, args=[str(ROOT/'mock_mcp_server.py')])) as client:
            for state in server.RESTART_STATES:
                args = dict(schema_version='1.0.0',session_id='review',trace_id='trace',
                            logical_tool_call_id='call',attempt_id='attempt',mode='status',
                            action_id='mock-fixture-'+state)
                result = await client.call_tool('restart_service', args)
                response = result.structured_content
                server.TOOLS['restart_service'][4].validate(response)
                assert not result.is_error and json.loads(result.content[0].text) == response
                assert response['data']['message'] == 'Synthetic status fixture.'
                assert response['data']['proposal_status'] == state
                expected = None if state == 'execution_unknown' else state in {'succeeded','failed'}
                assert response['data']['executed'] is expected
    asyncio.run(exercise())
    for example in json.loads((ROOT/'docs/examples/review-r2.json').read_text(encoding='utf-8')):
        if example['tool'] == 'restart_service' and example['input']['mode'] == 'status':
            assert example['structuredContent']['data']['message'] == 'Synthetic status fixture.'
