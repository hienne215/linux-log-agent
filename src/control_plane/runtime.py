"""VM-only service wiring. No MCP/model imports, commands or automatic dispatch.

Administrator-owned paths/identities are fixed. Each explicit operator execute
request consumes one approved grant; approval alone never invokes the helper.
No reconnect, redelivery, unit unlock or state recovery is provided.
"""
import asyncio
import json
import logging
import os
from pathlib import Path
import pwd
import signal
import socket
import stat
from uuid import uuid4

from .approval import ApprovalCoordinator, ApprovalError, GrantCodec, Policy, _unique
from .handoff import HelperHandoff
from .protocol import ApprovalEndpoint, Port
from .state import CoordinatorState
from .transport import ChannelError, UnixMessageChannel

BASE = Path('/run/linux-log-agent')
SOCKETS = {name: BASE/name/'service.sock' for name in ('proposal','control','helper')}
EPOCH = BASE/'helper/epoch'
CONFIG = Path('/etc/linux-log-agent/deployment.json')
KEY = Path('/etc/linux-log-agent/grant.key')


def roles():
    return {name:pwd.getpwnam(account) for name,account in
            [('reader','mcp-log-reader'),('approval','lla-approval'),('operator','lla-operator')]}


def protected_read(path, *, mode, gid, limit):
    """Root-owned fixed configuration; symlinks/writable ancestors are denied."""
    parent=path.parent
    while True:
        info=parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
            raise ApprovalError('INVALID_DEPLOYMENT')
        if parent==parent.parent:break
        parent=parent.parent
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=0
                or info.st_gid!=gid or stat.S_IMODE(info.st_mode)!=mode or info.st_size>limit):
            raise ApprovalError('INVALID_DEPLOYMENT')
        value=os.read(fd,limit+1)
        if len(value)>limit:raise ApprovalError('INVALID_DEPLOYMENT')
        return value
    finally:os.close(fd)


def deployment():
    identities=roles()
    raw=protected_read(CONFIG,mode=0o640,gid=identities['approval'].pw_gid,limit=1024)
    try:
        config=json.loads(raw,object_pairs_hook=_unique)
        if (type(config) is not dict or set(config)!={'environment_id','policy_version','write_enabled'}
                or config['environment_id']!='linux-log-lab-hien'
                or config['policy_version']!='vm-candidate-v1' or type(config['write_enabled']) is not bool
                or socket.gethostname()!='linux-log-lab-hien'
                or Path('/sys/class/dmi/id/sys_vendor').read_text().strip()!='QEMU'):
            raise ValueError()
    except Exception:raise ApprovalError('INVALID_DEPLOYMENT') from None
    policy=Policy(config['environment_id'],config['policy_version'],identities['reader'].pw_uid,
                  identities['approval'].pw_uid,frozenset({identities['operator'].pw_uid}))
    key=protected_read(KEY,mode=0o640,gid=identities['approval'].pw_gid,limit=32)
    if len(key)!=32:raise ApprovalError('INVALID_DEPLOYMENT')
    return identities,policy,key,config['write_enabled']


async def connect(path, expected_uid):
    connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    connection.setblocking(False)
    try:
        await asyncio.wait_for(asyncio.get_running_loop().sock_connect(connection,str(path)),2)
        return UnixMessageChannel(connection,expected_uid=expected_uid,seconds=2)
    except BaseException:
        connection.close()
        raise


