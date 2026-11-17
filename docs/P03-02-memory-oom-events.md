# P03-02: `memory_oom_events` collector

**Current revision:** [2026-11-18 pagination/dependency follow-up](P03-02-pagination-20261118.md).
It supersedes the historical wiring/cursor notes below: the opt-in SDK uses a
connection-owned pager, while the standalone VM diagnostic worker stays stateless.
Canonical Pagination remains yes. Older tests/VM evidence are not new binding acceptance.

Status: Draft (review fixes applied, waiting for re-review) | Owner: Trang | Support/review: Thuy
Depends on: P03-01 (reader permissions), P01-01 (server scaffold, draft), P00-03 (contracts R3, `fd737fb`)
Scope: one read-only tool. It does not change the contract, the scaffold or any other tool.

## 1. P03-02.1: design

The tool answers one question: *did the kernel kill a process because of memory pressure in a window, and could we actually read the places where that would be logged?*

| Source (`coverage.source`) | How it is read | What "readable" means |
|---|---|---|
| `journal.kernel` | `journalctl -o json _TRANSPORT=kernel` for the window (all boots, not `-k`, which only shows the current boot) | The reader can see the system journal |
| `dmesg` | `dmesg` (default format: seconds since the kernel started). Wall time = kernel start time + that offset | The kernel ring buffer is readable (VM-specific: `dmesg_restrict` is off) and the kernel start time is known |

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
| A source is not `ok`, **or any single source starts after `since`** | `partial` | null |
| The result was cut (more than `limit` events, response size cap, or command output cap) | `partial`, `truncated: true`, `next_cursor: null` | `OUTPUT_LIMIT` |
| No source `ok`, all `permission_denied` | `error` | `PERMISSION_DENIED` |
| No source `ok`, any other mix | `error` | `UNSUPPORTED` |
| Bad window / limit / `cursor` | `error` | `INVALID_INPUT` |

Coverage is judged **per source** (R3). `dmesg` only reaches back to the last boot, so a 24 h window that starts before the boot is `partial` even when the journal covers it. A window that starts after the oldest `dmesg` line can be `ok`.

**Events.** One event per kill. The line `Out of memory: Killed process <pid> (<name>) total-vm:..kB, anon-rss:..kB` (also the cgroup form and the old two-line form) gives pid, process and sizes. The `oom-kill:...task=..,pid=..` line is used only when no kill line exists for that kill. "invoked oom-killer" lines are context and are not counted. Events are sorted by time; if there are more than `limit`, the most recent are kept and the result is `partial` + `truncated`.

**De-duplication (identity, not time).** One kill is printed as several kernel lines (an `oom-kill:` summary and a `Killed process` line, or the old two-line form) and can be seen by both sources. Two records are treated as one kill **only when their identity proves it**: the same boot (`_BOOT_ID` for the journal; the current `/proc/sys/kernel/random/boot_id` for `dmesg`), kernel timestamps (`_SOURCE_MONOTONIC_TIMESTAMP` / the `dmesg` offset) within one second, the same pid and a compatible process name. The richer record wins; the journal wins ties. Consequences:

- The same pid and name in **different boots** are two kills, whatever the wall-clock gap.
- The same pid and name 30 s apart in **one boot** are two kills.
- If identity is missing (no boot id, no kernel timestamp, boot id unreadable) nothing is merged: a kill may then appear twice, but never disappears.
- A journal event from an older boot is never merged with `dmesg`, which only covers the current boot.

**Cursor (checked against R3, `docs/mcp-tool-contracts.md` sections 7, 10.1 and 10.4).**

- R3 says cursors are opaque, random, bound to tool, session, normalised filters and a 300 s TTL, are kept in process memory, and that an expired, modified or unknown cursor must be rejected, never reset.
- A worker is a new process for every call, so it cannot hold cursor state; that state would have to live in the server/binder. This collector therefore **does not issue cursors**: `next_cursor` is always `null`, and any `cursor` in a request was never issued, so it is rejected with `INVALID_INPUT`. This is consistent with R3's rejection rule.
- A cut result follows R3's "non-resumable partial": complete records only, `partial` + `OUTPUT_LIMIT` + `truncated: true`, `next_cursor: null`. The Agent must report the data as incomplete and narrow the window.
- Gap to settle with Thuy: the R3 table lists `memory_oom_events` as "Pagination: yes". With a stateless worker that is not provided. Either the table row changes for the real backend, or the binder owns cursor state in a later task.

**Limits.** 8 s total budget (the server allows 10 s); 4 MiB read per command; the response is trimmed (oldest whole events first) to 28,000 bytes because the server sends the result twice inside one 64 KiB frame.

**Privacy (SECURITY.md section 5.2).** A process name is text chosen by the workload, and no filter can tell a harmless name from a secret one (a length or keyword filter lets `Z9K2-7D19` through). So names are **allowlisted, not filtered**:

