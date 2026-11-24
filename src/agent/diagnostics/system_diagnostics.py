"""Bounded system-wide sources. Raw kernel/auth/config data never leaves worker."""
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import stat

from agent.diagnostics.journal_policy import parse_stamp, stamp, valid_window

LIMIT=100
AUTH_IDENTIFIERS=('sshd','sshd-session','sudo','login','su')
SOURCES={'memory_oom_events':'journal.kernel','failed_logins':'journal.auth'}
KNOWN_PROCESSES=frozenset(('nginx','sshd','python','python3','java','node','mysqld','postgres','redis-server','systemd','containerd','dockerd'))
OOM=re.compile(r'Killed process ([0-9]+) \(([^)]+)\).*?total-vm:([0-9]+)kB.*?anon-rss:([0-9]+)kB',re.I)
LOGIN=re.compile(r'Failed (?:password|publickey|keyboard-interactive(?:/pam)?) for (?:invalid user )?(\S+) from (\S+) port [0-9]+',re.I)
AUTH_LINE=re.compile(r'^(\w{3} +\d{1,2} \d\d:\d\d:\d\d) \S+ (sshd|sudo|login|su)(?:\[([0-9]+)\])?: (.*)$')


class SourceError(Exception):
    def __init__(self,code):self.code=code;super().__init__(code)


async def run(command):
    # Shared owner with the already-tested journal collector, not a new runner.
    from agent.diagnostics.journal_reader import execute,JournalError
    try:return await execute(command)
    except TimeoutError:raise SourceError('TIMEOUT') from None
    except PermissionError:raise SourceError('PERMISSION_DENIED') from None
    except FileNotFoundError:raise SourceError('UNSUPPORTED') from None
    except JournalError as e:raise SourceError(e.code) from None


def checked(code,stdout,stderr):
    if len(stdout)>65536 or len(stderr)>4096:raise SourceError('OUTPUT_LIMIT')
    lower=stderr.lower()
    if any(x in lower for x in (b'permission denied',b'access denied',b'not seeing messages',b'operation not permitted')):
        raise SourceError('PERMISSION_DENIED')
    if code!=0 or stderr:raise SourceError('UNSUPPORTED')


def journal_command(tool,a):
    if tool not in SOURCES or not valid_window(a) or a.get('limit',LIMIT)!=LIMIT or 'cursor' in a:
        raise SourceError('POLICY_DENIED')
    command=('/usr/bin/journalctl','--no-pager','--no-hostname','--output=json','--lines=101',
        '--output-fields=__REALTIME_TIMESTAMP,PRIORITY,MESSAGE,_TRANSPORT,SYSLOG_IDENTIFIER,_PID,_UID,__CURSOR',
        '--since='+a['since'],'--until='+a['until'])
    # Filter relevant messages before the bounded 101-row read. Unrelated kernel
    # chatter must not consume the entire OOM result budget. Patterns are fixed
    # application constants, never user/model regexes or commands.
    if tool=='memory_oom_events':
        return command+('--dmesg','--case-sensitive=no',
            '--grep=Killed process [0-9]+|oom-kill:|invoked oom-killer')
    command+=('--case-sensitive=no','--grep=Failed (password|publickey|keyboard-interactive)|authentication failure')
    filters=[]
    for identifier in AUTH_IDENTIFIERS:
        if filters:filters.append('+')
        filters.append('SYSLOG_IDENTIFIER='+identifier)
    return command+tuple(filters)


