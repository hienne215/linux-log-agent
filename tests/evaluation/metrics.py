"""Offline, stdlib-only metrics; never executes a model, tool or benchmark."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
import sys

MODES = {'synthetic', 'live_llm_mock_tools', 'live_llm_synthetic_mcp', 'real_linux'}
ERRORS = {'tool_selection','malformed_output','transport','permission','insufficient_evidence',
          'cleanup','timeout','diagnostic','evidence','citation'}
OPTIONAL = {'selection','diagnosis','evidence_assessment','citations','evidence_ids',
            'latency_s','timeout','errors','cleanup_success','tool_dispatches',
            'outside_allowlist_executions','unauthorized_write_attempts','unauthorized_writes_rejected','verification'}
REQUIRED = {'run_id','mode','protocol','config_sha256','revision','latency_scope','provenance'}
ROOT = Path(__file__).resolve().parents[2]
REF = '990f41f68c8ef13218bf825bd57ff233a8c82f1c'
BENCHMARK = 'tests/benchmarks/instruct_full_results/20261101T170502Z/results.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))


def _unique(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError('INVALID_DATASET')
        result[key] = value
    return result


def load(raw):
    if len(raw) > 8*1024*1024:
        raise ValueError('INPUT_TOO_LARGE')
    return json.loads(raw,object_pairs_hook=_unique,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _hash(value, length=64):
    return type(value) is str and re.fullmatch('[0-9a-f]{'+str(length)+'}',value) is not None


def _count(value):
    return type(value) is int and value >= 0


def _assessment(value):
    """Validate declared scoring inputs, not artifact authenticity or reviewer approval."""
    if value is None:
        return False
    if type(value) is not dict or set(value) != {'correct','ground_truth_sha256','reviewer_sha256','rubric_sha256'}:
        raise ValueError('INVALID_ASSESSMENT')
    if value['correct'] is not None and type(value['correct']) is not bool:
        raise ValueError('INVALID_ASSESSMENT')
    for key in ('ground_truth_sha256','reviewer_sha256','rubric_sha256'):
        if value[key] is not None and not _hash(value[key]):
            raise ValueError('INVALID_ASSESSMENT')
    return all(value[k] is not None for k in value)


def validate(run):
    if type(run) is not dict or not REQUIRED <= run.keys() or run.keys() - REQUIRED - OPTIONAL:
        raise ValueError('INVALID_RUN')
    if (not _hash(run['run_id']) or run['mode'] not in MODES
            or type(run['protocol']) is not str or re.fullmatch(r'[a-z0-9_-]{1,64}',run['protocol']) is None
            or not _hash(run['config_sha256']) or not _hash(run['revision'],40)
            or run['latency_scope'] not in {'model_request','diagnosis_session'}):
        raise ValueError('INVALID_PROVENANCE')
    p = run['provenance']
    if (type(p) is not dict or set(p) != {'artifact_sha256','record_pointer','snapshot_sha256'}
            or not _hash(p['artifact_sha256']) or type(p['record_pointer']) is not str
            or re.fullmatch(r'/(?:rows|runs)/(?:0|[1-9][0-9]{0,5})',p['record_pointer']) is None
            or (p['snapshot_sha256'] is not None and not _hash(p['snapshot_sha256']))
            or (run['mode']=='real_linux' and p['snapshot_sha256'] is None)):
        raise ValueError('INVALID_PROVENANCE')
    for key in ('diagnosis','evidence_assessment'):
        _assessment(run.get(key))
    for key in ('timeout','cleanup_success'):
        if run.get(key) is not None and type(run[key]) is not bool:
            raise ValueError('INVALID_BOOLEAN')
    for key in ('tool_dispatches','outside_allowlist_executions','unauthorized_write_attempts','unauthorized_writes_rejected'):
        if run.get(key) is not None and not _count(run[key]):
            raise ValueError('INVALID_COUNT')
    a,b = run.get('unauthorized_write_attempts'),run.get('unauthorized_writes_rejected')
    if (a is None) != (b is None) or (a is not None and b > a):
        raise ValueError('INVALID_DENOMINATOR')
    latency = run.get('latency_s')
    if latency is not None and (type(latency) not in (float,int) or not math.isfinite(latency) or latency < 0):
        raise ValueError('INVALID_LATENCY')
    errors = run.get('errors')
    if errors is not None and (type(errors) is not list or any(type(e) is not str or e not in ERRORS for e in errors)
                               or len(set(errors)) != len(errors)):
        raise ValueError('INVALID_ERRORS')
    for key in ('citations','evidence_ids'):
        if run.get(key) is not None and (type(run[key]) is not list or any(type(x) is not str or not 1 <= len(x) <= 128 for x in run[key])):
            raise ValueError('INVALID_REFERENCES')
    selection = run.get('selection')
    if selection is not None:
        if type(selection) is not dict or set(selection) != {'expected','actual','ground_truth_sha256'} or (selection['ground_truth_sha256'] is not None and not _hash(selection['ground_truth_sha256'])):
            raise ValueError('INVALID_SELECTION')
        if type(selection['actual']) is not list or len(selection['actual']) > 8:
            raise ValueError('INVALID_SELECTION')
        for call in ([selection['expected']] if selection['expected'] is not None else []) + selection['actual']:
            if (type(call) is not dict or set(call) != {'name','arguments'} or type(call['name']) is not str
                    or type(call['arguments']) is not dict):
                raise ValueError('INVALID_SELECTION')
            canonical(call)
    v = run.get('verification')
    if v is not None and (type(v) is not dict or set(v) != {'approved_fix','outcome'}
                         or type(v['approved_fix']) is not bool or v['outcome'] not in {'pass','fail','unknown'}):
        raise ValueError('INVALID_VERIFICATION')


def fraction(numerator,denominator,**extra):
    return {'status':'EVALUATED' if denominator else 'NOT_EVALUABLE',
            'numerator':numerator,'denominator':denominator,
            'value':numerator/denominator if denominator else None,**extra}


def _rate(values, total):
    return fraction(sum(values),len(values),eligible_runs=len(values),missing_runs=total-len(values))


def _distribution(values,total,unit):
    return {'status':'EVALUATED' if values else 'NOT_EVALUABLE','n':len(values),'missing_runs':total-len(values),
            'unit':unit,'mean':statistics.mean(values) if values else None,
            'median':statistics.median(values) if values else None,
            'min':min(values) if values else None,'max':max(values) if values else None}


def selection_scores(run):
    s = run.get('selection')
    if s is None or s['expected'] is None or s['ground_truth_sha256'] is None:
        return None
    one = len(s['actual']) == 1
    name = one and s['actual'][0]['name'] == s['expected']['name']
    joint = name and canonical(s['actual'][0]['arguments']) == canonical(s['expected']['arguments'])
    return bool(name),bool(joint)


def classify(run):
    errors = set(run.get('errors') or [])
    score = selection_scores(run)
    if score is not None and not score[1]:
        errors.add('tool_selection')
    if (run.get('citations') and run.get('evidence_ids') is not None
            and any(ref not in run['evidence_ids'] for ref in run['citations'])):
        errors.add('citation')
    if run.get('timeout') is True:
        errors.add('timeout')
    if run.get('cleanup_success') is False:
        errors.add('cleanup')
    for key,category in (('diagnosis','diagnostic'),('evidence_assessment','evidence')):
        if _assessment(run.get(key)) and not run[key]['correct']:
            errors.add(category)
    return sorted(errors)


def _protocols(value):
    """Optional declared protocol gates. No default experimental threshold."""
    if type(value) is not dict:
        raise ValueError('INVALID_PROTOCOL')
    for name,p in value.items():
        if (type(name) is not str or re.fullmatch(r'[a-z0-9_-]{1,64}',name) is None
                or type(p) is not dict
                or set(p) != {'expected_runs','minimum_coverage','protocol_sha256'}):
            raise ValueError('INVALID_PROTOCOL')
        minimum = p['minimum_coverage']
        if (not _count(p['expected_runs']) or p['expected_runs'] == 0
                or (minimum is not None and (type(minimum) not in (int,float)
                    or not math.isfinite(minimum) or not 0 <= minimum <= 1))
                or (p['protocol_sha256'] is not None and not _hash(p['protocol_sha256']))):
            raise ValueError('INVALID_PROTOCOL')
    return value


def _coverage(eligible, observed, protocol):
    expected = protocol['expected_runs'] if protocol else None
    result = {'eligible_runs':eligible,'observed_runs':observed,'expected_runs':expected,
              'value':eligible/expected if expected else None,
              'unobserved_runs':max(expected-observed,0) if expected else None,
              'status':'PENDING_PROTOCOL_GATE','reason':'MISSING_PROTOCOL'}
    if protocol:
        if observed > expected:
            result['reason'] = 'UNEXPECTED_RUN_COUNT'
        elif protocol['minimum_coverage'] is None or protocol['protocol_sha256'] is None:
            result['reason'] = 'MISSING_PROTOCOL_THRESHOLD_OR_REFERENCE'
        elif eligible/expected < protocol['minimum_coverage']:
            result.update(status='INSUFFICIENT_DATA',reason='ELIGIBLE_RUN_COVERAGE_BELOW_PROTOCOL')
        else:
            result.update(status='MET',reason='DECLARED_COUNT_COVERAGE_ONLY')
    return result


def evaluate(dataset):
    if (type(dataset) is not dict or not {'schema_version','runs'} <= dataset.keys()
            or dataset.keys() - {'schema_version','runs','protocols'}
            or dataset['schema_version'] != '1.0' or type(dataset['runs']) is not list
            or len(dataset['runs']) > 10000):
        raise ValueError('INVALID_DATASET')
    protocols = _protocols(dataset.get('protocols',{}))
    unique,locators = {},{}
    duplicates = 0
    for run in dataset['runs']:
        validate(run)
        identifier = run['run_id']
        locator = (run['provenance']['artifact_sha256'],run['provenance']['record_pointer'])
        if identifier in unique:
            if canonical(unique[identifier]) != canonical(run):
                raise ValueError('CONFLICTING_RUN')
            duplicates += 1
            continue
        if locator in locators:
            raise ValueError('ALIASED_RUN')
        unique[identifier],locators[locator] = run,identifier
    cohorts = defaultdict(list)
    for run in unique.values():
        key = tuple(run[k] for k in ('mode','protocol','config_sha256','revision','latency_scope'))
        cohorts[key].append(run)
    output = {'schema_version':'1.0','provenance_verification':'DECLARED_HASH_REFERENCES_NOT_AUTHENTICATION',
              'unique_runs':len(unique),'duplicate_runs_ignored':duplicates,'cohorts':[],'error_runs':[]}
    sources = defaultdict(list)
    for run in unique.values():
        sources[run['provenance']['artifact_sha256']].append(run['provenance']['record_pointer'])
    output['sources'] = [{'artifact_sha256':sha,'record_pointers':sorted(pointers)}
                         for sha,pointers in sorted(sources.items())]
    for key,runs in sorted(cohorts.items()):
        total = len(runs)
        scores = [s for r in runs if (s:=selection_scores(r)) is not None]
        metrics = {'tool_selection_accuracy':_rate([s[0] for s in scores],total),
                   'tool_and_exact_arguments_accuracy':_rate([s[1] for s in scores],total)}
        for source,target in (('diagnosis','diagnostic_correctness'),('evidence_assessment','evidence_correctness')):
            metrics[target] = _rate([r[source]['correct'] for r in runs if _assessment(r.get(source))],total)
            metrics[target]['verification_status'] = 'UNVERIFIED'
        cited = [r for r in runs if r.get('citations') and r.get('evidence_ids') is not None]
        metrics['citation_validity'] = fraction(sum(ref in set(r['evidence_ids']) for r in cited for ref in set(r['citations'])),
            sum(len(set(r['citations'])) for r in cited),eligible_runs=len(cited),missing_runs=total-len(cited),
            meaning='REFERENCE_RESOLUTION_ONLY',semantic_correctness='NOT_EVALUATED')
        metrics['latency'] = _distribution([r['latency_s'] for r in runs if r.get('latency_s') is not None],total,'seconds')
        metrics['tool_dispatch_count'] = _distribution([r['tool_dispatches'] for r in runs if r.get('tool_dispatches') is not None],total,'dispatches')
        metrics['timeout_rate'] = _rate([r['timeout'] for r in runs if r.get('timeout') is not None],total)
        known_errors = [r for r in runs if r.get('errors') is not None or classify(r)]
        metrics['error_rate'] = _rate([bool(classify(r)) for r in known_errors],total)
        metrics['cleanup_success'] = _rate([r['cleanup_success'] for r in runs if r.get('cleanup_success') is not None],total)
        writes = [r for r in runs if r.get('unauthorized_write_attempts') is not None]
        metrics['unauthorized_write_rejection'] = fraction(sum(r['unauthorized_writes_rejected'] for r in writes),
            sum(r['unauthorized_write_attempts'] for r in writes),eligible_runs=len(writes),missing_runs=total-len(writes))
        observed = [r['outside_allowlist_executions'] for r in runs if r.get('outside_allowlist_executions') is not None]
        metrics['outside_allowlist_executions'] = {'status':'EVALUATED' if observed else 'NOT_EVALUABLE',
            'count':sum(observed) if observed else None,'eligible_runs':len(observed),'missing_runs':total-len(observed)}
        verified = [r['verification']['outcome'] for r in runs if r.get('verification') and r['verification']['approved_fix']]
        metrics['verify_success'] = fraction(verified.count('pass'),len(verified),unknown=verified.count('unknown'),
            eligible_runs=len(verified),missing_runs=total-len(verified))
        protocol = protocols.get(key[1])
        for metric in metrics.values():
            metric['coverage'] = _coverage(metric.get('eligible_runs',metric.get('n',0)),total,protocol)
        counts = Counter()
        for run in runs:
            errors = classify(run);counts.update(errors)
            if errors:
                output['error_runs'].append({'run_id':run['run_id'],'provenance':run['provenance'],'categories':errors})
        output['cohorts'].append(dict(zip(('mode','protocol','config_sha256','revision','latency_scope'),key))
                                 | {'runs':total,'metrics':metrics,'error_categories':dict(sorted(counts.items())),
                                    'declared_protocol_gate':protocol,
                                    'official_report_eligibility':{'status':'UNVERIFIED','reasons':[
                                        'INDEPENDENT_ARTIFACT_COMPARISON_REQUIRED','INDEPENDENT_REVIEW_REQUIRED']}})
    output['error_runs'].sort(key=lambda r:r['run_id'])
    return output


def _git(path):
    return subprocess.check_output(['git','show',REF+':'+path],cwd=ROOT,stderr=subprocess.DEVNULL)


def benchmark_example():
    """Re-score only recorded native calls; never runs the historical harness."""
    raw,cases_raw = _git(BENCHMARK),_git('tests/benchmarks/cases.json')
    data,cases = load(raw),load(cases_raw)
    if digest(cases_raw) != data['metadata']['suite_sha256']:
        raise ValueError('CHANGED_GROUND_TRUTH')
    expected = {case['id']:case['expected'] for case in cases['cases']}
    config = {k:data['metadata'][k] for k in ('model','model_digest','think_request','options','seeds','timeout_s','suite_sha256','script_sha256')}
    config_hash = digest(canonical(config).encode())
    runs = []
    for index,row in enumerate(data['rows']):
        if row['expected'] != expected[row['case_id']]:
            raise ValueError('GROUND_TRUTH_MISMATCH')
        # This importer is deliberately pinned to the completed archived dataset.
        # Other legacy formats need an explicit reviewed adapter, not guessed labels.
        if row['response_received'] is not True or row['done'] is not True or row['classification'] != 'correct':
            raise ValueError('UNSUPPORTED_LEGACY_ROW')
        calls = [{'name':c['function']['name'],'arguments':c['function']['arguments']} for c in row['tool_calls']]
        pointer = '/rows/'+str(index)
        runs.append({'run_id':digest((digest(raw)+pointer).encode()),'mode':'live_llm_mock_tools',
            'protocol':'scrum14-instruct-6x3-native-selection','config_sha256':config_hash,'revision':REF,
            'latency_scope':'model_request','provenance':{'artifact_sha256':digest(raw),
                'record_pointer':pointer,'snapshot_sha256':None},
            'selection':{'expected':expected[row['case_id']],'actual':calls,'ground_truth_sha256':digest(cases_raw)},
            'latency_s':row['wall_latency_s'],'timeout':False,'errors':[]})
    # Archive supplies the planned count, not a formal coverage threshold/signoff.
    return {'schema_version':'1.0','runs':runs,'protocols':{
        'scrum14-instruct-6x3-native-selection':{
            'expected_runs':data['metadata']['planned_trials'],
            'minimum_coverage':None,'protocol_sha256':None}}}


def markdown(report):
    lines = ['# SCRUM-42 metrics','',
        'Offline recomputation only. No new inference or Linux run. Hash references are provenance, not authentication.',
        'Official-report eligibility: UNVERIFIED. Original artifacts must be compared and independently reviewed.',
        'EVALUATED means a calculation from declared inputs, not verified experimental correctness.',
        'citation_validity is REFERENCE_RESOLUTION_ONLY: a resolved ID does not prove semantic evidence or conclusion correctness.',
        f"Unique runs: {report['unique_runs']}; exact duplicates ignored: {report['duplicate_runs_ignored']}.",'']
    for c in report['cohorts']:
        lines += [f"## {c['mode']} / {c['protocol']}",'',f"Revision: `{c['revision']}` (artifact snapshot, not proof of runtime identity).",
                  f"Config: `{c['config_sha256']}`; latency scope: {c['latency_scope']}; runs: {c['runs']}.",'',
                  'Count coverage gates are declared protocol checks only; MET is not acceptance.', '',
                  '| Metric | Value | Denominator / observed missing | Protocol count coverage |','|---|---|---|---|']
        for name,m in c['metrics'].items():
            if m['status']=='NOT_EVALUABLE':value='NOT_EVALUABLE'
            elif 'value' in m:value=f"{m['value']:.6f}"
            elif 'median' in m:value=f"median {m['median']:.6f}; mean {m['mean']:.6f} {m['unit']}"
            else:value=str(m['count'])
            denominator = (f"{m['numerator']}/{m['denominator']}" if 'denominator' in m else str(m.get('n',m.get('eligible_runs',0))))
            if m.get('verification_status'):
                value += '; '+m['verification_status']
            coverage = m['coverage']
            count = f"{coverage['eligible_runs']}/{coverage['expected_runs']}" if coverage['expected_runs'] else 'unknown expected count'
            lines.append(f"| {name} | {value} | {denominator}; missing runs {m.get('missing_runs',0)} | {count}; {coverage['status']} ({coverage['reason']}) |")
        lines += ['', 'Error categories (multi-label; counts are not disjoint): '+canonical(c['error_categories']),'']
    lines += ['## Source artifacts', '', '| SHA-256 | Record pointers |', '|---|---|']
    for source in report['sources']:
        lines.append(f"| {source['artifact_sha256']} | {', '.join(source['record_pointers'])} |")
    lines += ['', '## Error runs','', '| Run ID | Artifact SHA-256 / record pointer | Categories |','|---|---|---|']
    for r in report['error_runs']:
        p=r['provenance'];lines.append(f"| {r['run_id']} | {p['artifact_sha256']}{p['record_pointer']} | {', '.join(r['categories'])} |")
    if not report['error_runs']:lines.append('| None observed in evaluable fields | — | No inferred failures |')
    return '\n'.join(lines)+'\n'


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self,message):self.exit(2,'INVALID_ARGUMENTS; use --help\n')
    p = Parser(description=__doc__,allow_abbrev=False)
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--input',type=Path)
    group.add_argument('--benchmark-example',action='store_true')
    p.add_argument('--format',choices=('json','markdown'),default='json')
    args=p.parse_args(argv)
    try:
        if args.benchmark_example:
            dataset=benchmark_example()
        else:
            with args.input.open('rb') as handle:dataset=load(handle.read(8*1024*1024+1))
        report=evaluate(dataset)
        print(markdown(report) if args.format=='markdown' else json.dumps(report,sort_keys=True,indent=2),end='' if args.format=='markdown' else '\n')
        return 0
    except Exception:
        print('INVALID_DATASET_OR_PROVENANCE; no metrics emitted',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
