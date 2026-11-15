# PR #6 — R3 response to Trang's second review

Revision **2026-11-15-R3**, schema_version 1.0.0 (pre-finalization).
**NOT finalized; no merge.** This revision supersedes conflicting R2 details.
Review: https://github.com/hienne215/linux-log-agent/pull/6#pullrequestreview-5909870121

| Item | R3 contract and implementation |
|---|---|
| 1. Accepted-request correlation | A request that passes schema AND semantic validation keeps session_id, trace_id, logical_tool_call_id and attempt_id when source access fails. Error data contains coverage and a safe message such as `journal.auth: permission denied` / `auth.log: source unavailable`; no field/rule. Source names come from a fixed enum, never raw stderr or request text. Invalid window/cursor/schema still uses safe-error placeholders without reflecting input. Cursor validation happens before coverage failure handling. |
| 2. execution_unknown | Status executed is boolean/null; execution_unknown requires null and other states require boolean in the output schema. Unknown means dispatch/outcome cannot be established. **Never retry a write automatically, even when executed is null**; retain unknown and seek authoritative reconciliation. The tool has no approve/execute API. |
| 3. Source enums | OOM: journal.kernel / dmesg. Login: journal.auth / auth.log. Tool data and whole-result schemas constrain these enums, including error coverage. Runtime, test fixtures and all published examples use the same names; synthetic nature is stated in messages/docs rather than inventing source names. |
| 4. TTL | Proposal TTL is **60 seconds**, matching ARCHITECTURE §6. Cursor TTL remains independently 300 seconds. Stored proposal expiry is fixed at creation and not extended by status reads; expired/unknown stored proposals return NOT_FOUND. Public expired fixture illustrates lifecycle status expired. |
| 5. Evidence | Every returned OOM/login event has evidence_id referencing exactly one returned evidence record, with the same UTC event time, known source, unit=null, filtered synthetic excerpt, redacted=true and truncated=false. Pagination returns evidence only for events on that page; empty events return empty evidence. No real log parser/redaction is claimed. |
| 6. Unit | Both restart response variants require canonical unit. Proposed unit is already canonical by strict request pattern, stored with the proposal and returned on status; caller-supplied legacy status unit is ignored and cannot override it. Public status fixtures use nginx.service. A real coordinator's authoritative unit lookup remains unimplemented. |
| 7. Retention | oldest_available_at is the oldest retained readable timestamp for that source, not the first matching event in the query. If later than since, or unknown/null for a readable source, coverage cannot establish full-window absence: status=partial, even with zero events and truncated=false. Synthetic journal events before the retained timestamp are not returned. Missing source access also yields partial when some source is readable; no readable sources yields the existing safe permission/unavailable error. |
| 8. Fixture time | Status fixtures generate expires_at relative to each call: +60 seconds, except expired which uses -60 seconds. Pending/approved/executing fixtures are not already expired relative to response started_at. Published JSON is a snapshot; compare times to that response's started_at, not today's clock. Actual proposal status uses the original stored deadline. |
| 9. Untrusted reason | Intentional full redaction: reason is model-generated/untrusted and may contain secrets. Propose returns [REDACTED], not the raw or partially sliced reason. CLI may label it untrusted/redacted; do not use it as authorization or an authoritative action label. Input remains bounded to 512 characters. |
| 10. Login identity | Keep user fully [REDACTED]. Add required user_class=root/existing/unknown to support coarse analysis without exposing a mistyped password. Synthetic examples illustrate root and unknown. Future collectors must classify against trusted lab account policy and default unknown; no account lookup has been implemented here. |

## Evidence and validation

- Current examples: `docs/examples/review-r2.json` (filename retained for existing review links; contents regenerated for R3). Includes 19 input/output cases with linked evidence, tool-specific sources, accepted error IDs and relative fixture deadlines.
- `tests/test_trang_r3.py`: accepted error correlation, source enums on success/error, evidence linking, retention gap, 60-second expiry, authoritative mock unit, unknown/null semantics, expiry cleanup and official SDK stdio.
- `tests/test_trang_r2.py`: earlier invalid-input privacy, pagination, schema generation and transport regressions remain enabled.
- Full local tests, official SDK 12-tool smoke, result schema generation check and diff check must pass before handoff.

## Signoff scope and unchanged obligations

Hiền accepted **Integration R1 only**, at d442479 against 917d9f0; R3 needs confirmation of changed mapping. Trang accepted several R2 design points but requested changes; her stated prospective confirmation covers security/privacy of these **three tools only**. It does not establish review of the other nine tools, complete transport, deployment or Linux smoke. Do not treat a partial-scope signoff as full finalization.

128-byte serialized JSON-RPC ID admission, 65,536-byte tools/call frame cap, safe invalid-input errors and EOF/observed-exit-2 cleanup obligations remain. Do not truncate/substitute IDs, auto-retry or auto-reconnect. Binder retains application-owned service_status/nginx.service allowlist and full evidence provenance. Real Linux collectors, production coordinator/binder/permissions and VM smoke are outside this mock PR.
