"""VM service boundary tests: fake helper only, test-owned local Unix sockets."""
import asyncio
import io
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from control_plane.approval import ApprovalCoordinator, ApprovalError, GrantCodec, Policy
from control_plane.command import validate_attempt, operator, store_evidence
from agent.cli.report_store import StoreError
from control_plane.protocol import ApprovalEndpoint, Port
from control_plane.review import PendingReview
from control_plane.runtime import ApprovalService, Listener
import test_review as tty_helpers
from test_review import record


def attempt(view,**changes):
    result={k:getattr(view,k) for k in ('action_id','session_id','trace_id','environment_id')}
    return dict(result,unit='nginx.service',outcome='execution_unknown',executed=None,
                job_path=None,job_result=None,error_code=None,cleanup_pending=True,**changes)


class ExecutionPromptTests(unittest.TestCase):
    tty=tty_helpers.ReviewTests.tty

    def view(self,state='approved'):
        return PendingReview(record(state=state),request_started=time.monotonic())

    def test_execution_requires_approved_state_and_distinct_fresh_phrase(self):
        view=self.view()
        original=view.confirm
        view.confirm=lambda **kwargs:original(execution=True,**kwargs)
        decision,output=self.tty(view,lambda v:'THUC_THI '+v.action_id+'\n')
        self.assertEqual(decision,'execution_confirmed')
        self.assertIn('XÁC NHẬN THỰC THI RESTART',output)
        self.assertIn('không retry',output)
        self.assertFalse(view.cleanup_pending)

    def test_old_approval_phrase_cannot_authorize_execution(self):
        view=self.view();original=view.confirm
        view.confirm=lambda **kwargs:original(execution=True,**kwargs)
        decision,_=self.tty(view,lambda v:'DONG_Y '+v.action_id+'\n')
        self.assertEqual(decision,'invalid_input')

    def test_pending_non_tty_and_existing_decision_fail_closed(self):
        self.assertEqual(self.view('pending').confirm(execution=True),'state_final')
        self.assertEqual(self.view().confirm(execution=True,stdin=io.StringIO(),stdout=io.StringIO()),'non_tty')
        view=self.view();view._decision='confirmed'
        self.assertNotEqual(view.confirm(execution=True),'execution_confirmed')

    def test_unknown_result_requires_nullable_executed_and_exact_correlation(self):
        view=self.view();result=attempt(view)
        self.assertIsNone(validate_attempt(result,view)['executed'])
        for change in ({'executed':False},{'executed':True},{'action_id':'wrong'},
                       {'outcome':'succeeded'},{'unit':'ssh.service'},{'cleanup_pending':1},
                       {'job_result':'done'}):
            with self.subTest(change=change),self.assertRaises(ApprovalError):
                validate_attempt(result|change,view)


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.policy=Policy('synthetic-lab','p1',14001,14002,frozenset({14003}))
        self.core=ApprovalCoordinator(GrantCodec(self.policy,bytes(range(32)),'epoch1'))
        self.endpoint=ApprovalEndpoint(self.core)
        self.session=self.core.open_session(peer_uid=14003)
        self.action=self.core.propose(peer_uid=14001,session_id=self.session,trace_id='trace1',
                                     unit='nginx.service',evidence_ids=['synthetic-test-evidence'])
        self.connects=self.submits=0
        outer=self
        class FakeHandoff:
            async def submit(self,channel,action_id):
                outer.endpoint._take_grant(action_id);outer.submits+=1
                return dict(outcome='execution_unknown',executed=None)
            async def status(self,channel,action_id):
                return dict(outcome='execution_unknown',executed=None)
        async def connect():self.connects+=1;return object()
        self.service=ApprovalService(self.endpoint,FakeHandoff(),write_enabled=True,helper_connect=connect)

    async def asyncTearDown(self):self.endpoint.close()

    def request(self,op):return dict(version=1,op=op,action_id=self.action.action_id)

    async def call(self,op,port=Port.CONTROL,uid=14003):
        return await self.service.dispatch(self.request(op),port,uid)

    async def test_approval_alone_never_connects_or_submits_to_helper(self):
        result=await self.call('approve')
        self.assertEqual(result['state'],'approved')
        self.assertIsNone(result['executed'])
        self.assertEqual((self.connects,self.submits),(0,0))

    async def test_reader_proposal_context_requires_exact_owner_session(self):
        request=self.request('proposal_context')|{'session_id':self.session}
        result=await self.service.dispatch(request,Port.PROPOSAL,14001)
        self.assertEqual(result['action']['state'],'pending')
        self.assertIsNone(result['attempt'])
        for change,port,uid in [({'session_id':'forged'},Port.PROPOSAL,14001),
                ({},Port.CONTROL,14003),({},Port.PROPOSAL,14004),
                ({'grant':'synthetic-canary'},Port.PROPOSAL,14001)]:
            with self.subTest(change=change,uid=uid),self.assertRaises(ApprovalError):
                await self.service.dispatch(request|change,port,uid)
        self.assertEqual((self.connects,self.submits),(0,0))

    async def test_reader_context_reads_approved_history_without_submit(self):
        await self.call('approve')
        from unittest.mock import AsyncMock
        self.service.handoff.read_status=AsyncMock(return_value={'outcome':'execution_unknown','executed':None})
        result=await self.service.dispatch(self.request('proposal_context')|{'session_id':self.session},Port.PROPOSAL,14001)
        self.assertIsNone(result['attempt']['executed'])
        self.service.handoff.read_status.assert_awaited_once()
        self.assertEqual((self.connects,self.submits),(1,0))
        self.assertIn(self.action.action_id,self.endpoint._grants)

    async def test_rejected_pending_and_reader_cannot_execute(self):
        with self.assertRaises(ApprovalError):await self.call('execute')
        await self.call('approve')
        for port,uid in [(Port.PROPOSAL,14001),(Port.CONTROL,14001),(Port.CONTROL,14004)]:
            with self.subTest(port=port,uid=uid),self.assertRaises(ApprovalError):
                await self.call('execute',port,uid)
        self.assertEqual(self.submits,0)

    async def test_disabled_write_keeps_grant_and_does_not_connect(self):
        await self.call('approve');self.service.enabled=False
        with self.assertRaises(ApprovalError) as error:await self.call('execute')
        self.assertEqual(error.exception.code,'EXECUTION_DISABLED')
        self.assertEqual(self.connects,0)
        self.assertIn(self.action.action_id,self.endpoint._grants)

    async def test_explicit_execute_once_preserves_unknown_no_retry(self):
        await self.call('approve')
        result=await self.call('execute')
        self.assertEqual(result,dict(outcome='execution_unknown',executed=None))
        with self.assertRaises(ApprovalError):await self.call('execute')
        self.assertEqual(self.submits,1)
        self.assertEqual(self.core.status(self.action.action_id,peer_uid=14003).state,'approved')

    async def test_invalid_fields_and_shutdown_cannot_dispatch(self):
        await self.call('approve')
        for change in ({'version':True},{'unit':'ssh.service'},{'command':'private-canary'},
                       {'approval_token':'private-canary'}):
            with self.subTest(change=change),self.assertRaises(ApprovalError):
                await self.service.dispatch(self.request('execute')|change,Port.CONTROL,14003)
        self.endpoint.close()
        with self.assertRaises(ApprovalError):await self.call('execute')
        self.assertEqual(self.submits,0)


class ListenerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.root.chmod(0o750)

    async def test_preexisting_path_is_not_deleted_or_overwritten(self):
        path=self.root/'service.sock';path.write_text('owned-by-someone-else')
        with self.assertRaises(OSError):Listener(path,os.getegid(),os.geteuid(),None)
        self.assertEqual(path.read_text(),'owned-by-someone-else')

    async def test_untrusted_parent_is_rejected_before_bind(self):
        self.root.chmod(0o777)
        with self.assertRaises(ApprovalError):Listener(self.root/'service.sock',os.getegid(),os.geteuid(),None)

    async def test_handlers_bounded_and_all_joined_on_shutdown(self):
        release=asyncio.Event()
        async def hold(channel):
            async with channel:await release.wait()
        listener=Listener(self.root/'service.sock',os.getegid(),os.geteuid(),hold)
        peers=[]
        try:
            listener.start()
            for _ in range(7):
                peer=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);peer.setblocking(False)
                peers.append(peer)
                await asyncio.get_running_loop().sock_connect(peer,str(listener.path))
                await asyncio.sleep(.01)
            self.assertEqual(len(listener.tasks),4)
            await listener.close()
            self.assertFalse(listener.tasks)
            self.assertFalse(listener.path.exists())
            self.assertTrue(listener.accept_task.done())
        finally:
            for peer in peers:peer.close()
            await listener.close()


class EvidenceStorageTests(unittest.TestCase):
    def setUp(self):
        self.cwd=os.open('.',os.O_RDONLY|os.O_DIRECTORY)
        self.addCleanup(os.close,self.cwd)
        self.addCleanup(os.fchdir,self.cwd)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.root.chmod(0o700)
        self.path=patch('control_plane.command.READER_STATE',self.root)
        self.path.start();self.addCleanup(self.path.stop)
        self.envelope={'status':'ok','evidence':[{'evidence_id':'synthetic-storage-evidence',
            'source':'synthetic.storage-fixture','observed_at':'2026-01-01T00:00:00Z',
            'excerpt':'fixed synthetic state','redacted':True,'truncated':False}]}

    def test_snapshot_keeps_complete_provenance_private_and_no_overwrite(self):
        import json
        before=Path.cwd()
        path=store_evidence(self.envelope,'a'*32)
        self.assertEqual(Path.cwd(),before)
        self.assertTrue(path.is_absolute())
        text=path.read_text()
        self.assertEqual(json.loads(text.split('```json\n',1)[1].split('\n```',1)[0]),self.envelope)
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        with self.assertRaises(StoreError):store_evidence(self.envelope,'a'*32)
        self.assertEqual(Path.cwd(),before)
        self.assertEqual(path.read_text(),text)

    def test_report_directory_symlink_is_denied(self):
        other=self.root/'other';other.mkdir()
        (self.root/'reports').symlink_to(other,target_is_directory=True)
        with self.assertRaises(StoreError):store_evidence(self.envelope,'a'*32)
        self.assertEqual(list(other.iterdir()),[])

    def test_oversized_snapshot_is_never_published(self):
        with self.assertRaises(StoreError):store_evidence({'oversized':'x'*70001},'a'*32)
        self.assertFalse((self.root/'reports').exists())  # Cap is checked before directory/file creation.

    def test_untrusted_state_directory_is_denied(self):
        self.root.chmod(0o777)
        with self.assertRaises(ApprovalError):store_evidence(self.envelope,'a'*32)


if __name__=='__main__':unittest.main()
