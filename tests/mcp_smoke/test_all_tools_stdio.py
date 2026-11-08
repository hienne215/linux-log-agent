
import asyncio
import json
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters


SERVER = Path(__file__).resolve().parents[2] / "mock_mcp_server.py"


# Client creates these IDs.
COMMON = {
    "schema_version": "1.0.0",
    "session_id": "test-session",
    "trace_id": "test-trace",
    "logical_tool_call_id": "client-call-001",
    "attempt_id": "client-attempt-001",
}


# Test requests for all 12 tools.
REQUESTS = {
    "query_journal": {
        **COMMON,
        "unit": "nginx.service",
        "since": "2026-11-01T00:00:00Z",
        "until": "2026-11-05T00:00:00Z",
    },
    "search_log": {
        **COMMON,
        "source_id": "mock-source",
        "query": "error",
        "since": "2026-11-01T00:00:00Z",
        "until": "2026-11-05T00:00:00Z",
    },
    "service_status": {
        **COMMON,
        "unit": "nginx.service",
    },
    "list_failed_units": {
        **COMMON,
    },
    "disk_usage": {
        **COMMON,
    },
    "listening_ports": {
        **COMMON,
    },
    "check_config": {
        **COMMON,
        "profile": "default",
    },
    "memory_oom_events": {
        **COMMON,
        "since": "2026-11-01T00:00:00Z",
        "until": "2026-11-05T00:00:00Z",
    },
    "failed_logins": {
        **COMMON,
        "since": "2026-11-01T00:00:00Z",
        "until": "2026-11-05T00:00:00Z",
    },
    "detect_anomaly": {
        **COMMON,
        "since": "2026-11-01T00:00:00Z",
        "until": "2026-11-05T00:00:00Z",
    },
"verify_fix": {
    **COMMON,
    "unit": "nginx.service",
    "check_types": ["service_active"],
    "baseline_evidence_refs": ["baseline-001"],
    "since": "2026-11-01T00:00:00Z",
    "until": "2026-11-05T00:00:00Z",
    },
    "restart_service": {
        **COMMON,
        "mode": "propose",
        "unit": "nginx.service",
        "reason": "STDIO mock test",
    },
}


# CommonResponse v1.0 required fields.
REQUIRED_RESPONSE_FIELDS = {
    "schema_version",
    "session_id",
    "trace_id",
    "logical_tool_call_id",
    "attempt_id",
    "tool",
    "status",
    "started_at",
    "completed_at",
    "duration_ms",
    "data",
    "evidence",
    "error_code",
    "truncated",
    "next_cursor",
}


async def main():
    if not SERVER.exists():
        raise FileNotFoundError(
            f"Không tìm thấy MCP server: {SERVER}"
        )

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER)],
    )

    async with Client(params) as client:

        # 1. Discover tools.
        result = await client.list_tools()
        available = {tool.name for tool in result.tools}

        print(f"Server có {len(available)} tools")

        assert len(available) == 12, (
            f"Expected 12 tools, found {len(available)}"
        )

        # 2. Call and validate all tools.
        for tool_name, arguments in REQUESTS.items():

            assert tool_name in available, (
                f"Thiếu tool: {tool_name}"
            )

            response = await client.call_tool(
                tool_name,
                arguments,
            )

            assert not response.is_error, (
                f"{tool_name}: MCP call error"
            )

            # Read structured response.
            data = response.structured_content

            # Fallback: read text content if structured_content is absent.
            if data is None:
                text_blocks = [
                    item.text
                    for item in response.content
                    if getattr(item, "type", None) == "text"
                ]

                if not text_blocks:
                    raise RuntimeError(
                        f"{tool_name}: không có dữ liệu response"
                    )

                data = json.loads(text_blocks[0])

            # Some SDK versions may wrap structured content.
            if (
                isinstance(data, dict)
                and "structuredContent" in data
                and "status" not in data
            ):
                data = data["structuredContent"]

            assert isinstance(data, dict), (
                f"{tool_name}: response phải là dict"
            )

            # 3. Validate CommonResponse envelope.
            missing = [
                field
                for field in REQUIRED_RESPONSE_FIELDS
                if field not in data
            ]

            assert not missing, (
                f"{tool_name}: thiếu fields {missing}"
            )

            # 4. Validate Client-created IDs.
            assert data["schema_version"] == COMMON["schema_version"]
            assert data["session_id"] == COMMON["session_id"]
            assert data["trace_id"] == COMMON["trace_id"]

            assert (
                data["logical_tool_call_id"]
                == COMMON["logical_tool_call_id"]
            )

            assert data["attempt_id"] == COMMON["attempt_id"]
            assert data["tool"] == tool_name

            # 5. Validate status.
            assert data["status"] in {
                "ok",
                "partial",
                "error",
                "approval_required",
            }, (
                f"{tool_name}: status không hợp lệ: "
                f"{data['status']}"
            )

            # 6. Validate field types.
            assert isinstance(data["schema_version"], str)
            assert isinstance(data["started_at"], str)
            assert isinstance(data["completed_at"], str)

            assert isinstance(data["duration_ms"], (int, float))
            assert not isinstance(data["duration_ms"], bool)
            assert data["duration_ms"] >= 0

            assert isinstance(data["data"], dict)
            assert isinstance(data["evidence"], list)

            assert (
                data["error_code"] is None
                or isinstance(data["error_code"], str)
            )

            assert isinstance(data["truncated"], bool)

            assert (
                data["next_cursor"] is None
                or isinstance(data["next_cursor"], str)
            )

            print(
                f"PASS: {tool_name} "
                f"- status={data['status']}"
            )

        print("\nALL 12 TOOLS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
