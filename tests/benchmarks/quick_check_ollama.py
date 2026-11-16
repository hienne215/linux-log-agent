#!/usr/bin/env python3
"""SCRUM-14 follow-up: exactly six mock trials, one/case, supported think=True.

Only output budget changes vs baseline think=True repetition 1. No tool dispatch.
This entrypoint has no repeats/mode/full-run flags and never invokes baseline main.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics
import time
from urllib.error import URLError
import benchmark_ollama as bench

HERE = Path(__file__).resolve().parent
OLD = HERE / 'results/20261101T162601152976Z/results.json'


def write(out, metadata, rows, status):
    result = {'metadata': metadata, 'status': status, 'summary': bench.summarize(rows), 'rows': rows}
    temp = out / 'results.json.tmp'
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    temp.replace(out / 'results.json')
    if rows:
        fields = sorted(set().union(*(r.keys() for r in rows)))
        with (out / 'results.csv').open('w', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                              for k, v in row.items()} for row in rows)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    suite_path = HERE / 'cases.json'
    suite = json.loads(suite_path.read_text())
    old = json.loads(OLD.read_text())
    assert len(suite['cases']) == 6 and len({c['id'] for c in suite['cases']}) == 6
    assert hashlib.sha256(suite_path.read_bytes()).hexdigest() == old['metadata']['suite_sha256']
    baseline = [r for r in old['rows'] if r['think'] is True and r['repetition'] == 1]
    assert len(baseline) == 6
    url = bench.local_url('http://localhost:11434')
    out = HERE / 'quick_checks' / time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    out.mkdir(parents=True, exist_ok=False)
    options = {**old['metadata']['options'], 'num_predict': 512, 'seed': 42}
    metadata = {'started_at': bench.utc(), 'model': 'qwen3:4b', 'planned_trials': 6,
                'think': True, 'options': options, 'timeout_s': 90, 'total_budget_s': 600,
                'base_url': url, 'suite_sha256': old['metadata']['suite_sha256'],
                'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'reused_harness_sha256': hashlib.sha256((HERE / 'benchmark_ollama.py').read_bytes()).hexdigest(),
                'baseline_result_sha256': hashlib.sha256(OLD.read_bytes()).hexdigest(),
                'baseline': str(OLD.relative_to(HERE)),
                'baseline_comparison': 'think=true, repetition=1, seed=42, same six cases/tools/prompts/order',
                'changed_generation_option': {'num_predict': [256, 512]},
                'changed_deadline_s': [45, 90],
                'gate': 'unchanged; quick six trials cannot qualify 18-trial gate',
                'warmup': 'none; cold/warm status and load duration retained; no warmup inference'}
    rows, status = [], 'preflight'
    start = time.monotonic()
    try:
        metadata['ollama_version'] = bench.http_json(url + '/api/version')['version']
        tags = bench.http_json(url + '/api/tags')['models']
        model = next((m for m in tags if m.get('name') == metadata['model']), None)
        if not model or model.get('digest') != old['metadata']['model_digest']:
            raise ValueError('Installed model missing or digest changed; no pull/fallback allowed')
        metadata['model_digest'] = model['digest']
        metadata['loaded_before'] = bench.http_json(url + '/api/ps')
        show = bench.http_json(url + '/api/show', {'model': metadata['model']})
        metadata['thinking'] = show.get('thinking')
        metadata['capabilities'] = show.get('capabilities')
        metadata['model_identity'] = {k: v for k, v in show.get('model_info', {}).items() if k.startswith('general.')}
        metadata['model_parameters'] = show.get('parameters')
        template = show.get('template', '')
        metadata['template_sha256'] = hashlib.sha256(template.encode()).hexdigest()
        metadata['template_always_opens_think'] = '<think>' in template and '$.Think' not in template
        metadata['system_mem_before'] = bench.proc_snapshot()['mem']
        if not any(v is True for v in (show.get('thinking') or {}).get('values', [])):
            raise ValueError('Server does not advertise boolean think=true; stop rather than guessing')
        (out / 'preflight.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
        status = 'running'
        write(out, metadata, rows, status)
        for case in suite['cases']:
            if time.monotonic() - start > 510:
                status = 'incomplete_total_budget'
                break
            available = bench.proc_snapshot()['mem'].get('MemAvailable')
            if available is not None and available < 1024 * bench.MIB:
                status = 'incomplete_low_memory'
                break
            request = {'model': metadata['model'], 'messages': [
                {'role': 'system', 'content': suite['system_prompt']},
                {'role': 'user', 'content': case['prompt']}],
                'tools': suite['tools'], 'think': True, 'stream': False, 'keep_alive': '5m', 'options': options}
            print(f"START {len(rows)+1}/6 {case['id']} think=true max_tokens=512", flush=True)
            trial_start = bench.utc()
            with bench.Sampler() as sampler:
                result, latency = bench.bounded_chat(url + '/api/chat', request, 90)
            response = result.get('response')
            row = {'case_id': case['id'], 'case_name': case['name'], 'repetition': 1, 'think': True,
                   'seed': 42, 'started_at': trial_start, 'wall_latency_s': latency,
                   'response_received': 'response' in result, 'expected': case['expected'],
                   'request_sha256': hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest(),
                   **sampler.summary(), **bench.evaluate(response, case['expected'], suite['tools'])}
            if result.get('error_kind'):
                row.update(classification=result['error_kind'], error=result['error'], joint_success=False)
            elif isinstance(response, dict):
                if response.get('error'):
                    row.update(classification='api_error', error=str(response['error'])[:300], joint_success=False)
                row['done_reason'] = response.get('done_reason')
                row['done'] = response.get('done')
                for name in ('total_duration', 'load_duration', 'prompt_eval_duration', 'eval_duration'):
                    value = response.get(name)
                    row[name + '_s'] = value / 1e9 if isinstance(value, (int, float)) else None
                for name in ('prompt_eval_count', 'eval_count'):
                    row[name] = response.get(name)
                row['tokens_per_second'] = row['eval_count'] / row['eval_duration_s'] if row.get('eval_count') is not None and row.get('eval_duration_s') else None
            previous = next(r for r in baseline if r['case_id'] == case['id'])
            row['baseline_256'] = {k: previous.get(k) for k in ('joint_success', 'classification', 'wall_latency_s', 'eval_count', 'done_reason')}
            rows.append(row)
            write(out, metadata, rows, status)
            print(f"END {case['id']} {row['classification']} {latency:.2f}s tokens={row.get('eval_count')}", flush=True)
            if row['classification'] in ('timeout', 'json_error', 'http_error', 'api_error'):
                status = 'incomplete_' + row['classification']
                break
        else:
            status = 'complete_quick_check'
    except KeyboardInterrupt:
        status = 'interrupted'
    except (OSError, ValueError, KeyError, URLError) as error:
        metadata['error'] = str(error)[:500]
        status = 'preflight_failed'
    finally:
        metadata['finished_at'] = bench.utc()
        metadata['elapsed_s'] = time.monotonic() - start
        metadata['system_mem_after'] = bench.proc_snapshot()['mem']
        write(out, metadata, rows, status)
        print(f'RESULT {status}: {len(rows)}/6 trials; {out}', flush=True)
    return 0 if status == 'complete_quick_check' else 2


if __name__ == '__main__':
    raise SystemExit(main())
