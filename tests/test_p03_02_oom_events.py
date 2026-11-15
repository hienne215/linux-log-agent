"""P03-02 memory_oom_events: parser and collector tests (no Linux needed) + worker/server smoke."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.mcp_server import oom_events as oom

ROOT = Path(__file__).resolve().parents[1]
REQ = {"schema_version": "1.0.0", "session_id": "s", "trace_id": "t", "logical_tool_call_id": "l",
       "attempt_id": "a", "since": "2026-11-14T00:00:00Z", "until": "2026-11-15T00:00:00Z"}
HINT = (b"Hint: You are currently not seeing messages from other users and the system.\n"
        b"      Users in groups 'adm', 'systemd-journal' can see all messages.\n")

# Kernel message formats (from the Linux kernel's mm/oom_kill.c); not captured from this VM.
KILL_MODERN = ("Out of memory: Killed process 2231 (java) total-vm:3145728kB, anon-rss:1048576kB, "
               "file-rss:0kB, shmem-rss:0kB, UID:1000 pgtables:2500kB oom_score_adj:0")
KILL_CGROUP = ("Memory cgroup out of memory: Killed process 3300 (python3) total-vm:204800kB, "
               "anon-rss:102400kB, file-rss:1024kB, shmem-rss:0kB, UID:0 pgtables:300kB oom_score_adj:0")
KILL_OLD_1 = "Out of memory: Kill process 77 (mysqld) score 880 or sacrifice child"
KILL_OLD_2 = "Killed process 77 (mysqld) total-vm:900000kB, anon-rss:800000kB, file-rss:100kB"
OOMKILL = ("oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null),cpuset=/,mems_allowed=0,global_oom,"
           "task_memcg=/system.slice/lab-backend.service,task=java,pid=2231,uid=1000")
INVOKED = "nginx invoked oom-killer: gfp_mask=0x100cca(GFP_HIGHUSER_MOVABLE), order=0, oom_score_adj=0"
FIXTURE = "Out of memory: Killed process 4242 (lab-oom) total-vm:409600kB, anon-rss:102400kB"
NOISE = "EXT4-fs (sda5): re-mounted. Opts: errors=remount-ro"


def jline(iso, message, cursor="c"):
    us = (oom.parse_ts(iso) - oom.EPOCH) // oom.timedelta(microseconds=1)
    return json.dumps({"__REALTIME_TIMESTAMP": str(us), "__CURSOR": cursor + iso,
                       "MESSAGE": message, "_TRANSPORT": "kernel"})


def dline(iso, message):
    return "%s %s" % (iso.replace("Z", ",000000+00:00"), message)


def ok(out, err=b""):
    return (0, out if isinstance(out, bytes) else out.encode(), err, False, False)


def fake(journal, oldest, dmesg):
    def run(argv, timeout, max_bytes=0, first_lines=None):
        if os.path.basename(argv[0]) == "dmesg":
            return dmesg
        return oldest if first_lines else journal
    return run


def go(req=None, journal=None, oldest=None, dmesg=None, found=True, lacks=False):
    find = (lambda names: names[0]) if found else (lambda names: None)
    return oom.collect(dict(REQ, **(req or {})), fake(journal, oldest, dmesg), find, lambda: lacks)


QUIET_OLDEST = ok(jline("2026-11-02T02:12:54Z", NOISE))
QUIET_DMESG = ok(dline("2026-11-14T22:00:00Z", NOISE))
QUIET_JOURNAL = ok(jline("2026-11-14T10:00:00Z", NOISE))


# ---------------------------------------------------------------- parser
@pytest.mark.parametrize("line,pid,name,vm,anon", [
    (KILL_MODERN, 2231, "java", 3145728, 1048576),
    (KILL_CGROUP, 3300, "python3", 204800, 102400),
    (KILL_OLD_2, 77, "mysqld", 900000, 800000),
    (KILL_OLD_1, 77, "mysqld", None, None),
    (OOMKILL, 2231, "java", None, None),
    (FIXTURE, 4242, "lab-oom", 409600, 102400)])
def test_parse_oom_kill_lines(line, pid, name, vm, anon):
    f = oom.parse_oom_message(line)
    assert (f["pid"], f["process"], f["total_vm_kb"], f["anon_rss_kb"]) == (pid, name, vm, anon)


@pytest.mark.parametrize("line", [INVOKED, NOISE, "Killed process 5 (x)", "", "pid=1 task=x"])
def test_non_kill_lines_are_ignored(line):
    assert oom.parse_oom_message(line) is None


def test_pid_zero_and_empty_name_become_null():
    f = oom.parse_oom_message("Out of memory: Killed process 0 (  ) total-vm:1kB")
    assert f["pid"] is None and f["process"] is None


# ---------------------------------------------------------------- collector
def test_oom_found_in_journal_with_evidence():
    r = go(journal=ok("\n".join([jline("2026-11-14T09:00:00Z", INVOKED), jline("2026-11-14T09:00:01Z", OOMKILL),
                                 jline("2026-11-14T09:00:01Z", KILL_MODERN), jline("2026-11-14T09:30:00Z", NOISE)])),
           oldest=QUIET_OLDEST, dmesg=QUIET_DMESG)
    assert r["status"] == "ok" and r["error_code"] is None and not r["truncated"]
    event = r["data"]["events"][0]
    assert r["data"]["count"] == 1 and (event["pid"], event["process"], event["total_vm_kb"]) == (2231, "java", 3145728)
    assert event["timestamp"] == "2026-11-14T09:00:01Z"
    assert [e["evidence_id"] for e in r["evidence"]] == [event["evidence_id"]]
    assert r["evidence"][0]["source"] == "journal.kernel" and KILL_MODERN.startswith(r["evidence"][0]["excerpt"][:20])
    assert [c["status"] for c in r["data"]["coverage"]] == ["ok", "ok"]


def test_no_oom_and_readable_is_ok_with_empty_events():
    r = go(journal=QUIET_JOURNAL, oldest=QUIET_OLDEST, dmesg=QUIET_DMESG)
    assert r["status"] == "ok" and r["data"]["events"] == [] and r["evidence"] == []
    assert [c["status"] for c in r["data"]["coverage"]] == ["ok", "ok"]


def test_no_permission_is_not_reported_as_no_oom():
    denied = (1, b"", b"dmesg: read kernel buffer failed: Operation not permitted\n", False, False)
    r = go(journal=(0, b"", HINT, False, False), oldest=ok(b""), dmesg=denied)
    assert r["status"] == "error" and r["error_code"] == "PERMISSION_DENIED"
    assert [c["status"] for c in r["data"]["coverage"]] == ["permission_denied"] * 2
    assert "journal.kernel: permission denied" in r["data"]["message"]
    empty_ok = go(journal=QUIET_JOURNAL, oldest=QUIET_OLDEST, dmesg=QUIET_DMESG)
    assert (r["status"], r["error_code"]) != (empty_ok["status"], empty_ok["error_code"])


def test_journal_denied_but_dmesg_readable_is_partial_and_keeps_dmesg_events():
    """Matches the P03-01 probe stage A: no group, journalctl only prints a hint."""
    r = go(journal=(0, b"", HINT, False, False), oldest=ok(b""),
           dmesg=ok(dline("2026-11-14T23:53:00Z", FIXTURE)))
    assert r["status"] == "partial" and r["error_code"] is None
    assert [c["status"] for c in r["data"]["coverage"]] == ["permission_denied", "ok"]
    assert r["data"]["events"][0]["pid"] == 4242 and r["evidence"][0]["source"] == "dmesg"


def test_missing_group_with_silent_journalctl_is_denied_not_empty():
    r = go(journal=ok(b""), oldest=ok(b""), dmesg=QUIET_DMESG, lacks=True)
    assert [c["status"] for c in r["data"]["coverage"]] == ["permission_denied", "ok"]
    assert r["status"] == "partial"


def test_tools_not_installed_is_unsupported():
    r = go(found=False)
    assert r["status"] == "error" and r["error_code"] == "UNSUPPORTED"
    assert [c["status"] for c in r["data"]["coverage"]] == ["unavailable"] * 2


def test_mixed_denied_and_unavailable_is_unsupported():
    r = go(journal=(0, b"", HINT, False, False), oldest=ok(b""), dmesg=(127, b"", b"", False, False))
    assert r["error_code"] == "UNSUPPORTED"


def test_history_gap_makes_empty_result_partial():
    r = go(journal=QUIET_JOURNAL, oldest=ok(jline("2026-11-14T08:00:00Z", NOISE)),
           dmesg=ok(dline("2026-11-14T08:00:00Z", NOISE)))
    assert r["status"] == "partial" and r["data"]["events"] == []   # oldest data starts after "since"


def test_same_kill_in_journal_and_dmesg_is_counted_once():
    r = go(journal=ok(jline("2026-11-14T09:00:00Z", KILL_MODERN)), oldest=QUIET_OLDEST,
           dmesg=ok(dline("2026-11-14T09:00:07Z", KILL_MODERN)))
    assert r["data"]["count"] == 1 and r["evidence"][0]["source"] == "journal.kernel"


def test_old_kernel_two_line_kill_is_one_event_with_sizes():
    r = go(journal=ok("\n".join([jline("2026-11-14T09:00:00Z", KILL_OLD_1),
                                 jline("2026-11-14T09:00:00Z", KILL_OLD_2)])),
           oldest=QUIET_OLDEST, dmesg=QUIET_DMESG)
    assert r["data"]["count"] == 1 and r["data"]["events"][0]["total_vm_kb"] == 900000


def test_limit_keeps_most_recent_and_marks_truncated():
    lines = [jline("2026-11-14T0%d:00:00Z" % h, "Out of memory: Killed process %d (p%d) total-vm:1kB" % (h + 10, h))
             for h in range(1, 4)]
    r = go(req={"limit": 2}, journal=ok("\n".join(lines)), oldest=QUIET_OLDEST, dmesg=QUIET_DMESG)
    assert r["status"] == "partial" and r["truncated"] and r["data"]["count"] == 2
    assert [e["pid"] for e in r["data"]["events"]] == [12, 13]


def test_window_is_half_open():
    r = go(journal=ok("\n".join([jline("2026-11-14T00:00:00Z", KILL_MODERN), jline("2026-11-15T00:00:00Z", FIXTURE)])),
           oldest=QUIET_OLDEST, dmesg=QUIET_DMESG)
    assert [e["pid"] for e in r["data"]["events"]] == [2231]


def test_hostile_text_is_neutralised_and_json_noise_ignored():
    evil = "Out of memory: Killed process 9 (a\x1b[31mb\nc) total-vm:5kB"
    lines = ["-- Logs begin at x --", "not json", json.dumps({"MESSAGE": [1, 2], "__REALTIME_TIMESTAMP": "1"}),
             jline("2026-11-14T09:00:00Z", evil)]
    r = go(journal=ok("\n".join(lines)), oldest=QUIET_OLDEST, dmesg=QUIET_DMESG)
    event = r["data"]["events"][0]
    assert "\x1b" not in json.dumps(r) and "\n" not in event["message"] and event["pid"] == 9


def test_huge_result_is_trimmed_to_fit_budget():
    lines = [jline("2026-11-14T%02d:%02d:00Z" % (i // 60, i % 60),
                   "Out of memory: Killed process %d (%s) total-vm:1kB" % (i + 1, "x" * 60)) for i in range(200)]
    r = go(req={"limit": 200}, journal=ok("\n".join(lines)), oldest=QUIET_OLDEST, dmesg=QUIET_DMESG)
    assert r["truncated"] and r["status"] == "partial" and 0 < r["data"]["count"] < 200
    assert oom._size(r) <= oom.MAX_RESPONSE_BYTES
    assert r["data"]["events"][-1]["pid"] == 200 and len(r["evidence"]) == r["data"]["count"]


@pytest.mark.parametrize("change", [{"since": "bad"}, {"until": "2026-11-13T00:00:00Z"},
                                    {"until": "2026-11-15T00:00:01Z"}, {"limit": 0}, {"limit": True},
                                    {"limit": 201}, {"cursor": "x"}])
def test_invalid_requests_are_rejected_before_any_command(change):
    def boom(*args, **kwargs):
        raise AssertionError("command must not run")
    r = oom.collect(dict(REQ, **change), boom, lambda n: n[0], lambda: False)
    assert r["status"] == "error" and r["error_code"] == "INVALID_INPUT"


def test_budget_exhaustion_means_unavailable_not_empty():
    ticks = iter([0.0] + [100.0] * 50)
    r = oom.collect(REQ, fake(QUIET_JOURNAL, QUIET_OLDEST, QUIET_DMESG), lambda n: n[0], lambda: False,
                    lambda: next(ticks))
    assert r["error_code"] == "UNSUPPORTED"


def test_ids_are_echoed_on_every_response():
    for r in (go(journal=QUIET_JOURNAL, oldest=QUIET_OLDEST, dmesg=QUIET_DMESG), go(found=False), go({"limit": 0})):
        assert (r["session_id"], r["trace_id"], r["logical_tool_call_id"], r["attempt_id"]) == ("s", "t", "l", "a")


def test_no_shell_and_fixed_absolute_commands():
    source = (ROOT / "src/mcp_server/oom_events.py").read_text(encoding="utf-8")
    assert "shell=True" not in source and "os.system" not in source
    assert all(os.path.isabs(p) for p in oom.JOURNALCTL + oom.DMESG)


# ---------------------------------------------------------------- contract (needs P00-03 code)
def test_responses_match_the_published_schema():
    contracts = pytest.importorskip("mock_mcp_server")
    validator = contracts.TOOLS[oom.TOOL][4]
    cases = [go(journal=ok(jline("2026-11-14T09:00:00Z", KILL_MODERN)), oldest=QUIET_OLDEST, dmesg=QUIET_DMESG),
             go(journal=QUIET_JOURNAL, oldest=QUIET_OLDEST, dmesg=QUIET_DMESG),
             go(journal=(0, b"", HINT, False, False), oldest=ok(b""),
                dmesg=ok(dline("2026-11-14T23:53:00Z", FIXTURE))),
             go(journal=(0, b"", HINT, False, False), oldest=ok(b""),
                dmesg=(1, b"", b"Operation not permitted", False, False)),
             go(found=False), go({"limit": 0})]
    for response in cases:
        validator.validate(response)


def run_worker(request):
    return subprocess.run([sys.executable, "-I", str(ROOT / "src/mcp_server/oom_worker.py")],
                          input=json.dumps(request).encode(), capture_output=True, timeout=30)


def test_worker_process_returns_a_schema_valid_response():
    contracts = pytest.importorskip("mock_mcp_server")
    done = run_worker(REQ)
    assert done.returncode == 0
    response = json.loads(done.stdout.decode("utf-8"))
    contracts.TOOLS[oom.TOOL][4].validate(response)
    assert response["trace_id"] == "t"
    if os.name == "nt":
        assert response["error_code"] == "UNSUPPORTED"   # no journalctl/dmesg on Windows


def test_worker_rejects_oversized_or_non_object_input():
    assert subprocess.run([sys.executable, "-I", str(ROOT / "src/mcp_server/oom_worker.py")],
                          input=b"[]", capture_output=True, timeout=30).returncode == 2
    assert run_worker({"pad": "x" * 20000}).returncode == 2


def test_through_the_server_scaffold():
    pytest.importorskip("mcp")
    import asyncio
    from types import SimpleNamespace
    from mcp.types import CallToolRequestParams
    from src.mcp_server.server import ServerScaffold
    import mock_mcp_server as contracts
    argv = (sys.executable, "-I", str(ROOT / "src/mcp_server/oom_worker.py"))

    async def exercise():
        app = ServerScaffold(backends={oom.TOOL: argv})
        result = await app.call_tool(SimpleNamespace(request_id=1),
                                     CallToolRequestParams(name=oom.TOOL, arguments=dict(REQ)))
        response = result.structured_content
        contracts.TOOLS[oom.TOOL][4].validate(response)
        assert response["tool"] == oom.TOOL
        if os.name == "nt":
            assert response["error_code"] == "UNSUPPORTED"
        else:
            assert response["status"] in ("ok", "partial", "error")
    asyncio.run(exercise())
