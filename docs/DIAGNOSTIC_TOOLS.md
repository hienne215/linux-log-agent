# Local read-only diagnostic Agent

This local implementation reuses the canonical MCP server, official SDK stdio,
ToolBridge, Coordinator, asynchronous Ollama worker and Incident Report renderer.
It is not a privileged remediation system or evidence of all deployment gates.
The changes remain local and uncommitted.

## Run

From a configured installation:

```bash
linux-log-agent
```

The installed launcher defaults to live Ollama and the `all` application scope.
Enter a natural-language question at the prompt; enter `thoát` to exit. A new
question starts a new session. From a source checkout with pinned dependencies:

```bash
PYTHONPATH=src .venv/bin/python -B -m agent.read_only --scope all
```

`--backend mock` uses scripted model responses, **not natural-language reasoning**;
it still invokes real read-only collectors unless a test explicitly substitutes
fixtures. Neither tests nor mock output should be labelled live model results.
`--show-model-text` displays the original unverified model prose separately.
By default, terminal answers prioritize validated collector fields.

## Trusted application scope

| Tool | Implemented local profile | Important limits |
| --- | --- | --- |
| `service_status` | systemd status for nginx.service, ssh.service, cron.service | Missing is not failed; active is not application health. |
| `list_failed_units` | Visible failed systemd units | No restart; descriptive text withheld. |
| `disk_usage` | Root filesystem byte usage and inode counts | Counts come from one statvfs observation; namespace and user availability matter. |
| `listening_ports` | TCP/UDP, optional port, or bounded full snapshot | IPv4/IPv6; missing process ownership is permitted; local addresses withheld in projection. |
| `query_journal` | nginx.service, priority 0..7, last 1..1440 minutes | Defaults priority 3/15 minutes; at most 100 records. |
| `search_log` | Bounded literal query in nginx journal or fixed nginx error.log/current + .1 | No regex, arbitrary path or shell; rotation races and unreadable sources fail closed. |
| `detect_anomaly` | nginx error journal, two equal 15-minute windows | Local uncalibrated rate rule; zero baseline is insufficient, always partial, no root cause claim. |
| `verify_fix` | Fresh service state, fixed HTTP HEAD and new error logs | Default full profile; unknown/partial never means verified repair; no action execution. |
| `memory_oom_events` | Kernel journal OOM events plus /proc/meminfo snapshot | Preserves actual event timestamps/PIDs; non-allowlisted process names pseudonymized. |
| `failed_logins` | Trusted root-origin authentication journal, fixed auth.log fallback | Per-run HMAC pseudonyms for identities/IPs; bounded sources, no raw messages. |
| `check_config` | Fixed nginx -t in read-only filesystem + PID/network sandbox | No unsandboxed fallback. Host permissions/runtime prerequisites can block validation. |
| `restart_service` | Canonical proposal/status only, explicit VM operator session | No approve/execute mode; default scope still exposes only the eleven read-only tools. |

Tool names are capabilities, not a list of fixed questions. The model proposes
business arguments; the application owns metadata, paths, executable argv,
source permissions, windows/defaults and dispatch authorization. Model output,
discovery and user text cannot expand that authorization.

Examples (available data/permissions determine the outcome):

- “Nginx có đang chạy không? Kiểm tra thêm HTTP health.”
- “Ổ đĩa gốc còn bao nhiêu dung lượng và inode?”
- “Có unit nào đang failed không?”
- “Port TCP 22 có đang lắng nghe không? Kiểm tra UDP 53 nữa.”
- “Đọc lỗi journal Nginx trong một giờ gần đây.”
- “Tìm chuỗi upstream trong error log Nginx.”
- “So sánh tần suất lỗi journal Nginx giữa hai khoảng 15 phút.”
- “Máy có dấu hiệu hết RAM hoặc OOM gần đây không?”
- “Có đăng nhập thất bại trong 15 phút vừa qua không?”
- “Kiểm tra cú pháp cấu hình Nginx.”