def records(payload,a,tool):
    from src.mcp_server.journal_parser import parse_journal_json,_object
    parsed=parse_journal_json(payload)
    if len(parsed)>LIMIT:raise SourceError('OUTPUT_LIMIT')
    result=[];seen=set();start,end=parse_stamp(a['since']),parse_stamp(a['until'])
    for entry,line in zip(parsed,payload.splitlines(),strict=True):
        raw=json.loads(line,object_pairs_hook=_object)
        observed=parse_stamp(entry['timestamp'])
        if not start<=observed<=end:raise SourceError('EXECUTION_UNKNOWN')
        if tool=='memory_oom_events':
            if raw.get('_TRANSPORT')!='kernel':raise SourceError('EXECUTION_UNKNOWN')
            service='kernel'
        else:
            service=raw.get('SYSLOG_IDENTIFIER')
            if service not in AUTH_IDENTIFIERS or raw.get('_UID')!='0':raise SourceError('EXECUTION_UNKNOWN')
            service='sshd' if service=='sshd-session' else service
        if observed==end:continue  # Explicit half-open interval.
        cursor=raw.get('__CURSOR')
        if cursor is not None and (type(cursor) is not str or len(cursor)>2048):raise SourceError('EXECUTION_UNKNOWN')
        identity=cursor or hashlib.sha256((entry['timestamp']+'|'+service+'|'+str(raw.get('_PID',''))+'|'+entry['message']).encode()).hexdigest()
        if identity not in seen:result.append((entry['timestamp'],service,entry['message']));seen.add(identity)
    return result


def pseudonym(key,kind,value):
    return kind+'-'+hmac.new(key,value.encode(),hashlib.sha256).hexdigest()[:24]


def extract(items,tool,key):
    events=[];evidence=[]
    for at,service,message in items:
        event=None
        if tool=='memory_oom_events':
            match=OOM.search(message)
            if match:
                pid,process,total,rss=match.groups()
                if not 0<int(pid)<2**31 or max(int(total),int(rss))>2**63-1:raise SourceError('EXECUTION_UNKNOWN')
                event=dict(timestamp=at,pid=int(pid),process=process if process in KNOWN_PROCESSES else pseudonym(key,'process',process),
                    anon_rss_kb=int(rss),total_vm_kb=int(total),message='Kernel reported a process killed by the OOM killer.')
            elif 'oom-kill:' in message or 'invoked oom-killer' in message:
                event=dict(timestamp=at,pid=None,process=None,message='Kernel OOM diagnostic record; no process kill inferred from this record.')
        else:
            match=LOGIN.search(message) if service=='sshd' else None
            if match:user,ip=match.groups()
            elif 'authentication failure' in message.casefold():
                usermatch=re.search(r'\buser=([^\s;]+)',message)
                ipmatch=re.search(r'\brhost=([^\s;]+)',message)
                user=usermatch.group(1) if usermatch else 'unknown'
                ip=ipmatch.group(1) if ipmatch else None
            else:continue
            if ip is not None:
                try:ip=str(ipaddress.ip_address(ip))
                except ValueError:raise SourceError('EXECUTION_UNKNOWN') from None
            event=dict(timestamp=at,user='root' if user=='root' else pseudonym(key,'user',user),
                source_ip=pseudonym(key,'ip',ip) if ip else None,
                service=service,user_class='root' if user=='root' else 'unknown',
                message='Authentication failure observed; identity and address pseudonymized for this run.')
        if event is not None:
            identifier='event-'+secrets.token_hex(16);event['evidence_id']=identifier
            events.append(event)
            evidence.append(dict(evidence_id=identifier,source='collector.'+tool+'.'+SOURCES[tool]+'.v1',
                observed_at=at,unit=None,excerpt=json.dumps({k:v for k,v in event.items() if k!='evidence_id'}),
                redacted=True,truncated=False))
    return events,evidence


def fixed_lines(paths):
    """Internal application constants only; reject symlinks, rotation races and partial writes."""
    lines=[];opened=False
    for name in paths:
        fd=None
        try:
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            before=os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):raise SourceError('POLICY_DENIED')
            offset=max(0,before.st_size-65536);os.lseek(fd,offset,os.SEEK_SET)
            raw=os.read(fd,65537)
            if len(raw)>65536:raise SourceError('OUTPUT_LIMIT')
            after=os.fstat(fd);current=os.stat(name,follow_symlinks=False)
            if (before.st_ino,before.st_size)!=(after.st_ino,after.st_size) or (current.st_dev,current.st_ino)!=(before.st_dev,before.st_ino):
                raise SourceError('EXECUTION_UNKNOWN')
            if offset:raw=raw.partition(b'\n')[2]
            if raw and not raw.endswith(b'\n'):raise SourceError('EXECUTION_UNKNOWN')
            opened=True;lines.extend(raw.decode('utf-8').splitlines())
        except FileNotFoundError:continue
        except PermissionError:raise SourceError('PERMISSION_DENIED') from None
        finally:
            if fd is not None:os.close(fd)
    if not opened:raise SourceError('UNSUPPORTED')
    return lines


