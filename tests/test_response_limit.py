
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

import mock_mcp_server


ROOT = Path(__file__).resolve().parents[1]


def load_response_validator():
    schema_file = ROOT / "schemas" / "common-response.schema.json"

    with schema_file.open(encoding="utf-8") as file:
        schema = json.load(file)

    return Draft202012Validator(
        schema,
        format_checker=FormatChecker()
    )


def create_response(data):
    return mock_mcp_server.make_response(
        tool="service_status",
        trace_id="trace-test",
        logical_tool_call_id="call-test",
        attempt_id="attempt-test",
        session_id="session-test",
        data=data,
    )


def test_response_under_64_kib_preserves_data():
    data = {"message": "Small response"}

    response = create_response(data)

    assert response["status"] == "ok"
    assert response["data"] == data
    assert response["truncated"] is False
    assert response["error_code"] is None

    serialized = json.dumps(
        response,
        ensure_ascii=False
    ).encode("utf-8")

    assert len(serialized) <= mock_mcp_server.MAX_RESPONSE_BYTES

    load_response_validator().validate(response)


def test_response_over_64_kib_returns_output_limit():
    data = {"message": "A" * (70 * 1024)}

    response = create_response(data)

    assert response["status"] == "error"
    assert response["error_code"] == "OUTPUT_LIMIT"
    assert response["truncated"] is True
    assert response["data"]["max_response_bytes"] == 64 * 1024
    assert response["evidence"] == []
    assert response["next_cursor"] is None

    serialized = json.dumps(
        response,
        ensure_ascii=False
    ).encode("utf-8")

    assert len(serialized) <= mock_mcp_server.MAX_RESPONSE_BYTES

    load_response_validator().validate(response)


def test_response_limit_counts_utf8_bytes():
    data = {"message": "Thủy" * 15_000}

    response = create_response(data)

    assert response["status"] == "error"
    assert response["error_code"] == "OUTPUT_LIMIT"
    assert response["truncated"] is True

    serialized = json.dumps(
        response,
        ensure_ascii=False
    ).encode("utf-8")

    assert len(serialized) <= mock_mcp_server.MAX_RESPONSE_BYTES

    load_response_validator().validate(response)
