#!/usr/bin/env python3
"""Exactly four offline-tool / live-LLM smoke cases; no retries or dispatch.

Parent process adds a 45s wall guard and checks MemAvailable every 0.2s.
This is a harness guard, NOT a hard deadline implemented by the adapter.
Only metadata, mock arguments and pass/fail are written. No response text.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import time
from urllib.request import build_opener, ProxyHandler, Request

from agent.llm import AdapterError, Message, OllamaAdapter, OllamaConfig, ToolDefinition
from agent.llm.ollama import HTTPTransport

ROOT = Path(__file__).resolve().parents[2]
MODEL = 'qwen3:4b-instruct-2507-q4_K_M'
DIGEST = '0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0'
BASE = 'http://127.0.0.1:11434'
MIB = 1024 ** 2
TOOLS = (
    ToolDefinition('mock_service_status', 'Mock only: return service status; never execute a command.', {
        'type': 'object', 'properties': {'unit': {'type': 'string', 'enum': ['lab.service']}},
        'required': ['unit'], 'additionalProperties': False}),
    ToolDefinition('mock_listening_ports', 'Mock only: inspect a port; never execute anything.', {
        'type': 'object', 'properties': {'port': {'type': 'integer', 'minimum': 1, 'maximum': 65535},
                                      'protocol': {'type': 'string', 'enum': ['tcp', 'udp']}},
        'required': ['port', 'protocol'], 'additionalProperties': False}),
)
CASES = (
    {'id': 'S1', 'name': 'Normal text', 'prompt': 'Reply exactly SMOKE_OK. No explanation.',
     'tools': False, 'require_tool': False, 'text_expected': 'SMOKE_OK'},
    {'id': 'S2', 'name': 'Native mock call',
     'prompt': 'Use exactly one native tool call to mock_service_status with unit lab.service. '
               'Do not write prose or JSON in content. All tools are mocks; do not execute anything.',
     'tools': True, 'require_tool': True,
     'expected': {'name': 'mock_service_status', 'arguments': {'unit': 'lab.service'}}},
    {'id': 'S3', 'name': 'Schema-correct arguments',
     'prompt': 'Use exactly one native call to mock_listening_ports for TCP port 80. '
               'Use port as integer 80 and protocol as string tcp. No prose. All tools are mocks.',
     'tools': True, 'require_tool': True,
     'expected': {'name': 'mock_listening_ports', 'arguments': {'port': 80, 'protocol': 'tcp'}}},
    {'id': 'S4', 'name': 'Text-only rejected by require_tool',
     'prompt': 'This is a greeting, unrelated to Linux diagnostics. Do not use any tools. '
               'Reply exactly TEXT_ONLY_OK as plain text; do not call a function.',
     'tools': True, 'require_tool': True, 'expected_error': 'OUTPUT_POLICY'},
)


def utc():
    return datetime.now(timezone.utc).isoformat()


def available_mib():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) / 1024
    raise ValueError('MemAvailable unavailable')


def get_metadata(path):
    opener = build_opener(ProxyHandler({}))
    with opener.open(Request(BASE + '/api/' + path), timeout=5) as reply:
        body = reply.read(1048577)
    if len(body) > 1048576:
        raise ValueError('metadata size limit')
    return json.loads(body)


class MeasuredTransport:
    """Observe sanitized response metadata in memory; never persist raw content."""
    def __init__(self):
        self.calls = 0
        self.observation = {}

    def post(self, url, body, timeout, limit):
        if self.calls:
            raise AdapterError('SMOKE_REQUEST_LIMIT')
        request = json.loads(body)
        assert 'think' not in request and request['stream'] is False
        assert request['options'] == {'num_ctx': 4096, 'num_predict': 256,
                                      'temperature': 0, 'num_thread': 4}
        self.calls += 1
        started = time.monotonic()
        raw = HTTPTransport().post(url, body, timeout, limit)
        latency = time.monotonic() - started
        # Inspect only for report metrics; adapter still receives original bytes.
        try:
            obj = json.loads(raw)
            message = obj.get('message', {})
            content = message.get('content', '')
            thinking = message.get('thinking', '')
            native = message.get('tool_calls', [])
            self.observation = {'http_latency_s': latency,
                                'content_chars': len(content) if isinstance(content, str) else None,
                                'thinking_chars': len(thinking) if isinstance(thinking, str) else None,
                                'native_calls_count': len(native) if isinstance(native, list) else None,
                                'done': obj.get('done') is True,
                                'done_reason': obj.get('done_reason') if obj.get('done_reason') in ('stop', 'length') else 'other'}
            for key in ('prompt_eval_count', 'eval_count', 'eval_duration', 'total_duration', 'load_duration'):
                value = obj.get(key)
                self.observation[key] = value if type(value) is int and 0 <= value <= 2**63-1 else None
            count, duration = self.observation['eval_count'], self.observation['eval_duration']
            self.observation['tokens_per_second'] = count * 1e9 / duration if count is not None and duration else None
        except (ValueError, TypeError, AttributeError):
            self.observation = {'http_latency_s': latency, 'metadata_parse_failed': True}
        return raw


def worker(pipe, case):
    transport = MeasuredTransport()
    started = time.monotonic()
    row = {'case_id': case['id'], 'name': case['name'], 'started_at': utc()}
    try:
        result = OllamaAdapter(OllamaConfig(timeout_seconds=45), transport=transport).complete(
            [Message('user', case['prompt'])], TOOLS if case['tools'] else (),
            require_tool=case['require_tool'])
        row.update(kind=result.kind, latency_s=result.latency_seconds, usage=asdict(result.usage),
                   tool_calls=[], schema_valid=True if result.tool_calls else None)
        # Persist mock arguments only when exactly the synthetic expected value;
        # do not store arbitrary live arguments if validation/expectation differs.
        expected = case.get('expected')
        row['exact_arguments'] = bool(expected and len(result.tool_calls) == 1
            and result.tool_calls[0].name == expected['name']
            and result.tool_calls[0].arguments == expected['arguments'])
        if row['exact_arguments']:
            row['tool_calls'] = [expected]
        if case['id'] == 'S1':
            row['text_matches_expected'] = result.text.strip() == case['text_expected']
            row['pass'] = result.kind == 'text' and row['text_matches_expected']
        else:
            row['pass'] = result.kind == 'tool_calls' and row['exact_arguments'] and not case.get('expected_error')
    except AdapterError as error:
        row.update(adapter_error=error.code, latency_s=time.monotonic() - started)
        observed = transport.observation
        row['pass'] = (case.get('expected_error') == error.code
                       and observed.get('native_calls_count') == 0
                       and (observed.get('content_chars') or 0) > 0
                       and observed.get('thinking_chars') == 0
                       and observed.get('done') is True and observed.get('done_reason') == 'stop')
    except Exception:
        row.update(adapter_error='SMOKE_INTERNAL_ERROR', pass_=False)
        row['pass'] = False
    finally:
        row.update(observed=transport.observation, inference_requests=transport.calls,
                   finished_at=utc())
        pipe.send(row)
        pipe.close()


def trial(case):
    context = mp.get_context('spawn')
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=worker, args=(child, case))
    minimum = available_mib()
    started = time.monotonic()
    process.start()
    child.close()
    row = None
    try:
        while True:
            try:
                minimum = min(minimum, available_mib())
            except (OSError, ValueError):
                row = {'case_id': case['id'], 'pass': False, 'adapter_error': 'RAM_MONITOR_UNAVAILABLE'}
                break
            if minimum < 1024:
                row = {'case_id': case['id'], 'pass': False, 'adapter_error': 'LOW_MEMORY'}
                break
            if time.monotonic() - started >= 45:
                row = {'case_id': case['id'], 'pass': False, 'adapter_error': 'SMOKE_WALL_TIMEOUT'}
                break
            if parent.poll(0.2):
                try:
                    row = parent.recv()
                except EOFError:
                    row = {'case_id': case['id'], 'pass': False, 'adapter_error': 'SMOKE_CLIENT_EXITED'}
                break
    finally:
        if process.is_alive():
            process.terminate()  # Only our client; NEVER Ollama/server or a tool.
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join()
        parent.close()
    row['min_available_mib'] = minimum
    row['wall_elapsed_s'] = time.monotonic() - started
    return row


def save(out, metadata, rows, status):
    data = {'metadata': metadata, 'status': status, 'rows': rows,
            'pass': status == 'complete' and len(rows) == 4 and all(r['pass'] for r in rows)}
    tmp = out / 'results.json.tmp'
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(out / 'results.json')


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    out = ROOT / 'tests/smoke/results' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True, exist_ok=False)
    metadata = {'started_at': utc(), 'model': MODEL, 'planned_max_requests': 4,
                'options': {'num_ctx': 4096, 'num_predict': 256, 'temperature': 0, 'num_thread': 4},
                'think': 'omitted', 'stream': False, 'timeout_seconds': 45,
                'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'adapter_sha256': hashlib.sha256((ROOT / 'src/agent/llm/ollama.py').read_bytes()).hexdigest(),
                'cases': CASES, 'tool_definitions': [asdict(t) for t in TOOLS]}
    rows, status = [], 'preflight'
    try:
        if available_mib() < 1024:
            raise ValueError('low_memory')
        metadata['ollama_version'] = get_metadata('version')['version']
        tags = get_metadata('tags')['models']
        model = next((x for x in tags if x.get('name') == MODEL and x.get('digest') == DIGEST), None)
        if not model:
            raise ValueError('model_missing_or_digest_changed')
        metadata['model_digest'] = model['digest']
        metadata['loaded_before'] = get_metadata('ps')
        metadata['memory_available_before_mib'] = available_mib()
        for case in CASES:
            if available_mib() < 1024:
                status = 'stopped_low_memory'
                break
            print('START', case['id'], flush=True)
            row = trial(case)
            rows.append(row)
            status = 'running'
            save(out, metadata, rows, status)
            print('END', case['id'], 'PASS' if row['pass'] else 'FAIL',
                  row.get('adapter_error', row.get('kind')), flush=True)
            if row.get('adapter_error') and not row['pass']:
                status = 'stopped_error'
                break
        else:
            status = 'complete'
    except (OSError, ValueError, KeyError):
        status = 'preflight_or_safety_error'
    finally:
        metadata['finished_at'] = utc()
        save(out, metadata, rows, status)
        print('RESULT', status, out, flush=True)
    return 0 if status == 'complete' and all(r['pass'] for r in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
