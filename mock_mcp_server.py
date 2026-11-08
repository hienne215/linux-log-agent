from datetime import datetime, timezone
from mcp.server import MCPServer

mcp = MCPServer("linux-log-agent-mock")


def current_time() -> str:
    """Return current UTC time in RFC3339 format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def aggregate_verify_results(checks: list[dict]) -> str:
    """
    Tổng hợp kết quả kiểm tra verify_fix theo thứ tự ưu tiên:
    fail > unknown > pass. Nếu danh sách checks rỗng, trả về 'unknown'.
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
    status: str = "ok",
    error_code: str | None = None,
    evidence: list | None = None,
    truncated: bool = False,
    next_cursor: str | None = None,
    session_id: str | None = None,
) -> dict:
    """
    Tạo envelope phản hồi chuẩn cho MCP tools.
    Khóa chặt thuộc tính theo CommonResponse JSON Schema (additionalProperties: false).
    """
    started_at = current_time()
    completed_at = current_time()

    return {
        "schema_version": "1.0.0",
        "session_id": session_id,
        "trace_id": trace_id,
        "logical_tool_call_id": logical_tool_call_id,
        "attempt_id": attempt_id,
        "tool": tool,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": 0,
        "data": data,
        "evidence": evidence or [],
        "error_code": error_code,
        "truncated": truncated,
        "next_cursor": next_cursor,
    }


@mcp.tool()
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
    """Return mock journal entries for a service."""
    return make_response(
        tool="query_journal",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
        data={
            "entries": [
                {
                    "timestamp": "2026-11-02T09:30:00Z",
                    "priority": 3,
                    "message": "Mock service started successfully.",
                }
            ],
            "count": 1,
            "limit": limit,
        },
        session_id=session_id,
    )


@mcp.tool()
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
    return make_response(
        tool="search_log",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
        data={
            "source_id": source_id,
            "query": query,
            "matches": [
                {
                    "timestamp": "2026-11-02T09:35:00Z",
                    "message": f"Mock log entry matching: {query}",
                }
            ],
            "count": 1,
            "limit": limit,
        },
        session_id=session_id,
    )


@mcp.tool()
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
        data={
            "unit": unit,
            "active_state": "active",
            "sub_state": "running",
            "load_state": "loaded",
            "description": "Mock service",
        },
        session_id=session_id,
    )


@mcp.tool()
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
    return make_response(
        tool="list_failed_units",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
        data={
            "failed_units": [
                {
                    "unit": "mock-worker.service",
                    "active_state": "failed",
                    "description": "Mock worker service",
                }
            ],
            "count": 1,
            "limit": limit,
        },
        session_id=session_id,
    )


@mcp.tool()
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
        data={
            "filesystem": filesystem or "/dev/mock",
            "mount_point": "/",
            "used_percent": 65.5,
            "available_bytes": 10737418240,
            "inodes_included": include_inodes,
        },
        session_id=session_id,
    )


@mcp.tool()
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
        data={
            "ports": [
                {
                    "protocol": protocol or "tcp",
                    "port": port or 8080,
                    "address": "0.0.0.0",
                    "process": "mock-server",
                }
            ],
            "count": 1,
        },
        session_id=session_id,
    )


@mcp.tool()
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
        data={
            "profile": profile,
            "valid": True,
            "issues": [],
            "message": "Mock configuration validation passed.",
        },
        session_id=session_id,
    )


@mcp.tool()
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
        session_id=session_id,
    )


@mcp.tool()
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
        session_id=session_id,
    )


@mcp.tool()
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
            data={"message": message},
            status="error",
            error_code=error_code,
            session_id=session_id,
        )

    # Validate khung thời gian
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
                data={
                    "message": "Unsupported signal type.",
                    "invalid_signals": invalid_signals,
                },
                status="error",
                error_code="INVALID_INPUT",
                session_id=session_id,
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
    for signal in selected_signals:
        finding = mock_findings[signal].copy()
        finding["window"] = {"since": since, "until": until}
        finding["evidence_refs"] = [f"mock-evidence-{signal}"]
        findings.append(finding)

    findings_truncated = len(findings) > max_findings
    findings = findings[:max_findings]

    return make_response(
        tool="detect_anomaly",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
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
        session_id=session_id,
    )


@mcp.tool()
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
            data={"message": message},
            status="error",
            error_code=error_code,
            session_id=session_id,
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
            data={
                "message": "Unsupported verification check.",
                "invalid_checks": invalid_checks,
            },
            status="error",
            error_code="INVALID_INPUT",
            session_id=session_id,
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

    checks = []
    for check_type in check_types:
        if check_type == "service_active":
            summary = "Mock cannot verify the live service state."
        elif check_type == "no_new_failures":
            summary = "Mock cannot verify new failure events."
        else:
            summary = "Mock has no trusted expected-port profile."

        checks.append({
            "check_type": check_type,
            "result": "unknown",
            "summary": summary,
            "evidence_refs": [],
        })

    overall_result = aggregate_verify_results(checks)

    return make_response(
        tool="verify_fix",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
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
        session_id=session_id,
    )


@mcp.tool()
def restart_service(
    schema_version: str,
    session_id: str,
    trace_id: str,
    logical_tool_call_id: str,
    attempt_id: str,
    mode: str,
    unit: str,
    reason: str,
    evidence_ids: list[str] | None = None,
    action_id: str | None = None,
) -> dict:
    """Create or inspect a mock restart proposal. Never restarts a service."""

    if mode == "propose":
        return make_response(
            tool="restart_service",
            trace_id=trace_id,
            logical_tool_call_id=logical_tool_call_id,
            attempt_id=attempt_id,
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
            session_id=session_id,
        )

    if mode == "status":
        if not action_id:
            return make_response(
                tool="restart_service",
                trace_id=trace_id,
                logical_tool_call_id=logical_tool_call_id,
                attempt_id=attempt_id,
                data={
                    "message": "action_id is required to inspect proposal status."
                },
                status="error",
                error_code="INVALID_INPUT",
                session_id=session_id,
            )

        return make_response(
            tool="restart_service",
            trace_id=trace_id,
            logical_tool_call_id=logical_tool_call_id,
            attempt_id=attempt_id,
            data={
                "action_id": action_id,
                "proposal_status": "pending_approval",
                "executed": False,
                "message": "Mock proposal is waiting for approval.",
            },
            session_id=session_id,
        )

    return make_response(
        tool="restart_service",
        trace_id=trace_id,
        logical_tool_call_id=logical_tool_call_id,
        attempt_id=attempt_id,
        data={"message": "Unsupported mock mode."},
        status="error",
        error_code="INVALID_INPUT",
        session_id=session_id,
    )


if __name__ == "__main__":
    mcp.run()