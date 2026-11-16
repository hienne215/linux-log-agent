"""Read-only canonical mock verification boundary; no remediation or polling."""
import asyncio
from copy import deepcopy
from datetime import datetime
import json
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry
from referencing.exceptions import NoSuchResource

from agent.coordinator import Evidence, Observation
from agent.integration_boundary import TrustedTool
from agent.mcp_client import MCPClientError, ToolResult

TOOL = 'verify_fix'
META = ('schema_version', 'session_id', 'trace_id', 'logical_tool_call_id', 'attempt_id')
CAP = 65536


def _json_copy(value):
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if len(text.encode('utf-8')) > CAP:
            raise MCPClientError('RESULT_TOO_LARGE')
        return json.loads(text)
    except MCPClientError:
        raise
    except Exception:
        raise MCPClientError('MALFORMED_RESULT') from None


def _no_retrieve(uri):
    raise NoSuchResource(ref=uri)


def _validator(schema):
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker(),
                               registry=Registry(retrieve=_no_retrieve))


def _utc(value):
    if not value.endswith('Z'):
        raise ValueError()
    return datetime.fromisoformat(value[:-1] + '+00:00')


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


class VerifyFixBinding:
    """Client facade below existing ToolBridge; caller retains SDK cleanup owner.

    Schemas, redactor, baseline evidence and action IDs are application-owned.
    Baseline records must already be reviewed/redacted; immutable provenance is
    never rewritten. A pass is a received check result, not proof of remediation.
    """
    def __init__(self, client, input_schema, data_schema, result_schema, *, redact,
                 baseline_evidence=(), action_ids=frozenset()):
        try:
            request, data, result = map(_json_copy, (input_schema, data_schema, result_schema))
            if result['properties']['tool']['const'] != TOOL or not callable(redact):
                raise ValueError()
            self._input, self._data, self._output = map(_validator, (request, data, result))
            self._evidence_validator = _validator(result['properties']['evidence']['items'])
            business = deepcopy(request)
            business['properties'] = {k:v for k,v in business['properties'].items() if k not in META}
            business['required'] = [k for k in business['required'] if k not in META]
            business['properties']['unit'] = {'const':'nginx.service'}
            self._business_schema, self._business = business, _validator(business)
            self.client, self.redact = client, redact
            self._baseline = {}
            for evidence in _json_copy(list(baseline_evidence)):
                self._evidence_validator.validate(evidence)
                if (evidence['unit'] not in (None, 'nginx.service')
                        or evidence['evidence_id'] in self._baseline
                        or len(evidence['evidence_id']) > 128 or len(evidence['source']) > 256
                        or self._sanitize(evidence) != evidence):
                    raise ValueError()
                _utc(evidence['observed_at'])
                self._baseline[evidence['evidence_id']] = evidence
            if type(action_ids) is not frozenset or any(type(x) is not str or not x for x in action_ids):
                raise ValueError()
            self._actions = action_ids
        except Exception:
            raise MCPClientError('INVALID_CONFIG') from None
        self.session_id, self.trace_id = uuid4().hex, uuid4().hex
        self.correlation = None
        self.execution_state, self.last_error = 'not_dispatched', None
        self._result, self._ledger = None, {}
        self.local_redaction_applied = False

    @property
    def trusted_tool(self):
        return TrustedTool(TOOL, 'Read verification checks from canonical mock; never fixes anything.',
                           deepcopy(self._business_schema), True)

    @property
    def evidence(self):
        return deepcopy({**self._baseline, **self._ledger})

    @property
    def result(self):
        return deepcopy(self._result)

    @property
    def checks_passed(self):
        """Strict local assessment of received checks, NOT 'the fix was performed'."""
        r = self._result
        return bool(r and r['status'] == 'ok' and not r['truncated']
                    and r['data']['overall_result'] == 'pass' and r['evidence']
                    and not any(e['truncated'] for e in r['evidence'])
                    and r['data']['checks'] and all(c['result'] == 'pass' and c['evidence_refs']
                                                   for c in r['data']['checks']))

    def _fail(self, code):
        self.last_error = code
        raise MCPClientError(code) from None

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
        await self.client.disconnect()

    async def call_tool(self, name, arguments):
        self.execution_state, self.last_error, self._result = 'not_dispatched', None, None
        args = _json_copy(arguments)
        try:
            if name != TOOL:
                raise ValueError()
            self._business.validate(args)
            since = _utc(args['since'])
            until = _utc(args['until']) if args.get('until') is not None else None
            if (until is not None and since >= until) or self._sanitize(args) != args:
                raise ValueError()
            refs = args.get('baseline_evidence_refs') or []
            if any(ref not in self._baseline for ref in refs):
                raise ValueError()
            if args.get('action_id') is not None and args['action_id'] not in self._actions:
                raise ValueError()
        except Exception:
            self._fail('TOOL_NOT_ALLOWED')
        self.correlation = dict(schema_version='1.0.0', session_id=self.session_id,
            trace_id=self.trace_id, logical_tool_call_id=uuid4().hex, attempt_id=uuid4().hex)
        request = {**args, **self.correlation}
        try:
            self._input.validate(request)
        except Exception:
            self._fail('TOOL_NOT_ALLOWED')
        self.execution_state = 'unknown'
        try:
            response = await self.client.call_tool(TOOL, request)
        except asyncio.CancelledError:
            self.last_error = 'CANCELLED'
            raise
        except Exception as error:
            code = error.code if isinstance(error, MCPClientError) else None
            self._fail(code if code in {'TIMEOUT','DISCONNECTED','PROTOCOL_ERROR'} else 'TRANSPORT_ERROR')
        self.execution_state = 'response_received'
        if type(response) is not ToolResult or type(response.is_error) is not bool:
            self._fail('MALFORMED_RESULT')
        _json_copy({'content':response.content, 'structuredContent':response.structured_content,
                    'isError':response.is_error})
        r = _json_copy(response.structured_content)
        try:
            self._output.validate(r)
            if r['status'] == 'approval_required' or response.is_error != (r['status'] == 'error'):
                raise ValueError()
            if (type(response.content) is not tuple or len(response.content) != 1
                    or response.content[0].get('type') != 'text'
                    or json.loads(response.content[0]['text'], object_pairs_hook=_unique) != r):
                raise ValueError()
            placeholder = (r['status'] == 'error' and r['session_id'] is None and not r['evidence']
                           and all(r[k] == 'invalid-input' for k in META[2:]))
            if not placeholder and any(r[k] != request[k] for k in META):
                raise ValueError()
            if _utc(r['started_at']) > _utc(r['completed_at']):
                raise ValueError()
            ids = set()
            for e in r['evidence']:
                observed = _utc(e['observed_at'])
                if (e['evidence_id'] in ids or e['evidence_id'] in self.evidence
                        or len(e['evidence_id']) > 128 or len(e['source']) > 256
                        or e['unit'] not in (None, 'nginx.service') or observed < since
                        or observed > _utc(r['completed_at'])
                        or (until is not None and observed >= until)):
                    raise ValueError()
                ids.add(e['evidence_id'])
            if r['status'] != 'error':
                d = r['data']; self._data.validate(d)
                checks = d['checks']
                if (d['unit'] != args['unit'] or d['action_id'] != args.get('action_id')
                        or d['baseline_evidence_refs'] != refs
                        or len({c['check_type'] for c in checks}) != len(checks)
                        or {c['check_type'] for c in checks} != set(args['check_types'])
                        or any(ref not in ids for c in checks for ref in c['evidence_refs'])
                        or any(len(set(c['evidence_refs'])) != len(c['evidence_refs']) for c in checks)):
                    raise ValueError()
                states = {c['result'] for c in checks}
                aggregate = 'fail' if 'fail' in states else 'unknown' if not states or 'unknown' in states else 'pass'
                if (d['overall_result'] != aggregate
                        or not since <= _utc(d['verified_at']) <= _utc(r['completed_at'])):
                    raise ValueError()
            safe = _json_copy(self._sanitize(r))
            # Only human-readable excerpts/check summaries/error messages may change.
            original = deepcopy(r)
            for old, new in zip(original['evidence'], safe['evidence']):
                old['excerpt'] = new['excerpt']
            for old, new in zip(original['data'].get('checks', []), safe['data'].get('checks', [])):
                old['summary'] = new['summary']
            if r['status'] == 'error' and 'message' in original['data']:
                original['data']['message'] = safe['data']['message']
            if original != safe:
                raise ValueError()
            self._output.validate(safe)
            ledger = {**self._ledger, **{e['evidence_id']:e for e in safe['evidence']}}
            _json_copy({**self._baseline, **ledger})
        except Exception:
            self._fail('MALFORMED_RESULT')
        self.local_redaction_applied = r != safe
        self._result, self._ledger = safe, ledger
        self.last_error = safe['error_code'] if safe['status'] == 'error' else None
        return ToolResult(({'type':'text','text':json.dumps(safe,ensure_ascii=False)},),deepcopy(safe),response.is_error)

    def interpret(self, message):
        try:
            r = json.loads(message.content)['structuredContent']
            if message.tool_name != TOOL or r != self._result:
                raise ValueError()
            checks = [{k:c[k] for k in ('check_type','result','evidence_refs')}
                      for c in r['data'].get('checks', [])]
            # Baselines remain in the ledger; they are not fresh verification evidence.
            return Observation(tuple(Evidence(e['evidence_id'],e['source'],e['excerpt']) for e in r['evidence']),
                not self.checks_passed,
                'UNTRUSTED mock verification; no action performed; status=' + r['status']
                + '; overall=' + str(r['data'].get('overall_result'))
                + '; error=' + str(r['error_code']) + '; checks=' + json.dumps(checks))
        except Exception:
            self._fail('MALFORMED_RESULT')