- A name is echoed only if it is an exact match on the allowlist. Anything else becomes `[REDACTED]` and `evidence.redacted` is `true`. The pid, sizes, time and source are still returned.
- The built-in list is small (`nginx`, `sshd`, `java`, `python3`, `mysqld`, `postgres`, `lab-oom`, `lab-backend`, ...). The operator can extend it with `/etc/linux-log-agent/oom-process-allowlist` (one name per line, up to 15 plain characters). The file is ignored unless it is a regular file owned by root and not writable by group or others.
- `message` and `excerpt` are **rebuilt from the parsed fields** (pid, process, sizes). Raw kernel text, cgroup paths, UIDs and other trailing text are never copied.
- Cost: the Agent does not see the name of an unlisted process. Two different unlisted names are still kept apart internally when de-duplicating (an internal hash, never returned).

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

**Wiring (decision by Thuy, implemented):** `python run_mcp_server.py --linux-oom` enables the real backend. The flag is operator-owned and **off by default**. The worker argv is built in `build_backends()` from `sys.executable`, `-I` and a fixed repository path; no MCP argument can change the executable or the script, and the parser accepts no other option. On Linux the server must run as the reader account (it refuses root).

## 3. P03-02.3: verification

| Acceptance criterion | Evidence | Status |
|---|---|---|
| Returns process, time and evidence when present | `test_oom_found_in_journal_with_evidence`, parser tests | Automated |
| Tells "no OOM" apart from "no permission" | `test_no_oom_and_readable...`, `test_no_permission_is_not_reported_as_no_oom`, `test_journal_denied_but_dmesg_readable...`, `test_each_source_is_judged_on_its_own_history` | Automated |
| Fixtures: OOM / no OOM / permission denied | Same tests; kernel line formats listed in the test file | Automated |
| No unlisted process name reaches the response | `test_process_name_with_secret_like_text_never_appears_anywhere` (includes `APIKEY_7D19`, `Z9K2-7D19`), `test_allowlisted_process_names_are_kept`, `test_operator_allowlist_file...`, `test_message_is_rebuilt_from_parsed_fields_only` | Automated |
| A failed command is never a successful read | `test_failing_journalctl_is_unavailable_even_with_a_valid_record` | Automated |
| `--linux-oom` is off by default, fixed argv | `test_linux_oom_flag_is_off_by_default_and_the_argv_is_fixed_in_code`, `test_flag_enables_the_backend_through_the_real_stdio_server` | Automated |
| Smoke, RAM read-only | `scripts/p03-02/smoke.sh` in the VM (section 6) | Run in the VM, output saved |

Kernel message formats in the fixtures follow the Linux kernel source; they were **not captured** from a real OOM on the lab VM. The P03-01 fixture line is a simulated kernel-log line, not a real OOM.

Run the tests: `python -m pytest -q tests/test_p03_02_oom_events.py`

## 4. Decisions taken in review

1. **RAM status** stays out of the OOM schema (contract unchanged). The smoke only shows `/proc/meminfo` as evidence.
2. **Wiring:** operator-owned `--linux-oom` flag, default off, fixed argv (implemented, section 2).
3. **Coverage:** judged per source.
4. **De-duplication:** by identity (boot id + kernel timestamp), section 1. Review `163079a` showed that pid/name/time alone merges independent kills.
5. **Cursor:** proposal in section 1; Thuy asked for it to be aligned with R3 before it counts as settled.
6. Resolved by scaffold `5b73a50`: for log-source tools the server now keeps `coverage` for `PERMISSION_DENIED` / `UNSUPPORTED` errors (rebuilt from schema enums, worker text is never forwarded). It requires every source to be non-`ok` and the correlation IDs to be echoed, which this worker does.

## 5. Known limits

- `journalctl` permission detection relies on the systemd 245 hint text plus a group check; another systemd version may word it differently (the group check still avoids a false "no OOM").
- `dmesg` wall times are the kernel start time plus the printed offset, so they can drift after a VM or host pause. The journal time wins when both sources report the same kill. If the kernel runs with `printk.time=0`, `dmesg` lines carry no offset: the source then has no usable lines and the result is `partial`.
- Only kernel OOM kills are reported, not user-space killers such as `systemd-oomd`.
- Not tested on a real OOM; not tested with other kernel versions.

## 6. Smoke (lab VM `linux-log-lab`)

Commands (in the VM; `reader-user.sh` and `fixtures.sh` are from P03-01):

```
bash ~/p03-01/fixtures.sh up
bash ~/p03-01/reader-user.sh groups-none;    bash smoke.sh A-no-groups .
bash ~/p03-01/reader-user.sh groups-journal; bash smoke.sh B-journal .
bash ~/p03-01/reader-user.sh groups-none
```

Expected with the per-source rule (the VM boots shortly before the run, so `dmesg` starts after `since`):

| Stage | Reader groups | Response | `journal.kernel` | `dmesg` | Events |
|---|---|---|---|---|---|
| A | none | `partial` | `permission_denied` | `ok` (gap) | 1 (pid 4242, from `dmesg`) |
| B | `systemd-journal` | `partial` | `ok` | `ok` (gap: oldest after `since`) | 1 (pid 4242, journal and dmesg merged) |

Evidence: `docs/evidence/p03-02-A-no-groups.txt`, `docs/evidence/p03-02-B-journal.txt`. The first run (2026-11-16) was made before the review fixes and reported `ok` for stage B under the old union rule; re-run it with the current code.
