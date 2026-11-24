"""Opt-in lab read gateway. Canonical requests in, sanitized results out.

The unprivileged Agent/MCP never acquires journal/root access. A root-owned VM
service admits only these read profiles from the kernel-authenticated reader.
No commands, paths, units, URLs, approvals or restart operations are accepted.
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
import sys
from uuid import uuid4

from control_plane.transport import UnixMessageChannel,ChannelError

CONFIG=Path('/etc/linux-log-agent-read.json')
SOCKET=Path('/run/linux-log-agent-read/service.sock')
ALLOWED=frozenset({'query_journal','search_log','detect_anomaly','memory_oom_events','failed_logins','check_config'})
META=('schema_version','session_id','trace_id','logical_tool_call_id','attempt_id')
SAFE=frozenset({'PERMISSION_DENIED','POLICY_DENIED','UNSUPPORTED','OUTPUT_LIMIT','TIMEOUT','EXECUTION_UNKNOWN'})


class ReadError(Exception):
    def __init__(self,code):self.code=code;super().__init__(code)


def configured():
    """Missing config means local reader mode. Invalid opt-in never falls back."""
    from control_plane.approval import _unique
    try:
        fd=os.open(CONFIG,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return False
    try:
        parent=CONFIG.parent.lstat();info=os.fstat(fd)
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=0 or parent.st_mode&0o022
                or not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1
                or stat.S_IMODE(info.st_mode)!=0o644 or info.st_size>512):raise ValueError()
        value=json.loads(os.read(fd,513),object_pairs_hook=_unique)
        if (value!=dict(enabled=True,environment_id='linux-log-lab-hien')
                or type(value.get('enabled')) is not bool
                or socket.gethostname()!='linux-log-lab-hien'
                or Path('/sys/class/dmi/id/sys_vendor').read_text().strip()!='QEMU'):raise ValueError()
        return True
    except Exception:raise ReadError('POLICY_DENIED') from None
    finally:os.close(fd)


def enabled():
    # Root collector children do not proxy recursively. Server startup separately
    # requires the trusted opt-in and expected root identity.
    if os.geteuid()==0:return False
    active=configured()
    if active and os.geteuid()!=pwd.getpwnam('mcp-log-reader').pw_uid:
        raise ReadError('PERMISSION_DENIED')
    return active


def validated(tool,arguments,result):
    import mock_mcp_server as contracts
    from agent.llm.redaction import _clean
    from src.mcp_server.server import valid_evidence_links
    try:
        contracts.TOOLS[tool][4].validate(result)
        placeholder=(result['status']=='error' and result['session_id'] is None
            and all(result[k]=='invalid-input' for k in META[2:]) and not result['evidence'])
        if (result['tool']!=tool or result['status']=='approval_required'
                or (not placeholder and any(result[k]!=arguments[k] for k in META))
                or (result['status']!='error' and not valid_evidence_links(result))):raise ValueError()
        text=json.dumps(result,ensure_ascii=False,allow_nan=False)
        if len(text.encode())>65536 or _clean(text)!=text:raise ValueError()
        return result
    except Exception:raise ReadError('EXECUTION_UNKNOWN') from None


async def request(tool,arguments):
    """One authenticated exchange; no retry, reconnect or local fallback."""
    if tool not in ALLOWED:raise ReadError('POLICY_DENIED')
    connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);connection.setblocking(False)
    try:
        await asyncio.wait_for(asyncio.get_running_loop().sock_connect(connection,str(SOCKET)),1)
        async with UnixMessageChannel(connection,expected_uid=0,seconds=8,max_bytes=65536) as channel:
            await channel.send(dict(version=1,tool=tool,arguments=arguments))
            response=await channel.receive()
        if type(response) is not dict or set(response)!={'version','result'} or type(response['version']) is not int or response['version']!=1:
            raise ReadError('EXECUTION_UNKNOWN')
        return validated(tool,arguments,response['result'])
    except asyncio.CancelledError:raise
    except ReadError:raise
    except (ChannelError,TimeoutError) as error:
        code=getattr(error,'code','TIMEOUT')
        raise ReadError(code if code in SAFE else 'EXECUTION_UNKNOWN') from None
    except PermissionError:raise ReadError('PERMISSION_DENIED') from None
    except Exception:raise ReadError('EXECUTION_UNKNOWN') from None
    finally:connection.close()


async def journal_entries(arguments):
    """Fresh filtered entries for the existing health aggregator, not raw logs."""
    from agent.diagnostics.journal_policy import parse_stamp,safe_message
    args={key:arguments[key] for key in ('since','until')}
    args.update(schema_version='1.0.0',session_id=uuid4().hex,trace_id=uuid4().hex,
        logical_tool_call_id=uuid4().hex,attempt_id=uuid4().hex,unit='nginx.service',priority=3,limit=100)
    result=await request('query_journal',args)
    if result['status']=='error':raise ReadError(result['error_code'] if result['error_code'] in SAFE else 'EXECUTION_UNKNOWN')
    try:
        entries=result['data']['entries']
        if (result['status']!='partial' or result['truncated'] or result['next_cursor'] is not None
                or len(entries)!=result['data']['count'] or len(entries)>100
                or any(not safe_message(e['message']) or not 0<=e['priority']<=3
                    or not parse_stamp(args['since'])<=parse_stamp(e['timestamp'])<=parse_stamp(args['until']) for e in entries)):
            raise ValueError()
        return entries
    except Exception:raise ReadError('EXECUTION_UNKNOWN') from None


class ReadService:
    def __init__(self,runner):self.runner=runner

    async def dispatch(self,message):
        import mock_mcp_server as contracts
        from agent.diagnostic_worker import authorized
        if (type(message) is not dict or set(message)!={'version','tool','arguments'}
                or type(message['version']) is not int or message['version']!=1
                or type(message['tool']) is not str or message['tool'] not in ALLOWED):
            raise ReadError('POLICY_DENIED')
        tool,args=message['tool'],message['arguments']
        if (not contracts.TOOLS[tool][3].is_valid(args) or not authorized(tool,args)
                or len(json.dumps(args,ensure_ascii=True).encode())>16384):
            return contracts.safe_error(tool,{},'INVALID_INPUT',rule='gateway_scope')
        try:
            raw=await self.runner.run((sys.executable,'-I','-B',str(Path(__file__).with_name('read_gateway_worker.py')),tool),args)
            from control_plane.approval import _unique
            result=validated(tool,args,json.loads(raw,object_pairs_hook=_unique))
            # Include internal framing/wrapper, not just the canonical envelope.
            if len(json.dumps(dict(version=1,result=result),ensure_ascii=True,separators=(',',':')).encode())>65536:
                return contracts.safe_error(tool,{},'OUTPUT_LIMIT',rule='gateway_output')
            return result
        except asyncio.CancelledError:raise
        except Exception as error:
            code=getattr(error,'code','EXECUTION_UNKNOWN')
            return contracts.safe_error(tool,{},code if code in SAFE else 'EXECUTION_UNKNOWN',rule='gateway_collection')

    async def serve(self,channel):
        async with channel:
            message=await channel.receive()
            try:result=await self.dispatch(message)
            except ReadError:
                # Unknown tools have no canonical result schema; fixed generic
                # denial contains neither caller fields nor an invented envelope.
                await channel.send(dict(version=1,error_code='POLICY_DENIED'));return
            await channel.send(dict(version=1,result=result))


async def serve():
    if os.geteuid()!=0 or not configured():raise ReadError('POLICY_DENIED')
    from control_plane.runtime import Listener,ready
    from src.mcp_server.runner import WorkerRunner
    identity=pwd.getpwnam('mcp-log-reader')
    runner=WorkerRunner(timeout=6)
    service=ReadService(runner)
    listener=Listener(SOCKET,identity.pw_gid,identity.pw_uid,service.serve,max_bytes=65536)
    stop=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stop.set)
    logging.disable(logging.CRITICAL)
    loop.set_exception_handler(lambda *_:print('READ_GATEWAY_ASYNC_ERROR',flush=True))
    try:
        listener.start();listener.accept_task.add_done_callback(lambda _:stop.set());ready()
        await stop.wait()
        if listener.accept_task.done() and not listener.accept_task.cancelled() and listener.accept_task.exception():
            raise ReadError('EXECUTION_UNKNOWN')
    finally:
        await listener.close()
        while not await runner.close():await asyncio.sleep(.05)


def main():
    if len(sys.argv)!=1:return 2
    try:asyncio.run(serve());return 0
    except KeyboardInterrupt:return 130
    except Exception:print('READ_GATEWAY_START_FAILED',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
