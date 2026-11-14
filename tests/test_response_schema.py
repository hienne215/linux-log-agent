import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = ROOT / "schemas" / "tools"
COMMON_SCHEMA_PATH = ROOT / "schemas" / "common-response.schema.json"

with COMMON_SCHEMA_PATH.open(encoding="utf-8") as file:
    COMMON_SCHEMA = json.load(file)

common_validator = Draft202012Validator(
    COMMON_SCHEMA,
    format_checker=FormatChecker()
)

TOOL_NAMES = [
    "query_journal",
    "search_log",
    "service_status",
    "list_failed_units",
    "disk_usage",
    "listening_ports",
    "check_config",
    "memory_oom_events",
    "failed_logins",
    "detect_anomaly",
    "verify_fix",
    "restart_service",
]


def load_tool_response_schema(tool_name):
    path = SCHEMAS_DIR / f"{tool_name}.response.schema.json"

    with path.open(encoding="utf-8") as file:
        return json.load(file)


def test_valid_response():
    response = {
        "schema_version": "1.0.0",
        "session_id": "session-001",
        "trace_id": "trace-001",
        "logical_tool_call_id": "call-001",
        "attempt_id": "attempt-001",
        "tool": "query_journal",
        "status": "ok",
        "started_at": "2026-11-05T09:00:00Z",
        "completed_at": "2026-11-05T09:00:01Z",
        "duration_ms": 1000,
        "data": {
            "entries": [],
            "count": 0
        },
        "evidence": [],
        "error_code": None,
        "truncated": False,
        "next_cursor": None
    }

    errors = list(common_validator.iter_errors(response))

    for error in errors:
        print(f"Field: {list(error.absolute_path)}")
        print(f"Error: {error.message}")

    assert not errors


def test_missing_trace_id_is_rejected():
    response = {
        "schema_version": "1.0.0",
        "session_id": "session-001",
        "logical_tool_call_id": "call-001",
        "attempt_id": "attempt-001",
        "tool": "query_journal",
        "status": "ok",
        "started_at": "2026-11-05T09:00:00Z",
        "completed_at": "2026-11-05T09:00:01Z",
        "duration_ms": 1000,
        "data": {},
        "evidence": [],
        "error_code": None,
        "truncated": False,
        "next_cursor": None
    }

    assert not common_validator.is_valid(response)


def test_invalid_status_is_rejected():
    response = {
        "schema_version": "1.0.0",
        "session_id": "session-001",
        "trace_id": "trace-001",
        "logical_tool_call_id": "call-001",
        "attempt_id": "attempt-001",
        "tool": "query_journal",
        "status": "success",
        "started_at": "2026-11-05T09:00:00Z",
        "completed_at": "2026-11-05T09:00:01Z",
        "duration_ms": 1000,
        "data": {},
        "evidence": [],
        "error_code": None,
        "truncated": False,
        "next_cursor": None
    }

    assert not common_validator.is_valid(response)


def test_unknown_field_is_rejected():
    response = {
        "schema_version": "1.0.0",
        "session_id": "session-001",
        "trace_id": "trace-001",
        "logical_tool_call_id": "call-001",
        "attempt_id": "attempt-001",
        "tool": "query_journal",
        "status": "ok",
        "started_at": "2026-11-05T09:00:00Z",
        "completed_at": "2026-11-05T09:00:01Z",
        "duration_ms": 1000,
        "data": {},
        "evidence": [],
        "error_code": None,
        "truncated": False,
        "next_cursor": None,
        "unknown_field": "test"
    }

    assert not common_validator.is_valid(response)


def test_invalid_error_code_is_rejected():
    response = {
        "schema_version": "1.0.0",
        "session_id": "session-001",
        "trace_id": "trace-001",
        "logical_tool_call_id": "call-001",
        "attempt_id": "attempt-001",
        "tool": "query_journal",
        "status": "ok",
        "started_at": "2026-11-05T09:00:00Z",
        "completed_at": "2026-11-05T09:00:01Z",
        "duration_ms": 1000,
        "data": {},
        "evidence": [],
        "error_code": "SOME_UNKNOWN_ERROR",
        "truncated": False,
        "next_cursor": None
    }

    assert not common_validator.is_valid(response)


def test_all_12_tool_response_schemas_exist():
    schema_files = list(SCHEMAS_DIR.glob("*.response.schema.json"))

    assert len(schema_files) == 12


def test_all_12_tool_response_schemas_are_valid():
    for tool_name in TOOL_NAMES:
        schema = load_tool_response_schema(tool_name)

        Draft202012Validator.check_schema(schema)


def test_all_12_tool_response_schemas_have_correct_names():
    for tool_name in TOOL_NAMES:
        schema = load_tool_response_schema(tool_name)

        assert schema["title"] == f"{tool_name} Response Data"
