#!/usr/bin/env python3
"""SCRUM-14 Instruct: exactly 18 mock trials (six cases x three seeds); think omitted, max 256 tokens.
Reuses unchanged cases and scorer. No tool dispatch or downloads. Fixed approved full-run configuration.
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


def summarize(rows):
    durations = [r['wall_latency_s'] for r in rows]
    tokens = sum(r.get('eval_count') or 0 for r in rows)
    generation = sum(r.get('eval_duration_s') or 0 for r in rows)
    return {'trials': len(rows),
            **{key: sum(bool(r.get(key)) for r in rows)
               for key in ('tool_correct', 'arguments_correct', 'valid_structure', 'joint_success')},
            'mean_latency_s': statistics.mean(durations) if durations else None,
            'median_latency_s': statistics.median(durations) if durations else None,
            'output_tokens': tokens,
            'weighted_tokens_per_second': tokens / generation if generation else None}


def write(out, metadata, rows, status):
    summary = summarize(rows)
    checks = {
        'complete_18': status == 'complete_full_benchmark' and len(rows) == 18 and all(sum(r['case_id'] == case for r in rows) == 3 for case in {r['case_id'] for r in rows}),
        'joint_at_least_17': summary['joint_success'] >= 17,
        'valid_structure_18': summary['valid_structure'] == 18,
        'no_errors': bool(rows) and not any(r['classification'] in ('timeout', 'api_error', 'http_error', 'json_error') for r in rows),
        'median_at_most_30s': bool(rows) and summary['median_latency_s'] <= 30,
        'memory_at_least_1024MiB': bool(rows) and all(r.get('system_available_min_mib') is not None and r['system_available_min_mib'] >= 1024 for r in rows)}
    summary['gate_checks'] = checks
    summary['gate_pass'] = all(checks.values())
    result = {'metadata': metadata, 'status': status, 'summary': summary, 'rows': rows}
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
    baseline = [r for r in old['rows'] if r['think'] is True]
    assert len(baseline) == 18
    url = bench.local_url('http://localhost:11434')
    out = HERE / 'instruct_full_results' / time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    out.mkdir(parents=True, exist_ok=False)
    options = {**old['metadata']['options'], 'num_predict': 256, 'seed': 42}
    metadata = {'started_at': bench.utc(), 'model': 'qwen3:4b-instruct-2507-q4_K_M', 'planned_trials': 18,
                'think_request': 'omitted', 'options': options, 'timeout_s': 45, 'total_budget_s': 900, 'seeds': [42, 43, 44], 'unit_tests': '15/15 passed before inference',
                'base_url': url, 'suite_sha256': old['metadata']['suite_sha256'],
                'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'reused_harness_sha256': hashlib.sha256((HERE / 'benchmark_ollama.py').read_bytes()).hexdigest(),
                'baseline_result_sha256': hashlib.sha256(OLD.read_bytes()).hexdigest(),
                'baseline': str(OLD.relative_to(HERE)),
                'baseline_comparison': 'think=true, repetitions=1/2/3, seeds=42/43/44; identical six cases/tools/prompts',
                'changed_model': 'Thinking-2507 -> Instruct-2507; native model defaults retained',
                'changed_deadline_s': [45, 45],
                'gate': '18 complete, joint>=17, structure=18, errors=0, median<=30s, available RAM>=1024MiB',
                'warmup': 'none; cold/warm status and load duration retained; no warmup inference'}
    rows, status = [], 'preflight'
    start = time.monotonic()
    try:
        metadata['ollama_version'] = bench.http_json(url + '/api/version')['version']
        tags = bench.http_json(url + '/api/tags')['models']
        model = next((m for m in tags if m.get('name') == metadata['model']), None)
        if not model or model.get('digest') != '0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0':
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
        metadata['template_contains_think_marker'] = '<think>' in template
        metadata['template_generation_suffix'] = template.rsplit('{%- if add_generation_prompt %}', 1)[-1]
        metadata['system_mem_before'] = bench.proc_snapshot()['mem']
        if show.get('model_info', {}).get('general.finetune') != 'Instruct':
            raise ValueError('Model identity is not Instruct')
        (out / 'model-template.txt').write_text(template)
        (out / 'preflight.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
        status = 'running'
        write(out, metadata, rows, status)
        for repetition, case in [(rep, case) for rep in (1, 2, 3) for case in suite['cases']]:
            options = {**options, 'seed': 41 + repetition}
            if time.monotonic() - start > 855:
                status = 'incomplete_total_budget'
                break
            available = bench.proc_snapshot()['mem'].get('MemAvailable')
            if available is not None and available < 1024 * bench.MIB:
                status = 'incomplete_low_memory'
                break
            request = {'model': metadata['model'], 'messages': [
                {'role': 'system', 'content': suite['system_prompt']},
                {'role': 'user', 'content': case['prompt']}],
                'tools': suite['tools'], 'stream': False, 'keep_alive': '5m', 'options': options}
            assert 'think' not in request
            print(f"START {len(rows)+1}/18 {case['id']} think=omitted max_tokens=256", flush=True)
            trial_start = bench.utc()
            with bench.Sampler() as sampler:
                result, latency = bench.bounded_chat(url + '/api/chat', request, 45)
            response = result.get('response')
            row = {'case_id': case['id'], 'case_name': case['name'], 'repetition': repetition, 'think_request': 'omitted',
                   'seed': 41 + repetition, 'started_at': trial_start, 'wall_latency_s': latency,
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
            previous = next(r for r in baseline if r['case_id'] == case['id'] and r['repetition'] == repetition)
            row['baseline_256'] = {k: previous.get(k) for k in ('joint_success', 'classification', 'wall_latency_s', 'eval_count', 'done_reason')}
            rows.append(row)
            write(out, metadata, rows, status)
            print(f"END {case['id']} {row['classification']} {latency:.2f}s tokens={row.get('eval_count')}", flush=True)
            if row.get('system_available_min_mib') is not None and row['system_available_min_mib'] < 1024:
                status = 'incomplete_low_memory'
                break
            if row['classification'] in ('timeout', 'json_error', 'http_error', 'api_error'):
                status = 'incomplete_' + row['classification']
                break
        else:
            status = 'complete_full_benchmark'
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
        print(f'RESULT {status}: {len(rows)}/18 trials; {out}', flush=True)
    return 0 if status == 'complete_full_benchmark' else 2


if __name__ == '__main__':
    raise SystemExit(main())
