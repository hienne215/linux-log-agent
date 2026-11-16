"""Synthetic parser fixtures only; no Linux, subprocess or reader access."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import pytest

from src.mcp_server.journal_parser import (
    JournalParseError, MAX_INPUT_BYTES, MAX_RECORD_BYTES, MAX_RECORDS,
    parse_journal_json,
)

ROOT = Path(__file__).resolve().parents[1]
RECORD = {"__REALTIME_TIMESTAMP": "1000001", "PRIORITY": "3", "MESSAGE": "synthetic event"}


def wire(record=None):
    return json.dumps(RECORD if record is None else record, ensure_ascii=False).encode("utf-8")


def test_valid_records_map_to_canonical_entries():
    rows = parse_journal_json(wire() + b'\n' + wire(dict(RECORD, PRIORITY='7', MESSAGE='Tiếng Việt 🌍')) + b'\n')
    assert rows[0] == dict(timestamp='1970-01-01T00:00:01.000001Z', priority=3, message='synthetic event')
    assert rows[1]['message'] == 'Tiếng Việt 🌍'
    data = json.loads((ROOT / 'schemas/tools/query_journal.response.schema.json').read_text())
    Draft202012Validator(data, format_checker=FormatChecker()).validate(dict(entries=rows, count=2, limit=2))


@pytest.mark.parametrize('priority', list('01234567'))
def test_all_priority_levels(priority):
    assert parse_journal_json(wire(dict(RECORD, PRIORITY=priority)))[0]['priority'] == int(priority)


@pytest.mark.parametrize('missing', list(RECORD))
def test_missing_required_field(missing):
    record = dict(RECORD)
    del record[missing]
    with pytest.raises(JournalParseError):
        parse_journal_json(wire(record))


@pytest.mark.parametrize('stamp', ['', '-1', '+1', '1.5', ' 1', '1\n', '١', '2026-11-17T00:00:00Z',
                                   '253402300800000000', '9' * 100, 100, True, None, ['1']])
def test_invalid_timestamps(stamp):
    with pytest.raises(JournalParseError):
        parse_journal_json(wire(dict(RECORD, __REALTIME_TIMESTAMP=stamp)))


@pytest.mark.parametrize('stamp,expected', [('0', '1970-01-01T00:00:00.000000Z'),
    ('253402300799999999', '9999-12-31T23:59:59.999999Z')])
def test_timestamp_range_and_integer_microsecond_precision(stamp, expected):
    assert parse_journal_json(wire(dict(RECORD, __REALTIME_TIMESTAMP=stamp)))[0]['timestamp'] == expected


@pytest.mark.parametrize('priority', ['', '8', '-1', '03', '3 ', 3, True, None, ['3']])
def test_invalid_priority(priority):
    with pytest.raises(JournalParseError):
        parse_journal_json(wire(dict(RECORD, PRIORITY=priority)))


@pytest.mark.parametrize('message', [None, 1, ['text'], [255], {'value': 'text'}])
def test_unsupported_message_representation(message):
    with pytest.raises(JournalParseError):
        parse_journal_json(wire(dict(RECORD, MESSAGE=message)))


@pytest.mark.parametrize('payload', [b'\xff', b'{', b'[]', b'null', b'"text"', b'\n',
    wire() + b'\n\n' + wire(), wire()[:-1] + b',"MESSAGE":"second"}',
    wire()[:-1] + b',"EXTRA":NaN}', b'{"MESSAGE":"\\ud800"}',
    b'{"__REALTIME_TIMESTAMP":"1","PRIORITY":"3","MESSAGE":"\\udfff"}'])
def test_malformed_wire_and_ambiguous_json(payload):
    with pytest.raises(JournalParseError):
        parse_journal_json(payload)


def test_empty_is_only_a_parse_result_not_a_success_claim():
    assert parse_journal_json(b'') == []


def test_extra_metadata_is_not_forwarded_and_no_redaction_is_claimed():
    marker = 'SYNTHETIC_UNTRUSTED_SECRET'
    rows = parse_journal_json(wire(dict(RECORD, MESSAGE=marker, _CMDLINE='not forwarded', __CURSOR='opaque')))
    assert rows[0]['message'] == marker  # Policy is deliberately outside this parser.
    assert set(rows[0]) == {'timestamp', 'priority', 'message'}


@pytest.mark.parametrize('payload', [b'x' * (MAX_INPUT_BYTES + 1),
    wire(dict(RECORD, extra='x' * MAX_RECORD_BYTES)),
    b'\n'.join([wire()] * (MAX_RECORDS + 1)), wire(dict(RECORD, MESSAGE='x' * 2049))])
def test_resource_bounds_reject_without_slicing(payload):
    with pytest.raises(JournalParseError):
        parse_journal_json(payload)


def test_record_and_message_boundaries():
    assert len(parse_journal_json(b'\n'.join([wire()] * MAX_RECORDS))) == MAX_RECORDS
    assert len(parse_journal_json(wire(dict(RECORD, MESSAGE='🌍' * 2048)))) == 1
    record = dict(RECORD, extra='')
    record['extra'] = 'x' * (MAX_RECORD_BYTES - len(wire(record)))
    assert len(wire(record)) == MAX_RECORD_BYTES
    assert len(parse_journal_json(wire(record))) == 1


def test_bad_tail_rejects_entire_batch_and_errors_do_not_echo_content(capsys):
    marker = b'SYNTHETIC_SENSITIVE_MARKER'
    with pytest.raises(JournalParseError) as caught:
        parse_journal_json(wire() + b'\n' + marker)
    assert marker.decode() not in str(caught.value)
    assert caught.value.__suppress_context__
    assert capsys.readouterr() == ('', '')


def test_non_bytes_input():
    with pytest.raises(JournalParseError):
        parse_journal_json('text')


@pytest.mark.parametrize('extra', [
    b'1e999', b'-1e999', b'"\\ud800"', b'"\\udfff"',
    b'{"nested":[0,{"value":1e999}]}',
    b'[{"value":-1e999}]',
    b'{"nested":[0,{"value":"\\ud800"}]}',
    b'[{"value":"\\udfff"}]',
    b'{"\\ud800":"value"}',
    b'[{"deep":{"\\udfff":"value"}}]',
])
def test_recursive_metadata_rejects_invalid_values(extra, capsys):
    # All required fields are valid; only EXTRA can cause this rejection.
    payload = wire()[:-1] + b',"EXTRA":' + extra + b'}'
    for batch in (payload, wire() + b'\n' + payload):
        with pytest.raises(JournalParseError, match=r'^Invalid journal record\.$') as caught:
            parse_journal_json(batch)
        assert caught.value.__suppress_context__
    assert capsys.readouterr() == ('', '')


def test_top_level_surrogate_key_is_rejected():
    payload = wire()[:-1] + b',"\\ud800":"value"}'
    with pytest.raises(JournalParseError, match=r'^Invalid journal record\.$'):
        parse_journal_json(payload)


@pytest.mark.parametrize('suffix', [
    b',"MESSAGE":"duplicate"}',
    b',"EXTRA":{"key":1,"key":2}}',
    b',"EXTRA":NaN}', b',"EXTRA":Infinity}', b',"EXTRA":-Infinity}',
    b',"EXTRA":[{"value":NaN}]}',
])
def test_duplicate_keys_and_constants_with_complete_record(suffix):
    payload = wire()[:-1] + suffix
    with pytest.raises(JournalParseError, match=r'^Invalid or oversized journal record\.$'):
        parse_journal_json(payload)


def test_valid_nested_metadata_is_accepted_but_not_forwarded():
    metadata = {'nested': [None, True, False, 42, -1, 1.25, 1e308,
                           {'Tiếng Việt 🌍': ['value', {'empty': ''}]}]}
    payload = wire(dict(RECORD, EXTRA=metadata))
    assert parse_journal_json(payload) == parse_journal_json(wire())
    # JSON escaped surrogate PAIRS are valid Unicode, not unpaired surrogates.
    escaped = wire()[:-1] + b',"EXTRA":{"\\ud83c\\udf0d":"\\ud83c\\udf0d"}}'
    assert parse_journal_json(escaped) == parse_journal_json(wire())
