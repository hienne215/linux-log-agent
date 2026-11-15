# P03-02: `memory_oom_events` collector

Status: Draft (review fixes applied, waiting for re-review) | Owner: Trang | Support/review: Thuy
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
| `ok` | The command exited with status 0 (or was stopped by our own output cap) and the output was read |
| `permission_denied` | `journalctl` printed its "not seeing messages from other users" hint, or `dmesg` said "Operation not permitted". Also used, as the safe choice, when `journalctl` printed no record at all and the account is in neither `adm` nor `systemd-journal` |
| `unavailable` | Binary missing, **command exited with a non-zero status (even if it printed a valid-looking record)**, or the time budget ran out |

**Response status** (separates "no OOM" from "cannot read"):

| Situation | `status` | `error_code` |
|---|---|---|
| Every source `ok` **and each source reaches back to `since`**, nothing cut | `ok` (events may be `[]`) | null |
| A source is not `ok`, **or any single source starts after `since`**, or the result was cut | `partial` | null |
| No source `ok`, all `permission_denied` | `error` | `PERMISSION_DENIED` |
| No source `ok`, any other mix | `error` | `UNSUPPORTED` |
| Bad window / limit / `cursor` | `error` | `INVALID_INPUT` |

Coverage is judged **per source** (R3). `dmesg` only reaches back to the last boot, so a 24 h window that starts before the boot is `partial` even when the journal covers it. A window that starts after the oldest `dmesg` line can be `ok`.

**Events.** One event per kill. The line `Out of memory: Killed process <pid> (<name>) total-vm:..kB, anon-rss:..kB` (also the cgroup form and the old two-line form) gives pid, process and sizes. The `oom-kill:...task=..,pid=..` line is used only when no kill line exists for that kill. "invoked oom-killer" lines are context and are not counted. Events are sorted by time; if there are more than `limit`, the most recent are kept and the result is `partial` + `truncated`.

**De-duplication (settled).** Two records are one kill only if they have the **same pid**, a **compatible process name** (equal, or one of them unknown) and are within **60 s** of each other. The richer record wins; the journal wins ties. A reused pid with another name is kept as a separate event. Known limit: after a VM pause the `dmesg` clock can drift by more than 60 s, and then the same kill appears twice.

**Cursor (settled).** The tool is stateless and never returns `next_cursor`. A `cursor` in a request is therefore invalid (`INVALID_INPUT`). When the result is cut, the response is `partial` with `truncated: true`; narrow the window to get the rest.

**Limits.** 8 s total budget (the server allows 10 s); 4 MiB read per command; the response is trimmed (oldest whole events first) to 28,000 bytes because the server sends the result twice inside one 64 KiB frame.

**Privacy (SECURITY.md section 5.2).** A process name is text chosen by the workload, so it is never echoed as it came:

- A name is kept only if it looks like a kernel `comm` name (at most 15 characters from a plain character set) and contains no credential keyword (password, secret, token, api key, bearer, authorization, credential, private). Anything else becomes `[REDACTED]`, and `evidence.redacted` is `true`.
- `message` and `excerpt` are **rebuilt from the parsed fields** (pid, process, sizes). Raw kernel text, cgroup paths, UIDs and other trailing text are never copied.
- This is a filter, not a guarantee: a short, plain name that happens to be a secret cannot be recognised.

## 2. P03-02.2: deliverables

| File | Purpose |
|---|---|
| `src/mcp_server/oom_events.py` | Parser and collector (stdlib only, Python 3.8+) |
| `src/mcp_server/oom_worker.py` | Worker entrypoint: JSON request on stdin, JSON response on stdout |
| `tests/test_p03_02_oom_events.py` | Parser, collector, privacy, schema, worker and scaffold tests |
| `scripts/p03-02/smoke.sh` | Read-only smoke run inside the VM |

Registering it in the scaffold (the argv is code-owned, never taken from MCP arguments):

