"""Owner/key/fallback candidate tests. No real key or Linux logs are read."""
import json
import stat
from types import SimpleNamespace

import pytest

from test_p03_03_failed_logins import REQ, PRIVACY, SINCE, snapshot, validate, UTC
from src.mcp_server.failed_login_events import Privacy, parse_message
from src.mcp_server.failed_logins_adapter import FailedLoginsAdapter
from src.mcp_server.failed_logins_owner import FailedLoginsOwner
from src.mcp_server import failed_logins_owner as ownership
from src.mcp_server import failed_login_sources as sources


@pytest.mark.parametrize('gap', ['permission_denied','unavailable','unknown_retention','late_retention','incomplete','cut'])
def test_explicit_fallback_trigger_even_when_auth_has_only_partial_history(gap):
    data=snapshot(3)
    data[1].oldest=None
    if gap in ('permission_denied','unavailable'): data[0].status=gap
    if gap=='unknown_retention': data[0].oldest=None
    if gap=='late_retention': data[0].oldest=SINCE.replace(minute=1)
    if gap=='incomplete': data[0].incomplete=True
    if gap=='cut': data[0].truncated=True
    result=validate(FailedLoginsAdapter(lambda *_:data).call(REQ))
    assert result['status']=='partial'
    assert all(e['source']=='auth.log' for e in result['evidence'])


def test_empty_journal_with_proven_coverage_does_not_fallback():
    data=snapshot(3)
    data[0].events=[]
    result=validate(FailedLoginsAdapter(lambda *_:data).call(REQ))
    assert result['status']=='ok' and result['data']['events']==[]


def test_failed_fallback_keeps_readable_partial_journal():
    data=snapshot(3)
    data[0].incomplete=True
    data[1].status='permission_denied'
    result=validate(FailedLoginsAdapter(lambda *_:data).call(REQ))
    assert result['status']=='partial'
    assert all(e['source']=='journal.auth' for e in result['evidence'])


def owner_fixture(access=lambda:True):
    calls=[]
    def factory(privacy):
        def provider(*_):
            calls.append(1)
            data=snapshot(3)
            data[0].events=[parse_message('Failed password for synthetic-user from 192.0.2.10 port 22 ssh2',
                              'sshd',i,SINCE.replace(second=i+1),privacy,str(i)) for i in range(3)]
            return data
        return provider
    instance=FailedLoginsOwner(('peer-uid',129),'session-test',PRIVACY,factory,access_check=access)
    return instance,calls


def test_persistent_owner_serves_later_calls_without_another_source_read():
    owner,calls=owner_fixture()
    first=validate(owner.call({**REQ,'limit':1},('peer-uid',129)))
    next_page=validate(owner.call({**REQ,'limit':1,'cursor':first['next_cursor']},('peer-uid',129)))
    assert len(calls)==1 and next_page['data']['events']!=first['data']['events']


@pytest.mark.parametrize('actor,session', [(('peer-uid',130),'session-test'),(('peer-uid',129),'other')])
def test_trusted_peer_and_session_isolation(actor,session):
    owner,calls=owner_fixture()
    response=validate(owner.call({**REQ,'session_id':session},actor))
    assert response['error_code']=='INVALID_INPUT' and not calls


def test_policy_revocation_invalidates_cursor_even_if_access_restored():
    access=[True]
    owner,calls=owner_fixture(lambda:access[0])
    token=owner.call({**REQ,'limit':1},('peer-uid',129))['next_cursor']
    access[0]=False
    denied=validate(owner.call({**REQ,'limit':1,'cursor':token},('peer-uid',129)))
    assert denied['error_code']=='POLICY_DENIED' and len(calls)==1
    access[0]=True
    assert owner.call({**REQ,'limit':1,'cursor':token},('peer-uid',129))['error_code']=='INVALID_INPUT'


def test_policy_exception_fails_closed_without_details():
    def bad(): raise RuntimeError('SECRET key material')
    owner,calls=owner_fixture(bad)
    response=validate(owner.call(REQ,('peer-uid',129)))
    assert response['error_code']=='POLICY_DENIED' and 'SECRET' not in json.dumps(response) and not calls


def test_key_rotation_invalidates_old_tokens_and_changes_new_pseudonyms():
    owner,_=owner_fixture()
    first=owner.call({**REQ,'limit':1},('peer-uid',129))
    owner.rotate(Privacy(b'a-new-synthetic-test-key-32-bytes!', 'synthetic-eval'))
    invalid=owner.call({**REQ,'limit':1,'cursor':first['next_cursor']},('peer-uid',129))
    fresh=validate(owner.call({**REQ,'limit':1},('peer-uid',129)))
    assert invalid['error_code']=='INVALID_INPUT'
    assert first['data']['events'][0]['source_ip']!=fresh['data']['events'][0]['source_ip']


def test_missing_replacement_key_closes_owner():
    owner,calls=owner_fixture()
    with pytest.raises(ValueError,match='Key rotation failed'):
        owner.rotate(None)
    assert owner.call(REQ,('peer-uid',129))['error_code']=='INVALID_INPUT' and not calls


