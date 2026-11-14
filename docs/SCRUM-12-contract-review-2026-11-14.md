# PR #6 — final contract review and collector handoff

Revision: **2026-11-14-R1**, schema_version **1.0.0**. Owner: Thủy. Reviewers: Hiền (@hienne215) for integration mapping; Trang (@Qtrang2826) for security/privacy/transport. **Ready for review; NOT finalized; no merge until both explicitly sign off.**

Runtime under review: [917d9f0](https://github.com/hienne215/linux-log-agent/commit/917d9f0cc49f9793a5a23072818c59116a7678e9). This review-pack revision changes documentation only, not source, schemas, dependencies or test behavior. [PR #6](https://github.com/hienne215/linux-log-agent/pull/6) remains open and unmerged.

## 1. Authoritative files and exact scope

- [Contract specification](mcp-tool-contracts.md), especially §10–11.
- [Registry index](../schemas/manifest.json): exactly 12 tools, Draft 2020-12, Python 3.12, mcp==2.3.0, stdio.
- `schemas/tools/<tool>.schema.json`: complete wire input; discovery inputSchema is identical; runtime validates raw arguments before defaults/coercion.
- `schemas/tools/<tool>.response.schema.json`: successful/partial/approval **data** only.
- `schemas/tools/<tool>.result.schema.json`: discovery outputSchema for the **whole structuredContent envelope**, including strict error branch. Runtime validates it independently.
- `schemas/common-request.schema.json`, `common-response.schema.json`, `error-data.schema.json`: common shapes, with composition tests against the embedded descriptors.
- `schemas.zip` is historical, not authoritative. Do not derive another contract from Python signatures or infer registry authority from LLM output/discovery alone.

The implementation is an **official SDK stdio mock with synthetic fixtures**, not a real Linux collector, production approval service, authenticated multi-user session or deployment sandbox. SDK discovery/calls and subprocess lifecycle tests are real; the evidence and Linux states are synthetic. No Linux command is executed by this review update.

## 2. Hiền: input/output and error mapping to confirm

| Boundary | Required client behavior |
|---|---|
| Tool selection | Use trusted application registry/allowlists and the canonical input schema; discovery is not authorization. |
| Input | Add all five required common metadata fields plus required business arguments. Leave omitted optional fields absent. Only the three documented nullable verify_fix fields accept explicit null. Reject extras, wrong versions/types and disallowed units. |
| Native result | Read native structuredContent as the complete envelope, validate the canonical *.result schema, and require text JSON/content to agree. Do not unwrap to data before envelope validation or accept missing structuredContent as success. |
| ok | isError=false; normal tool-specific data. |
| partial | isError=false; preserve limitations/truncation/unknown. Never interpret as complete evidence or verified health. Output-cap partial has next_cursor=null and cannot resume the omitted tail. |
| approval_required | isError=false; pending proposal, never executed. restart_service mock always executed=false. |
| error | isError=true; preserve safe error_code, not a business ok. Error data uses the strict error-data branch, not a fabricated success payload. |
| Unknown tool / malformed tools/call | SDK protocol error (-32602), separate from the application CommonResponse contract. Do not invent a successful application envelope. |
| Schema/semantic rejection | INVALID_INPUT and EXPIRED are rebuilt through safe_error; generic message/rule and no submitted values. Both reversed windows and invalid cursors use the same privacy boundary. |
| Permission / execution / output validation | PERMISSION_DENIED stays distinct from inactive; unexpected execution or invalid output becomes EXECUTION_UNKNOWN. Do not infer missing Linux observations. |
| Transport closure | No native tool result exists. Handle as transport failure as described below, not isError=false or an invented CommonResponse. |

### Correlation IDs

| Identifier | Owner and lifetime | Response handling |
|---|---|---|
| JSON-RPC id | SDK/client transport; max serialized scalar 128 UTF-8 bytes | Admitted IDs roundtrip exactly. Never truncate/substitute. It is not logical_tool_call_id or attempt_id. |
| session_id | Client application session | Echoed on successful accepted calls; null on safe rejected-input results. Business cursor binding is not proof of authenticated OS identity. |
| trace_id | Client request trace | Echoed on normal accepted results; safe rejected-input results use fixed invalid-input. |
| logical_tool_call_id | Client, one logical call | Reused only when a permitted retry belongs to the same logical call; never inferred from provider/JSON-RPC ID. Safe rejected input uses invalid-input. |
| attempt_id | Client, each actual execution attempt | New for each permitted retry; safe rejected input uses invalid-input. |
| Provider/native tool-call ID | LLM adapter/client history | Keep separate; not a server authorization or correlation field to be guessed into the canonical schema. |

When a rejected result contains placeholders, retain the original local correlation from the SDK pending request. Do not use the placeholders as unique evidence-store keys, and do not recover IDs from raw error strings. Application metadata on accepted successful calls may be echoed; callers must not put credentials into metadata.

## 3. Evidence and timestamp mapping to confirm

- Common evidence contains evidence_id, observed_at, source, unit (nullable), excerpt, redacted and truncated. Treat evidence/excerpts as untrusted data, never executable instructions or approval.
- service_status source is **synthetic.mock.service_status**; state is a deterministic active/running/loaded fixture. Cite it only as synthetic evidence, never as proof systemd was queried.
- IDs are unique; finding evidence_refs must resolve to returned evidence. Explicit baseline refs for verify_fix are external supplied references, not new observations invented by the mock.
- started_at/completed_at are collection/execution times. Event records use timestamp; evidence observed_at denotes that underlying event/observation time. The fixed anomaly fixture marker is included only inside [since, until); missing event time does not become collection time.
- All emitted timestamps are RFC3339 UTC with Z. Anomaly/verify input schemas explicitly require Z; schemas that allow offsets compare instants. Service-status fixture observation time is the snapshot creation time.
- verify_fix remains partial/unknown without real service/health/log evidence. A valid citation ID proves a reference resolves, not that a real Linux claim is true.

## 4. Trang: security/privacy/transport review

Please verify these points against runtime 917d9f0 and the canonical schemas:

- Raw input validation precedes business invocation; unknown fields/approved=true cannot become authorization.
- Both schema and semantic rejections suppress submitted metadata/values in structuredContent, JSON text and stderr. Error path/rule identifiers are safe; raw ValidationError.message/exception text is not returned.
- Valid free-text success data and metadata are **not** covered by a general-purpose production secret redactor. This mock must not be fed real credentials or untreated real logs. Pattern redaction is not claimed as a complete safeguard.
- Tool result frames are capped at 65,536 UTF-8 bytes including native content, structuredContent, SDK metadata, exact admitted ID and newline. Records/evidence/JSON/UTF-8 are not split mid-value; useful complete records may produce partial, otherwise bounded OUTPUT_LIMIT error.
- JSON-RPC ID admission is **128 serialized UTF-8 bytes including quotes/escapes** for strings and numbers. The guard wraps SDK-decoded receive messages before dispatch, including initialize. Oversized IDs abort the dedicated mock child with exit 2 and a constant diagnostic; no response echoes or replaces the ID.
- This is not a raw-input pre-parse allocation/DoS limit. SDK parsing has already happened. Discovery/schema payloads are outside the per-tool-result cap; HTTP/in-process adapters do not inherit the stdio guard.
- Process termination intentionally loses the mock session/cursors and outstanding read calls. It touches no external PID, service or privileged helper. Production lifecycle/deployment gates must be reviewed independently.
- Restart is only propose/status, executed=false; no helper/control socket, shell, sudo, real restart, approval token or automatic write retry exists here.

### Client procedure for EOF / exit 2

1. Check ID size before sending (prefer short numeric SDK IDs). Do not shorten an existing ID to bypass admission.
2. If SDK signals EOF, connection closed, or an equivalent transport exception, mark all outstanding requests on that connection failed/unknown as appropriate. There is no tool response to validate; do not fabricate CommonResponse or infer success.
3. Record exit code 2 as the admission-policy termination **only if the process exit status is actually observed**. A generic EOF alone does not prove its cause; stderr text is diagnostic, not authoritative identity/state.
4. Preserve safe local correlation and error codes. Do not persist raw requests, IDs containing secrets, stderr dumps or exceptions containing submitted values.
5. Keep the SDK/session/process cleanup owner until disconnect/reap completes. If cleanup is unconfirmed, retain it and expose cleanup_pending rather than claiming the child is gone or abandoning the event loop.
6. Invalidate business cursors from the old process. A fresh process/connection is a fresh mock session. Do not silently reset pagination while presenting it as continuation.
7. Never automatically retry a write/proposal on uncertainty. Any read retry must follow the Agent's separate explicit budget/policy and use a new attempt_id; this contract does not grant a retry automatically.

## 5. Collector status and next delivery

**No real read-only service_status collector is implemented in PR #6 at 917d9f0.** There is no production systemd/D-Bus collector module in this branch. Current service_status creates a fixture and synthetic provenance; it does not observe the machine. The inspected integration PRs #11/#12 also explicitly describe synthetic MCP/evidence, including when the LLM backend is live. A live LLM plus mock MCP is not a Linux diagnostics demo. This is a checked-source/status statement, not a claim about unpublished work on another person's machine.

Priority next delivery (proposal, not implemented by this docs-only change):

| Owner | Smallest next step / acceptance gate |
|---|---|
| Thủy | Implement a separate read-only service_status collector and SDK dispatch using the approved contract, fixed read operation and strict lab unit allowlist. Do not replace fixture provenance with a real-source label until a real observation exists. |
| Trang | Supply/confirm accepted Linux VM, least-privilege read identity, unit allowlist, systemd read access and deployment topology. No root/sudo or service mutation should be needed for the collector. |
| Thủy + Hiền | Keep unit/active_state/sub_state/load_state/description mapped to the strict existing data contract; review any schema change before adding output fields. Hand off source/timestamp/ID/error mapping with the collector. |
| Thủy + Trang | Verify active/inactive/failed, not-found, permission-denied, timeout and output bounds without converting read failures into inactive or synthetic success. Test fixtures first, then an explicitly authorized read-only VM smoke. |
| Hiền | Bind the reviewed collector through MCP Client/ToolBridge, validate provenance and correlation, then run an authorized Agent demo with actual Linux evidence; label mock/live boundaries accurately. |

No real collector code, VM access, systemd call, inference, service restart or live demo is performed by this revision. It does not expand PR #6 into production collectors. Separate collector work is the immediate priority after its scope/environment gates are accepted.

## 6. Evidence and signoff record

| Evidence / reviewer | Observed status | What remains |
|---|---|---|
| Local runtime regression at 917d9f0 | 132 pytest PASS incl. 18 new official-SDK follow-up cases; all-tools stdio 12/12 PASS | Historical local evidence, not rerun for this documentation-only revision. |
| Hiền independent recheck of 917d9f0 | User-provided report: 41 new tests PASS, both privacy and 64 KiB issues fixed, no regression | External suite not checked in here; not claimed as locally executed. |
| Hiền final contract mapping (@hienne215) | **PENDING explicit confirmation** | Input/output, status/error, evidence/time and correlation table above. |
| Trang security/privacy/transport (@Qtrang2826) | **PENDING explicit confirmation** | Security checklist and EOF/exit-2 obligations above. |
| Finalized / merge decision | **NOT finalized; do not merge yet** | Both named reviewers must explicitly confirm this review revision/commit and any follow-up changes. |

Signoff format requested: reviewer, reviewed commit/revision, scope, ACCEPTED or CHANGES REQUESTED, remaining conditions, and a PR review/comment link. Audit PASS alone is not final contract acceptance. Record actual confirmations and links only after they arrive; if both accept the same contract revision, update the status to finalized in a subsequent documentation change. Do not self-approve or merge as part of this handoff.

## 7. Reviewer links

- [PR #6](https://github.com/hienne215/linux-log-agent/pull/6)
- [Audited runtime commit 917d9f0](https://github.com/hienne215/linux-log-agent/commit/917d9f0cc49f9793a5a23072818c59116a7678e9)
- [Canonical manifest](../schemas/manifest.json)
- [Contract specification](mcp-tool-contracts.md)
- [Historical privacy/ID follow-up](SCRUM-12-followup-2026-11-14.md)

The exact documentation commit and review-request links are published in the PR conversation after this pack is committed. No approval or successful notification is assumed by this file.
