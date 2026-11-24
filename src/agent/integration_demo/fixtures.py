"""Scripted demo data. Not SCRUM-12 schemas, provenance or Linux observations."""
SCENARIOS = ('active', 'port-conflict', 'permission-denied', 'crash-loop', 'config-error', 'backend-502')

TOOLS = {
    'service_status': 'Read synthetic service state',
    'listening_ports': 'Read synthetic port fixture',
    'query_journal': 'Read synthetic journal fixture',
    'check_config': 'Read synthetic nginx syntax-validation fixture; never nginx -t',
    'verify_fix': 'Read synthetic loopback HTTP fixture; no real HTTP',
}
PARAMETERS = {'type': 'object', 'properties': {'unit': {'const': 'nginx.service'}},
              'required': ['unit'], 'additionalProperties': False}


def parameters(tool):
    if tool == 'check_config':
        return {'type':'object','properties':{'profile':{'const':'nginx'}},
                'required':['profile'],'additionalProperties':False}
    return PARAMETERS


def arguments(tool):
    return {'profile':'nginx'} if tool == 'check_config' else {'unit':'nginx.service'}


def steps(scenario):
    sequences = {
        'active': ('service_status',),
        'port-conflict': ('service_status','listening_ports'),
        'permission-denied': ('service_status','query_journal'),
        'crash-loop': ('service_status','query_journal'),
        'config-error': ('service_status','check_config'),
        'backend-502': ('service_status','verify_fix','query_journal'),
    }
    return sequences[scenario]


def observation(scenario, tool):
    if scenario not in SCENARIOS or tool not in steps(scenario):
        raise ValueError('FIXTURE_SCOPE')
    # S1/S2/S4 are explicit synthetic examples, not observed failures or live
    # model reasoning. Existing three scenarios retain their exact evidence.
    if scenario in ('crash-loop','config-error','backend-502'):
        text = {
            ('crash-loop','service_status'): 'Synthetic fixture: nginx failed, automatic restart counter increased.',
            ('crash-loop','query_journal'): 'Synthetic fixture: repeated start/exit failures and restart scheduling within the test window.',
            ('config-error','service_status'): 'Synthetic fixture: nginx failed; cause not inferred from service state alone.',
            ('config-error','check_config'): 'Synthetic fixture: fixed nginx syntax validation reports invalid configuration; no reload/restart.',
            ('backend-502','service_status'): 'Synthetic fixture: nginx active/running; this does not prove HTTP health.',
            ('backend-502','verify_fix'): 'Synthetic fixture: loopback HEAD returns HTTP 502; health fails despite active service.',
            ('backend-502','query_journal'): 'Synthetic fixture: connect() failed while connecting to an allowlisted upstream; no private address retained.',
        }[(scenario,tool)]
    else:
        text = {
            'service_status': ('Fixture nginx.service active; HTTP health chưa được kiểm tra.'
                           if scenario == 'active' else 'Fixture nginx.service failed.'),
            'listening_ports': 'Fixture port 80 already in use; nginx bind failed.',
            'query_journal': 'Fixture PERMISSION_DENIED: không đủ quyền đọc journal.',
        }[tool]
    return {'fixture': 'integration-demo-v1', 'synthetic': True,
            'scenario': scenario, 'tool': tool,
            'limited': scenario == 'permission-denied' and tool == 'query_journal',
            'evidence': [{'id': scenario + '-' + tool,
                          'source': 'synthetic:integration-demo-v1/' + scenario + '/' + tool,
                          'text': text}]}


def conclusion(scenario):
    return {
        'active': 'Fixture cho thấy Nginx active; chưa xác minh HTTP health hoặc Linux thật.',
        'port-conflict': 'Trong fixture, Nginx failed và có dấu hiệu xung đột cổng 80; chưa xác minh Linux thật.',
        'permission-denied': 'Không đủ bằng chứng; fixture không cho phép đọc journal.',
        'crash-loop': 'Trong fixture S1, service failed và log ghi nhận vòng lặp khởi động/thất bại; chưa xác minh Linux thật hoặc nguyên nhân sâu hơn.',
        'config-error': 'Trong fixture S2, service failed và trình kiểm tra cú pháp báo cấu hình không hợp lệ; không có thao tác sửa/reload.',
        'backend-502': 'Trong fixture S4, Nginx active nhưng HTTP 502 và log báo upstream connection failed; service active không có nghĩa website khỏe.',
    }[scenario]
