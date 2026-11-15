# SCRUM-39 / P04-03 — Week 2 gate, offline evidence

**Overall: BLOCKED for formal W2 acceptance.** Offline orchestration support is
PASS/PARTIAL as scoped below. This report does not mark SCRUM-39 Done.

## Acceptance source (no Jira access)

Read the preserved local planning files, not rewritten acceptance criteria:

- outputs/JIRA_ISSUE_MAPPING.json: P04-03 -> SCRUM-39.
- docs/TASKS.md section P04-03 (lines 523–531): run S1/S2/S4 through CLI, review
  evidence, confirm 11 R and week-3 backlog. Acceptance: all three terminate with
  evidence/uncertainty, all 11 R available, no real W. Method: VM smoke with one
  fixed model and manual trace review.
- docs/ROADMAP.md evaluation: S1 crash loop; S2 Nginx syntax; S4 backend down/502.
  Port conflict is S3; active and permission-denied are not substitute S1/S2/S4 cases.

Those planning artifacts are untracked historical files outside this branch; they
were only read, not copied/committed or claimed present on main. Source SHA-256:

| Artifact | SHA-256 |
|---|---|
| TASKS.md | 093d0cda4c2b9b9008af52bfe93fc5506077be967fdabb9608675b4e18af9de4 |
| ROADMAP.md | 716603ccb6c546b6a55a40eecf4230a09f42edbd2aef04ccaa50ab5a91135d7e |
| JIRA_ISSUE_MAPPING.json | 9a334432381d618f3969c6c8e29c577277e6ea82b49d5ee91ae79212822e2aed |

## Fresh offline evidence

Base PR #12: `440c2004a7742f75550765b9db339734740d80f3`. Python 3.12.16 / MCP 2.3.0.
Four new probes use existing MockHTTP/worker, Coordinator, ToolBridge and **actual
official SDK synthetic stdio**, then renderer. The mock responses are scripted;
this validates multi-turn orchestration, not live model inference or reasoning.

[OBSERVATIONS.json](../tests/week2_gate/OBSERVATIONS.json) contains actual observed
traces/counts, provenance IDs, cleanup checks and child return codes, plus source
revision and test-source hashes. No experimental semantic correctness is inferred.

| Probe | Status | Mock turns / dispatches | Evidence / cleanup |
|---|---|---|---|
| active | concluded | 2 / 1 | active-service_status; all owners cleared, worker/server exit 0 |
| port-conflict | concluded | 3 / 2 | service_status + listening_ports; citations resolve; owners cleared, exits 0 |
| permission-denied | limited | 3 / 2 | received denial marker + service evidence; uncertainty retained; cleanup complete |
| port-conflict, cap 1 | budget_exhausted | 2 / 1 | first evidence preserved; no second dispatch or conclusion; cleanup complete |

Exact success traces: RECEIVED -> (ANALYZING -> COLLECTING) repeated once/twice
-> ANALYZING -> CONCLUDED -> REPORTED. The capped probe ends BUDGET_EXHAUSTED ->
REPORTED after the second analysis. All conclusion_verified values remain false.
A citation resolving proves membership, not semantic truth or Linux verification.

## Gate matrix

| Criterion | Status | Evidence / gap |
|---|---|---|
| Three current synthetic scenarios terminate with evidence/uncertainty | PASS (offline support only) | Four fresh probes; permission denial remains limited; existing CLI report equality tests |
| Multi-turn state machine, budgets, safe evidence/citations | PASS (offline) | Exact traces + tool cap probe; existing turn-cap/forged-citation regressions |
| Error/partial, timeout, cancellation, cleanup ownership | PASS (offline) | Fresh full regression includes failure/late-response/cleanup-pending tests listed below; new probes assert all owners/handles settled |
| Actual required S1/S2/S4 through CLI with evidence/uncertainty | BLOCKED | Current fixtures do not implement these three labs; no approved VM/ground truth/collector acceptance |
| All 11 R available in target runtime | BLOCKED | This scoped fixture uses only service_status/listening_ports/query_journal; no 11-R runtime handoff/acceptance established; mocks/bindings are not proof |
| No real write action in this run | PASS (scoped observation) | Only mock backend + synthetic read-only server; existing write-policy regression checks no dispatch. Not production approval/helper acceptance |
| One fixed real model + VM smoke | BLOCKED | No new inference/Linux run authorized; historical active-only smoke is synthetic MCP, not S1/S2/S4 VM coverage |
| Three traces and W2 gate deliverable | PARTIAL | Offline fixture traces present; required VM traces and independent manual review absent |
| Week-3 backlog/reviewer confirmation | PARTIAL | Owner checklist proposed below; team signoff not manufactured |

