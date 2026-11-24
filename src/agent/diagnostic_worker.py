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
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mock_mcp_server as contracts
from agent.diagnostics.journal_anomaly import authorized as anomaly_authorized, evaluate as evaluate_anomaly
from agent.diagnostics.service_verification import authorized as verify_authorized, collect as collect_verification
from agent.diagnostics.system_diagnostics import collect_events, check_config, nginx_file, SourceError
from agent.diagnostics import health_verification
from agent.diagnostics.journal_reader import collect_journal, JournalError
from agent.diagnostics.journal_policy import UNIT, SOURCE_ID, LIMIT, MARKERS, valid_window, project_message, marker_counts
from src.mcp_server.log_search_matcher import match_literal_indices, LogSearchError
from src.mcp_server.list_failed_units import collect_failed_units, FailedUnitsError
from src.mcp_server.listening_ports import collect_listening_ports, ListeningPortsError

SOURCES = {'list_failed_units': 'collector.list_failed_units.systemctl.v1',
           'listening_ports': 'collector.listening_ports.ss.v1',
           'query_journal': 'collector.query_journal.journalctl.v1',
           'search_log': 'collector.search_log.nginx_journal.v1',
           'detect_anomaly': 'collector.query_journal.journalctl.v1',
           'verify_fix': 'collector.service_status.systemctl.v1',
           'memory_oom_events':'collector.memory_oom_events.journal.kernel.v1',
           'failed_logins':'collector.failed_logins.journal.auth.v1',
           'check_config':'collector.check_config.nginx.v1'}


