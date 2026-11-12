from datetime import datetime, timezone, timedelta
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
from functools import wraps
from typing import Literal
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
import inspect
import json
import secrets


mcp = MCPServer("linux-log-agent-mock")

MAX_RESPONSE_BYTES = 64 * 1024
SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


def load_schema_registry() -> Registry:
    """Load local JSON schemas for resolving relative references."""
    registry = Registry()

    for schema_path in SCHEMA_DIR.rglob("*.schema.json"):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        schema_uri = schema_path.resolve().as_uri()
        schema.setdefault("$id", schema_uri)

        registry = registry.with_resource(
            schema_uri,
            Resource.from_contents(schema),
        )

    return registry


SCHEMA_REGISTRY = load_schema_registry()
# Cursor configuration for mock pagination.
CURSOR_TTL_SECONDS = 300

# Store cursor state in memory.
CURSOR_STORE: dict[str, dict] = {}


def current_time() -> str:
    """Return current UTC time in RFC3339 format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def create_cursor(
    tool: str,
    session_id: str,
    filters: dict,
    offset: int,
) -> str:
    """Create a session-bound, filter-bound pagination cursor."""
    token = secrets.token_urlsafe(32)

    CURSOR_STORE[token] = {
        "tool": tool,
        "session_id": session_id,
        "filters": filters,
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

    if state["filters"] != filters:
        return False, "INVALID_INPUT"

    return True, state["offset"]


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

    def serialized_size() -> int:
        return len(
            json.dumps(response, ensure_ascii=False).encode("utf-8")
        )

    response["completed_at"] = current_time()

    if serialized_size() > MAX_RESPONSE_BYTES:
        response["status"] = "partial"
        response["error_code"] = "OUTPUT_LIMIT"
        response["truncated"] = True
        response["data"] = {
            "message": "Response exceeded the maximum size limit.",
            "max_response_bytes": MAX_RESPONSE_BYTES,
        }
        response["evidence"] = []
        response["next_cursor"] = None
        response["completed_at"] = current_time()

    # Preserve all client-generated IDs when compacting the response.
    if serialized_size() > MAX_RESPONSE_BYTES:
        response["data"] = {"message": "OUTPUT_LIMIT"}
        response["evidence"] = []
        response["next_cursor"] = None
        response["completed_at"] = current_time()

    if serialized_size() > MAX_RESPONSE_BYTES:
        raise ValueError("Unable to construct a response within 64 KiB.")

    return response
def register_tool():
    """Register a tool with JSON Schema validation and structured results."""

    def decorator(fn):
        schema_path = (
            Path(__file__).resolve().parent
            / "schemas"
            / "tools"
            / f"{fn.__name__}.schema.json"
        )

        with schema_path.open("r", encoding="utf-8") as file:
            schema = json.load(file)

        schema_uri = schema_path.resolve().as_uri()
        schema.setdefault("$id", schema_uri)

        Draft202012Validator.check_schema(schema)

        validator = Draft202012Validator(
            schema,
            registry=SCHEMA_REGISTRY,
            format_checker=FormatChecker(),
        )

        @wraps(fn)
        def wrapper(*args, **kwargs):
            bound = inspect.signature(fn).bind(*args, **kwargs)
            arguments = bound.arguments

            errors = sorted(
                validator.iter_errors(arguments),
                key=lambda error: list(map(str, error.absolute_path)),
            )

            if errors:
                error = errors[0]
                field = ".".join(map(str, error.absolute_path))

                response = make_response(
                    tool=fn.__name__,
                    trace_id=arguments.get("trace_id", "invalid-input"),
                    logical_tool_call_id=arguments.get(
                        "logical_tool_call_id", "invalid-input"
                    ),
                    attempt_id=arguments.get("attempt_id", "invalid-input"),
                    session_id=arguments.get("session_id"),
                    data={
                        "message": "Input does not match tool schema.",
                        "field": field,
                        "details": error.message,
                    },
                    status="error",
                    error_code="INVALID_INPUT",
                )
            else:
                response = fn(*args, **kwargs)

            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(response, ensure_ascii=False),
                    )
                ],
                structured_content=response,
                is_error=response.get("status") == "error",
            )

        mcp.tool()(wrapper)
        return fn

    return decorator

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
            ],
            "count": 1,
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
            ],
            "count": 1,
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
                    "invalid_signals": invalid_signals,
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

    for signal in selected_signals:
        evidence_id = f"mock-evidence-{signal}"

        finding = mock_findings[signal].copy()
        finding["window"] = {"since": since, "until": until}
        finding["evidence_refs"] = [evidence_id]
        findings.append(finding)

        evidence_items.append({
            "evidence_id": evidence_id,
            "observed_at": current_time(),
            "source": f"mock_{signal}",
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
            "insufficient_signals": [],
            "findings_truncated": findings_truncated,
            "confidence": None,
        },
        status="partial" if findings_truncated else "ok",
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
                "invalid_checks": invalid_checks,
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
    mcp.run()
