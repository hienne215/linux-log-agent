"""Filtered read IPC policy, privacy and ownership. No real journal/systemd IO."""
import asyncio,json,os,socket,struct,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from agent.diagnostics import read_gateway as gateway
from agent.diagnostics import read_gateway_worker as worker
from agent.diagnostics import health_verification as health
from agent.diagnostics.journal_policy import window
from agent.diagnostics.system_diagnostics import SourceError
from agent import diagnostic_worker
from control_plane.runtime import Listener
from control_plane.transport import UnixMessageChannel,ChannelError


def args(tool='query_journal'):
 value=dict(schema_version='1.0.0',session_id='gateway-test-session',trace_id='gateway-test-trace',
            logical_tool_call_id='gateway-test-call',attempt_id='gateway-test-attempt')
 if tool in ('query_journal','search_log','detect_anomaly','memory_oom_events','failed_logins'):value.update(window(),limit=100)
 if tool=='query_journal':value.update(unit='nginx.service',priority=3)
 if tool=='check_config':value.update(profile='nginx',unit='nginx.service')
 return value


def result(a=None):
 import mock_mcp_server as c
 a=a or args()
 return c.make_response('query_journal',a['trace_id'],a['logical_tool_call_id'],a['attempt_id'],
   dict(entries=[],count=0,limit=100),a['session_id'],status='partial',evidence=[dict(evidence_id='gateway-synthetic-evidence',
   source='collector.query_journal.journalctl.v1',observed_at=c.current_time(),unit='nginx.service',
   excerpt='Synthetic empty bounded read; not proof of completeness.',redacted=True,truncated=False)])


class PolicyTests(unittest.TestCase):
 def test_write_and_unrelated_tools_not_admitted(self):
  self.assertEqual(gateway.ALLOWED,{'query_journal','search_log','detect_anomaly','memory_oom_events','failed_logins','check_config'})
  self.assertNotIn('restart_service',gateway.ALLOWED)

 def test_missing_config_is_local_mode_not_silent_failure_fallback(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(gateway,'CONFIG',Path(tmp)/'absent'):
   self.assertFalse(gateway.configured())
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'config';p.write_text('{"enabled":true}');p.chmod(0o644)
   with patch.object(gateway,'CONFIG',p),self.assertRaises(gateway.ReadError):gateway.configured()

 def test_root_worker_does_not_recurse_or_require_reader_membership(self):
  with patch.object(gateway.os,'geteuid',return_value=0),patch.object(gateway,'configured') as config:
   self.assertFalse(gateway.enabled());config.assert_not_called()

 def test_opted_in_wrong_reader_is_denied(self):
  with patch.object(gateway.os,'geteuid',return_value=12000),patch.object(gateway,'configured',return_value=True),patch.object(
    gateway.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=12001)),self.assertRaises(gateway.ReadError):gateway.enabled()

 def test_config_sandbox_fixed_argv_and_unit_scope(self):
  command=worker.config_command('lla-config-check-'+'a'*32+'.service')
  self.assertEqual(command[-5:],('/usr/sbin/nginx','-t','-q','-e','stderr'))
  for expected in ('DynamicUser=yes','NoNewPrivileges=yes','PrivateNetwork=yes','ProtectSystem=strict',
     'TemporaryFileSystem=/run:rw,mode=1777','RuntimeMaxSec=3','KillMode=control-group'):
   self.assertIn(expected,command)
  self.assertNotIn('sudo',command);self.assertNotIn('restart',command)
  with self.assertRaises(ValueError):worker.config_command('nginx.service')

 def test_canonical_result_metadata_and_secret_rejected(self):
  good=result();self.assertEqual(gateway.validated('query_journal',args(),good),good)
  for change in ('correlation','secret','forged_ref','approval'):
   value=json.loads(json.dumps(good))
   if change=='correlation':value['attempt_id']='wrong'
   elif change=='secret':value['evidence'][0]['excerpt']='MYSQL_PWD=private-gateway-canary'
   elif change=='forged_ref':value['evidence'].append(dict(value['evidence'][0]))
   else:value['status']='approval_required'
   # Schema/correlation/privacy rejects without reflecting wire text.
   with self.subTest(change=change),self.assertRaises(gateway.ReadError) as e:gateway.validated('query_journal',args(),value)
   self.assertEqual(str(e.exception),'EXECUTION_UNKNOWN')


