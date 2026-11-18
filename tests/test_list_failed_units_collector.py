"""Offline command/pipe doubles only. Never execute systemctl or Linux mutations."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

from jsonschema import Draft202012Validator
import pytest

from src.mcp_server import list_failed_units as core


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'schemas/tools/list_failed_units.response.schema.json').read_text())


def item(name='nginx.service', description='Synthetic example', **extra):
    return dict(unit=name, load='loaded', active='failed', sub='failed', description=description, **extra)


def listing(records):
    return json.dumps(records, ensure_ascii=False).encode('utf-8')


def row(name='nginx.service', description='Synthetic example'):
    return listing([item(name, description)])


class Pipe:
    def __init__(self, data=b'', block=False):
        self.data, self.block, self.requests = data, block, []
        self.transferred = 0
        self.release = asyncio.Event()

    async def read(self, size):
        self.requests.append(size)
        if self.block:
            await self.release.wait()
        part, self.data = self.data[:size], self.data[size:]
        self.transferred += len(part)
        return part


class Process:
    def __init__(self, stdout=b'[]', stderr=b'', code=0, block=False):
        self.stdout, self.stderr = Pipe(stdout, block), Pipe(stderr, block)
        self.code, self.returncode, self.killed, self.waited = code, None, False, False

    async def wait(self):
        if self.stdout.block:
            await self.stdout.release.wait()
        self.returncode = -9 if self.killed else self.code
        self.waited = True
        return self.returncode

    def kill(self):
        self.killed = True
        self.stdout.release.set()
        self.stderr.release.set()


@pytest.fixture
def spawn(monkeypatch):
    mock = AsyncMock(return_value=Process())
    monkeypatch.setattr(core.asyncio, 'create_subprocess_exec', mock)
    return mock


def rejected(call, code):
    with pytest.raises(core.FailedUnitsError, match='^' + code + '$') as caught:
        call()
    assert caught.value.code == code
    return caught.value


@pytest.mark.parametrize('count', [0, 1, 3, 100, 200])
def test_success_and_canonical_shape(spawn, count):
    process = Process(listing([item(f'unit{i}.service') for i in range(count)]))
    spawn.return_value = process
    data = asyncio.run(core.collect_failed_units(limit=max(1, count)))
    assert data['count'] == count
    assert data['limit'] == max(1, count)
    assert set(data) == {'failed_units', 'count', 'limit'}
    assert all(set(item) == {'unit', 'active_state', 'description'} and item['active_state'] == 'failed' for item in data['failed_units'])
    Draft202012Validator(SCHEMA).validate(data)
    assert process.waited


def test_fixed_read_command_and_clean_environment(spawn):
    asyncio.run(core.collect_failed_units())
    args, kwargs = spawn.call_args
    assert args == ('/usr/bin/systemctl', '--system', '--no-pager', '--no-ask-password',
        '--no-legend', '--plain', '--full', '--all', '--state=failed', '--output=json', 'list-units')
    assert kwargs == dict(stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, shell=False, cwd='/', env=core.ENV, limit=4096)
    assert not any(item in args for item in ('sudo', 'restart', 'stop', 'enable', 'disable'))


@pytest.mark.parametrize('limit', [0, 201, True, False, None, '100', 1.0, [], {}])
def test_invalid_limits_fail_before_spawn(spawn, limit):
    rejected(lambda: asyncio.run(core.collect_failed_units(limit)), 'INVALID_INPUT')
    spawn.assert_not_called()


@pytest.mark.parametrize('limit', [1, 100, 200])
def test_no_fake_complete_page(spawn, limit):
    spawn.return_value = Process(listing([item(f'u{i}.service') for i in range(limit + 1)]))
    rejected(lambda: asyncio.run(core.collect_failed_units(limit)), 'OUTPUT_LIMIT')


def test_description_preserved_with_outer_spacing_removed():
    data = core.parse_failed_units(row(description='  Tiếng Việt: test (lab)  '))
    assert data['failed_units'][0]['description'] == 'Tiếng Việt: test (lab)'
    assert core.parse_failed_units(row(description=''))['failed_units'][0]['description'] == ''


@pytest.mark.parametrize('unit', ['x' * 256 + '.service', '../foo.service', '-foo.service',
    '/tmp/a.service', 'foo@.service', 'foo@@bar.service', 'foo.unknown', 'foo\\x2dbar.service'])
def test_bad_unit_names(unit):
    rejected(lambda: core.parse_failed_units(row(unit)), 'EXECUTION_UNKNOWN')


@pytest.mark.parametrize('name', ['worker@lab.service', 'example.socket', 'example.timer', 'example.mount'])
def test_supported_canonical_names_are_preserved(name):
    assert core.parse_failed_units(row(name))['failed_units'][0]['unit'] == name


@pytest.mark.parametrize('payload', [b'\xff\n', b'UNIT LOAD ACTIVE SUB DESCRIPTION\n',
    b'0 loaded units listed.\n', b'nginx.service charge echec echec description\n',
    row()[:-1], row().replace(b'"active": "failed"', b'"active": "active"'),
    row().replace(b'loaded', b'unknown'), row(description='unsafe\x1b[31m'),
    row(description='unsafe\x00'), row(description='hidden\u200btext'),
    b'', b'{}', b'null', b'\n', listing([item(), item()]), row() + row()])
def test_malformed_localized_unsafe_or_truncated_output(payload):
    rejected(lambda: core.parse_failed_units(payload), 'EXECUTION_UNKNOWN')


def test_size_and_description_caps():
    rejected(lambda: core.parse_failed_units(b'x' * (core.STDOUT_LIMIT + 1)), 'OUTPUT_LIMIT')
    rejected(lambda: core.parse_failed_units(row(description='x' * 2049)), 'OUTPUT_LIMIT')
    rejected(lambda: core.parse_failed_units(listing([item(f'u{i}.service') for i in range(201)]), 200), 'OUTPUT_LIMIT')


@pytest.mark.parametrize('stdout,stderr,code,expected', [
    (b'', b'Permission denied SENSITIVE', 1, 'PERMISSION_DENIED'),
    (b'', b'Access denied', 0, 'PERMISSION_DENIED'),
    (b'', b'System has not been booted with systemd', 1, 'UNSUPPORTED'),
    (b'', b'Failed to connect to bus: No such file', 1, 'UNSUPPORTED'),
    (row(), b'', 1, 'EXECUTION_UNKNOWN'),
    (b'', b'', 1, 'EXECUTION_UNKNOWN'),
    (b'', b'SENSITIVE unknown warning', 0, 'EXECUTION_UNKNOWN'),
])
def test_command_errors_never_become_empty_success(spawn, capsys, stdout, stderr, code, expected):
    spawn.return_value = Process(stdout, stderr, code)
    rejected(lambda: asyncio.run(core.collect_failed_units()), expected)
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('failure,expected', [(FileNotFoundError('SENSITIVE'), 'UNSUPPORTED'),
    (NotImplementedError('SENSITIVE'), 'UNSUPPORTED'), (PermissionError('SENSITIVE'), 'PERMISSION_DENIED'),
    (OSError('SENSITIVE'), 'EXECUTION_UNKNOWN')])
def test_spawn_errors_are_static(spawn, failure, expected):
    spawn.side_effect = failure
    caught = rejected(lambda: asyncio.run(core.collect_failed_units()), expected)
    assert caught.__suppress_context__


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_stream_limits_reap_owned_child(spawn, stream):
    process = Process(stdout=row())
    pipe = getattr(process, stream)
    budget = core.STDOUT_LIMIT if stream == 'stdout' else core.STDERR_LIMIT
    pipe.data = b'x' * (budget + 1)
    spawn.return_value = process
    rejected(lambda: asyncio.run(core.collect_failed_units()), 'OUTPUT_LIMIT')
    assert process.waited
    # Cleanup may request another chunk at EOF without retaining any bytes.
    assert pipe.transferred == budget + 1
    assert max(pipe.requests) <= 4096


def test_timeout_kills_and_reaps(spawn, monkeypatch):
    process = Process(block=True)
    spawn.return_value = process
    monkeypatch.setattr(core, 'TIMEOUT', 0.01)
    rejected(lambda: asyncio.run(core.collect_failed_units()), 'TIMEOUT')
    assert process.killed and process.waited


def test_cancellation_retains_cleanup(spawn):
    process = Process(block=True)
    spawn.return_value = process
    async def exercise():
        task = asyncio.create_task(core.collect_failed_units())
        while not process.stdout.requests:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.killed and process.waited
    asyncio.run(exercise())


def test_pending_spawn_timeout_still_reaps(spawn, monkeypatch):
    process = Process(block=True)
    async def delayed(*args, **kwargs):
        await asyncio.sleep(0.02)
        return process
    spawn.side_effect = delayed
    monkeypatch.setattr(core, 'TIMEOUT', 0.005)
    rejected(lambda: asyncio.run(core.collect_failed_units()), 'TIMEOUT')
    assert process.killed and process.waited


@pytest.mark.parametrize('budget', [0, core.STDOUT_LIMIT, core.STDERR_LIMIT])
def test_reader_accepts_exact_cap_and_rejects_one_more_byte(budget):
    pipe = Pipe(b'x' * budget)
    assert asyncio.run(core._read(pipe, budget)) == b'x' * budget
    overflow = Pipe(b'x' * (budget + 1))
    rejected(lambda: asyncio.run(core._read(overflow, budget)), 'OUTPUT_LIMIT')
    assert overflow.transferred == budget + 1


def test_pending_spawn_cancellation_still_reaps(spawn):
    process = Process(block=True)
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return process
        spawn.side_effect = delayed
        task = asyncio.create_task(core.collect_failed_units())
        await entered.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.killed and process.waited
    asyncio.run(exercise())


def test_pending_job_is_not_part_of_description(spawn):
    spawn.return_value = Process(listing([item(job='start'), item('other.service', '', job='')]))
    data = asyncio.run(core.collect_failed_units())
    assert data['failed_units'][0]['description'] == 'Synthetic example'
    assert data['failed_units'][1]['description'] == ''
    assert all('job' not in unit for unit in data['failed_units'])
    Draft202012Validator(SCHEMA).validate(data)


def test_ambiguous_legacy_job_column_is_rejected():
    rejected(lambda: core.parse_failed_units(
        b'nginx.service loaded failed failed start Actual description\n'), 'EXECUTION_UNKNOWN')


@pytest.mark.parametrize('suffix', [b',"unit":"duplicate"}]', b',"job":NaN}]',
    b',"job":Infinity}]', b',"job":1e999}]', b',"job":"\\ud800"}]'])
def test_invalid_json_values_on_complete_record(suffix):
    payload = row()[:-2] + suffix
    rejected(lambda: core.parse_failed_units(payload), 'EXECUTION_UNKNOWN')


@pytest.mark.parametrize('field', ['unit', 'load', 'active', 'sub', 'description'])
def test_missing_json_field(field):
    record = item()
    del record[field]
    rejected(lambda: core.parse_failed_units(listing([record])), 'EXECUTION_UNKNOWN')


@pytest.mark.parametrize('extra', [{'unexpected': 'value'}, {'job': 1}, {'job': ['start']}, {'job': '\x1b'}])
def test_unknown_or_unsafe_metadata(extra):
    rejected(lambda: core.parse_failed_units(listing([item(**extra)])), 'EXECUTION_UNKNOWN')


def test_json_literal_ellipsis_preserved_and_no_placeholder_description():
    assert core.parse_failed_units(row(description='Progress...'))['failed_units'][0]['description'] == 'Progress...'
    assert core.parse_failed_units(row(description=''))['failed_units'][0]['description'] == ''
