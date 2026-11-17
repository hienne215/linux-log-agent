"""P03-03 standalone, process-local adapter. No shared server wiring.

An operator-owned provider returns two SourceSnapshots. Cursor snapshots contain
only sanitized records and coverage, never raw IPs/messages. Instantiate once per
server process, not once per worker call. This is not session authentication.
"""
import copy
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from .failed_login_events import SOURCES, parse_time, timestamp

TOOL = 'failed_logins'
ROOT = Path(__file__).resolve().parents[2]


@dataclass
class SourceSnapshot:
    source: str
    status: str
    events: list = field(default_factory=list)
    oldest: object = None
    incomplete: bool = False
    truncated: bool = False


class FailedLoginsAdapter:
    TTL = 300
    MAX_STATES = 256
    MAX_SNAPSHOT_BYTES = 1024 * 1024
    MAX_CACHE_BYTES = 8 * 1024 * 1024
    # Conservative payload budget, not a replacement for the SDK's exact frame cap.
    PAGE_BYTES = 24000

    def __init__(self, provider, clock=time.monotonic, duration_clock=time.monotonic,
                 wall_clock=None):
        self._provider, self._clock = provider, clock
        self._duration_clock = duration_clock
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._states, self._lock = {}, threading.RLock()
        directory = ROOT / 'schemas' / 'tools'
        def validator(suffix):
            schema = json.loads((directory / (TOOL + suffix + '.schema.json')).read_text(encoding='utf-8'))
            return Draft202012Validator(schema, format_checker=FormatChecker())
        self._input, self._output = validator(''), validator('.result')

    def _response(self, request, status, data, evidence=None, code=None, truncated=False, cursor=None):
        now = timestamp(datetime.now(timezone.utc))
        return dict(schema_version='1.0.0', session_id=request.get('session_id'),
                    trace_id=request.get('trace_id', 'invalid-input'),
                    logical_tool_call_id=request.get('logical_tool_call_id', 'invalid-input'),
                    attempt_id=request.get('attempt_id', 'invalid-input'), tool=TOOL,
                    started_at=now, completed_at=now, duration_ms=0, status=status,
                    data=data, evidence=evidence or [], error_code=code,
                    truncated=truncated if status == 'partial' else False, next_cursor=cursor)

    def _invalid(self, code='INVALID_INPUT'):
        return self._response({}, 'error', dict(message='Request rejected.', field='', rule='validation'), code=code)

    def _binding(self, request):
        since, until = parse_time(request['since']), parse_time(request['until'])
        if not timedelta(0) < until - since <= timedelta(hours=24):
            raise ValueError()
        return (TOOL, request['session_id'], timestamp(since), timestamp(until), request.get('limit', 100))

    def _prune(self, now):
        for token in list(self._states):
            if self._states[token]['expires'] <= now:
                del self._states[token]

    def _cache_size(self):
        # A snapshot may have multiple tokens, count each object only once.
        return sum(s['size'] for s in {id(v['snapshot']): v['snapshot'] for v in self._states.values()}.values())

    def _snapshot(self, request, binding):
        sources = self._provider(parse_time(request['since']), parse_time(request['until']))
        if (len(sources) != 2 or tuple(s.source for s in sources) != SOURCES or
                any(s.status not in ('ok', 'permission_denied', 'unavailable') for s in sources)):
            raise ValueError('Invalid provider result')
        coverage = [dict(source=s.source, status=s.status,
                         oldest_available_at=timestamp(s.oldest) if s.oldest else None) for s in sources]
        readable = [s for s in sources if s.status == 'ok']
        if not readable:
            code = 'PERMISSION_DENIED' if all(s.status == 'permission_denied' for s in sources) else 'UNSUPPORTED'
            return None, self._response(request, 'error', dict(message='No readable login source.', coverage=coverage), code=code)
        # One authoritative source per snapshot: no journal/auth.log union counts.
        # Empty events alone never trigger fallback. Retention/access/parse gaps do.
        since, until = parse_time(request['since']), parse_time(request['until'])
        journal, auth = sources
        journal_complete = (journal.status == 'ok' and journal.oldest is not None
                            and journal.oldest <= since and not journal.incomplete
                            and not journal.truncated)
        chosen = journal if journal_complete else (auth if auth.status == 'ok' else readable[0])
        events = sorted((e for e in chosen.events if since <= e.time < until), key=lambda e: (e.time, e.identity))
        rows, seen = [], set()
        for event in events:
            if event.identity in seen:
                continue
            seen.add(event.identity)
            public = event.public()
            rows.append((public, dict(evidence_id=public['evidence_id'],
                        observed_at=public['timestamp'], source=chosen.source,
                        unit=None, excerpt=public['message'], redacted=True, truncated=False)))
        incomplete = any(s.status != 'ok' or not s.oldest or s.oldest > since or s.incomplete or s.truncated for s in sources)
        snapshot = dict(rows=rows, coverage=coverage, partial=incomplete,
                        cut=chosen.truncated, binding=binding)
        snapshot['size'] = len(json.dumps(snapshot, ensure_ascii=False).encode('utf-8'))
        if snapshot['size'] > self.MAX_SNAPSHOT_BYTES:
            # Never retain an unbounded snapshot or invent a continuation for discarded rows.
            return None, self._response(request, 'error', dict(message='Snapshot exceeds local budget.'), code='OUTPUT_LIMIT', truncated=True)
        return snapshot, None

    def call(self, request):
        """Return the canonical complete envelope; caller owns native SDK wrapping."""
        started, tick = self._wall_clock(), self._duration_clock()
        with self._lock:
            result = self._call(request)
            if not self._output.is_valid(result):
                result = self._response({}, 'error', dict(message='Output rejected.', field='', rule='output_validation'), code='EXECUTION_UNKNOWN')
            result.update(started_at=timestamp(started), completed_at=timestamp(self._wall_clock()),
                          duration_ms=max(0, int((self._duration_clock() - tick) * 1000)))
            return result

    def _call(self, request):
        if not isinstance(request, dict) or not self._input.is_valid(request):
            return self._invalid()
        try:
            binding = self._binding(request)
        except (ValueError, TypeError, OverflowError):
            return self._invalid()
        now = self._clock()
        token = request.get('cursor')
        state = self._states.get(token) if token else None
        if token and (not state or state['snapshot']['binding'] != binding):
            self._prune(now)
            return self._invalid()
        if state and state['expires'] <= now:
            self._prune(now)
            return self._invalid('EXPIRED')
        self._prune(now)
        if state:
            snapshot, offset, expiry = state['snapshot'], state['offset'], state['expires']
        else:
            try:
                snapshot, error = self._snapshot(request, binding)
            except Exception:
                return self._response(request, 'error', dict(message='Source read failed.'), code='EXECUTION_UNKNOWN')
            if error:
                return error
            offset, expiry = 0, now + self.TTL
        rows = snapshot['rows']
        page = []
        budget = len(json.dumps(snapshot['coverage']).encode('utf-8')) + 4096
        while offset + len(page) < len(rows) and len(page) < binding[-1]:
            row = rows[offset + len(page)]
            cost = len(json.dumps(row, ensure_ascii=False).encode('utf-8'))
            if budget + cost > self.PAGE_BYTES:
                break
            budget += cost
            page.append(row)
        if not page and offset < len(rows):
            return self._response(request, 'error', dict(message='Record exceeds page budget.'), code='OUTPUT_LIMIT', truncated=True)
        end = offset + len(page)
        next_token = None
        if end < len(rows) and not snapshot['cut']:
            if len(self._states) >= self.MAX_STATES or (not state and self._cache_size() + snapshot['size'] > self.MAX_CACHE_BYTES):
                return self._response(request, 'error', dict(message='Cursor budget exhausted.'), code='OUTPUT_LIMIT', truncated=True)
            # Repeated continuation calls return the same next token/page.
            if state and state.get('next') in self._states:
                next_token = state['next']
            else:
                next_token = secrets.token_urlsafe(32)
                self._states[next_token] = dict(snapshot=snapshot, offset=end, expires=expiry)
                if state:
                    state['next'] = next_token
        truncated = snapshot['cut']
        result = self._response(request, 'partial' if snapshot['partial'] or truncated else 'ok',
                    dict(events=[r[0] for r in page], count=len(page), limit=binding[-1],
                         coverage=snapshot['coverage']), [r[1] for r in page],
                    code='OUTPUT_LIMIT' if truncated else None, truncated=truncated, cursor=next_token)
        # API users cannot mutate retained snapshots through a returned dictionary.
        return copy.deepcopy(result)

    def close(self):
        with self._lock:
            self._states.clear()
