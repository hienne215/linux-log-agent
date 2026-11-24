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
from unittest.mock import patch

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


class ReviewRegressionTests(unittest.TestCase):
    """Artificial protocol/assessment fixtures, never reviewer approvals."""
    def test_historical_source_archive_is_exact_without_git(self):
        with patch.object(m.subprocess,'check_output',side_effect=FileNotFoundError):
            result=m.evaluate(m.benchmark_example())
        metric=result['cohorts'][0]['metrics']['tool_and_exact_arguments_accuracy']
        self.assertEqual((metric['numerator'],metric['denominator']),(18,18))

    def test_changed_archive_never_becomes_historical_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/m.BENCHMARK;path.parent.mkdir(parents=True)
            path.write_text('{}')
            with patch.object(m,'ROOT',root),patch.object(m.subprocess,'check_output',side_effect=FileNotFoundError):
                with self.assertRaisesRegex(ValueError,'CHANGED_ARCHIVE'):m.benchmark_example()

    def with_protocol(self, *runs, expected=15, minimum=1.0, reference='b'*64):
        return {**dataset(*runs),'protocols':{'unit-fixture':{'expected_runs':expected,
            'minimum_coverage':minimum,'protocol_sha256':reference}}}

    def test_metadata_only_assessment_remains_unverified(self):
        result=m.evaluate(dataset(run(diagnosis=assessment(True))))['cohorts'][0]
        metric=result['metrics']['diagnostic_correctness']
        self.assertEqual(metric['value'],1)
        self.assertEqual(metric['verification_status'],'UNVERIFIED')
        self.assertEqual(result['official_report_eligibility']['status'],'UNVERIFIED')
        self.assertIn('INDEPENDENT_ARTIFACT_COMPARISON_REQUIRED',result['official_report_eligibility']['reasons'])
        self.assertIn('INDEPENDENT_REVIEW_REQUIRED',result['official_report_eligibility']['reasons'])
        missing=metrics(run(diagnosis={**assessment(True),'reviewer_sha256':None}))['diagnostic_correctness']
        self.assertEqual(missing['status'],'NOT_EVALUABLE')
        self.assertEqual(missing['verification_status'],'UNVERIFIED')

    def test_input_cannot_approve_itself(self):
        for extra in ({'verified':True},{'reviewer_verified':True},{'artifacts_verified':True}):
            with self.assertRaises(ValueError):metrics(run(diagnosis={**assessment(True),**extra}))

    def test_high_accuracy_low_protocol_coverage_is_insufficient_data(self):
        record=run(selection=selection([{'name':'service_status','arguments':{'unit':'nginx.service'}}]))
        result=m.evaluate(self.with_protocol(record))['cohorts'][0]
        score=result['metrics']['tool_selection_accuracy']
        self.assertEqual(score['value'],1)
        self.assertEqual(score['coverage']['value'],1/15)
        self.assertEqual(score['coverage']['status'],'INSUFFICIENT_DATA')
        self.assertEqual(score['coverage']['unobserved_runs'],14)
        self.assertEqual(result['official_report_eligibility']['status'],'UNVERIFIED')

    def test_coverage_uses_eligible_runs_not_only_observed_count(self):
        rows=[run(i,diagnosis=assessment(True) if i==0 else None) for i in range(3)]
        score=m.evaluate(self.with_protocol(*rows,expected=3))['cohorts'][0]['metrics']['diagnostic_correctness']
        self.assertEqual(score['coverage']['eligible_runs'],1)
        self.assertEqual(score['coverage']['observed_runs'],3)
        self.assertEqual(score['coverage']['value'],1/3)
        self.assertEqual(score['coverage']['unobserved_runs'],0)
        self.assertEqual(score['coverage']['status'],'INSUFFICIENT_DATA')

    def test_missing_protocol_and_missing_threshold_stay_pending(self):
        result=m.evaluate(dataset(run(latency_s=1)))['cohorts'][0]
        self.assertEqual(result['metrics']['latency']['coverage']['status'],'PENDING_PROTOCOL_GATE')
        self.assertIsNone(result['metrics']['latency']['coverage']['value'])
        for minimum,reference in ((None,'b'*64),(1.0,None)):
            metric=m.evaluate(self.with_protocol(run(latency_s=1),expected=1,minimum=minimum,reference=reference))['cohorts'][0]['metrics']['latency']
            self.assertEqual(metric['coverage']['value'],1)
            self.assertEqual(metric['coverage']['status'],'PENDING_PROTOCOL_GATE')

    def test_full_count_coverage_is_not_acceptance_or_accuracy_pass(self):
        r=run(selection=selection([]))
        c=m.evaluate(self.with_protocol(r,expected=1))['cohorts'][0]
        self.assertEqual(c['metrics']['tool_selection_accuracy']['value'],0)
        self.assertEqual(c['metrics']['tool_selection_accuracy']['coverage']['status'],'MET')
        self.assertEqual(c['official_report_eligibility']['status'],'UNVERIFIED')

    def test_invalid_protocol_and_invalid_provenance_fail_closed(self):
        for expected,minimum in ((0,1.0),(True,1.0),(15,True),(15,float('nan')),(15,1.1)):
            with self.assertRaises(ValueError):m.evaluate(self.with_protocol(run(),expected=expected,minimum=minimum))
        for sha,pointer in (('not-a-hash','/runs/0'),('a'*64,'/runs/00'),('a'*64,'/runs/../0')):
            with self.assertRaises(ValueError):metrics(run(provenance={'artifact_sha256':sha,
                'record_pointer':pointer,'snapshot_sha256':None}))

    def test_duplicate_cannot_fill_protocol_missing_runs(self):
        r=run(latency_s=1)
        c=m.evaluate(self.with_protocol(r,deepcopy(r),expected=2))['cohorts'][0]
        self.assertEqual(c['metrics']['latency']['coverage']['value'],.5)
        self.assertEqual(c['metrics']['latency']['coverage']['status'],'INSUFFICIENT_DATA')

    def test_reference_resolution_alias_does_not_claim_semantics(self):
        c=m.evaluate(dataset(run(citations=['e1'],evidence_ids=['e1'])))['cohorts'][0]
        metric=c['metrics']['citation_validity']
        self.assertEqual(metric['value'],1)
        self.assertEqual(metric['meaning'],'REFERENCE_RESOLUTION_ONLY')
        self.assertEqual(metric['semantic_correctness'],'NOT_EVALUATED')
        self.assertEqual(c['metrics']['evidence_correctness']['status'],'NOT_EVALUABLE')
        text=m.markdown(m.evaluate(dataset(run(citations=['e1'],evidence_ids=['e1']))))
        self.assertIn('REFERENCE_RESOLUTION_ONLY',text)
        self.assertIn('does not prove',text)
        r=run(citations=['e1','missing','e1'],evidence_ids=['e1'])
        metric=m.evaluate(self.with_protocol(r,expected=2))['cohorts'][0]['metrics']['citation_validity']
        self.assertEqual(metric['denominator'],2)  # Unique references, not runs.
        self.assertEqual(metric['coverage']['eligible_runs'],1)
        self.assertEqual(metric['coverage']['value'],.5)
        self.assertEqual(metric['value'],.5)

    def test_no_data_and_excess_runs_not_mislabelled_failed_diagnosis(self):
        c=m.evaluate(self.with_protocol(run(),expected=1))['cohorts'][0]
        self.assertEqual(c['metrics']['diagnostic_correctness']['status'],'NOT_EVALUABLE')
        self.assertEqual(c['metrics']['diagnostic_correctness']['coverage']['status'],'INSUFFICIENT_DATA')
        c=m.evaluate(self.with_protocol(run(0,latency_s=1),run(1,latency_s=2),expected=1))['cohorts'][0]
        self.assertEqual(c['metrics']['latency']['coverage']['status'],'PENDING_PROTOCOL_GATE')
        self.assertEqual(c['metrics']['latency']['coverage']['reason'],'UNEXPECTED_RUN_COUNT')