## Existing evidence inventory / regression mapping

New coverage is deliberately limited to exact state transitions across the three
existing fixtures and an explicit integrated post-evidence tool-count cap check.
Existing code/tests were reused, not duplicated or extended into a new loop:

- tests/integration_demo/test_demo.py: active/port-conflict/permission-limited;
  invalid tool/args/write/malformed response -> no dispatch; timeout/disconnect
  preserve unknown execution; cancellation/late model results do not dispatch;
  model and executor cleanup_pending retain owners and can rejoin; turn budget
  preserves evidence; CLI stdout/report equality and report permissions/path safety.
- tests/integration_demo/test_first_tool_policy.py: native first-tool policy,
  text-only/invalid conclusion/citations, exposure/allowlist alignment; all offline.
- tests/integration_boundary: schema/read-only enforcement, bounded result
  preservation and cleanup ownership (including prior R1/R2 regressions).
- tests/async_llm + tests/mcp_client: worker/request ownership, timeout/cancellation,
  late responses and SDK lifecycle; optional stdio runs synthetic servers only.
- tests/agent_loop, tests/agent_acceptance, tests/cli: fixture consistency,
  Coordinator/Incident Report and original synthetic CLI safeguards.
- docs/TRUSTED_BACKEND.md on #12 records **historical** live smoke v2 on 14/11:
  active only, two real model requests + one synthetic dispatch, 28.188s, cleanup
  and report success. Not rerun here, not a benchmark or proof of Linux accuracy.
- #15/#16/#17/#18 are independent anomaly/verify/UI/evaluator work and are not
  included in this base's fresh regression count. No claim of revalidation here.

## New execution totals

Fresh targeted: **4 PASS**. Fresh regression: **352 PASS** (57 Adapter, 46 Client,
50 Boundary, 4 acceptance fixtures, 60 Coordinator/report, 35 CLI, 33 async Adapter,
67 integration/backend/policy tests). **356 PASS, 0 FAIL, 0 SKIP** total, one execution
of each suite; no old overlapping totals added. These are test counts, **not an
accuracy numerator/denominator**. Full unittest stderr remains outside Git; no
private trace dump. All four recorded probe model workers and observed synthetic
MCP subprocesses returned 0; no pending owners remained at assertion time.

No new live Ollama, real Linux, production MCP, fault injection, benchmark, sudo,
restart, contract/permission change, Jira or old PR/worktree modification.

## Minimum remaining handoff / proposed week-3 backlog

- **Thủy:** reviewed canonical registry/contracts plus executable read-only collectors,
  launch topology and evidence/error semantics; independent proof all 11 R work in
  the agreed target runtime. Do not derive that from synthetic discovery.
- **Trang:** accepted VM/snapshot/restore/least-privilege environment and S1/S2/S4
  lab fixtures with frozen ground truth, safe runbook and expected evidence.
- **Hiền:** bind only after handoff; run approved fixed-model CLI/VM smoke for the
  required cases, retain trace/report/correlation, review uncertainty and citations
  against ground truth. No production binder or live runs implemented in this task.
- **Thủy + Trang review:** manually inspect traces/limitations and confirm remaining
  week-3 work. Approval/helper/security remain separate gates; no signoff invented.

Offline support can be reviewed as Draft. Formal gate is **BLOCKED**, not PASS.
