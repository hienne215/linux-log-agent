# Lab VM control plane

Candidate deployment only for the private QEMU/KVM VM `linux-log-lab-hien`.
Do not install these units on the laptop or a production server. The root-owned
runtime, configuration, Nginx unit/dependencies and account separation require
review. The default Agent/MCP command remains read-only.

The administrator provisions `/opt/linux-log-agent/runtime`, root-owned units,
three distinct accounts (`mcp-log-reader`, `lla-approval`, `lla-operator`), and
private state directories under `/var/lib/linux-log-agent/`. The helper state is
root-only; the approval state belongs only to `lla-approval`.

Fixed configuration `/etc/linux-log-agent/deployment.json`, root:lla-approval
0640, inside a root:lla-approval 0750 directory:

```json
{"environment_id":"linux-log-lab-hien","policy_version":"vm-candidate-v1","write_enabled":false}
```

The shared 32-byte key `grant.key` has the same owner/mode. Never put a key or
grant in Git, CLI arguments, environment variables, reports or logs. Only root
may opt this lab into execution. `write_enabled=false` denies dispatch at both
coordinator and helper, even if a grant exists. Hostname and QEMU checks are
additional lab guards, not authentication against a malicious administrator.

Runtime directories (created by administrator/tmpfiles; never by a model):

| Directory | Owner:group | Mode | Socket peer |
| --- | --- | --- | --- |
| `/run/linux-log-agent/proposal` | lla-approval:mcp-log-reader | 0750 | reader UID |
| `/run/linux-log-agent/control` | lla-approval:lla-operator | 0750 | operator UID |
| `/run/linux-log-agent/helper` | root:lla-approval | 0750 | approval UID |

Each process creates its own 0660 socket, so kernel peer credentials identify
the real server UID. Do not replace this with root-created socket activation:
the client's peer UID assumption would change. A stale socket fails startup;
the runtime never deletes a pre-existing path to make startup pass.

The approval service has only the two supplementary route groups, not sudo or
journal groups. It is part of the trusted computing base and holds the signing
key. Reader/model compromise cannot access control/helper sockets or the key.
Code running as the operator/coordinator or root is outside that protection.

Readiness uses systemd `Type=notify`: the helper publishes a fresh epoch and
binds its socket before `READY=1`; approval completes the pinned-UID/epoch
handshake before accepting clients. Neither service restarts automatically.

## Manual flow

Run each command as the named VM identity. No command accepts a shell, arbitrary
unit, path, model, URL, grant or automatic confirmation flag.

1. Operator: `python -I -B -m control_plane.command open-session`.
2. Reader: `python -I -B -m control_plane.command propose SESSION_ID`.
   This performs one actual canonical SDK `service_status(nginx.service)` read,
   validates it and attaches its real evidence IDs. No LLM is called. The proposal
   is an explicit lab request, not a claim that a healthy service needs repair.
   Before submission, the complete validated/redacted envelope is saved as a
   private no-overwrite Markdown snapshot under the reader's `reports` directory.
   Returned evidence and the saved ledger retain original IDs/source/timestamps.
   This is an untrusted reader observation, not independent semantic validation;
   the operator is not granted filesystem access to the reader's private home.
3. Operator: `python -I -B -m control_plane.command review ACTION_ID`.
   Type the exact `DONG_Y ACTION_ID` or `TU_CHOI ACTION_ID` on a fresh TTY.
   **Approval does not execute anything.**
4. Only if root enabled lab execution: operator separately runs
   `python -I -B -m control_plane.command execute ACTION_ID` and types the exact
   `THUC_THI ACTION_ID`. This can interrupt Nginx in this VM.
5. Operator explicitly queries `execution-status ACTION_ID`. No submit retry or
   reconnect/recovery occurs after transport failure. Normal status queries are
   separate read requests. `status ACTION_ID` means approval status only.

