# P03-02: `memory_oom_events` collector

Status: Proposed (waiting for review) | Owner: Trang | Support/review: Thuy
Depends on: P03-01 (reader permissions), P01-01 (server scaffold, draft), P00-03 (contracts R3, `fd737fb`)
Scope: one read-only tool. It does not change the contract, the scaffold or any other tool.

## 1. P03-02.1: design

The tool answers one question: *did the kernel kill a process because of memory pressure in a window, and could we actually read the places where that would be logged?*

| Source (`coverage.source`) | How it is read | What "readable" means |
|---|---|---|
| `journal.kernel` | `journalctl -o json _TRANSPORT=kernel` for the window (all boots, not `-k`, which only shows the current boot) | The reader can see the system journal |
| `dmesg` | `dmesg --time-format iso` | The kernel ring buffer is readable (VM-specific: `dmesg_restrict` is off) |

Commands use absolute paths, a fixed argv, no shell and a fixed environment (the scaffold gives the worker no `PATH`).

**Source status** (`coverage[].status`):

| Status | Meaning |
|---|---|
| `ok` | Command ran and the output was read |
| `permission_denied` | `journalctl` printed its "not seeing messages from other users" hint, or `dmesg` said "Operation not permitted". Also used, as the safe choice, when `journalctl` printed no record at all and the account is in neither `adm` nor `systemd-journal` |
| `unavailable` | Binary missing, command failed, or the time budget ran out |

**Response status** (the part that separates "no OOM" from "cannot read"):

| Situation | `status` | `error_code` |
|---|---|---|
| Both sources `ok`, history covers the window, nothing cut | `ok` (events may be `[]`) | null |
| One source not `ok`, or oldest data is newer than `since`, or the result was cut | `partial` | null |
| No source `ok`, all `permission_denied` | `error` | `PERMISSION_DENIED` |
| No source `ok`, any other mix | `error` | `UNSUPPORTED` |
| Bad window / limit / `cursor` | `error` | `INVALID_INPUT` |

**Events.** One event per kill. The line `Out of memory: Killed process <pid> (<name>) total-vm:..kB, anon-rss:..kB` (also the cgroup form and the old two-line form) gives pid, process and sizes. The `oom-kill:...task=..,pid=..` line is used only when no kill line exists for that pid. "invoked oom-killer" lines are context and are not counted. The same pid within 60 s is one event; the richer record wins, and the journal wins ties. Events are sorted by time; if there are more than `limit`, the most recent are kept and the result is `partial` + `truncated`.

**Limits.** 8 s total budget (the server allows 10 s); 4 MiB read per command; the response is trimmed (oldest whole events first) to 28,000 bytes because the server sends the result twice inside one 64 KiB frame. `cursor` is rejected (`INVALID_INPUT`): the tool does not paginate, narrow the window instead.

**Privacy.** Only OOM lines are returned; control characters are removed; the message is cut at 2048 characters and the evidence excerpt at 500. There is no secret redactor: `redacted` is always `false`, which is accurate because nothing is rewritten.

## 2. P03-02.2: deliverables

| File | Purpose |
|---|---|
| `src/mcp_server/oom_events.py` | Parser and collector (stdlib only, Python 3.8+) |
| `src/mcp_server/oom_worker.py` | Worker entrypoint: JSON request on stdin, JSON response on stdout |
| `tests/test_p03_02_oom_events.py` | Parser/collector tests, schema tests, worker and scaffold tests |
| `scripts/p03-02/smoke.sh` | Read-only smoke run inside the VM |

Registering it in the scaffold (the argv is code-owned, never taken from MCP arguments):

```python
argv = (sys.executable, "-I", str(ROOT / "src/mcp_server/oom_worker.py"))
ServerScaffold(backends={"memory_oom_events": argv})
```

Wiring this into `run_mcp_server.py` (a new flag) is not done here; it needs a decision from Thuy.

## 3. P03-02.3: verification

| Acceptance criterion | Evidence | Status |
|---|---|---|
| Returns process, time and evidence when present | `test_oom_found_in_journal_with_evidence`, parser tests | Automated |
| Tells "no OOM" apart from "no permission" | `test_no_oom_and_readable...`, `test_no_permission_is_not_reported_as_no_oom`, `test_journal_denied_but_dmesg_readable...` | Automated |
| Fixtures: OOM / no OOM / permission denied | Same tests, kernel line formats listed in the test file | Automated |
| Smoke, RAM read-only | `scripts/p03-02/smoke.sh` in the VM, stages A and B (see below) | Run in the VM, output saved |

Kernel message formats in the fixtures follow the Linux kernel source; they were **not captured** from a real OOM on the lab VM. The P03-01 fixture line is a simulated kernel-log line, not a real OOM.

Run the tests: `python -m pytest -q tests/test_p03_02_oom_events.py`

Smoke (in the VM; `reader-user.sh` is from P03-01):

```
scp src/mcp_server/oom_events.py src/mcp_server/oom_worker.py scripts/p03-02/smoke.sh <lab-user>@192.168.56.101:~/p03-02/
cd ~/p03-02 && sed -i 's/\r$//' smoke.sh
bash ~/p03-01/fixtures.sh up
bash ~/p03-01/reader-user.sh groups-none;    bash smoke.sh A-no-groups .
bash ~/p03-01/reader-user.sh groups-journal; bash smoke.sh B-journal .
bash ~/p03-01/reader-user.sh groups-none
```

Expected: stage A is `partial` (`journal.kernel` = `permission_denied`, `dmesg` = `ok`); stage B finds the simulated event (pid 4242) with both sources `ok`.

## 4. Open points for Thuy

1. **Error details are dropped.** `server.py` replaces every worker error by `safe_error(code)`, so `coverage` (which source was denied) is lost for `PERMISSION_DENIED` / `UNSUPPORTED`. The error code survives, which is enough for the acceptance criterion. If the Agent needs the per-source detail, the scaffold could keep a validated `coverage` block (enum values only, no free text).
2. **RAM status.** The task text says "RAM status", but the R3 data schema has no RAM field (`additionalProperties: false`). The smoke only shows `/proc/meminfo` as evidence. Adding RAM to the contract would be a contract change.
3. **Wiring.** Which flag or config enables the real backend in `run_mcp_server.py`.
4. **Partial rule.** "Oldest data newer than `since` is `partial`" is applied to the union of the readable sources. Confirm this matches the contract's intent.
5. **Local test state.** Merging the scaffold with R3 gives 8 failing tests in `tests/test_p01_scaffold.py` (already reported); they are unrelated to this tool.

## 5. Known limits

- `journalctl` permission detection relies on the systemd 245 hint text plus a group check; another systemd version may word it differently (the group check still avoids a false "no OOM").
- `dmesg` times are derived from boot time and can drift after a VM pause; the journal time wins when both report the same kill.
- Only kernel OOM kills are reported, not user-space killers such as `systemd-oomd`.
- Not tested on a real OOM; not tested with other kernel versions.
