from datetime import datetime, timezone
from uuid import uuid4

from mcp.server import MCPServer


mcp = MCPServer("linux-log-agent-mock")


def current_time():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def make_response(
    tool,
    trace_id,
    unit,
    data,
    status="ok",
    error_code=None
):
    started_at = current_time()
    completed_at = current_time()

    return {
        "schema_version": "1.0.0",
        "trace_id": trace_id,
        "logical_tool_call_id": f"call-{uuid4().hex[:8]}",
        "attempt_id": f"attempt-{uuid4().hex[:8]}",
        "tool": tool,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "unit": unit,
        "data": data,
        "evidence": [],
        "command_source": "mock_fixture",
        "error_code": error_code,
        "truncated": False,
        "next_cursor": None,
        "duration_ms": 0
    }


@mcp.tool()
def query_journal(
    schema_version: str,
    session_id: str,
    trace_id: str,
    unit: str,
    since: str,
    until: str,
    priority: int | None = None,
    limit: int = 100,
    cursor: str | None = None
) -> dict:
    """Return mock journal entries for a service."""
    return make_response(
        "query_journal",
        trace_id,
        unit,
        {
            "entries": [
                {
                    "timestamp": "2026-11-02T09:30:00Z",
                    "priority": 3,
                    "message": "Mock service started successfully."
                }
            ],
            "count": 1,
            "limit": limit
        }
    )


@mcp.tool()
def search_log(
    schema_version: str,
    session_id: str,
    trace_id: str,
    source_id: str,
    query: str,
    since: str,
    until: str,
    limit: int = 100,
    cursor: str | None = None
) -> dict:
    """Search mock log data using a text query."""
    return make_response(
        "search_log",
        trace_id,
        None,
        {
            "source_id": source_id,
            "query": query,
            "matches": [
                {
                    "timestamp": "2026-11-02T09:35:00Z",
                    "message": f"Mock log entry matching: {query}"
                }
            ],
            "count": 1,
            "limit": limit
        }
    )


@mcp.tool()
def service_status(
    schema_version: str,
    session_id: str,
    trace_id: str,
    unit: str
) -> dict:
    """Return mock status information for a systemd service."""
    return make_response(
        "service_status",
        trace_id,
        unit,
        {
            "active_state": "active",
            "sub_state": "running",
            "load_state": "loaded",
            "description": "Mock service"
        }
    )


@mcp.tool()
def list_failed_units(
    schema_version: str,
    session_id: str,
    trace_id: str,
    limit: int = 100,
    cursor: str | None = None
) -> dict:
    """Return a mock list of failed systemd units."""
    return make_response(
        "list_failed_units",
        trace_id,
        None,
        {
            "failed_units": [
                {
                    "unit": "mock-worker.service",
                    "active_state": "failed",
                    "description": "Mock worker service"
                }
            ],
            "count": 1,
            "limit": limit
        }
    )


@mcp.tool()
def disk_usage(
    schema_version: str,
    session_id: str,
    trace_id: str,
    filesystem: str | None = None,
    include_inodes: bool = True
) -> dict:
    """Return mock disk usage information."""
    return make_response(
        "disk_usage",
        trace_id,
        None,
        {
            "filesystem": filesystem or "/dev/mock",
            "mount_point": "/",
            "used_percent": 65.5,
            "available_bytes": 10737418240,
            "inodes_included": include_inodes
        }
    )


@mcp.tool()
def listening_ports(
    schema_version: str,
    session_id: str,
    trace_id: str,
    protocol: str | None = None,
    port: int | None = None,
    unit: str | None = None
) -> dict:
    """Return mock listening network ports."""
    return make_response(
        "listening_ports",
        trace_id,
        unit,
        {
            "ports": [
                {
                    "protocol": protocol or "tcp",
                    "port": port or 8080,
                    "address": "0.0.0.0",
                    "process": "mock-server"
                }
            ],
            "count": 1
        }
    )


