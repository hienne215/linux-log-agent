"""Resource discovery does not require the operator's current directory."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import tomllib
import unittest
import io
from contextlib import redirect_stdout,redirect_stderr
from unittest.mock import patch

from agent import runtime

ROOT = Path(__file__).resolve().parents[2]


class RuntimeTests(unittest.TestCase):
    def test_bad_arguments_do_not_echo_sensitive_values_or_start_session(self):
        from agent.read_only import main
        canary='synthetic-cli-canary'
        for argv in (['--scope','MYSQL_PWD='+canary],['--backend',canary],['--unknown-token',canary]):
            with self.subTest(argument=argv[0]),patch('agent.read_only.ReadOnlySession') as session:
                out,err=io.StringIO(),io.StringIO()
                with redirect_stdout(out),redirect_stderr(err),self.assertRaises(SystemExit) as error:
                    main(argv)
                self.assertEqual(error.exception.code,2)
                self.assertTrue(canary not in out.getvalue()+err.getvalue(),'ARGUMENT_PRIVACY')
                session.assert_not_called()

    def test_doctor_does_not_create_session_or_read_question(self):
        from agent.read_only import main
        with patch('agent.runtime.doctor',return_value=('PREFLIGHT ONLY\n',0)) as check,patch(
                'agent.read_only.ReadOnlySession') as session,redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--doctor','--backend','mock']),0)
            from agent.read_only import SCOPES
            check.assert_called_once_with('mock',tools=SCOPES['all']);session.assert_not_called()

    def test_doctor_rejects_diagnosis_or_report_arguments(self):
        from agent.read_only import main
        for extra in (['--question','Check disk'],['--report','unexpected.md']):
            with patch('agent.runtime.doctor') as check,redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):main(['--doctor',*extra])
                check.assert_not_called()

    def test_doctor_uses_selected_cli_scope(self):
        from agent.read_only import main
        with patch('agent.runtime.doctor',return_value=('SCOPE ONLY\n',0)) as check,redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--doctor','--backend','mock','--scope','service']),0)
        check.assert_called_once_with('mock',tools=('service_status',))

    def test_doctor_service_does_not_require_nginx_or_unrelated_executables(self):
        with patch.object(runtime,'_executable',return_value=True) as executable,patch(
                'subprocess.Popen',side_effect=AssertionError('PREFLIGHT_MUST_NOT_SPAWN')):
            text,code=runtime.doctor('mock',tools=('service_status',))
        self.assertEqual(code,0);executable.assert_called_once_with('/usr/bin/systemctl')
        self.assertIn('service_status: NOT_VERIFIED',text);self.assertNotIn('check_config:',text)
        self.assertNotIn('nginx: BLOCKED',text);self.assertIn('Exit 0',text)

    def test_doctor_disk_needs_no_external_collector_executable(self):
        with patch.object(runtime,'_executable') as executable:
            text,code=runtime.doctor('mock',tools=('disk_usage',))
        executable.assert_not_called();self.assertEqual(code,0)
        self.assertIn('disk_usage: NOT_VERIFIED',text)

    def test_doctor_config_missing_prerequisite_is_blocked_without_unsandboxed_fallback(self):
        with patch.object(runtime,'_executable',side_effect=lambda p:p!='/usr/bin/bwrap'):
            text,code=runtime.doctor('mock',tools=('check_config',))
        self.assertEqual(code,1);self.assertIn('check_config: BLOCKED — thiếu bwrap',text)
        self.assertIn('không chạy ngoài sandbox',text)

    def test_doctor_missing_packaged_schema_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(runtime,'runtime_root',return_value=Path(directory)):
            text,code=runtime.doctor('mock',tools=('disk_usage',))
        self.assertEqual(code,1);self.assertIn('Canonical resources: BLOCKED',text)

    def test_doctor_scope_covers_cli_and_rejects_unknown_before_model_preflight(self):
        from agent.read_only import SCOPES
        self.assertEqual(set(runtime.TOOL_COMMANDS),set(SCOPES['all']))
        self.assertTrue(all(set(tools)<=runtime.TOOL_COMMANDS.keys() for tools in SCOPES.values()))
        with patch('agent.integration_demo.preflight.check') as check:
            for backend,tools in [('invalid',('service_status',)),('ollama',()),('ollama',('restart_service',))]:
                with self.subTest(backend=backend,tools=tools),self.assertRaisesRegex(ValueError,'^INVALID_PREFLIGHT_SCOPE$'):
                    runtime.doctor(backend,tools=tools)
            check.assert_not_called()

    def test_checkout_resources_exist_and_all_packages_are_declared(self):
        self.assertEqual(runtime.runtime_root(), ROOT)
        config = tomllib.loads((ROOT/'pyproject.toml').read_text())
        declared = set(config['tool']['setuptools']['packages'])
        for init in (ROOT/'src/agent').rglob('__init__.py'):
            name = '.'.join(init.parent.relative_to(ROOT/'src').parts)
            self.assertIn(name, declared)
        self.assertEqual(config['project']['scripts']['linux-log-agent'], 'agent.read_only:main')
        self.assertIn('src.mcp_server', declared)
        self.assertIn('schemas.tools', declared)

    def test_installed_resources_are_found_by_module_location(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)/'site-packages'
            site.mkdir();(site/'schemas').mkdir()
            (site/'schemas/manifest.json').write_text('{}')
            (site/'run_mcp_server.py').write_text('')
            with patch.object(runtime,'__file__',str(site/'agent/runtime.py')),patch.object(
                    runtime,'find_spec',return_value=SimpleNamespace(origin=str(site/'mock_mcp_server.py'))):
                self.assertEqual(runtime.runtime_root(),site)

    def test_incomplete_installation_fails_without_running_any_collector(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runtime,'__file__',str(Path(directory)/'agent/runtime.py')),patch.object(
                    runtime,'find_spec',return_value=None):
                with self.assertRaisesRegex(RuntimeError,'INCOMPLETE_INSTALLATION'):
                    runtime.runtime_root()
