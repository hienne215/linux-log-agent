import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas" / "tools"

COMMON_REQUEST = {
    "schema_version": "1.0.0",
    "session_id": "session-001",
    "trace_id": "trace-001"
}

TEST_CASES = [
    (
        "query_journal",
        {
            **COMMON_REQUEST,
            "unit": "nginx.service",
            "since": "2026-11-01T00:00:00Z",
            "until": "2026-11-02T00:00:00Z",
            "priority": 3,
            "limit": 100
        }
    ),
    (
        "search_log",
        {
            **COMMON_REQUEST,
            "source_id": "syslog-main",
            "query": "connection failed",
            "since": "2026-11-01T00:00:00Z",
            "until": "2026-11-02T00:00:00Z",
            "limit": 50
        }
    ),
    (
        "service_status",
        {
            **COMMON_REQUEST,
            "unit": "nginx.service"
        }
    ),
    (
        "list_failed_units",
        {
            **COMMON_REQUEST,
            "limit": 100
        }
    ),
    (
        "disk_usage",
        {
            **COMMON_REQUEST,
            "filesystem": "/",
            "include_inodes": True
        },
    ),
        (
        "listening_ports",
        {
            **COMMON_REQUEST,
            "protocol": "tcp",
            "port": 8080,
        },
    ),
    (
        "check_config",
        {
            **COMMON_REQUEST,
            "profile": "ssh",
        },
    ),
]


def load_validator(tool_name):
    schema_path = SCHEMA_DIR / f"{tool_name}.schema.json"
    common_path = ROOT / "schemas" / "common-request.schema.json"

    with schema_path.open(encoding="utf-8") as file:
        schema = json.load(file)

    with common_path.open(encoding="utf-8") as file:
        common_schema = json.load(file)

    # Replace the shared schema reference with its actual content.
    for item in schema.get("allOf", []):
        if item.get("$ref") == "../common-request.schema.json":
            item.clear()
            item.update(common_schema)

    Draft202012Validator.check_schema(schema)

    return Draft202012Validator(
        schema,
        format_checker=FormatChecker()
    )


@pytest.mark.parametrize("tool_name, payload", TEST_CASES)
def test_valid_request(tool_name, payload):
    validator = load_validator(tool_name)

    errors = list(validator.iter_errors(payload))

    assert errors == [], [
        error.message for error in errors
    ]




@pytest.mark.parametrize("tool_name, payload", TEST_CASES)
def test_missing_session_id_is_rejected(tool_name, payload):
    validator = load_validator(tool_name)

    invalid_request = payload.copy()
    invalid_request.pop("session_id")

    assert not validator.is_valid(invalid_request)


@pytest.mark.parametrize("tool_name, payload", TEST_CASES)
def test_unknown_field_is_rejected(tool_name, payload):
    validator = load_validator(tool_name)

    invalid_request = {
        **payload,
        "unexpected_field": "not allowed"
    }

    assert not validator.is_valid(invalid_request)


def test_query_journal_rejects_invalid_priority():
    validator = load_validator("query_journal")

    invalid_request = {
        **COMMON_REQUEST,
        "unit": "nginx.service",
        "since": "2026-11-01T00:00:00Z",
        "until": "2026-11-02T00:00:00Z",
        "priority": 10
    }

    assert not validator.is_valid(invalid_request)


def test_service_status_rejects_invalid_unit():
    validator = load_validator("service_status")

    invalid_request = {
        **COMMON_REQUEST,
        "unit": "nginx"
    }

    assert not validator.is_valid(invalid_request)
def test_listening_ports_rejects_invalid_port():
    validator = load_validator("listening_ports")

    request = {
        **COMMON_REQUEST,
        "port": 70000,
    }

    assert not validator.is_valid(request)


def test_check_config_requires_profile():
    validator = load_validator("check_config")

    request = {
        **COMMON_REQUEST,
    }

    assert not validator.is_valid(request)