```python
argv = (sys.executable, "-I", str(ROOT / "src/mcp_server/oom_worker.py"))
ServerScaffold(backends={"memory_oom_events": argv})
```

**Wiring decision (Thuy):** the real backend is enabled by an operator-owned flag `--linux-oom`, off by default. The flag itself is not implemented in this branch yet.

## 3. P03-02.3: verification

| Acceptance criterion | Evidence | Status |
|---|---|---|
| Returns process, time and evidence when present | `test_oom_found_in_journal_with_evidence`, parser tests | Automated |
| Tells "no OOM" apart from "no permission" | `test_no_oom_and_readable...`, `test_no_permission_is_not_reported_as_no_oom`, `test_journal_denied_but_dmesg_readable...`, `test_each_source_is_judged_on_its_own_history` | Automated |
| Fixtures: OOM / no OOM / permission denied | Same tests; kernel line formats listed in the test file | Automated |
| No secret-like process name reaches the response | `test_process_name_with_secret_like_text_never_appears_anywhere`, `test_message_is_rebuilt_from_parsed_fields_only` | Automated |
| A failed command is never a successful read | `test_failing_journalctl_is_unavailable_even_with_a_valid_record` | Automated |
| Smoke, RAM read-only | `scripts/p03-02/smoke.sh` in the VM (section 6) | Run in the VM, output saved |

Kernel message formats in the fixtures follow the Linux kernel source; they were **not captured** from a real OOM on the lab VM. The P03-01 fixture line is a simulated kernel-log line, not a real OOM.

Run the tests: `python -m pytest -q tests/test_p03_02_oom_events.py`

## 4. Decisions taken in review

1. **RAM status** stays out of the OOM schema (contract unchanged). The smoke only shows `/proc/meminfo` as evidence.
2. **Wiring:** operator-owned `--linux-oom` flag, default off (not implemented here).
3. **Coverage:** judged per source.
4. **Cursor** and **de-duplication:** settled as described in section 1.
5. Still open: the scaffold replaces every worker error by `safe_error(code)`, so per-source `coverage` is lost for `PERMISSION_DENIED` / `UNSUPPORTED`. The code survives, which meets the acceptance criterion.

## 5. Known limits

- `journalctl` permission detection relies on the systemd 245 hint text plus a group check; another systemd version may word it differently (the group check still avoids a false "no OOM").
- `dmesg` times are derived from boot time and can drift after a VM pause; the journal time wins when both report the same kill.
- Only kernel OOM kills are reported, not user-space killers such as `systemd-oomd`.
- Not tested on a real OOM; not tested with other kernel versions.

## 6. Smoke results (lab VM `linux-log-lab`, 2026-11-16, after the review fixes)

| Stage | Reader groups | Response | `journal.kernel` | `dmesg` | Events |
|---|---|---|---|---|---|
| A | none | `partial` | `permission_denied` | `ok` (oldest 2026-11-15T17:24Z) | 1 (pid 4242, from `dmesg`) |
| B | `systemd-journal` | `partial` | `ok` (oldest 2026-11-01) | `ok` (oldest 2026-11-15T17:24Z) | 1 (pid 4242, journal and dmesg merged) |

- Stage B is `partial` because each source is judged on its own: `dmesg` only reaches back to the last boot, which is after `since`. The first run on the same day, made before the review fixes, reported `ok` under the old union rule.
- The simulated event appears once in each stage. Its time differs by 1 s between A (`dmesg`) and B (journal): the clock drift described in section 5. The journal time wins when both report it.
- The reader can read `MemTotal` and `MemAvailable` from `/proc/meminfo` in both stages.
- The case where both sources are denied (`PERMISSION_DENIED`) and the `ok` path are covered by unit tests only.
- The event is the simulated kernel line from P03-01, not a real OOM.
- Raw output: `docs/evidence/p03-02-A-no-groups.txt`, `docs/evidence/p03-02-B-journal.txt`.