@mcp.tool()
def check_config(
    schema_version: str,
    session_id: str,
    trace_id: str,
    profile: str,
    unit: str | None = None
) -> dict:
    """Validate a configuration using a mock predefined profile."""
    return make_response(
        "check_config",
        trace_id,
        unit,
        {
            "profile": profile,
            "valid": True,
            "issues": [],
            "message": "Mock configuration validation passed."
        }
    )

@mcp.tool()
def memory_oom_events(
    schema_version: str,
    session_id: str,
    trace_id: str,
    since: str,
    until: str,
    limit: int = 100
) -> dict:
    """Return mock Linux out-of-memory events."""
    return make_response(
        "memory_oom_events",
        trace_id,
        None,
        {
            "events": [
                {
                    "timestamp": "2026-11-02T09:20:00Z",
                    "process": "mock-worker",
                    "message": "Mock process was terminated by OOM killer."
                }
            ],
            "count": 1,
            "limit": limit
        }
    )


@mcp.tool()
def failed_logins(
    schema_version: str,
    session_id: str,
    trace_id: str,
    since: str,
    until: str,
    limit: int = 100
) -> dict:
    """Return mock failed login events."""
    return make_response(
        "failed_logins",
        trace_id,
        None,
        {
            "events": [
                {
                    "timestamp": "2026-11-02T09:25:00Z",
                    "user": "mock-user",
                    "source_ip": "192.0.2.10",
                    "message": "Mock authentication failure."
                }
            ],
            "count": 1,
            "limit": limit
        }
    )


@mcp.tool()
def detect_anomaly(
    schema_version: str,
    session_id: str,
    trace_id: str,
    source_id: str,
    window: str,
    baseline_window: str,
    threshold: float = 2.0
) -> dict:
    """Return a mock log anomaly detection result."""
    return make_response(
        "detect_anomaly",
        trace_id,
        None,
        {
            "source_id": source_id,
            "anomaly_detected": True,
            "baseline_count": 10,
            "current_count": 30,
            "ratio": 3.0,
            "threshold": threshold,
            "window": window,
            "baseline_window": baseline_window
        }
    )


@mcp.tool()
def verify_fix(
    schema_version: str,
    session_id: str,
    trace_id: str,
    unit: str,
    since: str,
    evidence_ids: list[str] | None = None
) -> dict:
    """Return a mock verification result for a proposed fix."""
    return make_response(
        "verify_fix",
        trace_id,
        unit,
        {
            "result": "pass",
            "reasons": [
                "Mock service is active.",
                "No new mock failure event was detected."
            ],
            "checked_evidence_ids": evidence_ids or []
        }
    )


@mcp.tool()
def restart_service(
    schema_version: str,
    session_id: str,
    trace_id: str,
    mode: str,
    unit: str,
    reason: str,
    evidence_ids: list[str] | None = None
) -> dict:
    """Create or inspect a mock restart proposal. Never restarts a service."""
    if mode == "propose":
        return make_response(
            "restart_service",
            trace_id,
            unit,
            {
                "proposal_id": "mock-proposal-001",
                "proposal_status": "pending_approval",
                "reason": reason,
                "evidence_ids": evidence_ids or [],
                "executed": False,
                "message": "Mock proposal created. Human approval is required."
            },
            status="approval_required",
            error_code="APPROVAL_REQUIRED"
        )

    if mode == "status":
        return make_response(
            "restart_service",
            trace_id,
            unit,
            {
                "proposal_id": "mock-proposal-001",
                "proposal_status": "pending_approval",
                "executed": False,
                "message": "Mock proposal is waiting for approval."
            }
        )

    return make_response(
        "restart_service",
        trace_id,
        unit,
        {
            "message": "Unsupported mock mode."
        },
        status="error",
        error_code="INVALID_INPUT"
    )
if __name__ == "__main__":
    mcp.run()