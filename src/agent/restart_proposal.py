"""Canonical non-executing restart proposal/status bridge. No helper privileges.

MCP has no execute/approve mode. The operator opens the session separately;
the Agent owns correlation, reason and references to already validated evidence.
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import re
from uuid import uuid4

from agent.anomaly import _validator,_copy,_unique,_time
from agent.coordinator import Observation
from agent.integration_boundary import TrustedTool
from agent.mcp_client import MCPClientError,ToolResult

TOOL='restart_service'
REASON='Explicit operator request; review the referenced service observation before any action.'
BUSINESS={'type':'object','properties':{'unit':{'type':'string','const':'nginx.service'},
 'mode':{'type':'string','enum':['propose','status']}},'required':['unit','mode'],'additionalProperties':False}
META=('schema_version','session_id','trace_id','logical_tool_call_id','attempt_id')


class RestartBinding:
    def __init__(self,client,root,*,redact,evidence):
        self.client,self.redact,self._evidence=client,redact,evidence
        self.schemas=tuple(json.loads((Path(root)/'schemas/tools'/(TOOL+s)).read_text())
            for s in ('.schema.json','.response.schema.json','.result.schema.json'))
        self.validators=tuple(_validator(s) for s in self.schemas)
        self.business_validator=_validator(BUSINESS)
        self.session_id=self.trace_id=None;self._result=None;self._ledger={};self.last_error=None
        self.execution_state='not_dispatched';self.action_id=None;self._used=set()

    @property
    def trusted_tool(self):
        return TrustedTool(TOOL,'Create or inspect a restart PROPOSAL for nginx.service only, after service_status evidence. Never approves or executes. App supplies metadata/reason/evidence; operator confirms separately.',deepcopy(BUSINESS),False)

    @property
    def evidence(self):return deepcopy(self._ledger)

    def fail(self,code):
        self.last_error=code;raise MCPClientError(code) from None

    async def disconnect(self):await self.client.disconnect()

    async def call_tool(self,name,arguments):
        if name!=TOOL or not self.business_validator.is_valid(arguments):self.fail('TOOL_NOT_ALLOWED')
        mode=arguments['mode']
        if mode in self._used or re.fullmatch('[0-9a-f]{32}',self.session_id or '') is None:self.fail('TOOL_NOT_ALLOWED')
        request=dict(schema_version='1.0.0',session_id=self.session_id,trace_id=self.trace_id,
                     logical_tool_call_id=uuid4().hex,attempt_id=uuid4().hex,mode=mode)
        if mode=='propose':
            refs=self._evidence()
            if (not refs or len(refs)>16 or len(set(refs))!=len(refs)
                    or any(type(r) is not str or not 1<=len(r)<=128 for r in refs)):
                self.fail('INSUFFICIENT_EVIDENCE')
            request.update(unit='nginx.service',reason=REASON,evidence_ids=list(refs))
        else:
            if self.action_id is None:self.fail('UNKNOWN_ACTION')
            request['action_id']=self.action_id
        self.validators[0].validate(request);self._used.add(mode);self.execution_state='unknown'
        try:result=await self.client.call_tool(name,request)
        except asyncio.CancelledError:self.last_error='CANCELLED';raise
        except MCPClientError as e:self.last_error=e.code;raise
        except Exception:self.fail('TRANSPORT_ERROR')
        try:
            if type(result) is not ToolResult or type(result.is_error) is not bool:raise ValueError()
            envelope=_copy(result.structured_content);self.validators[2].validate(envelope)
            if envelope['tool']!=TOOL or result.is_error!=(envelope['status']=='error'):raise ValueError()
            if len(result.content)!=1 or result.content[0].get('type')!='text' or json.loads(result.content[0]['text'],object_pairs_hook=_unique)!=envelope:raise ValueError()
            placeholder=(envelope['status']=='error' and envelope['session_id'] is None
                and all(envelope[k]=='invalid-input' for k in META[2:]))
            if not placeholder and any(envelope[k]!=request[k] for k in META):raise ValueError()
            if (envelope['evidence'] or envelope['truncated'] or envelope['next_cursor'] is not None
                    or _time(envelope['started_at'])>_time(envelope['completed_at'])):raise ValueError()
            encoded=json.dumps(envelope,ensure_ascii=False,allow_nan=False)
            if len(encoded.encode())>65536 or self.redact(encoded)!=encoded:raise ValueError()
            if envelope['status']!='error':
                d=envelope['data'];self.validators[1].validate(d)
                if d['unit']!='nginx.service' or re.fullmatch('[0-9a-f]{32}',d['action_id']) is None:raise ValueError()
                if mode=='propose':
                    if (envelope['status']!='approval_required' or d['proposal_status']!='pending_approval'
                            or d['executed'] is not False or d['reason']!=REASON
                            or d['evidence_ids']!=request['evidence_ids']):raise ValueError()
                elif envelope['status']!='ok' or d['action_id']!=self.action_id:raise ValueError()
        except Exception:self.fail('MALFORMED_RESULT')
        self.execution_state='response_received';self._result=envelope
        if envelope['status']=='error':
            if envelope['error_code'] in ('TIMEOUT','EXECUTION_UNKNOWN'):self.execution_state='unknown'
            self.fail(envelope['error_code'])
        self.action_id=envelope['data']['action_id'];return result

    def interpret(self,message):
        if message.tool_name!=TOOL or self._result is None or message.is_error:self.fail('MALFORMED_RESULT')
        try:
            if json.loads(message.content,object_pairs_hook=_unique)['structuredContent']!=self._result:raise ValueError()
        except Exception:self.fail('MALFORMED_RESULT')
        # Proposal metadata is not an observed Linux event: no invented evidence.
        return Observation((),True,json.dumps(dict(tool=TOOL,status=self._result['status'],data=self._result['data']),ensure_ascii=False))
