"""SCRUM-25 Agent boundary for a pinned canonical MOCK; never detects on its own."""
from copy import deepcopy
import asyncio
from datetime import datetime
import json
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry
from referencing.exceptions import NoSuchResource

from agent.coordinator import Evidence, Observation
from agent.integration_boundary import TrustedTool
from agent.mcp_client import MCPClientError, ToolResult

TOOL = 'detect_anomaly'
META = ('schema_version', 'session_id', 'trace_id', 'logical_tool_call_id', 'attempt_id')
LIMIT = 65536


def _copy(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if len(raw.encode()) > LIMIT:
            raise MCPClientError('RESULT_TOO_LARGE')
        return json.loads(raw)
    except MCPClientError:
        raise
    except Exception:
        raise MCPClientError('MALFORMED_RESULT') from None


def _no_retrieve(uri):
    raise NoSuchResource(ref=uri)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _validator(schema):
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker(),
                               registry=Registry(retrieve=_no_retrieve))


def _time(value):
    if not value.endswith('Z'):
        raise ValueError()
    return datetime.fromisoformat(value[:-1] + '+00:00')


class AnomalyBinding:
    """Client facade below ToolBridge; caller retains Client/session cleanup owner.

    Schemas/redactor/client are trusted application configuration, never LLM input.
    Evidence ledger is a bounded sanitized view, not new server observations.
    """
    def __init__(self, client, input_schema, data_schema, result_schema, *, redact):
        try:
            self._input, data, result = map(_copy, (input_schema, data_schema, result_schema))
            if result['properties']['tool']['const'] != TOOL or not callable(redact):
                raise ValueError()
            self._input_validator = _validator(self._input)
            self._data_validator, self._result_validator = _validator(data), _validator(result)
            business = deepcopy(self._input)
            business['properties'] = {k:v for k,v in business['properties'].items() if k not in META}
            business['required'] = [k for k in business['required'] if k not in META]
            # Local demo policy narrows the canonical contract, not server authority.
            business['properties']['unit'] = {'const':'nginx.service'}
            business['required'].append('unit')
            business['properties']['max_findings']['maximum'] = 5
            self._business = business
            self._business_validator = _validator(business)
        except Exception:
            raise MCPClientError('INVALID_CONFIG') from None
        self.client, self.redact = client, redact
        self.session_id, self.trace_id = uuid4().hex, uuid4().hex
        self.correlation = None
        self.execution_state = 'not_dispatched'
        self.last_error = None
        self._result, self._request, self._ledger = None, None, {}
        self.local_redaction_applied = False

    @property
    def trusted_tool(self):
        return TrustedTool(TOOL, 'Read canonical mock anomaly findings; no Linux execution.',
                           deepcopy(self._business), True)

    @property
    def evidence(self):
        return deepcopy(self._ledger)

    @property
    def result(self):
        return deepcopy(self._result)

    def _fail(self, code):
        self.last_error = code
        raise MCPClientError(code) from None

    def _valid(self, validator, value):
        try:
            return validator.is_valid(value)
        except Exception:
            return False

    def _sanitize(self, value):
        if type(value) is str:
            cleaned = self.redact(value)
            if type(cleaned) is not str:
                raise ValueError()
            return cleaned
        if type(value) is list:
            return [self._sanitize(x) for x in value]
        if type(value) is dict:
            return {k:self._sanitize(v) for k,v in value.items()}
        return value

    async def disconnect(self):
        await self.client.disconnect()  # same SDK owner; no reconnect/retry

    async def call_tool(self, name, arguments):
        self.last_error, self._result = None, None
        self.execution_state = 'not_dispatched'
        if name != TOOL:
            self._fail('TOOL_NOT_ALLOWED')
        args = _copy(arguments)
        if not self._valid(self._business_validator, args):
            self._fail('TOOL_NOT_ALLOWED')
        try:
            if _time(args['since']) >= _time(args['until']) or self._sanitize(args) != args:
                raise ValueError()
        except Exception:
            self._fail('TOOL_NOT_ALLOWED')
        self.correlation = dict(schema_version='1.0.0', session_id=self.session_id,
            trace_id=self.trace_id, logical_tool_call_id=uuid4().hex, attempt_id=uuid4().hex)
        request = {**args, **self.correlation}
        request.setdefault('max_findings', 5)  # application default, never expands model input
        if not self._valid(self._input_validator, request):
            self._fail('MALFORMED_RESULT')
        self._request = request
        self.execution_state = 'unknown'
        try:
            response = await self.client.call_tool(TOOL, request)
        except asyncio.CancelledError:
            self.last_error = 'CANCELLED'
            raise
        except Exception as error:
            self.last_error = (error.code if isinstance(error, MCPClientError)
                               and error.code in {'TIMEOUT','DISCONNECTED','TRANSPORT_ERROR','PROTOCOL_ERROR'}
                               else 'TRANSPORT_ERROR')
            raise MCPClientError(self.last_error) from None
        self.execution_state = 'response_received'
        if type(response) is not ToolResult or type(response.is_error) is not bool:
            self._fail('MALFORMED_RESULT')
        _copy({'content':response.content, 'structuredContent':response.structured_content,
               'isError':response.is_error})
        envelope = _copy(response.structured_content)
        if (not self._valid(self._result_validator, envelope)
                or envelope['status'] == 'approval_required'
                or response.is_error != (envelope['status'] == 'error')):
            self._fail('MALFORMED_RESULT')
        try:
            if (type(response.content) is not tuple or len(response.content) != 1
                    or response.content[0].get('type') != 'text'
                    or json.loads(response.content[0]['text'], object_pairs_hook=_unique) != envelope):
                raise ValueError()
            placeholders = (envelope['status'] == 'error' and envelope['session_id'] is None
                            and all(envelope[k] == 'invalid-input' for k in META[2:])
                            and not envelope['evidence'])
            if not placeholders and any(envelope[k] != request[k] for k in META):
                raise ValueError()
            if _time(envelope['started_at']) > _time(envelope['completed_at']):
                raise ValueError()
            ids = set()
            for e in envelope['evidence']:
                if (e['evidence_id'] in ids or e['evidence_id'] in self._ledger
                        or len(e['evidence_id']) > 128 or len(e['source']) > 256
                        or not _time(args['since']) <= _time(e['observed_at']) < _time(args['until'])
                        or e['unit'] not in (None, 'nginx.service')):
                    raise ValueError()
                ids.add(e['evidence_id'])
            if envelope['status'] != 'error':
                if not self._valid(self._data_validator, envelope['data']):
                    raise ValueError()
                findings = envelope['data']['anomalies']
                if (len(findings) > request['max_findings']
                        or len({f['id'] for f in findings}) != len(findings)):
                    raise ValueError()
                for finding in envelope['data']['anomalies']:
                    if (any(ref not in ids for ref in finding['evidence_refs'])
                            or ('signals' in args and finding['type'] not in args['signals'])
                            or any(_time(finding['window'][k]) != _time(args[k]) for k in ('since','until'))):
                        raise ValueError()
        except Exception:
            self._fail('MALFORMED_RESULT')
        try:
            safe = _copy(self._sanitize(envelope))
            # Metadata/provenance are never rewritten to make redaction succeed.
            for k in (*META, 'started_at', 'completed_at'):
                if safe[k] != envelope[k]:
                    raise ValueError()
            for raw, cleaned in zip(envelope['evidence'], safe['evidence']):
                if any(raw[k] != cleaned[k] for k in raw if k != 'excerpt'):
                    raise ValueError()
            for raw, cleaned in zip(envelope['data'].get('anomalies', []),
                                    safe['data'].get('anomalies', [])):
                if any(raw[k] != cleaned[k] for k in raw if k != 'summary'):
                    raise ValueError()
            if not self._valid(self._result_validator, safe):
                raise ValueError()
            ledger = {**self._ledger, **{e['evidence_id']:e for e in safe['evidence']}}
            _copy(ledger)
        except Exception:
            self._fail('MALFORMED_RESULT')
        self.local_redaction_applied = safe != envelope
        self._ledger, self._result = ledger, safe
        self.last_error = safe['error_code'] if safe['status'] == 'error' else None
        return ToolResult(({'type':'text','text':json.dumps(safe,ensure_ascii=False)},),
                          deepcopy(safe), response.is_error)

    def interpret(self, message):
        """Project only received evidence; full provenance stays in evidence ledger."""
        try:
            envelope = json.loads(message.content)['structuredContent']
            if message.tool_name != TOOL or envelope != self._result:
                raise ValueError()
            data = envelope['data']
            limited = (envelope['status'] != 'ok' or envelope['truncated']
                       or not envelope['evidence'] or bool(data.get('insufficient_signals'))
                       or data.get('confidence') is None
                       or data.get('findings_truncated', False)
                       or any(not f['evidence_refs'] for f in data.get('anomalies', [])))
            findings = [{k:f[k] for k in ('id','type','severity','observed_count','evidence_refs')}
                        for f in data.get('anomalies', [])]
            return Observation(tuple(Evidence(e['evidence_id'],e['source'],e['excerpt'])
                for e in envelope['evidence']), limited,
                'UNTRUSTED canonical mock findings only; status=' + envelope['status']
                + '; error=' + str(envelope['error_code'])
                + '; confidence=' + str(data.get('confidence')) + '; findings='
                + json.dumps(findings, ensure_ascii=False))
        except Exception:
            self._fail('MALFORMED_RESULT')
