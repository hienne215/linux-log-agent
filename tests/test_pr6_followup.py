"""Remaining PR #6 privacy and transport-policy regressions (official SDK stdio)."""
import asyncio
import json
import tempfile

import anyio
import pytest
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCRequest, JSONRPCNotification
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'mock_mcp_server.py'
MARKER = 'SYNTHETIC_PRIVATE_METADATA_52bd'
COMMON = dict(schema_version='1.0.0', session_id=MARKER, trace_id=MARKER,
              logical_tool_call_id=MARKER, attempt_id=MARKER)


@pytest.mark.parametrize('name,business', [
    ('detect_anomaly', dict(since='2026-11-03T00:00:00Z', until='2026-11-02T00:00:00Z')),
    ('list_failed_units', dict(cursor='invalid-cursor')),
    ('query_journal', dict(unit='nginx.service', since='2026-11-01T00:00:00Z', until='2026-11-05T00:00:00Z', cursor='invalid-cursor')),
    ('search_log', dict(source_id='mock-source', query='error', since='2026-11-01T00:00:00Z', until='2026-11-05T00:00:00Z', cursor='invalid-cursor')),
    ('verify_fix', dict(unit='nginx.service', check_types=['service_active'], since='2026-11-03T00:00:00Z', until='2026-11-02T00:00:00Z')),
])
def test_semantic_rejection_privacy_stdio(name, business, capfd):
    async def exercise():
        async with Client(StdioServerParameters(command=sys.executable, args=[str(SERVER)])) as client:
            result = await client.call_tool(name, {**COMMON, **business})
            assert result.is_error
            envelope = result.structured_content
            assert envelope['error_code'] == 'INVALID_INPUT'
            assert MARKER not in json.dumps(envelope)
            assert MARKER not in ''.join(block.text for block in result.content if block.type == 'text')
            assert envelope['trace_id'] == 'invalid-input'
            assert envelope['session_id'] is None
    asyncio.run(exercise())
    captured = capfd.readouterr()
    assert MARKER not in captured.out + captured.err


async def exchange(request_id, expect_close, near_cap=False):
    with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as errlog:
        entrypoint = [str(ROOT / 'tests/mcp_smoke/adversarial_server.py'), 'near'] if near_cap else [str(SERVER)]
        async with stdio_client(StdioServerParameters(command=sys.executable, args=entrypoint), errlog=errlog) as (read, write):
            await write.send(SessionMessage(JSONRPCRequest(jsonrpc='2.0', id=1, method='initialize', params={
                'protocolVersion':'2025-06-18', 'capabilities':{}, 'clientInfo':{'name':'policy-test','version':'1'}})))
            with anyio.fail_after(15):
                initialized = await read.receive()
            assert initialized.message.id == 1
            await write.send(SessionMessage(JSONRPCNotification(jsonrpc='2.0', method='notifications/initialized')))
            await write.send(SessionMessage(JSONRPCRequest(jsonrpc='2.0', id=request_id, method='tools/call',
                params={'name':'service_status', 'arguments':{**COMMON, 'unit':'nginx.service'}})))
            with anyio.fail_after(5):
                if expect_close:
                    with pytest.raises(anyio.EndOfStream):
                        await read.receive()
                else:
                    reply = (await read.receive()).message
                    assert reply.id == request_id
                    assert reply.result['structuredContent']['status'] == ('error' if near_cap else 'ok')
                    if near_cap:
                        assert reply.result['structuredContent']['error_code'] == 'OUTPUT_LIMIT'
                    assert len((reply.model_dump_json(by_alias=True, exclude_unset=True)+'\n').encode('utf-8')) <= 65536
        errlog.seek(0)
        stderr = errlog.read()
        assert MARKER not in stderr
        if expect_close:
            assert stderr == 'Transport policy: JSON-RPC ID exceeds 128 UTF-8 bytes; closing stdio.\n'


@pytest.mark.parametrize('request_id', ['X'*126, '🙂'*31+'aa', '\\'*63, 2**63-1, int('9'*128)], ids=['ascii-max','unicode-max','escaped-max','integer-normal','integer-max'])
def test_transport_id_boundary_roundtrip_stdio(request_id):
    asyncio.run(exchange(request_id, expect_close=False))


@pytest.mark.parametrize('request_id', ['X'*127, '🙂'*32, '\\'*64, int('9'*129), MARKER+'X'*70000], ids=['ascii-over','unicode-over','escaped-over','integer-over','70000-byte-id'])
def test_transport_overlong_id_closes_without_echo_stdio(request_id):
    asyncio.run(exchange(request_id, expect_close=True))


@pytest.mark.parametrize('request_id', ['X'*126, '🙂'*31+'aa'])
def test_near_cap_with_maximum_allowed_id_stdio(request_id):
    asyncio.run(exchange(request_id, expect_close=False, near_cap=True))


def test_oversized_initialize_id_is_rejected_before_dispatch_stdio():
    async def exercise():
        with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as errlog:
            async with stdio_client(StdioServerParameters(command=sys.executable, args=[str(SERVER)]), errlog=errlog) as (read, write):
                await write.send(SessionMessage(JSONRPCRequest(jsonrpc='2.0', id=MARKER+'X'*70000, method='initialize', params={
                    'protocolVersion':'2025-06-18', 'capabilities':{}, 'clientInfo':{'name':'policy-test','version':'1'}})))
                with anyio.fail_after(15):
                    with pytest.raises(anyio.EndOfStream):
                        await read.receive()
            errlog.seek(0)
            assert errlog.read() == 'Transport policy: JSON-RPC ID exceeds 128 UTF-8 bytes; closing stdio.\n'
    asyncio.run(exercise())