def authorized(tool, arguments):
    if tool in ('memory_oom_events','failed_logins'):
        return valid_window(arguments) and arguments.get('limit',LIMIT)==LIMIT and 'cursor' not in arguments
    if tool=='check_config':
        return arguments.get('profile')=='nginx' and arguments.get('unit',UNIT)==UNIT
    if tool=='verify_fix':
        return verify_authorized(arguments) or health_verification.authorized(arguments)
    if tool=='detect_anomaly':
        return anomaly_authorized(arguments)
    if tool in ('query_journal', 'search_log'):
        if (not valid_window(arguments,minutes=1440) or arguments.get('limit', LIMIT)!=LIMIT
                or 'cursor' in arguments):
            return False
        return (arguments.get('unit')==UNIT and type(arguments.get('priority',3)) is int and 0<=arguments.get('priority',3)<=7
                if tool=='query_journal' else arguments.get('source_id') in (SOURCE_ID,'nginx_error')
                and type(arguments.get('query')) is str and 1<=len(arguments['query'])<=128 and arguments['query'].isprintable())
    if tool == 'list_failed_units':
        return arguments.get('limit', 100) == 100 and 'cursor' not in arguments
    return ('unit' not in arguments and arguments.get('protocol') in (None,'tcp','udp')
            and (arguments.get('port') is None or (type(arguments['port']) is int and 1<=arguments['port']<=65535)))


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
        from agent.diagnostics import read_gateway
        if tool in read_gateway.ALLOWED and read_gateway.enabled():
            return await read_gateway.request(tool,arguments)
        if tool in ('memory_oom_events','failed_logins','check_config'):
            if tool=='check_config':
                data=await check_config(arguments)
                evidence=[dict(evidence_id='config-'+secrets.token_hex(16),source=SOURCES[tool],
                    observed_at=contracts.current_time(),unit=UNIT,excerpt=data['message'],redacted=True,truncated=False)]
            else:data,evidence=await collect_events(tool,arguments)
            response=contracts.make_response(tool,arguments['trace_id'],arguments['logical_tool_call_id'],
                arguments['attempt_id'],data,arguments['session_id'],status='ok' if tool=='check_config' else 'partial',evidence=evidence)
            response.update(started_at=start,completed_at=contracts.current_time(),duration_ms=int((time.monotonic()-tick)*1000))
            if not contracts.TOOLS[tool][4].is_valid(response):raise ValueError()
            return response
        if tool=='verify_fix':
            response=await (health_verification.collect(arguments) if arguments['check_types']==health_verification.CHECKS else collect_verification(arguments))
            if not contracts.TOOLS[tool][4].is_valid(response):
                raise ValueError()
            return response
        if tool=='detect_anomaly':
            entries=await collect_journal(arguments, minutes=30)
            # journalctl --until is inclusive; canonical anomaly is half-open.
            from agent.diagnostics.journal_policy import parse_stamp
            entries=[e for e in entries if parse_stamp(e['timestamp'])<parse_stamp(arguments['until'])]
            data,evidence=evaluate_anomaly(arguments,entries)
            response=contracts.make_response(tool,arguments['trace_id'],arguments['logical_tool_call_id'],
                arguments['attempt_id'],data,arguments['session_id'],status='partial',evidence=evidence)
            response.update(started_at=start,completed_at=contracts.current_time(),duration_ms=int((time.monotonic()-tick)*1000))
            if not contracts.TOOLS[tool][4].is_valid(response):
                raise ValueError()
            return response
        if tool == 'list_failed_units':
            data = await collect_failed_units(100)
            # Drop descriptions completely, including malicious instructions and
            # credentials; not a claim that descriptions were inspected safely.
            for row in data['failed_units']:
                row['description'] = '[REDACTED]'
            excerpt = 'systemctl failed-unit snapshot; count=' + str(data['count'])
        elif tool == 'listening_ports':
            data = await collect_listening_ports(protocol=arguments.get('protocol'), port=arguments.get('port'))
            for row in data['ports']:
                row['address'] = private_address(row['address'])
                if row['process'] != '' or row['unit'] is not None:
                    raise ValueError()
            excerpt = 'ss socket snapshot; count=' + str(data['count']) + '; ownership not established'
        else:
            from agent.diagnostics.journal_policy import parse_stamp
            minutes=15 if (parse_stamp(arguments['until'])-parse_stamp(arguments['since'])).total_seconds()<=900 else 1440
            entries = [] if tool=='search_log' and arguments['source_id']=='nginx_error' else await collect_journal(arguments,minutes=minutes)
            if tool == 'query_journal':
                data = dict(entries=[{**entry, 'message': project_message(entry['message'])}
                                     for entry in entries], count=len(entries), limit=LIMIT)
            else:
                # Explicit case-sensitive literal semantics from the team's matcher.
                if arguments['source_id']=='nginx_error':
                    matches=nginx_file(arguments)
                else:
                    indices = match_literal_indices([e['message'] for e in entries], arguments['query'], limit=LIMIT)
                    matches=[dict(timestamp=entries[i]['timestamp'], message=project_message(entries[i]['message']))
                             for i in indices]
                data = dict(source_id=arguments['source_id'], query=arguments['query'],
                            matches=matches, count=len(matches), limit=LIMIT)
            records = data['entries' if tool=='query_journal' else 'matches']
            counts = marker_counts(records)
            excerpt = (('fixed nginx error.log + rotation; since=' if tool=='search_log' and arguments['source_id']=='nginx_error' else 'nginx.service journal; since=')+arguments['since']+
                       '; until='+arguments['until']+'; visible_count='+str(data['count'])+
                       '; literal_marker_counts='+json.dumps(counts,sort_keys=True)+
                       '; raw messages withheld; visibility/history completeness not established')
        observed = contracts.current_time()
        response = contracts.make_response(tool, arguments['trace_id'], arguments['logical_tool_call_id'],
            arguments['attempt_id'], data, arguments['session_id'], evidence=[dict(
                evidence_id='diag-'+secrets.token_hex(16), source=('collector.search_log.nginx_error.v1' if tool=='search_log' and arguments['source_id']=='nginx_error' else SOURCES[tool]), observed_at=observed,
                unit=UNIT if tool in ('query_journal','search_log') else None, excerpt=excerpt, redacted=True, truncated=False)],
            status='partial' if tool in ('query_journal','search_log') else 'ok')
        response.update(started_at=start, completed_at=contracts.current_time(),
                        duration_ms=int((time.monotonic()-tick)*1000))
        if not contracts.TOOLS[tool][4].is_valid(response):
            raise ValueError()
        return response
    except (FailedUnitsError, ListeningPortsError, JournalError, LogSearchError, SourceError, read_gateway.ReadError) as error:
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
    def cancel_once():
        if not owner.cancelling():
            owner.cancel()
    loop.add_signal_handler(signal.SIGTERM, cancel_once)
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
