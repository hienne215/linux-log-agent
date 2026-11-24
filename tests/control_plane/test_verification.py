"""Post-action boundary tests; synthetic jobs and collector IO, never a restart."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

from agent.llm.redaction import _clean
from agent.mcp_client import StdioMCPClient,StdioConfig,MCPClientError
from agent.service_binding import ServiceBinding
from agent.runtime import runtime_root
from control_plane.approval import ApprovalError
from control_plane.command import store_evidence,validate_attempt,verify
from control_plane.verification import baseline,fresh_checks,incident,now
from control_plane.review import PendingReview
from control_plane.protocol import Port
from test_review import record
import test_runtime

ROOT=runtime_root()
FIXTURE=Path(__file__).resolve().parents[1]/'canonical_agent/health_fixture.py'


def before(view):
    import mock_mcp_server as c
    at=now()
    result=c.make_response('service_status',view.trace_id,'baseline-call','baseline-attempt',
        dict(unit='nginx.service',load_state='loaded',active_state='failed',sub_state='failed',description='[REDACTED]'),
        view.session_id,evidence=[dict(evidence_id=view.evidence_ids[0],source='collector.service_status.systemctl.v1',
        observed_at=at,unit='nginx.service',excerpt='unit=nginx.service; load=loaded; active=failed; sub=failed',redacted=True,truncated=False)])
    result['started_at']=at
    return result


def view():
    return PendingReview(record(trace_id='c'*32,state='approved'),request_started=time.monotonic())


def attempt(v,**changes):
    return dict({k:getattr(v,k) for k in ('action_id','session_id','trace_id','environment_id')},
        unit='nginx.service',outcome='succeeded',executed=True,job_path='/org/freedesktop/systemd1/job/1',
        job_result='done',error_code=None,cleanup_pending=False)|changes


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.cwd=os.open('.',os.O_RDONLY|os.O_DIRECTORY)
        self.addCleanup(os.close,self.cwd);self.addCleanup(os.fchdir,self.cwd)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.root.chmod(0o700)
        self.v=view();self.data=before(self.v)
        self.schema=ServiceBinding(None,ROOT,redact=_clean).schemas[2]
        with patch('control_plane.command.READER_STATE',self.root):self.path=store_evidence(self.data,self.v.trace_id)
        self.path=self.root/self.path

    def load(self):return baseline(self.root,self.v,self.schema,_clean)

    def test_full_canonical_ledger_and_external_refs_preserved(self):
        self.assertEqual(self.load(),self.data)

    def test_forged_baseline_correlation_ids_source_and_raw_secret_fail_closed(self):
        for change in ('session_id','trace_id','source','secret','evidence_id'):
            value=deepcopy(self.data)
            if change in ('session_id','trace_id'):value[change]='wrong'
            elif change=='source':value['evidence'][0]['source']='synthetic.forged'
            elif change=='secret':value['data']['description']='MYSQL_PWD=private-canary'
            else:value['evidence'][0]['evidence_id']='forged'
            from control_plane.verification import SNAPSHOT_PREFIX
            self.path.write_text(SNAPSHOT_PREFIX+json.dumps(value)+'\n```\n')
            with self.subTest(change=change),self.assertRaises(ApprovalError) as error:self.load()
            self.assertEqual(str(error.exception),'INVALID_BASELINE')
            self.assertNotIn('private-canary',str(error.exception))

    def test_symlink_hardlink_writable_and_oversized_files_rejected(self):
        original=self.path.read_bytes()
        self.path.chmod(0o644)
        with self.assertRaises(ApprovalError):self.load()
        self.path.chmod(0o600)
        link=self.path.with_suffix('.link');os.link(self.path,link)
        with self.assertRaises(ApprovalError):self.load()
        link.unlink() # Only this test's own temporary hardlink.
        self.path.write_bytes(b'x'*70001)
        with self.assertRaises(ApprovalError):self.load()
        self.path.write_bytes(original)
        other=self.path.with_suffix('.target');self.path.rename(other);self.path.symlink_to(other)
        with self.assertRaises(ApprovalError):self.load()


class VerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_sdk_fresh_checks_report_and_unknown_never_claim_repair(self):
        v=view();original=before(v)
        for fault,overall in [('active','unknown'),('http_500','fail'),('permission','unknown')]:
            with self.subTest(fault=fault):
                c=StdioMCPClient(StdioConfig(sys.executable,('-B',str(FIXTURE),fault),frozenset({'verify_fix'})))
                result=await fresh_checks(c,ROOT,v,original,since=now(),redact=_clean)
                self.assertIsNone(c._owner);self.assertFalse(c.connected)
                self.assertEqual(result['session_id'],v.session_id);self.assertEqual(result['trace_id'],v.trace_id)
                self.assertEqual(result['data']['action_id'],v.action_id)
                self.assertEqual(result['data']['baseline_evidence_refs'],list(v.evidence_ids))
                self.assertEqual(result['data']['overall_result'],overall)
                self.assertNotIn(v.evidence_ids[0],[e['evidence_id'] for e in result['evidence']])
                for observed in (attempt(v),attempt(v,outcome='execution_unknown',executed=None,job_path=None,job_result=None,error_code='TIMEOUT')):
                    validate_attempt(observed,v)
                    text=incident(v,observed,original,result,redact=_clean)
                    self.assertIn('chỉ đọc',text);self.assertIn('Status: limited',text)
                    self.assertIn('### '+v.evidence_ids[0].replace('-',r'\-'),text)
                    self.assertIn('"job_outcome": "'+observed['outcome']+'"',text)
                    self.assertIn('executed',text)
                    self.assertNotIn('private-canary',text);self.assertNotIn('Set-Cookie',text)

    async def test_sdk_cancellation_disconnect_and_slow_cleanup_keep_owner_without_retry(self):
        for fault in ('cancel','timeout','disconnect'):
            ready=asyncio.Event();closing=asyncio.Event();release=asyncio.Event()
            class Client:
                def __init__(self):self._owner=object();self.calls=self.closes=0
                async def connect(self):pass
                async def list_tools(self):
                    from agent.diagnostics.health_binding import HealthBinding
                    b=HealthBinding(self,ROOT,redact=_clean)
                    return SimpleNamespace(next_cursor=None,tools=[dict(name='verify_fix',inputSchema=b.schemas[0],outputSchema=b.schemas[2])])
                async def call_tool(self,*args):
                    self.calls+=1;ready.set()
                    if fault=='cancel':await asyncio.Event().wait()
                    raise MCPClientError('TIMEOUT' if fault=='timeout' else 'DISCONNECTED')
                async def disconnect(self):
                    self.closes+=1;closing.set();await release.wait();self._owner=None
            c=Client();v=view();task=asyncio.create_task(fresh_checks(c,ROOT,v,before(v),since=now(),redact=_clean))
            await ready.wait()
            if fault=='cancel':task.cancel()
            await closing.wait();task.cancel()
            for _ in range(5):await asyncio.sleep(0)
            self.assertFalse(task.done());self.assertIsNotNone(c._owner)
            release.set()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertIsNone(c._owner);self.assertEqual(c.calls,1)

    async def test_pending_cleanup_and_wrong_role_block_before_collector(self):
        v=view();context=dict(action=dict(v._record,evidence_ids=list(v.evidence_ids)),attempt=attempt(v,cleanup_pending=True))
        with patch('control_plane.command.roles',return_value={'reader':SimpleNamespace(pw_uid=os.geteuid()),'approval':SimpleNamespace(pw_uid=1)}),patch(
                'control_plane.command.OperatorClient._request',AsyncMock(return_value=context)),patch(
                'control_plane.verification.fresh_checks',AsyncMock()) as collect:
            with self.assertRaises(ApprovalError) as error:await verify(v.action_id,v.session_id)
            self.assertEqual(error.exception.code,'CLEANUP_PENDING');collect.assert_not_awaited()
        with patch('control_plane.command.roles',return_value={'reader':SimpleNamespace(pw_uid=os.geteuid()+1)}):
            with self.assertRaises(ApprovalError) as error:await verify(v.action_id,v.session_id)
            self.assertEqual(error.exception.code,'CALLER_DENIED')

    async def test_full_reader_command_writes_private_incident_from_real_sdk_fixture(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        cwd=os.open('.',os.O_RDONLY|os.O_DIRECTORY);self.addCleanup(os.close,cwd);self.addCleanup(os.fchdir,cwd)
        root=Path(tmp.name);root.chmod(0o700);v=view()
        original=before(v)
        with patch('control_plane.command.READER_STATE',root):store_evidence(original,v.trace_id)
        c=StdioMCPClient(StdioConfig(sys.executable,('-B',str(FIXTURE),'active'),frozenset({'verify_fix'})))
        context=dict(action=dict(v._record,evidence_ids=list(v.evidence_ids)),attempt=attempt(v))
        with patch('control_plane.command.READER_STATE',root),patch('control_plane.command.roles',return_value={
                'reader':SimpleNamespace(pw_uid=os.geteuid()),'approval':SimpleNamespace(pw_uid=1)}),patch(
                'control_plane.command.OperatorClient._request',AsyncMock(return_value=context)) as request,patch(
                'agent.mcp_client.StdioMCPClient',return_value=c):
            result=await verify(v.action_id,v.session_id)
        report=root/result['report']
        self.assertEqual(report.stat().st_mode&0o777,0o600)
        self.assertFalse(result['repair_verified']);self.assertFalse(result['cleanup_pending'])
        self.assertEqual(result['check_result'],'unknown');self.assertIsNone(c._owner)
        self.assertEqual(request.await_count,1);self.assertIn('verification_context',str(request.call_args))
        self.assertIn('Status: limited',report.read_text())


class ReaderRouteTests(unittest.IsolatedAsyncioTestCase):
    # Reuse only setup/teardown from the existing boundary fixture, not its tests.
    setUp=test_runtime.DispatchTests.setUp
    asyncTearDown=test_runtime.DispatchTests.asyncTearDown
    request=test_runtime.DispatchTests.request
    call=test_runtime.DispatchTests.call
    async def test_reader_context_requires_original_session_and_never_submits(self):
        self.service.handoff.status=AsyncMock(return_value=dict(outcome='execution_unknown',executed=None))
        request=dict(version=1,op='verification_context',action_id=self.action.action_id,session_id=self.session)
        with self.assertRaises(ApprovalError):await self.service.dispatch(request,Port.PROPOSAL,14001)
        await self.call('approve')
        for changed,port,uid in [(dict(request,session_id='wrong'),Port.PROPOSAL,14001),
                                 (request,Port.CONTROL,14003),(request,Port.PROPOSAL,14004),
                                 (dict(request,command='private-canary'),Port.PROPOSAL,14001)]:
            with self.subTest(changed=changed),self.assertRaises(ApprovalError):await self.service.dispatch(changed,port,uid)
        result=await self.service.dispatch(request,Port.PROPOSAL,14001)
        self.assertEqual(result['action']['session_id'],self.session)
        self.assertIsNone(result['attempt']['executed']);self.assertEqual(self.submits,0)
        self.assertEqual(self.connects,1);self.assertIn(self.action.action_id,self.endpoint._grants)

if __name__=='__main__':unittest.main()
