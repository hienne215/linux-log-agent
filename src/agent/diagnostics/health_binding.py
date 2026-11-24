"""Full three-check read-only verification; reuses SCRUM-26 validation/ledger."""
from copy import deepcopy
import json
from pathlib import Path
import re

from agent.verify_fix import VerifyFixBinding,_validator,_utc
from agent.service_binding import BUSINESS
from agent.diagnostics.journal_policy import window
from agent.diagnostics.service_verification import assess,SOURCE
from agent.diagnostics.health_verification import CHECKS,HTTP_SOURCE,JOURNAL_SOURCE,HTTP_SUMMARY
from agent.integration_boundary import TrustedTool
from agent.coordinator import Observation,Evidence


class HealthBinding(VerifyFixBinding):
    def __init__(self,client,root,*,redact,baseline_evidence=(),action_id=None,since=None):
        p=Path(root)/'schemas/tools'
        self.schemas=tuple(json.loads((p/('verify_fix'+s)).read_text()) for s in ('.schema.json','.response.schema.json','.result.schema.json'))
        super().__init__(client,*self.schemas,redact=redact,baseline_evidence=baseline_evidence,
            action_ids=frozenset({action_id}) if action_id is not None else frozenset())
        self.action_id=action_id;self.since=since
        self.business_validator=_validator(BUSINESS);self._used=False
    @property
    def trusted_tool(self):
        return TrustedTool('verify_fix','Read-only verification of nginx.service using fresh service state, fixed loopback HTTP HEAD and new error logs. Required unit nginx.service. App owns checks/time/metadata. Active alone is not HTTP health; unknown/partial is never verified repair. No action performed.',deepcopy(BUSINESS),True)
    async def call_tool(self,name,args):
        if name!='verify_fix' or self._used or not self.business_validator.is_valid(args):self._fail('TOOL_NOT_ALLOWED')
        self._used=True
        self._request_since=self.since or window()['since']
        r=await super().call_tool(name,dict(unit='nginx.service',since=self._request_since,until=None,
            check_types=list(CHECKS),action_id=self.action_id,baseline_evidence_refs=list(self._baseline)))
        e=r.structured_content
        if e['status']=='error':
            if e['error_code'] in ('TIMEOUT','EXECUTION_UNKNOWN'):self.execution_state='unknown'
            self._fail(e['error_code'])
        try:
            if e['status']!='partial' or e['truncated'] or e['next_cursor'] is not None:raise ValueError()
            ev={v['evidence_id']:v for v in e['evidence']}
            if len(ev)>3:raise ValueError()
            for item in ev.values():
                if (item['source'] not in (SOURCE,HTTP_SOURCE,JOURNAL_SOURCE) or item['unit']!='nginx.service'
                    or not item['redacted'] or item['truncated'] or _utc(item['observed_at'])<_utc(e['started_at'])):raise ValueError()
            for check in e['data']['checks']:
                refs=check['evidence_refs'];items=[ev[x] for x in refs]
                if not refs:
                    if check['result']!='unknown' or len(check['summary'])>256:raise ValueError()
                    if check['check_type']=='service_active':
                        if not re.fullmatch(r'Service read unavailable: [A-Z_]+; no repair claim\.',check['summary']):raise ValueError()
                    elif check['check_type']=='http_health':
                        if check['summary'] not in ('Loopback HTTP connection refused; no HTTP response received.',
                            'Loopback HTTP response unavailable, malformed or timed out; no body retained.'):raise ValueError()
                    elif not re.fullmatch(r'New-log check unavailable: [A-Z_]+; no absence-of-errors claim\.',check['summary']):raise ValueError()
                    continue
                if len(items)!=1:raise ValueError()
                v=items[0]
                if check['check_type']=='service_active':
                    outcome,summary=assess(v['excerpt'],action_context=self.action_id is not None)
                    if v['source']!=SOURCE or check['result']!=outcome or check['summary']!=summary:raise ValueError()
                elif check['check_type']=='http_health':
                    m=re.fullmatch(re.escape(HTTP_SUMMARY)+r'([1-5][0-9]{2}); fixed 127\.0\.0\.1:80; no redirect followed; no body retained',v['excerpt'])
                    if not m or v['source']!=HTTP_SOURCE:raise ValueError()
                    status=int(m[1]);expected='pass' if 200<=status<300 else 'fail' if status>=400 else 'unknown'
                    if check['result']!=expected or check['summary']!=v['excerpt']:raise ValueError()
                else:
                    m=re.fullmatch(r'New nginx priority 0\.\.3 journal records visible since=([^;]+); until=([^;]+); count=([0-9]+); history/reader coverage not independently complete',v['excerpt'])
                    if (not m or v['source']!=JOURNAL_SOURCE or m[1]!=self._request_since
                            or not _utc(m[1])<_utc(m[2])<=_utc(e['completed_at'])
                            or check['result']!=('fail' if int(m[3]) else 'unknown')):raise ValueError()
                    summary=('New error-priority journal records were observed in the requested window.' if int(m[3]) else
                        'No new error records visible; incomplete source coverage prevents a verified pass.')
                    if check['summary']!=summary:raise ValueError()
            if set(ev)!={ref for check in e['data']['checks'] for ref in check['evidence_refs']}:raise ValueError()
            if e['data']['overall_result']=='pass':raise ValueError() # no verified coverage/action yet
        except Exception:
            self._result=None;self._ledger={};self._fail('MALFORMED_RESULT')
        return r
    def interpret(self,message):
        super().interpret(message);r=self._result
        return Observation(tuple(Evidence(e['evidence_id'],e['source'],json.dumps({k:v for k,v in e.items() if k not in ('evidence_id','source')},ensure_ascii=False)) for e in r['evidence']),True,
            json.dumps(dict(tool='verify_fix',status='partial',data=r['data']),ensure_ascii=False))
