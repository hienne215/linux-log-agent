"""Single-dispatch bridge. No LLM, SDK, approval, schema discovery or retries.

The application supplies trusted business schemas and a reviewed redactor. This
module deliberately does not invent SCRUM-12 envelopes or attach correlation IDs.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry
from referencing.exceptions import NoSuchResource

from agent.mcp_client import MCPClient, MCPClientError, ToolResult


class BoundaryError(Exception):
    """Fixed code/layer only; peer payloads and underlying errors are suppressed."""
    def __init__(self, code: str, layer: str = "policy", *,
                 phase: str = "not_dispatched", execution_state: str = "not_dispatched",
                 safe_result: ToolMessage | None = None):
        self.code, self.layer = code, layer
        self.phase, self.execution_state = phase, execution_state
        self.safe_result = safe_result
        super().__init__(code)


def _json(value: Any, limit: int, code: str) -> str:
    def check(item, depth=0):
        if depth > 32:
            raise ValueError
        if type(item) is dict:
            if any(type(k) is not str for k in item):
                raise ValueError
            for child in item.values():
                check(child, depth + 1)
        elif type(item) is list:
            for child in item:
                check(child, depth + 1)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError
    try:
        check(value)
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(text.encode("utf-8")) > limit:
            raise ValueError
        return text
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise BoundaryError(code) from None


def _local_refs(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("$ref", "$dynamicRef") and (not isinstance(child, str) or not child.startswith("#")):
                raise BoundaryError("INVALID_SCHEMA")
            _local_refs(child)
    elif isinstance(value, list):
        for child in value:
            _local_refs(child)


def _no_retrieve(uri):
    # Defense in depth: even malformed trusted refs cannot fetch network/files.
    raise NoSuchResource(ref=uri)


@dataclass(frozen=True)
class TrustedTool:
    name: str
    description: str
    parameters: dict[str, Any] = field(repr=False)
    read_only: bool


@dataclass(frozen=True)
class BridgeBudget:
    max_calls: int = 4
    total_seconds: float = 30.0
    request_seconds: float = 10.0
    cleanup_seconds: float = 8.0
    max_bytes: int = 64 * 1024

    def __post_init__(self):
        if type(self.max_calls) is not int or not 1 <= self.max_calls <= 8:
            raise BoundaryError("INVALID_CONFIG")
        if type(self.max_bytes) is not int or not 1024 <= self.max_bytes <= 65536:
            raise BoundaryError("INVALID_CONFIG")
        for value, maximum in ((self.total_seconds, 120), (self.request_seconds, 60),
                               (self.cleanup_seconds, 60)):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
                raise BoundaryError("INVALID_CONFIG")


@dataclass(frozen=True)
class ToolMessage:
    tool_name: str
    content: str = field(repr=False)
    is_error: bool
    budget_exhausted: bool = False

    def message_fields(self) -> dict[str, Any]:
        """Fields for SCRUM-23 Message(**fields); never a system instruction."""
        if self.budget_exhausted:
            raise BoundaryError("BUDGET_EXHAUSTED", phase="sanitized",
                                execution_state="response_received", safe_result=self)
        return {"role": "tool", "tool_name": self.tool_name, "content": self.content}


class ToolBridge:
    """One already-selected proposal at a time; caller owns session lifecycle.

    Budget starts at construction, counts failed dispatches, and never resets.
    Read-only is trusted application policy, not an MCP/LLM annotation. Discovery
    is still independently required by StdioMCPClient. Redaction is mandatory.
    """
    def __init__(self, client: MCPClient, tools: tuple[TrustedTool, ...], *,
                 redact: Callable[[str], str], budget: BridgeBudget | None = None):
        if not callable(redact) or not isinstance(tools, tuple) or not tools or len(tools) > 64:
            raise BoundaryError("INVALID_CONFIG")
        self._budget = budget or BridgeBudget()
        if not isinstance(self._budget, BridgeBudget):
            raise BoundaryError("INVALID_CONFIG")
        self._client, self._redact = client, redact
        self._tools: dict[str, TrustedTool] = {}
        self._validators = {}
        for tool in tools:
            if (not isinstance(tool, TrustedTool) or not isinstance(tool.name, str)
                    or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", tool.name)
                    or tool.name in self._tools or not isinstance(tool.description, str)
                    or type(tool.read_only) is not bool or not isinstance(tool.parameters, dict)):
                raise BoundaryError("INVALID_SCHEMA")
            schema = json.loads(_json(tool.parameters, self._budget.max_bytes, "INVALID_SCHEMA"))
            if (schema.get("type") != "object" or schema.get("$schema", "https://json-schema.org/draft/2020-12/schema")
                    != "https://json-schema.org/draft/2020-12/schema"):
                raise BoundaryError("INVALID_SCHEMA")
            _local_refs(schema)
            try:
                Draft202012Validator.check_schema(schema)
                validator = Draft202012Validator(schema, format_checker=FormatChecker(),
                                                registry=Registry(retrieve=_no_retrieve))
            except Exception:
                raise BoundaryError("INVALID_SCHEMA") from None
            self._tools[tool.name] = TrustedTool(tool.name, tool.description, schema, tool.read_only)
            self._validators[tool.name] = validator
        self._deadline = time.monotonic() + self._budget.total_seconds
        self._calls = 0
        self._busy = False
        self._operation: asyncio.Task | None = None
        self._cleanup_state = "idle"
        self._phase = "not_dispatched"
        self._execution_state = "not_dispatched"
        self._safe_result: ToolMessage | None = None
        self._stopped = False

    @property
    def calls_used(self) -> int:
        return self._calls

    @property
    def cleanup_state(self) -> str:
        """idle / pending / confirmed / unconfirmed; never inferred from a timer."""
        return self._cleanup_state

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def execution_state(self) -> str:
        return self._execution_state

    @property
    def safe_result(self) -> ToolMessage | None:
        return self._safe_result

    @property
    def has_pending_task(self) -> bool:
        return self._operation is not None and not self._operation.done()

    @staticmethod
    def _client_error(error: Exception) -> BoundaryError:
        known = {"TIMEOUT", "DISCONNECTED", "TRANSPORT_ERROR", "PROTOCOL_ERROR",
                 "MALFORMED_RESULT", "RESULT_TOO_LARGE", "CLEANUP_TIMEOUT",
                 "NOT_CONNECTED", "BUSY", "TOOL_NOT_ALLOWED", "UNSUPPORTED_RESULT"}
        code = (error.code if isinstance(error, MCPClientError) and error.code in known
                else "TIMEOUT" if isinstance(error, TimeoutError) else "TRANSPORT_ERROR")
        layer = "protocol" if code in {"PROTOCOL_ERROR", "MALFORMED_RESULT", "UNSUPPORTED_RESULT"} else "transport"
        return BoundaryError(code, layer)

    async def _disconnect(self):
        # MCP Client's existing owner task still owns every SDK/AnyIO scope.
        self._cleanup_state = "pending"
        try:
            await self._client.disconnect()
        except Exception as error:
            self._cleanup_state = "unconfirmed"
            code = "CLEANUP_TIMEOUT" if isinstance(error, MCPClientError) and error.code == "CLEANUP_TIMEOUT" else "CLEANUP_FAILED"
            return BoundaryError(code, "transport")
        self._cleanup_state = "confirmed"
        return None

    async def _request(self, name, arguments):
        try:
            return await self._client.call_tool(name, arguments)
        except (Exception, asyncio.CancelledError) as error:
            failure = (BoundaryError("CANCELLED", "transport") if isinstance(error, asyncio.CancelledError)
                       else self._client_error(error))
            # A cancellation may interrupt Client's *wait* for its owner; join
            # that same owner again. No second owner cancellation or SDK enter.
            cleanup_error = await self._disconnect()
            return cleanup_error or failure

    async def _wait_cleanup(self):
        """Bounded join; repeated caller cancellation never cancels owned work."""
        deadline = time.monotonic() + self._budget.cleanup_seconds
        cancelled = False
        while not self._operation.done():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._cleanup_state = "pending"
                raise BoundaryError("CLEANUP_TIMEOUT", "transport")
            try:
                await asyncio.wait({self._operation}, timeout=remaining)
            except asyncio.CancelledError:
                cancelled = True
        result = (BoundaryError("CANCELLED", "transport") if self._operation.cancelled()
                  else self._operation.result())
        self._operation = None
        if self._cleanup_state == "idle":
            # Cancellation before worker startup (or a client suppressing it)
            # still needs explicit session teardown; preserve the same budget.
            self._operation = asyncio.create_task(self._disconnect(), name="tool-bridge-cleanup")
            while not self._operation.done():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._cleanup_state = "pending"
                    raise BoundaryError("CLEANUP_TIMEOUT", "transport")
                try:
                    await asyncio.wait({self._operation}, timeout=remaining)
                except asyncio.CancelledError:
                    cancelled = True
            cleanup_error = self._operation.result()
            self._operation = None
            if cleanup_error is not None:
                raise cleanup_error
        if self._cleanup_state == "unconfirmed":
            raise result
        return result, cancelled

    async def finish_cleanup(self) -> None:
        """Explicitly rejoin pending teardown, never reconnect or resend a call.

        On CLEANUP_TIMEOUT the bridge/client retain their task references. The
        caller must keep this bridge and call finish_cleanup again; calls remain
        blocked until that join. Each join has its own bounded cleanup budget.
        """
        if self._busy:
            raise BoundaryError("BUSY")
        if self._operation is None and self._cleanup_state not in {"pending", "unconfirmed"}:
            return
        self._busy = True
        if self._operation is None:
            self._operation = asyncio.create_task(self._disconnect(), name="tool-bridge-cleanup")
        try:
            _, cancelled = await self._wait_cleanup()
            if cancelled:
                raise asyncio.CancelledError
        except BoundaryError as error:
            error.phase, error.execution_state = self._phase, self._execution_state
            raise
        finally:
            self._busy = False

    def definitions(self) -> tuple[TrustedTool, ...]:
        """Detached read-only business definitions; not wire metadata schemas."""
        return tuple(TrustedTool(t.name, t.description, json.loads(_json(t.parameters,
                     self._budget.max_bytes, "INVALID_SCHEMA")), True)
                     for t in self._tools.values() if t.read_only)

    def _sanitize(self, value: dict[str, Any]) -> dict[str, Any]:
        text = _json(value, self._budget.max_bytes, "RESULT_TOO_LARGE")
        try:
            clean = self._redact(text)
            if not isinstance(clean, str) or len(clean.encode("utf-8")) > self._budget.max_bytes:
                raise ValueError
            # Reject duplicate keys, NaN, or redactors changing the root shape.
            def pairs(items):
                result = {}
                for key, item in items:
                    if key in result:
                        raise ValueError
                    result[key] = item
                return result
            result = json.loads(clean, object_pairs_hook=pairs,
                                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if type(result) is not dict:
                raise ValueError
            _json(result, self._budget.max_bytes, "DATA_POLICY")
            return result
        except Exception:
            raise BoundaryError("DATA_POLICY") from None

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolMessage:
        """Dispatch once. Tool isError remains a result, not a transport error."""
        if self._busy:
            raise BoundaryError("BUSY")
        if self._operation is not None or self._cleanup_state in {"pending", "unconfirmed"}:
            raise BoundaryError("CLEANUP_PENDING", "transport", phase=self._phase,
                                execution_state=self._execution_state)
        self._phase, self._execution_state = "not_dispatched", "not_dispatched"
        if not isinstance(name, str) or name not in self._tools:
            raise BoundaryError("UNKNOWN_TOOL")
        if not self._tools[name].read_only:
            raise BoundaryError("WRITE_NOT_ALLOWED")
        if type(arguments) is not dict:
            raise BoundaryError("INVALID_ARGUMENTS")
        arguments = json.loads(_json(arguments, self._budget.max_bytes, "INVALID_ARGUMENTS"))
        try:
            errors = self._validators[name].iter_errors(arguments)
            if next(errors, None) is not None:
                raise BoundaryError("INVALID_ARGUMENTS")
        except BoundaryError:
            raise
        except Exception:
            raise BoundaryError("INVALID_SCHEMA") from None
        if self._sanitize(arguments) != arguments:
            # Arguments are never silently changed by credential filtering.
            raise BoundaryError("DATA_POLICY")
        remaining = self._deadline - time.monotonic()
        if self._stopped or remaining <= 0 or self._calls >= self._budget.max_calls:
            raise BoundaryError("BUDGET_EXHAUSTED")
        self._calls += 1
        self._safe_result = None
        self._busy = True
        self._phase, self._execution_state = "awaiting_result", "unknown"
        self._cleanup_state = "idle"
        self._operation = asyncio.create_task(self._request(name, arguments), name="tool-bridge-request")
        expired = cancelled = False
        try:
            try:
                done, _ = await asyncio.wait({self._operation}, timeout=min(remaining, self._budget.request_seconds))
                expired = not done
            except asyncio.CancelledError:
                cancelled = True
            if expired or cancelled:
                self._stopped = True
                # If the worker is already cleaning up, do not interrupt it.
                # Otherwise cancel its request once; its handler rejoins owner.
                if (not self._operation.done() and self._cleanup_state == "idle"
                        and not self._operation.cancelling()):
                    self._operation.cancel()
                result, later_cancel = await self._wait_cleanup()
                if cancelled or later_cancel:
                    raise asyncio.CancelledError
                if isinstance(result, BoundaryError):
                    if result.code == "CANCELLED":
                        raise BoundaryError("TIMEOUT", "transport")
                    raise result
            else:
                result = self._operation.result()
                self._operation = None
            if isinstance(result, BoundaryError):
                raise result
            self._phase, self._execution_state = "result_received", "response_received"
            if not isinstance(result, ToolResult) or type(result.is_error) is not bool:
                raise BoundaryError("MALFORMED_RESULT", "protocol")
            if (not isinstance(result.content, tuple)
                    or any(not isinstance(b, dict) or b.get("type") != "text"
                           or not isinstance(b.get("text"), str) for b in result.content)
                    or (result.structured_content is not None and type(result.structured_content) is not dict)):
                # No automatic image/resource fetching or interpretation.
                raise BoundaryError("UNSUPPORTED_RESULT", "protocol")
            data = self._sanitize({"isError": result.is_error, "content": list(result.content),
                                   "structuredContent": result.structured_content})
            if (set(data) != {"isError", "content", "structuredContent"}
                    or data.get("isError") is not result.is_error
                    or type(data["content"]) is not list
                    or any(type(b) is not dict or b.get("type") != "text"
                           or type(b.get("text")) is not str for b in data["content"])
                    or (data["structuredContent"] is not None
                        and type(data["structuredContent"]) is not dict)):
                raise BoundaryError("DATA_POLICY")
            content = _json(data, self._budget.max_bytes, "RESULT_TOO_LARGE")
            exhausted = expired or time.monotonic() >= self._deadline
            self._phase = "sanitized"
            self._stopped |= exhausted
            self._safe_result = ToolMessage(name, content, result.is_error, budget_exhausted=exhausted)
            return self._safe_result
        except BoundaryError as error:
            error.phase, error.execution_state = self._phase, self._execution_state
            error.safe_result = self._safe_result
            raise
        finally:
            self._busy = False
