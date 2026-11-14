from datetime import datetime, timezone, timedelta
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
import asyncio
import os
from anyio.abc import ObjectReceiveStream
import copy
import time
from mcp.types import Tool, ListToolsResult, JSONRPCResponse
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, TextContent
from typing import Literal
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
import json
import secrets


TOOLS = {}

MAX_RESPONSE_BYTES = 64 * 1024
MAX_JSONRPC_ID_BYTES = 128
SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


# Cursor configuration for mock pagination.
CURSOR_TTL_SECONDS = 300
MAX_CURSOR_STATES = 256
MAX_PAGE_OFFSET = 200
ANOMALY_EVENT_AT = "2026-11-02T00:00:00Z"

# Store cursor state in memory.
CURSOR_STORE: dict[str, dict] = {}


def current_time() -> str:
    """Return current UTC time in RFC3339 format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalized_filters(filters):
    normalized = dict(filters)
    for field in ("since", "until"):
        if isinstance(normalized.get(field), str):
            normalized[field] = datetime.fromisoformat(normalized[field].replace(
                "Z", "+00:00")).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return normalized


def create_cursor(
    tool: str,
    session_id: str,
    filters: dict,
    offset: int,
) -> str:
    """Create a session-bound, filter-bound pagination cursor."""
    now = datetime.now(timezone.utc)
    for old in list(CURSOR_STORE):
        if CURSOR_STORE[old]["expires_at"] <= now:
            CURSOR_STORE.pop(old)
    if len(CURSOR_STORE) >= MAX_CURSOR_STATES or not 0 < offset <= MAX_PAGE_OFFSET:
        raise ValueError("Cursor budget exhausted")
    token = secrets.token_urlsafe(32)

    CURSOR_STORE[token] = {
        "tool": tool,
        "session_id": session_id,
        "filters": normalized_filters(filters),
        "offset": offset,
        "expires_at": datetime.now(timezone.utc)
        + timedelta(seconds=CURSOR_TTL_SECONDS),
    }

    return token


def validate_cursor(
    cursor: str,
    tool: str,
    session_id: str,
    filters: dict,
) -> tuple[bool, int | str]:
    """Validate cursor ownership, filters and expiration."""
    state = CURSOR_STORE.get(cursor)

    if state is None:
        return False, "INVALID_INPUT"

    if datetime.now(timezone.utc) >= state["expires_at"]:
        CURSOR_STORE.pop(cursor, None)
        return False, "EXPIRED"

    if state["tool"] != tool:
        return False, "INVALID_INPUT"

    if state["session_id"] != session_id:
        return False, "INVALID_INPUT"

    if state["filters"] != normalized_filters(filters):
        return False, "INVALID_INPUT"

    if not 0 < state["offset"] <= MAX_PAGE_OFFSET:
        return False, "OUTPUT_LIMIT"
    return True, state["offset"]


def within_window(records, since, until):
    start = datetime.fromisoformat(since.replace("Z", "+00:00"))
    end = datetime.fromisoformat(until.replace("Z", "+00:00"))
    return [record for record in records if start <= datetime.fromisoformat(
        record["timestamp"].replace("Z", "+00:00")) < end]


def aggregate_verify_results(checks: list[dict]) -> str:
    """
    Aggregate verify_fix results using this precedence:
    fail > unknown > pass. An empty list returns unknown.
    """
    if not checks:
        return "unknown"

    results = {check.get("result") for check in checks}

    if "fail" in results:
        return "fail"
    if "unknown" in results:
        return "unknown"
    return "pass"


def make_response(
    tool: str,
    trace_id: str,
    logical_tool_call_id: str,
    attempt_id: str,
    data: dict,
    session_id: str | None,
    status: str = "ok",
    error_code: str | None = None,
    evidence: list | None = None,
    truncated: bool = False,
    next_cursor: str | None = None,
) -> dict:
    """
    Create a standard MCP response envelope.
    Enforce a maximum serialized response size of 64 KiB.

    session_id is required as a field and may be null.
    """
    response = {
        "schema_version": "1.0.0",
        "session_id": session_id,
        "trace_id": trace_id,
        "logical_tool_call_id": logical_tool_call_id,
        "attempt_id": attempt_id,
        "tool": tool,
        "status": status,
        "started_at": current_time(),
        "completed_at": current_time(),
        "duration_ms": 0,
        "data": data,
        "evidence": evidence or [],
        "error_code": error_code,
        "truncated": truncated,
        "next_cursor": next_cursor,
    }

    return bound_response(response)


def bound_response(response, request_id=0, native_builder=None):
    """Keep whole records when useful data fits; otherwise return an atomic error."""
    native_builder = native_builder or native_result
    if wire_size(native_builder(response), request_id) <= MAX_RESPONSE_BYTES:
        return response
    candidate = copy.deepcopy(response)
    for key in ("entries", "matches", "failed_units", "events", "ports"):
        records = candidate["data"].get(key)
        if response["status"] not in {"ok", "partial"} or not isinstance(records, list):
            continue
        while len(records) > 1:
            records.pop()
            candidate["data"]["count"] = len(records)
            candidate.update(status="partial", error_code="OUTPUT_LIMIT",
                             truncated=True, next_cursor=None)
            if wire_size(native_builder(candidate), request_id) <= MAX_RESPONSE_BYTES:
                return candidate
    # Do not split evidence, individual records, JSON strings, or UTF-8 bytes.
    response.update(status="error", error_code="OUTPUT_LIMIT", truncated=True,
                    data={"message": "Response exceeded the maximum size limit.",
                          "max_response_bytes": MAX_RESPONSE_BYTES},
                    evidence=[], next_cursor=None)
    return response


def native_result(response):
    return CallToolResult(
        _meta={"io.modelcontextprotocol/serverInfo": mcp.server_info_stamp},
        result_type="complete",
        content=[TextContent(type="text", text=json.dumps(response, ensure_ascii=False))],
        structured_content=response, is_error=response["status"] == "error")


def wire_size(result, request_id=0):
    # Same model serialization as SDK runner and stdio stdout_writer; include newline.
    frame = JSONRPCResponse(jsonrpc="2.0", id=request_id,
        result=result.model_dump(by_alias=True, mode="json", exclude_none=True))
    return len(frame.model_dump_json(by_alias=True, exclude_unset=True).encode("utf-8")) + 1


def register_tool():
    """Record the raw callable; the SDK never derives a second signature schema."""
    def decorator(fn):
        name = fn.__name__
        schemas = [json.loads((SCHEMA_DIR / "tools" / f"{name}{suffix}").read_text(encoding="utf-8"))
                   for suffix in (".schema.json", ".result.schema.json")]
        for schema in schemas:
            Draft202012Validator.check_schema(schema)
        TOOLS[name] = (fn, *schemas, *[Draft202012Validator(schema,
            format_checker=FormatChecker()) for schema in schemas])
        return fn
    return decorator


def safe_error(name, arguments, code="INVALID_INPUT", field="", rule="validation"):
    # Never reflect invalid input (including attacker-chosen property names or IDs).
    # Valid metadata is echoed only after the complete request passes validation.
    return make_response(name, "invalid-input", "invalid-input", "invalid-input",
        {"message": "Request rejected.", "field": field, "rule": rule},
        None, status="error", error_code=code)


async def list_tools(ctx, params):
    if params is not None and params.cursor is not None:
        raise MCPError(code=-32602, message="Invalid discovery cursor")
    return ListToolsResult(tools=[Tool(name=name, description=fn.__doc__,
        input_schema=input_schema, output_schema=output_schema)
        for name, (fn, input_schema, output_schema, _, _) in TOOLS.items()])


async def call_tool(ctx, params):
    if params.name not in TOOLS:
        raise MCPError(code=-32602, message="Unknown tool")
    fn, input_schema, _, input_validator, output_validator = TOOLS[params.name]
    arguments = params.arguments if params.arguments is not None else {}
    error = next(input_validator.iter_errors(arguments), None)
    if error is not None:
        # A path segment is safe only if it is a schema-declared top-level identifier.
        field = next(iter(error.absolute_path), "")
        if field not in input_schema["properties"]:
            field = ""
        response = safe_error(params.name, arguments, field=field,
                              rule=str(error.validator))
    else:
        started = current_time()
        tick = time.monotonic()
        try:
            response = fn(**arguments)
        except PermissionError:
            response = safe_error(params.name, {}, "PERMISSION_DENIED", rule="permission")
        except Exception:
            # No exception text, input repr, or traceback crosses the trust boundary.
            response = safe_error(params.name, {}, "EXECUTION_UNKNOWN", rule="execution")
        # Schema-valid input can still fail semantic checks (window/cursor).
        # All rejected input crosses the same privacy boundary before serialization.
        if (response.get("status") == "error"
                and response.get("error_code") in {"INVALID_INPUT", "EXPIRED"}):
            response = safe_error(params.name, {}, response["error_code"], rule="semantic")
        response["started_at"] = started
        response["completed_at"] = current_time()
        response["duration_ms"] = int((time.monotonic() - tick) * 1000)
    if not output_validator.is_valid(response):
        response = safe_error(params.name, {}, "EXECUTION_UNKNOWN", rule="output_validation")
    response = bound_response(response, ctx.request_id)
    result = native_result(response)
    if not output_validator.is_valid(response):
        raise RuntimeError("Internal result contract failure")
    return result


async def protocol_guard(ctx, call_next):
    # Reject malformed tools/call parameters before SDK model errors can quote them.
    if ctx.method == "tools/call":
        params = ctx.params
        if (not isinstance(params, dict) or not isinstance(params.get("name"), str)
                or (params.get("arguments") is not None
                    and not isinstance(params.get("arguments"), dict))):
            raise MCPError(code=-32602, message="Invalid tool call parameters")
    return await call_next(ctx)


mcp = Server("linux-log-agent-mock", on_list_tools=list_tools, on_call_tool=call_tool)
mcp.middleware.append(protocol_guard)


class IDBoundedReadStream(ObjectReceiveStream):
    """Admission guard around SDK-decoded messages; SDK still owns JSON-RPC/stdio."""
    def __init__(self, stream):
        self.stream = stream

    @property
    def last_context(self):
        return getattr(self.stream, "last_context", None)

    async def receive(self):
        item = await self.stream.receive()
        if not isinstance(item, Exception):
            request_id = getattr(item.message, "id", None)
            if (request_id is not None and len(json.dumps(request_id,
                    ensure_ascii=False).encode("utf-8")) > MAX_JSONRPC_ID_BYTES):
                # An error reply would echo the forbidden ID and may exceed the cap.
                # Fail closed for this mock process; do not truncate, log or dispatch it.
                try:
                    os.write(2, b"Transport policy: JSON-RPC ID exceeds 128 UTF-8 bytes; closing stdio.\n")
                finally:
                    os._exit(2)
        return item

    async def aclose(self):
        await self.stream.aclose()


async def run():
    async with stdio_server() as (read, write):
        await mcp.run(IDBoundedReadStream(read), write, mcp.create_initialization_options())

@register_tool()
def query_journal(
    schema_version: str,
    session_id: str,
    trace_id: str,
    logical_tool_call_id: str,
    attempt_id: str,
    unit: str,
    since: str,
    until: str,
    priority: int | None = None,
    limit: int = 100,
    cursor: str | None = None,
) -> dict:
    """Return mock journal entries with cursor pagination."""
    filters = {
        "unit": unit,
        "since": since,
        "until": until,
        "priority": priority,
        "limit": limit,
    }

    all_entries = [
        {
            "timestamp": "2026-11-02T09:30:00Z",
            "priority": 3,
            "message": "Mock service started successfully.",
        },
        {
            "timestamp": "2026-11-02T09:35:00Z",
            "priority": 4,
            "message": "Mock service received a request.",
        },
        {
            "timestamp": "2026-11-02T09:40:00Z",
            "priority": 3,
            "message": "Mock service completed a task.",
        },
    ]

    all_entries = within_window(all_entries, since, until)
    if priority is not None:
        all_entries = [
            entry for entry in all_entries
            if entry["priority"] == priority
        ]

    offset = 0

    if cursor is not None:
        valid, result = validate_cursor(
            cursor=cursor,
            tool="query_journal",
            session_id=session_id,
            filters=filters,
        )

        if not valid:
            return make_response(
                tool="query_journal",
                trace_id=trace_id,
                logical_tool_call_id=logical_tool_call_id,
                attempt_id=attempt_id,
                session_id=session_id,
                data={"message": "Invalid or expired pagination cursor."},
                status="error",
                error_code=result,
            )

        offset = result

    page_entries = all_entries[offset:offset + limit]
    next_offset = offset + len(page_entries)
    next_cursor = None

    if next_offset < len(all_entries):
        next_cursor = create_cursor(
            tool="query_journal",
            session_id=session_id,
            filters=filters,
            offset=next_offset,
        )

    return make_response(
        tool="query_journal",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
        session_id=session_id,
        data={
            "entries": page_entries,
            "count": len(page_entries),
            "limit": limit,
        },
        status="ok",
        next_cursor=next_cursor,
    )


@register_tool()
def search_log(
    schema_version: str,
    session_id: str,
    trace_id: str,
    logical_tool_call_id: str,
    attempt_id: str,
    source_id: str,
    query: str,
    since: str,
    until: str,
    limit: int = 100,
    cursor: str | None = None,
) -> dict:
    """Search mock log data using a text query."""
    filters = {
        "source_id": source_id,
        "query": query,
        "since": since,
        "until": until,
        "limit": limit,
    }

    all_matches = [
        {
            "timestamp": "2026-11-02T09:30:00Z",
            "message": f"Mock log entry matching: {query}",
        },
        {
            "timestamp": "2026-11-02T09:35:00Z",
            "message": f"Another mock log entry matching: {query}",
        },
        {
            "timestamp": "2026-11-02T09:40:00Z",
            "message": f"Additional mock log entry matching: {query}",
        },
    ]

    offset = 0

    if cursor is not None:
        valid, result = validate_cursor(
            cursor,
            tool="search_log",
            session_id=session_id,
            filters=filters,
        )

        if not valid:
            return make_response(
                tool="search_log",
                trace_id=trace_id,
                logical_tool_call_id=logical_tool_call_id,
                attempt_id=attempt_id,
                session_id=session_id,
                data={"message": "Invalid or expired pagination cursor."},
                status="error",
                error_code=result,
            )

        offset = result

    all_matches = within_window(all_matches, since, until)
    page_matches = all_matches[offset:offset + limit]
    next_offset = offset + len(page_matches)
    next_cursor = None

    if next_offset < len(all_matches):
        next_cursor = create_cursor(
            tool="search_log",
            session_id=session_id,
            filters=filters,
            offset=next_offset,
        )

    return make_response(
        tool="search_log",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
        session_id=session_id,
        data={
            "source_id": source_id,
            "query": query,
            "matches": page_matches,
            "count": len(page_matches),
            "limit": limit,
        },
        status="ok",
        next_cursor=next_cursor,
    )


@register_tool()
def service_status(
    schema_version: str,
    session_id: str,
    trace_id: str,
    logical_tool_call_id: str,
    attempt_id: str,
    unit: str,
) -> dict:
    """Return mock status information for a systemd service."""
    return make_response(
        tool="service_status",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
        session_id=session_id,
        evidence=[{
            "evidence_id": "mock-status-" + secrets.token_hex(12),
            "observed_at": current_time(),
            "source": "synthetic.mock.service_status", "unit": unit,
            "excerpt": "Synthetic mock: ActiveState=active; SubState=running; LoadState=loaded",
            "redacted": False, "truncated": False,
        }],
        data={
            "unit": unit,
            "active_state": "active",
            "sub_state": "running",
            "load_state": "loaded",
            "description": "Mock service",
        },
    )


@register_tool()
def list_failed_units(
    schema_version: str,
    session_id: str,
    trace_id: str,
    logical_tool_call_id: str,
    attempt_id: str,
    limit: int = 100,
    cursor: str | None = None,
) -> dict:
    """Return a mock list of failed systemd units."""
    filters = {"limit": limit}

    all_failed_units = [
        {
            "unit": "mock-worker.service",
            "active_state": "failed",
            "description": "Mock worker service",
        },
        {
            "unit": "mock-api.service",
            "active_state": "failed",
            "description": "Mock API service",
        },
        {
            "unit": "mock-database.service",
            "active_state": "failed",
            "description": "Mock database service",
        },
    ]

    offset = 0

    if cursor is not None:
        valid, result = validate_cursor(
            cursor,
            tool="list_failed_units",
            session_id=session_id,
            filters=filters,
        )

        if not valid:
            return make_response(
                tool="list_failed_units",
                trace_id=trace_id,
                logical_tool_call_id=logical_tool_call_id,
                attempt_id=attempt_id,
                session_id=session_id,
                data={"message": "Invalid or expired pagination cursor."},
                status="error",
                error_code=result,
            )

        offset = result

    page_units = all_failed_units[offset:offset + limit]
    next_offset = offset + len(page_units)
    next_cursor = None

    if next_offset < len(all_failed_units):
        next_cursor = create_cursor(
            tool="list_failed_units",
            session_id=session_id,
            filters=filters,
            offset=next_offset,
        )

    return make_response(
        tool="list_failed_units",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
        session_id=session_id,
        data={
            "failed_units": page_units,
            "count": len(page_units),
            "limit": limit,
        },
        status="ok",
        next_cursor=next_cursor,
    )


@register_tool()
def disk_usage(
    schema_version: str,
    session_id: str,
    trace_id: str,
    logical_tool_call_id: str,
    attempt_id: str,
    filesystem: str | None = None,
    include_inodes: bool = True,
) -> dict:
    """Return mock disk usage information."""
    return make_response(
        tool="disk_usage",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
        session_id=session_id,
        data={
            "filesystem": filesystem if filesystem is not None else "/dev/mock",
            "mount_point": "/",
            "used_percent": 65.5,
            "available_bytes": 10737418240,
            "inodes_included": include_inodes,
        },
    )


@register_tool()
def listening_ports(
    schema_version: str,
    session_id: str,
    trace_id: str,
    logical_tool_call_id: str,
    attempt_id: str,
    protocol: str | None = None,
    port: int | None = None,
    unit: str | None = None,
) -> dict:
    """Return mock listening network ports."""
    return make_response(
        tool="listening_ports",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
        session_id=session_id,
        data={
            "ports": [
                {
                    "protocol": protocol if protocol is not None else "tcp",
                    "port": port if port is not None else 8080,
                    "address": "0.0.0.0",
                    "process": "mock-server",
                    "unit": unit,
                }
            ],
            "count": 1,
        },
    )


@register_tool()
def check_config(
    schema_version: str,
    session_id: str,
    trace_id: str,
    logical_tool_call_id: str,
    attempt_id: str,
    profile: str,
    unit: str | None = None,
) -> dict:
    """Validate a configuration using a mock predefined profile."""
    return make_response(
        tool="check_config",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
        session_id=session_id,
        data={
            "profile": profile,
            "unit": unit,
            "valid": True,
            "issues": [],
            "message": "Mock configuration validation passed.",
        },
    )


@register_tool()
def memory_oom_events(
    schema_version: str,
    session_id: str,
    trace_id: str,
    logical_tool_call_id: str,
    attempt_id: str,
    since: str,
    until: str,
    limit: int = 100,
) -> dict:
    """Return mock Linux out-of-memory events."""
    return make_response(
        tool="memory_oom_events",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
        session_id=session_id,
        data={
            "events": [
                {
                    "timestamp": "2026-11-02T09:20:00Z",
                    "process": "mock-worker",
                    "message": "Mock process was terminated by OOM killer.",
                }
            ] if within_window([{"timestamp": "2026-11-02T09:20:00Z"}], since, until) else [],
            "count": int(bool(within_window([{"timestamp": "2026-11-02T09:20:00Z"}], since, until))),
            "limit": limit,
        },
    )


@register_tool()
def failed_logins(
    schema_version: str,
    session_id: str,
    trace_id: str,
    logical_tool_call_id: str,
    attempt_id: str,
    since: str,
    until: str,
    limit: int = 100,
) -> dict:
    """Return mock failed login events."""
    return make_response(
        tool="failed_logins",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
        session_id=session_id,
        data={
            "events": [
                {
                    "timestamp": "2026-11-02T09:25:00Z",
                    "user": "mock-user",
                    "source_ip": "192.0.2.10",
                    "message": "Mock authentication failure.",
                }
            ] if within_window([{"timestamp": "2026-11-02T09:25:00Z"}], since, until) else [],
            "count": int(bool(within_window([{"timestamp": "2026-11-02T09:25:00Z"}], since, until))),
            "limit": limit,
        },
    )


@register_tool()
def detect_anomaly(
    schema_version: str,
    session_id: str,
    trace_id: str,
    logical_tool_call_id: str,
    attempt_id: str,
    since: str,
    until: str,
    unit: str | None = None,
    signals: list[str] | None = None,
    max_findings: int = 20,
) -> dict:
    """Detect anomalies using deterministic mock fixtures. Read-only."""
    supported_signals = {
        "service_failures",
        "journal_errors",
        "oom_events",
        "failed_logins",
    }

    def error_response(message: str, error_code: str = "INVALID_INPUT"):
        return make_response(
            tool="detect_anomaly",
            trace_id=trace_id,
            logical_tool_call_id=logical_tool_call_id,
            attempt_id=attempt_id,
            session_id=session_id,
            data={"message": message},
            status="error",
            error_code=error_code,
        )

    # Validate the time window.
    try:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))

        if since_dt.tzinfo is None or until_dt.tzinfo is None:
            return error_response("Timestamps must include UTC timezone.")

        if since_dt.utcoffset().total_seconds() != 0:
            return error_response("since must be RFC3339 UTC.")

        if until_dt.utcoffset().total_seconds() != 0:
            return error_response("until must be RFC3339 UTC.")

        if until_dt <= since_dt:
            return error_response("until must be greater than since.")

    except (ValueError, AttributeError):
        return error_response("Invalid RFC3339 UTC timestamp.")

    if not isinstance(max_findings, int) or isinstance(max_findings, bool):
        return error_response("max_findings must be an integer.")

    if not 1 <= max_findings <= 50:
        return error_response("max_findings must be between 1 and 50.")

    if signals is not None:
        if not isinstance(signals, list):
            return error_response("signals must be an array.")

        if any(not isinstance(signal, str) for signal in signals):
            return error_response("Every signal must be a string.")

        if len(signals) != len(set(signals)):
            return error_response("signals must not contain duplicates.")

        invalid_signals = [
            signal for signal in signals if signal not in supported_signals
        ]

        if invalid_signals:
            return make_response(
                tool="detect_anomaly",
                trace_id=trace_id,
                logical_tool_call_id=logical_tool_call_id,
                attempt_id=attempt_id,
                session_id=session_id,
                data={
                    "message": "Unsupported signal type.",
                },
                status="error",
                error_code="INVALID_INPUT",
            )

    selected_signals = (
        signals if signals is not None else sorted(supported_signals)
    )

    mock_findings = {
        "service_failures": {
            "id": "mock-anomaly-001",
            "type": "service_failures",
            "severity": "high",
            "summary": "A mock service failure event was detected.",
            "observed_count": 3,
            "detection_method": "rule_based",
        },
        "journal_errors": {
            "id": "mock-anomaly-002",
            "type": "journal_errors",
            "severity": "medium",
            "summary": "Mock journal error events were detected.",
            "observed_count": 4,
            "detection_method": "rule_based",
        },
        "oom_events": {
            "id": "mock-anomaly-003",
            "type": "oom_events",
            "severity": "high",
            "summary": "A mock out-of-memory event was detected.",
            "observed_count": 2,
            "detection_method": "rule_based",
        },
        "failed_logins": {
            "id": "mock-anomaly-004",
            "type": "failed_logins",
            "severity": "high",
            "summary": "Mock failed login events were detected.",
            "observed_count": 8,
            "detection_method": "rule_based",
        },
    }
    findings = []
    evidence_items = []

    event_dt = (datetime.fromisoformat(ANOMALY_EVENT_AT.replace("Z", "+00:00"))
                if ANOMALY_EVENT_AT is not None else None)
    for signal in selected_signals:
        if event_dt is None or not since_dt <= event_dt < until_dt:
            continue
        evidence_id = f"mock-evidence-{secrets.token_hex(12)}"

        finding = mock_findings[signal].copy()
        finding["window"] = {"since": since, "until": until}
        finding["evidence_refs"] = [evidence_id]
        findings.append(finding)

        evidence_items.append({
            "evidence_id": evidence_id,
            "observed_at": ANOMALY_EVENT_AT,
            "source": f"synthetic.mock.{signal}",
            "unit": unit,
            "excerpt": finding["summary"],
            "redacted": False,
            "truncated": False,
        })

    findings_truncated = len(findings) > max_findings
    findings = findings[:max_findings]

    evidence_ids = {
        finding["evidence_refs"][0]
        for finding in findings
    }
    evidence_items = [
        item for item in evidence_items
        if item["evidence_id"] in evidence_ids
    ]

    return make_response(
        tool="detect_anomaly",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
        session_id=session_id,
        evidence=evidence_items,
        data={
            "anomalies": findings,
            "analyzed_signals": selected_signals,
            "insufficient_signals": selected_signals if event_dt is None else [],
            "findings_truncated": findings_truncated,
            "confidence": None,
        },
        status="partial" if findings_truncated or event_dt is None else "ok",
        truncated=findings_truncated,
        next_cursor=None,
    )

@register_tool()
def verify_fix(
    schema_version: str,
    session_id: str,
    trace_id: str,
    logical_tool_call_id: str,
    attempt_id: str,
    unit: str,
    since: str,
    check_types: list[str],
    until: str | None = None,
    action_id: str | None = None,
    baseline_evidence_refs: list[str] | None = None,
) -> dict:
    """Verify Linux service state using mock data. Read-only."""
    supported_checks = {
        "service_active",
        "no_new_failures",
        "port_listening",
    }

    def error_response(message: str, error_code: str = "INVALID_INPUT"):
        return make_response(
            tool="verify_fix",
            trace_id=trace_id,
            logical_tool_call_id=logical_tool_call_id,
            attempt_id=attempt_id,
            session_id=session_id,
            data={"message": message},
            status="error",
            error_code=error_code,
        )

    if not isinstance(unit, str) or not unit.strip():
        return error_response("unit must be a non-empty string.")

    if not isinstance(check_types, list) or not check_types:
        return error_response("check_types must be a non-empty array.")

    if any(not isinstance(check, str) for check in check_types):
        return error_response("Every check_type must be a string.")

    if len(check_types) != len(set(check_types)):
        return error_response("check_types must not contain duplicates.")

    invalid_checks = [
        check for check in check_types if check not in supported_checks
    ]

    if invalid_checks:
        return make_response(
            tool="verify_fix",
            trace_id=trace_id,
            logical_tool_call_id=logical_tool_call_id,
            attempt_id=attempt_id,
            session_id=session_id,
            data={
                "message": "Unsupported verification check.",
            },
            status="error",
            error_code="INVALID_INPUT",
        )

    try:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))

        if since_dt.tzinfo is None or since_dt.utcoffset().total_seconds() != 0:
            return error_response("since must be RFC3339 UTC.")

        if until is not None:
            until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))

            if until_dt.tzinfo is None or until_dt.utcoffset().total_seconds() != 0:
                return error_response("until must be RFC3339 UTC.")

            if until_dt <= since_dt:
                return error_response("until must be greater than since.")

    except (ValueError, AttributeError):
        return error_response("Invalid RFC3339 UTC timestamp.")

    if baseline_evidence_refs is None:
        baseline_evidence_refs = []

    if not isinstance(baseline_evidence_refs, list) or any(
        not isinstance(ref, str) or not ref.strip()
        for ref in baseline_evidence_refs
    ):
        return error_response(
            "baseline_evidence_refs must be an array of non-empty strings."
        )

    # Reject duplicate references so the baseline list is unambiguous.
    if len(baseline_evidence_refs) != len(set(baseline_evidence_refs)):
        return error_response(
            "baseline_evidence_refs must not contain duplicates."
        )

    checks = []

    for check_type in check_types:
        if check_type == "service_active":
            summary = "Mock cannot verify the live service state."
        elif check_type == "no_new_failures":
            summary = "Mock cannot verify new failure events."
        else:
            summary = "Mock has no trusted expected-port profile."

        checks.append(
            {
                "check_type": check_type,
                "result": "unknown",
                "summary": summary,
                "evidence_refs": [],
            }
        )

    overall_result = aggregate_verify_results(checks)

    return make_response(
        tool="verify_fix",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
        session_id=session_id,
        data={
            "action_id": action_id,
            "unit": unit,
            "overall_result": overall_result,
            "checks": checks,
            "baseline_evidence_refs": baseline_evidence_refs,
            "verified_at": current_time(),
        },
        status="partial",
        next_cursor=None,
    )


@register_tool()
def restart_service(
    schema_version: str,
    session_id: str,
    trace_id: str,
    logical_tool_call_id: str,
    attempt_id: str,
    mode: Literal["propose", "status"],
    unit: str,
    reason: str,
    evidence_ids: list[str] | None = None,
    action_id: str | None = None,
) -> dict:
    """Create or inspect a mock restart proposal. Never restarts a service."""
    if not unit or not unit.strip():
        return make_response(
            tool="restart_service",
            trace_id=trace_id,
            logical_tool_call_id=logical_tool_call_id,
            attempt_id=attempt_id,
            session_id=session_id,
            data={"message": "unit is required."},
            status="error",
            error_code="INVALID_INPUT",
        )

    if mode == "propose":
        return make_response(
            tool="restart_service",
            trace_id=trace_id,
            logical_tool_call_id=logical_tool_call_id,
            attempt_id=attempt_id,
            session_id=session_id,
            data={
                "proposal_id": "mock-proposal-001",
                "proposal_status": "pending_approval",
                "reason": reason,
                "evidence_ids": evidence_ids or [],
                "executed": False,
                "message": "Mock proposal created. Human approval is required.",
            },
            status="approval_required",
            error_code="APPROVAL_REQUIRED",
        )

    if mode == "status":
        if not action_id:
            return make_response(
                tool="restart_service",
                trace_id=trace_id,
                logical_tool_call_id=logical_tool_call_id,
                attempt_id=attempt_id,
                session_id=session_id,
                data={
                    "message": "action_id is required to inspect proposal status."
                },
                status="error",
                error_code="INVALID_INPUT",
            )

        return make_response(
            tool="restart_service",
            trace_id=trace_id,
            logical_tool_call_id=logical_tool_call_id,
            attempt_id=attempt_id,
            session_id=session_id,
            data={
                "action_id": action_id,
                "proposal_status": "pending_approval",
                "executed": False,
                "message": "Mock proposal is waiting for approval.",
            },
        )

    return make_response(
        tool="restart_service",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
        session_id=session_id,
        data={"message": "Unsupported mock mode."},
        status="error",
        error_code="INVALID_INPUT",
    )


if __name__ == "__main__":
    asyncio.run(run())
