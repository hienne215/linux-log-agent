"""Canonical read-only diagnostic boundary; transport and collectors stay unchanged."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

from agent.anomaly import META, _copy, _validator, _unique, _time
from agent.coordinator import Evidence, Observation
from agent.integration_boundary import TrustedTool
from agent.mcp_client import MCPClientError, ToolResult

TOOL = 'service_status'
SOURCE = 'collector.service_status.systemctl.v1'
BUSINESS = {'type': 'object', 'properties': {'unit': {'const': 'nginx.service'}},
            'required': ['unit'], 'additionalProperties': False}


POLICIES = {
    'service_status': (SOURCE, BUSINESS, 'Read current systemd state of nginx.service only. No repair.'),
    'disk_usage': ('collector.disk_usage.statvfs.v1', {
        'type': 'object', 'properties': {'filesystem': {'const': 'root'}},
        'required': ['filesystem'], 'additionalProperties': False},
        'Read disk usage and available bytes of the root filesystem only. No file reads or repair.'),
    'list_failed_units': ('collector.list_failed_units.systemctl.v1', {
        'type': 'object', 'properties': {}, 'additionalProperties': False},
        'Read a bounded complete snapshot of failed systemd units. Descriptions withheld. No repair.'),
    'listening_ports': ('collector.listening_ports.ss.v1', {
        'type': 'object', 'properties': {'protocol': {'const': 'tcp'}, 'port': {'const': 80}},
        'required': ['protocol', 'port'], 'additionalProperties': False},
        'Read listening TCP port 80 sockets. No process/service attribution or remote reachability claim.'),
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
                    if data['unit'] != 'nginx.service':
                        raise ValueError()
                elif self.tool == 'disk_usage' and (data['filesystem'] != 'root' or data['mount_point'] != '/'
                        or data['inodes_included'] is not True):
                    raise ValueError()
                if self.tool == 'list_failed_units':
                    if (data['limit'] != 100 or data['count'] != len(data['failed_units'])
                            or envelope['next_cursor'] is not None
                            or len({r['unit'] for r in data['failed_units']}) != data['count']
                            or any(r['description'] != '[REDACTED]' for r in data['failed_units'])):
                        raise ValueError()
                if self.tool == 'listening_ports':
                    if (data['count'] != len(data['ports']) or envelope['next_cursor'] is not None
                            or any(r['protocol'] != 'tcp' or r['port'] != 80 or r['process'] != ''
                                   or r['unit'] is not None
                                   or r['address'] not in {'0.0.0.0', '::', '*', '127.0.0.1', '::1', '[REDACTED]'}
                                   for r in data['ports'])):
                        raise ValueError()
                if not envelope['evidence']:
                    raise ValueError()
            ids = set()
            for e in envelope['evidence']:
                if (e['evidence_id'] in ids or len(e['evidence_id']) > 128
                        or e['source'] != self.source or e['unit'] != ('nginx.service' if self.tool == TOOL else None)
                        or not start <= _time(e['observed_at']) <= end):
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
            evidence = tuple(Evidence(e['evidence_id'], e['source'], json.dumps(
                {k: v for k, v in e.items() if k not in ('evidence_id', 'source')}, ensure_ascii=False))
                for e in envelope['evidence'])
            return Observation(evidence, envelope['status'] != 'ok' or envelope['truncated']
                               or any(e['truncated'] for e in envelope['evidence']),
                               json.dumps({'status': envelope['status'], 'data': envelope['data']}, ensure_ascii=False))
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
        if tool not in ('list_failed_units', 'listening_ports'):
            raise ValueError('TOOL_NOT_ALLOWED')
        super().__init__(client, root, redact=redact, tool=tool)
