"""memory_oom_events collector (P03-02). Read-only, standard library only, Python 3.8+.

Reads kernel OOM-kill messages from the systemd journal and from dmesg and reports,
per source, whether it could really be read. A source that cannot be read is never
reported as "no OOM events": the caller can tell the two cases apart.
"""
import hashlib
import json
import os
import re
import stat
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

TOOL = "memory_oom_events"
SOURCES = ("journal.kernel", "dmesg")
UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MAX_WINDOW = timedelta(hours=24)
DEFAULT_LIMIT = 100
MAX_BYTES = 4 * 1024 * 1024      # stdout read per command
MAX_MESSAGE = 2048
INCIDENT_US = 1000000           # lines of ONE kill are printed within a second of each other
BUDGET_SECONDS = 8.0             # whole call; the server allows 10 s
# The server sends the result twice (text + structuredContent) inside one 64 KiB frame.
MAX_RESPONSE_BYTES = 28000
MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_SNAPSHOT_EVENTS = 2000
JOURNALCTL = ("/usr/bin/journalctl", "/bin/journalctl")
DMESG = ("/usr/bin/dmesg", "/bin/dmesg")
CHILD_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "SYSTEMD_COLORS": "0"}
DENIED_HINTS = ("not seeing messages from other users", "permission denied",
                "insufficient permissions", "operation not permitted", "access denied")

_TS = re.compile(r"^(\d{4})-(\d\d)-(\d\d)[Tt](\d\d):(\d\d):(\d\d)(?:\.(\d+))?"
                 r"(?:[Zz]|([+-])(\d\d):(\d\d))$")
_DMESG_LINE = re.compile(r"^\[\s*(\d+)\.(\d{1,9})\]\s?(.*)$")      # [  471.301396] message
_KILL = re.compile(r"^(?:(?:Memory cgroup out of memory|Out of memory(?: \(\w+\))?): )?"
                   r"Kill(?:ed)? process (\d+) \((.{1,64}?)\)")
_OOMKILL = re.compile(r"\boom-kill:.*?,task=([^,]{1,64}),pid=(\d+)")
_VM = re.compile(r"total-vm:(\d+)kB")
_ANON = re.compile(r"anon-rss:(\d+)kB")
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# A process name is workload-controlled free text and no filter can tell a harmless name from a
# secret one. So a name is echoed only if it is on an allowlist (exact match); anything else becomes
# "[REDACTED]" and the evidence says so. The built-in list is small and common; the operator can add
# names in a root-owned file that is not group/world-writable.
REDACTED = "[REDACTED]"
_NAME_OK = re.compile(r"^[A-Za-z0-9._+\-/:@]{1,15}$")      # shape of an allowlist entry
DEFAULT_ALLOWED = frozenset({
    "nginx", "sshd", "systemd", "systemd-journal", "java", "python", "python3", "python3.8",
    "python3.12", "node", "mysqld", "mariadbd", "postgres", "redis-server", "apache2", "httpd",
    "php-fpm", "gunicorn", "uwsgi", "dockerd", "containerd", "stress", "stress-ng",
    "lab-oom", "lab-backend"})
OPERATOR_ALLOWLIST = "/etc/linux-log-agent/oom-process-allowlist"
ALLOWED = DEFAULT_ALLOWED


# ---------------------------------------------------------------- helpers
def parse_ts(value):
    """RFC 3339 string -> aware UTC datetime. Raises ValueError."""
    m = _TS.match(value) if isinstance(value, str) else None
    if not m:
        raise ValueError("bad timestamp")
    y, mo, d, h, mi, s = (int(m.group(i)) for i in range(1, 7))
    micro = int((m.group(7) or "0")[:6].ljust(6, "0"))
    dt = datetime(y, mo, d, h, mi, s, micro, tzinfo=UTC)
    if m.group(8):
        offset = timedelta(hours=int(m.group(9)), minutes=int(m.group(10)))
        dt = dt - offset if m.group(8) == "+" else dt + offset
    return dt


