#!/usr/bin/env python3
"""SCRUM-14: native Ollama tool selection, metadata only; NEVER dispatch tools.

Python standard library only. All network requests are restricted to loopback.
A disposable HTTP client process enforces a wall deadline; no Ollama process is killed.
"""
import argparse
import csv
import hashlib
import ipaddress
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import socket
import statistics
import threading
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
MIB = 1024 ** 2


def utc():
    return datetime.now(timezone.utc).isoformat()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('', '/') or parsed.port != 11434):
        raise ValueError('Only http://localhost:11434 (or literal loopback) is permitted')
    # Resolve the permitted hostname once and pin subsequent requests to a literal.
    addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port)}
    if not addresses or not all(ipaddress.ip_address(a).is_loopback for a in addresses):
        raise ValueError('Endpoint does not resolve exclusively to loopback')
    return 'http://[::1]:11434' if parsed.hostname == '::1' else 'http://127.0.0.1:11434'


def http_json(url, payload=None, timeout=10):
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(url, data=data, headers={'Content-Type': 'application/json'})
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(2 * MIB + 1)
    if len(raw) > 2 * MIB:
        raise ValueError('Response exceeds 2 MiB safety limit')
    return json.loads(raw)


def _http_worker(pipe, url, payload, timeout):
    try:
        pipe.send({'response': http_json(url, payload, timeout)})
    except json.JSONDecodeError as error:
        pipe.send({'error_kind': 'json_error', 'error': str(error)})
    except HTTPError as error:
        pipe.send({'error_kind': 'http_error', 'error': f'HTTP {error.code}'})
    except (socket.timeout, TimeoutError):
        pipe.send({'error_kind': 'timeout', 'error': 'HTTP request deadline reached'})
    except (OSError, URLError, ValueError) as error:
        pipe.send({'error_kind': 'api_error', 'error': str(error)[:300]})
    finally:
        pipe.close()


def bounded_chat(url, payload, timeout):
    context = mp.get_context('spawn')
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_http_worker, args=(child, url, payload, timeout))
    started = time.monotonic()
    process.start()
    child.close()
    try:
        if parent.poll(timeout):
            try:
                result = parent.recv()
            except EOFError:
                result = {'error_kind': 'api_error', 'error': 'HTTP client exited unexpectedly'}
        else:
            result = {'error_kind': 'timeout', 'error': 'Hard wall deadline; HTTP client closed'}
    finally:
        # This terminates only our disposable client, NEVER the server/runner.
        if process.is_alive():
            process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join()
        parent.close()
    return result, time.monotonic() - started


def proc_snapshot():
    mem = {}
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, value = line.split(':', 1)
            mem[key] = int(value.strip().split()[0]) * 1024
    except (OSError, ValueError):
        pass
    processes = {}
    try:
        entries = list(Path('/proc').iterdir())
    except OSError:
        entries = []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / 'stat').read_text()
            comm = stat[stat.index('(') + 1:stat.rindex(')')]
            fields = stat[stat.rindex(')') + 1:].split()
            processes[int(entry.name)] = {
                'ppid': int(fields[1]), 'comm': comm, 'start': int(fields[19]),
                'ticks': int(fields[11]) + int(fields[12]),
                'rss': max(0, int(fields[21])) * os.sysconf('SC_PAGE_SIZE')}
        except (OSError, ValueError, IndexError):
            continue
    selected = {pid for pid, data in processes.items() if data['comm'].lower().startswith('ollama')}
    while True:
        children = {pid for pid, data in processes.items() if data['ppid'] in selected}
        if children <= selected:
            break
        selected |= children
    return {'at': time.monotonic(), 'mem': mem,
            'processes': {pid: processes[pid] for pid in selected}}


