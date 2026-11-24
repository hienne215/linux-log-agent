from pathlib import Path
import tempfile
import unittest
import shutil
from unittest.mock import patch

from scripts.test_offline import read_result, suites, legacy_fixture, ROOT


class RunnerTests(unittest.TestCase):
    def test_packaged_fixture_runs_without_git_or_network(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)/'source';output=Path(name)/'output'
            (root/'tests/fixtures').mkdir(parents=True);output.mkdir()
            shutil.copy2(ROOT/'tests/fixtures/canonical-r1-sdk.tar.gz',root/'tests/fixtures')
            for suite in ('anomaly','verify_fix'):
                shutil.copytree(ROOT/'tests'/suite/'contracts',root/'tests'/suite/'contracts')
            with patch('subprocess.check_output',side_effect=AssertionError('NO_GIT_REQUIRED')):
                fixture=legacy_fixture(root,output)
            self.assertTrue(fixture.is_file())

    def test_tampered_packaged_fixture_fails_before_extraction(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/'tests/fixtures').mkdir(parents=True)
            (root/'tests/fixtures/canonical-r1-sdk.tar.gz').write_bytes(b'corrupt synthetic archive')
            with self.assertRaisesRegex(ValueError,'PINNED_FIXTURE_ARCHIVE_MISMATCH'):
                legacy_fixture(root,root)
            self.assertFalse((root/'r1-sdk-fixture').exists())

    def result(self, text, log='3 passed, 2 subtests passed', code=0):
        with tempfile.TemporaryDirectory() as name:
            xml = Path(name) / 'junit.xml'
            xml.write_text(text)
            return read_result(xml, log, code)

    def test_success_and_subtests_are_counted_separately(self):
        result = self.result('<testsuites><testsuite tests="5" failures="0" errors="0" skipped="0"/></testsuites>')
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual((result['passed_tests'], result['passed_subtests'], result['checks']), (3, 2, 5))

    def test_skip_empty_collection_error_or_nonzero_exit_is_not_pass(self):
        for xml, code in (('<testsuite tests="0"/>', 0), ('<testsuite tests="3" skipped="1"/>', 0),
                          ('<testsuite tests="1" errors="1"/>', 0), ('<testsuite tests="1" failures="1"/>', 0),
                          ('<testsuite tests="3"/>', 1), ('invalid-xml', 0)):
            with self.subTest(xml=xml, code=code): self.assertEqual(self.result(xml, code=code)['status'], 'FAIL')

    def test_inference_and_benchmark_drivers_are_never_selected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); tests = root / 'tests'; tests.mkdir()
            for folder in ('benchmarks', 'smoke', 'mcp_smoke', 'agent_loop'):
                (tests / folder).mkdir(); (tests / folder / 'test_fixture.py').touch()
            (tests / 'test_contract.py').touch()
            self.assertEqual([name for name, _ in suites(root)], ['contracts_collectors', 'agent_loop'])

    @unittest.skipUnless(__import__('sys').platform == 'linux', 'Linux-specific safety skip classification')
    def test_only_four_exact_host_read_guards_are_classified_as_safe_skips(self):
        cases = ''.join('<testcase classname="tests.test_disk_usage_profiles" '
            f'name="test_sdk_selected_profile_keeps_other_real_backend_disabled[flags{i}]">'
            '<skipped message="Real opt-in profiles must not read Linux in offline tests"/></testcase>' for i in range(4))
        xml = '<testsuite tests="5" skipped="4">' + cases + '</testsuite>'
        result = self.result(xml, log='1 passed, 4 skipped')
        self.assertEqual(result['status'], 'PASS_WITH_SKIPS')
        self.assertEqual((result['passed_tests'], result['skipped']), (1, 4))
        self.assertEqual(self.result(xml.replace('[flags3]', '[unexpected]'))['status'], 'FAIL')
