"""Trang review 5935965430: strict patterns and bounded canonical contracts."""
import copy
import json
from pathlib import Path
import re

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from test_contract_audit import arguments, check, local

ROOT = Path(__file__).resolve().parents[1]
UNIT_TOOLS = ('query_journal', 'service_status', 'listening_ports', 'check_config',
              'detect_anomaly', 'verify_fix', 'restart_service')


def schema(name, suffix=''):
    return json.loads((ROOT / f'schemas/tools/{name}{suffix}.schema.json').read_text())


def validator(name, suffix=''):
    return Draft202012Validator(schema(name, suffix), format_checker=FormatChecker())


@pytest.mark.parametrize('name', UNIT_TOOLS)
@pytest.mark.parametrize('unit,valid', [
    ('nginx.service', True), ('lab-backend.service', True), ('A_1.test.service', True),
    ('nginx.service\n', False), ('nginx.service\r\n', False),
    ('nginx.service\x00', False), ('nginx.service\u2028', False),
    ('nginx.service\nother.service', False), ('/nginx.service', False),
    ('--nginx.service', False), ('nginx@lab.service', False),
])
def test_unit_patterns_have_strict_end_of_input(name, unit, valid):
    descriptor = schema(name)['properties']['unit']
    # JSON strings must roundtrip and Python search must match jsonschema.
    pattern = json.loads(json.dumps(descriptor))['pattern']
    assert bool(re.search(pattern, unit)) is valid
    assert Draft202012Validator(descriptor).is_valid(unit) is valid
    assert validator(name).is_valid({**arguments(name), 'unit': unit}) is valid


@pytest.mark.parametrize('source,valid', [
    ('mock-source', True), ('syslog-main', True), ('journal_auth', True), ('a1', True),
    ('a' * 128, True), ('a' * 129, False), ('/', False), ('..', False),
    ('/var/log/auth.log', False), ('auth.log', False), ('../auth', False),
    ('-option', False), ('UPPER', False), ('source id', False),
    ('source\n', False), ('source\x00', False), ('source\t', False),
])
def test_source_is_an_id_not_a_path(source, valid):
    assert validator('search_log').is_valid({**arguments('search_log'), 'source_id': source}) is valid


@pytest.mark.parametrize('filesystem,valid', [
    ('root', True), ('/', True), ('/dev/mock', True), ('/dev/mapper/lab-root', True),
    ('/mnt/data_1', True), ('../root', False), ('/../root', False),
    ('/mnt/../root', False), ('/mnt/./data', False), ('/mnt//data', False),
    ('/mnt/data/', False), ('-root', False), ('--help', False), ('other', False),
    ('/root\n', False), ('/root\x00', False), ('/root\t', False),
])
def test_safe_filesystem_selector_syntax(filesystem, valid):
    assert validator('disk_usage').is_valid({**arguments('disk_usage'), 'filesystem': filesystem}) is valid


def issue():
    return dict(code='SYNTAX_ERROR', severity='error', message='Filtered diagnostic.')


@pytest.mark.parametrize('issues,valid', [
    ([], True), ([issue()], True), ([issue()] * 32, True), ([issue()] * 33, False),
    ([{}], False), ([{**issue(), 'raw_stderr': 'PRIVATE'}], False),
    ([{**issue(), 'message': 'x' * 2049}], False),
    ([{**issue(), 'code': 'x' * 129}], False),
    ([{**issue(), 'severity': 'unknown'}], False),
    ([{**issue(), 'message': None}], False),
])
def test_check_config_issues_are_strict_bounded_objects(issues, valid):
    data = dict(profile='systemd', unit=None, valid=False, issues=issues, message='Fixture only.')
    assert validator('check_config', '.response').is_valid(data) is valid


@pytest.mark.parametrize('field,value,valid', [
    ('action_id', None, True), ('action_id', 'a' * 128, True), ('action_id', 'a' * 129, False),
    ('baseline_evidence_refs', None, True), ('baseline_evidence_refs', [], True),
    ('baseline_evidence_refs', ['x' * 128], True),
    ('baseline_evidence_refs', ['x' * 129], False),
    ('baseline_evidence_refs', [str(i) for i in range(32)], True),
    ('baseline_evidence_refs', [str(i) for i in range(33)], False),
    ('baseline_evidence_refs', ['same', 'same'], False),
    ('check_types', ['service_active', 'no_new_failures', 'port_listening'], True),
    ('check_types', ['service_active'] * 4, False),
    ('check_types', ['service_active', 'service_active'], False),
])
def test_verify_fix_request_bounds_preserve_nullable_semantics(field, value, valid):
    assert validator('verify_fix').is_valid({**arguments('verify_fix'), field: value}) is valid


