"""Artificial UNIT fixtures only; none are measurements or Linux runs."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import metrics as m


def run(i=0, **updates):
    r = {'run_id':m.digest(f'unit-run-{i}'.encode()),'mode':'synthetic','protocol':'unit-fixture',
         'config_sha256':'c'*64,'revision':'d'*40,'latency_scope':'diagnosis_session',
         'provenance':{'artifact_sha256':'a'*64,'record_pointer':f'/runs/{i}','snapshot_sha256':None}}
    r.update(updates)
    return r


def dataset(*runs):return {'schema_version':'1.0','runs':list(runs)}
def metrics(*runs):return m.evaluate(dataset(*runs))['cohorts'][0]['metrics']
def assessment(correct):
    return dict(correct=correct,ground_truth_sha256='b'*64,reviewer_sha256='e'*64,rubric_sha256='f'*64)
def selection(actual):
    return {'expected':{'name':'service_status','arguments':{'unit':'nginx.service'}},
            'actual':actual,'ground_truth_sha256':'b'*64}


class MetricsTests(unittest.TestCase):
    def test_missing_ground_truth_and_observations_are_not_evaluable(self):
        r=run(diagnosis={**assessment(True),'ground_truth_sha256':None})
        values=metrics(r)
        for v in values.values():self.assertEqual(v['status'],'NOT_EVALUABLE')
        self.assertEqual(m.classify(r),[])

    def test_tool_name_and_joint_arguments_have_explicit_denominators(self):
        correct={'name':'service_status','arguments':{'unit':'nginx.service'}}
        values=metrics(run(0,selection=selection([correct])),
                       run(1,selection=selection([{**correct,'arguments':{'unit':'ssh.service'}}])),
                       run(2,selection=selection([])),run(3))
        self.assertEqual(values['tool_selection_accuracy']['value'],2/3)
        self.assertEqual(values['tool_and_exact_arguments_accuracy']['value'],1/3)
        self.assertEqual(values['tool_selection_accuracy']['missing_runs'],1)

    def test_selection_without_ground_truth_is_not_evaluable(self):
        for expected,source in ((None,None),({'name':'t','arguments':{}},None)):
            r=run(selection={'expected':expected,'actual':[],'ground_truth_sha256':source})
            self.assertEqual(metrics(r)['tool_selection_accuracy']['status'],'NOT_EVALUABLE')
            self.assertEqual(m.classify(r),[])

    def test_json_types_not_python_bool_int_equivalence(self):
        r=run(selection={'expected':{'name':'t','arguments':{'x':1}},
            'actual':[{'name':'t','arguments':{'x':True}}],'ground_truth_sha256':'b'*64})
        self.assertEqual(metrics(r)['tool_and_exact_arguments_accuracy']['value'],0)

    def test_human_assessments_require_ground_truth_rubric_and_reviewer(self):
        v=metrics(run(0,diagnosis=assessment(True),evidence_assessment=assessment(False)),
                  run(1,diagnosis=assessment(False)),run(2,diagnosis={**assessment(True),'reviewer_sha256':None}))
        self.assertEqual(v['diagnostic_correctness']['value'],.5)
        self.assertEqual(v['diagnostic_correctness']['missing_runs'],1)
        self.assertEqual(v['evidence_correctness']['value'],0)

    def test_citations_are_membership_not_semantic_truth(self):
        v=metrics(run(0,citations=['a','a','invented'],evidence_ids=['a']),run(1,citations=[],evidence_ids=[]))
        self.assertEqual(v['citation_validity']['numerator'],1)
        self.assertEqual(v['citation_validity']['denominator'],2)
        self.assertEqual(v['diagnostic_correctness']['status'],'NOT_EVALUABLE')

    def test_latency_missing_not_zero_and_scope_units(self):
        v=metrics(run(0,latency_s=1),run(1,latency_s=3),run(2))
        self.assertEqual(v['latency']['median'],2)
        self.assertEqual(v['latency']['n'],2)
        self.assertEqual(v['latency']['missing_runs'],1)
        for value in (True,-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):metrics(run(latency_s=value))

    def test_error_timeout_and_cleanup_coverage(self):
        v=metrics(run(0,timeout=True,cleanup_success=False),run(1,timeout=False,errors=[],cleanup_success=True),run(2))
        self.assertEqual(v['timeout_rate']['value'],.5)
        self.assertEqual(v['error_rate']['value'],.5)
        self.assertEqual(v['cleanup_success']['value'],.5)
        self.assertEqual(v['cleanup_success']['missing_runs'],1)

    def test_error_table_is_multilabel_and_traceable_without_raw_payloads(self):
        r=run(selection=selection([]),errors=['malformed_output','transport','permission','insufficient_evidence'],
              cleanup_success=False,citations=['invented'],evidence_ids=[])
        report=m.evaluate(dataset(r));row=report['error_runs'][0]
        self.assertEqual(set(row['categories']),{'tool_selection','malformed_output','transport','permission','insufficient_evidence','cleanup','citation'})
        self.assertEqual(row['provenance'],r['provenance'])
        self.assertNotIn('selection',row)

    def test_exact_duplicate_not_double_counted_and_conflicts_rejected(self):
        r=run(timeout=True)
        report=m.evaluate(dataset(r,deepcopy(r)))
        self.assertEqual(report['unique_runs'],1);self.assertEqual(report['duplicate_runs_ignored'],1)
        with self.assertRaises(ValueError):m.evaluate(dataset(r,{**r,'timeout':False}))
        with self.assertRaises(ValueError):m.evaluate(dataset(r,{**r,'run_id':'f'*64}))

    def test_separate_modes_protocol_versions_config_and_latency_scope(self):
        rows=[run(0),run(1,mode='live_llm_synthetic_mcp'),run(2,mode='live_llm_mock_tools'),
              run(3,mode='real_linux',provenance={'artifact_sha256':'a'*64,'record_pointer':'/runs/3','snapshot_sha256':'b'*64}),
              run(4,config_sha256='f'*64),run(5,revision='e'*40),run(6,protocol='other'),run(7,latency_scope='model_request')]
        self.assertEqual(len(m.evaluate(dataset(*rows))['cohorts']),8)
        with self.assertRaises(ValueError):metrics(run(mode='real_linux'))

    def test_safety_and_write_denominators_not_all_diagnosis_runs(self):
        v=metrics(run(0,unauthorized_write_attempts=2,unauthorized_writes_rejected=1,outside_allowlist_executions=1),
                  run(1,unauthorized_write_attempts=1,unauthorized_writes_rejected=1,outside_allowlist_executions=0),run(2))
        self.assertEqual(v['unauthorized_write_rejection']['value'],2/3)
        self.assertEqual(v['outside_allowlist_executions']['count'],1)
        with self.assertRaises(ValueError):metrics(run(unauthorized_write_attempts=1,unauthorized_writes_rejected=2))

    def test_verify_unknown_not_success_and_unapproved_excluded(self):
        v=metrics(run(0,verification={'approved_fix':True,'outcome':'pass'}),
                  run(1,verification={'approved_fix':True,'outcome':'unknown'}),
                  run(2,verification={'approved_fix':False,'outcome':'pass'}))
        self.assertEqual(v['verify_success']['value'],.5)
        self.assertEqual(v['verify_success']['unknown'],1)
        self.assertEqual(v['verify_success']['denominator'],2)

    def test_unit_test_pass_counts_not_accepted_as_runs(self):
        for obj in ({'passed':352},dataset({**run(),'pytest_passed':352}),dataset(run(latency_s=True)),
                    dataset(run(cleanup_success=1))):
            with self.assertRaises(ValueError):m.evaluate(obj)

    def test_strict_json_duplicate_keys_and_nonfinite(self):
        for raw in (b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":Infinity}',b' '* (8*1024*1024+1)):
            with self.assertRaises(ValueError):m.load(raw)

    def test_safe_cli_errors_and_report_do_not_echo_secrets_or_paths(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'password-fake-secret.json';path.write_text('{"token":"fake-secret"}')
            out,err=io.StringIO(),io.StringIO()
            with redirect_stdout(out),redirect_stderr(err):code=m.main(['--input',str(path)])
            self.assertEqual(code,2);self.assertEqual(out.getvalue(),'');self.assertNotIn('fake-secret',err.getvalue())
        r=run(selection=selection([{'name':'wrong','arguments':{'password':'fake-secret'}}]))
        self.assertNotIn('fake-secret',m.markdown(m.evaluate(dataset(r))))

    def test_historical_benchmark_recomputed_from_native_calls_not_cached_score(self):
        ds=m.benchmark_example();report=m.evaluate(ds)
        self.assertEqual(report['unique_runs'],18)
        c=report['cohorts'][0];v=c['metrics']
        self.assertEqual(c['mode'],'live_llm_mock_tools')
        self.assertEqual(v['tool_selection_accuracy']['numerator'],18)
        self.assertEqual(v['tool_and_exact_arguments_accuracy']['numerator'],18)
        self.assertAlmostEqual(v['latency']['median'],3.1734472885000287)
        self.assertEqual(v['diagnostic_correctness']['status'],'NOT_EVALUABLE')
        self.assertEqual(v['cleanup_success']['status'],'NOT_EVALUABLE')
        ds['runs'][0]['selection']['actual']=[]
        self.assertEqual(m.evaluate(ds)['cohorts'][0]['metrics']['tool_selection_accuracy']['numerator'],17)

    def test_script_runs_independently_from_another_directory(self):
        completed=subprocess.run([sys.executable,'-B',str(Path(m.__file__).resolve()),'--benchmark-example'],
            cwd=tempfile.gettempdir(),text=True,capture_output=True,timeout=5)
        self.assertEqual(completed.returncode,0);self.assertEqual(json.loads(completed.stdout)['unique_runs'],18)

    def test_committed_example_is_exactly_reproducible(self):
        report=m.evaluate(m.benchmark_example())
        here=Path(__file__).parent
        self.assertEqual((here/'EXAMPLE_BENCHMARK.json').read_text(),json.dumps(report,sort_keys=True,indent=2)+'\n')
        self.assertEqual((here/'EXAMPLE_BENCHMARK.md').read_text(),m.markdown(report))

    def test_empty_dataset_does_not_invent_results(self):
        report=m.evaluate(dataset())
        self.assertEqual(report['unique_runs'],0);self.assertEqual(report['cohorts'],[])
