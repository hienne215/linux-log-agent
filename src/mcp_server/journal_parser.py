"""Bounded parsing only: returned messages are UNTRUSTED and NOT redacted.

Not a collector or an MCP result builder. Never forward these entries to a client
until a separately reviewed access/redaction/provenance policy has been applied.
"""

from datetime import datetime, timedelta, timezone
import json
import re


MAX_INPUT_BYTES = 65536
MAX_RECORD_BYTES = 16384
MAX_RECORDS = 200
MAX_MESSAGE_CHARS = 2048
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MICROSECONDS = re.compile(r"[0-9]{1,18}\Z")


class JournalParseError(ValueError):
    """Static local parse failure; not an MCP error code or response semantics."""


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise JournalParseError("Invalid journal record.")
        value[key] = item
    return value


def _invalid_constant(_value):
    raise JournalParseError("Invalid journal record.")


def parse_journal_json(payload: bytes) -> list[dict]:
    """Parse journalctl JSON lines into canonical entry-shaped, untrusted data.

    Accept only scalar timestamp/priority/message fields. Binary, null or repeated
    field representations are unsupported and rejected rather than guessed.
    Empty bytes produce an empty list, making no claim about reader permissions,
    coverage or absence of events. Any invalid/oversized record rejects the batch;
    nothing is silently skipped, truncated, printed or assigned an MCP status.
    """
    if not isinstance(payload, bytes):
        raise JournalParseError("Journal input must be bytes.")
    if len(payload) > MAX_INPUT_BYTES:
        raise JournalParseError("Journal input exceeds parser bounds.")
    lines = payload.split(b"\n")
    if lines[-1] == b"":
        lines.pop()  # Optional final newline; interior blank records are invalid.
    if len(lines) > MAX_RECORDS:
        raise JournalParseError("Journal input exceeds parser bounds.")
    entries = []
    for line in lines:
        if len(line) > MAX_RECORD_BYTES:
            raise JournalParseError("Journal record exceeds parser bounds.")
        try:
            record = json.loads(line.decode("utf-8", errors="strict"),
                                object_pairs_hook=_object,
                                parse_constant=_invalid_constant)
            if not isinstance(record, dict):
                raise ValueError
            stamp = record["__REALTIME_TIMESTAMP"]
            priority = record["PRIORITY"]
            message = record["MESSAGE"]
            if not isinstance(stamp, str) or not _MICROSECONDS.fullmatch(stamp):
                raise ValueError
            if not isinstance(priority, str) or priority not in tuple("01234567"):
                raise ValueError
            if not isinstance(message, str):
                raise ValueError
            # Reject unpaired JSON surrogates as well as invalid wire UTF-8.
            message.encode("utf-8", errors="strict")
            if len(message) > MAX_MESSAGE_CHARS:
                raise JournalParseError("Journal message exceeds parser bounds.")
            observed = _EPOCH + timedelta(microseconds=int(stamp))
            timestamp = observed.isoformat(timespec="microseconds").replace("+00:00", "Z")
        except JournalParseError:
            raise JournalParseError("Invalid or oversized journal record.") from None
        except (ValueError, KeyError, TypeError, OverflowError, RecursionError):
            raise JournalParseError("Invalid journal record.") from None
        entries.append({"timestamp": timestamp, "priority": int(priority), "message": message})
    return entries
