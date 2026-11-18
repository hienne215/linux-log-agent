"""Offline integration; all positive observations come from explicit doubles."""
import asyncio
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from mcp import Client, StdioServerParameters
from mcp.types import CallToolRequestParams
import mock_mcp_server as contracts
from src.mcp_server import disk_usage as disk
from src.mcp_server import disk_usage_worker as worker
from src.mcp_server.runner import WorkerRunner
from src.mcp_server.server import ServerScaffold

ROOT = Path(__file__).resolve().parents[1]
ARGS = dict(schema_version="1.0.0", session_id="disk-session", trace_id="disk-trace",
            logical_tool_call_id="disk-call", attempt_id="disk-attempt")
MARKER = "PRIVATE_MARKER"


def argv(mode):
    return (sys.executable, "-I", str(ROOT / "tests/disk_usage_stdio_fixture.py"), "--worker", mode)


def validate(result):
    value = result.structured_content
    contracts.TOOLS['disk_usage'][4].validate(value)
    assert json.loads(result.content[0].text) == value
    assert result.is_error == (value['status'] == 'error')
    assert contracts.wire_size(result, 1) <= 65536
    assert MARKER not in result.model_dump_json()
    return value


@pytest.fixture
def observed(monkeypatch):
    monkeypatch.setattr(worker.sys, 'platform', 'linux')
    function = Mock(return_value=SimpleNamespace(f_blocks=100, f_bfree=30,
        f_bavail=20, f_frsize=4096, f_bsize=8192, f_files=50, f_ffree=20, f_favail=10))
    monkeypatch.setattr(disk.os, 'statvfs', function, raising=False)
    return function


@pytest.mark.parametrize('include', [True, False])
@pytest.mark.parametrize('selector', [None, 'root'])
def test_observation_schema_correlation_and_evidence(observed, include, selector):
    args = {**ARGS, 'include_inodes': include}
    if selector is not None:
        args['filesystem'] = selector
    response = worker.collect_response(args)
    contracts.TOOLS['disk_usage'][4].validate(response)
    assert response['status'] == 'ok'
    for key in ('session_id', 'trace_id', 'logical_tool_call_id', 'attempt_id'):
        assert response[key] == ARGS[key]
    assert response['data'] == dict(filesystem='root', mount_point='/', used_percent=70.,
                                   available_bytes=81920, inodes_included=include)
    evidence, = response['evidence']
    assert evidence['source'] == worker.SOURCE and evidence['unit'] is None
    assert evidence['redacted'] and not evidence['truncated']
    assert response['started_at'] <= evidence['observed_at'] <= response['completed_at']
    assert 'available_bytes=81920' in evidence['excerpt']
    assert evidence['evidence_id'] != worker.collect_response(args)['evidence'][0]['evidence_id']
    observed.assert_called_with('/')


@pytest.mark.parametrize('change,code', [({'filesystem': None}, 'INVALID_INPUT'),
    ({'filesystem': '../root'}, 'INVALID_INPUT'), ({'filesystem': 'root\n'}, 'INVALID_INPUT'),
    ({'filesystem': '--help'}, 'INVALID_INPUT'), ({'filesystem': '/'}, 'POLICY_DENIED'),
    ({'filesystem': '/tmp'}, 'POLICY_DENIED'), ({'include_inodes': 1}, 'INVALID_INPUT'),
    ({'unknown': 'x'}, 'INVALID_INPUT')])
def test_reject_before_api_and_no_sensitive_reflection(observed, change, code):
    value = worker.collect_response({**ARGS, 'trace_id': MARKER, **change})
    contracts.TOOLS['disk_usage'][4].validate(value)
    assert value['error_code'] == code and value['evidence'] == []
    assert value['session_id'] is None and value['trace_id'] == 'invalid-input'
    assert MARKER not in json.dumps(value)
    observed.assert_not_called()


