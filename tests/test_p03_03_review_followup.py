"""PR #29 review 6951590599: execution diagnostics and measured timing."""
import json
import time
import pytest

from test_p03_03_failed_logins import PRIVACY, REQ, SINCE, UTC, snapshot, validate
from test_p03_03_owner_policy import owner_fixture
from src.mcp_server import failed_login_sources as sources
from src.mcp_server.failed_logins_adapter import FailedLoginsAdapter


def journal_line():
    return json.dumps(dict(__CURSOR='synthetic-review',
        __REALTIME_TIMESTAMP=str(int(SINCE.timestamp()*1000000)),
        SYSLOG_IDENTIFIER='sshd', _PID='100',
        MESSAGE='A synthetic diagnostic mentions permission denied; no login failure.')).encode()


@pytest.mark.parametrize('stderr',[b'',b'permission denied'])
def test_successful_stdout_log_text_is_not_permission_diagnostic(monkeypatch,stderr):
    monkeypatch.setattr(sources,'bounded_command',lambda *_:(0,journal_line(),stderr,False,False))
    result=sources.LinuxLoginSources(PRIVACY,2026,UTC)._journal(1)
    assert result.status=='ok' and result.oldest==SINCE


@pytest.mark.parametrize('rc,stderr,timed,expected',[
    (1,b'Permission denied',False,'permission_denied'),
    (1,b'Other failure',False,'unavailable'),
    (1,b'Permission denied',True,'unavailable'),
])
def test_exit_status_and_stderr_classification(monkeypatch,rc,stderr,timed,expected):
    monkeypatch.setattr(sources,'bounded_command',lambda *_:(rc,journal_line(),stderr,False,timed))
    assert sources.LinuxLoginSources(PRIVACY,2026,UTC)._journal(1).status==expected


@pytest.mark.parametrize('missing,status',[(True,'permission_denied'),(False,'ok')])
def test_empty_success_uses_local_access_policy_not_log_words(monkeypatch,missing,status):
    monkeypatch.setattr(sources,'bounded_command',lambda *_:(0,b'',b'',False,False))
    monkeypatch.setattr(sources,'journal_access_missing',lambda:missing)
    assert sources.LinuxLoginSources(PRIVACY,2026,UTC)._journal(1).status==status


def test_acquisition_duration_is_measured():
    def slow_provider(*_):
        time.sleep(0.03)
        return snapshot()
    result=validate(FailedLoginsAdapter(slow_provider).call(REQ))
    assert result['duration_ms']>=20
    assert result['completed_at']>result['started_at']


def test_monotonic_duration_independent_of_wall_timestamp_and_ttl():
    ticks=iter([10,12.5])
    adapter=FailedLoginsAdapter(lambda *_:snapshot(),clock=lambda:100,
        duration_clock=lambda:next(ticks),wall_clock=lambda:SINCE)
    result=validate(adapter.call({**REQ,'limit':1}))
    assert result['duration_ms']==2500
    assert result['started_at']==result['completed_at']
    assert adapter._states[result['next_cursor']]['expires']==400


def test_continuation_does_not_replay_initial_acquisition_duration():
    reads=[]
    def provider(*_):
        reads.append(1)
        return snapshot()
    ticks=iter([0,2,10,10.125])
    adapter=FailedLoginsAdapter(provider,duration_clock=lambda:next(ticks),wall_clock=lambda:SINCE)
    first=validate(adapter.call({**REQ,'limit':1}))
    second=validate(adapter.call({**REQ,'limit':1,'cursor':first['next_cursor']}))
    assert first['duration_ms']==2000 and second['duration_ms']==125
    assert len(reads)==1


def test_failed_acquisition_is_also_measured():
    def fail(*_):
        time.sleep(0.03)
        raise RuntimeError('SYNTHETIC_SECRET')
    result=validate(FailedLoginsAdapter(fail).call(REQ))
    assert result['error_code']=='EXECUTION_UNKNOWN' and result['duration_ms']>=20
    assert 'SYNTHETIC_SECRET' not in json.dumps(result)


def test_owner_policy_denial_has_measured_elapsed_time():
    def deny():
        time.sleep(0.03)
        return False
    owner,calls=owner_fixture(deny)
    result=validate(owner.call(REQ,('peer-uid',129)))
    assert result['error_code']=='POLICY_DENIED' and result['duration_ms']>=20
    assert not calls