For a focused scope use `--scope oom`, `auth`, `health`, `config`, `ports`,
`journal`, `search`, `anomaly` or `service`. Legacy `--scope verify` deliberately
retains the old service-active-only profile; `health` and `all` use three checks.

## Health and evidence semantics

HTTP health is application-owned: HEAD / at 127.0.0.1:80, Host localhost,
three-second timeout, bounded headers, no proxy, redirect, cookies or retained
body. A 2xx response only passes this particular HTTP check. It does not verify
business correctness, TLS, public reachability or a performed repair.

The default verification window covers the last 15 minutes. A trusted caller
can supply an actual action timestamp and external baseline references; the
model cannot create actions, approvals or baseline observations. External
baseline references remain distinct from newly observed evidence. Missing log
coverage keeps the aggregate partial/unknown; a visible new error can fail a
check. An active service alone never establishes overall verification.

OOM/auth observations preserve actual source timestamps and source coverage.
Zero visible events does not establish that no event occurred. Authentication
identities/IPs are pseudonymized with an ephemeral per-run key, not correlatable
across separate runs. Journal authentication records require protected root UID
metadata; a user-supplied syslog identifier alone is insufficient authority.
Raw log messages are replaced by bounded safe classifications before IPC.

Full validated, sanitized canonical results remain in the binding ledger. The
LLM receives bounded projections (at most three OOM/auth event examples plus
coverage/memory snapshots; anomaly uses its own bounded representative sample).
Citation IDs must resolve to retained evidence. Reference resolution is **not
semantic verification of the conclusion**. Tool data remains untrusted data,
not system instructions. An absent source is not replaced with synthetic success.

For the local systemctl profile, the Agent checks the unit and explicit state
vocabulary against exactly one fixed-format evidence snapshot. Data fields,
excerpt, source, unit, UTC observation interval and flags must agree. An unknown
state, injected excerpt, extra snapshot or unexpected cursor is rejected before
LLM/report publication. The same check is reused by both service verification
profiles; a valid citation ID alone cannot authorize a contradictory state.
The RAM projection similarly requires one meminfo snapshot with exactly
`MemTotal` and `MemAvailable`: integer byte counts (not booleans/strings), a
positive bounded total and availability between zero and total. Malformed,
ambiguous or missing memory snapshots cannot be printed as collector facts.
These application-profile checks do not modify canonical schemas, authenticate
the peer's actual OS acquisition, or prove a later conclusion semantically true.

## Safety and budgets

- Eleven read-only profiles are exposed in `all`; at most **five dispatches per
  question**, six Coordinator turns, two LLM requests, 45 seconds/request and
  120 seconds diagnosis budget. Discovery/startup is included in session budget.
- Opening a session starts no MCP subprocess. Only a model-selected, locally
  valid tool starts its fixed server; the peer's canonical discovery schemas
  must match before dispatch. A failed/cancelled connection is not retried.
  Compact model hints retain all eleven names and exact business schemas;
  reducing descriptive text does not grant or remove dispatch authority.
- This CLI opts into Ollama's documented `format` JSON Schema for the
  conclusion-only request (no tools supplied). The Adapter default remains off;
  native tool requests never carry this format. Local shape/citation validation
  is still mandatory, with no repair/fallback/retry of rejected model text.
  Fixed diagnostic reasons distinguish invalid JSON, conclusion shape, unknown
  citations and duplicate citations without retaining prompts or reasoning.
  For the final request, the application derives a citation enum exclusively
  from observed evidence IDs. User/model text cannot add IDs to that enum. The
  response is still rejected locally if any citation is unknown or duplicated;
  schema-constrained generation is not semantic evidence verification.
  See [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs).
- Each binding is single-use per session. No retry, reconnect/fallback or third
  inference request is used to repair invalid/late model output.
