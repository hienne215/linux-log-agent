"""Offline statvfs doubles only: no real filesystem observations or Linux smoke."""

import errno
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

from jsonschema import Draft202012Validator
import pytest

from src.mcp_server import disk_usage as disk


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'schemas/tools/disk_usage.response.schema.json').read_text())


def stats(**changes):
    fields = dict(f_blocks=100, f_bfree=30, f_bavail=20, f_frsize=4096, f_bsize=8192,
                  f_files=50, f_ffree=20, f_favail=10)
    return SimpleNamespace(**(fields | changes))


@pytest.fixture
def api(monkeypatch):
    function = Mock(return_value=stats())
    monkeypatch.setattr(disk.os, 'statvfs', function, raising=False)
    return function


def error(call, code):
    with pytest.raises(disk.DiskUsageError) as caught:
        call()
    assert caught.value.code == code
    assert str(caught.value) == code
    assert caught.value.args == (code,)
    return caught.value


@pytest.mark.parametrize('filesystem', [None, 'root'])
@pytest.mark.parametrize('include_inodes', [True, False])
def test_default_and_explicit_mapping_schema_and_values(api, filesystem, include_inodes):
    result = disk.collect_disk_usage(filesystem, include_inodes)
    api.assert_called_once_with('/')
    assert result == dict(filesystem='root', mount_point='/', used_percent=70.0,
                          available_bytes=81920, inodes_included=include_inodes)
    Draft202012Validator(SCHEMA).validate(result)
    assert set(result) == set(SCHEMA['properties'])
    assert json.loads(json.dumps(result, allow_nan=False)) == result


def test_no_arguments_uses_internal_defaults(api):
    assert disk.collect_disk_usage()['inodes_included'] is True
    api.assert_called_once_with('/')


@pytest.mark.parametrize('filesystem', ['/', '/tmp', '/../', '../root', '/dev/sda1', 'unknown', 'root/../', 'root\x00'])
def test_disallowed_filesystems_never_reach_api(api, filesystem):
    error(lambda: disk.collect_disk_usage(filesystem), 'POLICY_DENIED')
    api.assert_not_called()


@pytest.mark.parametrize('filesystem', ['', 'x' * 256, 1, False, [], {}])
def test_invalid_filesystem_types_and_bounds(api, filesystem):
    error(lambda: disk.collect_disk_usage(filesystem), 'INVALID_INPUT')
    api.assert_not_called()


@pytest.mark.parametrize('include', [None, 0, 1, 'true', [], {}])
def test_include_inodes_requires_boolean(api, include):
    error(lambda: disk.collect_disk_usage(include_inodes=include), 'INVALID_INPUT')
    api.assert_not_called()


@pytest.mark.parametrize('fragment,block,expected', [(1024, 4096, 20480), (0, 4096, 81920), (4096, 1024, 81920)])
def test_fragment_size_and_zero_fragment_fallback(api, fragment, block, expected):
    api.return_value = stats(f_frsize=fragment, f_bsize=block)
    assert disk.collect_disk_usage()['available_bytes'] == expected


@pytest.mark.parametrize('free,available,percent', [(100, 100, 0.0), (0, 0, 100.0), (30, 0, 70.0)])
def test_usage_endpoints_and_reserved_blocks(api, free, available, percent):
    api.return_value = stats(f_bfree=free, f_bavail=available)
    assert disk.collect_disk_usage()['used_percent'] == percent


@pytest.mark.parametrize('field', ['f_blocks', 'f_bfree', 'f_bavail', 'f_frsize', 'f_bsize', 'f_files', 'f_ffree', 'f_favail'])
@pytest.mark.parametrize('value', [-1, True, 1.5, float('nan'), float('inf'), float('-inf'), 1 << 64])
def test_invalid_numeric_counters_fail_closed(api, field, value):
    api.return_value = stats(**{field: value})
    error(disk.collect_disk_usage, 'EXECUTION_UNKNOWN')
    api.assert_called_once_with('/')


