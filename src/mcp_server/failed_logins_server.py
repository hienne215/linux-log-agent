"""P03-03 opt-in binding. Shared scaffold/runner are not changed."""
import asyncio
import hmac
import os
import sys
import time
from datetime import timedelta, timezone

from mcp.server.stdio import stdio_server

import mock_mcp_server as contracts
from .failed_login_sources import LinuxLoginSources
from .failed_logins_owner import FailedLoginsOwner, load_privacy
from .server import ServerScaffold, AdmissionStream, require_reader_identity

TOOL='failed_logins'


class KeyEpochPolicy:
    """Key/UID revocation latches until restart, never swaps a live snapshot's key.

    This is feature authorization, not detection of every journal/file ACL change.
    The dedicated stdio launcher remains responsible for trusted pipe ownership.
    """
    def __init__(self,uid,gid,scope,privacy):
        self.uid,self.gid,self.scope=uid,gid,scope
        self._epoch=privacy.digest('key-epoch-policy','failed_logins')
        self.revoked=False

    def __call__(self):
        if self.revoked:
            return False
        try:
            valid=(os.geteuid()==self.uid and self.uid!=0 and hmac.compare_digest(
                load_privacy(self.gid,self.scope).digest('key-epoch-policy','failed_logins'),self._epoch))
        except Exception:
            valid=False
        if not valid:
            self.revoked=True
        return valid


def create_live_server(session_id,scope,auth_year,offset_minutes):
    if not sys.platform.startswith('linux') or not -840<=offset_minutes<=840:
        raise ValueError('Linux reader configuration required')
    import pwd
    reader=pwd.getpwnam('mcp-log-reader')
    if os.geteuid()!=reader.pw_uid or reader.pw_uid==0:
        raise ValueError('Configured reader UID required')
    privacy=load_privacy(reader.pw_gid,scope)
    policy=KeyEpochPolicy(reader.pw_uid,reader.pw_gid,scope,privacy)
    provider=lambda p:LinuxLoginSources(p,auth_year,timezone(timedelta(minutes=offset_minutes)))
    owner=FailedLoginsOwner(reader.pw_uid,session_id,privacy,provider,access_check=policy)
    return FailedLoginsServer(owner,reader.pw_uid)


class FailedLoginsServer(ServerScaffold):
    """One owner and one tracked task per dedicated stdio connection.

    principal is trusted process/launcher context, never copied from request JSON.
    Acquisition is a bounded source operation in an owned background thread,
    not a new WorkerRunner process presumed to retain cursor state.
    """
    def __init__(self,owner,principal):
        super().__init__()
        self.owner,self.principal=owner,principal
        self.pending=None
        self.closing=False

    async def list_tools(self,ctx,params):
        result=await super().list_tools(ctx,params)
        for tool in result.tools:
            if tool.name==TOOL:
                tool.description='Opt-in read-only failed logins with connection-owned sanitized pagination.'
        return result

    async def call_tool(self,ctx,params):
        if params.name!=TOOL:
            return await super().call_tool(ctx,params)
        args=params.arguments if params.arguments is not None else {}
        request_id=ctx.request_id if ctx.request_id is not None else 0
        started,tick=contracts.current_time(),time.monotonic()
        if not contracts.TOOLS[TOOL][3].is_valid(args) or not contracts.valid_window(args):
            response=contracts.safe_error(TOOL,{},rule='validation')
        elif self.closing or self.pending is not None:
            response=contracts.safe_error(TOOL,{},'POLICY_DENIED',rule='owner_busy_or_closing')
        else:
            task=asyncio.create_task(asyncio.to_thread(self.owner.call,dict(args),self.principal))
            self.pending=task
            try:
                response=await asyncio.shield(task)
            except asyncio.CancelledError:
                self.closing=True
                raise
            except Exception:
                response=contracts.safe_error(TOOL,{},'EXECUTION_UNKNOWN',rule='failed_login_acquisition')
            finally:
                if task.done():
                    self.pending=None
        response.update(started_at=started,completed_at=contracts.current_time(),
                        duration_ms=max(0,int((time.monotonic()-tick)*1000)))
        if not contracts.TOOLS[TOOL][4].is_valid(response):
            response=contracts.safe_error(TOOL,{},'EXECUTION_UNKNOWN',rule='output_validation')
        result=self.native(response)
        if contracts.wire_size(result,request_id)>contracts.MAX_RESPONSE_BYTES:
            result=self.native(contracts.safe_error(TOOL,{},'OUTPUT_LIMIT',rule='native_frame'))
        if contracts.wire_size(result,request_id)>contracts.MAX_RESPONSE_BYTES:
            raise RuntimeError('Bounded failed-logins result failure')
        return result

    async def close(self):
        self.closing=True
        if self.pending is not None:
            task=self.pending
            try:
                await asyncio.shield(task)
            except Exception:
                pass
            if task.done():
                self.pending=None
        self.owner.close()
        while not await self.runner.close():
            await asyncio.sleep(0.05)
        return True

    async def serve(self):
        require_reader_identity()
        try:
            async with stdio_server() as (read,write):
                await self.sdk.run(AdmissionStream(read,self),write,self.sdk.create_initialization_options())
        finally:
            await self.close()