class DispatchTests(unittest.IsolatedAsyncioTestCase):
 async def test_unknown_write_and_extra_fields_never_spawn(self):
  runner=SimpleNamespace(run=AsyncMock());service=gateway.ReadService(runner)
  for message in ({'version':1,'tool':'restart_service','arguments':{}},
      {'version':True,'tool':'query_journal','arguments':args()},
      {'version':1,'tool':'query_journal','arguments':args(),'command':'private-canary'}):
   with self.subTest(message=message),self.assertRaises(gateway.ReadError):await service.dispatch(message)
  runner.run.assert_not_awaited()

 async def test_invalid_canonical_scope_never_spawns_or_reflects_secret(self):
  runner=SimpleNamespace(run=AsyncMock());service=gateway.ReadService(runner)
  for a in (args()|{'unit':'sshd.service'},args()|{'unknown':'private-gateway-canary'},args()|{'since':None}):
   value=await service.dispatch(dict(version=1,tool='query_journal',arguments=a))
   self.assertEqual(value['status'],'error');self.assertEqual(value['evidence'],[])
   self.assertNotIn('private-gateway-canary',json.dumps(value))
  runner.run.assert_not_awaited()

 async def test_worker_success_timeout_malformed_and_output_limit_no_retry(self):
  from src.mcp_server.runner import WorkerFailure
  for raw in (json.dumps(result()).encode(),b'{malformed private-canary',WorkerFailure('TIMEOUT'),WorkerFailure('OUTPUT_LIMIT')):
   run=AsyncMock(side_effect=raw if isinstance(raw,Exception) else None,return_value=raw)
   service=gateway.ReadService(SimpleNamespace(run=run))
   response=await service.dispatch(dict(version=1,tool='query_journal',arguments=args()))
   self.assertEqual(run.await_count,1);self.assertNotIn('private-canary',json.dumps(response))
   self.assertEqual(response['status'],'partial' if type(raw) is bytes and raw.startswith(b'{"') else 'error')
   if isinstance(raw,Exception):self.assertEqual(response['error_code'],raw.code)

 async def test_unicode_wrapper_cap_returns_bounded_canonical_error(self):
  import mock_mcp_server as contracts
  value=result();value['data']['entries']=[dict(timestamp=value['completed_at'],priority=3,message='ế'*120) for _ in range(100)]
  value['data']['count']=100
  contracts.TOOLS['query_journal'][4].validate(value)
  raw=json.dumps(value,ensure_ascii=False).encode()
  self.assertLess(len(raw),65536)
  self.assertGreater(len(json.dumps(dict(version=1,result=value),ensure_ascii=True).encode()),65536)
  run=AsyncMock(return_value=raw)
  response=await gateway.ReadService(SimpleNamespace(run=run)).dispatch(dict(version=1,tool='query_journal',arguments=args()))
  contracts.TOOLS['query_journal'][4].validate(response)
  self.assertEqual(response['error_code'],'OUTPUT_LIMIT');self.assertEqual(response['evidence'],[])
  self.assertNotIn('entries',response['data']);self.assertNotIn('ế',json.dumps(response,ensure_ascii=False))
  self.assertLess(len(json.dumps(response).encode()),65536)
  self.assertEqual(run.await_count,1)

 async def test_proxy_does_not_fall_back_to_local_collector(self):
  a=args();r=result(a)
  with patch.object(gateway,'enabled',return_value=True),patch.object(gateway,'request',AsyncMock(return_value=r)) as request,patch.object(
    diagnostic_worker,'collect_journal',AsyncMock()) as local:
   self.assertEqual(await diagnostic_worker.collect_response('query_journal',a),r)
   local.assert_not_awaited();request.assert_awaited_once_with('query_journal',a)
  with patch.object(gateway,'enabled',return_value=True),patch.object(gateway,'request',AsyncMock(side_effect=gateway.ReadError('TIMEOUT'))) as request,patch.object(
    diagnostic_worker,'collect_journal',AsyncMock()) as local:
   r=await diagnostic_worker.collect_response('query_journal',a)
   self.assertEqual(r['error_code'],'TIMEOUT');self.assertEqual(r['evidence'],[])
   local.assert_not_awaited();self.assertEqual(request.await_count,1)

 async def test_health_uses_projected_records_without_broad_journal_access(self):
  with patch.object(gateway,'enabled',return_value=True),patch.object(gateway,'journal_entries',AsyncMock(return_value=[])) as query,patch(
    'agent.diagnostics.journal_reader.collect_journal',AsyncMock()) as local:
   check,evidence=await health.log_check({'since':window()['since']})
   self.assertEqual(check['result'],'unknown');self.assertEqual(len(evidence),1)
   self.assertIn('history/reader coverage not independently complete',evidence[0]['excerpt'])
   local.assert_not_awaited();self.assertEqual(query.await_count,1)

 async def test_config_cancellation_retains_cleanup_of_only_owned_validator(self):
  started=asyncio.Event();closing=asyncio.Event();release=asyncio.Event();commands=[]
  async def execute(command):
   commands.append(command)
   if command[0]=='/usr/bin/systemd-run':started.set();await asyncio.Event().wait()
   closing.set();await release.wait();return 0,b'',b''
  with patch('agent.diagnostics.journal_reader.execute',execute):
   owner=asyncio.create_task(worker.sandbox_config({'profile':'nginx'}))
   await started.wait();owner.cancel();await closing.wait();owner.cancel()
   for _ in range(5):await asyncio.sleep(0)
   self.assertFalse(owner.done());release.set()
   with self.assertRaises(asyncio.CancelledError):await owner
  self.assertEqual(len(commands),2)
  self.assertEqual(commands[1][-1],commands[0][5].split('=',1)[1])
  self.assertNotEqual(commands[1][-1],'nginx.service')
  self.assertFalse(any(t.get_name()=='config-validator-cleanup' and not t.done() for t in asyncio.all_tasks()))


