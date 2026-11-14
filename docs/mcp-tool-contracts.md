
# MCP Tool Contracts

**Project:** Linux Log Agent  
**Owner:** Thủy  
**SDK:** Official MCP Python SDK 2.3.0  
**Python:** 3.12  
**Transport:** stdio  
**Contract version:** 1.0.0  
**Status:** Contracts 1.0.0 ready for Hiền/Trang review — revision 2026-11-14-R1; NOT finalized, signoff pending

**Review entry point:** [Final contract review and collector handoff](SCRUM-12-contract-review-2026-11-14.md). Runtime audited at `917d9f0`; this revision changes documentation only.

---

## 1. Purpose

This document defines the common request/response envelope,
input/output contracts, error handling, evidence format,
and safety constraints for the 12 MCP tools.

All tools must use the Official MCP Python SDK.
The MCP Server must not execute arbitrary shell commands,
use sudo, or perform privileged operations.


## 2. Common Request Envelope

Each tool request contains the following common metadata fields:

| Field | Type | Required | Description |
|---|---|---|---|
| schema_version | string | Yes | Contract version, currently 1.0.0 |
| session_id | string | Yes | Client session identifier |
| trace_id | string | Yes | Request tracing identifier |
| logical_tool_call_id | string | Yes | Client-generated identifier for one logical tool call |
| attempt_id | string | Yes | Client-generated identifier for one execution attempt |

Tool-specific input arguments are passed as additional arguments defined by each tool's input schema.

ID ownership:

- The MCP Client generates `logical_tool_call_id` once for a logical tool call.
- The MCP Client generates a new `attempt_id` for every retry attempt.
- Retries of the same logical call reuse the same `logical_tool_call_id`.
- The JSON-RPC request ID is managed by the MCP SDK and must not be replaced by either application ID.
- The MCP Server validates and echoes the application IDs in the common response.


## 3. Common Response Envelope


Every tool returns a normalized result with these fields:

| Field | Type | Description |
|---|---|---|
| schema_version | string | Contract version |
| session_id | string/null | Client session identifier |
| trace_id | string | Request tracing identifier |
| logical_tool_call_id | string | Logical call identifier from Client |
| attempt_id | string | Execution attempt identifier from Client |
| tool | string | Tool name |
| status | string | Execution status |
| started_at | string | RFC3339 UTC execution start time |
| completed_at | string | RFC3339 UTC execution completion time |
| duration_ms | integer | Execution duration in milliseconds |
| data | object | Tool-specific result |
| evidence | array | Supporting evidence |
| error_code | string/null | Normalized error code |
| truncated | boolean | Whether output was truncated |
| next_cursor | string/null | Pagination cursor |

Tool-specific fields such as `unit` belong inside `data`, not in the common response envelope.

The JSON-RPC request ID is managed by the MCP SDK and is not part of this application-level response envelope.


### 3.1 Status values

- `ok`: Tool completed successfully.
- `partial`: Tool returned incomplete or truncated data.
- `error`: Tool execution failed.
- `approval_required`: An operation requires external approval.

### 3.2 MCP result mapping

| Contract status | MCP isError | Meaning |
|---|---|---|
| ok | false | Successful result |
| partial | false | Partial result; do not treat as complete |
| error | true | Tool execution error |
| approval_required | false | Approval is pending; operation was not executed |

The structured result preserves the contract status.
The content field contains a filtered summary for the model.

Unknown tools and invalid protocol requests are handled by the MCP SDK.

## 4. Timestamp Rules

All timestamps use RFC3339 UTC format.

- `started_at`: time when tool execution begins.
- `completed_at`: time when tool execution ends.
- `evidence.observed_at`: time when the underlying event was recorded.

Time-range queries use the half-open interval `[since, until)`.


## 5. Evidence Object

Each evidence item contains:

| Field | Type | Description |
|---|---|---|
| evidence_id | string | Unique evidence identifier |
| observed_at | string | Event observation time in RFC3339 UTC |
| source | string | Approved source identifier or location |
| unit | string/null | Related service unit, when applicable |
| excerpt | string | Filtered evidence excerpt |
| redacted | boolean | Whether sensitive data was redacted |
| truncated | boolean | Whether the excerpt was shortened |

Evidence must identify its source.

Sensitive information must be redacted before returning results.

Evidence references in tool-specific data must refer to evidence identifiers returned by the relevant tool or an explicitly supplied baseline.


