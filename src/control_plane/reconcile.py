"""Explicit VM administrator attestation: expired approval, no helper intent.

No grant recovery, execution or generic unlock. Existing history is retained;
only a root-owned per-action attestation permits the next coordinator generation
to expire that approved decision. Any helper intent blocks this operation.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import subprocess
import sys

from .approval import Action, ApprovalError, _json, _unique

PROOFS=Path('/etc/linux-log-agent/reconciliations')
FIELDS=frozenset(('version','action_id','action_sha256','environment_id','policy_version',
                  'expires_at','observed_at','helper_sha256','reason'))


def action_hash(action):
    record=asdict(action);del record['state']
    return hashlib.sha256(_json(record)).hexdigest()


def valid_proof(value,action,policy):
    try:
        return (type(value) is dict and set(value)==FIELDS and type(value['version']) is int
            and value['version']==1 and value['action_id']==action.action_id
            and value['action_sha256']==action_hash(action)
            and value['environment_id']==policy.environment_id=='linux-log-lab-hien'
            and value['policy_version']==policy.version=='vm-candidate-v1'
            and value['expires_at']==action.expires_at
            and value['reason']=='EXPIRED_WITHOUT_HELPER_INTENT'
            and type(value['helper_sha256']) is str and re.fullmatch('[0-9a-f]{64}',value['helper_sha256']) is not None
            and type(value['observed_at']) is str and value['observed_at'].endswith('Z')
            and datetime.fromisoformat(value['expires_at'].replace('Z','+00:00'))
                < datetime.fromisoformat(value['observed_at'].replace('Z','+00:00')))
    except Exception:return False


def attested(action,policy):
    """A missing attestation keeps the existing lock; invalid files fail closed."""
    if policy.environment_id!='linux-log-lab-hien' or policy.version!='vm-candidate-v1':return False
    if re.fullmatch('[0-9a-f]{32}',action.action_id) is None:raise ApprovalError('STATE_UNAVAILABLE')
    path=PROOFS/(action.action_id+'.json')
    try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return False
    try:
        from .runtime import protected_read
        import pwd
        raw=protected_read(path,mode=0o640,gid=pwd.getpwnam('lla-approval').pw_gid,limit=2048)
        # protected_read reopens safely; this fd also rejects a symlink before IO.
        value=json.loads(raw,object_pairs_hook=_unique)
        if not valid_proof(value,action,policy):raise ValueError()
        return True
    except Exception:raise ApprovalError('STATE_UNAVAILABLE') from None
    finally:os.close(fd)


def eligibility(action,counts,now):
    if (action.state!='approved' or action.unit!='nginx.service'
            or action.environment_id!='linux-log-lab-hien'
            or counts!={'attempts':0,'audit':0,'outcomes':0}
            or datetime.fromisoformat(action.expires_at.replace('Z','+00:00'))>=now):
        raise ApprovalError('RECONCILIATION_DENIED')


def main(argv=None):
    args=sys.argv[1:] if argv is None else argv
    if len(args)!=1 or re.fullmatch('[0-9a-f]{32}',args[0]) is None:
        print('INVALID_COMMAND',file=sys.stderr);return 2
    descriptors=[]
    try:
        if (os.geteuid()!=0 or socket.gethostname()!='linux-log-lab-hien'
                or Path('/sys/class/dmi/id/sys_vendor').read_text().strip()!='QEMU'):
            raise ApprovalError('CALLER_DENIED')
        from .runtime import deployment,SOCKETS
        identities,policy,key,enabled=deployment()
        if enabled:raise ApprovalError('RECONCILIATION_DENIED')
        for unit in ('lla-approval','lla-helper'):
            state=subprocess.check_output(['/usr/bin/systemctl','show',unit,'--property=ActiveState','--value'],
                text=True,timeout=5).strip()
            if state!='inactive':raise ApprovalError('RECONCILIATION_DENIED')
        if any(path.exists() for path in SOCKETS.values()):raise ApprovalError('RECONCILIATION_DENIED')
        contents={}
        for name,uid in (('approval',identities['approval'].pw_uid),('helper',0)):
            folder=Path('/var/lib/linux-log-agent')/name
            info=folder.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=uid or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError()
            path=folder/('coordinator.sqlite' if name=='approval' else 'ledger.sqlite')
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK);descriptors.append(fd)
            info=os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or stat.S_IMODE(info.st_mode)!=0o600
                    or info.st_nlink!=1 or info.st_size>8*1024*1024):raise ValueError()
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if any(Path(str(path)+suffix).exists() for suffix in ('-journal','-wal','-shm')):raise ValueError()
            with sqlite3.connect('file:'+str(path)+'?mode=ro&immutable=1',uri=True) as db:
                db.execute('PRAGMA trusted_schema=OFF')
                if db.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:raise ValueError()
                if name=='approval':
                    row=db.execute('select record from actions where action_id=?',(args[0],)).fetchone()
                    if row is None:raise ValueError()
                    value=json.loads(row[0],object_pairs_hook=_unique);value['evidence_ids']=tuple(value['evidence_ids'])
                    action=Action(**value)
                else:
                    counts={table:db.execute('select count(*) from '+table).fetchone()[0] for table in ('attempts','audit','outcomes')}
            contents[name]=hashlib.sha256(os.pread(fd,info.st_size,0)).hexdigest()
        now=datetime.now(timezone.utc);eligibility(action,counts,now)
        if action.action_id!=args[0]:raise ValueError()
        proof=dict(version=1,action_id=action.action_id,action_sha256=action_hash(action),
            environment_id=policy.environment_id,policy_version=policy.version,expires_at=action.expires_at,
            observed_at=now.isoformat(timespec='microseconds').replace('+00:00','Z'),
            helper_sha256=contents['helper'],reason='EXPIRED_WITHOUT_HELPER_INTENT')
        if not valid_proof(proof,action,policy):raise ValueError()
        gid=identities['approval'].pw_gid
        if not PROOFS.exists():
            PROOFS.mkdir(mode=0o750);os.chown(PROOFS,0,gid);os.chmod(PROOFS,0o750)
        parent=PROOFS.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=0 or parent.st_gid!=gid or stat.S_IMODE(parent.st_mode)!=0o750:raise ValueError()
        fd=os.open(PROOFS/(action.action_id+'.json'),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
        try:
            os.fchown(fd,0,gid);os.fchmod(fd,0o640);os.write(fd,_json(proof));os.fsync(fd)
        finally:os.close(fd)
        fd=os.open(PROOFS,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)
        print(json.dumps(dict(action_id=action.action_id,attested_no_intent=True,
            executed=None,write_enabled=False,history_preserved=True)));return 0
    except Exception:print('RECONCILIATION_DENIED',file=sys.stderr);return 1
    finally:
        for fd in descriptors:os.close(fd)


if __name__=='__main__':raise SystemExit(main())