@pytest.mark.parametrize('error,code', [(PermissionError(MARKER), 'PERMISSION_DENIED'),
    (FileNotFoundError(MARKER), 'NOT_FOUND'), (NotImplementedError(MARKER), 'UNSUPPORTED'),
    (RuntimeError(MARKER), 'EXECUTION_UNKNOWN')])
def test_safe_collection_failure(observed, error, code, capsys):
    observed.side_effect = error
    value = worker.collect_response({**ARGS, 'trace_id': MARKER})
    contracts.TOOLS['disk_usage'][4].validate(value)
    assert value['error_code'] == code and value['evidence'] == []
    assert MARKER not in json.dumps(value)
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('change', [{'mount_point': '/private'}, {'filesystem': 'other'},
    {'used_percent': float('nan')}, {'used_percent': 101}, {'extra': MARKER},
    {'inodes_included': False}])
def test_invalid_observation_never_becomes_evidence(observed, monkeypatch, change):
    data = disk.collect_disk_usage() | change
    monkeypatch.setattr(worker, 'collect_disk_usage', lambda *a: data)
    response = worker.collect_response(ARGS)
    assert response['error_code'] == 'EXECUTION_UNKNOWN' and response['evidence'] == []
    assert MARKER not in json.dumps(response)


@pytest.mark.parametrize('raw', [b'{}', b'\xff', b'{',
    b'{"trace_id":"first","trace_id":"second"}', b'{"trace_id":NaN}'])
def test_direct_worker_rejects_invalid_json(raw):
    result = subprocess.run(argv('ok'), input=raw, capture_output=True, timeout=15)
    assert result.returncode == 0 and result.stderr == b''
    response = json.loads(result.stdout)
    assert response['error_code'] == 'INVALID_INPUT' and response['evidence'] == []
    contracts.TOOLS['disk_usage'][4].validate(response)


def test_worker_request_byte_limit():
    result = subprocess.run(argv('ok'), input=b'x' * 16385, capture_output=True, timeout=15)
    assert result.returncode == 2 and result.stdout == result.stderr == b''


def test_worker_output_byte_limit(monkeypatch):
    monkeypatch.setattr(worker, 'collect_response', lambda args: {'oversized': 'x' * 65537})
    incoming = SimpleNamespace(buffer=io.BytesIO(json.dumps(ARGS).encode()))
    outgoing = SimpleNamespace(buffer=io.BytesIO())
    monkeypatch.setattr(worker.sys, 'stdin', incoming)
    monkeypatch.setattr(worker.sys, 'stdout', outgoing)
    assert worker.main() == 0
    response = json.loads(outgoing.buffer.getvalue())
    contracts.TOOLS['disk_usage'][4].validate(response)
    assert response['error_code'] == 'OUTPUT_LIMIT' and response['evidence'] == []
    assert len(outgoing.buffer.getvalue()) <= 65536


def test_trusted_registration_and_five_second_deadline(monkeypatch):
    import run_mcp_server as entry
    calls = []
    class Capture:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        async def serve(self):
            pass
    monkeypatch.setattr(entry, 'ServerScaffold', Capture)
    monkeypatch.setattr(sys, 'argv', ['run_mcp_server.py', '--linux-disk-usage'])
    entry.main()
    config, = calls
    assert config['backends'] == {'disk_usage': (sys.executable, '-I',
        str(ROOT/'src/mcp_server/disk_usage_worker.py'))}
    assert config['runner'].timeout == 5.0
    assert config['runner'].stdout_limit == 65536 and config['runner'].stderr_limit == 4096


@pytest.mark.parametrize('mode,code', [('ok', None), ('denied', 'PERMISSION_DENIED'),
    ('missing', 'NOT_FOUND'), ('unsupported', 'UNSUPPORTED')])
