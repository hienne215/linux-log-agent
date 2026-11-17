#!/usr/bin/env python3
"""Optional read-only host /proc sampler for a run inside a PID sandbox.

Run in a normal host terminal, not as root. Never sends model requests or signals.
Keeps separate evidence, does not modify results.json or benchmark scores.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from benchmark_ollama import Sampler, utc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results_dir', type=Path)
    parser.add_argument('--max-seconds', type=float, default=1500)
    args = parser.parse_args()
    if not 1 <= args.max_seconds <= 2400:
        parser.error('max-seconds must be 1..2400')
    directory = args.results_dir.resolve()
    results = directory / 'results.json'
    if not results.is_file():
        parser.error('results.json not found')
    output = directory / ('host-metrics-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    started_wall, started_mono = time.time(), time.monotonic()
    baseline = json.loads(results.read_text())
    print('Host read-only monitor started; completed trials before monitor:', len(baseline['rows']), flush=True)
    with Sampler() as sampler:
        while time.monotonic() - started_mono < args.max_seconds:
            current = json.loads(results.read_text())
            if current['status'] != 'running':
                break
            time.sleep(1)
    ended_wall = time.time()
    current = json.loads(results.read_text())
    summaries = []
    for row in current['rows']:
        trial_start = datetime.fromisoformat(row['started_at']).timestamp()
        trial_end = trial_start + row['wall_latency_s']
        selected = [s for s in sampler.samples
                    if trial_start <= started_wall + s['at'] - started_mono <= trial_end]
        partial = Sampler()
        partial.samples = selected
        summaries.append({'case_id': row['case_id'], 'repetition': row['repetition'], 'think': row['think'],
                          'monitor_covers_entire_trial': started_wall <= trial_start and ended_wall >= trial_end,
                          'sample_count': len(selected), **partial.summary()})
    result = {'scope': 'read-only host /proc, separate from benchmark scores',
              'started_at': datetime.fromtimestamp(started_wall, timezone.utc).isoformat(), 'finished_at': utc(),
              'completed_trials_before_monitor': len(baseline['rows']), 'samples': len(sampler.samples),
              'summary': sampler.summary(), 'trials': summaries,
              'limitations': ['May start after benchmark; only full-coverage rows are comparable.',
                             'RSS sums can double-count shared pages; CPU includes other Ollama clients.',
                             '0.5 second sampling may miss peaks; no inference or process signals issued.']}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(output, json.dumps(result['summary']), flush=True)


if __name__ == '__main__':
    main()