class Listener:
    """At most four retained handlers. Never unlinks a pre-existing path."""
    def __init__(self,path,gid,expected_uid,handler,*,max_bytes=4096):
        if type(max_bytes) is not int or max_bytes not in (4096,65536):
            raise ApprovalError('INVALID_DEPLOYMENT')
        self.max_bytes=max_bytes
        self.path,self.expected_uid,self.handler=Path(path),expected_uid,handler
        self.socket=None;self.accept_task=None;self.tasks=set();self.identity=None
        parent=self.path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.geteuid()
                or parent.st_gid!=gid or stat.S_IMODE(parent.st_mode)!=0o750):
            raise ApprovalError('INVALID_DEPLOYMENT')
        connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            connection.bind(str(self.path))
            self.identity=(self.path.lstat().st_dev,self.path.lstat().st_ino)
            os.chown(self.path,-1,gid)
            os.chmod(self.path,0o660)
            connection.listen(4);connection.setblocking(False)
            self.socket=connection
        except BaseException:
            connection.close();self._unlink()
            raise

    def _unlink(self):
        if self.identity is not None:
            try:
                info=self.path.lstat()
                if stat.S_ISSOCK(info.st_mode) and (info.st_dev,info.st_ino)==self.identity:
                    self.path.unlink()
            except FileNotFoundError:pass

    async def _handle(self,connection):
        try:
            channel=UnixMessageChannel(connection,expected_uid=self.expected_uid,
                seconds=2 if self.max_bytes==4096 else 8,max_bytes=self.max_bytes)
            await self.handler(channel)
        except asyncio.CancelledError:raise
        except Exception:pass  # Fixed failures only; never log frames/OS exceptions.
        finally:connection.close()

    async def _accept(self):
        while True:
            connection,_=await asyncio.get_running_loop().sock_accept(self.socket)
            if len(self.tasks)>=4:
                connection.close();continue
            task=asyncio.create_task(self._handle(connection),name='control-channel-owner')
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    def start(self):
        self.accept_task=asyncio.create_task(self._accept(),name='control-listener-owner')

    async def close(self):
        if self.accept_task is not None:
            self.accept_task.cancel()
            await asyncio.gather(self.accept_task,return_exceptions=True)
        if self.socket is not None:self.socket.close()
        pending=tuple(self.tasks)
        for task in pending:task.cancel()
        if pending:await asyncio.gather(*pending,return_exceptions=True)
        self._unlink()


class ApprovalService:
    def __init__(self,endpoint,handoff,*,write_enabled,helper_connect):
        self.endpoint,self.handoff=endpoint,handoff
        self.enabled,self.helper_connect=write_enabled,helper_connect

    async def dispatch(self,request,port,uid):
        if self.endpoint._closed:raise ApprovalError('CLOSED')
        op=request.get('op') if type(request) is dict else None
        if op in ('verification_context','proposal_context'):
            # Read-only reader route. Authorize the original session/action
            # before asking the helper for an already-admitted job's status.
            # No grant, submit, reconnect or new approval is exposed here.
            if port is not Port.PROPOSAL or uid!=self.endpoint.coordinator.policy.reader_uid:
                raise ApprovalError('CALLER_DENIED')
            action=self.endpoint._dispatch(dict(request,op='status'),port,uid)
            if op=='verification_context' and action['state']!='approved':raise ApprovalError('CONFIRMATION_REQUIRED')
            attempt=None
            if op=='verification_context':
                attempt=await self.handoff.status(await self.helper_connect(),action['action_id'])
            elif action['state']=='approved':
                approved=self.endpoint.coordinator.status(action['action_id'],peer_uid=uid,session_id=request['session_id'])
                try:attempt=await self.handoff.read_status(await self.helper_connect(),approved)
                except ApprovalError as e:
                    if e.code!='HELPER_REJECTED':raise
                    # A missing/rejected status is not proof of nonexecution.
                    attempt=None
            return dict(action=action,attempt=attempt)
        if op not in ('execute','execution_status'):
            return self.endpoint._dispatch(request,port,uid)
        if (port is not Port.CONTROL or uid not in self.endpoint.coordinator.policy.operator_uids):
            raise ApprovalError('CALLER_DENIED')
        if (set(request)!={'version','op','action_id'} or type(request['version']) is not int or request['version']!=1):
            raise ApprovalError('INVALID_REQUEST')
        action=self.endpoint.coordinator.status(request['action_id'],peer_uid=uid)
        if action.state!='approved':raise ApprovalError('CONFIRMATION_REQUIRED')
        if op=='execute' and not self.enabled:raise ApprovalError('EXECUTION_DISABLED')
        channel=await self.helper_connect()  # One explicit exchange, no retry.
        if op=='execute':return await self.handoff.submit(channel,action.action_id)
        return await self.handoff.status(channel,action.action_id)

    async def serve(self,channel,*,port):
        async with channel:
            policy=self.endpoint.coordinator.policy
            allowed=(channel.peer.uid==policy.reader_uid if port is Port.PROPOSAL
                     else port is Port.CONTROL and channel.peer.uid in policy.operator_uids)
            if not allowed:raise ApprovalError('CALLER_DENIED')
            request=await channel.receive()
            try:response=dict(version=1,ok=True,result=await self.dispatch(request,port,channel.peer.uid))
            except (ApprovalError,ChannelError) as error:
                response=dict(version=1,ok=False,error_code=error.code)
            except Exception:response=dict(version=1,ok=False,error_code='STATE_UNAVAILABLE')
            await channel.send(response)


def ready():
    address=os.environ.pop('NOTIFY_SOCKET',None)
    if not address or not (address.startswith('/') or address.startswith('@')):
        raise ApprovalError('INVALID_DEPLOYMENT')
    if address.startswith('@'):address='\0'+address[1:]
    with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as connection:
        connection.sendto(b'READY=1',address)


