"""Synthetic snapshots only. These functions are NOT production adapters."""
import json

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from scripts.p01_02_offline_audit import CONTRACT_SHA, load_cores, read_revision

parser, matcher = load_cores()


def record(message="fixture error", stamp="1000001", priority="3", **metadata):
    return dict(__REALTIME_TIMESTAMP=stamp, PRIORITY=priority,
                MESSAGE=message, **metadata)


def wire(*records):
    return b"\n".join(json.dumps(r, ensure_ascii=False).encode("utf-8") for r in records)


def compose(payload, query="error", limit=100):
    entries = parser.parse_journal_json(payload)
    indices = matcher.match_literal_indices([e["message"] for e in entries], query, limit=limit)
    return entries, indices


def validate(tool, kind, value):
    suffix = ".response" if kind == "data" else ""
    schema = json.loads(read_revision(CONTRACT_SHA, f"schemas/tools/{tool}{suffix}.schema.json"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validator.check_schema(schema)
    validator.validate(value)


def test_duplicates_original_order_and_fixture_timestamps_are_preserved():
    payload = wire(record(stamp="3000003"), record("ok", "2000002"),
                   record(stamp="3000003"), record("later error", "1000001"))
    entries, indices = compose(payload)
    assert indices == (0, 2, 3)
    assert entries[0] == entries[2]
    assert entries[0] is not entries[2]
    assert [entries[i]["timestamp"] for i in indices] == [
        "1970-01-01T00:00:03.000003Z", "1970-01-01T00:00:03.000003Z",
        "1970-01-01T00:00:01.000001Z"]
    assert compose(payload) == (entries, indices)


@pytest.mark.parametrize("payload", [
    b"{", b"\xff", wire(record()) + b"\n{", wire(record()) + b"\n\n",
    wire(record()) + b'\n{"PRIORITY":"3","MESSAGE":"missing timestamp"}',
    wire(record()) + b'\n{"__REALTIME_TIMESTAMP":"1","PRIORITY":"3",'
                     b'"MESSAGE":"error","EXTRA":1e999}',
    wire(record()) + b'\n{"__REALTIME_TIMESTAMP":"1","PRIORITY":"3",'
                     b'"MESSAGE":"error","MESSAGE":"duplicate"}',
])
def test_bad_record_rejects_before_matcher_runs(payload, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("matcher must not run after parse rejection")
    monkeypatch.setattr(matcher, "match_literal_indices", forbidden)
    with pytest.raises(parser.JournalParseError, match=r"^Invalid"):
        compose(payload)


@pytest.mark.parametrize("payload", [
    b"x" * 65537, wire(*[record()] * 201),
    wire(record("x" * 2049)), wire(record(EXTRA="x" * 16384)),
])
def test_parser_caps_reject_whole_composition(payload):
    with pytest.raises(parser.JournalParseError, match=r"bounds|Invalid or oversized"):
        compose(payload)


def test_result_overflow_does_not_produce_partial_projection():
    with pytest.raises(matcher.LogSearchError, match="^OUTPUT_LIMIT$"):
        compose(wire(record(), record()), limit=1)


@pytest.mark.parametrize("query", ["", None, "x" * 501, "\ud800"])
def test_matcher_rejection_propagates_without_a_result(query):
    with pytest.raises(matcher.LogSearchError, match="^INVALID_INPUT$"):
        compose(wire(record()), query=query)


@pytest.mark.parametrize("payload", [b"", wire(record("no match"))])
def test_empty_match_is_only_an_internal_value(payload):
    entries, indices = compose(payload)
    assert indices == ()
    assert isinstance(entries, list)  # No status, permissions or coverage claim.


@pytest.mark.parametrize("query", ["(a+)+$", "[", "Tiếng Việt 🌍", "error"])
def test_literal_unicode_and_case_sensitivity_through_parser(query):
    entries, indices = compose(wire(record(query), record("ERROR")), query=query)
    assert indices == (0,)
    assert entries[indices[0]]["message"] == query


def test_largest_record_count_composes_without_deduplication():
    entries, indices = compose(wire(*[record()] * 200), limit=200)
    assert len(entries) == 200
    assert indices == tuple(range(200))


def test_no_redaction_or_source_provenance_is_invented(capsys):
    marker = "SYNTHETIC_UNTRUSTED_SECRET"
    entries, indices = compose(wire(record(marker, _SYSTEMD_UNIT="unverified.service",
                                            __CURSOR="unverified-cursor")), query=marker)
    assert indices == (0,)
    assert entries[0]["message"] == marker  # NOT safe to publish.
    assert set(entries[0]) == {"timestamp", "priority", "message"}
    assert capsys.readouterr() == ("", "")


def test_both_canonical_fixture_projections_and_requests():
    entries, indices = compose(wire(record(), record("ok")))
    validate("query_journal", "data", dict(entries=entries, count=2, limit=100))
    # Search entries cannot retain priority: additionalProperties=false.
    with pytest.raises(ValidationError):
        validate("search_log", "data", dict(source_id="synthetic.fixture.only", query="error",
                                             matches=[entries[i] for i in indices],
                                             count=len(indices), limit=100))
    matches = [{k: entries[i][k] for k in ("timestamp", "message")} for i in indices]
    validate("search_log", "data", dict(source_id="synthetic.fixture.only", query="error",
                                         matches=matches, count=len(matches), limit=100))
    metadata = dict(schema_version="1.0.0", session_id="fixture", trace_id="fixture",
                    logical_tool_call_id="fixture", attempt_id="fixture")
    window = dict(since="1970-01-01T00:00:01Z", until="1970-01-01T00:00:02Z")
    validate("query_journal", "request", dict(metadata, **window, unit="fixture.service"))
    validate("search_log", "request", dict(metadata, **window,
                                            source_id="synthetic.fixture.only", query="error"))


def test_window_is_not_enforced_by_either_core():
    # Explicit capability gap: [1s,2s) filtering is not implemented here.
    entries, indices = compose(wire(*[record(stamp=t) for t in
                                      ("999999", "1000000", "1999999", "2000000")]))
    assert indices == (0, 1, 2, 3)
    assert [e["timestamp"] for e in entries] == [
        "1970-01-01T00:00:00.999999Z", "1970-01-01T00:00:01.000000Z",
        "1970-01-01T00:00:01.999999Z", "1970-01-01T00:00:02.000000Z"]
