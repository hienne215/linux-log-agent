"""Official-SDK scaffold over the provisional P00-03 contracts, not a collector."""
import asyncio
import json
import os
import time
from pathlib import Path

from anyio.abc import ObjectReceiveStream
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

import mock_mcp_server as contracts
from .runner import WorkerFailure, WorkerRunner


ROOT = Path(__file__).resolve().parents[2]


def require_reader_identity():
    # Identity is established by deployment, never by sudo/setuid in MCP.
    if os.name == "posix" and os.geteuid() == 0:
        raise RuntimeError("Refusing root: launch under the configured reader UID")


class ServerScaffold:
    def __init__(self, backends=None, runner=None):
        self.backends = dict(backends or {})
        if not set(self.backends) <= (set(contracts.TOOLS) - {"restart_service"}):
            raise ValueError("Only known read-only worker profiles may be registered")
        self.runner = runner or WorkerRunner()
        self.sdk = Server("linux-log-agent-scaffold-draft", version="0.1.0",
                          on_list_tools=self.list_tools, on_call_tool=self.call_tool)
        self.sdk.middleware.append(contracts.protocol_guard)

    def native(self, response):
        return CallToolResult(
            _meta={"io.modelcontextprotocol/serverInfo": self.sdk.server_info_stamp},
            result_type="complete", content=[TextContent(type="text", text=json.dumps(response, ensure_ascii=False))],
            structured_content=response, is_error=response["status"] == "error")

    async def list_tools(self, ctx, params):
        if params is not None and params.cursor is not None:
            raise MCPError(code=-32602, message="Invalid discovery cursor")
        return ListToolsResult(tools=[Tool(name=name,
            description="Draft scaffold: backend availability is application-owned; no real Linux collector enabled.",
            input_schema=record[1], output_schema=record[2]) for name, record in contracts.TOOLS.items()])

    async def call_tool(self, ctx, params):
        request_id = ctx.request_id if ctx.request_id is not None else 0
        if params.name not in contracts.TOOLS:
            raise MCPError(code=-32602, message="Unknown tool")
        _, schema, _, input_validator, output_validator = contracts.TOOLS[params.name]
        arguments = params.arguments if params.arguments is not None else {}
        error = next(input_validator.iter_errors(arguments), None)
        started = contracts.current_time()
        tick = time.monotonic()
        if error is not None:
            field = next(iter(error.absolute_path), "")
            response = contracts.safe_error(params.name, {}, field=field if field in schema["properties"] else "",
                                            rule=str(error.validator))
        elif params.name not in self.backends:
            response = contracts.safe_error(params.name, {}, "UNSUPPORTED", rule="backend_unavailable")
        else:
            try:
                raw = await self.runner.run(self.backends[params.name], arguments)
                response = json.loads(raw.decode("utf-8"))
                if not output_validator.is_valid(response):
                    raise WorkerFailure("EXECUTION_UNKNOWN")
                if response["status"] == "error":
                    # No child message, stderr, metadata or evidence is trusted for errors.
                    response = contracts.safe_error(params.name, {}, response["error_code"], rule="worker")
                elif any(response[key] != arguments[key] for key in
                         ("session_id", "trace_id", "logical_tool_call_id", "attempt_id")):
                    raise WorkerFailure("EXECUTION_UNKNOWN")
            except WorkerFailure as failure:
                response = contracts.safe_error(params.name, {}, failure.code, rule="worker")
                response["truncated"] = failure.code == "OUTPUT_LIMIT"
            except (ValueError, UnicodeError, TypeError, KeyError):
                response = contracts.safe_error(params.name, {}, "EXECUTION_UNKNOWN", rule="worker_output")
            except Exception:
                response = contracts.safe_error(params.name, {}, "EXECUTION_UNKNOWN", rule="worker_runtime")
        if (getattr(self.runner, "closing", False)
                and (getattr(self.runner, "process", None) is not None
                     or getattr(self.runner, "spawn", None) is not None)):
            response = contracts.safe_error(params.name, {}, "EXECUTION_UNKNOWN", rule="cleanup_pending")
        response.update(started_at=started, completed_at=contracts.current_time(),
                        duration_ms=int((time.monotonic() - tick) * 1000))
        if not output_validator.is_valid(response):
            response = contracts.safe_error(params.name, {}, "EXECUTION_UNKNOWN", rule="output_validation")
        response = contracts.bound_response(response, request_id, native_builder=self.native)
        if response["status"] == "error" and (response["session_id"] is not None or any(
                response[key] != "invalid-input" for key in ("trace_id", "logical_tool_call_id", "attempt_id"))):
            # A post-validation cap failure also must not echo potentially secret metadata.
            limited = response["error_code"] == "OUTPUT_LIMIT"
            response = contracts.safe_error(params.name, {}, response["error_code"], rule="result_failure")
            response.update(started_at=started, completed_at=contracts.current_time(),
                            duration_ms=int((time.monotonic() - tick) * 1000), truncated=limited)
            if limited:
                response["data"]["max_response_bytes"] = contracts.MAX_RESPONSE_BYTES
        if not output_validator.is_valid(response):
            response = contracts.safe_error(params.name, {}, "EXECUTION_UNKNOWN", rule="bounded_output")
        result = self.native(response)
        if contracts.wire_size(result, request_id) > contracts.MAX_RESPONSE_BYTES:
            raise RuntimeError("Internal bounded-result failure")
        return result

    async def serve(self):
        require_reader_identity()
        try:
            async with stdio_server() as (read, write):
                await self.sdk.run(AdmissionStream(read, self.runner), write,
                                   self.sdk.create_initialization_options())
        finally:
            # Retain ownership until the OS confirms cleanup; no hard wall SLA.
            while not await self.runner.close():
                await asyncio.sleep(0.05)


class AdmissionStream(ObjectReceiveStream):
    """Preserve P00-03 ID policy but reap owned workers before session exit 2."""
    def __init__(self, stream, runner):
        self.stream, self.runner = stream, runner

    @property
    def last_context(self):
        return getattr(self.stream, "last_context", None)

    async def receive(self):
        item = await self.stream.receive()
        if not isinstance(item, Exception):
            request_id = getattr(item.message, "id", None)
            if (request_id is not None and len(json.dumps(request_id, ensure_ascii=False).encode("utf-8"))
                    > contracts.MAX_JSONRPC_ID_BYTES):
                while not await self.runner.close():
                    await asyncio.sleep(0.05)
                try:
                    os.write(2, b"Transport policy: JSON-RPC ID exceeds 128 UTF-8 bytes; closing stdio.\n")
                finally:
                    os._exit(2)
        return item

    async def aclose(self):
        await self.stream.aclose()
