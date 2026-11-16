"""Prepared live smoke; no inference unless explicitly enabled after approval.

Run from repository: PYTHONPATH=src python tests/async_llm/live_smoke.py --help
"""
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import re
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler

from agent.llm.async_adapter import AsyncOllamaAdapter, AsyncLLMError, _decode
from agent.llm.interface import Message, ToolDefinition
from agent.llm.ollama import OllamaConfig, _NoRedirect

MODEL = 'qwen3:4b-instruct-2507-q4_K_M'
MIN_RAM = 1024**3
TOOL = ToolDefinition('service_status', 'Synthetic read-only fixture, never executed', {
    'type':'object', 'properties':{'unit':{'const':'nginx.service'}},
    'required':['unit'], 'additionalProperties':False})


class SmokeError(Exception):
    pass


def metadata():
    """Only version/tags/show, never chat/generate/pull or configuration writes."""
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    def read(path, body=None):
        request = Request('http://127.0.0.1:11434'+path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type':'application/json'})
        with opener.open(request, timeout=5) as response:
            raw = response.read(1024*1024+1)
            if len(raw)>1024*1024:
                raise SmokeError('METADATA_LIMIT')
            return _decode(raw)
    try:
        version = read('/api/version').get('version')
        model = next((m for m in read('/api/tags').get('models', []) if m.get('name')==MODEL), None)
        if model is None:
            raise SmokeError('MODEL_NOT_INSTALLED')
        show = read('/api/show', {'model':MODEL})
        quantization = show.get('details',{}).get('quantization_level')
        capabilities = show.get('capabilities', [])
        if quantization != 'Q4_K_M' or 'tools' not in capabilities:
            raise SmokeError('MODEL_METADATA_MISMATCH')
        digest = model.get('digest')
        if (not isinstance(version,str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.]+)?',version)
                or not isinstance(digest,str) or not re.fullmatch(r'(?:sha256:)?[a-f0-9]{64}',digest)):
            raise SmokeError('INVALID_METADATA')
        return {'version':version, 'model':MODEL, 'digest':digest, 'quantization':quantization,
                'tools_capability':True, 'thinking_behavior':'NOT VERIFIED; capability labels are not proof'}
    except SmokeError:
        raise
    except Exception:
        raise SmokeError('METADATA_UNAVAILABLE') from None


def available_ram():
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1])*1024
    except Exception:
        pass
    raise SmokeError('RAM_UNAVAILABLE')


async def _request(adapter, messages, tools, require_tool, ram_samples):
    value = available_ram();ram_samples.append(value)
    if value < MIN_RAM:
        raise SmokeError('RAM_LOW')
    task = asyncio.create_task(adapter.complete(messages, tools, require_tool=require_tool))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=.1)
            value = available_ram();ram_samples.append(value)
            if value < MIN_RAM:
                raise SmokeError('RAM_LOW')
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _finish_owned(adapter):
    # Never let asyncio.run close its loop with an unconfirmed live owner.
    warned = False
    while adapter.cleanup_pending:
        try:
            await adapter.finish_cleanup()
        except AsyncLLMError as error:
            if error.code != 'CLEANUP_PENDING':
                raise
            if not warned:
                print('CLEANUP_PENDING: giữ owner, chờ rejoin; backend inference UNKNOWN.', file=sys.stderr)
                warned = True
            await asyncio.sleep(.05)


