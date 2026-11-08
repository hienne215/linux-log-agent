import asyncio
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
SERVER_FILE = ROOT / "mock_mcp_server.py"


async def main():
    # Kết nối đến Mock MCP Server
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_FILE)]
    )

    async with Client(server) as client:

        # ==========================================
        # 1. KIỂM TRA DANH SÁCH 12 TOOLS
        # ==========================================

        result = await client.list_tools()
        tool_names = {tool.name for tool in result.tools}

        expected_tools = {
            "query_journal",
            "search_log",
            "service_status",
            "list_failed_units",
            "disk_usage",
            "listening_ports",
            "check_config",
            "memory_oom_events",
            "failed_logins",
            "restart_service",
            "detect_anomaly",
            "verify_fix"
        }

        print("DISCOVERED TOOLS:", sorted(tool_names))

        assert expected_tools.issubset(tool_names), (
            "Some expected tools are missing."
        )

        print("12 TOOLS DISCOVERED PASSED")

        # ==========================================
        # 2. KHỞI TẠO RESPONSE SCHEMA VALIDATOR
        # ==========================================

        schema_path = ROOT / "schemas" / "common-response.schema.json"

        with schema_path.open(encoding="utf-8") as file:
            response_schema = json.load(file)

        validator = Draft202012Validator(
            response_schema,
            format_checker=FormatChecker()
        )

        # ==========================================
        # TEST ALL 12 MCP TOOLS
        # ==========================================

        common = {
            "schema_version": "1.0.0",
            "session_id": "session-test-all",
        }

        test_cases = [
    (
        "query_journal",
        {
            **common,
            "trace_id": "trace-query-journal",
            "logical_tool_call_id": "call-query-journal",
            "attempt_id": "attempt-query-journal",
            "unit": "nginx.service",
            "since": "2026-11-02T00:00:00Z",
            "until": "2026-11-02T23:59:59Z",
            "priority": 3,
            "limit": 100,
        },
    ),
    (
        "search_log",
        {
            **common,
            "trace_id": "trace-search-log",
            "logical_tool_call_id": "call-search-log",
            "attempt_id": "attempt-search-log",
            "source_id": "system-journal",
            "query": "error",
            "since": "2026-11-02T00:00:00Z",
            "until": "2026-11-02T23:59:59Z",
            "limit": 100,
        },
    ),
    (
        "service_status",
        {
            **common,
            "trace_id": "trace-service-status",
            "logical_tool_call_id": "call-service-status",
            "attempt_id": "attempt-service-status",
            "unit": "nginx.service",
        },
    ),
    (
        "list_failed_units",
        {
            **common,
            "trace_id": "trace-list-failed",
            "logical_tool_call_id": "call-list-failed",
            "attempt_id": "attempt-list-failed",
            "limit": 100,
        },
    ),
    (
        "disk_usage",
        {
            **common,
            "trace_id": "trace-disk-usage",
            "logical_tool_call_id": "call-disk-usage",
            "attempt_id": "attempt-disk-usage",
            "filesystem": "/dev/mock",
            "include_inodes": True,
        },
    ),
    (
        "listening_ports",
        {
            **common,
            "trace_id": "trace-listening-ports",
            "logical_tool_call_id": "call-listening-ports",
            "attempt_id": "attempt-listening-ports",
            "protocol": "tcp",
            "port": 8080,
            "unit": "nginx.service",
        },
    ),
    (
        "check_config",
        {
            **common,
            "trace_id": "trace-check-config",
            "logical_tool_call_id": "call-check-config",
            "attempt_id": "attempt-check-config",
            "profile": "ssh",
            "unit": "sshd.service",
        },
    ),
    (
        "memory_oom_events",
        {
            **common,
            "trace_id": "trace-memory-oom",
            "logical_tool_call_id": "call-memory-oom",
            "attempt_id": "attempt-memory-oom",
            "since": "2026-11-02T00:00:00Z",
            "until": "2026-11-02T23:59:59Z",
            "limit": 100,
        },
    ),
    (
        "failed_logins",
        {
            **common,
            "trace_id": "trace-failed-logins",
            "logical_tool_call_id": "call-failed-logins",
            "attempt_id": "attempt-failed-logins",
            "since": "2026-11-02T00:00:00Z",
            "until": "2026-11-02T23:59:59Z",
            "limit": 100,
        },
    ),
    (
        "detect_anomaly",
        {
            **common,
            "trace_id": "trace-detect-anomaly",
            "logical_tool_call_id": "call-detect-anomaly",
            "attempt_id": "attempt-detect-anomaly",
            "since": "2026-11-02T00:00:00Z",
            "until": "2026-11-02T23:59:59Z",
            "signals": ["service_failures", "journal_errors"],
            "max_findings": 20,
        },
    ),
    (
        "verify_fix",
        {
            **common,
            "trace_id": "trace-verify-fix",
            "logical_tool_call_id": "call-verify-fix",
            "attempt_id": "attempt-verify-fix",
            "unit": "nginx.service",
            "since": "2026-11-02T00:00:00Z",
            "check_types": ["service_active", "no_new_failures"],
            "baseline_evidence_refs": ["evidence-001"],
        },
    ),
    (
        "restart_service",
        {
            **common,
            "trace_id": "trace-restart-service",
            "logical_tool_call_id": "call-restart-service",
            "attempt_id": "attempt-restart-service",
            "mode": "propose",
            "unit": "nginx.service",
            "reason": "Testing mock restart proposal",
            "evidence_ids": ["evidence-001"],
        },
    ),
]
        for tool_name, arguments in test_cases:
            res = await client.call_tool(tool_name, arguments)

            assert getattr(res, "is_error", getattr(res, "isError", False)) is not True, (
                f"{tool_name} returned an MCP error"
            )

            response_data = json.loads(res.content[0].text)

            # Kiểm tra thông tin tool và request IDs
            assert response_data["tool"] == tool_name
            assert response_data["trace_id"] == arguments["trace_id"]
            assert response_data["logical_tool_call_id"] == arguments["logical_tool_call_id"]
            assert response_data["attempt_id"] == arguments["attempt_id"]

            # Kiểm tra response schema
            validator.validate(response_data)

            # Kiểm tra status theo từng tool
            if tool_name == "restart_service":
                assert response_data["status"] == "approval_required"
                assert response_data["error_code"] == "APPROVAL_REQUIRED"
                assert response_data["data"]["executed"] is False

            elif tool_name == "verify_fix":
                assert response_data["status"] == "partial"

            else:
                assert response_data["status"] == "ok"

            print(f"{tool_name}: PASSED")

        print("ALL 12 TOOLS INVOKED AND RESPONSE SCHEMAS VALIDATED")


        # ==========================================
        # 3. KIỂM TRA TOOL SERVICE_STATUS
        # ==========================================

        response = await client.call_tool(
            "service_status",
            {
                "schema_version": "1.0.0",
                "session_id": "session-test",
                "trace_id": "trace-test",
                "logical_tool_call_id": "call-service-status-extra",
                "attempt_id": "attempt-service-status-extra",
                "unit": "nginx.service"
            }
        )

        print("IS ERROR:", response.is_error)
        print("STRUCTURED CONTENT:", getattr(response, "structured_content", None))
        print("CONTENT:", response.content)

        assert response.is_error is False

        # Đọc dữ liệu trả về từ MCP
        if getattr(response, "structured_content", None) is not None:
            data = response.structured_content
        else:
            text_content = [
                item.text
                for item in response.content
                if hasattr(item, "text")
            ]

            assert text_content, "MCP response không có nội dung text."

            data = json.loads(text_content[0])

        assert data["status"] == "ok"
        assert data["tool"] == "service_status"
        assert data["data"]["active_state"] == "active"

        # Kiểm tra response schema
        errors = list(validator.iter_errors(data))

        if errors:
            for error in errors:
                print("SCHEMA ERROR:", error.message)

        assert not errors, (
            "Service status response does not match schema."
        )

        print("SERVICE STATUS TEST PASSED")
        print("RESPONSE SCHEMA VALIDATED")

        # ==========================================
        # 4. KIỂM TRA RESTART_SERVICE - MODE: PROPOSE
        # ==========================================

        restart_response = await client.call_tool(
            "restart_service",
            {
                "schema_version": "1.0.0",
                "session_id": "session-test",
                "trace_id": "trace-restart-test",
                "logical_tool_call_id": "call-restart-propose",
                "attempt_id": "attempt-restart-propose",
                "mode": "propose",
                "unit": "nginx.service",
                "reason": "Testing mock restart proposal",
                "evidence_ids": ["evidence-001"]
            }
        )

        assert restart_response.is_error is False

        restart_text = [
            item.text
            for item in restart_response.content
            if hasattr(item, "text")
        ]

        assert restart_text, "Restart response không có nội dung text."

        restart_data = json.loads(restart_text[0])

        # Kiểm tra kết quả proposal
        assert restart_data["status"] == "approval_required"
        assert restart_data["error_code"] == "APPROVAL_REQUIRED"
        assert restart_data["data"]["executed"] is False
        assert restart_data["data"]["proposal_status"] == "pending_approval"

        # Kiểm tra response schema
        restart_errors = list(
            validator.iter_errors(restart_data)
        )

        if restart_errors:
            for error in restart_errors:
                print("RESTART SCHEMA ERROR:", error.message)

        assert not restart_errors, (
            "Restart response does not match schema."
        )

        print("RESTART PROPOSAL TEST PASSED")
        print(
            "REAL RESTART EXECUTED:",
            restart_data["data"]["executed"]
        )

        # ==========================================
        # 5. KIỂM TRA RESTART_SERVICE - MODE: STATUS
        # ==========================================

        status_response = await client.call_tool(
            "restart_service",
            {
                "schema_version": "1.0.0",
                "session_id": "session-test",
                "trace_id": "trace-restart-status",
                "logical_tool_call_id": "call-restart-status",
                "attempt_id": "attempt-restart-status",
                "mode": "status",
                "unit": "nginx.service",
                "reason": "Check proposal status",
                "evidence_ids": ["evidence-001"],
                "action_id": "mock-proposal-001"
            }
        )

        assert status_response.is_error is False

        status_text = [
            item.text
            for item in status_response.content
            if hasattr(item, "text")
        ]

        assert status_text, "Status response không có nội dung text."

        status_data = json.loads(status_text[0])

        assert status_data["data"]["executed"] is False
        assert status_data["data"]["proposal_status"] == "pending_approval"

        status_errors = list(validator.iter_errors(status_data))
        assert not status_errors, [
            error.message for error in status_errors
        ]

        print("RESTART STATUS TEST PASSED")

        # ==========================================
        # 6. KIỂM TRA MODE KHÔNG HỢP LỆ
        # ==========================================

        invalid_result = await client.call_tool(
            "restart_service",
            {
                "schema_version": "1.0.0",
                "session_id": "session-test",
                "trace_id": "trace-restart-invalid",
                "logical_tool_call_id": "call-restart-invalid",
                "attempt_id": "attempt-restart-invalid",
                "mode": "invalid",
                "unit": "nginx.service",
                "reason": "Testing invalid mode",
                "evidence_ids": [],
            },
        )

        invalid_data = json.loads(invalid_result.content[0].text)

        print("INVALID MODE RESPONSE:")
        print(json.dumps(invalid_data, indent=2, ensure_ascii=False))

        assert invalid_data["status"] == "error"
        assert invalid_data["error_code"] == "INVALID_INPUT"
        assert "message" in invalid_data["data"]

        # Invalid input must not execute a real restart.
        assert "executed" not in invalid_data["data"]

        validator.validate(invalid_data)

        print("RESTART INVALID MODE TEST PASSED")
        print("REAL RESTART EXECUTED: False")

        # ==========================================
        # 7. TỔNG KẾT
        # ==========================================

        print("\nALL MOCK MCP TESTS PASSED")

if __name__ == "__main__":
    asyncio.run(main())