- Canonical metadata/correlation IDs are generated locally. Complete request,
  response, error semantics, references and source provenance are validated.
- Raw acquisition: five seconds, stdout 64 KiB/stderr 4 KiB for shared subprocess
  reader. MCP output remains bounded by existing canonical transport limits.
  Overflow is an error, not an invented complete page or cursor.
  Kernel OOM/authentication queries use fixed application-owned MESSAGE filters
  before the row/byte cap, so unrelated journal chatter does not consume the
  whole event budget. Nonzero journal exits remain unavailable, not empty success.
- Owners retain cleanup separately from diagnosis budget. Timeout/disconnect
  after dispatch is unknown execution, not proof of nonexecution. Worker
  terminate/kill/reap does not guarantee Ollama backend inference has stopped.
  The three health checks share a structured task owner: a producer failure
  cancels and joins its siblings before an error can be published. HTTP socket
  close is retained and joined even under repeated caller cancellation. A slow
  close can exceed the request budget; it is not silently detached or a hard
  wall guarantee. Failed checks do not manufacture a completed health report.
  Service-status, failed-unit, socket and journal acquisition share the same
  process cleanup joiner. It owns an in-flight spawn, cancels and joins readers,
  drains both pipes and waits for the known child, including after repeated
  caller cancellation or a pipe read error. It does not search for or kill other
  processes, reconnect or resend a tool. Service-status acquisition includes
  process startup in its five-second deadline; cleanup remains separately owned.
- In local reader mode, `check_config` never runs outside its fixed bubblewrap sandbox. It
  uses an absolute nginx executable and -t, never reload/start/stop. Runtime or
  permission errors are not classified as invalid syntax based on [emerg] alone.
  The current non-root Linux probe is blocked by an AF_INET bind permission
  denial inside the isolated network namespace. A minimal-device probe retained
  only namespace CAP_NET_BIND_SERVICE, with read-only host/config mounts, but
  the enforced bwrap//&unpriv_bwrap AppArmor stack still denied port 80 while
  allowing port 8080. The installed profile explicitly denies capabilities.
  No AppArmor policy, sysctl or host permission was changed. This is not a syntax result;
  a suitable reviewed sandbox runtime is needed for positive acceptance on that
  host. The separately opted-in lab VM instead uses the fixed DynamicUser/systemd
  validator described in [lab deployment](../deploy/lab/README.md); no host
  AppArmor policy, reader group or capability is changed by enabling that VM path.
- Reports are private (0600), published without following symlinks or overwriting
  existing names. `--report example.md` writes under reports/ in the operator's
  current directory. Reports include the full safe canonical ledger, are capped
  at 70,000 UTF-8 bytes, and fail closed if unsafe or oversized.
- **restart_service is not enabled.** A separated approval/helper candidate is
  installed only in the lab VM with `write_enabled=false`; proposal approval is
  not execution. Real restart and after-action acceptance remain pending. There
  is no automatic repair or privileged Agent/LLM.

Local approved schema extensions add `nginx` to check_config profiles and
`http_health` to verify_fix request/response enums. These are backward-compatible
additions with regenerated result schema, not a claim of unchanged reviewed R3
contracts or team-wide signoff. Inode details use existing evidence, without
adding new canonical data fields.

`linux-log-agent --doctor --scope service` checks only the selected application's
software prerequisites and packaged canonical resources. It starts no MCP,
collector or inference. Missing unrelated nginx/bubblewrap executables do not
block a service-state read. Per-tool `NOT_VERIFIED` means runtime permissions,
source coverage and actual behavior remain untested, even when doctor exits 0.
For config checks, executable availability never accepts the sandbox policy.

## Verification scope

On 19 November 2026, Python 3.12.16/MCP 2.3.0 local regression produced 998 PASS,
2 optional legacy fixture SKIP (345 subtests reported separately). This broad run
preceded final inode/projection refinements; final affected regression then
produced 191 PASS. Later focused tests cover the runtime/config classifier and
safe ledger export. These overlapping suites must not be added as independent
experiments or interpreted as accuracy.

