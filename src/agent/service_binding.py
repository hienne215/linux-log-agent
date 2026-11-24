"""Canonical read-only diagnostic boundary; transport and collectors stay unchanged."""
import asyncio
from copy import deepcopy
import json
import re
from pathlib import Path
from uuid import uuid4

from agent.diagnostics.journal_policy import window, parse_stamp, safe_message, UNIT, SOURCE_ID, LIMIT, MARKERS, marker_counts
from agent.diagnostics.service_verification import validate_snapshot
from agent.anomaly import META, _copy, _validator, _unique, _time
from agent.coordinator import Evidence, Observation
from agent.integration_boundary import TrustedTool
from agent.mcp_client import MCPClientError, ToolResult

TOOL = 'service_status'
SOURCE = 'collector.service_status.systemctl.v1'


def memory_snapshot(envelope):
    """Typed local meminfo projection; an evidence ID alone is insufficient."""
    items=[e for e in envelope['evidence'] if e['source']=='collector.memory_oom_events.meminfo.v1']
    if len(items)!=1:raise ValueError('INVALID_MEMORY_SNAPSHOT')
    memory=json.loads(items[0]['excerpt'],object_pairs_hook=_unique)
    if (type(memory) is not dict or set(memory)!={'MemTotal','MemAvailable'}
            or any(type(v) is not int for v in memory.values())
            or not 0 < memory['MemTotal'] < 2**64
            or not 0 <= memory['MemAvailable'] <= memory['MemTotal']):
        raise ValueError('INVALID_MEMORY_SNAPSHOT')
    return memory


def inode_snapshot(envelope):
    """v1 data has only a flag; typed counts remain in the original evidence."""
    for e in envelope.get('evidence',[]):
        match=re.search(r'\binodes_total=([0-9]+) inodes_free=([0-9]+) inodes_available=([0-9]+)\b',e['excerpt'])
        if match:
            total,free,available=map(int,match.groups())
            if not 0<=available<=free<=total<2**64:raise ValueError()
            return dict(total=total,free=free,available=available,used_percent=(total-free)*100/total if total else None)
    return None
BUSINESS = {'type': 'object', 'properties': {'unit': {'type': 'string', 'enum': ['nginx.service'], 'const': 'nginx.service'}},
            'required': ['unit'], 'additionalProperties': False}
SERVICE_BUSINESS=deepcopy(BUSINESS)
SERVICE_BUSINESS['properties']['unit']={'type':'string','enum':['nginx.service','ssh.service','cron.service']}


