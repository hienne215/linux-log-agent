# P01-01 / SCRUM-15 — MCP scaffold v0.1.0-draft

Owner: Thủy; reviewer: Hiền; EPIC-01; Critical; 5 SP / estimate 6h (not measured effort). Date: 15/11/2026. **DRAFT / In Progress, not accepted or Done.** [SCRUM-15](https://vku-team-etxjrjs2.atlassian.net/browse/SCRUM-15).

Base: P00-03 candidate `d442479493d94c54a2d35698d3f920e7758b7744`; schema_version 1.0.0, official mcp==2.3.0, Python 3.12, Draft 2020-12, stdio. The user explicitly authorized draft implementation while P00-03 awaits signoff. This does not finalize P00-03, remove Jira dependency SCRUM-12 or authorize merging PR #6.

## 1. P01-01.1 — design and input checklist

| Boundary | Decision |
|---|---|
| Contracts | Reuse canonical registry, validators and shared envelope from P00-03. No signature-derived schema or new envelope. Exactly 12 discovery entries; discovery is not implementation/authorization. |
| Shared code | Temporarily import P00 helpers from mock_mcp_server.py. Its bound_response gains an optional native_builder callback so actual scaffold SDK metadata is measured; the old mock behavior remains the default. No business fixture is invoked automatically by this import. |
| Backend availability | Default server has no Linux collectors: valid calls return schema-valid UNSUPPORTED. --fixtures explicitly enables only synthetic service_status in a fixed isolated Python worker. restart_service cannot be a registered worker. |
| SDK and dispatch | Official low-level Server and stdio_server. No custom JSON-RPC transport, product UI, LLM inference or helper. Absolute executable/argv, fixed repository cwd and minimal environment are application-owned; MCP arguments only go through stdin, not command/executable/cwd/sudo fields. No shell=True. |
| Budgets | Default 10s monotonic worker deadline including spawn/I/O; IPC input <=16 KiB, worker stdout <=64 KiB, discarded stderr <=4 KiB; whole native tools/call frame <=64 KiB UTF-8 including exact admitted ID, SDK metadata and newline. |
| Concurrency | One owned worker; busy/closing requests fail POLICY_DENIED. No unbounded worker queue or automatic retry. |
| Privacy | Static safe codes/messages/rules; no raw child stderr, exception, malformed output, forged IDs or worker error payload. All scaffold error responses, including late output-cap failure, suppress application correlation metadata. Successful/partial results echo validated client IDs. |
| Output | Independently validate complete canonical structured envelope; child success must also match tool/client metadata. Native text/structuredContent/isError agree. Keep whole-record partial or bounded error; never split JSON/UTF-8/records. |
| Identity | Refuse root on POSIX. Never change UID/groups, call sudo/setuid, or claim process isolation from the Agent sharing its UID. Deployment supplies reviewed reader identity and immutable code/config. Windows tests are not Linux permission/sandbox evidence. |
| Lifecycle | Own pending spawn/direct child; timeout/cancel/failure terminate -> grace -> kill -> wait. Unconfirmed cleanup retains ownership, refuses new work and uses EXECUTION_UNKNOWN/rule=cleanup_pending when a response exists. OS creation/teardown is not a hard wall SLA. |

Moving helpers into a final production package should follow P00 signoff; this task does not refactor the 12 business tools or fork their schemas.

## 2. P01-01.2 — deliverable and launch

- run_mcp_server.py: fixed stdio entrypoint with operator-owned --fixtures flag.
- src/mcp_server/server.py: validation, canonical discovery, native result mapping and ID admission.
- src/mcp_server/runner.py: bounded direct-child owner, timeout/cancellation, cleanup and minimal environment.
- src/mcp_server/fixture_worker.py: one synthetic fixture, no Linux reads.
- tests/test_p01_scaffold.py plus two fixed test-only fault launchers. Fault modes are not production CLI flags or MCP arguments.

From the repository and its existing Python 3.12 venv:

```text
.venv/Scripts/python.exe run_mcp_server.py
.venv/Scripts/python.exe run_mcp_server.py --fixtures
```

These are entrypoints for an SDK stdio client, not interactive commands to paste into the server's stdin. Default mode returns UNSUPPORTED; fixture mode can only produce explicitly synthetic service_status success. A real SDK handshake is not proof of reading real Linux data.

Linux launch requires administrator-owned readable source/venv and the accepted reader/service/stdio deployment topology. Account/group setup is outside MCP. No VM deployment or Linux permission probe is performed here. HTTP/socket transport is not enabled.

### Transport and client obligations

P00 policy remains: JSON-RPC ID <=128 serialized UTF-8 bytes including quotes/escapes; reject oversized IDs without echo/truncation and terminate the dedicated scaffold process with exit 2. First reap any owned worker, or retain cleanup ownership until confirmed, before exit. SDK parsing has already occurred: this is not a raw pre-parse allocation/DoS limit; discovery is outside the per-tool-result cap.

On EOF/exit 2, fail outstanding calls without inventing CommonResponse/success. Record exit 2 only if observed; EOF alone does not establish its cause. Retain cleanup ownership while unconfirmed, invalidate old cursors and never automatically retry a write. A fresh process is a fresh session.

The runner owns a direct fixed worker, not arbitrary descendant process trees; profiles spawning descendants need a separately reviewed process-group/job-object policy. No universal orphan-free or hard wall guarantee is claimed. Valid successful metadata/free text are not covered by a general-purpose production secret redactor; do not feed real credentials/untreated logs to this draft. Future collectors must redact and normalize before emitting successful evidence.

## 3. P03-01 branch read and gates

Read-only inspection: [PR #13](https://github.com/hienne215/linux-log-agent/pull/13), feature/P03-01-reader-permissions, pinned `f914a3615356b7944cae3912e670b255df096927`. Read permission matrix, runbook, three scripts and textual probe evidence. None was run, copied into MCP dispatch or modified; no host/VM permission changed.

1. Reader has no sudo in that VM; UID/GID are system-assigned and must not be hard-coded. Agent/MCP share a UID, not an OS isolation boundary.
2. systemd-journal exposes the whole journal: keep source/unit allowlists and collector redaction. Do not default to adm; the ACL alternative is unverified.
3. service_status only has is-active evidence; required systemctl show fields are unprobed. Real collector implementation belongs to SCRUM-17/P01-03, not this scaffold.
4. nginx -t is not read-only/ready verified; listening-port process information remains unknown. Do not escalate privilege or fabricate success to compensate.
5. Probe evidence ends with journal+adm although the runbook recommends groups-none. Trang must confirm/reset final VM group state and snapshot policy before real smoke tests.
6. probe.sh is an admin harness using sudo/bash -c, not an MCP collector. It lacks the hostname guard of the other scripts, truncates output and does not preserve all command exit statuses. These are review follow-ups, not execution permission or proof every tool is ready.
7. OOM fixture is synthetic; keyword counts do not establish absence of secrets; dmesg readability is VM-specific, not portable.

## 4. P01-01.3 — acceptance and evidence checklist

| Acceptance | Evidence | Status |
|---|---|---|
| Mock client connection | SDK in-process Client over same Server and schemas | Automated test |
| Real transport handshake | SDK subprocess stdio, auto + forced legacy initialize, canonical schema equality | Automated test; data synthetic/unavailable |
| Invalid input | Missing/extra/null/traversal rejected before spawn | Automated test |
| No shell=True | AST check, fixed argv, no wire command field | Automated test |
| Timeout | Real isolated Python child exceeds budget, safe TIMEOUT and cleanup | Automated test |
| Large output | Stream flood, Unicode/native cap, whole-record partial, valid bounded errors | Tests + actual stdout byte measurements |
| Error privacy | Synthetic sentinel in stderr/malformed/error/forged metadata is not reflected | Automated tests |
| Lifecycle | Known child reaped; no duplicate dispatch; unconfirmed owner retained/refuses work | Real-child tests + fault injection |
| Linux least privilege | Accepted VM, reader UID/groups and deployment probes | NOT RUN; Trang/VM gate pending |
| P00-03 finalization | Hiền/Trang explicit signoff | Pending; draft exception authorized by user |
| Independent P01 review | Hiền reviews this revision, tests and limits | Pending; not self-approved |

```text
.venv/Scripts/python.exe -m pytest -q tests/test_p01_scaffold.py
.venv/Scripts/python.exe -m pytest -q tests
.venv/Scripts/python.exe tests/mcp_smoke/test_all_tools_stdio.py
```

Final counts/logs and failure/fix notes are in delivery evidence. No credentials, real Linux logs, environment dumps or machine-specific runtime artifacts should be committed. Jira remains In Progress/draft with its SCRUM-12 blocking link intact; not Done until dependencies and independent review are satisfied.
