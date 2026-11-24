"""Private durable intents and observed job results, with no automatic unlock.

    A reservation commits before a future helper may attempt an operation.
    Crash/replay is conservative: recorded intent means execution_unknown, never
    automatic retry. No running service uses this candidate yet.
"""
from dataclasses import dataclass,replace
from datetime import datetime,timezone
import re

from .approval import ApprovalError,GrantCodec,_id
from .storage import PrivateDatabase


def job_id(path):
    if type(path) is not str or re.fullmatch(r'/org/freedesktop/systemd1/job/[1-9][0-9]{0,9}',path) is None:
        raise ApprovalError('PROTOCOL_ERROR')
    value=int(path.rsplit('/',1)[1])
    if value>=2**32:raise ApprovalError('PROTOCOL_ERROR')
    return value


@dataclass(frozen=True)
class JobResult:
    job_path:str|None=None
    job_result:str|None=None
    error_code:str|None=None

    def __post_init__(self):
        if self.job_path is not None:job_id(self.job_path)
        if (self.job_result not in (None,'done','failed','dependency','canceled','timeout','skipped')
                or (self.job_result is not None and (self.job_path is None or self.error_code is not None))
                or self.error_code not in (None,'TIMEOUT','DISCONNECTED','PROTOCOL_ERROR','CANCELLED','EXPIRED','TRANSPORT_ERROR')):
            raise ApprovalError('PROTOCOL_ERROR')

    @property
    def outcome(self):
        return 'succeeded' if self.job_result=='done' else 'failed' if self.job_result in ('failed','dependency') else 'execution_unknown'

    @property
    def executed(self):
        return True if self.job_result=='done' else None


@dataclass(frozen=True)
class Attempt:
    action_id:str
    unit:str
    outcome:str='execution_unknown'
    executed:bool|None=None
    job_path:str|None=None
    job_result:str|None=None
    error_code:str|None=None
    environment_id:str|None=None
    session_id:str|None=None
    trace_id:str|None=None


