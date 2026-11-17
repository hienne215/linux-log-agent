"""P03-03: conservative login parser and privacy boundary (Python 3.8+).

Raw messages, usernames and IPs exist only inside this module while parsing.
No shell, subprocess, host login attempts or account lookup occurs here.
"""
import hashlib
import hmac
import ipaddress
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

UTC = timezone.utc
SOURCES = ('journal.auth', 'auth.log')
SERVICES = ('sshd', 'sudo', 'login', 'su')
MAX_LINE = 8192
SSH = re.compile(r'^Failed (password|publickey|keyboard-interactive/pam) for '
                 r'(invalid user )?(\S{1,128}) from (\S{1,128}) port ([0-9]{1,5})(?: ssh2)?$')
PAM = re.compile(r'^pam_unix\((sudo|login|su):auth\): authentication failure;(.{0,2048})$')
ISO = re.compile(r'^(\d{4}-\d\d-\d\dT\S+) \S+ (sshd|sudo|login|su)\[(\d+)\]: (.*)$')
BSD = re.compile(r'^([A-Z][a-z]{2} +\d{1,2} \d\d:\d\d:\d\d) \S+ '
                 r'(sshd|sudo|login|su)\[(\d+)\]: (.*)$')


def parse_time(value):
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError('Invalid time')
    result = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result.astimezone(UTC)


def timestamp(value):
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


class Privacy:
    """Operator-supplied secret and evaluation scope; never obtained from MCP.

    Retaining the same secret/scope preserves correlation across restarts.
    Different scopes deliberately unlink IP pseudonyms. No reverse map is kept.
    """
    def __init__(self, key, scope):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError('At least 32 secret bytes required')
        if not isinstance(scope, str) or not 1 <= len(scope) <= 128:
            raise ValueError('Evaluation scope required')
        self._key, self._scope = key, scope

    def digest(self, kind, value):
        payload = json.dumps([self._scope, kind, value], ensure_ascii=True,
                             separators=(',', ':')).encode('ascii')
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def ip(self, value):
        if not value:
            return None
        try:
            # A zone identifier is an interface name, not part of a public IP.
            if '%' in value:
                return None
            canonical = str(ipaddress.ip_address(value))
        except ValueError:
            return None
        return 'ip-' + self.digest('ip', canonical)[:32]


@dataclass(frozen=True)
class Event:
    time: datetime
    pid: int
    service: str
    user_class: str
    source_ip: object
    method: str
    identity: str

    def public(self):
        return dict(timestamp=timestamp(self.time), user='[REDACTED]',
                    source_ip=self.source_ip, service=self.service,
                    user_class=self.user_class, evidence_id='login-' + self.identity[:32],
                    message='Failed %s authentication (%s).' % (self.service, self.method))


def parse_message(message, service, pid, when, privacy, record_id):
    """Accept only recognized failure records, reconstruct output without raw text.

    sshd's PAM companion is intentionally ignored; its Failed ... line is the
    authoritative event. PAM-only sshd failures are not supported in this version.
    """
    if (not isinstance(message, str) or len(message) > MAX_LINE or
            any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in message) or
            service not in SERVICES or when.tzinfo is None):
        return None
    username, raw_ip, kind, user_class = '', '', '', 'unknown'
    match = SSH.fullmatch(message) if service == 'sshd' else None
    if match:
        kind, invalid, username, raw_ip, port = match.groups()
        if not 1 <= int(port) <= 65535 or privacy.ip(raw_ip) is None:
            return None
        user_class = 'unknown' if invalid else ('root' if username == 'root' else 'existing')
    else:
        match = PAM.fullmatch(message)
        if not match or match.group(1) != service:
            return None
        fields = dict(re.findall(r'(?:^|\s)(user|rhost)=([^\s;]*)', match.group(2)))
        username, raw_ip = fields.get('user', ''), fields.get('rhost', '')
        kind = 'pam'
        user_class = 'root' if username == 'root' else 'unknown'
    identity = privacy.digest('event', [record_id, timestamp(when), pid, service])
    return Event(when, pid, service, user_class, privacy.ip(raw_ip), kind, identity)


def parse_journal(lines, privacy):
    events, seen, incomplete = [], set(), False
    for line in lines:
        if not isinstance(line, str) or len(line) > MAX_LINE:
            incomplete = True
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError()
            # A journal cursor is the record identity, not a caller pagination token.
            cursor = record['__CURSOR']
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048:
                raise ValueError()
            when = datetime.fromtimestamp(int(record['__REALTIME_TIMESTAMP']) / 1000000, UTC)
            service = record.get('SYSLOG_IDENTIFIER', '')
            pid = int(record.get('_PID', record.get('SYSLOG_PID', 0)))
            message = record.get('MESSAGE')
        except (ValueError, TypeError, KeyError, OverflowError, OSError):
            incomplete = True
            continue
        if cursor in seen:
            continue
        seen.add(cursor)
        if not isinstance(message, str):
            incomplete = True
            continue
        event = parse_message(message, service, pid, when, privacy, ['journal', cursor])
        if event:
            events.append(event)
        elif 'failed' in message.lower() or 'failure' in message.lower():
            # Includes unsupported PAM-only sshd forms. Never claim complete coverage.
            incomplete = True
    return events, incomplete


def parse_auth(lines, privacy, year, local_tz, record_ids=None):
    """RFC3339 syslog or traditional syslog, trusted year/timezone supplied locally.

    Traditional yearless records always mark the snapshot incomplete. The caller
    must choose a year per rotated file; no silent year/host-timezone guessing.
    """
    events, incomplete = [], False
    for index, line in enumerate(lines):
        line = line.rstrip('\r\n')
        if len(line) > MAX_LINE:
            incomplete = True
            continue
        match = ISO.fullmatch(line)
        try:
            if match:
                when = parse_time(match.group(1))
            else:
                match = BSD.fullmatch(line)
                if not match:
                    incomplete = True
                    continue
                when = datetime.strptime(str(year) + ' ' + match.group(1), '%Y %b %d %H:%M:%S')
                when = when.replace(tzinfo=local_tz).astimezone(UTC)
                incomplete = True
            service, pid, message = match.group(2), int(match.group(3)), match.group(4)
        except (ValueError, OverflowError):
            incomplete = True
            continue
        record_id = record_ids[index] if record_ids is not None else [index, line]
        event = parse_message(message, service, pid, when, privacy, ['auth', record_id])
        if event:
            events.append(event)
        elif 'failed' in message.lower() or 'failure' in message.lower():
            incomplete = True
    return events, incomplete


def aggregate(events):
    """Local analysis helper, not an extra wire field in the canonical schema."""
    return dict(Counter(event.source_ip for event in events))
