"""Pure, bounded service evidence encoding; no I/O or wire-schema changes."""
import re

DETAILS = ('exec_main_code', 'exec_main_status', 'restart_count')
BASE = ('unit', 'load', 'active', 'sub')


def numeric(value, *, maximum=2**32-1):
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('INVALID_SERVICE_DETAILS')
    return value


def format_snapshot(data, details=None):
    text = (f"unit={data['unit']}; load={data['load_state']}; "
            f"active={data['active_state']}; sub={data['sub_state']}")
    if details is not None:
        if type(details) is not dict or set(details) != set(DETAILS):
            raise ValueError('INVALID_SERVICE_DETAILS')
        for key in DETAILS:
            value = numeric(details[key], maximum=6 if key == 'exec_main_code' else 2**32-1)
            text += '; ' + key + '=' + ('null' if value is None else str(value))
    return text


def parse_snapshot(text, unit):
    if type(text) is not str or len(text) > 1024:
        raise ValueError('INVALID_SERVICE_SNAPSHOT')
    fields = {}
    for item in text.split('; '):
        key, value = item.split('=', 1)
        if key in fields:
            raise ValueError('INVALID_SERVICE_SNAPSHOT')
        fields[key] = value
    if set(fields) not in (set(BASE), set(BASE) | set(DETAILS)) or fields['unit'] != unit:
        raise ValueError('INVALID_SERVICE_SNAPSHOT')
    if any(re.fullmatch('[a-z-]+',fields[k]) is None for k in ('load','active','sub')):
        raise ValueError('INVALID_SERVICE_SNAPSHOT')
    data = dict(unit=unit,load_state=fields['load'],active_state=fields['active'],sub_state=fields['sub'])
    details = None
    if set(DETAILS) <= set(fields):
        details = {}
        for key in DETAILS:
            value = fields[key]
            if value != 'null' and re.fullmatch('0|[1-9][0-9]{0,9}',value) is None:
                raise ValueError('INVALID_SERVICE_DETAILS')
            details[key] = numeric(None if value == 'null' else int(value),
                                   maximum=6 if key == 'exec_main_code' else 2**32-1)
    if format_snapshot(data,details) != text:
        raise ValueError('INVALID_SERVICE_SNAPSHOT')
    return data, details
