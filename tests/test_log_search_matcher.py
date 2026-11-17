"""Synthetic in-memory tests; no file/journal reader or Linux smoke."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from src.mcp_server.log_search_matcher import LogSearchError, match_literal_indices


@pytest.mark.parametrize("messages,query,expected", [
    ([], "error", ()),
    (["", "ok"], "error", ()),
    (["error", "ok", "error again"], "error", (0, 2)),
    (["Error", "error"], "error", (1,)),
    (["same", "same"], "same", (0, 1)),
    (["abc", "abcabc"], "abc", (0, 1)),
    (["a", "b"], "ab", ()),
    (["lỗi tiếng Việt", "không"], "lỗi", (0,)),
    (["😀", "x😀y"], "😀", (0, 1)),
    (["é", "e\u0301"], "é", (0,)),
    (["line\nnext\tfield"], "next\t", (0,)),
    (["/etc/passwd", "../private"], "../", (1,)),
    (["a" * 2048], "a" * 500, (0,)),
    (tuple(["ok"] * 200), "ok", tuple(range(200))),
])
def test_literal_order_and_snapshot_identity(messages, query, expected):
    before = list(messages)
    assert match_literal_indices(messages, query, limit=200) == expected
    assert list(messages) == before


@pytest.mark.parametrize("query", [".*", "(a+)+$", "[", "\\", "^$", "a|b"])
def test_regex_metacharacters_are_literal(query):
    assert match_literal_indices(["aaaa", query], query) == (1,)


@pytest.mark.parametrize("messages,query,limit", [
    (None, "x", 1), ("x", "x", 1), ({"x"}, "x", 1),
    (["x"], None, 1), (["x"], b"x", 1), (["x"], "", 1),
    (["x"], "x" * 501, 1), ([b"x"], "x", 1),
    ([None], "x", 1), (["x" * 2049], "x", 1),
    (["x"], "x", True), (["x"], "x", 0), (["x"], "x", 201),
    (["x"], "x", 1.0), (["x"], "x", "1"),
    (["\ud800"], "x", 1), (["\udfff"], "x", 1),
    (["x"], "\ud800", 1), (["x"], "\udfff", 1),
])
def test_invalid_input_is_specific_static_failure(messages, query, limit):
    with pytest.raises(LogSearchError) as caught:
        match_literal_indices(messages, query, limit=limit)
    assert caught.value.code == "INVALID_INPUT"
    assert str(caught.value) == "INVALID_INPUT"


def test_iterator_is_not_consumed():
    def unbounded():
        raise AssertionError("must not start consuming an arbitrary iterator")
        yield "x"
    with pytest.raises(LogSearchError, match="^INVALID_INPUT$"):
        match_literal_indices(unbounded(), "x")


@pytest.mark.parametrize("messages,query,limit", [
    (["x"] * 201, "x", 200),
    (["x", "x"], "x", 1),
    (["😀" * 2048] * 8, "x", 100),
])
def test_caps_reject_instead_of_silent_truncation(messages, query, limit):
    with pytest.raises(LogSearchError, match="^OUTPUT_LIMIT$"):
        match_literal_indices(messages, query, limit=limit)


def test_exact_byte_boundary_includes_query():
    messages = ["a" * 2048] * 31 + ["a" * 2047]
    assert len(match_literal_indices(messages, "a")) == 32
    with pytest.raises(LogSearchError, match="^OUTPUT_LIMIT$"):
        match_literal_indices(messages + ["b"], "a")


def test_invalid_late_record_rejects_whole_batch_after_matching_limit():
    with pytest.raises(LogSearchError, match="^INVALID_INPUT$"):
        match_literal_indices(["match", "unmatched\ud800"], "match", limit=1)


def test_untrusted_text_is_never_returned_or_printed(capsys):
    secret = "SYNTHETIC_SECRET_MARKER"
    assert match_literal_indices([secret], secret) == (0,)
    with pytest.raises(LogSearchError) as caught:
        match_literal_indices([secret, None], secret)
    assert secret not in str(caught.value)
    assert capsys.readouterr() == ("", "")


def test_indices_allow_exact_canonical_fixture_projection():
    # Test-only synthetic projection, NOT a production response or redaction.
    entries = [
        {"timestamp": "2026-11-17T00:00:00Z", "message": "ok"},
        {"timestamp": "2026-11-17T00:00:01Z", "message": "fixture error"},
    ]
    indices = match_literal_indices([e["message"] for e in entries], "error")
    data = dict(source_id="fixture", query="error",
                matches=[entries[i] for i in indices], count=len(indices), limit=100)
    schema = json.loads((Path(__file__).resolve().parents[1] /
                         "schemas/tools/search_log.response.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(data)
    assert data["matches"] == [entries[1]]