async def run(kind):
    identities,policy,key,enabled=deployment()
    required_uid=0 if kind=='helper' else policy.coordinator_uid
    if os.geteuid()!=required_uid:raise ApprovalError('CALLER_DENIED')
    stop=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stop.set)
    # Third-party disconnect diagnostics must not emit raw exception context.
    logging.disable(logging.CRITICAL)
    loop.set_exception_handler(lambda *_: print('CONTROL_ASYNC_ERROR',flush=True))
    listeners=[];store=endpoint=service=driver=None
    try:
        if kind=='helper':
            from dbus_next.aio import MessageBus
            from dbus_next.constants import BusType
            from .helper import HelperRunner
            from .ledger import HelperLedger
            from .service import HelperService
            from .systemd import SystemdJob
            epoch=uuid4().hex
            # Only a new root-owned generation replaces its own protected file.
            if EPOCH.exists():protected_read(EPOCH,mode=0o640,gid=identities['approval'].pw_gid,limit=32)
            temp=EPOCH.with_name('epoch.'+epoch)
            fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
            try:
                os.fchown(fd,0,identities['approval'].pw_gid);os.fchmod(fd,0o640)
                os.write(fd,epoch.encode('ascii'));os.fsync(fd)
            finally:os.close(fd)
            os.replace(temp,EPOCH)
            codec=GrantCodec(policy,key,epoch)
            store=HelperLedger('/var/lib/linux-log-agent/helper',codec)
            bus=MessageBus(bus_type=BusType.SYSTEM)
            driver=SystemdJob(bus)
            await asyncio.wait_for(bus.connect(),5)
            service=HelperService(HelperRunner(store,lambda:driver))
            async def handle(channel):
                if service._task is not None and service._task.done() and not service._joining:
                    await service.finish_cleanup(.05)
                # Disable dispatch even for a valid signed grant unless root opted in.
                if not enabled:
                    async with channel:
                        request=await channel.receive()
                        if request.get('op')=='submit':
                            await channel.send(dict(version=1,ok=False,error_code='EXECUTION_DISABLED'));return
                        try:result=service._request(request,channel.peer.uid)
                        except ApprovalError as error:
                            await channel.send(dict(version=1,ok=False,error_code=error.code));return
                        await channel.send(dict(version=1,ok=True,result=result))
                else:await service.serve(channel)
            listeners.append(Listener(SOCKETS['helper'],identities['approval'].pw_gid,policy.coordinator_uid,handle))
        else:
            epoch=protected_read(EPOCH,mode=0o640,gid=identities['approval'].pw_gid,limit=32).decode('ascii')
            codec=GrantCodec(policy,key,epoch)
            store=CoordinatorState('/var/lib/linux-log-agent/approval',codec)
            endpoint=ApprovalEndpoint(ApprovalCoordinator(codec,store=store))
            handoff=HelperHandoff(endpoint,helper_uid=0)
            factory=lambda:connect(SOCKETS['helper'],0)
            await handoff.hello(await factory())
            service=ApprovalService(endpoint,handoff,write_enabled=enabled,helper_connect=factory)
            for name,uid,gid,port in [('proposal',policy.reader_uid,identities['reader'].pw_gid,Port.PROPOSAL),
                                      ('control',identities['operator'].pw_uid,identities['operator'].pw_gid,Port.CONTROL)]:
                async def handle(channel,port=port):await service.serve(channel,port=port)
                listeners.append(Listener(SOCKETS[name],gid,uid,handle))
        for listener in listeners:
            listener.start()
            listener.accept_task.add_done_callback(lambda _:stop.set())
        ready()
        await stop.wait()
        if any(item.accept_task.done() and not item.accept_task.cancelled()
               and item.accept_task.exception() is not None for item in listeners):
            raise ApprovalError('LISTENER_FAILED')
    finally:
        for listener in listeners:await listener.close()
        if endpoint is not None:endpoint.close()
        if kind=='helper':
            if service is not None:
                while not await service.shutdown(1):await asyncio.sleep(.05)
            if driver is not None:
                while not await driver.finish_cleanup(1):await asyncio.sleep(.05)
        if store is not None:store.close()


def main():
    import sys
    if len(sys.argv)!=2 or sys.argv[1] not in ('helper','approval'):
        print('INVALID_COMMAND',file=sys.stderr);return 2
    try:asyncio.run(run(sys.argv[1]));return 0
    except KeyboardInterrupt:return 130
    except Exception:print('CONTROL_START_FAILED',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