Use `/opt/linux-log-agent/runtime/bin/python` for `python` above. Never run the
Agent or LLM as operator/root. Pipe input, `--yes`, wrong IDs, EOF, expiry or
Ctrl+C cannot authorize execution. The total grant lifetime is still 60 seconds,
including both confirmation steps; a second prompt does not renew it.

The helper owns one admitted task across a client disconnect, journals intent
before its fixed D-Bus call, and retains cleanup until joined. Status preserves
`execution_unknown` and `executed=null`. A matching systemd `done` result means
the job finished; it does not prove HTTP health or successful remediation. A
fresh read/verification is a separate explicit reader operation:
`linux-log-action verify ACTION_ID --session SESSION_ID`. It uses the original
private canonical baseline, queries the existing helper attempt without dispatch,
then reads new service/loopback HTTP/journal evidence through SDK stdio. It saves
a 0600 no-overwrite Incident Report. Unknown execution and incomplete coverage
remain unknown/partial; the command never claims verified remediation. This path
has synthetic tests, not real after-restart acceptance. Restart stays disabled.

The durable unit lock is deliberately not reset after completion, uncertainty,
service restart or snapshot restore. There is no replay/unlock/reconciliation
command for consumed intents. An approved-but-expired action with **zero helper
intents** may use the explicit administrator reconciliation described in the
[control-plane guide](../../src/control_plane/README.md). Repeated demos need an explicit reviewed lab-state decision, not an
automatic retry. Never restore an active grant for reuse; a new helper epoch and
new approval generation invalidate old grants/pending actions.