POLICIES = {
    'memory_oom_events': (('collector.memory_oom_events.journal.kernel.v1','collector.memory_oom_events.meminfo.v1'),
        {'type':'object','properties':{},'additionalProperties':False},
        'Read system-wide kernel OOM records and current RAM, not just nginx. No arguments. App owns a bounded 15-minute window. Source coverage/permission failures are explicit; zero visible records does not prove no OOM.'),
    'failed_logins': (('collector.failed_logins.journal.auth.v1','collector.failed_logins.auth.log.v1','collector.failed_logins.coverage.v1'),
        {'type':'object','properties':{},'additionalProperties':False},
        'Read system-wide failed SSH/login/sudo/su authentication records, not just nginx. No arguments. App owns a bounded 15-minute window and fallback sources. Identities/IPs pseudonymized; partial visibility, not attack attribution.'),
    'check_config': ('collector.check_config.nginx.v1',
        {'type':'object','properties':{'profile':{'type':'string','const':'nginx','enum':['nginx']},
            'unit':{'type':'string','const':'nginx.service','enum':['nginx.service']}},
         'required':['profile'],'additionalProperties':False},
        'Check nginx configuration syntax with a fixed nginx -t in a read-only filesystem/process/network sandbox. Required profile nginx. No command/path/options accepted. Permission failure is unknown, not invalid config. Never reloads or restarts.'),
    'service_status': (SOURCE, SERVICE_BUSINESS, 'Read current systemd state for an application-allowlisted service. Required unit exactly nginx.service, ssh.service or cron.service. No arbitrary unit/command, repair or HTTP health inference.'),
    'disk_usage': ('collector.disk_usage.statvfs.v1', {
        'type': 'object', 'properties': {'filesystem': {'type': 'string', 'enum': ['root'], 'const': 'root'}},
        'required': ['filesystem'], 'additionalProperties': False},
        'Read disk usage and available bytes. Required filesystem is the logical identifier root, not a path. No file reads or repair.'),
    'list_failed_units': ('collector.list_failed_units.systemctl.v1', {
        'type': 'object', 'properties': {}, 'additionalProperties': False},
        'Read a bounded complete snapshot of failed systemd units. No arguments: pass an empty object. Descriptions withheld. No repair.'),
    'listening_ports': ('collector.listening_ports.ss.v1', {
        'type': 'object', 'properties': {'protocol': {'type': 'string', 'enum': ['tcp','udp']},
            'port': {'type': 'integer', 'minimum':1,'maximum':65535}},
        'additionalProperties': False},
        'Read system-wide listening TCP/UDP sockets. Optional protocol tcp or udp, optional port integer 1..65535; omit filters to inspect all bounded sockets. No unit argument or process/service attribution or reachability claim.'),
    'query_journal': ('collector.query_journal.journalctl.v1', {
        'type': 'object', 'properties': {'unit': {'type': 'string', 'enum': ['nginx.service'], 'const': 'nginx.service'},
            'priority':{'type':'integer','minimum':0,'maximum':7},'lookback_minutes':{'type':'integer','minimum':1,'maximum':1440}},
        'required': ['unit'], 'additionalProperties': False},
        'Read bounded nginx.service journal records. Required unit nginx.service; optional priority 0..7 (default error threshold 3), optional lookback_minutes 1..1440 (default 15). App generates canonical time/limit. Raw log text withheld; literal classifications only. Partial visibility, not root-cause proof.'),
    'search_log': (('collector.search_log.nginx_journal.v1','collector.search_log.nginx_error.v1'), {
        'type': 'object', 'properties': {'source_id': {'type': 'string', 'enum': ['nginx_journal','nginx_error']},
            'query': {'type': 'string', 'minLength':1,'maxLength':128},
            'lookback_minutes':{'type':'integer','minimum':1,'maximum':1440}},
        'required': ['source_id', 'query'], 'additionalProperties': False},
        'Case-sensitive bounded literal search in recent nginx journal or fixed nginx error.log/current+rotation. Required source_id nginx_journal or nginx_error, and query string 1..128 characters. No arbitrary file/path/regex. App owns time, redaction and caps; partial visibility.'),

}