def local_zone():
    from zoneinfo import ZoneInfo
    with open('/etc/localtime','rb') as f:return ZoneInfo.from_file(f)


def utc_candidate(value,fmt,start,end,zone):
    naive=datetime.strptime(value,fmt)
    candidates=set()
    for fold in (0,1):
        aware=naive.replace(tzinfo=zone,fold=fold)
        at=aware.astimezone(timezone.utc)
        if at.astimezone(zone).replace(tzinfo=None)==naive and start<=at<end:candidates.add(at)
    if len(candidates)>1:raise SourceError('EXECUTION_UNKNOWN')
    return next(iter(candidates)) if candidates else None


def auth_files(a):
    """Local-time/year are resolved from trusted host zone and requested interval."""
    result=[];seen=set();start,end=parse_stamp(a['since']),parse_stamp(a['until']);zone=local_zone()
    for line in fixed_lines(('/var/log/auth.log.1','/var/log/auth.log')):
        m=AUTH_LINE.fullmatch(line)
        if not m:continue
        date,service,pid,message=m.groups();candidates=[]
        for year in set((start.astimezone(zone).year,end.astimezone(zone).year)):
            try:at=utc_candidate(str(year)+' '+date,'%Y %b %d %H:%M:%S',start,end,zone)
            except ValueError:continue
            if at is not None:candidates.append(at)
        if len(candidates)!=1:continue
        identity=hashlib.sha256(line.encode()).digest()
        if identity not in seen:result.append((stamp(candidates[0]),service,message));seen.add(identity)
    if len(result)>LIMIT:raise SourceError('OUTPUT_LIMIT')
    return sorted(result)


def nginx_file(a):
    from agent.diagnostics.journal_policy import project_message
    from src.mcp_server.log_search_matcher import match_literal_indices
    start,end=parse_stamp(a['since']),parse_stamp(a['until']);zone=local_zone();records=[];seen=set()
    for line in fixed_lines(('/var/log/nginx/error.log.1','/var/log/nginx/error.log')):
        m=re.fullmatch(r'(\d{4}/\d\d/\d\d \d\d:\d\d:\d\d) \[([a-z]+)\] (.*)',line)
        if not m:continue
        at=utc_candidate(m[1],'%Y/%m/%d %H:%M:%S',start,end,zone)
        if at is None:continue
        identity=hashlib.sha256(line.encode()).digest()
        if identity not in seen:records.append((stamp(at),m[3]));seen.add(identity)
    if len(records)>LIMIT:raise SourceError('OUTPUT_LIMIT')
    records.sort()
    selected=match_literal_indices([text for at,text in records],a['query'],limit=LIMIT)
    return [dict(timestamp=records[i][0],message=project_message(records[i][1])) for i in selected]


def ram_snapshot():
    values={}
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith(('MemTotal:','MemAvailable:')):
            name,value,unit=line.split()
            if unit!='kB' or not value.isdecimal():raise SourceError('EXECUTION_UNKNOWN')
            values[name[:-1]]=int(value)*1024
    if set(values)!={'MemTotal','MemAvailable'} or not 0<=values['MemAvailable']<=values['MemTotal']:
        raise SourceError('EXECUTION_UNKNOWN')
    return values