## 6. Error Codes

The common error code set is:

- `INVALID_INPUT`
- `PERMISSION_DENIED`
- `TIMEOUT`
- `OUTPUT_LIMIT`
- `NOT_FOUND`
- `UNSUPPORTED`
- `POLICY_DENIED`
- `APPROVAL_REQUIRED`
- `EXPIRED`
- `REPLAY`
- `HELPER_UNAVAILABLE`
- `EXECUTION_UNKNOWN`

An empty result is not the same as permission denied.
A partial or truncated result must never be presented as complete.

## 7. Initial Resource Limits

These are initial limits and must be validated during implementation:

| Limit | Initial value |
|---|---|
| Read-tool timeout | 10 seconds |
| Maximum response size | 64 KiB |
| Maximum events per call | 200 |
| Default query window | 24 hours |

The 64 KiB limit applies to each serialized tools/call JSON-RPC response line, including native content, structuredContent, SDK metadata, the request ID, and the newline. Discovery is a separate registry response, not a tool result.
Collectors must also bound the amount of data they read.

Pagination cursors must be bound to:
- Tool name
- Normalized query filters
- Client session
- Expiration time

Expired or mismatched cursors must be rejected.

## 8. Tool Registry

Exactly 12 tools are included in this contract.

| No. | Tool | Access | Owner | Purpose |
|---:|---|---|---|---|
| 1 | query_journal | R | Thủy | Query journal events |
| 2 | search_log | R | Thủy | Search approved log sources |
| 3 | service_status | R | Thủy | Inspect service status |
| 4 | list_failed_units | R | Thủy | List failed systemd units |
| 5 | disk_usage | R | Thủy | Inspect disk and inode usage |
| 6 | memory_oom_events | R | Trang | Find OOM events |
| 7 | failed_logins | R | Trang | Inspect failed login events |
| 8 | listening_ports | R | Thủy | Inspect listening ports |
| 9 | check_config | R | Thủy | Validate supported configurations |
| 10 | detect_anomaly | R | Hiền | Detect log anomalies |
| 11 | verify_fix | R | Hiền | Verify whether an issue was fixed |
| 12 | restart_service | W facade | Trang | Create or read restart proposals |

`R` means read-only.
`W facade` means proposal/status operations only, not actual execution.

## 9. Security Constraints

- No arbitrary shell commands.
- No arbitrary executable paths.
- No sudo or privilege escalation.
- No direct systemctl restart from MCP.
- No access to privileged helper control sockets.
- No secrets or approval tokens exposed to the Agent.
- Service unit names must be canonical and allowlisted.
- Input schemas must reject unknown fields using `additionalProperties: false`.
- All collectors must use fixed, reviewed commands or approved APIs.

### 9.1 restart_service safety

The MCP tool may only:

- Create a restart proposal.
- Read proposal status.

It must not:

- Restart or stop a service.
- Execute systemctl.
- Invoke subprocess or a privileged helper.
- Accept `approved=true` or an approval token as authorization.

The mock implementation must never perform a real restart.

## 10. Authoritative Tool Contracts (1.0.0)

`schemas/manifest.json` is the registry index. For each tool:

- `schemas/tools/<name>.schema.json`: canonical complete input schema, self-contained Draft 2020-12. Discovery publishes this file verbatim; runtime validates raw `arguments` with the same schema before Python defaults are applied.
- `schemas/tools/<name>.response.schema.json`: canonical successful/partial/approval **data**, not the complete result.
- `schemas/tools/<name>.result.schema.json`: canonical discovery `outputSchema`, covering the entire `structuredContent` envelope. Its conditional error branch uses the strict error-data shape; every other status uses the tool data schema. Composition tests verify the embedded common/data/error contracts have not drifted.
- `schemas/common-request.schema.json`, `schemas/common-response.schema.json`, and `schemas/error-data.schema.json` define common shapes. Input metadata constraints are materialized in the input files for discovery without local filesystem references. Tests verify composition.

`schemas.zip` is a legacy snapshot and is NOT authoritative. `inspect_tool_metadata.py` prints the canonical registered descriptors; it no longer derives schemas from Python signatures.