class TransportTests(unittest.IsolatedAsyncioTestCase):
 async def test_wide_read_channel_does_not_widen_control_default(self):
  a,b=socket.socketpair()
  try:
   control=UnixMessageChannel(a,expected_uid=os.geteuid())
   with self.assertRaises(ChannelError):await control.send({'oversized':'x'*5000})
   control.close()
  finally:a.close();b.close()
  a,b=socket.socketpair()
  async with UnixMessageChannel(a,expected_uid=os.geteuid(),seconds=8,max_bytes=65536) as sender,UnixMessageChannel(b,expected_uid=os.geteuid(),seconds=8,max_bytes=65536) as receiver:
   value={'bounded':'x'*8000}
   await sender.send(value);self.assertEqual(await receiver.receive(),value)

 async def test_gateway_real_socket_roundtrip_and_peer_auth_no_root_claim(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp);path.chmod(0o750)
   runner=SimpleNamespace(run=AsyncMock(return_value=json.dumps(result()).encode()))
   service=gateway.ReadService(runner)
   listener=Listener(path/'service.sock',os.getegid(),os.geteuid(),service.serve,max_bytes=65536)
   listener.start()
   real=UnixMessageChannel
   def channel(connection,*,expected_uid,**kwargs):
    self.assertEqual(expected_uid,0) # Production pins root; this unit test uses one real current UID.
    return real(connection,expected_uid=os.geteuid(),**kwargs)
   try:
    with patch.object(gateway,'SOCKET',path/'service.sock'),patch.object(gateway,'UnixMessageChannel',channel):
     response=await gateway.request('query_journal',args())
     self.assertEqual(response['status'],'partial');self.assertEqual(runner.run.await_count,1)
   finally:await listener.close()
   self.assertFalse(listener.tasks);self.assertTrue(listener.accept_task.done());self.assertFalse(listener.path.exists())

if __name__=='__main__':unittest.main()