Actual laptop SDK reads (mock HTTP model + real Linux collectors) observed:

- OOM: no visible event, bounded kernel coverage and a real RAM snapshot; partial.
- Auth: no visible event, explicit coverage snapshot; partial.
- Health: service-active and loopback HTTP checks passed; new-log coverage unknown.
- Ports: an unfiltered read returned TCP/UDP sockets on ports beyond 80.
- Disk: real byte and inode counts from statvfs.
- Config: PERMISSION_DENIED in the non-root read-only sandbox, no syntax verdict.

One new bounded live Ollama RAM/OOM attempt with the full eleven-tool scope did
**not** complete: request one reached its 45-second deadline before any tool call
(60.655 seconds session elapsed including startup). No MCP dispatch or evidence
was produced, no second request/retry was sent. Available RAM was sampled every
100 ms with a minimum of 3.207 GiB; brief dips between samples are not excluded.
The model worker exited -15, all client/model/bridge owners were released and no
test process remained when checked. This does not prove backend inference stopped
or establish the cause of model latency. Broad-scope live readiness is unverified;
earlier focused live results must not be generalized to all eleven profiles.

These results do not establish positive OOM/auth event detection, invalid real
configuration handling, deployment reader UID/VM acceptance, calibrated anomaly
accuracy, successful remediation or all-tool live LLM reliability. Positive
error/event examples and adversarial cases use explicitly synthetic fixtures.
No host permission/service/model changes, sudo, restart, commit or push occurred.

### Follow-up verification, 20 November 2026

Lazy MCP startup and compact hints were checked with real SDK fixtures. The
eleven model tools retain identical business schemas; descriptor size reduced
from 5,044 to 3,245 UTF-8 bytes in the measurement. Only the selected peer starts.
Canonical discovery is still checked before dispatch; failed startup cannot be
reconnected automatically. Health CLI + real Linux read confirmed service and
loopback HTTP pass, journal unknown, three evidence records, private report and
no-overwrite. It used scripted HTTP, not live model reasoning.

A live request then selected memory_oom_events correctly but hit OUTPUT_LIMIT:
unrelated kernel messages consumed the source cap. Fixed source-side OOM/auth
filters were added without increasing caps. SDK verification on the changed
source returned a real meminfo snapshot with partial/unavailable kernel coverage.

Further bounded live verification acquired real RAM but rejected the final
conclusion. The last run (29.425 seconds, two model requests, one MCP dispatch)
identified UNKNOWN_CITATION safely, retained the RAM evidence and released all
owners. JSON shape enforcement alone did not ensure correct evidence references.
The final patch therefore derives the generation schema's citation enum from
observed evidence IDs and retains independent local validation. Subsequent live
verification completed in 67.784 seconds: two inference requests, one actual
memory_oom_events dispatch, real meminfo evidence, a matching citation and complete
cleanup (both worker exit codes 0). Minimum sampled available RAM was 5.530 GiB
at 100 ms intervals. Kernel journal coverage remained unavailable and status
remained limited. This verifies that one RAM-evidence path, not positive OOM
events, all eleven tool selections or model reliability. No model citation was
rewritten or manufactured and no failed inference request was retried.

Final regression: 178 PASS, 250 subtests separately, zero failures/skips. Earlier
overlapping suites are not added to this count. A subsequent focused check covers
the final citation-input validation guard. Runtime remains Python 3.12.16/MCP
2.3.0. Config permission/runtime and privileged remediation remain unresolved;
this is not all-tool acceptance or production readiness.

## Installation and environment checks

The distribution now includes the Agent, canonical contract utilities, MCP server,
workers and all 40 canonical JSON artifacts (manifest included). The installed
`linux-log-agent` entrypoint defaults to Ollama and `all`, like the local launcher.
Resource resolution supports both a source checkout and a wheel installation;
no personal source directory is required. See the root README for installation.

