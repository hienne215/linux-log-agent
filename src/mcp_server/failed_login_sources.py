"""P03-03 Linux reads with fixed paths/argv and bounded output; no sudo.

No production entrypoint enables this provider automatically. An administrator
must review read permissions and provision the pseudonym key/scope locally.
"""
import os
import stat
import subprocess
import sys
import threading
import time
from datetime import timezone

from .failed_login_events import parse_auth, parse_journal
from .failed_logins_adapter import SourceSnapshot

MAX_BYTES = 4 * 1024 * 1024
AUTH_FILES = ('/var/log/auth.log.1', '/var/log/auth.log')
JOURNAL = ('/usr/bin/journalctl', '--no-pager', '--output=json',
           '--output-fields=__CURSOR,__REALTIME_TIMESTAMP,SYSLOG_IDENTIFIER,SYSLOG_PID,_PID,MESSAGE',
           'SYSLOG_FACILITY=4', 'SYSLOG_FACILITY=10')
ENV = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'LANG': 'C', 'SYSTEMD_COLORS': '0'}


def bounded_command(argv, timeout):
    """Bound both pipes while reading, not after communicate buffers everything."""
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=ENV, close_fds=True, shell=False)
    buffers, cut, stop = [bytearray(), bytearray()], threading.Event(), threading.Event()
    def pump(stream, target, cap):
        try:
            while True:
                chunk = os.read(stream.fileno(), 65536)
                if not chunk:
                    break
                room = cap - len(target)
                target.extend(chunk[:max(0, room)])
                if len(chunk) > room:
                    cut.set()
                    stop.set()
                    break
        finally:
            stream.close()
    threads = [threading.Thread(target=pump, args=(proc.stdout, buffers[0], MAX_BYTES), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, buffers[1], 4096), daemon=True)]
    for thread in threads:
        thread.start()
    deadline, timed_out = time.monotonic() + max(timeout, 0), False
    try:
        while proc.poll() is None and not stop.wait(0.01):
            if time.monotonic() >= deadline:
                timed_out = True
                break
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=2)
    if any(thread.is_alive() for thread in threads):
        raise RuntimeError('Pipe cleanup incomplete')
    return proc.returncode, bytes(buffers[0]), bytes(buffers[1]), cut.is_set(), timed_out


def journal_access_missing():
    import grp
    allowed = set()
    for name in ('adm', 'systemd-journal'):
        try:
            allowed.add(grp.getgrnam(name).gr_gid)
        except KeyError:
            pass
    return os.geteuid() != 0 and not allowed.intersection(set(os.getgroups()) | {os.getegid()})


def read_regular(path, cap):
    """Open fixed system log without following a symlink; preserve inode identity."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != 0:
            raise PermissionError('Untrusted log')
        data = bytearray()
        wanted = min(before.st_size, cap)
        while len(data) < wanted:
            chunk = os.read(fd, min(65536, wanted - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        # In-place changes/append during acquisition are not an atomic snapshot.
        changed = (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        return bytes(data), (before.st_dev, before.st_ino, before.st_ctime_ns), changed or before.st_size > cap
    finally:
        os.close(fd)


class LinuxLoginSources:
    def __init__(self, privacy, auth_year, auth_timezone):
        # These parameters are trusted deployment configuration, never MCP fields.
        if auth_timezone is None or not 1970 <= auth_year <= 9999:
            raise ValueError('Explicit auth.log year and timezone required')
        self.privacy, self.year, self.zone = privacy, auth_year, auth_timezone
        self.acquisition_trace = ()

    def __call__(self, since, until):
        if not sys.platform.startswith('linux'):
            self.acquisition_trace = (('journal.auth', False, 'platform_unsupported'),
                                      ('auth.log', False, 'platform_unsupported'))
            return [SourceSnapshot(s, 'unavailable') for s in ('journal.auth', 'auth.log')]
        deadline = time.monotonic() + 8
        journal = self._journal(max(0, deadline - time.monotonic()))
        # Both sources are acquired only in a deployment that authorizes both
        # fixed readers. Selection/fallback is a separate adapter policy.
        auth_attempted = time.monotonic() < deadline
        auth = self._auth(deadline)
        self.acquisition_trace = (('journal.auth', True, journal.status),
                                  ('auth.log', auth_attempted,
                                   auth.status if auth_attempted else 'budget_exhausted'))
        return [journal, auth]

    def _journal(self, remaining):
        result = SourceSnapshot('journal.auth', 'unavailable')
        try:
            rc, data, err, cut, timeout = bounded_command(JOURNAL, remaining)
            # stdout is untrusted log data, never an execution diagnostic.
            hints = err.lower()
            if (not timeout and rc != 0 and any(x in hints for x in
                    (b'permission denied', b'not seeing messages from other users', b'access denied'))):
                result.status = 'permission_denied'
                return result
            if timeout or (rc != 0 and not cut):
                return result
            lines = data.decode('utf-8', 'replace').splitlines()
            if cut and lines:
                lines = lines[:-1]
            if not lines and journal_access_missing():
                result.status = 'permission_denied'
                return result
            events, incomplete = parse_journal(lines, self.privacy)
            # Retention is not inferred from first matching login event.
            import json
            from datetime import datetime
            times = []
            for line in lines:
                try:
                    times.append(datetime.fromtimestamp(int(json.loads(line)['__REALTIME_TIMESTAMP']) / 1000000, timezone.utc))
                except (ValueError, TypeError, KeyError, OverflowError, OSError):
                    incomplete = True
            result.status, result.events = 'ok', events
            result.oldest = min(times) if times else None
            result.incomplete, result.truncated = incomplete, cut
        except PermissionError:
            result.status = 'permission_denied'
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass
        return result

    def _auth(self, deadline):
        result = SourceSnapshot('auth.log', 'unavailable')
        lines, record_ids, seen, total, denied = [], [], set(), 0, False
        for path in AUTH_FILES:
            if time.monotonic() >= deadline:
                result.truncated = True
                break
            try:
                data, identity, cut = read_regular(path, max(0, MAX_BYTES - total))
                # Rename rotation can expose the same inode under both fixed names.
                if identity in seen:
                    continue
                seen.add(identity)
                total += len(data)
                chunks = data.splitlines(keepends=True)
                if cut and chunks:
                    chunks = chunks[:-1]
                offset = 0
                for chunk in chunks:
                    lines.append(chunk.decode('utf-8', 'replace'))
                    record_ids.append([*identity, offset])
                    offset += len(chunk)
                result.truncated |= cut
            except FileNotFoundError:
                pass
            except PermissionError:
                denied = True
            except OSError:
                pass
        if not seen:
            result.status = 'permission_denied' if denied else 'unavailable'
            return result
        result.status = 'ok'
        result.events, _ = parse_auth(lines, self.privacy, self.year, self.zone, record_ids)
        # .1/current is not proof of complete retention; .gz history is not read.
        # Copytruncate and yearless timestamps are deliberately incomplete.
        result.oldest = None
        result.incomplete = True
        return result