| Tool | Required business fields | Optional business fields | Normal result / data | Pagination |
|---|---|---|---|---|
| query_journal | unit, since, until | priority, limit, cursor | ok; entries/count/limit | yes |
| search_log | source_id, query, since, until | limit, cursor | ok; source_id/query/matches/count/limit | yes |
| service_status | unit | none | ok; unit/active_state/sub_state/load_state/description; synthetic evidence | no |
| list_failed_units | none | limit, cursor | ok; failed_units/count/limit | yes |
| disk_usage | none | filesystem, include_inodes | ok; filesystem/mount_point/used_percent/available_bytes/inodes_included | no |
| memory_oom_events | since, until | limit | ok; events/count/limit | no |
| failed_logins | since, until | limit | ok; events/count/limit | no |
| listening_ports | none | protocol, port, unit | ok; ports/count | no |
| check_config | profile | unit | ok; profile/unit/valid/issues/message | no |
| detect_anomaly | since, until | unit, signals, max_findings | ok/partial; anomalies/analyzed_signals/insufficient_signals/findings_truncated/confidence | no |
| verify_fix | unit, since, check_types | until, action_id, baseline_evidence_refs | partial; checks and overall_result=unknown for synthetic mock | no |
| restart_service | mode, unit, reason; action_id additionally for status | evidence_ids, action_id for propose | approval_required for propose; ok for status; executed=false | no |

All tools additionally require all five common metadata fields. Canonical schemas specify types, enums, numeric limits, patterns and required fields. Missing optional values remain missing on the wire. Only `verify_fix.until`, `action_id`, and `baseline_evidence_refs` explicitly accept null; all other optional fields reject null. Function defaults are internal implementation details. Units reject paths, options and template instances; OS deployment allowlists remain a separate production gate.

### 10.1 Dispatch, errors and metadata ownership

The official `mcp.server.lowlevel.Server` receives `tools/call`; the application validates raw arguments before calling the original Python function. No high-level Pydantic signature model owns tool arguments. Unknown tools and malformed tools/call parameters return generic SDK protocol errors (-32602); malformed JSON and protocol negotiation remain owned by the SDK. An application schema violation returns native `CallToolResult(isError=true)` with status=error and INVALID_INPUT. No business function runs for schema-invalid input. Schema-valid arguments may still be rejected semantically (for example a reversed time window or invalid/expired cursor). Before output validation or serialization, INVALID_INPUT and EXPIRED semantic results are rebuilt through the same safe-error boundary with rule=semantic. No rejected application metadata or business message is copied into that result.

Valid request IDs are client-owned and echoed. For both schema-rejected and semantically rejected input, all application correlation IDs use the fixed `invalid-input` marker and session_id=null: even otherwise valid metadata could contain a submitted secret. JSON-RPC correlation remains intact. Error fields are fixed message, a schema-declared top-level field (or empty), and a validator rule name. Never return ValidationError.message, invalid values, attacker property names, exception text, or tracebacks. PermissionError maps to PERMISSION_DENIED; unexpected execution/output-contract failures map to EXECUTION_UNKNOWN. Output validation independently covers normal, partial, approval and error envelopes before serialization.

Native structuredContent is the complete envelope; text content is its JSON representation. status=error alone maps to isError=true. partial and approval_required remain false; approval is never execution. Production redaction is not implemented by this synthetic mock; valid free-text requests are not real-secret fixtures and must not be used as a secret store.

### 10.2 Output limits

`wire_size` uses the SDK JSONRPCResponse model and SDK serialization settings. Native results explicitly carry SDK serverInfo metadata and resultType so modern SDK additions are included in the size calculation. The final dispatch check includes the actual request ID. Regression tests observe actual stdout response lines before SDK client parsing and check their UTF-8 byte lengths, including multibyte Unicode and a near-cap case.

On overflow, keep complete records from supported record arrays when at least one complete record fits, update count, and return partial/OUTPUT_LIMIT/truncated=true. No bytes or JSON strings are sliced. This bounded partial has next_cursor=null: the omitted tail cannot be resumed, and the client must report incomplete data. If useful atomic data with its evidence cannot fit, return error/OUTPUT_LIMIT, empty evidence and no cursor, using the error-data schema. Oversized evidence is not silently converted into an evidence-free success. Metadata is bounded by input constraints. The transport admission policy below bounds JSON-RPC IDs; they are never truncated, substituted, or echoed in an oversized error reply. Discovery/schema payloads are outside the 64 KiB per-tool-call cap.

### 10.2.1 Mandatory stdio JSON-RPC ID admission policy

