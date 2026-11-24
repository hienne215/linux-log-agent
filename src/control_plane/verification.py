"""Fresh read-only checks after an observed action; never dispatches an action.

Uses the existing canonical binding, SDK owner and Incident Report renderer.
Helper job success and current health are separate observations. Incomplete
log coverage never becomes verified remediation, even when HTTP returns 200.
"""
import asyncio
from datetime import datetime, timezone
import json
import os
import re
import stat

from .approval import ApprovalError

SNAPSHOT_PREFIX='# Canonical read-only evidence snapshot\n\nUntrusted data; not proof of a verified fix.\n\n```json\n'


def baseline(root, view, schemas, redact):
    """Load only our private, bounded, non-followed original canonical ledger."""
    from agent.verify_fix import _validator, _unique, CAP
    from agent.diagnostics.service_verification import validate_snapshot
    directory=reports=descriptor=None
    try:
        if re.fullmatch('[0-9a-f]{32}',view.trace_id) is None:raise ValueError()
        directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        reports=os.open('reports',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
        for fd in (directory,reports):
            info=os.fstat(fd)
            if info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError()
        descriptor=os.open('proposal-'+view.trace_id+'.md',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=reports)
        info=os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid()
                or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>70000):raise ValueError()
        text=os.read(descriptor,70001).decode('utf-8')
        if not text.startswith(SNAPSHOT_PREFIX) or not text.endswith('\n```\n'):raise ValueError()
        payload=text[len(SNAPSHOT_PREFIX):-len('\n```\n')]
        if len(payload.encode())>CAP:raise ValueError()
        result=json.loads(payload,object_pairs_hook=_unique)
        _validator(schemas).validate(result)
        if (result['status']!='ok' or result['session_id']!=view.session_id
                or result['trace_id']!=view.trace_id or result['tool']!='service_status'
                or result['truncated'] or result['next_cursor'] is not None
                or [e['evidence_id'] for e in result['evidence']]!=list(view.evidence_ids)
                or json.loads(redact(json.dumps(result,ensure_ascii=False)))!=result):raise ValueError()
        validate_snapshot(result,'nginx.service')
        return result
    except Exception:raise ApprovalError('INVALID_BASELINE') from None
    finally:
        for fd in (descriptor,reports,directory):
            if fd is not None:os.close(fd)


async def fresh_checks(client, root, view, before, *, since, redact):
    """One SDK connection/call. Repeated cancellation retains the same owner."""
    from agent.diagnostics.health_binding import HealthBinding
    from agent.mcp_client import MCPClientError
    binding=HealthBinding(client,root,redact=redact,baseline_evidence=before['evidence'],
                          action_id=view.action_id,since=since)
    binding.session_id,binding.trace_id=view.session_id,view.trace_id
    cancelled=False
    failure=None
    try:
        await client.connect()
        page=await client.list_tools()
        tools=[tool for tool in page.tools if tool['name']=='verify_fix']
        if (page.next_cursor is not None or len(tools)!=1
                or tools[0].get('inputSchema')!=binding.schemas[0]
                or tools[0].get('outputSchema')!=binding.schemas[2]):
            raise ApprovalError('INVALID_REGISTRY')
        await binding.call_tool('verify_fix',{'unit':'nginx.service'})
    except asyncio.CancelledError:cancelled=True
    except Exception as error:failure=error
    finally:
        while client._owner is not None:
            try:await client.disconnect()
            except asyncio.CancelledError:cancelled=True
            except MCPClientError as error:
                if error.code!='CLEANUP_TIMEOUT' and failure is None:failure=error
    if cancelled:raise asyncio.CancelledError()
    if failure is not None:raise failure from None
    return binding.result


def incident(view, attempt, before, after, *, redact):
    """Validated binding results only; render both ledgers, no success inference."""
    from agent.coordinator import Evidence,Report
    from agent.incident_report import render_markdown
    from agent.verify_fix import _utc
    try:
        # Defense in depth for caller mistakes. Full validation belongs to the
        # canonical binding, including correlation, checks and baseline refs.
        if (attempt['cleanup_pending'] or after['data']['action_id']!=view.action_id
                or after['session_id']!=view.session_id or after['trace_id']!=view.trace_id
                or after['data']['baseline_evidence_refs']!=list(view.evidence_ids)
                or after['status']!='partial' or after['data']['overall_result']=='pass'):
            raise ValueError()
        items=before['evidence']+after['evidence']
        if len({item['evidence_id'] for item in items})!=len(items):raise ValueError()
        for item in after['evidence']:
            if _utc(item['observed_at'])<_utc(after['started_at']):raise ValueError()
        ledger=tuple(Evidence(item['evidence_id'],item['source'],json.dumps(
            {k:v for k,v in item.items() if k not in ('evidence_id','source')},ensure_ascii=False)) for item in items)
        report=Report('limited',evidence=ledger,citations=tuple(e.id for e in ledger),
                      tool_calls=1,execution_state='response_received')
        body=render_markdown(report,redact=lambda text:redact(text))
        # Only validated enum fields/hex IDs are interpolated; untrusted excerpts
        # are handled exclusively by the existing escaped renderer above.
        context=json.dumps(dict(action_id=view.action_id,session_id=view.session_id,trace_id=view.trace_id,
            job_outcome=attempt['outcome'],executed=attempt['executed'],
            check_result=after['data']['overall_result'],
            canonical_correlation={key:after[key] for key in ('schema_version','session_id','trace_id',
                'logical_tool_call_id','attempt_id')},
            baseline_evidence_refs=after['data']['baseline_evidence_refs'],
            checks=[{key:check[key] for key in ('check_type','result','evidence_refs')}
                    for check in after['data']['checks']]),ensure_ascii=True)
        context=context.replace('`',r'\u0060').replace('<',r'\u003c').replace('>',r'\u003e')
        return ('# Kiểm tra sau hành động — chỉ đọc\n\n'
            'Kết quả job không chứng minh dịch vụ đã được sửa. Unknown/partial vẫn chưa xác minh.\n\n'
            'Fresh checks: service state, HTTP HEAD loopback và lỗi journal trong cửa sổ mới. '
            'Thiếu quyền/độ phủ journal không được coi là không có lỗi. Không retry hoặc restart.\n\n'
            '```json\n'+context+'\n```\n\n'+body)
    except Exception:raise ApprovalError('INVALID_VERIFICATION') from None


def now():
    # Taken only after receiving helper status; fresh evidence must follow this
    # local observation. No wall-clock timestamp is invented for the systemd job.
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