class CanonicalReadBinding:
    """Application IDs and authorization; caller retains SDK cleanup ownership.

    Reuses existing bounded JSON/schema utilities. Full sanitized canonical
    provenance stays in the ledger; Observation is only an untrusted projection.
    """
    def __init__(self, client, root, *, redact, tool):
        if tool not in POLICIES:
            raise ValueError('TOOL_NOT_ALLOWED')
        self.tool = tool
        self.source, business, self.description = POLICIES[tool]
        self.business = deepcopy(business)
        self.client, self.redact = client, redact
        folder = Path(root) / 'schemas/tools'
        self.schemas = tuple(json.loads((folder / (self.tool + suffix)).read_text())
                             for suffix in ('.schema.json', '.response.schema.json', '.result.schema.json'))
        self.validators = tuple(_validator(s) for s in self.schemas)
        self.business_validator = _validator(self.business)
        self.session_id, self.trace_id = uuid4().hex, uuid4().hex
        self.correlation = None
        self.execution_state = 'not_dispatched'
        self.last_error = None
        self._used = False
        self._result = None
        self._ledger = {}
        self.window = None

    @property
    def trusted_tool(self):
        return TrustedTool(self.tool, self.description, deepcopy(self.business), True)

    @property
    def evidence(self):
        return deepcopy(self._ledger)

    def fail(self, code):
        self.last_error = code
        raise MCPClientError(code) from None

    async def disconnect(self):
        await self.client.disconnect()

    async def call_tool(self, name, arguments):
        if name != self.tool or not self.business_validator.is_valid(arguments):
            self.fail('TOOL_NOT_ALLOWED')
        try:
            encoded=json.dumps(arguments,ensure_ascii=False)
            if self.redact(encoded)!=encoded:raise ValueError()
        except Exception:
            self.fail('TOOL_NOT_ALLOWED')  # No credential-bearing query crosses transport.
        if self._used:
            self.fail('TOOL_NOT_ALLOWED')
        self.correlation = dict(schema_version='1.0.0', session_id=self.session_id,
                                trace_id=self.trace_id, logical_tool_call_id=uuid4().hex,
                                attempt_id=uuid4().hex)
        request = {**_copy(arguments), **self.correlation}
        if self.tool == 'disk_usage':
            request['include_inodes'] = True  # Application-owned optional value, never model-owned.
        if self.tool == 'list_failed_units':
            request['limit'] = 100
        if self.tool in ('query_journal', 'search_log','memory_oom_events','failed_logins'):
            lookback=request.pop('lookback_minutes',None)
            self.window = window(lookback) if lookback is not None else self.window or window()
            request.update(self.window, limit=LIMIT)
            if self.tool == 'query_journal':
                request.setdefault('priority',3)
        self.validators[0].validate(request)
        self._used = True
        self.execution_state = 'unknown'
        try:
            result = await self.client.call_tool(name, request)
        except asyncio.CancelledError:
            self.last_error = 'CANCELLED'
            raise
        except MCPClientError as error:
            self.last_error = error.code
            raise
        except Exception:
            self.fail('TRANSPORT_ERROR')
        try:
            if type(result) is not ToolResult or type(result.is_error) is not bool:
                raise ValueError()
            _copy({'content': result.content, 'structuredContent': result.structured_content,
                   'isError': result.is_error})
            envelope = _copy(result.structured_content)
            self.validators[2].validate(envelope)
            if envelope['tool'] != self.tool or envelope['status'] == 'approval_required':
                raise ValueError()
            if result.is_error != (envelope['status'] == 'error'):
                raise ValueError()
            if (type(result.content) is not tuple or len(result.content) != 1
                    or result.content[0].get('type') != 'text'
                    or json.loads(result.content[0]['text'], object_pairs_hook=_unique) != envelope):
                raise ValueError()
            # Canonical rejected-input errors use placeholders, never real keys.
            placeholder = (envelope['status'] == 'error' and envelope['session_id'] is None
                           and all(envelope[k] == 'invalid-input' for k in META[2:])
                           and not envelope['evidence'])
            if not placeholder and any(envelope[k] != request[k] for k in META):
                raise ValueError()
            start, end = _time(envelope['started_at']), _time(envelope['completed_at'])
            if start > end:
                raise ValueError()
            if envelope['status'] != 'error':
                self.validators[1].validate(envelope['data'])
                data = envelope['data']
                if self.tool == TOOL:
                    validate_snapshot(envelope,request['unit'])
                elif self.tool == 'disk_usage' and (data['filesystem'] != 'root' or data['mount_point'] != '/'
                        or data['inodes_included'] is not True):
                    raise ValueError()
                if self.tool=='disk_usage':inode_snapshot(envelope)
                if self.tool == 'list_failed_units':
                    if (data['limit'] != 100 or data['count'] != len(data['failed_units'])
                            or envelope['next_cursor'] is not None
                            or len({r['unit'] for r in data['failed_units']}) != data['count']
                            or any(r['description'] != '[REDACTED]' for r in data['failed_units'])):
                        raise ValueError()
                if self.tool == 'listening_ports':
                    if (data['count'] != len(data['ports']) or envelope['next_cursor'] is not None
                            or any((request.get('protocol') is not None and r['protocol'] != request['protocol'])
                                   or (request.get('port') is not None and r['port'] != request['port']) or r['process'] != ''
                                   or r['unit'] is not None
                                   or r['address'] not in {'0.0.0.0', '::', '*', '127.0.0.1', '::1', '[REDACTED]'}
                                   for r in data['ports'])):
                        raise ValueError()
                if self.tool in ('query_journal', 'search_log'):
                    records = data['entries' if self.tool=='query_journal' else 'matches']
                    if (envelope['status']!='partial' or data['limit']!=LIMIT
                            or data['count']!=len(records) or data['count']>LIMIT
                            or envelope['next_cursor'] is not None or envelope['truncated']):
                        raise ValueError()
                    if self.tool=='search_log' and (data['source_id']!=request['source_id'] or data['query']!=request['query']
                            or any(e['source']!='collector.search_log.'+request['source_id']+'.v1' for e in envelope['evidence'])):
                        raise ValueError()
                    for entry in records:
                        if (not safe_message(entry['message'])
                                or not parse_stamp(request['since'])<=parse_stamp(entry['timestamp'])<=parse_stamp(request['until'])
                                or (self.tool=='query_journal' and entry['priority']>request['priority'])):
                            raise ValueError()
                if self.tool in ('memory_oom_events','failed_logins'):
                    events=data['events'];known={e['evidence_id']:e for e in envelope['evidence']}
                    if self.tool=='memory_oom_events':
                        memory_snapshot(envelope)
                        if len(envelope['evidence'])!=len(events)+1:raise ValueError()
                    if (envelope['status']!='partial' or data['count']!=len(events) or data['limit']!=LIMIT
                            or envelope['next_cursor'] is not None or envelope['truncated']
                            or len(events)>LIMIT or len({e['evidence_id'] for e in events})!=len(events)
                            or len({c['source'] for c in data['coverage']})!=len(data['coverage'])):
                        raise ValueError()
                    for event in events:
                        ev=known.get(event['evidence_id'])
                        if (ev is None or ev['observed_at']!=event['timestamp']
                                or not parse_stamp(request['since'])<=parse_stamp(event['timestamp'])<parse_stamp(request['until'])
                                or json.loads(ev['excerpt'])!={k:v for k,v in event.items() if k!='evidence_id'}):
                            raise ValueError()
                        coverage={c['source']:c['status'] for c in data['coverage']}
                        origin=('auth.log' if ev['source']=='collector.failed_logins.auth.log.v1' else
                            'journal.auth' if self.tool=='failed_logins' else 'journal.kernel')
                        if coverage.get(origin)!='ok':raise ValueError()
                        if self.tool=='failed_logins':
                            import re
                            if (event['user']!='root' and not re.fullmatch(r'user-[a-f0-9]{24}',event['user'])) or (event['source_ip'] is not None and not re.fullmatch(r'ip-[a-f0-9]{24}',event['source_ip'])):
                                raise ValueError()
                            if event['message']!='Authentication failure observed; identity and address pseudonymized for this run.':raise ValueError()
                        elif event['process'] is not None:
                            import re
                            from agent.diagnostics.system_diagnostics import KNOWN_PROCESSES
                            if event['process'] not in KNOWN_PROCESSES and not re.fullmatch(r'process-[a-f0-9]{24}',event['process']):raise ValueError()
                        if self.tool=='memory_oom_events' and event['message'] not in (
                            'Kernel reported a process killed by the OOM killer.',
                            'Kernel OOM diagnostic record; no process kill inferred from this record.'):raise ValueError()
                if self.tool=='check_config' and (data['profile']!='nginx' or data['unit']!='nginx.service'
                        or (data['valid'] and data['issues'])):
                    raise ValueError()
                if self.tool=='check_config':
                    message=('Nginx configuration syntax check succeeded in read-only sandbox; no reload or health assertion.' if data['valid'] else
                        'Nginx configuration validation failed; no files or services changed.')
                    if data['message']!=message or any(e['excerpt']!=message for e in envelope['evidence']):raise ValueError()
                if not envelope['evidence']:
                    raise ValueError()
            ids = set()
            for e in envelope['evidence']:
                historical=self.tool in ('memory_oom_events','failed_logins') and any(e['evidence_id']==event['evidence_id'] for event in envelope['data'].get('events',[]))
                valid_time=(parse_stamp(request['since'])<=_time(e['observed_at'])<parse_stamp(request['until']) if historical else start<=_time(e['observed_at'])<=end)
                if (e['evidence_id'] in ids or len(e['evidence_id']) > 128
                        or e['source'] not in (self.source if isinstance(self.source,tuple) else (self.source,))
                        or e['unit'] != (request['unit'] if self.tool==TOOL else UNIT if self.tool in ('query_journal','search_log','check_config') else None)
                        or e['redacted'] is not True or e['truncated'] or not valid_time):
                    raise ValueError()
                ids.add(e['evidence_id'])
            safe = deepcopy(envelope)
            # Only free text can change; IDs, source, time and flags stay exact.
            for e in safe['evidence']:
                e['excerpt'] = self.redact(e['excerpt'])
            if safe['status'] != 'error' and self.tool == TOOL:
                safe['data']['description'] = self.redact(safe['data']['description'])
            self.validators[2].validate(safe)
            _copy(safe)
            if self.redact(json.dumps(safe, ensure_ascii=False)) != json.dumps(safe, ensure_ascii=False):
                raise ValueError()
        except Exception:
            self.fail('MALFORMED_RESULT')
        self._result = safe
        self._ledger = {e['evidence_id']: e for e in safe['evidence']}
        if safe['status'] == 'error':
            self.execution_state = 'unknown' if safe['error_code'] in {'EXECUTION_UNKNOWN', 'TIMEOUT'} else 'response_received'
            self.fail(safe['error_code'])
        self.execution_state = 'response_received'
        return ToolResult(({'type': 'text', 'text': json.dumps(safe, ensure_ascii=False)},), deepcopy(safe), False)

    def interpret(self, message):
        try:
            envelope = json.loads(message.content)['structuredContent']
            if message.tool_name != self.tool or envelope != self._result or message.is_error:
                raise ValueError()
            projected=envelope['evidence']
            if self.tool in ('memory_oom_events','failed_logins'):
                events=envelope['data']['events'][:3]
                sampled={x['evidence_id'] for x in events}
                all_events={x['evidence_id'] for x in envelope['data']['events']}
                projected=[e for e in projected if e['evidence_id'] in sampled or e['evidence_id'] not in all_events]
            evidence = tuple(Evidence(e['evidence_id'], e['source'], json.dumps(
                {k: v for k, v in e.items() if k not in ('evidence_id', 'source')}, ensure_ascii=False))
                for e in projected)
            data = envelope['data']
            if self.tool=='disk_usage':data={**data,'inode_snapshot':inode_snapshot(envelope)}
            if self.tool in ('memory_oom_events','failed_logins'):
                data={**data,'events':data['events'][:3],'event_sample_limited':len(data['events'])>3,
                    'ledger_event_count':data['count'],'coverage_complete':False}
            if self.tool in ('query_journal','search_log'):
                records = data['entries' if self.tool=='query_journal' else 'matches']
                data = {'count': data['count'], 'limit': data['limit'],
                        'literal_marker_counts': marker_counts(records),
                        'raw_text_withheld': True, 'history_complete': False,
                        'window': deepcopy(self.window)}
                if self.tool=='search_log':
                    data.update(source_id=envelope['data']['source_id'], query=envelope['data']['query'])
            return Observation(evidence, envelope['status'] != 'ok' or envelope['truncated']
                               or any(e['truncated'] for e in envelope['evidence']),
                               json.dumps({'tool': self.tool, 'status': envelope['status'], 'data': data}, ensure_ascii=False))
        except Exception:
            self.fail('MALFORMED_RESULT')


class ServiceBinding(CanonicalReadBinding):
    def __init__(self, client, root, *, redact):
        super().__init__(client, root, redact=redact, tool='service_status')


class DiskBinding(CanonicalReadBinding):
    def __init__(self, client, root, *, redact):
        super().__init__(client, root, redact=redact, tool='disk_usage')


class DiagnosticBinding(CanonicalReadBinding):
    def __init__(self, client, root, *, redact, tool):
        if tool not in ('list_failed_units', 'listening_ports', 'query_journal', 'search_log','memory_oom_events','failed_logins','check_config'):
            raise ValueError('TOOL_NOT_ALLOWED')
        super().__init__(client, root, redact=redact, tool=tool)
