"""P03-02 persistent paging candidate; no shared MCP dispatch changes.

Create once per trusted dedicated connection. The binder must own scheduling,
principal/access policy, acquisition cancellation and close/EOF hooks. Never
instantiate this inside oom_worker.py, which exits after one call.
"""
import copy
import json
import secrets
import threading
import time

import mock_mcp_server as contracts
from .oom_events import parse_ts, MAX_SNAPSHOT_BYTES

TOOL = 'memory_oom_events'


class OomPaginator:
    TTL = 300
    MAX_TOKENS = 256
    MAX_CACHE_BYTES = 8 * MAX_SNAPSHOT_BYTES

    def __init__(self, acquire, session_id, access_check, clock=time.monotonic,
                 native_builder=None):
        if not callable(acquire) or not callable(access_check) or not isinstance(session_id,str) or not 1 <= len(session_id) <= 128:
            raise ValueError('Trusted acquisition/session/access policy required')
        self._acquire, self._session, self._access, self._clock = acquire, session_id, access_check, clock
        self._native = native_builder or contracts.native_result
        self._tokens, self._closed, self._lock = {}, False, threading.RLock()

    def _error(self, code='INVALID_INPUT'):
        return contracts.safe_error(TOOL, {}, code, rule='oom_pagination')

    def _binding(self, req):
        return (TOOL, req['session_id'], parse_ts(req['since']).isoformat(),
                parse_ts(req['until']).isoformat(), req.get('limit',100))

    def _prune(self):
        now=self._clock()
        self._tokens={k:v for k,v in self._tokens.items() if v['expires']>now}

    def _build(self, snapshot, rows, req, cursor):
        result=copy.deepcopy(snapshot['base'])
        result.update({k:req[k] for k in ('session_id','trace_id','logical_tool_call_id','attempt_id')})
        result['data']['events']=[r[0] for r in rows]
        result['data'].update(count=len(rows),limit=req.get('limit',100))
        result['evidence']=[r[1] for r in rows]
        result['next_cursor']=cursor
        return result

    def call(self, req, request_id=0):
        started,tick=contracts.current_time(),time.monotonic()
        with self._lock:
            result=self._call(req,request_id)
            result.update(started_at=started,completed_at=contracts.current_time(),
                          duration_ms=max(0,int((time.monotonic()-tick)*1000)))
            if not contracts.TOOLS[TOOL][4].is_valid(result):
                result=self._error('EXECUTION_UNKNOWN')
            if contracts.wire_size(self._native(result),request_id)>contracts.MAX_RESPONSE_BYTES:
                result=self._error('OUTPUT_LIMIT')
            return result

    def _call(self, req, request_id):
        if (self._closed or not isinstance(req,dict) or not contracts.TOOLS[TOOL][3].is_valid(req)
                or not contracts.valid_window(req) or req['session_id']!=self._session):
            return self._error()
        binding=self._binding(req)
        token=req.get('cursor')
        state=self._tokens.get(token) if token else None
        if token and (state is None or state['snapshot']['binding']!=binding):
            self._prune()
            return self._error()
        if state and state['expires']<=self._clock():
            self._prune()
            return self._error('EXPIRED')
        self._prune()
        try:
            permitted=self._access() is True
        except Exception:
            permitted=False
        if not permitted:
            self._tokens.clear()
            return self._error('POLICY_DENIED')
        if state:
            snapshot,offset,expiry=state['snapshot'],state['offset'],state['expires']
        else:
            try:
                # Cursor never reaches the stateless acquisition function.
                raw=self._acquire(dict(req))
                if (not contracts.TOOLS[TOOL][4].is_valid(raw) or
                        any(raw[k]!=req[k] for k in ('session_id','trace_id','logical_tool_call_id','attempt_id'))):
                    return self._error('EXECUTION_UNKNOWN')
                if raw['status']=='error':
                    coverage=raw['data'].get('coverage')
                    if coverage and raw['error_code'] in ('PERMISSION_DENIED','UNSUPPORTED') and all(c['status']!='ok' for c in coverage):
                        return contracts.coverage_error(TOOL,req,coverage)
                    return self._error(raw['error_code'])
                refs={e['evidence_id']:e for e in raw['evidence']}
                events=raw['data']['events']
                ids=[e['evidence_id'] for e in events]
                if len(refs)!=len(raw['evidence']) or len(set(ids))!=len(ids) or set(ids)!=set(refs):
                    return self._error('EXECUTION_UNKNOWN')
                rows=[(event,refs[event['evidence_id']]) for event in events]
                base=copy.deepcopy(raw)
                base['data']['events'],base['evidence']=[],[]
                for key in ('session_id','trace_id','logical_tool_call_id','attempt_id'):
                    base[key]=None if key=='session_id' else 'invalid-input'
                snapshot=copy.deepcopy(dict(base=base,rows=rows,binding=binding))
                snapshot['size']=len(json.dumps(snapshot,ensure_ascii=False).encode('utf-8'))
                if snapshot['size']>MAX_SNAPSHOT_BYTES:
                    return self._error('OUTPUT_LIMIT')
            except Exception:
                return self._error('EXECUTION_UNKNOWN')
            offset,expiry=0,self._clock()+self.TTL
        rows=snapshot['rows']
        page=[]
        next_token=state.get('next') if state else None
        next_token=next_token or secrets.token_urlsafe(32)
        for row in rows[offset:offset+req.get('limit',100)]:
            candidate=self._build(snapshot,page+[row],req,next_token)
            if contracts.wire_size(self._native(candidate),request_id)>contracts.MAX_RESPONSE_BYTES-128:
                break
            page.append(row)
        if not page and offset<len(rows):
            return self._error('OUTPUT_LIMIT')
        end=offset+len(page)
        resumable=end<len(rows) and not snapshot['base']['truncated']
        if resumable:
            known=next_token in self._tokens
            unique={id(v['snapshot']):v['snapshot'] for v in self._tokens.values()}
            used=sum(s['size'] for s in unique.values())
            if not known and (len(self._tokens)>=self.MAX_TOKENS or (not state and used+snapshot['size']>self.MAX_CACHE_BYTES)):
                return self._error('OUTPUT_LIMIT')
            if not known:
                self._tokens[next_token]=dict(snapshot=snapshot,offset=end,expires=expiry)
                if state:
                    state['next']=next_token
        else:
            next_token=None
        return self._build(snapshot,page,req,next_token)

    def close(self):
        with self._lock:
            self._tokens.clear()
            self._closed=True
