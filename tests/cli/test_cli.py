"""CLI regression: in-memory scenarios only, no Ollama/MCP/Linux collectors."""
import asyncio
from contextlib import redirect_stdout, redirect_stderr
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent.cli.__main__ import main, run_demo, format_outcome, DemoOutcome
from agent.cli.report_store import save_report, StoreError
from agent.cli.synthetic import make_coordinator, SyntheticExecutor, steps_for, ToolEvent
from agent.coordinator import Decision, Report, Evidence

ROOT = Path(__file__).resolve().parents[2]


class CLITests(unittest.TestCase):
    def invoke(self, *args, stdin=''):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), patch('sys.stdin', io.StringIO(stdin)):
            try:
                code = main(list(args))
            except SystemExit as error:
                code = error.code
        return code, out.getvalue(), err.getvalue()

    def test_three_scenarios_and_output_snapshots(self):
        for name, code, calls, refs in (
            ('active', 0, 1, ('active-01',)),
            ('port-conflict', 0, 3, ('failed-01', 'failed-02', 'failed-03')),
            ('permission-denied', 1, 2, ('failed-01',))):
            with self.subTest(name=name):
                actual, out, err = self.invoke('--scenario', name, '--question', 'Vì sao Nginx lỗi?')
                self.assertEqual(actual, code)
                self.assertEqual(out, (ROOT/'tests/cli/snapshots'/f'{name}.txt').read_text())
                self.assertEqual(err, '')
                self.assertIn('SYNTHETIC DEMO', out)
                self.assertIn(f'Tool attempts: {calls}', out)
                self.assertIn('Trace ứng dụng: RECEIVED', out)
                self.assertIn('chưa được xác minh', out)
                for ref in refs:
                    self.assertIn(ref.replace('-', '\\-'), out)

    def test_permission_denied_does_not_invent_cause(self):
        code, out, _ = self.invoke('--scenario', 'permission-denied', '--question', 'Đọc journal')
        self.assertEqual(code, 1)
        self.assertIn('Không đủ bằng chứng', out)
        self.assertIn('Không đủ quyền đọc journal', out)
        self.assertNotIn('nguyên nhân chắc chắn', out)
        self.assertNotIn('đã cấp quyền', out)

    def test_bad_arguments_do_not_echo_raw_values(self):
        for args in ([], ['--scenario', 'password=hunter2'],
                     ['--scenario', 'active', '--restart', 'password=hunter2'],
                     ['--scenario', 'active', '--timeout', 'nan'],
                     ['--scenario', 'active', '--timeout', '0'],
                     ['--scenario', 'active', '--timeout', '121']):
            with self.subTest(args=args):
                code, out, err = self.invoke(*args)
                self.assertEqual(code, 2)
                self.assertNotIn('hunter2', out+err)

    def test_question_from_stdin_is_not_echoed(self):
        code, out, err = self.invoke('--scenario', 'active', stdin='db_password=hunter2\n')
        self.assertEqual(code, 0)
        self.assertNotIn('hunter2', out+err)

    def test_invalid_question(self):
        for question in ('', ' ', 'x'*4097):
            self.assertEqual(self.invoke('--scenario', 'active', '--question', question)[0], 2)

    def test_restart_sudo_and_approval_requests_are_denied(self):
        for question in ('restart nginx', 'restart_service', 'sudo systemctl start nginx',
                         'approve restart', 'khởi động lại nginx'):
            with self.subTest(question=question), patch('agent.cli.__main__.run_demo') as runner:
                self.assertEqual(self.invoke('--scenario', 'active', '--question', question)[0], 2)
                runner.assert_not_called()

    def test_invalid_report_path_rejected_before_dispatch(self):
        for name in ('../outside.md', '/tmp/outside.md', 'sub/report.md', 'a\\b.md', '.hidden.md', 'a..md'):
            with self.subTest(name=name), patch('agent.cli.__main__.run_demo') as runner:
                self.assertEqual(self.invoke('--scenario', 'active', '--question', 'test', '--report', name)[0], 2)
                runner.assert_not_called()

    def test_real_budget_exhaustion(self):
        code, out, _ = self.invoke('--scenario', 'active', '--question', 'test', '--timeout', '0.000000000001')
        self.assertEqual(code, 1)
        self.assertIn('Đã hết ngân sách', out)
        self.assertIn('Không có evidence', out)

    def test_cancellation_exit_and_partial_report(self):
        async def cancelled(*_, **__):
            return DemoOutcome(Report('cancelled'), ('RECEIVED', 'CANCELLED', 'REPORTED'))
        with patch('agent.cli.__main__.run_demo', side_effect=cancelled):
            code, out, _ = self.invoke('--scenario', 'active', '--question', 'test')
        self.assertEqual(code, 130)
        self.assertIn('Đã hủy', out)
        self.assertIn('Không có evidence', out)

    def test_report_write_failure_does_not_expose_path_or_secret(self):
        with patch('agent.cli.__main__.save_report', side_effect=StoreError('password=hunter2')):
            code, out, err = self.invoke('--scenario', 'active', '--question', 'test', '--report', 'safe.md')
        self.assertEqual(code, 3)
        self.assertNotIn('hunter2', out+err)
        self.assertIn('Không lưu được', err)

    def test_backend_error_is_not_echoed(self):
        async def fail(*_, **__):
            raise ValueError('db_password=hunter2')
        with patch('agent.cli.__main__.run_demo', side_effect=fail):
            code, out, err = self.invoke('--scenario', 'active', '--question', 'test')
        self.assertEqual(code, 1)
        self.assertNotIn('hunter2', out+err)

    def test_redaction_and_escape_before_output(self):
        async def result(*_, **__):
            return DemoOutcome(Report('concluded', conclusion='password=hunter2',
                evidence=(Evidence('e1', 'synthetic:fixture', 'client_secret=abc123\n\x1b[31m<script>'),),
                citations=('e1',)), ('RECEIVED', 'REPORTED'))
        with patch('agent.cli.__main__.run_demo', side_effect=result), patch('agent.cli.__main__.save_report') as writer:
            code, out, err = self.invoke('--scenario', 'active', '--question', 'test', '--report', 'safe.md')
            saved = writer.call_args.args[1]
        self.assertEqual(code, 0)
        for secret in ('hunter2', 'abc123', '\x1b', '<script>'):
            self.assertNotIn(secret, out+err+saved)
        self.assertIn('DỮ LIỆU ĐÃ CHE', out)

    def test_no_evidence_report_is_useful(self):
        text = format_outcome(DemoOutcome(Report('insufficient_evidence'), ('RECEIVED', 'REPORTED')))
        self.assertIn('Không đủ bằng chứng', text)
        self.assertIn('Không có evidence', text)

    def test_untrusted_trace_is_rejected(self):
        with self.assertRaises(ValueError):
            format_outcome(DemoOutcome(Report('cancelled'), ('password=hunter2',)))

    def test_timeout_and_disconnect_messages(self):
        for status, phrase in (('tool_timeout', 'hết thời gian'), ('disconnected', 'Mất kết nối')):
            text = format_outcome(DemoOutcome(Report(status, execution_state='unknown'), ('RECEIVED', 'ERROR', 'REPORTED')))
            self.assertIn(phrase, text)
            self.assertIn('chưa xác định', text)

    def test_help_has_no_production_backend_or_approval_flags(self):
        code, out, _ = self.invoke('--help')
        self.assertEqual(code, 0)
        for flag in ('--restart', '--approve', '--server', '--model', '--yes'):
            self.assertNotIn(flag, out)

    def test_question_stdin_interrupt_is_safe(self):
        with patch('sys.stdin.readline', side_effect=KeyboardInterrupt), redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--scenario', 'active']), 130)

    def test_trace_rejects_raw_markup_even_when_evidence_renderer_escapes_it(self):
        report = Report('concluded', conclusion='test', evidence=(Evidence('<script>', 'synthetic', 'fixture'),), citations=('<script>',))
        with self.assertRaises(ValueError):
            format_outcome(DemoOutcome(report, ('RECEIVED', 'REPORTED'),
                                      (ToolEvent('service_status', 'ok', ('<script>',)),)))

    def test_unbounded_trace_is_rejected(self):
        with self.assertRaises(ValueError):
            format_outcome(DemoOutcome(Report('cancelled'), ('RECEIVED',)*25))

    def test_sensitive_fixture_formats_are_suppressed_before_render_and_save(self):
        from agent.cli.synthetic import redact
        values = ('db_password=hunter2', 'client_secret=abc123', 'AWS_SECRET_ACCESS_KEY=abc123',
                  'token=abc123', 'refresh_token=abc123', 'password is hunter2',
                  '{"password": "hunter2"}', '{\\"client_secret\\": \\"abc123\\"}',
                  'MYSQL_PWD=hunter2', 'https://user:hunter2@example.invalid', 'Bearer abc123')
        for text in values:
            with self.subTest(format=text):
                self.assertEqual(redact(text), '[DỮ LIỆU ĐÃ CHE]')
                rendered = format_outcome(DemoOutcome(Report('concluded', conclusion='giả lập',
                    evidence=(Evidence('e1', 'synthetic:fixture', text),), citations=('e1',)),
                    ('RECEIVED', 'REPORTED')))
                self.assertNotIn('hunter2', rendered)
                self.assertNotIn('abc123', rendered)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_cancellation_joins_owned_dispatch(self):
        started = asyncio.Event()
        class Waiting(SyntheticExecutor):
            async def execute(self, *_):
                started.set()
                await asyncio.Event().wait()
        executor = Waiting(steps_for('active'))
        c = make_coordinator('active', executor=executor)
        task = asyncio.create_task(run_demo('active', 'test', coordinator=c))
        await started.wait()
        task.cancel()
        outcome = await task
        self.assertEqual(outcome.report.status, 'cancelled')
        self.assertFalse(outcome.report.cleanup_pending)
        self.assertTrue(executor.closed)
        self.assertIsNone(c._operation)
        self.assertTrue(task.done())

    async def test_timeout_does_not_retry_or_leave_owned_task(self):
        class Waiting(SyntheticExecutor):
            attempts = 0
            async def execute(self, *_):
                self.attempts += 1
                await asyncio.Event().wait()
        executor = Waiting(steps_for('active'))
        c = make_coordinator('active', executor=executor, seconds=.01)
        outcome = await run_demo('active', 'test', coordinator=c)
        self.assertEqual(outcome.report.status, 'budget_exhausted')
        self.assertEqual(outcome.report.execution_state, 'unknown')
        self.assertEqual(executor.attempts, 1)
        self.assertTrue(executor.closed)
        self.assertIsNone(c._operation)

    async def test_model_cannot_dispatch_restart(self):
        class Evil:
            async def choose(self, *_):
                return Decision(tool='restart_service', arguments={'unit': 'nginx.service'})
        c = make_coordinator('active', model=Evil())
        outcome = await run_demo('active', 'test', coordinator=c)
        self.assertEqual(outcome.report.status, 'policy_denied')
        self.assertEqual(c.executor.calls, [])
        self.assertTrue(c.executor.closed)

    async def test_wrong_schema_is_rejected(self):
        executor = SyntheticExecutor(steps_for('active'))
        for args in ({'unit': 'ssh.service'}, {'unit': 'nginx.service', 'sudo': True}, {'port': 80}):
            with self.assertRaises(Exception):
                await executor.execute('service_status', args)
        self.assertEqual(executor.calls, [])
        await executor.finish_cleanup()

    async def test_fixture_does_not_call_network_or_spawn(self):
        with patch('subprocess.Popen', side_effect=AssertionError('no subprocess')), \
             patch('socket.socket', side_effect=AssertionError('no network')):
            outcome = await run_demo('port-conflict', 'test')
        self.assertEqual(outcome.report.status, 'concluded')


class ReportStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = Path.cwd()
        os.chdir(self.temp.name)

    def tearDown(self):
        os.chdir(self.previous)
        self.temp.cleanup()

    def test_private_atomic_report_and_no_pending_files(self):
        path = save_report('incident.md', 'SYNTHETIC DEMO — thử nghiệm')
        self.assertIn('SYNTHETIC DEMO', path.read_text())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_no_overwrite(self):
        path = save_report('incident.md', 'first')
        with self.assertRaises(StoreError):
            save_report('incident.md', 'second')
        self.assertEqual(path.read_text(), 'first')
        self.assertEqual(len(list(path.parent.iterdir())), 1)

    def test_symlink_report_is_not_followed(self):
        Path('reports').mkdir(mode=0o700)
        Path('outside').write_text('keep')
        Path('reports/incident.md').symlink_to('../outside')
        with self.assertRaises(StoreError):
            save_report('incident.md', 'overwrite')
        self.assertEqual(Path('outside').read_text(), 'keep')

    def test_symlink_directory_is_rejected(self):
        Path('elsewhere').mkdir()
        Path('reports').symlink_to('elsewhere')
        with self.assertRaises(StoreError):
            save_report('incident.md', 'unsafe')
        self.assertEqual(list(Path('elsewhere').iterdir()), [])

    def test_group_writable_directory_is_rejected(self):
        Path('reports').mkdir(mode=0o700)
        Path('reports').chmod(0o770)
        with self.assertRaises(StoreError):
            save_report('incident.md', 'unsafe')

    def test_path_traversal_and_oversize(self):
        for name in ('../outside.md', '/tmp/outside.md'):
            with self.assertRaises(StoreError):
                save_report(name, 'unsafe')
        with self.assertRaises(StoreError):
            save_report('safe.md', 'a'*70001)
        self.assertFalse(Path('reports').exists())

    def test_write_failure_leaves_no_report_or_temp(self):
        with patch('agent.cli.report_store.os.fsync', side_effect=OSError('fake secret')):
            with self.assertRaises(StoreError):
                save_report('safe.md', 'body')
        self.assertEqual(list(Path('reports').iterdir()), [])

    def test_temp_collision_does_not_delete_an_existing_file(self):
        Path('reports').mkdir(mode=0o700)
        Path('reports/.pending-fixed').write_text('keep')
        with patch('agent.cli.report_store.secrets.token_hex', return_value='fixed'):
            with self.assertRaises(StoreError):
                save_report('safe.md', 'body')
        self.assertEqual(Path('reports/.pending-fixed').read_text(), 'keep')

    def test_cli_saves_only_sanitized_output(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(['--scenario', 'active', '--question', 'password=hunter2', '--report', 'saved.md'])
        text = Path('reports/saved.md').read_text()
        self.assertEqual(code, 0)
        self.assertEqual(text, out.getvalue())
        self.assertNotIn('hunter2', text+err.getvalue())


class TerminalTests(unittest.TestCase):
    def test_three_module_invocations_from_external_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, code in (('active', 0), ('port-conflict', 0), ('permission-denied', 1)):
                env = dict(os.environ, PYTHONPATH=str(ROOT/'src'), PYTHONDONTWRITEBYTECODE='1')
                result = subprocess.run([sys.executable, '-B', '-m', 'agent.cli', '--scenario', name,
                                         '--question', 'Vì sao Nginx lỗi?', '--report', name+'.md'],
                                        cwd=directory, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn('SYNTHETIC DEMO', result.stdout)
                self.assertEqual((Path(directory)/'reports'/f'{name}.md').read_text(), result.stdout)