def fmt_ts(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(text, limit):
    return _CTRL.sub(" ", text).strip()[:limit]


def load_operator_names(path=OPERATOR_ALLOWLIST):
    """Extra allowed names, one per line. Ignored unless the file is root-owned and not writable by others."""
    try:
        info = os.stat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            return set()
        with open(path, encoding="utf-8") as handle:
            lines = handle.read(8192).splitlines()[:200]
    except (OSError, UnicodeError):
        return set()
    names = (line.strip() for line in lines)
    return {n for n in names if n and not n.startswith("#") and _NAME_OK.match(n)}


def safe_name(raw):
    """Return (name, redacted). Only an allowlisted name is ever returned as text."""
    name = clean(raw, 128)
    if not name:
        return None, False
    if name in ALLOWED:
        return name, False
    return REDACTED, True


def _norm_boot(value):
    value = value.strip().replace("-", "").lower() if isinstance(value, str) else ""
    return value if re.fullmatch(r"[0-9a-f]{32}", value) else None


def current_boot_id(path="/proc/sys/kernel/random/boot_id"):
    try:
        with open(path, encoding="ascii") as handle:
            return _norm_boot(handle.read(64))
    except (OSError, UnicodeError):
        return None


def default_boot_wall():
    """Wall-clock time of the kernel's time zero (what dmesg timestamps count from), or None."""
    try:
        return datetime.fromtimestamp(time.time() - time.clock_gettime(time.CLOCK_MONOTONIC), UTC)
    except (AttributeError, OSError, OverflowError, ValueError):
        return None


def find_exe(candidates):
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def lacks_journal_access():
    """True if this process is clearly not allowed to read the system journal."""
    try:
        import grp
        if os.geteuid() == 0:
            return False
        wanted = set()
        for name in ("adm", "systemd-journal"):
            try:
                wanted.add(grp.getgrnam(name).gr_gid)
            except KeyError:
                pass
        return not (wanted & (set(os.getgroups()) | {os.getegid()}))
    except (ImportError, AttributeError):
        return False


def run_cmd(argv, timeout, max_bytes=MAX_BYTES, first_lines=None):
    """Run a fixed argv (no shell). Returns (rc, stdout, stderr, truncated, timed_out)."""
    proc = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=CHILD_ENV, close_fds=True)
    out, err = bytearray(), bytearray()
    state = {"stop": False, "truncated": False}

    def pump(stream, buf, cap, stop_lines):
        fd = stream.fileno()
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            room = cap - len(buf)
            buf.extend(chunk[:max(room, 0)])
            if len(chunk) > room:
                state["truncated"] = state["stop"] = True
                return
            if stop_lines and buf.count(b"\n") >= stop_lines:
                state["stop"] = True
                return

    threads = [threading.Thread(target=pump, args=(proc.stdout, out, max_bytes, first_lines), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, err, 4096, None), daemon=True)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + max(timeout, 0.0)
    timed_out = False
    while proc.poll() is None and not state["stop"]:
        if time.monotonic() > deadline:
            timed_out = True
            break
        time.sleep(0.01)
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(2)
    except subprocess.TimeoutExpired:
        pass
    for t in threads:
        t.join(1)
    return proc.returncode, bytes(out), bytes(err), state["truncated"], timed_out


# ---------------------------------------------------------------- parser
def _fields(pid, name, msg, kind, sizes=True):
    pid = int(pid)
    vm = _VM.search(msg) if sizes else None
    anon = _ANON.search(msg) if sizes else None
    process, redacted = safe_name(name)
    raw = clean(name, 128)
    return {"pid": pid if pid >= 1 else None, "process": process, "redacted": redacted, "kind": kind,
            "name_key": hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12] if raw else None,
            "total_vm_kb": int(vm.group(1)) if vm else None,
            "anon_rss_kb": int(anon.group(1)) if anon else None}


