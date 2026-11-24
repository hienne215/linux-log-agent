"""Bounded, internal literal matching; no source access or MCP response building.

Messages are untrusted, potentially sensitive input. Returned indices refer to
the original snapshot, not evidence IDs or authorization to publish its text.
"""

MAX_RECORDS = 200
MAX_MESSAGE_CHARS = 2048
MAX_QUERY_CHARS = 500
MAX_INPUT_BYTES = 65536


class LogSearchError(ValueError):
    """Static internal failure; codes are not CommonResponse error semantics."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _utf8_size(value, maximum):
    if type(value) is not str or len(value) > maximum:
        raise LogSearchError("INVALID_INPUT")
    try:
        return len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        raise LogSearchError("INVALID_INPUT") from None


def match_literal_indices(messages, query, *, limit=100):
    """Return matching snapshot indices in order, preserving duplicate records.

    Literal, case-sensitive Unicode matching is an internal primitive, not a
    decision about the eventual search_log endpoint's query language. Accept
    only materialized built-in lists/tuples; never consume unbounded iterators.
    Validate the entire batch before matching. Overflow rejects the entire
    result rather than silently truncating it or inventing a pagination cursor.
    This function performs no I/O, redaction, timestamp or permission checks.
    """
    if type(messages) not in (list, tuple):
        raise LogSearchError("INVALID_INPUT")
    if type(limit) is not int or not 1 <= limit <= MAX_RECORDS:
        raise LogSearchError("INVALID_INPUT")
    if len(messages) > MAX_RECORDS:
        raise LogSearchError("OUTPUT_LIMIT")
    size = _utf8_size(query, MAX_QUERY_CHARS)
    if not query:
        raise LogSearchError("INVALID_INPUT")
    # Snapshot once so returned indices correspond to what was validated.
    snapshot = tuple(messages)
    for message in snapshot:
        size += _utf8_size(message, MAX_MESSAGE_CHARS)
        if size > MAX_INPUT_BYTES:
            raise LogSearchError("OUTPUT_LIMIT")
    matches = tuple(i for i, message in enumerate(snapshot) if query in message)
    if len(matches) > limit:
        raise LogSearchError("OUTPUT_LIMIT")
    return matches