- Maximum ID size: **128 UTF-8 bytes of the serialized JSON scalar**, including string quotes/escapes. The exact calculation is `len(json.dumps(id, ensure_ascii=False).encode("utf-8"))`; numeric IDs are subject to the same byte limit. Examples: 126 ASCII characters fit; 127 do not. Thirty-one four-byte emoji plus two ASCII characters fit; 32 emoji do not. Escaped characters count their serialized escape bytes.
- Enforcement: `IDBoundedReadStream` wraps the official SDK's decoded receive stream in the stdio entrypoint, **before any request dispatch**, including initialize and protocol errors. It does not implement JSON-RPC parsing, framing, negotiation or dispatch; the official SDK still owns those.
- Accepted IDs roundtrip exactly. An ID over the limit causes the dedicated mock server process to write one constant diagnostic to stderr and terminate with exit code 2. It emits **no response** for that request, so neither a huge echoed ID nor an invented/truncated ID can defeat the 64 KiB output policy. There is no submitted value in the diagnostic.
- This intentionally aborts the stdio session and any outstanding read calls; in-memory cursors are lost. A protocol-error reply would have to echo the forbidden ID and is therefore not used. Immediate process termination also avoids waiting indefinitely for a blocked stdin worker during malicious-session shutdown. The process is the mock child itself; no external PID, helper, service or write operation is touched.
- Binder responsibility: generate small numeric IDs, or enforce the same 128-byte check **before sending**; treat EOF/exit 2 as a transport-policy failure rather than a tool result, and fail outstanding calls explicitly. Never retry a write automatically, mutate the ID to make it fit, or interpret absence of a response as success. A new connection starts a fresh mock session.
- For admitted IDs, each tools/call response frame remains bounded by 65,536 UTF-8 bytes, including SDK metadata, native content/structuredContent, exact ID and newline. Discovery schemas are a separate payload and remain outside this per-tool-result cap.
- Scope: this is an **ID admission/output-frame policy**, not a claim of a pre-parse raw-input byte/memory limit. The SDK has already parsed the inbound message when the guard runs; raw input allocation/whitespace floods remain SDK/deployment concerns. HTTP/in-process adapters do not inherit the stdio guard automatically and are outside this mock entrypoint contract.

Regression evidence: official SDK stdio tests cover accepted/rejected ASCII, multibyte Unicode, JSON escape and integer boundaries, a 70,000-character ID, exact ID roundtrips, EOF/no response for rejected IDs, constant stderr, and near-cap output with the maximum admitted ID. The existing 12-tool schemas and SDK discovery are unchanged.

### 10.3 Timestamp and evidence policy

All emitted timestamps use RFC3339 UTC with `Z`. `started_at` and `completed_at` are collection/execution times, not event times. `event_at` is a semantic concept, represented by an event record's `timestamp`; the common evidence contract retains `observed_at` as that underlying event timestamp. No new common-envelope field is introduced. For a synthetic service-status snapshot, the observation is made at snapshot creation, so observed_at may match collection time. Its source is explicitly `synthetic.mock.service_status`, and its excerpt matches the returned active/sub/load states.

Anomaly findings use a fixed synthetic historical fixture marker, `2026-11-02T00:00:00Z`, only when it lies within `[since, until)`. This is an aggregate fixture marker, not a claim of real Linux events. Sources start with `synthetic.mock.`; evidence IDs are generated uniquely. Missing event timestamps produce no invented evidence, insufficient_signals, and a partial result. Evidence references resolve to returned evidence; supplied verification baselines remain external references. Other dated mock event records are filtered by their actual timestamps. Input schemas that allow timezone offsets accept them and compare instants; anomaly/verify inputs explicitly require Z and reject offsets.

### 10.4 Cursors and resource boundaries

Business cursors are opaque random tokens, bound to tool, application session_id, normalized UTC time filters, other filters/defaults, offset and a 300-second TTL. Cursors are in-memory and process-local; another stdio server process cannot use them. Expired, modified, cross-tool, cross-session and changed-filter cursors reject, never reset. Business cursors are distinct from tools/list discovery cursors (this fixed registry has no discovery pages).