def render(fields):
    """Rebuild the message from parsed fields; no raw kernel text, cgroup path or UID is echoed."""
    name = fields["process"] if fields["process"] is not None else "?"
    pid = fields["pid"] if fields["pid"] is not None else "?"
    if fields["kind"] == "oom-kill":
        return "oom-kill: task=%s pid=%s" % (name, pid)
    prefix = "Memory cgroup out of memory" if fields["kind"] == "cgroup" else "Out of memory"
    text = "%s: Killed process %s (%s)" % (prefix, pid, name)
    sizes = []
    if fields["total_vm_kb"] is not None:
        sizes.append("total-vm:%dkB" % fields["total_vm_kb"])
    if fields["anon_rss_kb"] is not None:
        sizes.append("anon-rss:%dkB" % fields["anon_rss_kb"])
    return text + (" " + ", ".join(sizes) if sizes else "")


def parse_oom_message(message):
    """Return pid/process/size fields if the kernel line reports an OOM kill, else None.

    'invoked oom-killer' lines are context and are not counted as separate events.
    """
    msg = clean(message, MAX_MESSAGE)           # control characters never reach the regexes
    m = _KILL.match(msg)
    if m:
        prefixed = msg.startswith(("Out of memory", "Memory cgroup out of memory"))
        if prefixed or "total-vm:" in msg:
            kind = "cgroup" if msg.startswith("Memory cgroup out of memory") else "global"
            return _fields(m.group(1), m.group(2), msg, kind)
        return None
    m = _OOMKILL.search(msg)
    if m:
        return _fields(m.group(2), m.group(1), msg, "oom-kill", sizes=False)
    return None


def _event(source, dt, fields, key, boot, mono):
    text = render(fields)
    digest = hashlib.sha1("|".join((source, str(key), fmt_ts(dt), str(fields["pid"]), text))
                          .encode("utf-8", "replace")).hexdigest()[:16]
    score = ((fields["total_vm_kb"] is not None) * 4 + (fields["anon_rss_kb"] is not None) * 2
             + (fields["process"] is not None) + (0.5 if source == SOURCES[0] else 0))
    return {"_dt": dt, "_src": source, "_score": score, "_boot": boot, "_mono": mono, "message": text,
            "evidence_id": "oom-" + digest, **fields}


def parse_journal(text, since, until, truncated=False):
    """Return (events in [since, until), number of journal records seen)."""
    lines = text.split("\n")
    if truncated:
        lines = lines[:-1]
    events, records = [], 0
    for line in lines:
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
            us = int(rec["__REALTIME_TIMESTAMP"])
        except (ValueError, KeyError, TypeError):
            continue
        records += 1
        msg = rec.get("MESSAGE")
        if not isinstance(msg, str):
            continue
        dt = EPOCH + timedelta(microseconds=us)
        fields = parse_oom_message(msg)
        if fields and since <= dt < until:
            try:
                mono = int(rec["_SOURCE_MONOTONIC_TIMESTAMP"])
            except (KeyError, ValueError, TypeError):
                mono = None
            events.append(_event(SOURCES[0], dt, fields, rec.get("__CURSOR", us),
                                 _norm_boot(rec.get("_BOOT_ID")), mono))
    return events, records


def parse_dmesg(text, boot_wall, boot, since, until, truncated=False):
    """Return (events in window, wall time of the oldest line or None).

    dmesg counts seconds since the kernel started; the wall time is boot_wall + that offset.
    """
    lines = text.split("\n")
    if truncated:
        lines = lines[:-1]
    events, oldest = [], None
    for line in lines:
        m = _DMESG_LINE.match(line)
        if not m:
            continue
        mono = int(m.group(1)) * 1000000 + int(m.group(2)[:6].ljust(6, "0"))
        dt = boot_wall + timedelta(microseconds=mono)
        oldest = oldest or dt
        fields = parse_oom_message(m.group(3))
        if fields and since <= dt < until:
            events.append(_event(SOURCES[1], dt, fields, mono, boot, mono))
    return events, oldest


def _same_kill(a, b):
    """One kill, shown by several records. Only provable from identity: same boot, same pid,
    compatible process name and kernel timestamps within one second. Wall-clock time alone, or a
    pid/name match across boots, is never enough: those are independent kills."""
    return (a["_boot"] is not None and a["_boot"] == b["_boot"]
            and a["_mono"] is not None and b["_mono"] is not None
            and abs(a["_mono"] - b["_mono"]) <= INCIDENT_US
            and a["pid"] is not None and a["pid"] == b["pid"]
            and (a["name_key"] == b["name_key"] or a["name_key"] is None or b["name_key"] is None))


