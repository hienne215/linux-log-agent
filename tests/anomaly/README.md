# SCRUM-25 — Agent-side detect_anomaly (canonical mock only)

Stacked base: PR #8, `61cdff0d2674f14150a1e7df5940a1372b8c1d24` (Coordinator +
ToolBridge/Client from #7). No CLI, Async Adapter, reasoning loop or Linux collector
is added. SCRUM-12/R1 is **not finalized**: this is an independently testable mock
prototype, not production acceptance or SCRUM-25 Done.

`contracts/` contains byte-identical copies of only the three canonical
`detect_anomaly` schemas from PR #6 commit
`d442479493d94c54a2d35698d3f920e7758b7744` (runtime `917d9f0`). They are test
snapshots, not a replacement registry or a second contract maintained by Hiền.
Production loading/version negotiation awaits finalized handoff. No Thủy source
is copied into this PR; the optional SDK test extracts its pinned mock into a
temporary directory and calls **only detect_anomaly**, never Linux commands.

## Boundary

`AnomalyBinding(client, input_schema, data_schema, result_schema, redact=...)`
is a Client facade **below** ToolBridge. Supply trusted canonical schemas and a
trusted redactor; never accept these, an endpoint or an executable from the LLM.

- `trusted_tool` is a detached read-only proposal schema. Only detect_anomaly,
  unit nginx.service, UTC since/until and canonical business fields are exposed.
  Metadata injection/extra arguments fail before dispatch. Local max_findings is
  1..5, application default 5; the canonical server schema itself is not modified.
- App creates schema_version/session_id/trace_id/logical_tool_call_id/attempt_id.
  Logical/attempt IDs are unique per call; no automatic retry or reconnect.
- Validate complete canonical input, full structuredContent result and non-error
  data, text/structured equality, isError/status and correlation. Placeholder
  safe-error IDs never replace local pending correlation. Error/partial/unknown
  are preserved, not converted into an empty successful analysis or inactive state.
- Validate event timestamps against the requested half-open window, evidence IDs,
  finding references and unique findings. Never synthesize anomaly observations.
- Keep a bounded evidence ledger with original IDs/source/observed_at/unit/flags.
  The Coordinator projection is id/source/text only; use `evidence`/`result` for
  full provenance in a later reporting integration. No canonical wire fields added.
- Excerpts/summaries may be locally redacted in the safe projection; immutable
  provenance changes fail closed. `local_redaction_applied` is internal and does
  not overwrite server redacted/truncated flags. Missing evidence, partial,
  insufficient signals and truncation produce limited observations.
- Interpret received findings only: this module does **not** implement an anomaly
  detector, Linux collectors, thresholds, invented observations or LLM inference.
  A valid citation proves an ID resolves, not the semantic truth of a conclusion.

## Ownership and errors

Caller owns connect/discovery and MUST compare discovery against pinned canonical
schemas, retain the MCP Client/Coordinator, and complete cleanup. Use
`StdioConfig.allowed_tools=frozenset({'detect_anomaly'})` and construct
`ToolBridge(binding, (binding.trusted_tool,), redact=..., budget=...)`; Coordinator
must use the same read-only allowlist and scope. The canonical SDK test demonstrates
this boundary with a single validated dispatch. No production launcher is supplied.

Binding forwards disconnect to the **existing** Client owner. ToolBridge handles
request cancellation/deadlines and cleanup joins; caller must explicitly join
Coordinator/Client on normal completion too. If cleanup is pending, retain all
owners and rejoin; do not close the event loop or claim an unconditional hard wall
SLA. This facade does not bypass same-task SDK/AnyIO lifecycle.

Timeout/EOF after dispatch leaves `execution_state='unknown'` and local correlation
intact; no fake CommonResponse is generated. A received response establishes
response_received (validation can still fail); EXECUTION_UNKNOWN remains a tool-level unknown. Generic EOF
cannot prove exit 2: the existing Client does not expose that exit status. Use short
SDK JSON-RPC IDs (PR6 admission <=128 serialized UTF-8 bytes). Local caps are after
SDK parsing, not a raw-input allocation/DoS guarantee. Pattern redaction is not a
production secret/privacy acceptance; use reviewed synthetic data only.

## Reproduce (Python 3.12, MCP 2.3.0)

Use the existing pinned dependencies from tests/mcp_client/requirements.txt and
 tests/integration_boundary/requirements.txt, plus the normal unittest stdlib.
No package installation is performed by these commands.

```bash
PYTHONDONTWRITEBYTECODE=1 python -B tests/anomaly/run_tests.py
# Only if the pinned PR6 Git object is already present:
PYTHONDONTWRITEBYTECODE=1 python -B tests/anomaly/run_tests.py --with-sdk
RUN_MCP_STDIO_SMOKE=1 PYTHONDONTWRITEBYTECODE=1 python -B tests/integration_boundary/run_composed_tests.py --baseline-ref 990f41f68c8ef13218bf825bd57ff233a8c82f1c
PYTHONDONTWRITEBYTECODE=1 python -B tests/agent_loop/run_tests.py
```

A fresh clone may first need an explicitly authorized fetch of PR6; the runner
never fetches/pulls automatically. The composed regression uses the existing
merged Adapter in a temporary tree, not code added to this branch.

Unit tests use clearly labelled server-side synthetic response fixtures. SDK smoke
uses Thủy's actual canonical mock with no fault injection into Linux. Production
collector/VM/topology/security acceptance, finalized contracts and real-data
redaction remain separate gates. Do not infer deployment readiness from PASS.

## Validation — 2026-11-15

Python 3.12.16 / MCP SDK 2.3.0: **232 PASS, 0 FAIL, 0 SKIP**.
This comprises 14 anomaly unit/mock tests + 1 canonical mock official-SDK stdio
test, 153 Adapter/Client/Boundary regressions (including real SDK synthetic
stdio), and 64 Coordinator/acceptance/report tests. No live Ollama or Linux
collector was run. Process handles observed by the SDK test were reaped.