async def run_live(info, *, adapter_factory=AsyncOllamaAdapter):
    """At most TWO inference attempts, no retries; stop on first failed gate."""
    adapter = adapter_factory(OllamaConfig(), seconds=100, max_calls=2, cleanup_seconds=2)
    report = {'input_kind':'SYNTHETIC ONLY', 'metadata':info, 'cases':[],
              'linux_diagnostics':'NOT RUN', 'backend_inference_cancellation':'NOT VERIFIED'}
    samples=[]
    error = None
    try:
        start = time.monotonic()
        selected = await _request(adapter, (
            Message('system','SYNTHETIC DEMO. Use one native tool call; no reasoning or real actions.'),
            Message('user','Select service_status for synthetic nginx.service. Do not execute it.')),
            (TOOL,), True, samples)
        if (selected.kind!='tool_calls' or len(selected.tool_calls)!=1
                or selected.tool_calls[0].name!='service_status'
                or selected.tool_calls[0].arguments!={'unit':'nginx.service'}):
            raise SmokeError('TOOL_SELECTION_INVALID')
        report['cases'].append({'case':'tool_selection','valid':True,'tool':'service_status',
            'arguments':{'unit':'nginx.service'},'usage':asdict(selected.usage),
            'http_latency_seconds':selected.latency_seconds,'wall_seconds':time.monotonic()-start})
        if adapter.cleanup_pending:
            raise SmokeError('CLEANUP_UNCONFIRMED')
        start=time.monotonic()
        concluded = await _request(adapter, (
            Message('system','SYNTHETIC DEMO. Return only JSON with conclusion and citations. '
                    'No tool calls, reasoning, repair or claims about real Linux.'),
            Message('user','Synthetic evidence smoke-e1: nginx.service active in a fixture. '
                    'HTTP health is unknown. Summarize briefly; citations must be ["smoke-e1"].')),
            (), False, samples)
        item=_decode(concluded.text)
        if (concluded.kind!='text' or type(item)is not dict or set(item)!={'conclusion','citations'}
                or type(item['conclusion'])is not str or not item['conclusion'].strip()
                or len(item['conclusion'])>2048 or item['citations']!=['smoke-e1']):
            raise SmokeError('CONCLUSION_INVALID')
        # Do not save raw model content/reasoning; only validated structural metrics.
        report['cases'].append({'case':'conclusion','valid':True,'citations':['smoke-e1'],
            'usage':asdict(concluded.usage),'http_latency_seconds':concluded.latency_seconds,
            'wall_seconds':time.monotonic()-start})
    except (AsyncLLMError,SmokeError) as failure:
        error = failure.code if isinstance(failure,AsyncLLMError) else str(failure)
    except asyncio.CancelledError:
        error = 'CANCELLED'
    except Exception:
        error = 'SMOKE_FAILURE'
    finally:
        await _finish_owned(adapter)
    report.update(inference_attempts=adapter.calls_used,
                  cleanup_confirmed=not adapter.cleanup_pending and adapter.worker_pid is None,
                  last_worker_returncode=adapter.last_returncode,
                  minimum_available_ram_gib=min(samples)/1024**3 if samples else None,
                  status='PASS' if error is None and len(report['cases'])==2 else 'FAIL')
    if error:
        report['error_code']=error
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    choice=parser.add_mutually_exclusive_group()
    choice.add_argument('--preflight-only',action='store_true',help='Read metadata only; zero inference')
    choice.add_argument('--run-live-two-requests',action='store_true',help='Use ONLY after separate Hiền approval')
    args=parser.parse_args(argv)
    if not args.preflight_only and not args.run_live_two_requests:
        print(json.dumps({'live_ollama':'NOT RUN','reason':'Separate approval and explicit flag required'}))
        return 0
    try:
        info=metadata()
        if args.preflight_only:
            print(json.dumps({'live_ollama':'NOT RUN','inference_attempts':0,'metadata':info,
                              'available_ram_gib':available_ram()/1024**3},ensure_ascii=False))
            return 0
        report=asyncio.run(run_live(info))
        print(json.dumps(report,ensure_ascii=False))
        return 0 if report['status']=='PASS' else 1
    except KeyboardInterrupt:
        print(json.dumps({'status':'CANCELLED','backend_inference_cancellation':'NOT VERIFIED'}))
        return 130
    except Exception:
        print(json.dumps({'status':'FAIL','error_code':'PREFLIGHT_OR_CLEANUP_FAILED'}))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
