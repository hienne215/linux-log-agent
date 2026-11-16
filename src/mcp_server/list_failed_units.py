"""Bounded read-only systemctl core, without MCP, evidence or cursor state."""

import asyncio
import re


ARGV = ('/usr/bin/systemctl', '--system', '--no-pager', '--no-ask-password',
        '--no-legend', '--plain', '--full', '--all', '--state=failed', 'list-units')
ENV = {'LC_ALL': 'C', 'LANG': 'C', 'SYSTEMD_COLORS': '0', 'SYSTEMD_PAGER': ''}
TIMEOUT = 5.0
STDOUT_LIMIT = 65536
STDERR_LIMIT = 4096
RECORD_LIMIT = 200
DESCRIPTION_LIMIT = 2048
_UNIT = re.compile(r'[A-Za-z0-9][A-Za-z0-9:_.@-]*\.(?:service|socket|target|mount|automount|swap|timer|path|slice|scope|device)\Z')
_ROW = re.compile(r' *([^ ]+) +(loaded|not-found|bad-setting|error|masked) +failed +failed(?: +(.*))?\Z')


class FailedUnitsError(Exception):
    """Static typed internal failure, never raw command/OS diagnostics."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _limit(limit):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise FailedUnitsError('INVALID_INPUT')


def parse_failed_units(payload: bytes, limit: int = 100) -> dict:
    """Parse the fixed C-locale full/plain/no-legend output, atomically.

    An empty byte stream is only meaningful after a successful, diagnostic-free
    command. collect_failed_units owns that check. No description redaction is
    claimed: these data remain untrusted and are not connected to MCP/LLM output.
    """
    _limit(limit)
    if type(payload) is not bytes:
        raise FailedUnitsError('EXECUTION_UNKNOWN')
    if len(payload) > STDOUT_LIMIT:
        raise FailedUnitsError('OUTPUT_LIMIT')
    try:
        text = payload.decode('utf-8', errors='strict')
    except UnicodeError:
        raise FailedUnitsError('EXECUTION_UNKNOWN') from None
    if text and not text.endswith('\n'):
        raise FailedUnitsError('EXECUTION_UNKNOWN')
    lines = text[:-1].split('\n') if text else []
    if len(lines) > RECORD_LIMIT:
        raise FailedUnitsError('OUTPUT_LIMIT')
    rows, names = [], set()
    for line in lines:
        if not line.isprintable() or '…' in line or '...' in line:
            raise FailedUnitsError('EXECUTION_UNKNOWN')
        match = _ROW.fullmatch(line)
        if match is None:
            raise FailedUnitsError('EXECUTION_UNKNOWN')
        unit, _load, description = match.groups()
        if (len(unit) > 255 or not _UNIT.fullmatch(unit) or unit.count('@') > 1
                or ('@' in unit and not all(unit.rsplit('.', 1)[0].split('@')))
                or unit in names):
            raise FailedUnitsError('EXECUTION_UNKNOWN')
        description = (description or '').strip(' ')
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


async def _discard(stream):
    while await stream.read(4096):
        pass


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
        # Retain an in-flight spawn even if timeout/cancellation occurred first.
        if process is None:
            try:
                process = await asyncio.shield(spawn)
            except Exception:
                pass
        if process is not None:
            try:
                if process.returncode is None:
                    process.kill()
            finally:
                for reader in readers:
                    reader.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
                await asyncio.gather(_discard(process.stdout), _discard(process.stderr), process.wait())


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