`linux-log-agent --doctor` checks pinned runtime/model metadata and executable
presence without inference, MCP startup or collector reads. `--backend mock`
omits Ollama metadata checks. A successful software preflight does not establish
reader permissions, config sandbox viability or VM acceptance. Diagnosis/report
arguments cannot be combined with `--doctor`.

The wheel was actually built and installed into a separate temporary Python 3.12
venv. Agent/server imports were verified to come from that installation, not the
source checkout. Its CLI completed mock-LLM + real statvfs stdio, emitted byte and
inode evidence, and wrote a private no-overwrite report. A separate packaged
mock-HTTP + official SDK synthetic port-conflict scenario also completed. All 40
JSON artifacts matched the local source byte for byte; test/runtime evidence and
venvs were excluded from the wheel. Editable installation was checked separately.
These test venvs reused existing pinned third-party dependencies through an explicit
dependency path; they were not fresh internet dependency-resolution experiments.
Only build tools were installed into a separate /tmp build venv; the existing
runtime/system packages were not changed.

After the packaging/runtime changes, affected Agent/MCP/CLI regression completed
with **558 PASS, 2 optional legacy fixture SKIP**, 349 subtests separately. Doctor
and runtime-focused verification subsequently passed 33 tests. The suites overlap
and must not be summed. This does not accept deployment hardening or remediation.


### Filtered lab reads, 23 November 2026

The separately authorized lab gateway now allows the unprivileged reader to use
six fixed filtered profiles without broad log-group membership. Official SDK
stdio completed twelve real Linux read requests over eleven unique tools. The
Nginx config validator passed in its DynamicUser/private-network sandbox;
query/search reads completed with explicit partial coverage. Health retained
journal uncertainty, anomaly retained insufficient signals, OOM kept unavailable
kernel coverage and authentication used the fixed auth.log fallback. No positive
error event, all-tool live LLM reliability, restart or remediation was inferred.
Reader/control process cleanup and unchanged Nginx invocation identity were
checked, then the VM shut down gracefully. See [lab deployment](../deploy/lab/README.md)
for the exact ownership, status table and deployment limits. The laptop's existing
reader permissions, model and services were not changed.

## Canonical proposal/status integration

Tool 12 uses the unchanged canonical restart schemas through a dedicated non-root
SDK stdio adapter. The model can propose only `unit=nginx.service` and
`mode=propose/status`; application code supplies correlation, fixed reason and
references to a fresh validated service observation. No new evidence is invented
for a proposal. Invalid, missing, altered or stale evidence blocks proposal creation.
The Coordinator stops at `AWAITING_APPROVAL` and emits an `approval_required`
report, without another model turn. Default read-only authorization still denies
restart proposals. The reader has no approval/helper socket, signing key or grant.

In the configured VM, an operator opens a session with `linux-log-action
open-session`. The reader may then run:

```bash
linux-log-agent --scope actions --action-session SESSION_ID --question 'Đề xuất restart nginx.service'
linux-log-agent --scope actions --action-session SESSION_ID --action-status ACTION_ID
```

Replace IDs with the application-created values. The second command performs
one canonical status read, no model inference. A zero exit code means the query
was validated, not that a restart or repair succeeded. Unknown job outcomes retain
`executed=null`; neither query retries execution or recovers a grant. The normal
operator TTY review and separate execution confirmation remain mandatory.
These commands are rejected outside the approved VM reader deployment.

On 23 November, one separate human-authorized VM restart was observed as one
helper intent and one systemd job done, with a changed Nginx invocation identity.
Fresh service/loopback HTTP checks passed; journal coverage stayed unknown, so
`repair_verified=false`. Execution was disabled again, control services stopped
and the VM reaped. This is a controlled restart of an already running service,
not proof of fault remediation, all-tool model accuracy or production acceptance.
No additional restart is performed to test the proposal/status integration.