class Sampler:
    def __init__(self):
        self.samples = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.is_set():
            self.samples.append(proc_snapshot())
            self.stop.wait(0.5)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *unused):
        self.stop.set()
        self.thread.join()
        self.samples.append(proc_snapshot())

    def summary(self):
        rss = [sum(p['rss'] for p in s['processes'].values()) / MIB
               for s in self.samples if s['processes']]
        available = [s['mem']['MemAvailable'] / MIB for s in self.samples
                     if 'MemAvailable' in s['mem']]
        swap = [(s['mem']['SwapTotal'] - s['mem']['SwapFree']) / MIB
                for s in self.samples if 'SwapTotal' in s['mem'] and 'SwapFree' in s['mem']]
        cpu_seconds, sampled_seconds = 0.0, 0.0
        for previous, current in zip(self.samples, self.samples[1:]):
            elapsed = current['at'] - previous['at']
            common = [(old, current['processes'][pid]) for pid, old in previous['processes'].items()
                      if pid in current['processes'] and old['start'] == current['processes'][pid]['start']]
            if common and elapsed > 0:
                cpu_seconds += sum(max(0, new['ticks'] - old['ticks']) for old, new in common) / os.sysconf('SC_CLK_TCK')
                sampled_seconds += elapsed
        cpu = 100 * cpu_seconds / sampled_seconds if sampled_seconds else None
        return {'ollama_tree_rss_peak_mib': max(rss) if rss else None,
                'ollama_tree_cpu_pct_one_core': cpu,
                'ollama_tree_cpu_pct_machine': cpu / (os.cpu_count() or 1) if cpu is not None else None,
                'system_available_min_mib': min(available) if available else None,
                'system_swap_used_max_mib': max(swap) if swap else None}


def evaluate(response, expected, tools):
    score = {'classification': 'invalid_structure', 'valid_structure': False,
             'tool_correct': False, 'arguments_correct': False, 'joint_success': False,
             'argument_schema_valid': False, 'tool_calls': None,
             'content_chars': 0, 'thinking_chars': 0, 'content_think_marker': False}
    if not isinstance(response, dict) or not isinstance(response.get('message'), dict):
        return score
    message = response['message']
    content, thinking = message.get('content', ''), message.get('thinking', '')
    score.update(content_chars=len(content) if isinstance(content, str) else 0,
                 thinking_chars=len(thinking) if isinstance(thinking, str) else 0,
                 content_think_marker=isinstance(content, str) and '<think' in content.lower())
    calls = message.get('tool_calls')
    score['tool_calls'] = calls
    if calls is None or calls == []:
        score['classification'] = 'text_only' if content or thinking else 'no_tool_call'
        return score
    if message.get('role') != 'assistant' or not isinstance(calls, list) or len(calls) != 1:
        return score
    function = calls[0].get('function') if isinstance(calls[0], dict) else None
    if not isinstance(function, dict) or not isinstance(function.get('name'), str) or not isinstance(function.get('arguments'), dict):
        return score
    score['valid_structure'] = True
    name, arguments = function['name'], function['arguments']
    schemas = {t['function']['name']: t['function']['parameters'] for t in tools}
    schema = schemas.get(name)
    if schema:
        properties = schema['properties']
        valid = set(schema.get('required', [])) <= set(arguments) <= set(properties)
        for key, value in arguments.items():
            spec = properties.get(key, {})
            if spec.get('type') == 'string':
                valid = valid and isinstance(value, str)
            if spec.get('type') == 'integer':
                valid = valid and type(value) is int
                valid = valid and type(value) is int and spec.get('minimum', value) <= value <= spec.get('maximum', value)
            if 'enum' in spec:
                valid = valid and value in spec['enum']
        score['argument_schema_valid'] = bool(valid)
    score['tool_correct'] = name == expected['name']
    # Strict JSON equivalence prevents True == 1 and "80" == 80 from passing.
    score['arguments_correct'] = json.dumps(arguments, sort_keys=True) == json.dumps(expected['arguments'], sort_keys=True)
    score['joint_success'] = (score['tool_correct'] and score['arguments_correct']
                              and score['argument_schema_valid'] and response.get('done') is True)
    score['classification'] = ('correct' if score['joint_success'] else
                               'wrong_tool' if not score['tool_correct'] else 'wrong_arguments'
                               if not score['arguments_correct'] or not score['argument_schema_valid'] else 'incomplete_response')
    return score


