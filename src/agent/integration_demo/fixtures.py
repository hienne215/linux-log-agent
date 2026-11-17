"""Scripted demo data. Not SCRUM-12 schemas, provenance or Linux observations."""
SCENARIOS = ('active', 'port-conflict', 'permission-denied')

TOOLS = {
    'service_status': 'Read synthetic service state',
    'listening_ports': 'Read synthetic port fixture',
    'query_journal': 'Read synthetic journal fixture',
}
PARAMETERS = {'type': 'object', 'properties': {'unit': {'const': 'nginx.service'}},
              'required': ['unit'], 'additionalProperties': False}


def steps(scenario):
    return ('service_status',) if scenario == 'active' else (
        'service_status', 'listening_ports' if scenario == 'port-conflict' else 'query_journal')


def observation(scenario, tool):
    if scenario not in SCENARIOS or tool not in steps(scenario):
        raise ValueError('FIXTURE_SCOPE')
    text = {
        'service_status': ('Fixture nginx.service active; HTTP health chưa được kiểm tra.'
                           if scenario == 'active' else 'Fixture nginx.service failed.'),
        'listening_ports': 'Fixture port 80 already in use; nginx bind failed.',
        'query_journal': 'Fixture PERMISSION_DENIED: không đủ quyền đọc journal.',
    }[tool]
    return {'fixture': 'integration-demo-v1', 'synthetic': True,
            'scenario': scenario, 'tool': tool,
            'limited': tool == 'query_journal',
            'evidence': [{'id': scenario + '-' + tool,
                          'source': 'synthetic:integration-demo-v1/' + scenario + '/' + tool,
                          'text': text}]}


def conclusion(scenario):
    return {
        'active': 'Fixture cho thấy Nginx active; chưa xác minh HTTP health hoặc Linux thật.',
        'port-conflict': 'Trong fixture, Nginx failed và có dấu hiệu xung đột cổng 80; chưa xác minh Linux thật.',
        'permission-denied': 'Không đủ bằng chứng; fixture không cho phép đọc journal.',
    }[scenario]