def dedupe(events):
    """Keep the most informative record of each kill; without identity, keep everything."""
    kept = []
    for e in sorted(events, key=lambda x: (-x["_score"], x["_dt"])):
        if not any(_same_kill(k, e) for k in kept):
            kept.append(e)
    return kept


# ---------------------------------------------------------------- sources
def _source(name, status, oldest=None, events=None, truncated=False):
    return {"source": name, "status": status, "oldest": oldest,
            "events": events or [], "truncated": truncated}


def _denied(err, out):
    lines = [l for l in out.decode("utf-8", "replace").splitlines()[:50] if not l.startswith("{")]
    text = (err.decode("utf-8", "replace") + "\n" + "\n".join(lines)).lower()
    return any(h in text for h in DENIED_HINTS)


def read_journal(run, find, lacks_access, since, until, remaining):
    exe = find(JOURNALCTL)
    if exe is None or remaining() <= 0:
        return _source(SOURCES[0], "unavailable")
    argv = [exe, "--no-pager", "-o", "json", "_TRANSPORT=kernel",
            "--since", (since - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "--until", (until + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S UTC")]
    rc, out, err, truncated, timed_out = run(argv, min(3.0, remaining()), MAX_BYTES, None)
    if timed_out:
        return _source(SOURCES[0], "unavailable")
    if _denied(err, out):
        return _source(SOURCES[0], "permission_denied")
    if rc != 0 and not truncated:
        return _source(SOURCES[0], "unavailable")      # even if some valid-looking record came out
    events, records = parse_journal(out.decode("utf-8", "replace"), since, until, truncated)
    if records == 0 and lacks_access():
        # journalctl may print nothing when the group is missing: never read that as "no OOM".
        return _source(SOURCES[0], "permission_denied")
    oldest = None
    if remaining() > 0:
        rc2, out2, _, _, _ = run([exe, "--no-pager", "-o", "json", "_TRANSPORT=kernel"],
                                 min(2.0, remaining()), 65536, 3)
        for line in out2.decode("utf-8", "replace").split("\n"):
            if line.startswith("{"):
                try:
                    oldest = EPOCH + timedelta(microseconds=int(json.loads(line)["__REALTIME_TIMESTAMP"]))
                except (ValueError, KeyError, TypeError):
                    pass
                break
    return _source(SOURCES[0], "ok", oldest, events, truncated)


def read_dmesg(run, find, since, until, remaining, boot_wall, boot):
    exe = find(DMESG)
    if exe is None or boot_wall is None or remaining() <= 0:
        return _source(SOURCES[1], "unavailable")
    rc, out, err, truncated, timed_out = run([exe], min(3.0, remaining()), MAX_BYTES, None)
    if timed_out:
        return _source(SOURCES[1], "unavailable")
    if _denied(err, out):
        return _source(SOURCES[1], "permission_denied")
    if rc != 0 and not truncated:
        return _source(SOURCES[1], "unavailable")
    events, oldest = parse_dmesg(out.decode("utf-8", "replace"), boot_wall, boot, since, until, truncated)
    return _source(SOURCES[1], "ok", oldest, events, truncated)


# ---------------------------------------------------------------- response
def _id(req, key):
    value = req.get(key)
    return value if isinstance(value, str) and 1 <= len(value) <= 128 else "invalid-input"


def _envelope(req, status, data, evidence=(), error_code=None, truncated=False):
    now = fmt_ts(datetime.now(UTC))
    session = req.get("session_id")
    return {"schema_version": "1.0.0",
            "session_id": session if isinstance(session, str) and 1 <= len(session) <= 128 else None,
            "trace_id": _id(req, "trace_id"), "logical_tool_call_id": _id(req, "logical_tool_call_id"),
            "attempt_id": _id(req, "attempt_id"), "tool": TOOL, "status": status,
            "started_at": now, "completed_at": now, "duration_ms": 0, "data": data,
            "evidence": list(evidence), "error_code": error_code, "truncated": truncated,
            "next_cursor": None}


def _invalid(req, message):
    return _envelope(req, "error", {"message": message}, error_code="INVALID_INPUT")


def _coverage(sources):
    return [{"source": s["source"], "status": s["status"],
             "oldest_available_at": fmt_ts(s["oldest"]) if s["oldest"] else None} for s in sources]


def _size(response):
    return len(json.dumps(response, ensure_ascii=False).encode("utf-8"))


def collect(req, run=run_cmd, find=find_exe, lacks_access=lacks_journal_access, clock=time.monotonic,
            allow_file=OPERATOR_ALLOWLIST, boot_id=current_boot_id, boot_wall=default_boot_wall,
            snapshot=False):
    global ALLOWED
    ALLOWED = DEFAULT_ALLOWED | load_operator_names(allow_file)
    start = clock()

    def remaining():
        return BUDGET_SECONDS - (clock() - start)

    try:
        since, until = parse_ts(req.get("since")), parse_ts(req.get("until"))
    except ValueError:
        return _invalid(req, "since/until must be RFC 3339 timestamps")
    limit = req.get("limit", DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        return _invalid(req, "limit must be an integer from 1 to 200")
    if "cursor" in req:
        return _invalid(req, "cursor is not supported: this tool does not paginate")
    if not (timedelta(0) < until - since <= MAX_WINDOW):
        return _invalid(req, "window must satisfy 0 < until - since <= 24h")

    journal = read_journal(run, find, lacks_access, since, until, remaining)
    dmesg = read_dmesg(run, find, since, until, remaining, boot_wall(), boot_id())
    sources = [journal, dmesg]
    coverage = _coverage(sources)
    readable = [s for s in sources if s["status"] == "ok"]
    if not readable:
        denied_only = all(s["status"] == "permission_denied" for s in sources)
        message = "; ".join("%s: %s" % (s["source"], s["status"].replace("_", " ")) for s in sources)
        return _envelope(req, "error", {"message": message, "coverage": coverage},
                         error_code="PERMISSION_DENIED" if denied_only else "UNSUPPORTED")

    events = sorted(dedupe(journal["events"] + dmesg["events"]), key=lambda e: e["_dt"])
    retained_limit = MAX_SNAPSHOT_EVENTS if snapshot else limit
    truncated = any(s["truncated"] for s in readable) or len(events) > retained_limit
    events = events[-retained_limit:]              # trusted internal snapshot is not a wire page
    # R3: judge every source on its own. A source that starts after "since" (dmesg only reaches
    # back to the last boot) leaves a history gap even when the other source covers the window.
    gap = any(s["status"] != "ok" or s["oldest"] is None or s["oldest"] > since for s in sources)

    def build(items, trunc):
        data = {"events": [{"timestamp": fmt_ts(e["_dt"]), "process": e["process"],
                            "message": e["message"], "pid": e["pid"],
                            "anon_rss_kb": e["anon_rss_kb"], "total_vm_kb": e["total_vm_kb"],
                            "evidence_id": e["evidence_id"]} for e in items],
                "count": len(items), "limit": limit, "coverage": coverage}
        evidence = [{"evidence_id": e["evidence_id"], "observed_at": fmt_ts(e["_dt"]),
                     "source": e["_src"], "unit": None, "excerpt": e["message"],
                     "redacted": e["redacted"], "truncated": False} for e in items]
        status = "partial" if (gap or trunc) else "ok"
        # R3 (section 10.1): a cut result is partial + OUTPUT_LIMIT + truncated, with no cursor.
        return _envelope(req, status, data, evidence, "OUTPUT_LIMIT" if trunc else None, trunc)

    response = build(events, truncated)
    output_budget = MAX_SNAPSHOT_BYTES if snapshot else MAX_RESPONSE_BYTES
    while _size(response) > output_budget and events:
        events, truncated = events[1:], True        # drop the oldest whole record
        response = build(events, truncated)
    return response