def average(values):
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def summarize(rows):
    groups = {}
    for mode in (False, True):
        subset = [r for r in rows if r['think'] is mode]
        if not subset:
            continue
        n = len(subset)
        good_durations = [r['wall_latency_s'] for r in subset if r.get('response_received')]
        latency = [r['wall_latency_s'] for r in subset]
        token_rows = [r for r in subset if r.get('eval_count') is not None and (r.get('eval_duration_s') or 0) > 0]
        counts = {}
        for row in subset:
            counts[row['classification']] = counts.get(row['classification'], 0) + 1
        groups[str(mode).lower()] = {
            'attempted': n, 'joint_success_count': sum(r['joint_success'] for r in subset),
            **{key + '_rate': sum(r[key] for r in subset) / n for key in
               ('valid_structure', 'tool_correct', 'arguments_correct', 'joint_success')},
            'latency_mean_s_all_attempts': average(latency),
            'latency_median_s_all_attempts': statistics.median(latency),
            'latency_mean_s_responses_only': average(good_durations),
            'tokens_per_second_weighted': (sum(r['eval_count'] for r in token_rows) /
                                           sum(r['eval_duration_s'] for r in token_rows)) if token_rows else None,
            'token_metrics_samples': len(token_rows), 'classifications': counts,
            'content_nonempty_count': sum(r.get('content_chars', 0) > 0 for r in subset),
            'thinking_nonempty_count': sum(r.get('thinking_chars', 0) > 0 for r in subset),
            'content_think_marker_count': sum(r.get('content_think_marker', False) for r in subset),
            'ollama_tree_rss_peak_mib': max((r['ollama_tree_rss_peak_mib'] for r in subset if r.get('ollama_tree_rss_peak_mib') is not None), default=None),
            'ollama_tree_cpu_pct_machine_mean': average([r.get('ollama_tree_cpu_pct_machine') for r in subset]),
            'system_available_min_mib': min((r['system_available_min_mib'] for r in subset if r.get('system_available_min_mib') is not None), default=None)}
    return groups


def fmt(value, precision=2):
    return 'N/A' if value is None else f'{value:.{precision}f}'


