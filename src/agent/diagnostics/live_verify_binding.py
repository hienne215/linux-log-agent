"""Limited real service verification using the existing SCRUM-26 boundary."""
from copy import deepcopy
import json
from pathlib import Path

from agent.verify_fix import VerifyFixBinding, _validator
from agent.coordinator import Evidence, Observation
from agent.integration_boundary import TrustedTool
from agent.diagnostics.journal_policy import UNIT, window, parse_stamp
from agent.diagnostics.service_verification import SOURCE, CHECKS, assess
from agent.service_binding import BUSINESS


class LiveVerifyBinding(VerifyFixBinding):
    def __init__(self, client, root, *, redact):
        folder = Path(root)/'schemas/tools'
        self.schemas = tuple(json.loads((folder/('verify_fix'+suffix)).read_text())
            for suffix in ('.schema.json', '.response.schema.json', '.result.schema.json'))
        super().__init__(client, *self.schemas, redact=redact)
        self.business_validator = _validator(BUSINESS)
        self.window = None
        self._used = False

    @property
    def trusted_tool(self):
        return TrustedTool('verify_fix',
            'Recheck current nginx.service state, read-only, without performing a fix. '
            'Required unit nginx.service. Only service_active is supported; app owns checks, '
            'time and metadata. No action/baseline supplied: partial verification only, '
            'never proof of successful repair, HTTP health or no new failures.', deepcopy(BUSINESS), True)

    async def call_tool(self, name, arguments):
        if name != 'verify_fix' or self._used or not self.business_validator.is_valid(arguments):
            self._fail('TOOL_NOT_ALLOWED')
        self._used = True
        self.window = self.window or window()
        request = dict(unit=UNIT, since=self.window['since'], until=None,
                       check_types=list(CHECKS), action_id=None, baseline_evidence_refs=[])
        result = await super().call_tool(name, request)
        envelope = result.structured_content
        if envelope['status'] == 'error':
            if envelope['error_code'] in ('TIMEOUT', 'EXECUTION_UNKNOWN'):
                self.execution_state = 'unknown'
            self._fail(envelope['error_code'])
        try:
            data, evidence = envelope['data'], envelope['evidence']
            if (envelope['status'] != 'partial' or envelope['error_code'] is not None
                    or envelope['truncated'] or envelope['next_cursor'] is not None
                    or len(evidence) != 1 or data['action_id'] is not None
                    or data['baseline_evidence_refs'] != []):
                raise ValueError()
            item = evidence[0]
            if (item['source'] != SOURCE or item['unit'] != UNIT
                    or item['redacted'] is not True or item['truncated']
                    or parse_stamp(item['observed_at']) < parse_stamp(envelope['started_at'])):
                raise ValueError()
            outcome, summary = assess(item['excerpt'])
            if (data['verified_at'] != item['observed_at'] or data['overall_result'] != outcome
                    or data['checks'] != [dict(check_type='service_active', result=outcome,
                        summary=summary, evidence_refs=[item['evidence_id']])]):
                raise ValueError()
        except Exception:
            self._result, self._ledger = None, {}
            self._fail('MALFORMED_RESULT')
        return result

    def interpret(self, message):
        super().interpret(message)  # Reuse canonical identity/reference validation.
        r = self._result
        return Observation(tuple(Evidence(e['evidence_id'], e['source'], json.dumps(
            {k:v for k,v in e.items() if k not in ('evidence_id', 'source')}, ensure_ascii=False))
            for e in r['evidence']), True,
            json.dumps(dict(tool='verify_fix', status='partial', data=r['data']), ensure_ascii=False))