def test_official_sdk_disk_worker_stdio(mode, code, capfd, actual_sdk_frames):
    async def exercise():
        async with Client(StdioServerParameters(command=sys.executable,
            args=[str(ROOT/'tests/disk_usage_stdio_fixture.py'), mode])) as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 12
            assert next(t for t in tools if t.name == 'disk_usage').input_schema == contracts.TOOLS['disk_usage'][1]
            result = await client.call_tool('disk_usage', ARGS)
            response = validate(result)
            assert response['error_code'] == code
            if code is None:
                for key in ('session_id', 'trace_id', 'logical_tool_call_id', 'attempt_id'):
                    assert response[key] == ARGS[key]
                assert response['data']['available_bytes'] == 81920
                assert response['evidence'][0]['source'].startswith('synthetic.')
            else:
                assert response['evidence'] == []
            rejected = validate(await client.call_tool('disk_usage', {**ARGS,
                'filesystem': '../root', 'trace_id': MARKER}))
            assert rejected['error_code'] == 'INVALID_INPUT'
    asyncio.run(asyncio.wait_for(exercise(), 30))
    assert MARKER not in capfd.readouterr().err


@pytest.fixture
def actual_sdk_frames(monkeypatch):
    import mcp.client.stdio as transport
    parse = transport._parse_line
    frames = []
    def record(line):
        frame = json.loads(line)
        if 'structuredContent' in frame.get('result', {}):
            frames.append(len((line+'\n').encode('utf-8')))
        return parse(line)
    monkeypatch.setattr(transport, '_parse_line', record)
    yield frames
    assert frames and max(frames) <= 65536


@pytest.mark.parametrize('mode,code', [('slow', 'TIMEOUT'), ('flood', 'OUTPUT_LIMIT')])
def test_worker_limits_reap_direct_child(mode, code, monkeypatch):
    async def exercise():
        real_spawn = asyncio.create_subprocess_exec
        processes = []
        async def tracked(*a, **kw):
            process = await real_spawn(*a, **kw)
            processes.append(process)
            return process
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', tracked)
        runner = WorkerRunner(timeout=.5 if mode == 'slow' else 5)
        app = ServerScaffold(backends={'disk_usage': argv(mode)}, runner=runner)
        try:
            response = validate(await app.call_tool(SimpleNamespace(request_id=1),
                CallToolRequestParams(name='disk_usage', arguments=ARGS)))
            assert response['error_code'] == code and not response['evidence']
            assert runner.process is None and runner.spawn is None and runner.drain_task is None
            assert processes and all(p.returncode is not None and p.stdout.at_eof()
                                     and p.stderr.at_eof() for p in processes)
        finally:
            assert await runner.close()
    asyncio.run(exercise())


def test_cancellation_reaps_owned_worker(monkeypatch):
    async def exercise():
        real_spawn = asyncio.create_subprocess_exec
        processes = []
        spawned = asyncio.Event()
        async def tracked(*a, **kw):
            process = await real_spawn(*a, **kw)
            processes.append(process)
            spawned.set()
            return process
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', tracked)
        runner = WorkerRunner(timeout=5)
        task = asyncio.create_task(runner.run(argv('slow'), ARGS))
        try:
            await asyncio.wait_for(spawned.wait(), 10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert runner.process is None and runner.spawn is None
            assert all(p.returncode is not None and p.stdout.at_eof() and p.stderr.at_eof() for p in processes)
        finally:
            assert await runner.close()
    asyncio.run(exercise())


def test_opt_in_default_and_platform():
    async def exercise():
        for flags in ([], ['--linux-disk-usage']):
            async with Client(StdioServerParameters(command=sys.executable,
                args=[str(ROOT/'run_mcp_server.py'), *flags])) as client:
                value = validate(await client.call_tool('disk_usage', ARGS))
                assert value['error_code'] == 'UNSUPPORTED' and not value['evidence']
    # The opt-in branch is checked on Windows without accessing a real filesystem.
    if sys.platform != 'linux':
        asyncio.run(asyncio.wait_for(exercise(), 30))
    result = subprocess.run([sys.executable, str(ROOT/'run_mcp_server.py'), '--fixtures',
        '--linux-disk-usage'], capture_output=True, timeout=15)
    assert result.returncode == 2
