"""Generate self-contained official SDK output schemas from canonical sources.

Run: python generate_result_schemas.py [--check]
"""
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent / 'schemas'


def generated(name):
    def load(path):
        return json.loads(path.read_text(encoding='utf-8'))
    schema = copy.deepcopy(load(ROOT / 'common-response.schema.json'))
    schema['title'] = f'{name} Structured Result'
    schema['properties']['tool'] = {'const': name}
    if name != 'restart_service':
        schema['properties']['status']['enum'].remove('approval_required')
    if name in {'memory_oom_events', 'failed_logins'}:
        sources = ['journal.kernel', 'dmesg'] if name == 'memory_oom_events' else ['journal.auth', 'auth.log']
        schema['allOf'].append({'properties': {'data': {'properties': {
            'coverage': {'items': {'properties': {'source': {'enum': sources}}}}
        }}}})
    schema['allOf'].append({
        'if': {'properties': {'status': {'const': 'error'}}, 'required': ['status']},
        'then': {'properties': {'data': load(ROOT / 'error-data.schema.json')}},
        'else': {'properties': {'data': load(ROOT / 'tools' / f'{name}.response.schema.json')}},
    })
    return schema


def main():
    stale = []
    for path in sorted((ROOT / 'tools').glob('*.response.schema.json')):
        name = path.name.removesuffix('.response.schema.json')
        target = path.with_name(f'{name}.result.schema.json')
        text = json.dumps(generated(name), indent=2, ensure_ascii=False) + '\n'
        if '--check' in sys.argv:
            if target.read_text(encoding='utf-8') != text:
                stale.append(name)
        else:
            target.write_text(text, encoding='utf-8')
    if stale:
        raise SystemExit('Stale result schemas: ' + ', '.join(stale))


if __name__ == '__main__':
    main()
