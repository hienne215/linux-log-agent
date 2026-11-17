"""Operator allowlist FD policy; fake OS facade, no real privileged files."""
import stat
from types import SimpleNamespace

import pytest
from src.mcp_server import oom_events as oom


def file_stat(**changes):
    fields=dict(st_mode=stat.S_IFREG|0o644,st_uid=0,st_dev=1,st_ino=2,
                st_size=6,st_mtime_ns=3,st_ctime_ns=4)
    fields.update(changes)
    return SimpleNamespace(**fields)


def install(monkeypatch,before=None,after=None,data=b'myapp\n',open_error=False):
    closed,flags=[],[]
    chunks=[data,b'']
    states=iter([before or file_stat(),after or before or file_stat()])
    def open_fd(path,mode):
        flags.append(mode)
        if open_error: raise OSError('symlink denied')
        return 7
    fake=SimpleNamespace(O_RDONLY=0,O_NOFOLLOW=1,O_NONBLOCK=2,open=open_fd,
        fstat=lambda fd:next(states),read=lambda fd,n:chunks.pop(0),close=lambda fd:closed.append(fd))
    monkeypatch.setattr(oom,'os',fake)
    return closed,flags


def test_root_owned_stable_file_read_via_same_fd(monkeypatch):
    closed,flags=install(monkeypatch)
    assert oom.load_operator_names('/fixed/test')=={'myapp'}
    assert closed==[7] and flags==[3]


@pytest.mark.parametrize('changes',[dict(st_uid=129),dict(st_mode=stat.S_IFREG|0o664),
    dict(st_mode=stat.S_IFREG|0o646),dict(st_mode=stat.S_IFIFO|0o644)])
def test_untrusted_file_does_not_grant_names(monkeypatch,changes):
    closed,_=install(monkeypatch,before=file_stat(**changes))
    assert oom.load_operator_names('/fixed/test')==set() and closed==[7]


@pytest.mark.parametrize('changes',[dict(st_ino=9),dict(st_uid=129),dict(st_mode=stat.S_IFREG|0o666),
    dict(st_size=7),dict(st_mtime_ns=9),dict(st_ctime_ns=9)])
def test_changed_file_rejected_after_read(monkeypatch,changes):
    closed,_=install(monkeypatch,after=file_stat(**changes))
    assert oom.load_operator_names('/fixed/test')==set() and closed==[7]


def test_symlink_open_failure_has_no_following_fallback(monkeypatch):
    closed,flags=install(monkeypatch,open_error=True)
    assert oom.load_operator_names('/fixed/test')==set()
    assert not closed and flags==[3]


def test_oversized_or_invalid_utf8_file_grants_nothing(monkeypatch):
    install(monkeypatch,data=b'x'*8193)
    assert oom.load_operator_names('/fixed/test')==set()
    install(monkeypatch,data=b'\xff')
    assert oom.load_operator_names('/fixed/test')==set()