The synthetic datasets contain three pageable records; pages terminate. Offset budget is 200 and live cursor-state budget 256; expired states are pruned on creation. Cursors are bearer-like application correlation tokens, not user authentication; production session ownership and cumulative Agent budgets still require binder/deployment policy. Read timeouts/cancellation/disconnect cleanup are exercised with SDK child processes. The synchronous deterministic mock has no real collector process, no production collector deadline enforcement, and no Linux permissions/health checks.

### 10.5 Reproducible examples and handoff

`tests/mcp_smoke/test_all_tools_stdio.py::REQUESTS` supplies all 12 valid wire examples and asserts discovery equality, native structuredContent, status, IDs, data and envelope schemas. `tests/test_contract_audit.py` adds required-only/null/wrong-type/missing/unknown-field examples, output corruption, cursor, timestamps, secrets, actual wire cap and SDK lifecycle faults. The fault launcher is test-only and is never used by the production entrypoint.

Dependencies: Python 3.12; mcp==2.3.0; jsonschema==4.26.0; referencing==0.37.0; pytest==9.2.0. Exact direct pins are in requirements.txt; this is not a full transitive dependency lock. No SDK baseline was changed.

Handoff to Hiền (SCRUM-22): bind from manifest, validate the complete structured envelope, use native isError, preserve client metadata and partial/unknown states, resolve evidence refs, keep LLM away from entrypoint/config and approval control. Please review anomaly/window semantics, unknown verification results and non-resumable output-limit partials.

Handoff to Trang: please confirm production unit/source/profile allowlists, authentication/session ownership, permission-denied handling, and external coordinator/helper contracts. The mock proposal/status response remains deterministic and does not persist or execute actions; no real approval, root/sudo, restart or write retry exists.

Review checklist and signoff for this revision are tracked in `SCRUM-12-contract-review-2026-11-14.md`; `SCRUM-12-followup-2026-11-14.md` remains the historical fix report. Technical finalization is a candidate until Hiền and Trang explicitly sign off on this revision; neither a previous audit nor an automated PASS is an APPROVED review. The 13/11 handoff is historical. No merge or Jira change is authorized by this follow-up.

## 11. Validation Checklist

- [x] All 12 canonical input/data/full-output schemas exist and pass Draft 2020-12 syntax checks.
- [x] Discovery publishes the canonical inputSchema and complete-envelope outputSchema.
- [x] Required-only examples for all 12 tools pass the real SDK stdio path.
- [x] Missing fields, unknown fields, approved=true, wrong versions, wrong types and disallowed null reject.
- [x] Runtime validates input and output independently; deliberate output corruption fails closed.
- [x] Normal, error, partial and approval envelopes validate; native content/isError agree.
- [x] Service-status synthetic provenance and historical anomaly windows are tested.
- [x] UTF-8 wire cap, whole-record partial and bounded error are tested on actual SDK stdio frames.
- [x] Positive pagination, binding, expiry, modification, budgets and cross-process rejection are tested.
- [x] Synthetic secret sentinels do not appear in captured application error content/stdout/stderr, including reversed-window and invalid-cursor semantic rejections.
- [x] Stdio JSON-RPC IDs have an enforced 128-byte serialized-UTF-8 admission limit; rejected IDs close the session without echo/truncation.
- [x] Maximum admitted IDs and near-cap results are verified through the official SDK stdio transport.
- [x] SDK timeout, cancellation, disconnect and child-process cleanup are tested.
- [x] restart_service remains a mock facade with executed=false.
- [ ] Hiền confirms anomaly/verify/binder semantics.
- [ ] Trang confirms Linux/approval/security deployment contracts.
- [x] Hiền reported an independent audit of 6d581f2 with 140 pytest PASS and service_status ToolBridge compatibility; three follow-up items remained (user-provided review, 14/11).
- [x] Hiền reported rechecking runtime commit 917d9f0: 41 follow-up tests PASS, privacy/cap fixed and no regression (user-provided result, 14/11; not rerun locally).
- [ ] Hiền explicitly confirms the final input/output/error/evidence/correlation mapping in the current review pack.
- [ ] Trang (@Qtrang2826) explicitly confirms security/privacy/transport and EOF/exit-2 policy in the current review pack.
- [ ] Team review confirms finalization and merge readiness.

Finalization candidate status remains review pending. Valid free-text metadata/reason/query may be echoed on successful calls; this mock has no general-purpose production secret redactor. The safe-error guarantee covers rejected input, not arbitrary secrets placed in otherwise accepted requests. Passing local tests does not constitute independent approval or production security verification.