@pytest.mark.parametrize('signals,valid', [
    ([], True), (['service_failures', 'journal_errors', 'oom_events', 'failed_logins'], True),
    (['service_failures'] * 5, False), (['oom_events', 'oom_events'], False),
    (['unsupported'], False),
])
def test_signal_bounds_and_existing_empty_semantics(signals, valid):
    assert validator('detect_anomaly').is_valid({**arguments('detect_anomaly'), 'signals': signals}) is valid


@pytest.mark.parametrize('name,change', [
    ('service_status', dict(unit='nginx.service\n')),
    ('search_log', dict(source_id='/var/log/PRIVATE')),
    ('disk_usage', dict(filesystem='/mnt/../PRIVATE')),
    ('verify_fix', dict(action_id='PRIVATE' * 30)),
    ('verify_fix', dict(baseline_evidence_refs=[str(i) for i in range(33)])),
])
def test_new_request_rejections_use_existing_safe_boundary(name, change):
    result = local(name, {**arguments(name), **change, 'trace_id': 'PRIVATE_TRACE'})
    data = check(name, result)
    assert data['status'] == 'error' and data['error_code'] == 'INVALID_INPUT'
    assert data['trace_id'] == 'invalid-input'
    assert 'PRIVATE' not in result.model_dump_json()


def test_restart_data_unit_patterns_are_strict_too():
    data = check('restart_service', local('restart_service', arguments('restart_service')))['data']
    assert validator('restart_service', '.response').is_valid(data)
    data = copy.deepcopy(data)
    data['unit'] += '\n'
    assert not validator('restart_service', '.response').is_valid(data)


def test_all_36_tool_schemas_and_generator():
    for suffix in ('', '.response', '.result'):
        paths = list((ROOT / 'schemas/tools').glob(f'*{suffix}.schema.json'))
        if not suffix:
            paths = [p for p in paths if not p.name.endswith(('.response.schema.json', '.result.schema.json'))]
        assert len(paths) == 12
        for path in paths:
            Draft202012Validator.check_schema(json.loads(path.read_text()))


@pytest.mark.parametrize('name,array,bound', [
    ('query_journal', 'entries', 200), ('search_log', 'matches', 200),
    ('list_failed_units', 'failed_units', 200), ('listening_ports', 'ports', 200),
    ('verify_fix', 'checks', 3), ('detect_anomaly', 'anomalies', 50),
])
def test_response_array_bounds_allow_full_boundary_then_reject(name, array, bound):
    data = check(name, local(name, arguments(name)))['data']
    row = data[array][0]
    data[array] = [copy.deepcopy(row) for _ in range(bound)]
    if 'count' in data:
        data['count'] = bound
    assert validator(name, '.response').is_valid(data)
    data[array].append(copy.deepcopy(row))
    assert not validator(name, '.response').is_valid(data)


@pytest.mark.parametrize('name,array,field,bound', [
    ('service_status', None, 'description', 2048),
    ('list_failed_units', 'failed_units', 'description', 2048),
    ('list_failed_units', 'failed_units', 'unit', 255),
    ('listening_ports', 'ports', 'process', 2048),
    ('listening_ports', 'ports', 'address', 128),
    ('verify_fix', 'checks', 'summary', 2048),
    ('detect_anomaly', 'anomalies', 'summary', 2048),
])
def test_free_text_response_bounds(name, array, field, bound):
    data = check(name, local(name, arguments(name)))['data']
    target = data[array][0] if array else data
    target[field] = 'x' * bound
    assert validator(name, '.response').is_valid(data)
    target[field] += 'x'
    assert not validator(name, '.response').is_valid(data)


def test_under_transport_cap_but_oversized_description_fails_output_validation(monkeypatch):
    import mock_mcp_server as server
    original = server.TOOLS['service_status']
    def invalid(**kwargs):
        response = original[0](**kwargs)
        response['data']['description'] = 'x' * 2049
        assert server.wire_size(server.native_result(response)) < 65536
        return response
    monkeypatch.setitem(server.TOOLS, 'service_status', (invalid, *original[1:]))
    data = check('service_status', local('service_status', arguments('service_status')))
    assert data['error_code'] == 'EXECUTION_UNKNOWN'
