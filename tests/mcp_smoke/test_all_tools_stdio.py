import asyncio
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from mcp import Client, StdioServerParameters


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "mock_mcp_server.py"
SCHEMAS_DIR = ROOT / "schemas" / "tools"
COMMON_SCHEMA_PATH = ROOT / "schemas" / "common-response.schema.json"


# Client creates these IDs.
COMMON = {
    "schema_version": "1.0.0",
    "session_id": "test-session",
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
        "profile": "ssh",
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


async def main():
    if not SERVER.exists():
        raise FileNotFoundError(
            f"Khong tim thay MCP server: {SERVER}"
        )

    with COMMON_SCHEMA_PATH.open(encoding="utf-8") as file:
        common_schema = json.load(file)

    common_validator = Draft202012Validator(
        common_schema,
        format_checker=FormatChecker()
    )

    tool_validators = {}

    for tool_name in REQUESTS:
        schema_path = (
            SCHEMAS_DIR / f"{tool_name}.response.schema.json"
        )

        if not schema_path.exists():
            raise FileNotFoundError(
                f"Missing output schema: {schema_path}"
            )

        with schema_path.open(encoding="utf-8") as file:
            schema = json.load(file)

        Draft202012Validator.check_schema(schema)

        tool_validators[tool_name] = Draft202012Validator(
            schema,
            format_checker=FormatChecker()
        )

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER)],
    )

    async with Client(params) as client:

        # 1. Discover tools.
        result = await client.list_tools()
        available = {tool.name for tool in result.tools}
        for descriptor in result.tools:
            for suffix, actual in [(".schema.json", descriptor.input_schema),
                                   (".result.schema.json", descriptor.output_schema)]:
                canonical = json.loads((SCHEMAS_DIR / (descriptor.name + suffix)).read_text(encoding="utf-8"))
                assert actual == canonical, f"{descriptor.name}: discovery schema mismatch"


        print(f"Server co {len(available)} tools")

        assert len(available) == 12, (
            f"Expected 12 tools, found {len(available)}"
        )

        assert set(REQUESTS) == available, (
            f"Tool mismatch. Expected: {set(REQUESTS)}, "
            f"Available: {available}"
        )

        # 2. Call and validate all tools.
        for tool_name, request_arguments in REQUESTS.items():

            # Create unique IDs for each logical tool call.
            arguments = {
                **request_arguments,
                "trace_id": f"trace-{tool_name}",
                "logical_tool_call_id": f"call-{tool_name}",
                "attempt_id": f"attempt-{tool_name}-001",
            }

            response = await client.call_tool(
                tool_name,
                arguments,
            )

            assert not response.is_error, (
                f"{tool_name}: MCP call error"
            )

            # Read structured response.
            data = response.structured_content

            assert data is not None, f"{tool_name}: missing native structuredContent"
            assert json.loads(response.content[0].text) == data

            assert isinstance(data, dict), (
                f"{tool_name}: response must be a dict"
            )

            # 3. Validate the complete CommonResponse envelope.
            common_errors = list(
                common_validator.iter_errors(data)
            )

            assert not common_errors, (
                f"{tool_name}: CommonResponse validation failed: "
                + "; ".join(
                    f"{list(error.absolute_path)}: {error.message}"
                    for error in common_errors
                )
            )

            # 4. Validate Client-created IDs.
            assert data["schema_version"] == COMMON["schema_version"]
            assert data["session_id"] == COMMON["session_id"]
            assert data["trace_id"] == arguments["trace_id"]

            assert (
                data["logical_tool_call_id"]
                == arguments["logical_tool_call_id"]
            )

            assert data["attempt_id"] == arguments["attempt_id"]
            assert data["tool"] == tool_name

            # 5. Validate tool-specific response data.
            # Error responses may contain generic error data,
            # so only validate normal result statuses here.
            if data["status"] in {
                "ok",
                "partial",
                "approval_required",
            }:
                tool_errors = list(
                    tool_validators[tool_name].iter_errors(
                        data["data"]
                    )
                )

                assert not tool_errors, (
                    f"{tool_name}: output data validation failed: "
                    + "; ".join(
                        f"{list(error.absolute_path)}: {error.message}"
                        for error in tool_errors
                    )
                )

            # 6. Validate expected status.
            expected_status = {
                "verify_fix": "partial",
                "restart_service": "approval_required",
            }.get(tool_name, "ok")

            assert data["status"] == expected_status, (
                f"{tool_name}: expected status {expected_status}, "
                f"got {data['status']}"
            )

            # restart_service must never execute a real restart.
            if tool_name == "restart_service":
                assert data["error_code"] == "APPROVAL_REQUIRED"
                assert data["data"]["executed"] is False

            print(
                f"PASS: {tool_name} "
                f"- status={data['status']} "
                f"- output schema valid"
            )

        print("\nALL 12 TOOLS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
