import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "common-response.schema.json"


with SCHEMA_PATH.open(encoding="utf-8") as file:
    SCHEMA = json.load(file)


validator = Draft202012Validator(
    SCHEMA,
    format_checker=FormatChecker()
)


VALID_RESPONSE = {
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

def test_valid_response():
    errors = list(validator.iter_errors(VALID_RESPONSE))

    for error in errors:
        print(f"Field: {list(error.absolute_path)}")
        print(f"Error: {error.message}")

    assert not errors


def test_missing_trace_id_is_rejected():
    response = VALID_RESPONSE.copy()
    response.pop("trace_id")

    assert not validator.is_valid(response)


def test_invalid_status_is_rejected():
    response = VALID_RESPONSE.copy()
    response["status"] = "success"

    assert not validator.is_valid(response)


def test_unknown_field_is_rejected():
    response = VALID_RESPONSE.copy()
    response["unknown_field"] = "test"

    assert not validator.is_valid(response)


def test_invalid_error_code_is_rejected():
    response = VALID_RESPONSE.copy()
    response["error_code"] = "SOME_UNKNOWN_ERROR"

    assert not validator.is_valid(response)