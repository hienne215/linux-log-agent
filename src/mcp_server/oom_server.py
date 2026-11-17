"""Opt-in P03-02 binding; shared scaffold/runner remain unchanged.

One pager and one tracked acquisition task per dedicated stdio connection.
Collection runs in an owned thread using oom_events' bounded direct children.
Cancellation closes the owner and retains the task until acquisition finishes.
"""
import asyncio
import os
import time

from mcp.server.stdio import stdio_server

import mock_mcp_server as contracts
from . import oom_events
from .oom_pagination import OomPaginator, TOOL
from .server import ServerScaffold, AdmissionStream, require_reader_identity


def reader_policy():
    if os.name != 'posix':
        return True  # Windows returns UNSUPPORTED; it cannot acquire Linux data.
    try:
        import pwd
        return os.geteuid() == pwd.getpwnam('mcp-log-reader').pw_uid and os.geteuid() != 0
    except (KeyError,OSError):
        return False


class OomServer(ServerScaffold):
    def __init__(self, session_id, backends=None, acquire=None, access_check=None):
        if backends and TOOL in backends:
            raise ValueError('OOM must use its persistent owner, not a per-call worker')
        super().__init__(backends=backends)
        source = acquire or (lambda req: oom_events.collect(req,snapshot=True))
        self.pager=OomPaginator(source,session_id,access_check or reader_policy,native_builder=self.native)
        self.pending=None
        self.closing=False

    async def list_tools(self,ctx,params):
        result=await super().list_tools(ctx,params)
        for tool in result.tools:
            if tool.name==TOOL:
                tool.description='Opt-in read-only OOM collector with connection-owned snapshot pagination.'
        return result

    async def call_tool(self,ctx,params):
        if params.name != TOOL:
            return await super().call_tool(ctx,params)
        started,tick=contracts.current_time(),time.monotonic()
        args=params.arguments if params.arguments is not None else {}
        request_id=ctx.request_id if ctx.request_id is not None else 0
        if not contracts.TOOLS[TOOL][3].is_valid(args) or not contracts.valid_window(args):
            response=contracts.safe_error(TOOL,{},rule='validation')
        elif self.closing or self.pending is not None:
            response=contracts.safe_error(TOOL,{},'POLICY_DENIED',rule='owner_busy_or_closing')
        else:
            task=asyncio.create_task(asyncio.to_thread(self.pager.call,dict(args),request_id))
            self.pending=task
            try:
                response=await asyncio.shield(task)
            except asyncio.CancelledError:
                self.closing=True
                raise
            except Exception:
                response=contracts.safe_error(TOOL,{},'EXECUTION_UNKNOWN',rule='snapshot_acquisition')
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
            raise RuntimeError('Bounded OOM result failure')
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
        self.pager.close()
        while not await self.runner.close():
            await asyncio.sleep(0.05)
        return True

    async def serve(self):
        require_reader_identity()
        try:
            async with stdio_server() as (read,write):
                # Oversized transport IDs must close both the owner and runner.
                await self.sdk.run(AdmissionStream(read,self),write,self.sdk.create_initialization_options())
        finally:
            await self.close()
