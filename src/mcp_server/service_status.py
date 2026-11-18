"""Opt-in, read-only Linux systemd status. No user-selected commands or units."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import sys
import time

import mock_mcp_server as contracts

ALLOWED_UNITS = frozenset({'nginx.service'})  # Reviewed application policy, not MCP input.
EXECUTABLES = ('/usr/bin/systemctl', '/bin/systemctl')
PROPERTIES = ('Id', 'LoadState', 'ActiveState', 'SubState')
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
            '--property=' + ','.join(PROPERTIES), '--', unit)


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
        process = await asyncio.shield(spawn)
        tasks = [asyncio.create_task(read_bounded(process.stdout, STDOUT_LIMIT)),
                 asyncio.create_task(read_bounded(process.stderr, STDERR_LIMIT))]
        try:
            stdout, stderr, code = await asyncio.wait_for(
                asyncio.gather(*tasks, process.wait()), TIMEOUT)
        except asyncio.TimeoutError:
            raise CollectionFailure('TIMEOUT') from None
        return code, stdout, stderr
    finally:
        if process is None:
            try:  # Cancellation during OS spawn must not abandon the resulting child.
                process = await asyncio.shield(spawn)
            except Exception:
                pass
        if process is not None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            async def discard(stream):
                while await stream.read(4096):
                    pass
            # Killing alone does not confirm pipe EOF. Do not abandon a known child.
            await asyncio.gather(discard(process.stdout), discard(process.stderr), process.wait())


def parse(stdout, unit):
    try:
        text = stdout.decode('utf-8', errors='strict')
        values = {}
        for line in text.splitlines():
            key, value = line.split('=', 1)
            if key not in PROPERTIES or key in values:
                raise ValueError
            values[key] = value
        if set(values) != set(PROPERTIES) or values['Id'] != unit:
            raise ValueError
        if values['LoadState'] not in LOAD or values['ActiveState'] not in ACTIVE or values['SubState'] not in SUB:
            raise ValueError
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
        response = contracts.make_response(name, arguments['trace_id'], arguments['logical_tool_call_id'],
            arguments['attempt_id'], data, arguments['session_id'], evidence=[dict(
                evidence_id='ev-' + secrets.token_urlsafe(24), source=SOURCE,
                observed_at=observed, unit=arguments['unit'],
                excerpt=f"unit={data['unit']}; load={data['load_state']}; active={data['active_state']}; sub={data['sub_state']}",
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
