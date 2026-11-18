"""Fixed read-only profiles using the team's collector cores, never arbitrary argv."""
import asyncio
import ipaddress
import json
from pathlib import Path
import secrets
import signal
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import mock_mcp_server as contracts
from src.mcp_server.list_failed_units import collect_failed_units, FailedUnitsError
from src.mcp_server.listening_ports import collect_listening_ports, ListeningPortsError

SOURCES = {'list_failed_units': 'collector.list_failed_units.systemctl.v1',
           'listening_ports': 'collector.listening_ports.ss.v1'}


def authorized(tool, arguments):
    if tool == 'list_failed_units':
        return arguments.get('limit', 100) == 100 and 'cursor' not in arguments
    return (arguments.get('protocol') == 'tcp' and arguments.get('port') == 80
            and 'unit' not in arguments)


def private_address(address):
    # Socket addresses do not establish process/service ownership. Never publish
    # a LAN/WAN IP or interface identifier in this limited HTTP-port profile.
    try:
        ipaddress.ip_address(address)
        return address if address in {'0.0.0.0', '::', '127.0.0.1', '::1'} else '[REDACTED]'
    except ValueError:
        return '*' if address == '*' else '[REDACTED]'


async def collect_response(tool, arguments):
    if not contracts.TOOLS[tool][3].is_valid(arguments):
        return contracts.safe_error(tool, {}, rule='validation')
    if not authorized(tool, arguments):
        return contracts.safe_error(tool, {}, 'POLICY_DENIED', rule='scope')
    if sys.platform != 'linux':
        return contracts.safe_error(tool, {}, 'UNSUPPORTED', rule='platform')
    start, tick = contracts.current_time(), time.monotonic()
    try:
        if tool == 'list_failed_units':
            data = await collect_failed_units(100)
            # Drop descriptions completely, including malicious instructions and
            # credentials; not a claim that descriptions were inspected safely.
            for row in data['failed_units']:
                row['description'] = '[REDACTED]'
            excerpt = 'systemctl failed-unit snapshot; count=' + str(data['count'])
        else:
            data = await collect_listening_ports(protocol='tcp', port=80)
            for row in data['ports']:
                row['address'] = private_address(row['address'])
                if row['process'] != '' or row['unit'] is not None:
                    raise ValueError()
            excerpt = 'ss TCP port 80 snapshot; count=' + str(data['count']) + '; ownership not established'
        observed = contracts.current_time()
        response = contracts.make_response(tool, arguments['trace_id'], arguments['logical_tool_call_id'],
            arguments['attempt_id'], data, arguments['session_id'], evidence=[dict(
                evidence_id='diag-'+secrets.token_hex(16), source=SOURCES[tool], observed_at=observed,
                unit=None, excerpt=excerpt, redacted=True, truncated=False)])
        response.update(started_at=start, completed_at=contracts.current_time(),
                        duration_ms=int((time.monotonic()-tick)*1000))
        if not contracts.TOOLS[tool][4].is_valid(response):
            raise ValueError()
        return response
    except (FailedUnitsError, ListeningPortsError) as error:
        code = error.code if error.code in {'INVALID_INPUT', 'OUTPUT_LIMIT', 'PERMISSION_DENIED',
            'UNSUPPORTED', 'TIMEOUT', 'EXECUTION_UNKNOWN'} else 'EXECUTION_UNKNOWN'
        return contracts.safe_error(tool, {}, code, rule='collection')
    except Exception:
        return contracts.safe_error(tool, {}, 'EXECUTION_UNKNOWN', rule='collection')


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError()


async def run(tool, arguments):
    # SIGTERM from WorkerRunner cancels the collector owner so the inherited
    # collector's finally block kills and reaps its fixed ss/systemctl child.
    owner = asyncio.current_task()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, owner.cancel)
    try:
        return await collect_response(tool, arguments)
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in SOURCES:
        return 2
    tool = sys.argv[1]
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            return 2
        try:
            arguments = json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=invalid_constant)
        except (ValueError, UnicodeError):
            response = contracts.safe_error(tool, {}, rule='json')
        else:
            response = asyncio.run(run(tool, arguments))
        payload = json.dumps(response, ensure_ascii=True, allow_nan=False).encode()
        if len(payload) > contracts.MAX_RESPONSE_BYTES:
            payload = json.dumps(contracts.safe_error(tool, {}, 'OUTPUT_LIMIT', rule='worker_output')).encode()
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return 0
    except (Exception, asyncio.CancelledError):
        return 2  # Never emit OS errors, stderr, descriptions or raw payloads.


if __name__ == '__main__':
    raise SystemExit(main())
