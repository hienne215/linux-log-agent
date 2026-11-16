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


BOOT_WALL = oom.parse_ts("2026-11-13T22:00:00Z")      # wall time of the kernel's time zero in these tests
BOOT = "0123456789abcdef0123456789abcdef"             # the "current" boot id
OTHER_BOOT = "fedcba9876543210fedcba9876543210"


def jline(iso, message, cursor="c", boot=BOOT, mono=True):
    dt = oom.parse_ts(iso)
    record = {"__REALTIME_TIMESTAMP": str((dt - oom.EPOCH) // oom.timedelta(microseconds=1)),
              "__CURSOR": cursor + iso, "MESSAGE": message, "_TRANSPORT": "kernel", "_BOOT_ID": boot}
    if mono:
        record["_SOURCE_MONOTONIC_TIMESTAMP"] = str((dt - BOOT_WALL) // oom.timedelta(microseconds=1))
    return json.dumps(record)


def dline(iso, message):
    """A dmesg line: seconds since the kernel started, as the real command prints it."""
    return "[%12.6f] %s" % ((oom.parse_ts(iso) - BOOT_WALL).total_seconds(), message)


def ok(out, err=b""):
    return (0, out if isinstance(out, bytes) else out.encode(), err, False, False)


def fake(journal, oldest, dmesg):
    def run(argv, timeout, max_bytes=0, first_lines=None):
        if os.path.basename(argv[0]) == "dmesg":
            return dmesg
        return oldest if first_lines else journal
    return run


NO_FILE = "/nonexistent/oom-process-allowlist"


def go(req=None, journal=None, oldest=None, dmesg=None, found=True, lacks=False, allow_file=NO_FILE,
       boot=BOOT, boot_wall=BOOT_WALL):
    find = (lambda names: names[0]) if found else (lambda names: None)
    return oom.collect(dict(REQ, **(req or {})), fake(journal, oldest, dmesg), find, lambda: lacks,
                       allow_file=allow_file, boot_id=lambda: boot, boot_wall=lambda: boot_wall)


QUIET_OLDEST = ok(jline("2026-11-02T02:12:54Z", NOISE))
QUIET_DMESG = ok(dline("2026-11-13T22:00:00Z", NOISE))   # ring buffer reaches back before "since"
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
    assert r["evidence"][0]["source"] == "journal.kernel" and r["evidence"][0]["redacted"] is False
    assert r["evidence"][0]["excerpt"] == ("Out of memory: Killed process 2231 (java) "
                                           "total-vm:3145728kB, anon-rss:1048576kB")
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
    assert r["data"]["events"][0]["timestamp"] == "2026-11-14T23:53:00Z"      # boot time + kernel offset


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
           dmesg=ok(dline("2026-11-14T09:00:00Z", KILL_MODERN)))
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
    assert r["error_code"] == "OUTPUT_LIMIT" and r["next_cursor"] is None     # R3: not resumable
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
    assert event["process"] == oom.REDACTED and r["evidence"][0]["redacted"] is True


def test_huge_result_is_trimmed_to_fit_budget():
    lines = [jline("2026-11-14T%02d:%02d:00Z" % (i // 60, i % 60),
                   "Out of memory: Killed process %d (%s) total-vm:1kB" % (i + 1, "x" * 60)) for i in range(200)]
    r = go(req={"limit": 200}, journal=ok("\n".join(lines)), oldest=QUIET_OLDEST, dmesg=QUIET_DMESG)
    assert r["truncated"] and r["status"] == "partial" and 0 < r["data"]["count"] < 200
    assert r["error_code"] == "OUTPUT_LIMIT" and r["next_cursor"] is None
    assert oom._size(r) <= oom.MAX_RESPONSE_BYTES
    assert r["data"]["events"][-1]["pid"] == 200 and len(r["evidence"]) == r["data"]["count"]


@pytest.mark.parametrize("change", [{"since": "bad"}, {"until": "2026-11-13T00:00:00Z"},
                                    {"until": "2026-11-15T00:00:01Z"}, {"limit": 0}, {"limit": True},
                                    {"limit": 201}, {"cursor": "x"}])
def test_invalid_requests_are_rejected_before_any_command(change):
    def boom(*args, **kwargs):
        raise AssertionError("command must not run")
    r = oom.collect(dict(REQ, **change), boom, lambda n: n[0], lambda: False, allow_file=NO_FILE,
                    boot_id=lambda: BOOT, boot_wall=lambda: BOOT_WALL)
    assert r["status"] == "error" and r["error_code"] == "INVALID_INPUT"


def test_budget_exhaustion_means_unavailable_not_empty():
    ticks = iter([0.0] + [100.0] * 50)
    r = oom.collect(REQ, fake(QUIET_JOURNAL, QUIET_OLDEST, QUIET_DMESG), lambda n: n[0], lambda: False,
                    lambda: next(ticks), allow_file=NO_FILE, boot_id=lambda: BOOT, boot_wall=lambda: BOOT_WALL)
    assert r["error_code"] == "UNSUPPORTED"


def test_ids_are_echoed_on_every_response():
    for r in (go(journal=QUIET_JOURNAL, oldest=QUIET_OLDEST, dmesg=QUIET_DMESG), go(found=False), go({"limit": 0})):
        assert (r["session_id"], r["trace_id"], r["logical_tool_call_id"], r["attempt_id"]) == ("s", "t", "l", "a")


def test_no_shell_and_fixed_absolute_commands():
    source = (ROOT / "src/mcp_server/oom_events.py").read_text(encoding="utf-8")
    assert "shell=True" not in source and "os.system" not in source
    assert all(os.path.isabs(p) for p in oom.JOURNALCTL + oom.DMESG)



# ---------------------------------------------------------------- review findings (PR #21)
MARKER = "SYNTHETIC_CHILD_SECRET"


@pytest.mark.parametrize("name", [MARKER, "APIKEY_7D19", "Z9K2-7D19", "hunter2", "token=SYNTHETIC1", "password",
                                  "x" * 40, "a/b\x1b[0mc", "api_key9", "myapp"])
def test_process_name_with_secret_like_text_never_appears_anywhere(name):
    kill = "Out of memory: Killed process 31 (%s) total-vm:5kB, anon-rss:4kB" % name
    oomkill = ("oom-kill:constraint=CONSTRAINT_NONE,cpuset=/,task_memcg=/system.slice/%s.service,"
               "task=%s,pid=32,uid=0") % (name, name)
    r = go(journal=ok("\n".join([jline("2026-11-14T09:00:00Z", kill), jline("2026-11-14T09:01:00Z", oomkill)])),
           oldest=QUIET_OLDEST, dmesg=ok(dline("2026-11-14T09:00:00Z", kill)))
    text = json.dumps(r)
    assert name not in text and MARKER not in text and "system.slice" not in text
    assert r["data"]["count"] == 2
    assert all(e["process"] == oom.REDACTED for e in r["data"]["events"])
    assert all(ev["redacted"] is True and name not in ev["excerpt"] for ev in r["evidence"])


@pytest.mark.parametrize("name", ["java", "lab-oom", "nginx", "python3.8", "mysqld"])
def test_allowlisted_process_names_are_kept(name):
    line = "Out of memory: Killed process 31 (%s) total-vm:5kB" % name
    r = go(journal=ok(jline("2026-11-14T09:00:00Z", line)), oldest=QUIET_OLDEST, dmesg=QUIET_DMESG)
    assert r["data"]["events"][0]["process"] == name and r["evidence"][0]["redacted"] is False


def test_operator_allowlist_file_extends_the_list_only_when_it_is_trusted(tmp_path):
    line = "Out of memory: Killed process 31 (myapp) total-vm:5kB"
    kwargs = dict(journal=ok(jline("2026-11-14T09:00:00Z", line)), oldest=QUIET_OLDEST, dmesg=QUIET_DMESG)
    assert go(**kwargs)["data"]["events"][0]["process"] == oom.REDACTED
    listing = tmp_path / "allow"
    listing.write_text("# comment\nmyapp\nbad name with spaces\n" + "S" * 40 + "\n", encoding="utf-8")
    names = oom.load_operator_names(str(listing))
    if os.name == "posix" and os.geteuid() == 0:
        assert names == {"myapp"}
    else:
        assert names == set()                       # not root-owned: ignored
    monkey = oom.load_operator_names
    try:
        oom.load_operator_names = lambda path: {"myapp"}
        assert go(**kwargs)["data"]["events"][0]["process"] == "myapp"
    finally:
        oom.load_operator_names = monkey


def test_message_is_rebuilt_from_parsed_fields_only():
    line = KILL_CGROUP + " extra-text-that-must-not-be-echoed"
    r = go(journal=ok(jline("2026-11-14T09:00:00Z", line)), oldest=QUIET_OLDEST, dmesg=QUIET_DMESG)
    message = r["data"]["events"][0]["message"]
    assert message == "Memory cgroup out of memory: Killed process 3300 (python3) total-vm:204800kB, anon-rss:102400kB"
    assert "extra-text" not in json.dumps(r) and "UID" not in message


def test_each_source_is_judged_on_its_own_history():
    """Journal reaches back before 'since' but dmesg does not: the result must not be 'ok'."""
    r = go(journal=QUIET_JOURNAL, oldest=QUIET_OLDEST, dmesg=ok(dline("2026-11-14T17:24:38Z", NOISE)))
    assert [c["status"] for c in r["data"]["coverage"]] == ["ok", "ok"]
    assert r["status"] == "partial" and r["error_code"] is None and r["data"]["events"] == []
    full = go(journal=QUIET_JOURNAL, oldest=QUIET_OLDEST, dmesg=QUIET_DMESG)
    assert full["status"] == "ok"


def test_failing_journalctl_is_unavailable_even_with_a_valid_record():
    r = go(journal=(1, jline("2026-11-14T09:00:00Z", KILL_MODERN).encode(), b"boom", False, False),
           oldest=QUIET_OLDEST, dmesg=QUIET_DMESG)
    assert [c["status"] for c in r["data"]["coverage"]] == ["unavailable", "ok"]
    assert r["status"] == "partial" and r["data"]["events"] == []
    both = go(journal=(1, jline("2026-11-14T09:00:00Z", KILL_MODERN).encode(), b"", False, False),
              oldest=QUIET_OLDEST, dmesg=(2, dline("2026-11-14T09:00:00Z", KILL_MODERN).encode(), b"", False, False))
    assert both["status"] == "error" and both["error_code"] == "UNSUPPORTED"


def test_output_cut_by_our_own_cap_is_partial_not_a_failed_command():
    cut = (-9, jline("2026-11-14T09:00:00Z", KILL_MODERN).encode() + b"\n{partial", b"", True, False)
    r = go(journal=cut, oldest=QUIET_OLDEST, dmesg=QUIET_DMESG)
    assert r["status"] == "partial" and r["truncated"] and r["data"]["count"] == 1
    assert r["error_code"] == "OUTPUT_LIMIT"


def test_a_partial_that_is_not_cut_has_no_error_code():
    r = go(journal=(0, b"", HINT, False, False), oldest=ok(b""), dmesg=ok(dline("2026-11-14T23:53:00Z", FIXTURE)))
    assert r["status"] == "partial" and r["error_code"] is None and not r["truncated"]


ALPHA = "Out of memory: Killed process 500 (alpha) total-vm:1kB"
BETA = "Out of memory: Killed process 500 (beta) total-vm:1kB"


def count(journal, dmesg=QUIET_DMESG, **kwargs):
    return go(journal=ok("\n".join(journal)), oldest=QUIET_OLDEST, dmesg=dmesg, **kwargs)["data"]["count"]


def test_same_pid_and_name_in_different_boots_are_two_kills():
    assert count([jline("2026-11-14T09:00:00Z", ALPHA, boot=OTHER_BOOT), jline("2026-11-14T09:00:30Z", ALPHA)]) == 2
    assert count([jline("2026-11-14T09:00:00Z", ALPHA, boot=OTHER_BOOT), jline("2026-11-14T09:00:00Z", ALPHA)]) == 2


def test_same_pid_and_name_30_seconds_apart_in_one_boot_are_two_kills():
    assert count([jline("2026-11-14T09:00:00Z", ALPHA), jline("2026-11-14T09:00:30Z", ALPHA)]) == 2


def test_two_lines_of_one_kill_in_one_boot_are_one_event():
    assert count([jline("2026-11-14T09:00:00Z", ALPHA), jline("2026-11-14T09:00:00.500Z", ALPHA)]) == 1


def test_different_pids_or_names_are_never_merged():
    other_pid = "Out of memory: Killed process 501 (alpha) total-vm:1kB"
    assert count([jline("2026-11-14T09:00:00Z", ALPHA), jline("2026-11-14T09:00:00Z", other_pid)]) == 2
    r = go(journal=ok("\n".join([jline("2026-11-14T09:00:00Z", ALPHA), jline("2026-11-14T09:00:00Z", BETA)])),
           oldest=QUIET_OLDEST, dmesg=QUIET_DMESG)
    assert [e["process"] for e in r["data"]["events"]] == [oom.REDACTED, oom.REDACTED]   # two different names


def test_the_same_record_seen_by_journal_and_dmesg_is_one_event():
    assert count([jline("2026-11-14T09:00:00Z", ALPHA)], dmesg=ok(dline("2026-11-14T09:00:00Z", ALPHA))) == 1


def test_a_journal_event_of_an_older_boot_is_not_merged_with_the_current_dmesg():
    assert count([jline("2026-11-14T09:00:00Z", ALPHA, boot=OTHER_BOOT)],
                 dmesg=ok(dline("2026-11-14T09:00:00Z", ALPHA))) == 2


@pytest.mark.parametrize("journal_kwargs,boot", [({"mono": False}, BOOT), ({"boot": ""}, BOOT), ({}, None)])
def test_without_identity_nothing_is_merged(journal_kwargs, boot):
    assert count([jline("2026-11-14T09:00:00Z", ALPHA, **journal_kwargs)],
                 dmesg=ok(dline("2026-11-14T09:00:00Z", ALPHA)), boot=boot) == 2


def test_dmesg_is_unavailable_when_the_kernel_start_time_is_unknown():
    r = go(journal=QUIET_JOURNAL, oldest=QUIET_OLDEST, dmesg=QUIET_DMESG, boot_wall=None)
    assert [c["status"] for c in r["data"]["coverage"]] == ["ok", "unavailable"] and r["status"] == "partial"


def test_boot_id_is_read_and_normalised(tmp_path):
    good, bad = tmp_path / "good", tmp_path / "bad"
    good.write_text("01234567-89AB-cdef-0123-456789abcdef\n", encoding="ascii")
    bad.write_text("not a boot id", encoding="ascii")
    assert oom.current_boot_id(str(good)) == "0123456789abcdef0123456789abcdef"
    assert oom.current_boot_id(str(bad)) is None and oom.current_boot_id(str(tmp_path / "missing")) is None


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


def test_linux_oom_flag_is_off_by_default_and_the_argv_is_fixed_in_code():
    pytest.importorskip("mcp")
    import run_mcp_server as entry
    assert entry.build_backends() == {}
    backends = entry.build_backends(linux_oom=True)
    assert list(backends) == ["memory_oom_events"]
    argv = backends["memory_oom_events"]
    assert len(argv) == 3 and os.path.isabs(argv[0]) and argv[1] == "-I"
    assert Path(argv[2]) == ROOT / "src/mcp_server/oom_worker.py" and Path(argv[2]).is_file()
    assert set(entry.build_backends(fixtures=True, linux_oom=True)) == {"service_status", "memory_oom_events"}
    parser = entry.build_parser()
    assert {o for action in parser._actions for o in action.option_strings} == {"-h", "--help", "--fixtures", "--linux-oom"}
    assert parser.parse_args([]).linux_oom is False and parser.parse_args(["--linux-oom"]).linux_oom is True


def test_flag_enables_the_backend_through_the_real_stdio_server():
    pytest.importorskip("mcp")
    import asyncio
    from mcp import Client, StdioServerParameters
    import mock_mcp_server as contracts

    async def call(extra):
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "run_mcp_server.py"), *extra])
        async with Client(params, read_timeout_seconds=30) as client:
            return (await client.call_tool(oom.TOOL, dict(REQ))).structured_content

    off, on = asyncio.run(call([])), asyncio.run(call(["--linux-oom"]))
    for response in (off, on):
        contracts.TOOLS[oom.TOOL][4].validate(response)
    assert off["error_code"] == "UNSUPPORTED" and "coverage" not in off["data"]     # backend not enabled
    if os.name == "nt":                          # no journalctl/dmesg: both sources unavailable, detail kept
        assert on["error_code"] == "UNSUPPORTED"
        assert [(c["source"], c["status"]) for c in on["data"]["coverage"]] == [
            ("journal.kernel", "unavailable"), ("dmesg", "unavailable")]


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