async def collect_events(tool,a):
    coverage=[];items=[];source=SOURCES[tool]
    try:
        code,stdout,stderr=await run(journal_command(tool,a));checked(code,stdout,stderr)
        items=records(stdout,a,tool)
        coverage.append(dict(source=source,status='ok',oldest_available_at=None))
    except SourceError as e:
        if e.code not in ('PERMISSION_DENIED','UNSUPPORTED'):raise
        coverage.append(dict(source=source,status='permission_denied' if e.code=='PERMISSION_DENIED' else 'unavailable',oldest_available_at=None))
        if tool=='failed_logins':
            try:
                items=auth_files(a);coverage.append(dict(source='auth.log',status='ok',oldest_available_at=None))
            except SourceError as failure:
                if failure.code not in ('PERMISSION_DENIED','UNSUPPORTED'):raise
                coverage.append(dict(source='auth.log',status='permission_denied' if failure.code=='PERMISSION_DENIED' else 'unavailable',oldest_available_at=None))
    events,evidence=extract(items,tool,secrets.token_bytes(32))
    if tool=='failed_logins' and len(coverage)>1 and coverage[-1]['status']=='ok':
        for e in evidence:e['source']='collector.failed_logins.auth.log.v1'
    if tool=='memory_oom_events':
        memory=ram_snapshot()
        evidence.append(dict(evidence_id='ram-'+secrets.token_hex(16),source='collector.memory_oom_events.meminfo.v1',
            observed_at=stamp(datetime.now(timezone.utc)),unit=None,excerpt=json.dumps(memory),redacted=True,truncated=False))
    elif any(c['status']=='ok' for c in coverage):
        evidence.append(dict(evidence_id='coverage-'+secrets.token_hex(16),source='collector.failed_logins.coverage.v1',
            observed_at=stamp(datetime.now(timezone.utc)),unit=None,excerpt='Read completed; bounded source visibility, not proof of no authentication failures.',redacted=True,truncated=False))
    else:raise SourceError('PERMISSION_DENIED' if any(c['status']=='permission_denied' for c in coverage) else 'UNSUPPORTED')
    return dict(events=events,count=len(events),limit=LIMIT,coverage=coverage),evidence


CONFIG_COMMAND=('/usr/bin/bwrap','--unshare-pid','--unshare-net','--new-session','--die-with-parent',
    '--ro-bind','/','/','--proc','/proc','--dev','/dev',
    '--tmpfs','/var/log/nginx','--tmpfs','/run','--tmpfs','/tmp','--',
    '/usr/sbin/nginx','-t','-q','-e','stderr')


async def check_config(a):
    if a.get('profile')!='nginx' or a.get('unit','nginx.service')!='nginx.service':raise SourceError('POLICY_DENIED')
    # nginx -t opens configured files. Enforce a read-only filesystem and isolated
    # process/network namespaces, never fall back to an unsandboxed test.
    code,stdout,stderr=await run(CONFIG_COMMAND)
    return config_result(code,stdout,stderr)


def config_result(code,stdout,stderr):
    """Same privacy/error mapping for either trusted sandbox implementation."""
    lower=stderr.lower()
    if any(m in lower for m in (b'permission denied',b'operation not permitted',b'creating new namespace',b'read-only file system')):
        raise SourceError('PERMISSION_DENIED')
    if code==0:valid=True;issues=[];message='Nginx configuration syntax check succeeded in read-only sandbox; no reload or health assertion.'
    # Severity/prefix alone does not prove a syntax defect: nginx also uses
    # [emerg] for unavailable sockets, permissions and runtime directories.
    elif any(m in lower for m in (b'unknown directive',b'invalid number of arguments',
            b'unexpected "',b'unexpected end of file',b'invalid parameter',
            b'directive is not allowed here',b'directive is duplicate')):
        valid=False;issues=[dict(code='INVALID_CONFIG',severity='error',message='Nginx rejected configuration; raw diagnostic withheld.')]
        message='Nginx configuration validation failed; no files or services changed.'
    else:raise SourceError('UNSUPPORTED')
    return dict(profile='nginx',unit='nginx.service',valid=valid,issues=issues,message=message)