References: [systemd readiness](https://www.freedesktop.org/software/systemd/man/latest/sd_notify.html),
[systemd execution sandbox](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html).
These units and mock tests do not establish production security acceptance.

## Filtered read gateway

`lla-read-gateway.service` is a separately authorized lab service. It runs as
root with supplementary `adm`, NoNewPrivileges and no effective capabilities.
The reader, Agent and MCP server stay non-root and receive no journal, adm or
sudo membership. This service is part of the trusted computing base; it is not
a production security signoff or a general-purpose root command runner.

Opt-in requires the root-owned, regular, non-symlink 0644 file
`/etc/linux-log-agent-read.json` with exactly:

```json
{"enabled":true,"environment_id":"linux-log-lab-hien"}
```

The pinned VM hostname/QEMU identity must also match. With no file, existing
local reader mode is unchanged. Invalid configuration or a failed gateway does
not fall back to another collector. Install the reviewed wheel and this unit
only in the private lab VM; never on the laptop. No runtime key is stored in
this public read-only configuration.

The socket `/run/linux-log-agent-read/service.sock` is root:mcp-log-reader 0660
inside a 0750 directory. Both sides pin kernel peer UIDs. Only the actual reader
can submit six canonical profiles: `query_journal`, `search_log`, `detect_anomaly`,
`memory_oom_events`, `failed_logins`, `check_config`. Full request schemas and
the existing application scope are checked again before collection. No arbitrary
executable, path, unit, URL, shell, grant or restart operation is accepted.
Other read-only tools continue through their existing unprivileged collectors.

Raw logs stay inside bounded collector children. Only canonical, sanitized
results cross the socket. The internal frame cap is 64 KiB, request deadline
eight seconds and root worker deadline six seconds; the existing approval
channel remains 4 KiB. One root worker and at most four retained connections are
allowed. Cancellation/disconnect does not cause retry or transfer ownership to
an untracked task. Service shutdown joins handlers and the worker. The service
cannot access the approval key, private control state or execution sockets via
its filesystem namespace.

For `check_config`, the root gateway asks systemd to run only the fixed
`nginx -t -q -e stderr` validator as a transient DynamicUser, with read-only
system/home protection, a private network, temporary run/log directories,
NoNewPrivileges, resource/time limits and only CAP_NET_BIND_SERVICE. The
validator never reloads or restarts Nginx. Its owner stops only its own generated
validator unit and joins cleanup, including repeated cancellation. There is no
unsandboxed fallback. This is separate from the original non-root bubblewrap
path, whose runtime can still deny validation on the laptop.

An empty bounded read is not evidence of complete log history. OOM/auth journal
sources may be unavailable even when their other sources are readable; coverage
and `partial` remain explicit. Fresh health verification also uses the filtered
gateway, but missing history still yields `unknown`, not successful remediation.

### Observed reader scope (2026-11-23)

The installed wheel was exercised through official SDK stdio as the VM's
`mcp-log-reader` (UID 999), with NoNewPrivileges, an empty capability set and no
adm/journal/sudo groups. The root gateway was separately checked for pinned peer
UID, NoNewPrivileges and zero effective capabilities. This used real Linux IO,
no model inference, no restart and no artificial log events:

| Tools | Observed result | Limitation |
| --- | --- | --- |
| service_status, disk_usage, list_failed_units, listening_ports | Canonical `ok` | Only the tested requests, not arbitrary service/network coverage |
| check_config | Canonical `ok`, nginx sandbox validation `valid=true` | This VM configuration only; not reload or negative-config acceptance |
| query_journal, search_log (journal and nginx error.log) | `partial`, bounded filtered reads completed | No matching records in the observed windows; complete history not established |
| detect_anomaly | `partial`, `insufficient_signals` and no fabricated evidence | No positive anomaly observation or calibrated accuracy |
| verify_fix | `partial`: service and loopback HTTP pass, journal unknown | Not verified repair or complete log coverage |
| memory_oom_events | `partial`: current RAM evidence, kernel journal unavailable | No positive OOM event validation |
| failed_logins | `partial`: auth.log readable, auth journal unavailable | No positive authentication event validation |

Twelve SDK requests covered eleven unique tools (search_log used both fixed
sources). All SDK owners joined. The gateway rejected a write tool, extra command
and forged UID fields; the reader could not open control secrets/private state.
An idle socket receive reached its bounded timeout without dispatching a tool.
On shutdown, no reader/approval/operator process, gateway socket or transient
validator unit remained. The helper ledger contained zero intents, execution
remained disabled and Nginx's invocation/start identity was unchanged. QEMU
shut down gracefully and was reaped; snapshot
`filtered-read-ready-restart-disabled-v1` preserves the installed candidate.

Installed-runtime mock/control tests separately passed 184 parent tests and 132
subtests. The first run had test-package import collisions, corrected by importing
installed packages before test discovery; it was not a collector failure. An
intermediate cleanup check was inconclusive; the final isolated run explicitly
stopped unrelated control daemons before the read probes and confirmed no role
processes remained. All failed-run logs were retained outside the source tree.

These observations are not live all-tool LLM acceptance, complete log visibility,
production security signoff or successful remediation. One previous human
approval expired before execution and created zero helper intents; it did not
restart Nginx. Its conservative action lock is not automatically cleared. Final
human restart/after-action acceptance requires a separately reviewed fresh-state
procedure; never reset private state or restore an old grant to force a retry.
The explicitly authorized no-intent administrative procedure appends history;
it does not permit replay or reconcile an actual/unknown execution.

Final source regression on Python 3.12.16/MCP 2.3.0 passed 1,809 parent tests,
with 519 subtests counted separately and four intentional host-safety skips.
Additional gateway cap checks passed 15 parent tests and seven subtests, including
Unicode expansion above the 64 KiB frame cap; these overlap the broad run and
must not be added to it. The installed CLI also completed mock HTTP model →
Coordinator → SDK stdio → real sandbox config validation → Incident Report, with
one tool dispatch, a validated canonical ledger, a 0600 report and complete owner
cleanup. This was explicitly not live Ollama inference. Fourteen original
worktrees and 54 historical root untracked files retained their Git states and
checksums. Source remains local and uncommitted.
