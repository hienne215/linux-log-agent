"""Secondary, bounded credential filter; no I/O, execution or logging.

JSON is decoded recursively (including JSON-encoded strings), while free text
uses identifier/value parsing. Ambiguous sensitive structures fail closed.
This is not a universal secret detector; collector redaction is still required.
"""
import json
import math
import re
import shlex
import unicodedata

from .interface import AdapterError

_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_PRIVATE = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')
_FIELD = re.compile(
    r'''(?<![\w.-])(?P<key>"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|(?:--)?\w[\w.-]*)'''
    r'''(?P<separator>[ \t]*[:=][ \t]*|[ \t]+is[ \t]+)''', re.IGNORECASE)
_AUTH = re.compile(r'(?i)(\bauthorization[ \t]*:[ \t]*)(?:basic|bearer)[ \t]+[^\s,;]+')
_BEARER = re.compile(r'(?i)(\bbearer[ \t]+)[A-Za-z0-9._~+/=-]+')
_SHORT_P = re.compile(r'(?<!\S)-p(?=\S)')
_PASSWORD_COMMANDS = {'mysql', 'mariadb', 'mysqldump', 'mariadb-dump', 'mysqladmin'}
_NONPASSWORD_COMMANDS = {'curl', 'python', 'python3', 'pytest', 'grep', 'nginx', 'ssh',
                         'node', 'printf', 'server', 'docker', 'kubectl'}
_MAX_DEPTH = 16
_MAX_BYTES = 1024 * 1024


def _is_secret_key(key):
    """Classify complete identifiers, not a word-boundary suffix in the source."""
    normalized = unicodedata.normalize('NFKC', _CONTROL.sub('', key))
    normalized = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', normalized)
    normalized = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', normalized).casefold()
    parts = re.findall(r'[^\W_]+', normalized)
    if set(parts) & {'password', 'passwd', 'secret', 'token', 'authorization'}:
        return True
    compact = ''.join(parts)
    return ('apikey' in compact or compact.endswith(
        ('password', 'passwd', 'secret', 'secretkey', 'token')))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError()


_DECODER = json.JSONDecoder(object_pairs_hook=_unique, parse_constant=_invalid_constant)


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _walk(value, depth):
    if depth > _MAX_DEPTH:
        raise AdapterError('DATA_POLICY')
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            # Do not rewrite a data-bearing key into a different identifier.
            if _clean(key, depth + 1) != key:
                raise AdapterError('DATA_POLICY')
            if _is_secret_key(key) and child is not None and child != '':
                result[key] = '[REDACTED]'
            else:
                result[key] = _walk(child, depth + 1)
        return result
    if isinstance(value, list):
        return [_walk(child, depth + 1) for child in value]
    if isinstance(value, str):
        return _clean(value, depth + 1)
    if isinstance(value, float) and not math.isfinite(value):
        raise AdapterError('DATA_POLICY')
    return value


def _json_candidate(text):
    if text.startswith('{'):
        return True
    if text.startswith('['):
        tail = text[1:].lstrip()
        return bool(tail) and (tail[0] in '{["-]0123456789' or tail.startswith(('true', 'false', 'null')))
    return False


def _value_end(text, start):
    """Consume a complete quoted/escaped scalar without crossing an empty line."""
    if start >= len(text) or text[start] in '\r\n,;}]':
        return start, ''
    quote = text[start] if text[start] in ('"', "'") else ''
    index = start + bool(quote)
    while index < len(text):
        char = text[index]
        if char == '\\':
            if index + 1 >= len(text):
                raise AdapterError('DATA_POLICY')
            index += 2
            continue
        if quote and char == quote:
            return index + 1, quote
        if not quote and (char.isspace() or char in ',;}]'):
            return index, ''
        index += 1
    if quote:
        raise AdapterError('DATA_POLICY')
    return index, ''


def _short_passwords(text):
    lines = text.splitlines(keepends=True)
    for number, line in enumerate(lines):
        if not _SHORT_P.search(line):
            continue
        try:
            tokens = shlex.split(line, posix=True)
        except ValueError:
            raise AdapterError('DATA_POLICY') from None
        if tokens and tokens[0] == '$':
            tokens = tokens[1:]
        command = tokens[0].rsplit('/', 1)[-1] if tokens else ''
        if command in _NONPASSWORD_COMMANDS:
            continue
        if command not in _PASSWORD_COMMANDS or any(x in line for x in (';', '|', '&', '`', '$(')):
            # A bare -pVALUE cannot be safely classified as password or port/etc.
            raise AdapterError('DATA_POLICY')
        chunks, cursor = [], 0
        for match in _SHORT_P.finditer(line):
            if match.start() < cursor:
                continue
            end, quote = _value_end(line, match.end())
            chunks.append(line[cursor:match.end()] + quote + '[REDACTED]' + quote)
            cursor = end
        chunks.append(line[cursor:])
        lines[number] = ''.join(chunks)
    return ''.join(lines)


