# SCRUM-24 — Minimal read-only Agent loop (design candidate)

Status: **DESIGN ONLY**, 2026-11-10. Not implemented, not approved as SCRUM-12 contract, not a completion claim.

## Scope and boundary

- Start with CLI question, explicit Linux unit/time scope and trusted policy. App creates session/trace identity; LLM never chooses privilege or correlation IDs.
- Give the LLM only the fixed read-only registry, sanitized evidence and bounded history; validate structured proposals at the ToolBridge and MCP server separately.
- State transitions are owned by application code: `RECEIVED → COLLECTING ↔ ANALYZING → CONCLUDED → REPORTED`, plus `CANCELLED`, `BUDGET_EXHAUSTED` and error branches. No automatic write action.
- After a result, retain safe evidence/provenance (including an already-sanitized result on budget expiry); distinguish no-dispatch, dispatched-unknown-outcome, and received-result outcomes.
- Do not inject journal text as instructions; prompt injection from logs cannot authorize a command, a new tool, or privilege escalation.

## Algorithm sketch (not executable code)

1. Check remaining wall-time budget, max 8 tool calls, max 6 LLM turns. End with a limited report if depleted.
2. Ask LLM for a bounded structured choice: next read tool or final conclusion. Reject invalid output and allow at most two structured repair attempts within budget.
3. Validate tool name, schema, arguments, read-only policy and exact scope; reject repeated identical calls absent new evidence.
4. Dispatch via ToolBridge, at most one read retry for a transient error when safe and counted. Cancellation propagates; client cleanup has separate ownership/budget.
5. Append only bounded redacted evidence and error metadata. Correlate evidence refs before allowing citations; don't infer success from transport errors.
6. If evidence is sufficient, stop. Otherwise continue only if an additional read can answer a concrete gap. Report evidence-backed cause, alternatives, missing data and safety limits.

## Acceptance gates before implementation/integration

- SCRUM-12 supplies finalized common schemas, IDs ownership, `CallToolResult` mapping, cursor, provenance, timestamps, 64-KiB enforcement and negative contract tests; use those definitions verbatim, never duplicate draft fields here.
- Review current ToolBridge (`PR #7`) at its latest head and verify concurrency/cleanup, deadline and preserved redacted result under budget exhaustion.
- Add deterministic synthetic end-to-end tests for the three scenarios in `tests/agent_acceptance/` before any real Linux collection.
- Before VM demo: isolated runtime with Python 3.12, verified reader/journal permissions, safe memory limits, log redaction and explicit denial behavior. No live restart.

This design deliberately excludes SCRUM-12 schema implementation, production reasoning execution, approval/helper actions, Ollama inference, and live Linux tool invocation.
