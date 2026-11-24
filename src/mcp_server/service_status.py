"""Opt-in, read-only Linux systemd status. No user-selected commands or units."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import sys
import time

import mock_mcp_server as contracts
from .process_cleanup import finish_process
from agent.diagnostics.service_snapshot import format_snapshot

ALLOWED_UNITS = frozenset({'nginx.service','ssh.service','cron.service'})  # Local operator-owned read-only policy, never discovery/model-owned.
EXECUTABLES = ('/usr/bin/systemctl', '/bin/systemctl')
PROPERTIES = ('Id', 'LoadState', 'ActiveState', 'SubState')
DETAIL_PROPERTIES = ('ExecMainCode', 'ExecMainStatus', 'NRestarts')
TIMEOUT = 5.0
STDOUT_LIMIT = 8192
STDERR_LIMIT = 4096
SOURCE = 'collector.service_status.systemctl.v1'
ACTIVE = frozenset({'active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading', 'maintenance', 'refreshing'})
LOAD = frozenset({'loaded', 'masked', 'not-found', 'error', 'bad-setting', 'stub', 'merged'})
SUB = frozenset({'dead', 'running', 'exited', 'failed', 'start-pre', 'start', 'start-post',
                 'reload', 'reload-signal', 'reload-notify', 'stop', 'stop-watchdog',
                 'stop-sigterm', 'stop-sigkill', 'stop-post', 'final-watchdog',
                 'final-sigterm', 'final-sigkill', 'auto-restart', 'auto-restart-queued', 'cleaning'})


class CollectionFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def command(unit):
    if unit not in ALLOWED_UNITS:
        raise CollectionFailure('POLICY_DENIED')
    executable = next((p for p in EXECUTABLES if Path(p).is_file() and os.access(p, os.X_OK)), None)
    if executable is None:
        raise CollectionFailure('UNSUPPORTED')
    return (executable, '--no-pager', '--no-ask-password', 'show',
            '--property=' + ','.join(PROPERTIES + DETAIL_PROPERTIES), '--', unit)


async def read_bounded(stream, limit):
    result = bytearray()
    while chunk := await stream.read(4096):
        if len(result) + len(chunk) > limit:
            raise CollectionFailure('OUTPUT_LIMIT')
        result.extend(chunk)
    return bytes(result)


async def run(argv):
    """Own and reap exactly one fixed read child, including timeout/cancellation."""
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd='/',
        env={'LC_ALL': 'C', 'LANG': 'C', 'SYSTEMD_COLORS': '0', 'SYSTEMD_PAGER': ''}))
    process = None
    tasks = []
    try:
        try:
            async with asyncio.timeout(TIMEOUT):
                process = await asyncio.shield(spawn)
                tasks = [asyncio.create_task(read_bounded(process.stdout, STDOUT_LIMIT)),
                         asyncio.create_task(read_bounded(process.stderr, STDERR_LIMIT))]
                stdout, stderr, code = await asyncio.gather(*tasks, process.wait())
        except TimeoutError:
            raise CollectionFailure('TIMEOUT') from None
        return code, stdout, stderr
    finally:
        await finish_process(spawn,process,tasks)


def parse(stdout, unit):
    try:
        text = stdout.decode('utf-8', errors='strict')
        values = {}
        for line in text.splitlines():
            key, value = line.split('=', 1)
            if key not in PROPERTIES + DETAIL_PROPERTIES or key in values:
                raise ValueError
            values[key] = value
        if not set(PROPERTIES) <= set(values) or values['Id'] != unit:
            raise ValueError
        if values['LoadState'] not in LOAD or values['ActiveState'] not in ACTIVE or values['SubState'] not in SUB:
            raise ValueError
        # Older fixtures/peers may lack these optional observations. Never infer
        # a zero exit/restart count from omission or an empty property.
        for key in DETAIL_PROPERTIES:
            if key in values:
                raw = values[key]
                if raw == '':values[key] = None
                elif raw.isascii() and raw.isdecimal() and len(raw) <= 10:
                    value = int(raw)
                    if value > (6 if key == 'ExecMainCode' else 2**32-1):raise ValueError()
                    values[key] = value
                else:raise ValueError()
        return values
    except (ValueError, UnicodeError):
        raise CollectionFailure('EXECUTION_UNKNOWN') from None


async def collect(arguments, *, execute=run, platform_name=None):
    """Return the canonical envelope. Injection points are trusted Python tests only."""
    name = 'service_status'
    if not contracts.TOOLS[name][3].is_valid(arguments):
        return contracts.safe_error(name, {}, rule='validation')
    if arguments['unit'] not in ALLOWED_UNITS:
        return contracts.safe_error(name, {}, 'POLICY_DENIED', rule='unit_allowlist')
    if (sys.platform if platform_name is None else platform_name) != 'linux':
        return contracts.safe_error(name, {}, 'UNSUPPORTED', rule='platform')
    started = contracts.current_time(); tick = time.monotonic()
    try:
        code, stdout, stderr = await execute(command(arguments['unit']))
        if not isinstance(stdout, bytes) or not isinstance(stderr, bytes):
            raise CollectionFailure('EXECUTION_UNKNOWN')
        if len(stdout) > STDOUT_LIMIT or len(stderr) > STDERR_LIMIT:
            raise CollectionFailure('OUTPUT_LIMIT')
        # C locale diagnostics are inspected only to choose a static safe code.
        denied = any(marker in stderr.lower() for marker in
                     (b'permission denied', b'access denied', b'not authorized',
                      b'authentication is required', b'authentication required'))
        if denied:
            raise CollectionFailure('PERMISSION_DENIED')
        if code != 0:
            try:
                failed_properties = parse(stdout, arguments['unit'])
            except CollectionFailure:
                failed_properties = {}
            if failed_properties.get('LoadState') == 'not-found':
                raise CollectionFailure('NOT_FOUND')
            raise CollectionFailure('UNSUPPORTED')
        values = parse(stdout, arguments['unit'])
        if values['LoadState'] == 'not-found':
            raise CollectionFailure('NOT_FOUND')
        observed = contracts.current_time()
        data = dict(unit=arguments['unit'], active_state=values['ActiveState'],
                    sub_state=values['SubState'], load_state=values['LoadState'], description='[REDACTED]')
        details = None
        if any(key in values for key in DETAIL_PROPERTIES):
            details = dict(zip(('exec_main_code','exec_main_status','restart_count'),
                               (values.get(key) for key in DETAIL_PROPERTIES)))
        response = contracts.make_response(name, arguments['trace_id'], arguments['logical_tool_call_id'],
            arguments['attempt_id'], data, arguments['session_id'], evidence=[dict(
                evidence_id='ev-' + secrets.token_urlsafe(24), source=SOURCE,
                observed_at=observed, unit=arguments['unit'],
                excerpt=format_snapshot(data,details),
                redacted=True, truncated=False)])
    except PermissionError:
        response = contracts.safe_error(name, {}, 'PERMISSION_DENIED', rule='permission')
    except (FileNotFoundError, ConnectionError):
        response = contracts.safe_error(name, {}, 'UNSUPPORTED', rule='systemd_unavailable')
    except CollectionFailure as error:
        response = contracts.safe_error(name, {}, error.code, rule='collection')
    except Exception:
        response = contracts.safe_error(name, {}, 'EXECUTION_UNKNOWN', rule='collection')
    response.update(started_at=started, completed_at=contracts.current_time(),
                    duration_ms=int((time.monotonic() - tick) * 1000))
    return response