def write_results(out, metadata, suite, rows, status):
    summaries = summarize(rows)
    result = {'metadata': metadata, 'status': status, 'summary': summaries, 'rows': rows}
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
    lines = ['# SCRUM-14 — Qwen3 4B Smoke Benchmark', '',
             f"Trạng thái: **{status}**; {len(rows)}/{metadata['planned_trials']} lượt đo. Không thực thi tool Linux hoặc MCP thật.", '',
             f"Bắt đầu UTC: {metadata['started_at']}. Model: `{metadata['model']}`; Ollama: `{metadata.get('ollama_version', 'N/A')}`.",
             f"Digest: `{metadata.get('model_digest', 'N/A')}`. Cấu hình: `{json.dumps(metadata['options'], sort_keys=True)}`.",
             f"Timeout {metadata['timeout_s']} s/request; budget toàn bộ {metadata['total_budget_s']} s; mỗi mode {metadata['repeats']} lần/case.", '',
             '## Kết quả theo chế độ', '',
             '| think | n | Tool đúng | Args đúng | Structure | Đúng toàn bộ | Mean s | Median s | Token/s |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for mode, s in summaries.items():
        lines.append(f"| {mode} | {s['attempted']} | {s['tool_correct_rate']:.1%} | {s['arguments_correct_rate']:.1%} | {s['valid_structure_rate']:.1%} | {s['joint_success_rate']:.1%} | {fmt(s['latency_mean_s_all_attempts'])} | {fmt(s['latency_median_s_all_attempts'])} | {fmt(s['tokens_per_second_weighted'])} |")
    lines += ['', '## Từng tình huống', '', '| Case | Mode | Đúng toàn bộ / đã chạy | Mean s | Phân loại |', '|---|---|---:|---:|---|']
    for case in suite['cases']:
        for mode in (False, True):
            subset = [r for r in rows if r['case_id'] == case['id'] and r['think'] is mode]
            counts = {k: sum(r['classification'] == k for r in subset) for k in sorted({r['classification'] for r in subset})}
            lines.append(f"| {case['id']} {case['name']} | {str(mode).lower()} | {sum(r['joint_success'] for r in subset)}/{len(subset)} | {fmt(average([r['wall_latency_s'] for r in subset]))} | {json.dumps(counts, ensure_ascii=False)} |")
    lines += ['', '## RAM, CPU và biểu hiện thinking', '',
              'RSS là tổng RSS của process tree Ollama đọc từ /proc mỗi 0,5 s; có thể đếm trùng shared pages, bỏ lỡ đỉnh ngắn và thiếu process không đọc được. Không phải RAM riêng chính xác của model. CPU chuẩn 100% = một logical core; machine % chia số logical CPUs. Đây là CPU server/runner, không phải riêng benchmark nếu có client khác. MemAvailable và swap phản ánh toàn máy.', '']
    for mode, s in summaries.items():
        lines.append(f"- think={mode}: peak RSS {fmt(s['ollama_tree_rss_peak_mib'])} MiB; CPU machine trung bình {fmt(s['ollama_tree_cpu_pct_machine_mean'])}%; MemAvailable thấp nhất {fmt(s['system_available_min_mib'])} MiB. Content không rỗng {s['content_nonempty_count']}/{s['attempted']}; thinking field không rỗng {s['thinking_nonempty_count']}/{s['attempted']}; content có think marker {s['content_think_marker_count']}/{s['attempted']}.")
    lines += ['', '## Quyết định model và giới hạn', '',
              'Gate định trước cho khuyến nghị smoke: đủ 18/18 lượt của một mode (6 case × 3), đúng tool + exact args + schema + native structure + done ở ít nhất 17/18, structure 18/18, không timeout/JSON/API error, median ≤30 s và MemAvailable tối thiểu ≥1024 MiB. Đây là gate thực dụng của smoke test, không phải metric root-cause accuracy ≥80% của đánh giá cuối dự án.', '']
    qualified = []
    for mode, s in summaries.items():
        mode_rows = [r for r in rows if str(r['think']).lower() == mode]
        complete = len(mode_rows) == 18 and all(sum(r['case_id'] == c['id'] for r in mode_rows) == 3 for c in suite['cases'])
        if (complete and s['joint_success_count'] >= 17 and s['valid_structure_rate'] == 1
                and not any(r['classification'] in ('timeout', 'json_error', 'api_error', 'http_error') for r in mode_rows)
                and s['latency_median_s_all_attempts'] <= 30
                and s['system_available_min_mib'] is not None and s['system_available_min_mib'] >= 1024):
            qualified.append(mode)
    if status == 'complete' and qualified:
        best = min(qualified, key=lambda m: summaries[m]['latency_median_s_all_attempts'])
        lines.append(f"Khuyến nghị có điều kiện: Qwen3 4B ở think={best} cho baseline mock tool selection. Cần Trang review RAM khi VM chạy đồng thời và nhóm review trước khi chốt ADR model chính. Chưa chứng minh chẩn đoán nhiều bước, evidence correctness hoặc an toàn write.")
    else:
        lines.append('Chưa đủ điều kiện chọn Qwen3 4B làm LLM chính theo gate smoke đã định. Giữ là ứng viên; không tuyên bố đạt. Xem lỗi theo mode và điều chỉnh có review trong một run mới, không sửa điểm hoặc trộn cấu hình vào run này.')
    lines += ['',
              '- Tool accuracy, exact-argument accuracy và native-structure validity được tách riêng; joint success đòi hỏi cả ba, argument schema hợp lệ và response done. Không trích JSON trong content thành tool call.',
              '- JSON error chỉ JSON HTTP không parse được; arguments là JSON string thay vì object thuộc invalid_structure. Text có JSON nhưng không có native tool_calls vẫn text_only.',
              '- Mọi attempt, kể cả timeout/error, nằm trong denominator và thống kê latency toàn bộ. Token/s = tổng eval_count / tổng eval_duration_s của response có metrics; gồm token thinking nếu server tính chúng, không phải usable-tool throughput. Response-only mean có trong JSON.',
              '- Hai mode dùng cùng cases, tool order, prompt, context, token budget, threads và seed ghép cặp theo repetition; thứ tự mode luân phiên theo case/repetition. Cache, OS load và thermal effects chưa loại trừ; không có statistical significance với n=18.',
              '- think là giá trị request, không chứng minh server đã tuân thủ. Chỉ ghi độ dài field/marker, không lưu reasoning text. Có thể output token cap ngắt reasoning trước tool; done_reason được giữ trong JSON.',
              '- Không tự tải model, gọi tool, restart, đổi server config hoặc unload model. HTTP client deadline đóng client; không bảo đảm server đã dừng ngay khi client ngắt. Sau timeout dừng run để tránh request chồng nhau.',
              '- Kết quả thủ công 55,08 s / 653 tokens / 12,54 token/s do người dùng cung cấp không được cộng vào 36 lượt này. Chỉ một model local; API chưa được thử, ngân sách API = 0, không gửi dữ liệu ra ngoài.',
              '- Chưa có evidence hoàn tất P00-04/VM snapshot; benchmark mock trên host theo phạm vi người dùng cho phép không thay thế prerequisite kiểm RAM VM. Không push, PR hoặc Jira Done trong task này.', '',
              'Nguồn API: [chat](https://docs.ollama.com/api/chat), [thinking](https://docs.ollama.com/capabilities/thinking), [running models](https://docs.ollama.com/api/ps). API durations được đổi từ ns sang s; request think và field thinking được ghi riêng. Tài liệu hiện hành không thay thế version/capability thực đo ở metadata.', '']
    (out / 'REPORT.md').write_text('\n'.join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://localhost:11434')
    parser.add_argument('--model', default='qwen3:4b')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--num-predict', type=int, default=256)
    parser.add_argument('--num-ctx', type=int, default=4096)
    parser.add_argument('--num-thread', type=int, default=4)
    parser.add_argument('--timeout', type=float, default=45)
    parser.add_argument('--total-budget', type=float, default=1500)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    if not (1 <= args.repeats <= 3 and 1 <= args.num_predict <= 512 and 512 <= args.num_ctx <= 4096
            and 1 <= args.num_thread <= 8 and 1 <= args.timeout <= 90 and 1 <= args.total_budget <= 2400):
        parser.error('Outside safety limits: repeats 1..3, tokens 1..512, context 512..4096, threads 1..8, timeout 1..90, budget 1..2400')
    base = local_url(args.base_url)
    suite_path = HERE / 'cases.json'
    suite = json.loads(suite_path.read_text())
    out = args.output_dir or HERE / 'results' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True, exist_ok=False)
    options = {'temperature': 0, 'num_ctx': args.num_ctx, 'num_predict': args.num_predict, 'num_thread': args.num_thread}
    metadata = {'started_at': utc(), 'model': args.model, 'base_url': base, 'options': options,
                'repeats': args.repeats, 'timeout_s': args.timeout, 'total_budget_s': args.total_budget,
                'planned_trials': len(suite['cases']) * args.repeats * 2,
                'suite_sha256': hashlib.sha256(suite_path.read_bytes()).hexdigest(),
                'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'python': platform.python_version(), 'platform': platform.platform(), 'logical_cpus': os.cpu_count(),
                'metrics_source': '/proc snapshots; Ollama native durations/counts',
                'trial_order': 'repetition, case, alternating mode first by (repetition+case_index)%2',
                'warmup': 'none; existing loaded state recorded; load_duration retained',
                'manual_reference_excluded': {'total_duration_s': 55.08, 'prompt_eval_s': 2.98, 'eval_count': 653, 'tokens_per_second': 12.54}}
    rows, status = [], 'preflight'
    started = time.monotonic()
    try:
        metadata['ollama_version'] = http_json(base + '/api/version')['version']
        tags = http_json(base + '/api/tags')['models']
        model = next((m for m in tags if args.model in (m.get('name'), m.get('model'))), None)
        if not model:
            raise ValueError('Model not installed; refusing to pull or use another model')
        metadata['model_digest'] = model.get('digest')
        metadata['model_details'] = model.get('details')
        metadata['loaded_before'] = http_json(base + '/api/ps')
        show = http_json(base + '/api/show', {'model': args.model})
        metadata['show'] = {k: show.get(k) for k in ('capabilities', 'thinking', 'parameters', 'details')}
        metadata['template_sha256'] = hashlib.sha256(str(show.get('template', '')).encode()).hexdigest()
        metadata['system_mem_before'] = proc_snapshot()['mem']
        status = 'running'
        write_results(out, metadata, suite, rows, status)
        for repetition in range(args.repeats):
            for case_index, case in enumerate(suite['cases']):
                modes = (False, True) if (repetition + case_index) % 2 == 0 else (True, False)
                for think in modes:
                    remaining = args.total_budget - (time.monotonic() - started)
                    if remaining < args.timeout:
                        status = 'incomplete_total_budget'
                        raise StopIteration
                    available = proc_snapshot()['mem'].get('MemAvailable')
                    if available is not None and available < 1024 * MIB:
                        status = 'incomplete_low_memory'
                        raise StopIteration
                    request = {'model': args.model, 'messages': [
                        {'role': 'system', 'content': suite['system_prompt']},
                        {'role': 'user', 'content': case['prompt']}],
                        'tools': suite['tools'], 'stream': False, 'think': think, 'keep_alive': '5m',
                        'options': {**options, 'seed': 42 + repetition}}
                    print(f"START {len(rows)+1}/{metadata['planned_trials']} {case['id']} rep={repetition+1} think={think}", flush=True)
                    trial_started_at = utc()
                    with Sampler() as sampler:
                        result, latency = bounded_chat(base + '/api/chat', request, args.timeout)
                    row = {'case_id': case['id'], 'case_name': case['name'], 'repetition': repetition + 1,
                           'think': think, 'seed': 42 + repetition, 'started_at': trial_started_at,
                           'wall_latency_s': latency, 'response_received': 'response' in result,
                           'request_sha256': hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest(),
                           'expected': case['expected'], **sampler.summary()}
                    response = result.get('response')
                    row.update(evaluate(response, case['expected'], suite['tools']))
                    if result.get('error_kind'):
                        row.update(classification=result['error_kind'], error=result['error'])
                    elif isinstance(response, dict):
                        if response.get('error'):
                            row.update(classification='api_error', error=str(response['error'])[:300], joint_success=False)
                        row['done_reason'] = response.get('done_reason')
                        for field in ('total_duration', 'load_duration', 'prompt_eval_duration', 'eval_duration'):
                            value = response.get(field)
                            row[field + '_s'] = value / 1e9 if isinstance(value, (int, float)) and value >= 0 else None
                        for field in ('prompt_eval_count', 'eval_count'):
                            row[field] = response.get(field)
                        duration = row.get('eval_duration_s')
                        row['tokens_per_second'] = row.get('eval_count', 0) / duration if duration else None
                    rows.append(row)
                    print(f"END {case['id']} think={think} {row['classification']} {latency:.2f}s", flush=True)
                    write_results(out, metadata, suite, rows, status)
                    if row['classification'] in ('timeout', 'api_error', 'http_error', 'json_error'):
                        status = 'incomplete_' + row['classification']
                        raise StopIteration
        status = 'complete'
    except StopIteration:
        pass
    except KeyboardInterrupt:
        status = 'interrupted'
    except (ValueError, KeyError, OSError, URLError) as error:
        metadata['preflight_error'] = str(error)[:500]
        status = 'preflight_failed'
    finally:
        metadata['finished_at'] = utc()
        metadata['elapsed_s'] = time.monotonic() - started
        metadata['system_mem_after'] = proc_snapshot()['mem']
        write_results(out, metadata, suite, rows, status)
        print(f"RESULT {status}: {len(rows)}/{metadata['planned_trials']} attempts; {out}", flush=True)
    return 0 if status == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
