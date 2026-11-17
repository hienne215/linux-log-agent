"""Offline socket/process fixtures only: no ss, Linux or network execution."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

from jsonschema import Draft202012Validator
import pytest

from src.mcp_server import listening_ports as core

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'schemas/tools/listening_ports.response.schema.json').read_text())


def row(protocol='tcp', address='127.0.0.1:80', peer='0.0.0.0:*', state=None):
    state = state or ('LISTEN' if protocol == 'tcp' else 'UNCONN')
    return f'{protocol} {state} 0 128 {address} {peer}\n'.encode()


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
    def __init__(self, stdout=b'', stderr=b'', code=0, block=False):
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
    with pytest.raises(core.ListeningPortsError, match='^' + code + '$') as caught:
        call()
    assert caught.value.code == code
    return caught.value


def canonical(data):
    Draft202012Validator(SCHEMA).validate(data)
    assert set(data) == {'ports', 'count'}
    assert data['count'] == len(data['ports'])
    for item in data['ports']:
        assert set(item) == {'protocol', 'port', 'address', 'process', 'unit'}
        assert item['process'] == '' and item['unit'] is None
    return data


def test_tcp_udp_snapshot_and_no_ownership_inference(spawn):
    spawn.return_value = Process(row() + row('udp', '*:53', '*:*'))
    data = canonical(asyncio.run(core.collect_listening_ports()))
    assert data['ports'] == [dict(protocol='tcp', port=80, address='127.0.0.1', process='', unit=None),
                             dict(protocol='udp', port=53, address='*', process='', unit=None)]
    assert spawn.return_value.waited


@pytest.mark.parametrize('address,peer,expected', [
    ('0.0.0.0:80', '0.0.0.0:*', '0.0.0.0'), ('*:80', '*:*', '*'),
    ('[::]:80', '[::]:*', '::'), (':::80', ':::*', '::'),
    ('[2001:0db8::1]:80', '[::]:*', '2001:db8::1'),
    ('[fe80::1%eth0]:80', '[::]:*', 'fe80::1%eth0'),
    ('127.0.0.53%lo:80', '0.0.0.0:*', '127.0.0.53%lo'),
    ('[::ffff:127.0.0.1]:80', '*:0', '::ffff:7f00:1')])
def test_numeric_addresses_preserve_bind_meaning(address, peer, expected):
    data = canonical(core.parse_listening_ports(row(address=address, peer=peer)))
    assert data['ports'][0]['address'] == expected


@pytest.mark.parametrize('protocol,port,count', [(None, None, 3), ('tcp', None, 2),
    ('udp', None, 1), (None, 53, 2), ('tcp', 53, 1), ('udp', 80, 0), (None, 65535, 0)])
def test_filters(protocol, port, count):
    data = canonical(core.parse_listening_ports(row() + row(address='*:53', peer='*:*')
        + row('udp', '*:53', '*:*'), protocol, port))
    assert data['count'] == count


@pytest.mark.parametrize('port', [1, 65535])
def test_valid_port_boundaries(port):
    data = canonical(core.parse_listening_ports(row(address=f'*:{port}', peer='*:*'), port=port))
    assert data['ports'][0]['port'] == port


@pytest.mark.parametrize('filters', [{'protocol': 'TCP'}, {'protocol': 'unix'}, {'protocol': True},
    {'protocol': []}, {'port': 0}, {'port': 65536}, {'port': True}, {'port': False},
    {'port': '80'}, {'port': 80.0}, {'unit': '../nginx.service'}, {'unit': True},
    {'unit': ''}, {'unit': 'x' * 129}, {'unit': 'a@lab.service'}])
def test_invalid_filters_reject_before_execution(spawn, filters):
    rejected(lambda: asyncio.run(core.collect_listening_ports(**filters)), 'INVALID_INPUT')
    spawn.assert_not_called()


@pytest.mark.parametrize('unit', ['nginx.service', 'systemd-resolved.service', 'caller-claims-verified.service'])
def test_unit_attribution_unavailable_even_if_caller_claims_it(spawn, unit):
    rejected(lambda: asyncio.run(core.collect_listening_ports(unit=unit)), 'UNSUPPORTED')
    spawn.assert_not_called()  # No trusted attribution provider, hence no verified-unit path.


@pytest.mark.parametrize('payload', [row('udp', '*:53', '1.2.3.4:80'), row('udp', '*:53', '*:*', 'ESTAB'),
    row('tcp', '*:80', '1.2.3.4:0'), row('tcp', '*:80', '*:*', 'ESTAB')])
def test_connected_sockets_are_not_listener_evidence(payload):
    rejected(lambda: core.parse_listening_ports(payload), 'EXECUTION_UNKNOWN')


@pytest.mark.parametrize('address', ['localhost:80', '999.0.0.1:80', '1.2.3.4:0', '*:65536',
    '*:-1', '*:http', '[::1:80', '[127.0.0.1]:80', '[::1%bad/scope]:80', '*%bad/scope:80'])
def test_bad_address_or_port(address):
    rejected(lambda: core.parse_listening_ports(row(address=address)), 'EXECUTION_UNKNOWN')


@pytest.mark.parametrize('payload', [b'\xff\n', b'Netid State Recv-Q Send-Q Local Peer\n', b'\n',
    row().rstrip(b'\n'), row().replace(b'LISTEN', b'ECOUTE'), row().replace(b'0 128', b'-1 128'),
    row()[:-1] + b' users:(("nginx",pid=1,fd=3))\n', row().replace(b'0 128', b'0 18446744073709551616'),
    row()[:-1] + b'\x1b\n', row().replace(b'127.0.0.1', b'127.0.0.1\x00')])
def test_malformed_controls_and_extra_process_metadata(payload):
    rejected(lambda: core.parse_listening_ports(payload), 'EXECUTION_UNKNOWN')


def test_bad_tail_or_filtered_out_record_still_invalidates_whole_snapshot():
    rejected(lambda: core.parse_listening_ports(row() + b'bad\n', protocol='udp'), 'EXECUTION_UNKNOWN')


def test_duplicates_preserve_possible_reuseport_and_order_is_deterministic():
    payload = row('udp', '*:53', '*:*') + row() + row()
    data = canonical(core.parse_listening_ports(payload))
    assert data['count'] == 3 and data['ports'][0] == data['ports'][1]
    assert core.parse_listening_ports(row() + row() + row('udp', '*:53', '*:*')) == data


def test_record_and_raw_byte_limits_precede_filters():
    assert canonical(core.parse_listening_ports(row() * 200))['count'] == 200
    rejected(lambda: core.parse_listening_ports(row() * 201, protocol='udp'), 'OUTPUT_LIMIT')
    rejected(lambda: core.parse_listening_ports(b'x' * (core.STDOUT_LIMIT + 1)), 'OUTPUT_LIMIT')


def test_empty_only_after_successful_command(spawn):
    assert canonical(asyncio.run(core.collect_listening_ports())) == {'ports': [], 'count': 0}


@pytest.mark.parametrize('stderr,code,expected', [(b'Permission denied SECRET', 1, 'PERMISSION_DENIED'),
    (b'Operation not permitted', 0, 'PERMISSION_DENIED'), (b'not supported', 1, 'UNSUPPORTED'),
    (b'', 1, 'EXECUTION_UNKNOWN'), (b'unknown diagnostic SECRET', 0, 'EXECUTION_UNKNOWN')])
def test_command_failure_never_becomes_empty_success(spawn, capsys, stderr, code, expected):
    spawn.return_value = Process(stderr=stderr, code=code)
    rejected(lambda: asyncio.run(core.collect_listening_ports()), expected)
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('failure,expected', [(PermissionError('SECRET /private'), 'PERMISSION_DENIED'),
    (FileNotFoundError('SECRET /private'), 'UNSUPPORTED'), (NotImplementedError('SECRET'), 'UNSUPPORTED'),
    (OSError('SECRET'), 'EXECUTION_UNKNOWN')])
def test_os_errors_static(spawn, failure, expected):
    spawn.side_effect = failure
    assert rejected(lambda: asyncio.run(core.collect_listening_ports()), expected).__suppress_context__


def test_fixed_command_no_user_options_or_environment(spawn, monkeypatch):
    monkeypatch.setenv('SENSITIVE_TEST_VARIABLE', 'SECRET')
    asyncio.run(core.collect_listening_ports(protocol='tcp', port=80))
    args, kwargs = spawn.call_args
    assert args == ('/usr/bin/ss', '--no-header', '--numeric', '--listening', '--tcp', '--udp', '--oneline')
    assert kwargs == dict(stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, shell=False, cwd='/', env={'LC_ALL': 'C', 'LANG': 'C'}, limit=4096)


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_bounded_stream_overflow(spawn, stream):
    process = Process()
    budget = core.STDOUT_LIMIT if stream == 'stdout' else core.STDERR_LIMIT
    pipe = getattr(process, stream)
    pipe.data = b'x' * (budget + 1)
    spawn.return_value = process
    rejected(lambda: asyncio.run(core.collect_listening_ports()), 'OUTPUT_LIMIT')
    assert process.waited and pipe.transferred == budget + 1
    assert max(pipe.requests) <= 4096


@pytest.mark.parametrize('budget', [0, core.STDOUT_LIMIT, core.STDERR_LIMIT])
def test_reader_exact_cap_and_overflow(budget):
    assert asyncio.run(core._read(Pipe(b'x' * budget), budget)) == b'x' * budget
    rejected(lambda: asyncio.run(core._read(Pipe(b'x' * (budget + 1)), budget)), 'OUTPUT_LIMIT')


def test_timeout_reaps_owned_child(spawn, monkeypatch):
    process = Process(block=True)
    spawn.return_value = process
    monkeypatch.setattr(core, 'TIMEOUT', 0.01)
    rejected(lambda: asyncio.run(core.collect_listening_ports()), 'TIMEOUT')
    assert process.killed and process.waited


@pytest.mark.parametrize('pending_spawn', [False, True])
def test_cancellation_keeps_cleanup_ownership(spawn, pending_spawn):
    process = Process(block=True)
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        async def create(*args, **kwargs):
            entered.set()
            if pending_spawn:
                await release.wait()
            return process
        spawn.side_effect = create
        task = asyncio.create_task(core.collect_listening_ports())
        await entered.wait()
        if not pending_spawn:
            while not process.stdout.requests:
                await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.killed and process.waited
    asyncio.run(exercise())


def test_pending_spawn_timeout_reaps(spawn, monkeypatch):
    process = Process(block=True)
    async def delayed(*args, **kwargs):
        await asyncio.sleep(0.02)
        return process
    spawn.side_effect = delayed
    monkeypatch.setattr(core, 'TIMEOUT', 0.005)
    rejected(lambda: asyncio.run(core.collect_listening_ports()), 'TIMEOUT')
    assert process.killed and process.waited


@pytest.mark.parametrize('address,expected', [('[fe80::1]%eth0:5353', 'fe80::1%eth0'),
    ('[::]%12:80', '::%12'), ('*%eth0:80', '*%eth0')])
def test_iproute2_external_scope_and_interface_wildcard(address, expected):
    data = canonical(core.parse_listening_ports(row(address=address)))
    assert data['ports'][0]['address'] == expected


@pytest.mark.parametrize('address', ['[fe80::1]suffix:80', '[fe80::1]%:80',
    '[fe80::1%eth0]%eth1:80', '[fe80::1]%bad/scope:80', '*%eth0%eth1:80'])
def test_ambiguous_or_invalid_external_scope_rejected(address):
    rejected(lambda: core.parse_listening_ports(row(address=address)), 'EXECUTION_UNKNOWN')
