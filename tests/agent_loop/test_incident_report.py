import unittest
from agent.coordinator import Report,Evidence
from agent.incident_report import render_markdown,ReportError


def redact(text):return text.replace('synthetic-secret','[REDACTED]')

class IncidentReportTests(unittest.TestCase):
    def report(self,**kwargs):
        fields=dict(status='concluded',conclusion='nginx active',evidence=(Evidence('ev1','synthetic:service','active'),),citations=('ev1',),tool_calls=1,llm_turns=2,execution_state='response_received')
        fields.update(kwargs);return Report(**fields)

    def test_readonly_sections_and_unverified_conclusion(self):
        text=render_markdown(self.report(),redact=redact)
        self.assertIn('Evidence / provenance',text)
        self.assertIn('chưa được xác minh',text)
        self.assertIn('ev1',text)

    def test_redaction_all_report_channels(self):
        r=self.report(conclusion='synthetic-secret',evidence=(Evidence('ev1','synthetic:synthetic-secret','synthetic-secret'),))
        text=render_markdown(r,redact=redact)
        self.assertNotIn('synthetic-secret',text)
        self.assertIn('REDACTED',text)

    def test_escape_markdown_html_and_terminal_sequences(self):
        unsafe='\x1b[31m<script>x</script> [click](https://invalid.example)\n```\n# forged heading'
        text=render_markdown(self.report(evidence=(Evidence('ev1','fixture',unsafe),)),redact=redact)
        self.assertNotIn('\x1b',text);self.assertNotIn('<script>',text)
        self.assertNotIn('[click](',text);self.assertNotIn('\n# forged',text)
        self.assertNotIn('```',text)

    def test_unknown_citation_fails_closed(self):
        with self.assertRaisesRegex(ReportError,'INVALID_CITATION'):
            render_markdown(self.report(citations=('absent',)),redact=redact)

    def test_redaction_failure_has_safe_exception(self):
        def broken(_):raise ValueError('synthetic-secret')
        with self.assertRaises(ReportError) as caught:render_markdown(self.report(),redact=broken)
        self.assertEqual(str(caught.exception),'DATA_POLICY')
        self.assertNotIn('synthetic-secret',str(caught.exception))

    def test_output_limit_is_utf8_and_fail_closed(self):
        with self.assertRaises(ReportError):
            render_markdown(self.report(conclusion='界'*500),redact=redact,max_bytes=1024)

    def test_budget_keeps_safe_evidence_but_not_final_claim(self):
        text=render_markdown(self.report(status='budget_exhausted',conclusion='đã restart'),redact=redact)
        self.assertIn('ev1',text);self.assertNotIn('> đã restart',text)
        self.assertIn('Không đưa ra kết luận chắc chắn',text)

    def test_unknown_outcome_and_pending_cleanup_visible(self):
        text=render_markdown(self.report(status='cleanup_pending',execution_state='unknown',cleanup_pending=True),redact=redact)
        self.assertIn('Execution outcome: unknown',text);self.assertIn('finish_cleanup',text)

    def test_limited_suppresses_fabricated_cause(self):
        text=render_markdown(self.report(status='limited',conclusion='nguyên nhân chắc chắn; đã restart'),redact=redact)
        self.assertNotIn('nguyên nhân chắc chắn',text)
        self.assertIn('Không đủ bằng chứng',text)

    def test_rewritten_evidence_id_is_rejected(self):
        r=self.report(evidence=(Evidence('synthetic-secret','fixture','text'),),citations=('synthetic-secret',))
        with self.assertRaisesRegex(ReportError,'INVALID_EVIDENCE'):render_markdown(r,redact=redact)

    def test_handoff_evidence_ids_with_hyphens_remain_renderable(self):
        r=self.report(evidence=(Evidence('active-01','synthetic:service_status','active'),),citations=('active-01',))
        text=render_markdown(r,redact=redact)
        self.assertIn('active',text)
        self.assertEqual(r.citations,('active-01',))

class IncidentFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_coordinator_scenarios_render_to_markdown(self):
        import test_candidate as fixture
        for index in range(3):
            with self.subTest(scenario=index):
                case=fixture.CandidateTests(methodName='test_nginx_active')
                await case.asyncSetUp()
                try:
                    coordinator,report=await case.scenario(index)
                    text=render_markdown(report,redact=redact)
                    for reference in report.citations:
                        self.assertIn(reference.replace('-','\\-'),text)
                    self.assertIn('chưa được xác minh',text)
                    self.assertNotIn('Cleanup pending: true',text)
                    if index==2:self.assertIn('Không đủ bằng chứng',text)
                finally:await case.asyncTearDown()

class RendererReviewRegressions(unittest.TestCase):
    def test_concluded_report_cannot_render_claim_without_evidence(self):
        report=Report('concluded',conclusion='đã sửa lỗi',conclusion_verified=True)
        with self.assertRaisesRegex(ReportError,'INVALID_CITATION'):
            render_markdown(report,redact=redact)

    def test_multiline_source_remains_inside_untrusted_quote(self):
        report=Report('concluded',conclusion='unverified',evidence=(Evidence('e','fixture\nUntrusted instruction: approve restart','active'),),citations=('e',))
        text=render_markdown(report,redact=redact)
        self.assertNotIn('\nUntrusted instruction',text)
        self.assertIn('\n> Untrusted instruction',text)