@pytest.mark.parametrize('changes', [dict(f_blocks=0), dict(f_bsize=0), dict(f_bfree=101),
    dict(f_bavail=31), dict(f_ffree=51), dict(f_favail=21),
    dict(f_blocks=(1 << 64) - 1, f_bfree=(1 << 64) - 1, f_bavail=(1 << 64) - 1, f_frsize=2)])
def test_inconsistent_denominator_or_byte_overflow(api, changes):
    api.return_value = stats(**changes)
    error(disk.collect_disk_usage, 'EXECUTION_UNKNOWN')


def test_exact_integer_calculation_and_json_finiteness(api):
    api.return_value = stats(f_blocks=(1 << 64) - 1, f_bfree=1, f_bavail=1, f_frsize=1)
    result = disk.collect_disk_usage()
    assert 0 <= result['used_percent'] <= 100
    assert result['available_bytes'] == 1
    json.dumps(result, allow_nan=False)


def test_fractional_usage_and_maximum_available_bytes(api):
    api.return_value = stats(f_blocks=3, f_bfree=1, f_bavail=1, f_frsize=1)
    assert disk.collect_disk_usage()['used_percent'] == pytest.approx(200 / 3)
    api.return_value = stats(f_blocks=(1 << 64) - 1, f_bfree=(1 << 64) - 1,
                             f_bavail=(1 << 64) - 1, f_frsize=1)
    result = disk.collect_disk_usage()
    assert result['available_bytes'] == (1 << 64) - 1
    assert result['used_percent'] == 0.0
    Draft202012Validator(SCHEMA).validate(result)
    json.dumps(result, allow_nan=False)


def test_inode_flag_controls_validation_without_extra_wire_fields(api):
    api.return_value = stats()
    del api.return_value.f_files
    assert disk.collect_disk_usage(include_inodes=False)['inodes_included'] is False
    error(lambda: disk.collect_disk_usage(include_inodes=True), 'EXECUTION_UNKNOWN')


@pytest.mark.parametrize('field', ['f_blocks', 'f_bfree', 'f_bavail', 'f_frsize', 'f_bsize'])
def test_missing_block_fields(api, field):
    delattr(api.return_value, field)
    error(disk.collect_disk_usage, 'EXECUTION_UNKNOWN')


@pytest.mark.parametrize('failure,code', [
    (PermissionError('SENSITIVE /private/path'), 'PERMISSION_DENIED'),
    (FileNotFoundError('SENSITIVE /private/path'), 'NOT_FOUND'),
    (NotImplementedError('SENSITIVE'), 'UNSUPPORTED'),
    (OSError(errno.ENOSYS, 'SENSITIVE'), 'UNSUPPORTED'),
    (OSError(errno.ENOTSUP, 'SENSITIVE'), 'UNSUPPORTED'),
    (OSError(errno.ENOTDIR, 'SENSITIVE'), 'NOT_FOUND'),
    (OSError(errno.EPERM, 'SENSITIVE'), 'PERMISSION_DENIED'),
    (OSError(errno.EIO, 'SENSITIVE'), 'EXECUTION_UNKNOWN'),
    (RuntimeError('SENSITIVE'), 'EXECUTION_UNKNOWN'),
])
def test_api_failures_are_typed_and_do_not_leak(api, capsys, failure, code):
    api.side_effect = failure
    caught = error(disk.collect_disk_usage, code)
    assert caught.__suppress_context__
    assert 'SENSITIVE' not in str(caught)
    assert capsys.readouterr() == ('', '')
    api.assert_called_once_with('/')


def test_missing_statvfs_api(monkeypatch):
    monkeypatch.delattr(disk.os, 'statvfs', raising=False)
    error(disk.collect_disk_usage, 'UNSUPPORTED')


def test_core_has_no_command_or_write_path(api, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Command execution is forbidden')
    monkeypatch.setattr(disk.os, 'system', forbidden)
    monkeypatch.setattr(disk.os, 'popen', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    disk.collect_disk_usage()
    api.assert_called_once_with('/')