def test_close_models_eof_then_rejects_new_work():
    owner,calls=owner_fixture()
    owner.call({**REQ,'limit':1},('peer-uid',129))
    owner.close()
    response=validate(owner.call(REQ,('peer-uid',129)))
    assert response['error_code']=='INVALID_INPUT' and len(calls)==1
    assert not owner._adapter._states


@pytest.mark.parametrize('kwargs', [dict(privacy=None),dict(access_check=None),dict(principal=None),dict(session_id='')])
def test_owner_missing_trusted_configuration_fails_closed(kwargs):
    options=dict(principal=129,session_id='s',privacy=PRIVACY,provider_factory=lambda p:None,access_check=lambda:True)
    options.update(kwargs)
    with pytest.raises(ValueError): FailedLoginsOwner(**options)


def key_stat(**overrides):
    fields=dict(st_mode=stat.S_IFREG|0o640,st_uid=0,st_gid=135,st_size=32,
                st_dev=1,st_ino=2,st_mtime_ns=3)
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.mark.parametrize('overrides',[dict(st_uid=129),dict(st_gid=999),dict(st_size=31),dict(st_size=33),
    dict(st_mode=stat.S_IFREG|0o644),dict(st_mode=stat.S_IFREG|0o660),dict(st_mode=stat.S_IFIFO|0o640)])
def test_bad_key_permissions_owner_type_length_rejected_before_read(monkeypatch,overrides):
    def no_read(*_): pytest.fail('Unsafe key must not be read')
    monkeypatch.setattr(ownership,'os',SimpleNamespace(fstat=lambda _:key_stat(**overrides),read=no_read))
    with pytest.raises(ValueError,match='Invalid pseudonym key policy'):
        ownership.privacy_from_fd(50,135,'scope')


@pytest.mark.parametrize('short_or_changed',['short','changed'])
def test_key_short_read_or_change_rejected(monkeypatch,short_or_changed):
    stats=iter([key_stat(),key_stat(st_mtime_ns=4 if short_or_changed=='changed' else 3)])
    fake=SimpleNamespace(fstat=lambda _:next(stats),read=lambda *_:b'x'*(31 if short_or_changed=='short' else 32))
    monkeypatch.setattr(ownership,'os',fake)
    with pytest.raises(ValueError,match='Unstable pseudonym key'):
        ownership.privacy_from_fd(50,135,'scope')


def test_valid_key_never_appears_in_public_output_or_repr(monkeypatch):
    key=b'SYNTHETIC-SECRET-KEY-DO-NOT-USE!'
    assert len(key)==32
    monkeypatch.setattr(ownership,'os',SimpleNamespace(fstat=lambda _:key_stat(),read=lambda *_:key))
    privacy=ownership.privacy_from_fd(50,135,'scope')
    assert key.decode() not in repr(privacy)
    assert key.decode() not in privacy.ip('192.0.2.10')


def test_fixed_key_path_directory_walk_no_follow_and_cleanup(monkeypatch):
    opened,closed=[],[]
    def open_fd(path,flags,**kwargs):
        opened.append((path,flags,kwargs))
        return 100+len(opened)
    def info(fd):
        return key_stat() if fd==105 else SimpleNamespace(st_uid=0,st_mode=stat.S_IFDIR|0o750)
    fake=SimpleNamespace(O_RDONLY=0,O_DIRECTORY=1,O_NOFOLLOW=2,O_NONBLOCK=4,
                         open=open_fd,close=lambda fd:closed.append(fd),fstat=info,read=lambda *_:b'x'*32)
    monkeypatch.setattr(ownership,'os',fake)
    monkeypatch.setattr(ownership.sys,'platform','linux')
    privacy=ownership.load_privacy(135,'scope')
    assert [p for p,_,_ in opened]==['/','etc','linux-log-agent','failed-logins','pseudonym.key']
    assert all(flags&2 for _,flags,_ in opened)
    assert set(closed)=={101,102,103,104,105}
    assert privacy.ip('192.0.2.10').startswith('ip-')


def test_missing_key_has_no_fallback_or_creation(monkeypatch):
    def missing(*args,**kwargs): raise FileNotFoundError('Missing local secret')
    monkeypatch.setattr(ownership,'os',SimpleNamespace(O_RDONLY=0,O_DIRECTORY=1,O_NOFOLLOW=2,open=missing))
    monkeypatch.setattr(ownership.sys,'platform','linux')
    with pytest.raises(FileNotFoundError): ownership.load_privacy(135,'scope')


def test_inode_reuse_with_different_generation_is_not_dropped(monkeypatch):
    record=b'2026-11-17T12:00:01+00:00 fixture sshd[1]: Failed password for root from 192.0.2.10 port 22 ssh2\n'
    def read(path,cap):
        return record,(1,7,10 if path.endswith('.1') else 20),False
    monkeypatch.setattr(sources,'read_regular',read)
    result=sources.LinuxLoginSources(PRIVACY,2026,UTC)._auth(float('inf'))
    assert len(result.events)==2
    assert result.events[0].identity!=result.events[1].identity and result.incomplete
