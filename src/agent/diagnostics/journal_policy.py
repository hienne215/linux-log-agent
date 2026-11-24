"""Application-owned bounded nginx journal scope; no private raw text projection."""
from datetime import datetime, timedelta, timezone

UNIT = 'nginx.service'
SOURCE_ID = 'nginx_journal'
LIMIT = 100
MARKERS = ('error', 'failed', 'permission denied', 'address already in use')
PREFIX = 'Message withheld; literal markers='


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def parse_stamp(value):
    if type(value) is not str or not value.endswith('Z'):
        raise ValueError()
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() != timedelta(0):
        raise ValueError()
    return parsed


def window(minutes=15):
    if type(minutes) is not int or not 1<=minutes<=1440:raise ValueError()
    end = datetime.now(timezone.utc)
    return {'since': stamp(end-timedelta(minutes=minutes)), 'until': stamp(end)}


def valid_window(arguments, now=None, *, minutes=15):
    try:
        if type(minutes) is not int or minutes not in (15,30,1440):
            return False
        start, end = parse_stamp(arguments['since']), parse_stamp(arguments['until'])
        now = now or datetime.now(timezone.utc)
        return (timedelta(0) < end-start <= timedelta(minutes=minutes)
                and now-timedelta(minutes=minutes+1) <= start <= end <= now+timedelta(seconds=5))
    except (ValueError, KeyError, TypeError, OverflowError):
        return False


def project_message(text):
    # Never send arbitrary raw substrings, identifiers, IPs, credentials or log
    # instructions across worker IPC. These labels mean literal matches only.
    found = [marker for marker in MARKERS if marker in text.casefold()]
    return PREFIX + (','.join(found) if found else 'none')


def safe_message(text):
    if type(text) is not str or not text.startswith(PREFIX):
        return False
    values = text[len(PREFIX):].split(',')
    return values == ['none'] or (bool(values) and len(values) == len(set(values))
        and all(v in MARKERS for v in values) and values == [v for v in MARKERS if v in values])


def marker_counts(records):
    counts = dict.fromkeys(MARKERS, 0)
    for entry in records:
        text = entry['message']
        if not safe_message(text):
            raise ValueError()
        present = text[len(PREFIX):].split(',')
        for marker in MARKERS:
            counts[marker] += marker in present
    return counts
