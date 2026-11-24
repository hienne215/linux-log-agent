"""One fresh read-only service check. Never evidence of remediation or HTTP health."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
import time

from agent.diagnostics.journal_policy import UNIT, parse_stamp
from agent.diagnostics.service_snapshot import parse_snapshot, format_snapshot

SOURCE = 'collector.service_status.systemctl.v1'
CHECKS = ['service_active']
EXCERPT = re.compile(r'unit=nginx\.service; load=([a-z-]+); active=([a-z-]+); sub=([a-z-]+)')
# This application profile admits a fixed state vocabulary, not arbitrary peer
# strings. Future collector states require explicit review instead of widening it.
LOAD = frozenset(('loaded', 'masked', 'not-found', 'error', 'bad-setting', 'stub', 'merged'))
ACTIVE = frozenset(('active', 'inactive', 'failed', 'activating', 'deactivating',
                    'reloading', 'maintenance', 'refreshing'))
SUB = frozenset(('dead', 'running', 'exited', 'failed', 'start-pre', 'start', 'start-post',
    'reload', 'reload-signal', 'reload-notify', 'stop', 'stop-watchdog', 'stop-sigterm',
    'stop-sigkill', 'stop-post', 'final-watchdog', 'final-sigterm', 'final-sigkill',
    'auto-restart', 'auto-restart-queued', 'cleaning'))


def validate_snapshot(response, unit):
    """Check the fixed collector profile after canonical schema validation.

    This establishes internal consistency, not authentication of systemd or
    semantic correctness of a later model conclusion. The caller owns allowlists.
    """
    data, evidence = response['data'], response['evidence']
    if (data['unit'] != unit or data['load_state'] not in LOAD
            or data['active_state'] not in ACTIVE or data['sub_state'] not in SUB
            or len(evidence) != 1 or response['next_cursor'] is not None):
        raise ValueError('INVALID_SERVICE_SNAPSHOT')
    item = evidence[0]
    observed_data, details = parse_snapshot(item['excerpt'],unit)
    if any(observed_data[key] != data[key] for key in observed_data):
        raise ValueError('INVALID_SERVICE_SNAPSHOT')
    expected = format_snapshot(data,details)
    start, observed, end = map(parse_stamp, (response['started_at'], item['observed_at'], response['completed_at']))
    if (item['source'] != SOURCE or item['unit'] != unit or item['redacted'] is not True
            or item['truncated'] or item['excerpt'] != expected or not start <= observed <= end):
        raise ValueError('INVALID_SERVICE_SNAPSHOT')
    return item


def authorized(arguments):
    try:
        # until=null means a fresh read after dispatch, not the earlier snapshot
        # endpoint. Allow the existing 1-minute startup tolerance on app's since.
        now=datetime.now(timezone.utc)
        since=parse_stamp(arguments['since'])
        recent=now-timedelta(minutes=16)<=since<=now
    except (ValueError,TypeError,KeyError,OverflowError):
        return False
    return (arguments.get('unit') == UNIT and arguments.get('check_types') == CHECKS
            and arguments.get('until', 'absent') is None
            and arguments.get('action_id', 'absent') is None
            and arguments.get('baseline_evidence_refs') == [] and recent)


def assess(excerpt, *, action_context=False):
    data, _ = parse_snapshot(excerpt,UNIT)
    load, active, sub = (data[key] for key in ('load_state','active_state','sub_state'))
    if load not in LOAD or active not in ACTIVE or sub not in SUB:
        raise ValueError()
    result = ('pass' if (load, active, sub) == ('loaded', 'active', 'running') else
              'fail' if load == 'loaded' and active in ('inactive', 'failed') else 'unknown')
    context=('action/baseline are caller context, not proof of execution' if action_context
             else 'no action or baseline supplied')
    summary = (f'Read-only service_active={result}; load={load}; active={active}; sub={sub}; '
               +context+'; no remediation or HTTP health verified')
    return result, summary


async def collect(arguments):
    # Collector imports belong only in the isolated worker. CLI imports must work
    # from any directory with just the application's src on PYTHONPATH.
    import mock_mcp_server as contracts
    from src.mcp_server import service_status as service
    start, tick = contracts.current_time(), time.monotonic()
    request = {k: arguments[k] for k in ('schema_version', 'session_id', 'trace_id',
                                       'logical_tool_call_id', 'attempt_id', 'unit')}
    # Reuse the collector's fixed argv, timeout, privacy and child ownership.
    response = await service.collect(request)
    if (not contracts.TOOLS['service_status'][4].is_valid(response)
            or response['status'] not in ('ok', 'error')):
        raise ValueError()
    if response['status'] == 'error':
        return contracts.safe_error('verify_fix', {}, response['error_code'], rule='service_check')
    if (any(response[k] != request[k] for k in request if k != 'unit')
            or response['truncated'] or response['next_cursor'] is not None):
        raise ValueError()
    item = deepcopy(validate_snapshot(response, UNIT))
    evidence = [item]
    result, summary = assess(item['excerpt'])
    data = dict(action_id=None, unit=UNIT, overall_result=result,
                checks=[dict(check_type='service_active', result=result, summary=summary,
                             evidence_refs=[item['evidence_id']])], baseline_evidence_refs=[],
                verified_at=item['observed_at'])
    envelope = contracts.make_response('verify_fix', arguments['trace_id'], arguments['logical_tool_call_id'],
        arguments['attempt_id'], data, arguments['session_id'], status='partial', evidence=evidence)
    envelope.update(started_at=start, completed_at=contracts.current_time(),
                    duration_ms=int((time.monotonic()-tick)*1000))
    return envelope