def _field_key(token):
    if token.startswith('"'):
        try:
            return json.loads(token)
        except ValueError:
            raise AdapterError('DATA_POLICY') from None
    return token[1:-1] if token.startswith("'") else token


def _plain(text):
    text = _short_passwords(text)
    text = _AUTH.sub(lambda m: m.group(1) + '[REDACTED]', text)
    text = _BEARER.sub(lambda m: m.group(1) + '[REDACTED]', text)
    chunks, cursor = [], 0
    for match in _FIELD.finditer(text):
        if match.start() < cursor:
            continue
        key = _field_key(match.group('key'))
        if not _is_secret_key(key):
            continue
        start = match.end()
        end, quote = _value_end(text, start)
        if end == start or (quote and end == start + 2):
            continue  # Empty value: do not consume a following line/record.
        chunks.append(text[cursor:start] + quote + '[REDACTED]' + quote)
        cursor = end
    chunks.append(text[cursor:])
    return ''.join(chunks)


def _clean(text, depth=0):
    if not isinstance(text, str) or depth > _MAX_DEPTH:
        raise AdapterError('DATA_POLICY')
    try:
        if len(text.encode('utf-8')) > _MAX_BYTES:
            raise AdapterError('DATA_POLICY')
    except UnicodeError:
        raise AdapterError('DATA_POLICY') from None
    text = _CONTROL.sub('', text)
    if _PRIVATE.search(text):
        raise AdapterError('DATA_POLICY')
    stripped = text.lstrip()
    # Common escaped fragment with no outer JSON-string quotes.
    if stripped.startswith('{\\"') or stripped.startswith('[\\"'):
        try:
            decoded = json.loads('"' + stripped + '"')
            obj = _DECODER.decode(decoded)
            filtered = _walk(obj, depth + 1)
            return text if filtered == obj else _dump(_dump(filtered))[1:-1]
        except (ValueError, RecursionError):
            raise AdapterError('DATA_POLICY') from None
    if _json_candidate(stripped) or stripped.startswith('"'):
        try:
            obj, end = _DECODER.raw_decode(stripped)
        except (ValueError, RecursionError):
            if _json_candidate(stripped):
                raise AdapterError('DATA_POLICY') from None
        else:
            if not stripped[end:].strip():
                filtered = _walk(obj, depth + 1)
                return text if filtered == obj else _dump(filtered)
    # Escaped JSON fields embedded in a text log may lack object braces.
    # Decode only a valid JSON string representation, then re-escape the result.
    if '\\"' in text:
        try:
            decoded = json.loads('"' + text + '"')
        except (ValueError, RecursionError):
            if re.search(r'(?i)password|passwd|secret|token|api[_-]?key', text):
                raise AdapterError('DATA_POLICY') from None
        else:
            filtered = _clean(decoded, depth + 1)
            if filtered != decoded:
                return _dump(filtered)[1:-1]
    # JSON embedded in a log record: parse the full object before text matching,
    # including escaped keys; never truncate/regex-rewrite its internal syntax.
    chunks, cursor = [], 0
    for match in re.finditer(r'[\[{]', text):
        start = match.start()
        if start < cursor or not _json_candidate(text[start:]):
            continue
        try:
            obj, length = _DECODER.raw_decode(text[start:])
        except (ValueError, RecursionError):
            raise AdapterError('DATA_POLICY') from None
        head = text[cursor:start]
        preceding = list(_FIELD.finditer(head))
        sensitive_assignment = (preceding and preceding[-1].end() == len(head)
                                and _is_secret_key(_field_key(preceding[-1].group('key'))))
        filtered = _walk(obj, depth + 1)
        chunks.append(_plain(head))
        if sensitive_assignment:
            chunks.append('[REDACTED]')
        else:
            chunks.append(text[start:start+length] if filtered == obj else _dump(filtered))
        cursor = start + length
    chunks.append(_plain(text[cursor:]))
    return ''.join(chunks)
