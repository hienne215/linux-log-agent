# PR #6 — Trang review response R2

Revision **2026-11-15-R2**, schema_version **1.0.0** (pre-finalization revision).
**Pending independent Hiền/Trang review; NOT finalized; do not merge.**

Hiền accepted **R1 only**, documentation `d442479` against runtime `917d9f0`:
[integration acceptance](https://github.com/hienne215/linux-log-agent/pull/6#issuecomment-6888584230).
Trang's [review](https://github.com/hienne215/linux-log-agent/pull/6#pullrequestreview-5909686625)
has GitHub state APPROVED but explicitly requests fixes before confirmation.
It is not an unconditional final security signoff. R2 changes schemas/runtime;
Hiền must reconfirm affected mapping, Trang must confirm security/privacy/transport.
R1 approval is preserved as historical evidence, not applied to R2 automatically.

## Review disposition

| Item | Resolution / evidence |
|---|---|
| 1, 7 — restart lifecycle and mode inputs | Canonical `action_id` on propose and status; `expires_at` required. Propose requires unit, reason, 1–32 unique evidence IDs and forbids action_id. Status requires only common metadata, mode and action_id; legacy bounded unit/reason/evidence may be supplied but are ignored, never used to dispatch. Reason <=512; action/evidence IDs <=128. Proposal storage bounded to 256, 300-second retention, session-bound lookup, NOT_FOUND on expired/unknown/foreign actions. |
| 2 — cursor | Existing query_journal, search_log and list_failed_units already support cursor. Add cursor to memory_oom_events and failed_logins with bound session/filter/limit, expiration and whole records. Other tools are atomic and never issue next_cursor; detect_anomaly uses max_findings and reports limits without a resumable cursor. |
| 3 — coverage | Both log tools include coverage entries: source, status (ok/permission_denied/unavailable), oldest_available_at (UTC timestamp or null when unknown). Empty readable source is ok; any missing source with at least one readable source is partial, even with zero events. All denied => error/PERMISSION_DENIED; no readable source with unavailable sources => error/UNSUPPORTED. Errors retain safe coverage in error data. Never infer permission from journalctl exit 0 or absence of events. |
| 4 — command_source/unit | ADR-R2 below deliberately retains per-evidence provenance; no new top-level command_source/unit. |
| 5 — OOM | process and pid nullable; optional nullable nonnegative anon_rss_kb/total_vm_kb. Journal absolute UTC event time is preferred. Raw dmesg monotonic offsets cannot be presented as wall-clock timestamps: a future collector needs trusted boot-time conversion, explicit inferred provenance and clock/boot limitations; otherwise omit the event and report incomplete coverage. No such real parser is implemented here. |
| 6 — login privacy | source_ip nullable, service sshd/sudo/login/su; user <=64 and fully redacted to [REDACTED]. IP values on the Agent boundary must be opaque pseudonyms, never raw IP; synthetic mock uses ip:synthetic-peer-1. Future collector must implement session-scoped keyed pseudonymization and sanitize free-text before emitting anything; this mock is not a production redactor. Do not truncate and echo a potentially mistyped password as username. |
| 8 — limits | Every schema property named message/excerpt <=2048 Unicode code points. Serialized tools/call frame remains <=65,536 UTF-8 bytes, including admitted RPC ID and SDK overhead. Server requires 0 < until-since <=24 hours. For optional verify_fix until=null/omitted, effective end=min(now, since+24h); supply an explicit until for reproducible checks. Requests with offsets accepted by existing query/search/event schemas are compared/normalized in UTC; detect_anomaly/verify_fix retain Z-only inputs. All output timestamps UTC. |
| 9 — envelope | truncated=true requires partial. Whole-result OUTPUT_LIMIT errors use truncated=false, no success data/evidence/cursor; partial record reduction uses truncated=true, OUTPUT_LIMIT and no cursor. Read tools cannot return approval_required. Coverage partial can have truncated=false. |
| 10 — date-time | Runtime and tests use FormatChecker; rfc3339-validator==0.1.4 is now an explicit required dependency/import so unavailable format checking fails at startup rather than silently accepting invalid dates. Tests cover invalid date/hour and cross-field windows. |
| 11 — generation | python generate_result_schemas.py regenerates all 12 self-contained result schemas; --check detects drift. Common envelope/error schema and tool data schemas are authoritative; per-tool status restriction and tool const are deliberate variations. |
| 12 — walkthroughs | See fixtures below, tests/test_trang_r2.py, tests/mcp_smoke/adversarial_server.py and docs/examples/review-r2.json. Test-only coverage fixtures are never public tool parameters or production switches. |

## ADR-R2 — provenance remains per evidence

Decision: `evidence[].source` is the canonical equivalent of the architecture's
`command_source`; `evidence[].unit` identifies the relevant unit, nullable for
non-unit observations. A result may combine several sources/units, so a single
top-level source/unit would lose information or contradict individual evidence.
`data.coverage[].source` describes attempted log sources even with no events.
No redundant top-level command_source/unit is added. Consumers must preserve the
complete evidence entries and coverage rather than flattening to one source.
This clarifies ARCHITECTURE §4.1; acceptance of this clarification is pending review.

## Restart walkthrough fixtures and limitations

The public tool exposes only propose/status, never approve/execute. Proposed
actions get unpredictable IDs and cannot be read from another supplied session.
Session IDs remain application identifiers, not authenticated OS identities.
Reason is returned as [REDACTED]; neither a reason nor evidence is execution authority.

For status-only synthetic walkthroughs use `mock-fixture-<state>` with any state:
pending_approval, denied, expired, approved, executing, succeeded, failed,
execution_unknown. These public fixed IDs are explicitly synthetic, separate
from session-owned proposals; no state transition endpoint exists. All status
lookups return envelope ok/isError=false: the lookup succeeded; proposal_status
contains the outcome. A denied action is therefore not a tool execution error.
`executed` is boolean: synthetic succeeded/failed fixtures demonstrate confirmed
dispatch=true; all others false, including execution_unknown (not confirmed).
False in execution_unknown does NOT prove no execution: clients must preserve
unknown and never retry automatically. No fixture represents a real helper result.
Production authorization, durable state/expiry/ownership, helper dispatch and audit
are unimplemented and outside PR #6.

## Transport and binder obligations retained

The serialized JSON-RPC scalar ID cap remains 128 UTF-8 bytes. IDs roundtrip
unchanged; oversized IDs cause no tool response, constant stderr diagnostic and
process exit 2. This guard follows SDK decoding, not a preparse DoS/memory cap.
Discovery remains outside the tools/call 64-KiB cap. Do not truncate IDs.
EOF/disconnect means no authoritative result: fail pending calls, retain unknown
outcomes and local correlation, invalidate cursors, retain cleanup ownership.
Only report exit 2 if observed. No automatic retry or reconnect.

Agent binder must retain Hiền's application-owned `service_status/nginx.service`
allowlist and complete evidence provenance. R2 grants no broader tool authority.
Real Linux collector, VM/topology, production binder/security deployment and Linux
smoke are still outside this review. PR #14 scaffold is separate and must consume
R2 only after its own integration checks; no scaffold files are included here.

## Reproduce

```text
python -m pip install -r requirements.txt
python generate_result_schemas.py --check
python -m pytest -q
python tests/mcp_smoke/test_all_tools_stdio.py
```

Evidence and exact final commit are recorded in the local delivery report after
validation. Tests are mock/SDK transport tests, not Linux permission smoke.