class HelperLedger:
    def __init__(self,directory,codec):
        self._database=None
        self._owned={};self._failed=False
        if type(codec) is not GrantCodec:raise ApprovalError('INVALID_POLICY')
        self.codec=codec
        try:
            self._database=PrivateDatabase(directory,'ledger.sqlite')
            with self._connection() as db:
                db.execute('CREATE TABLE IF NOT EXISTS attempts (action_id TEXT PRIMARY KEY, unit TEXT UNIQUE NOT NULL, '
                    'environment_id TEXT NOT NULL, epoch TEXT NOT NULL, session_id TEXT NOT NULL, trace_id TEXT NOT NULL, '
                    'params_sha256 TEXT NOT NULL, consumed_ns INTEGER NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS audit (action_id TEXT PRIMARY KEY REFERENCES attempts(action_id), '
                    'event TEXT NOT NULL, approver_uid INTEGER NOT NULL, policy_version TEXT NOT NULL, consumed_at TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS outcomes (action_id TEXT PRIMARY KEY REFERENCES attempts(action_id), '
                    'event TEXT NOT NULL, job_path TEXT, job_result TEXT, error_code TEXT, observed_at TEXT NOT NULL)')
            self._database.sync()
        except Exception:
            self.close()
            raise ApprovalError('STATE_UNAVAILABLE') from None

    def close(self):
        self._owned.clear();self._failed=True
        if self._database is not None:
            self._database.close();self._database=None

    @property
    def _directory(self):
        return self._database.directory_fd if self._database is not None else None

    def __enter__(self):return self

    def __exit__(self,*args):self.close()

    def _connection(self):
        if self._database is None:raise ApprovalError('STATE_UNAVAILABLE')
        return self._database.connection()

    def consume(self,grant,*,peer_uid):
        # Auth/signature before writing any request state. Recheck after taking
        # the transaction lock so lock contention cannot bypass expiry.
        self.codec.verify(grant,peer_uid=peer_uid)
        if self._failed:raise ApprovalError('STATE_UNAVAILABLE')
        try:
            with self._connection() as db:
                db.execute('BEGIN IMMEDIATE')
                p=self.codec.verify(grant,peer_uid=peer_uid)
                if db.execute('SELECT 1 FROM attempts WHERE action_id=?',(p['action_id'],)).fetchone():
                    raise ApprovalError('REPLAY')
                unit=p['params']['unit']
                if db.execute('SELECT 1 FROM attempts WHERE unit=?',(unit,)).fetchone():
                    raise ApprovalError('UNIT_LOCKED')
                if db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]>=1024:raise ApprovalError('CAPACITY')
                db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?)',
                    (p['action_id'],unit,p['environment_id'],p['helper_epoch'],p['session_id'],p['trace_id'],
                     p['params_sha256'],self.codec.clock()))
                db.execute('INSERT INTO audit VALUES (?,?,?,?,?)',
                    (p['action_id'],'intent_consumed',p['approver_uid'],p['policy_version'],
                     datetime.now(timezone.utc).isoformat().replace('+00:00','Z')))
                db.execute('COMMIT')
            self._database.sync()
            attempt=Attempt(p['action_id'],unit,environment_id=p['environment_id'],
                session_id=p['session_id'],trace_id=p['trace_id'])
            self._owned[attempt.action_id]=attempt
            return attempt
        except ApprovalError:raise
        except Exception:
            self._failed=True
            raise ApprovalError('STATE_UNAVAILABLE') from None

    def status(self,action_id):
        if not _id(action_id):raise ApprovalError('INVALID_PROPOSAL')
        try:
            with self._connection() as db:
                row=db.execute('SELECT unit,environment_id,session_id,trace_id FROM attempts WHERE action_id=?',(action_id,)).fetchone()
                result=db.execute('SELECT event,job_path,job_result,error_code,observed_at FROM outcomes WHERE action_id=?',(action_id,)).fetchone()
            if row is None:return None
            if row[0]!='nginx.service' or not all(_id(value) for value in row[1:]):raise ValueError()
            attempt=Attempt(action_id,row[0],environment_id=row[1],session_id=row[2],trace_id=row[3])
            if result is not None:
                if result[0]!='job_observed' or type(result[4]) is not str or not result[4].endswith('Z'):raise ValueError()
                datetime.fromisoformat(result[4].replace('Z','+00:00'))
                observed=JobResult(*result[1:4])
                attempt=replace(attempt,outcome=observed.outcome,executed=observed.executed,
                    job_path=observed.job_path,job_result=observed.job_result,error_code=observed.error_code)
            return attempt
        except Exception:raise ApprovalError('STATE_UNAVAILABLE') from None

    def finish(self,action_id,result):
        """Internal driver observation only; never exposed to Agent/coordinator.

        Only an intent consumed by this live instance may be finalized once.
        Reopening an unknown intent does not grant ownership or infer completion.
        No result, including done, releases the conservative unit lock.
        """
        if self._failed:raise ApprovalError('STATE_UNAVAILABLE')
        if not _id(action_id) or type(result) is not JobResult:raise ApprovalError('PROTOCOL_ERROR')
        attempt=self._owned.get(action_id)
        if attempt is None:raise ApprovalError('STATE_FINAL')
        try:
            with self._connection() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM outcomes WHERE action_id=?',(action_id,)).fetchone():
                    raise ApprovalError('STATE_FINAL')
                db.execute('INSERT INTO outcomes VALUES (?,?,?,?,?,?)',(action_id,'job_observed',
                    result.job_path,result.job_result,result.error_code,
                    datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')))
                db.execute('COMMIT')
            self._database.sync()
            return replace(attempt,outcome=result.outcome,executed=result.executed,
                job_path=result.job_path,job_result=result.job_result,error_code=result.error_code)
        except ApprovalError:raise
        except Exception:
            self._failed=True
            raise ApprovalError('STATE_UNAVAILABLE') from None
        finally:
            self._owned.pop(action_id,None)  # never reissue/retry after uncertainty
