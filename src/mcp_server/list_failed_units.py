"""Bounded read-only systemctl core, without MCP, evidence or cursor state."""

import asyncio
import json
import re
from .process_cleanup import finish_process


ARGV = ('/usr/bin/systemctl', '--system', '--no-pager', '--no-ask-password',
        '--no-legend', '--plain', '--full', '--all', '--state=failed', '--output=json', 'list-units')
ENV = {'LC_ALL': 'C', 'LANG': 'C', 'SYSTEMD_COLORS': '0', 'SYSTEMD_PAGER': ''}
TIMEOUT = 5.0
STDOUT_LIMIT = 65536
STDERR_LIMIT = 4096
RECORD_LIMIT = 200
DESCRIPTION_LIMIT = 2048
_UNIT = re.compile(r'[A-Za-z0-9][A-Za-z0-9:_.@-]*\.(?:service|socket|target|mount|automount|swap|timer|path|slice|scope|device)\Z')


class FailedUnitsError(Exception):
    """Static typed internal failure, never raw command/OS diagnostics."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _limit(limit):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise FailedUnitsError('INVALID_INPUT')


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FailedUnitsError('EXECUTION_UNKNOWN')
        result[key] = value
    return result


def _constant(_value):
    raise FailedUnitsError('EXECUTION_UNKNOWN')


def parse_failed_units(payload: bytes, limit: int = 100) -> dict:
    """Parse the fixed systemctl JSON array format atomically.

    Only [] is an empty listing; empty bytes are invalid. collect_failed_units
    checks command exit and diagnostics first. No description redaction is
    claimed: these data remain untrusted and are not connected to MCP/LLM output.
    """
    _limit(limit)
    if type(payload) is not bytes:
        raise FailedUnitsError('EXECUTION_UNKNOWN')
    if len(payload) > STDOUT_LIMIT:
        raise FailedUnitsError('OUTPUT_LIMIT')
    try:
        records = json.loads(payload.decode('utf-8', errors='strict'),
                             object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise FailedUnitsError('EXECUTION_UNKNOWN') from None
    if not isinstance(records, list):
        raise FailedUnitsError('EXECUTION_UNKNOWN')
    if len(records) > RECORD_LIMIT:
        raise FailedUnitsError('OUTPUT_LIMIT')
    rows, names = [], set()
    required = {'unit', 'load', 'active', 'sub', 'description'}
    for record in records:
        if (not isinstance(record, dict) or not required <= record.keys()
                or record.keys() - required - {'job'}):
            raise FailedUnitsError('EXECUTION_UNKNOWN')
        if any(not isinstance(value, str) or (value and not value.isprintable())
               for value in record.values()):
            raise FailedUnitsError('EXECUTION_UNKNOWN')
        if (record['load'] not in {'loaded', 'not-found', 'bad-setting', 'error', 'masked'}
                or record['active'] != 'failed' or record['sub'] != 'failed'
                or len(record.get('job', '')) > 64):
            raise FailedUnitsError('EXECUTION_UNKNOWN')
        unit, description = record['unit'], record['description']
        if (len(unit) > 255 or not _UNIT.fullmatch(unit) or unit.count('@') > 1
                or ('@' in unit and not all(unit.rsplit('.', 1)[0].split('@')))
                or unit in names):
            raise FailedUnitsError('EXECUTION_UNKNOWN')
        description = description.strip(' ')
        if len(description) > DESCRIPTION_LIMIT:
            raise FailedUnitsError('OUTPUT_LIMIT')
        names.add(unit)
        rows.append(dict(unit=unit, active_state='failed', description=description))
    if len(rows) > limit:
        # No incomplete canonical page escapes before cursor/coverage integration.
        raise FailedUnitsError('OUTPUT_LIMIT')
    return dict(failed_units=rows, count=len(rows), limit=limit)


async def _read(stream, budget):
    kept = bytearray()
    while True:
        chunk = await stream.read(min(4096, budget - len(kept) + 1))
        if not chunk:
            return bytes(kept)
        if len(kept) + len(chunk) > budget:
            raise FailedUnitsError('OUTPUT_LIMIT')
        kept.extend(chunk)


async def _run():
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(
        *ARGV, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, shell=False, cwd='/', env=dict(ENV), limit=4096))
    process, readers = None, []
    try:
        async with asyncio.timeout(TIMEOUT):
            process = await asyncio.shield(spawn)
            readers = [asyncio.create_task(_read(process.stdout, STDOUT_LIMIT)),
                       asyncio.create_task(_read(process.stderr, STDERR_LIMIT))]
            stdout, stderr, code = await asyncio.gather(*readers, process.wait())
            return code, stdout, stderr
    finally:
        await finish_process(spawn,process,readers)


async def collect_failed_units(limit: int = 100) -> dict:
    """Collect one complete bounded observation, or raise FailedUnitsError.

    This async API owns subprocess cleanup; no synchronous asyncio.run wrapper,
    arbitrary command arguments, retries or reconnects are accepted.
    """
    _limit(limit)
    try:
        code, stdout, stderr = await _run()
    except TimeoutError:
        raise FailedUnitsError('TIMEOUT') from None
    except PermissionError:
        raise FailedUnitsError('PERMISSION_DENIED') from None
    except (FileNotFoundError, NotImplementedError):
        raise FailedUnitsError('UNSUPPORTED') from None
    except FailedUnitsError:
        raise
    except Exception:
        raise FailedUnitsError('EXECUTION_UNKNOWN') from None
    diagnostic = stderr.lower()
    if any(marker in diagnostic for marker in (b'permission denied', b'access denied', b'authentication is required')):
        raise FailedUnitsError('PERMISSION_DENIED')
    if any(marker in diagnostic for marker in (b'not been booted with systemd', b'failed to connect to bus', b'unknown option', b'unrecognized option')):
        raise FailedUnitsError('UNSUPPORTED')
    if code != 0 or stderr:
        raise FailedUnitsError('EXECUTION_UNKNOWN')
    return parse_failed_units(stdout, limit)
