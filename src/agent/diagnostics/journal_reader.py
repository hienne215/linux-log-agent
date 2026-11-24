"""Fixed read-only journalctl acquisition. No shell/files/paths from model input."""
import asyncio
import json

from src.mcp_server.journal_parser import parse_journal_json, JournalParseError
from src.mcp_server.process_cleanup import finish_process
from agent.diagnostics.journal_policy import valid_window, parse_stamp, LIMIT, UNIT

TIMEOUT = 5.0
MAX_STDOUT = 65536
MAX_STDERR = 4096


class JournalError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def argv(arguments, *, minutes=15):
    if not valid_window(arguments, minutes=minutes):
        raise JournalError('POLICY_DENIED')
    return ('/usr/bin/journalctl', '--no-pager', '--no-hostname', '--output=json',
            '--output-fields=__REALTIME_TIMESTAMP,PRIORITY,MESSAGE,_SYSTEMD_UNIT,UNIT,OBJECT_SYSTEMD_UNIT,_PID',
            '--unit='+UNIT, '--priority=0..'+str(arguments.get('priority',3)), '--lines='+str(LIMIT+1),
            '--since='+arguments['since'], '--until='+arguments['until'])


async def _read(stream, limit):
    result = bytearray()
    while chunk := await stream.read(min(4096, limit-len(result)+1)):
        if len(result)+len(chunk) > limit:
            raise JournalError('OUTPUT_LIMIT')
        result.extend(chunk)
    return bytes(result)


async def _run(arguments, *, minutes=15):
    command = argv(arguments, minutes=minutes)
    return await execute(command)


async def execute(command, *, seconds=None):
    """Bound acquisition; retain and join cleanup separately, even on repeated cancel.

    Cleanup never detaches from this owner. Its join can exceed the acquisition
    timeout; this is not a hard wall deadline or an inference cancellation claim.
    """
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(*command,
        stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
        shell=False,cwd='/',env={'LC_ALL':'C','LANG':'C','SYSTEMD_PAGER':'','SYSTEMD_COLORS':'0'},limit=4096))
    process, readers = None, []
    try:
        async with asyncio.timeout(TIMEOUT if seconds is None else seconds):
            process = await asyncio.shield(spawn)
            readers = [asyncio.create_task(_read(process.stdout,MAX_STDOUT)),
                       asyncio.create_task(_read(process.stderr,MAX_STDERR))]
            stdout, stderr, code = await asyncio.gather(*readers, process.wait())
            return code, stdout, stderr
    finally:
        await finish_process(spawn,process,readers)


def validate_records(payload, arguments):
    try:
        entries = parse_journal_json(payload)
        if len(entries)>LIMIT:
            raise JournalError('OUTPUT_LIMIT')
        # Verify the source unit before publishing even an anonymized record.
        from src.mcp_server.journal_parser import _object
        raw = payload.splitlines()
        start, end = parse_stamp(arguments['since']), parse_stamp(arguments['until'])
        previous = None
        for line,entry in zip(raw,entries,strict=True):
            record = json.loads(line.decode('utf-8'),object_pairs_hook=_object)
            observed = parse_stamp(entry['timestamp'])
            from_nginx = (record.get('_SYSTEMD_UNIT')==UNIT or (record.get('_PID')=='1'
                and (record.get('UNIT')==UNIT or record.get('OBJECT_SYSTEMD_UNIT')==UNIT)))
            if (not from_nginx or not start<=observed<=end
                    or entry['priority']>arguments.get('priority',3) or (previous is not None and observed<previous)):
                raise JournalError('EXECUTION_UNKNOWN')
            previous = observed
        return entries
    except JournalError:
        raise
    except (JournalParseError,ValueError,UnicodeError,KeyError,TypeError,OverflowError):
        raise JournalError('EXECUTION_UNKNOWN') from None


async def collect_journal(arguments, *, minutes=15):
    try:
        code,stdout,stderr = await (_run(arguments) if minutes==15 else _run(arguments, minutes=minutes))
        diagnostic = stderr.lower()
        if any(v in diagnostic for v in (b'permission denied',b'not seeing messages',b'access denied',b'insufficient permissions',b'no journal files were opened')):
            raise JournalError('PERMISSION_DENIED')
        if any(v in diagnostic for v in (b'unknown option',b'unrecognized option',b'not supported')):
            raise JournalError('UNSUPPORTED')
        if code != 0 or stderr:
            raise JournalError('EXECUTION_UNKNOWN')
        return validate_records(stdout,arguments)
    except JournalError:raise
    except TimeoutError:raise JournalError('TIMEOUT') from None
    except PermissionError:raise JournalError('PERMISSION_DENIED') from None
    except (FileNotFoundError,NotImplementedError):raise JournalError('UNSUPPORTED') from None
    except Exception:raise JournalError('EXECUTION_UNKNOWN') from None
