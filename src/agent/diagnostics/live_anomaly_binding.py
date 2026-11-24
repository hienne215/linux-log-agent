"""Opt-in real-journal adapter around the existing SCRUM-25 canonical binding."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

from agent.anomaly import AnomalyBinding,_validator
from agent.coordinator import Evidence,Observation
from agent.integration_boundary import TrustedTool
from agent.mcp_client import MCPClientError
from agent.diagnostics.journal_policy import UNIT,window,stamp,parse_stamp,safe_message
from agent.diagnostics.journal_anomaly import SOURCE,SIGNAL,MIN_CURRENT,RATE_MULTIPLIER,RULE,REFERENCE_LIMIT,describe

BUSINESS={'type':'object','properties':{'unit':{'type':'string','enum':[UNIT],'const':UNIT}},
          'required':['unit'],'additionalProperties':False}


class LiveAnomalyBinding(AnomalyBinding):
    """No new wire fields, transport or lifecycle. One canonical dispatch only."""
    def __init__(self,client,root,*,redact):
        folder=Path(root)/'schemas/tools'
        self.schemas=tuple(json.loads((folder/('detect_anomaly'+suffix)).read_text())
            for suffix in ('.schema.json','.response.schema.json','.result.schema.json'))
        super().__init__(client,*self.schemas,redact=redact)
        self.business_validator=_validator(BUSINESS)
        self.window=None
        self._used=False
        self.counts=None

    @property
    def trusted_tool(self):
        return TrustedTool('detect_anomaly',
            'Compare visible nginx.service journal error rates in two consecutive 15-minute windows. '
            'Required unit nginx.service. App owns time/signals/thresholds. Empty baseline is unknown; '
            'partial visibility, no statistical certainty, cause, repair or security-event analysis.',deepcopy(BUSINESS),True)

    async def call_tool(self,name,arguments):
        if name!='detect_anomaly' or self._used or not self.business_validator.is_valid(arguments):
            self._fail('TOOL_NOT_ALLOWED')
        self._used=True
        self.window=self.window or window()
        request={**arguments,'since':stamp(parse_stamp(self.window['since'])-timedelta(minutes=15)),
            'until':self.window['until'],'signals':[SIGNAL],'max_findings':1}
        result=await super().call_tool(name,request)
        envelope=result.structured_content
        if envelope['status']=='error':
            if envelope['error_code'] in ('TIMEOUT','EXECUTION_UNKNOWN'):
                self.execution_state='unknown'
            self._fail(envelope['error_code'])
        try:
            data=envelope['data'];evidence=envelope['evidence'];start=parse_stamp(request['since'])
            end=parse_stamp(request['until']);mid=start+(end-start)/2
            if (envelope['status']!='partial' or envelope['error_code'] is not None or envelope['truncated']
                    or envelope['next_cursor'] is not None or data['confidence'] is not None
                    or data['findings_truncated'] or len(evidence)>100):
                raise ValueError()
            baseline=[e for e in evidence if parse_stamp(e['observed_at'])<mid]
            current=[e for e in evidence if parse_stamp(e['observed_at'])>=mid]
            baseline_count,current_count=len(baseline),len(current)
            if (data['analyzed_signals']!=([SIGNAL] if baseline_count else [])
                    or data['insufficient_signals']!=([] if baseline_count else [SIGNAL])):
                raise ValueError()
            comparison=describe(request,baseline_count,current_count)
            previous=None
            for index,e in enumerate(evidence):
                observed=parse_stamp(e['observed_at'])
                if (e['source']!=SOURCE or e['unit']!=UNIT or e['redacted'] is not True or e['truncated']
                        or (previous is not None and observed<previous)):
                    raise ValueError()
                excerpt=e['excerpt']
                if index==0:
                    prefix,excerpt=excerpt.split('\n',1)
                    if prefix!=comparison:raise ValueError()
                if not safe_message(excerpt):raise ValueError()
                previous=observed
            triggered=baseline_count>0 and current_count>=MIN_CURRENT and current_count>=RATE_MULTIPLIER*baseline_count
            if len(data['anomalies'])!=(1 if triggered else 0):raise ValueError()
            if triggered:
                f=data['anomalies'][0]
                if (f['id']!=RULE or f['type']!=SIGNAL or f['severity']!='medium'
                        or f['observed_count']!=current_count or f['summary']!=comparison
                        or f['evidence_refs']!=[baseline[0]['evidence_id'],*[e['evidence_id'] for e in current[:REFERENCE_LIMIT-1]]]
                        or f['detection_method']!='rule_based'):
                    raise ValueError()
            self.counts={'baseline_visible_count':baseline_count,'current_visible_count':current_count}
        except Exception:
            self._ledger={};self._result=None
            self._fail('MALFORMED_RESULT')
        return result

    def interpret(self,message):
        # Reuse canonical projection identity/reference validation, replacing only
        # its legacy mock-labelled projection with complete real provenance.
        super().interpret(message)
        e=self._result
        data={**deepcopy(e['data']),**self.counts,'rule':RULE,
              'min_current':MIN_CURRENT,'rate_multiplier':RATE_MULTIPLIER,'coverage_complete':False,
              'projection_evidence_count':min(REFERENCE_LIMIT,len(e['evidence'])),
              'ledger_evidence_count':len(e['evidence'])}
        start,end=(parse_stamp(self._request[k]) for k in ('since','until'))
        mid=start+(end-start)/2
        baseline=[x for x in e['evidence'] if parse_stamp(x['observed_at'])<mid]
        current=[x for x in e['evidence'] if parse_stamp(x['observed_at'])>=mid]
        selected=([baseline[0]] if baseline else [])+current[:REFERENCE_LIMIT-(1 if baseline else 0)]
        if not current:
            selected=baseline[:REFERENCE_LIMIT]
        if len(e['evidence'])<=REFERENCE_LIMIT:
            selected=e['evidence']
        data['projection_evidence_count']=len(selected)
        return Observation(tuple(Evidence(item['evidence_id'],item['source'],json.dumps(
            {k:v for k,v in item.items() if k not in ('evidence_id','source')},ensure_ascii=False))
            for item in selected),True,json.dumps({'tool':'detect_anomaly','status':'partial','data':data},ensure_ascii=False))
