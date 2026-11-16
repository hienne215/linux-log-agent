# Linux Log AI Agent: Agent Technical Report

**SCRUM-44 / P04-08 — English report draft**

**Repository/status review: 16 November 2026; experimental evidence retains its original dates**

## Abstract

Linux Log AI Agent is a student project that aims to turn a diagnostic question
into a bounded sequence of read-only observations and an evidence-linked incident
report. The application validates proposed tools, maintains the execution state
and controls resource cleanup. A language model assists with tool selection and
interpretation; it does not receive operating-system privileges or approval authority.

The current evidence demonstrates a synthetic integration pipeline and one successful
Live Ollama + Synthetic MCP active-service smoke test. It does not establish real
Linux diagnostic accuracy, successful remediation, VM acceptance or production
readiness. This document brings together implementation and existing evidence;
**no tests, inference, benchmarks or experiments were rerun for this writing task**.

## Executive Summary

The Agent foundation is implemented across the main branch and pending Draft PRs,
as distinguished below. The historical live v2 smoke completed **two model requests,
one synthetic MCP dispatch and an incident report in 28.188 seconds**, for the
active scenario only. It does not demonstrate real Linux diagnostics.

SCRUM-39 records **356 offline tests PASS, 0 FAIL/SKIP**, while **formal Week 2
acceptance remains BLOCKED**. The missing evidence comprises S1/S2/S4 runs on the
accepted VM using one fixed model, frozen ground truth, all 11 read-only tools
available in the target runtime, and independent manual trace review. Offline
orchestration tests establish controlled behavior, not diagnostic correctness.

PR #6 currently publishes candidate contract revision **2026-11-15-R3** at
`fd737fbc1a6fe991e55af27b0554c342a818022a`; it is **not finalized**. Hiền's recorded
integration acceptance covers R1 only. R3 has not been established as accepted by
the Agent integration evidence reviewed here. Server-side test success and
partial-scope security review do not substitute for that acceptance.

### Implementation status at the reviewed repository snapshot

The table was checked against current GitHub head/base/state metadata and the
corresponding source trees on 16 November 2026. All Draft entries below remain
Open/Draft; their code is not present in `main@990f41f`. Implementation and formal
acceptance are separate states.

| Status | Component | Verified repository location | Acceptance limit |
|---|---|---|---|
| Implemented in main | Architecture and security design documents | [Main][main-tree], `990f41f`; [ARCHITECTURE][architecture], [SECURITY][security] | Design documentation, not deployed hardening or VM acceptance |
| Implemented in main | SCRUM-14 benchmark harness and archived results | Merge commit `ba9b2a5`; artifacts retained in [main][main-tree] | Local tool-selection experiment with mock definitions, not Linux accuracy |
| Implemented in main | Synchronous Ollama Adapter | Merge commit `990f41f` / [Adapter][adapter] | Validates proposals; performs no tool execution |
| Implemented in Draft PR | MCP Client and ToolBridge | [#7](https://github.com/hienne215/linux-log-agent/pull/7), `9c7fbc14` | Contract-independent transport/boundary, not a production binder |
| Implemented in Draft PR | Coordinator and Incident Report renderer | [#8](https://github.com/hienne215/linux-log-agent/pull/8), `61cdff0d` | Synthetic orchestration and reporting |
| Implemented in Draft PR | Synthetic diagnostic CLI | [#9](https://github.com/hienne215/linux-log-agent/pull/9), `ab05df01` | Three fixtures, not real diagnostics |
| Implemented in Draft PR | Async Ollama Adapter | [#10](https://github.com/hienne215/linux-log-agent/pull/10), `4cb5ab66` | Owned worker lifecycle; no provider cancellation guarantee |
| Implemented in Draft PR | Integration demo and trusted backend/first-tool policy | [#11](https://github.com/hienne215/linux-log-agent/pull/11), `a7d5e945`; [#12](https://github.com/hienne215/linux-log-agent/pull/12), `440c2004` | Mock pipeline plus one historical live-LLM/synthetic-MCP smoke |
| Implemented in Draft PR | Canonical mock anomaly/verification bindings | [#15](https://github.com/hienne215/linux-log-agent/pull/15), `2e1698eb`; [#16](https://github.com/hienne215/linux-log-agent/pull/16), `fbbc6c29` | Pinned R1 snapshots, not R3 integration acceptance or collectors |
| Implemented in Draft PR | Action confirmation UI | [#17](https://github.com/hienne215/linux-log-agent/pull/17), `264626cc` | Local UI decision only; no execution authority |
| Implemented in Draft PR | Metrics toolkit and offline gate evidence | [#18](https://github.com/hienne215/linux-log-agent/pull/18), `2ae801f`; [#19](https://github.com/hienne215/linux-log-agent/pull/19), `4ae61dd1` | Provisional metrics; formal gate remains BLOCKED |
| Planned / Blocked | Final contract adoption and production MCP binder | [PR #6 R3][contracts-r3], `fd737fb` | Mock contracts/runtime exist in an Open, non-Draft PR; finalization and Agent R3 revalidation remain pending |
| Planned / Blocked | Real collectors, accepted VM/topology, S1/S2/S4 evaluation and protected write plane | [Gate handoff][gate], [security design][security] | Not established by these inspected sources; no successful Linux diagnosis or remediation claimed |

## 1. Project overview and objectives

The intended workflow is: a user describes a problem, the Agent selects relevant
observations, examines the returned evidence and produces a report with citations
and uncertainty. The project targets a CLI, one selected LLM and 12 tool contracts:
11 read-only tools and one approval-gated restart facade. This is the planned scope;
availability of all 11 read-only collectors in the target runtime is unverified.

The evaluation plan identifies S1 crash loop, S2 Nginx configuration syntax error,
S3 port conflict, S4 backend failure causing HTTP 502 and S5 a nearly full test
filesystem. The planned baseline is five cases with three repetitions: 15 runs.
Optional approved extensions could increase this to six/seven cases and 18/21 runs.
These are evaluation requirements, not completed experiments. The Week 2 gate
specifically requires S1/S2/S4 and manual review. [The gate report][gate] preserves
the planning references and their hashes.

Implementation is distributed across branches. At the refreshed snapshot, PRs
#7–#12 and #15–#19 are Open/Draft. Main at `990f41f` contains the architecture/security
documents, benchmark artifacts and synchronous Adapter; it does not contain the
complete pending Agent stack.
This documentation PR is based on main and references the separate implementations
at immutable commits. Reading this report does not require merging their branches.

## 2. Agent architecture and trust boundaries

The [architecture][architecture] separates the diagnostic read plane from the
planned approval/helper write plane. In the implemented demo, the CLI wires the
Coordinator, model decision source, ToolBridge, MCP Client and synthetic server:

```mermaid
flowchart LR
    U[User question] --> C[CLI and application scope]
    C --> A[Agent Coordinator]
    A --> L[AsyncDecisionSource and Adapter]
    L --> P[Untrusted native tool proposal]
    P --> B[ToolBridge: schema, allowlist, budget]
    B --> M[MCP Client: official SDK stdio]
    M --> S[Synthetic MCP server]
    S --> E[Received evidence and provenance]
    E --> A
    A --> R[Incident Report renderer]
    R --> C
```

The [integration demo][demo] follows the pipeline shown above. Real collectors and the
privileged write plane are outside it. The model backend can be mock or explicitly
selected local Ollama; this choice does not turn synthetic server data into Linux
observations. [Implementation wiring][session] retains all component owners.

| Boundary | Application responsibility | Important limit |
|---|---|---|
| User/model to policy | Trusted configuration owns tools, unit scope, schemas and launch commands | User prose or model output cannot grant permission |
| Model to tool dispatch | Validate native call name/arguments, read-only policy and budgets | Discovery and annotations do not confer authority |
| MCP result to Agent/model | Bound, validate and redact data; preserve errors and evidence identity | Log text remains untrusted even with a valid schema |
| Agent to report | Resolve citations, retain uncertainty, escape output | Citation membership does not prove semantic correctness |
| Read plane to future write plane | Protected approval/control channels and helper policy | Not implemented or accepted by the synthetic UI |

The target deployment uses separate operator, read-plane and approval/helper roles.
Agent and stdio child server share a UID in the design: their protocol boundary is
not OS isolation. Those UID/service arrangements are deployment requirements,
not proof of an accepted VM configuration. [Security policy][security] requires
least privilege and a separate authorization path for writes.

## 3. Local LLM integration

The selected baseline is `qwen3:4b-instruct-2507-q4_K_M`. Trusted application
configuration uses `http://127.0.0.1:11434`, context 4096, output cap 256,
temperature 0, four threads and non-streaming requests; the Instruct path omits
`think`. Model/endpoint selection is not taken from the user's question.
The [synchronous Adapter][adapter] validates messages, native function calls,
arguments and response policy, and normalizes usage/latency and safe error codes.
It does not execute a proposed tool or convert JSON embedded in prose into a
native tool call.

The [async integration][async] runs synchronous HTTP in an owned subprocess so
that it does not block the event loop. It bounds IPC and concurrent ownership,
rejects late results after cancellation/deadline, and retains process cleanup
through terminate, kill if required, and join. Synchronous API compatibility is
preserved. Stopping this worker does **not** prove the Ollama backend has stopped
inference. Cancellation guarantees concern application state and owned resources,
not an unobserved provider-side operation.

[AsyncDecisionSource][decisions] returns either a validated tool proposal or a
conclusion with existing evidence IDs. Before any evidence exists, the first-tool
policy requests a native call and enables local `require_tool` validation. Text-only
output fails closed without retry or fabricated evidence. This is not a claim that
Ollama always produces native calls, nor an unsupported `tool_choice` guarantee.

The general Coordinator limits are eight tool calls, six model turns and a
120-second diagnosis budget. The trusted live demo narrows this to the active
scenario, at most two model requests, one synthetic dispatch and 45 seconds per
request including worker startup. Cleanup has separate ownership/budgets; these
numbers do not establish a hard wall-clock SLA. [Trusted backend scope][live]
records the factory, limits and historical smoke evidence.

## 4. MCP Client and ToolBridge

The [MCP Client][client] uses the official Python MCP SDK 2.3.0 over stdio. The
validated runtime baseline recorded in the gate evidence is Python 3.12.16.
The client owns initialization, discovery, calls, disconnect and SDK context
cleanup. Its generic interface does not interpret project-specific business
response envelopes. Server executable/arguments and the client allowlist come
from trusted configuration.

[ToolBridge][bridge] validates one proposal against trusted JSON Schemas and a
read-only registry, applies tool/time/output budgets, and preserves bounded safe
results. It does not treat discovery or model claims as authorization. It retains
`content`, `structuredContent` and `isError` for higher-level interpretation.
Transport timeout/disconnect after dispatch can leave the execution outcome
unknown; the application does not invent a successful response, automatically
retry/reconnect, or infer a particular exit code from generic EOF.

The synthetic demo uses an explicit fixture schema and provenance adapter, not the
unfinished production binder. Canonical mock bindings in #15/#16 separately use
PR #6 revision R1 at `d442479`, with application-created session/trace/logical-call/
attempt IDs and full input/output validation. Provider call IDs, logical IDs,
attempt IDs and SDK JSON-RPC IDs have different roles. The generic Client does not
manufacture business metadata or evidence. Candidate contract use does not confer
final contract or security signoff.

### Contract revision boundary: accepted R1 versus candidate R3

The current PR #6 manifest still declares schema_version **1.0.0**, Python 3.12,
MCP SDK 2.3.0 and stdio. The unchanged version string is not proof of compatibility:
the [R1-to-R3 comparison][contract-diff] includes schema and runtime changes.

| Revision / source | Evidence actually available | Agent integration status |
|---|---|---|
| R1: docs `d442479493d94c54a2d35698d3f920e7758b7744`, audited runtime `917d9f0cc49f9793a5a23072818c59116a7678e9` | [Hiền's explicit acceptance][integration-r1] covers input/output, status/isError, correlation, evidence/time and transport/cleanup mapping; #15/#16 retain these exact schema snapshots | Accepted **Integration Contract R1 only**; canonical mock binding evidence is scoped to those snapshots |
| R2/R3: current HEAD `fd737fbc1a6fe991e55af27b0554c342a818022a`, revision `2026-11-15-R3` | [R2 changes][contracts-r2] and [R3 handoff][contracts-r3]; author-reported tests; [Trang's R3 confirmation][security-r3] covers three tools at mock level and explicitly excludes detailed result schemas, transport, the other nine tools and real collectors | **PENDING Agent integration revalidation and finalization**; no R3 acceptance inferred from server tests or R1 signoff |

The reviewed changes include error coverage/correlation for accepted requests,
log-source enums and retention semantics, per-event evidence, restart lifecycle
fields with `execution_unknown` requiring `executed=null`, and a 60-second proposal
TTL. Whole-result schemas also change for read tools, including service_status,
detect_anomaly and verify_fix: status restrictions, bounded message/excerpt fields,
error coverage and truncated/partial rules must be checked against the consumer.
R3 is not a documentation-only update to R1. A schema_version match alone must not
silently upgrade the pinned Agent registry or validation snapshots.

Before adopting R3, Hiền and Thủy must compare the exact full input/result schemas,
discovery and structuredContent mapping; test changed errors, correlation,
partial/unknown and evidence handling through the Agent boundary; and record a
revision-specific integration decision. Trang must confirm the remaining security/
transport scope separately. The R3 handoff explicitly says **NOT finalized; no
merge**. No acceptance or tests are created by this report. In particular, live v2
used the integration demo's synthetic fixture contract, not the canonical R1 or
R3 server, and therefore validates neither canonical revision end to end.

## 5. Agent Coordinator and reasoning loop

The [Coordinator implementation][coordinator] owns the state machine. For the
successful active fixture, the observed trace is:

`RECEIVED → ANALYZING → COLLECTING → ANALYZING → CONCLUDED → REPORTED`

Multi-tool scenarios repeat ANALYZING/COLLECTING. Error, cancellation and budget
exhaustion have explicit terminal paths. A `limited` outcome can use the CONCLUDED
state while retaining an uncertainty report; that state does not certify truth.

A turn exposes bounded safe observations to the decision source. The application
validates a proposed read, dispatches through ToolBridge, records the returned
observation and asks for the next decision. A conclusion must cite evidence already
in the session. The Coordinator prevents unauthorized tools, repeated proposals
without new evidence and dispatch after exhausted budgets. It preserves received
safe evidence when a budget ends instead of creating an unsupported conclusion.

In mock mode, decisions are scripted by the existing MockHTTP fixture. Multiple
turns therefore demonstrate orchestration and lifecycle, not independent LLM
reasoning. Live mode substitutes the real Adapter while retaining the same synthetic
server and dispatch controls. The current live evidence covers only one active case.

## 6. CLI and Incident Reporting

The [synthetic CLI][cli] accepts diagnostic questions, selects active/port-conflict/
permission-denied fixtures and reuses the Coordinator. The integrated CLI additionally
selects the trusted mock or Ollama backend. Output identifies the mode and labels
synthetic data. Permission denial produces a limited result rather than an invented
root cause or a claim that access was granted.

The [Markdown renderer][renderer] produces the Incident Report from application
state, conclusions, evidence and citations. Export preserves the stdout text, uses
private report permissions and enforces a safe basename, no traversal/symlink and
no overwrite. Report content is escaped and filtered. Its role is explanation;
it is not an authoritative approval/helper audit or proof that a service was fixed.

[The action confirmation UI][actions] is a separate fixture-only command. It shows
nginx.service, the proposed action, reason, risk and available evidence sources.
States are proposed/approved/rejected/expired. A local approved decision is never
executed or backend authorization. Input requires the same actual TTY for stdin
and stdout, an exact phrase with the current application-generated ID, and an
unexpired deadline. Non-TTY input, EOF, invalid input and cancellation fail closed.
No model approval token, arbitrary command, grant, control socket or helper is
accepted. TTY checks do not authenticate a human; same-UID software can drive a
pseudo-terminal. Production authorization remains separate work.

## 7. Evidence, provenance and citation handling

Evidence is received from a tool/fixture; the Agent must not invent observations,
IDs or citations to complete a narrative. Synthetic sources remain explicitly
synthetic. The Coordinator's compact Evidence projection carries ID, source and
text. The canonical bindings retain richer safe ledgers, including observed_at,
unit, redacted/truncated flags and baseline references. Full ledger-to-report
integration remains unfinished.

ToolBridge can provide an internal tool-role message. The current decision source
serializes Coordinator observations as labelled untrusted user data because that
projection lacks authoritative tool-call correlation. It does not manufacture
role=tool metadata by parsing a source string. Schema validation and redaction do
not promote tool output into a system instruction or establish semantic truth.

[detect_anomaly binding][anomaly] validates received canonical findings, evidence
references and time windows. It does not itself detect anomalies or implement a
collector. [verify_fix binding][verify] preserves absent/null optional arguments,
known baseline/action references and fresh check evidence. Partial, unknown,
truncated or unsupported checks do not satisfy local checks_passed. Even a received
pass is not proof that remediation occurred. The canonical verify mock returns
partial/unknown without evidence, and its SDK test asserts that limitation.

## 8. Safety restrictions and cleanup lifecycle

Application and server allowlists remain read-only within the demos. No arbitrary
shell, sudo, real restart, privileged helper or automatic approval is involved.
The intended resolution of NoNewPrivileges versus sudo is architectural: the read
plane does not elevate itself; any future privileged action belongs to the separate
reviewed helper path. This report does not claim that hardening has been deployed.

Redaction occurs before data reaches model transport, with additional boundary
filtering and context-appropriate output escaping. Secret-bearing tool arguments
are rejected rather than silently changed. Errors use safe codes rather than raw
provider bodies, prompts or reasoning. Pattern redaction has known coverage and
false-positive limits; it is not clearance to process real sensitive logs.

`run_coordinator()` joins model and executor ownership; DemoSession also owns
Client disconnect and full-session cleanup. Request expiry cannot silently abandon
an owner. If cleanup is pending, retain the owner/session/event loop, report the
pending state and rejoin without retrying inference or an action. Tests observe
child return codes and ownership state at specific points; they do not prove
unconditional teardown under every OS failure. Post-parse output caps are also not
a pre-parse memory-allocation guarantee. [Lifecycle code][session] and [boundary
regressions][bridge-tests] document these limits.

## 9. Testing methodology and verified results

Evidence has several distinct levels:

| Level | What executes | What the evidence establishes |
|---|---|---|
| Unit/mock tests | Controlled HTTP responses and application fixtures | Validation, error policy and orchestration behavior |
| Mock LLM + Synthetic MCP | Fake HTTP transport in a real worker; official SDK stdio and synthetic server | End-to-end fixture dataflow, budgets, reports and observed cleanup |
| Live Ollama + Synthetic MCP | Real local inference and the synthetic server | The recorded active-case integration smoke only |
| Real Linux diagnostics | Requires accepted collectors, lab/VM and ground truth | Not yet verified by the evidence in this report |

### 9.1 Historical local tool-selection benchmark

[The evaluator's recomputation][metrics] uses the archived 1 November benchmark:
18 live LLM requests with mock tool definitions, no MCP dispatch. Native tool name
selection was 18/18, as was exact tool-plus-arguments matching. Median model-request
latency was 3.173447 seconds; mean was 4.556067 seconds. The recorded timeout/error
count was 0/18. These results concern the six-case selection protocol, not root-cause
accuracy or the project's S1–S5 lab evaluation.

Diagnostic/evidence correctness, citation validity, cleanup, safety and verification
are NOT_EVALUABLE from that archive. Semantic correctness requires independent
rubric/ground-truth assessment. The evaluator separates modes/protocol/config/
revision/latency scopes, deduplicates stable run identities and exposes denominators.
A valid hash reference is provenance, not authentication of a submitted claim.
The refreshed [metrics README][metrics-policy] at `2ae801f` explicitly keeps
assessment and official-report eligibility UNVERIFIED. Coverage uses eligible
runs divided by protocol-required/planned runs; absent gates remain pending.
`citation_validity` means reference resolution only, not semantic correctness.
These clarifications do not change the archived 18/18 or latency figures.

### 9.2 Historical live integrated smoke v2 — 14 November 2026

| Observation | Recorded result |
|---|---|
| Scenario | active only — PASS within this scope |
| Runtime/model | Python 3.12.16, MCP 2.3.0, Ollama 0.40.13; pinned Qwen3 4B Instruct |
| Model requests | 2 |
| Native selection | service_status with {"unit":"nginx.service"} |
| MCP dispatches | 1, to the synthetic server |
| Evidence/citation | active-service_status; synthetic source preserved |
| Conclusion | Validated conclusion JSON with an existing citation |
| Total recorded time | 28.188 seconds |
| Output/cleanup | Report and stdout matched; owners cleared; observed workers/server exited 0 |

This table is transcribed from [the committed v2 evidence summary][live]; it is not
a new smoke test. Its raw/runtime artifacts remain outside Git. The earlier v1
returned text-only and failed first-tool policy; v2 followed the policy correction.
One successful invocation is not a reliability benchmark, a causal A/B study or
proof that future requests always select tools correctly. It does not establish
Linux health or provider-side cancellation.

### 9.3 SCRUM-39 offline gate evidence — 15 November 2026

[The Week 2 report][gate] records **356 offline tests PASS, 0 FAIL/SKIP**: four new
gate probes plus 352 existing regressions. The component counts are 57 Adapter,
46 Client, 50 Boundary, 4 fixture acceptance, 60 Coordinator/report, 35 CLI,
33 async Adapter and 67 integration/backend/policy tests. These 352 plus the four
new probes form one recorded validation set. Older overlapping totals and the
independent binding/UI/evaluator suites are not added to it.

[Actual offline observations][observations] show active (2 mock turns/1 dispatch),
port-conflict (3/2), permission-denied (3/2, limited), and a cap-one port-conflict
probe (2/1, budget_exhausted, first evidence retained). Probe source hashes,
provenance references and cleanup/return-code observations are recorded. All
conclusion_verified flags are false. Scripted success is not LLM accuracy.

**Formal Week 2 acceptance remains BLOCKED:** required S1/S2/S4 VM/model traces,
accepted 11 read-only tools and independent manual review are missing. PASS here
validates offline behavior; the formal gate has not passed. Unit PASS counts are
never accuracy denominators. No execution was repeated for this report.

## 10. Limitations and unfinished integrations

1. Most Agent components are pending Draft PRs, with stacked dependencies. The
   integrated demo does not automatically include every independent binding/UI.
2. Synthetic fixture schemas and the canonical mock bindings are distinct layers.
   Production registry/binder adoption requires a reviewed contract handoff; this
   report does not finalize contracts or approve another member's implementation.
3. No accepted real-Linux collector/VM/topology, 11-R runtime proof or S1/S2/S4
   ground-truth trace set is established here.
4. One live active smoke cannot establish root-cause accuracy, general tool-calling
   reliability, performance under VM load or all-scenario completion.
5. Citation checks establish reference membership. Evidence relevance, causal
   sufficiency and diagnosis correctness still need independent review.
6. Rich ledger/report integration, real-data privacy validation and production
   deployment hardening remain incomplete. Cleanup and provider cancellation have
   the limits described above; no hard wall SLA is claimed.
7. The action UI grants no authority. Protected approval coordination, authenticated
   control channels, replay-resistant grants, audit and privileged execution remain
   separate work. No successful remediation or VM/security acceptance is claimed.

## 11. Future work and conclusion

### Final report handoff and responsibilities

| Owner | Required handoff / verification responsibility | Current unresolved condition |
|---|---|---|
| Hiền — Agent/application | Pin Agent commits and consumed contract revision; verify normalized records against original artifacts; reproduce metric arithmetic; preserve mode, provenance, citations and unknown outcomes; submit traces for independent review | Agent R3 revalidation, production binder and approved Linux integration evidence remain pending |
| Thủy — MCP/contracts/collectors | Supply revision-specific authoritative schemas/registry, runtime/dependency hashes, launch topology, error/evidence semantics and reproducible read-only collector evidence; identify changes since accepted R1 | PR #6 remains unfinalized; mock/server test results do not establish Agent compatibility or Linux collector acceptance |
| Trang — lab/security | Supply accepted VM/snapshot/restore and least-privilege conditions; prepare S1/S2/S4 lab conditions with frozen ground truth; review privacy, transport and write-plane restrictions within an explicit scope | Three-tool mock review is not full contract/transport, VM or Linux-runtime signoff |
| Hiền, Thủy and Trang — final submission | Reconcile exact source/evidence revisions, ground truth, counts and scope; retain named, dated independent review decisions and artifact references | Formal Week 2 gate remains BLOCKED; no final experimental acceptance inferred |

Before this draft is used as an **acceptance report**, the team must:

1. Resolve every relevant source/evidence link to the retained commit/artifact and
   verify content and hashes. Metadata supplied by the artifact producer is not
   independent authentication. Record which reviewer checked which revision.
2. Freeze the evaluation protocol, fixed model/configuration, lab state, expected
   cases/repetitions and ground-truth rubric before scoring. Independently compare
   normalized records with original traces and review conclusions against that
   ground truth; citation membership and offline PASS counts cannot replace this.
3. Recompute figures with explicit eligible/expected denominators, reconcile
   duplicates and omissions, and keep synthetic, live-LLM/synthetic-MCP and real
   Linux results separate. Do not add overlapping regression totals.
4. Retain an independent review record covering the actual evidence and limits.
   Missing source verification, ground truth, runs or reviewer confirmation stays
   **PENDING / UNVERIFIED / NOT_EVALUABLE**, as applicable. Leave unavailable values
   unknown; do not fill them with estimates, favorable fixtures or presumed approval.

These are handoff requirements, not claims that the checks have been completed.
The recorded numbers remain historical observations within their stated scopes.

### Next implementation and evaluation steps

Thủy's handoff should provide reviewed contracts/registry, read-only collector
behavior, launch topology and provenance/error semantics. Trang's handoff should
provide accepted VM/snapshot/least-privilege conditions and S1/S2/S4 labs with frozen
ground truth. Hiền can then connect a narrowly scoped read-only production binder,
retain complete evidence and run separately approved fixed-model CLI/VM tests.
The group should manually review the traces before deciding the Week 2 gate.

The later formal evaluation should lock versions/configurations and the rubric,
collect the planned 15 baseline runs, report denominators and disagreements, and
keep unknown results visible. Write operations must remain disabled until their
independent security/approval/helper requirements are met.

The present contribution is a reviewable Agent foundation: bounded orchestration,
validated interfaces, synthetic integration, one narrowly scoped live-LLM smoke
and reproducible offline evidence. It is a basis for the final report and slides;
formal integration/evaluation acceptance remains unfinished.

## Appendix: source and revision ledger

PRs #7–#12 and #15–#19 were rechecked as Open/Draft on 16 November 2026; PR #6 is
Open/non-Draft and explicitly unfinalized at R3. Main is still `990f41f`.
Short SHAs identify the snapshot; links below resolve immutable full commits. PR status may change
later without changing the evidence cited here. Documentation review checked
source links, terminology, numerical consistency and the separation of evidence
levels. It did not rerun any experiment or constitute an independent security signoff.

| Source | Snapshot | Material used |
|---|---|---|
| Main | 990f41f | Architecture/security and synchronous Adapter |
| PR #6 R1 | d442479 / runtime 917d9f0 | Historical accepted integration mapping; pinned mock binding schemas |
| PR #6 R3 | fd737fb | Current candidate schema/runtime changes and pending integration handoff |
| PR #7 | 9c7fbc14 | MCP Client, ToolBridge and lifecycle documentation |
| PR #8 | 61cdff0d | Coordinator, evidence projection and renderer |
| PR #9 | ab05df01 | Original CLI, report storage and fixtures |
| PR #10 | 4cb5ab66 | Async Adapter/worker ownership |
| PR #11 | a7d5e945 | Integrated synthetic pipeline |
| PR #12 | 440c2004 | Trusted factory, first-tool policy and live v2 summary |
| PR #15 | 2e1698eb | Canonical mock anomaly binding |
| PR #16 | fbbc6c29 | Canonical mock verification binding |
| PR #17 | 264626cc | Synthetic action confirmation UI |
| PR #18 | 2ae801f | Current provenance/coverage/citation policy and unchanged historical recomputation |
| PR #19 | 4ae61dd1 | Offline gate matrix and actual observation artifact |

The implementation commands in linked READMEs require their corresponding source
checkout/dependencies. The main-based documentation branch does not bundle these
unmerged modules. No figures extrapolate beyond the recorded measurements.

[architecture]: https://github.com/hienne215/linux-log-agent/blob/990f41f68c8ef13218bf825bd57ff233a8c82f1c/docs/ARCHITECTURE.md
[security]: https://github.com/hienne215/linux-log-agent/blob/990f41f68c8ef13218bf825bd57ff233a8c82f1c/docs/SECURITY.md
[adapter]: https://github.com/hienne215/linux-log-agent/blob/990f41f68c8ef13218bf825bd57ff233a8c82f1c/src/agent/llm/README.md
[async]: https://github.com/hienne215/linux-log-agent/blob/4cb5ab6615db22ca8ce246c65686da15616b4aee/src/agent/llm/async_adapter.py
[decisions]: https://github.com/hienne215/linux-log-agent/blob/440c2004a7742f75550765b9db339734740d80f3/src/agent/llm/decision_source.py
[client]: https://github.com/hienne215/linux-log-agent/blob/9c7fbc14e5526305e69ae4ca835153872211de28/src/agent/mcp_client/client.py
[bridge]: https://github.com/hienne215/linux-log-agent/blob/9c7fbc14e5526305e69ae4ca835153872211de28/tests/integration_boundary/README.md
[bridge-tests]: https://github.com/hienne215/linux-log-agent/tree/9c7fbc14e5526305e69ae4ca835153872211de28/tests/integration_boundary
[coordinator]: https://github.com/hienne215/linux-log-agent/blob/61cdff0d2674f14150a1e7df5940a1372b8c1d24/src/agent/coordinator.py
[renderer]: https://github.com/hienne215/linux-log-agent/blob/61cdff0d2674f14150a1e7df5940a1372b8c1d24/src/agent/incident_report.py
[cli]: https://github.com/hienne215/linux-log-agent/blob/ab05df010a5dc95faf2adbfa6ee997387d2dbc1e/src/agent/cli/__main__.py
[session]: https://github.com/hienne215/linux-log-agent/blob/440c2004a7742f75550765b9db339734740d80f3/src/agent/integration_demo/session.py
[live]: https://github.com/hienne215/linux-log-agent/blob/440c2004a7742f75550765b9db339734740d80f3/docs/TRUSTED_BACKEND.md
[anomaly]: https://github.com/hienne215/linux-log-agent/blob/2e1698eb58150db78d1ff3d3ab16de041cbfa389/tests/anomaly/README.md
[verify]: https://github.com/hienne215/linux-log-agent/blob/fbbc6c29d54b50441647852d39546ed0b6d4a147/tests/verify_fix/README.md
[actions]: https://github.com/hienne215/linux-log-agent/blob/264626ccee510ff95b3a58cb7ef9c29c7fba9e41/tests/action_cli/README.md
[metrics]: https://github.com/hienne215/linux-log-agent/blob/2ae801f4774f5db26f4ffc9b6abc253a00767765/tests/evaluation/EXAMPLE_BENCHMARK.md
[metrics-policy]: https://github.com/hienne215/linux-log-agent/blob/2ae801f4774f5db26f4ffc9b6abc253a00767765/tests/evaluation/README.md
[gate]: https://github.com/hienne215/linux-log-agent/blob/4ae61dd1df8d551d3d996db0d575d79f3ce8353c/docs/SCRUM39_OFFLINE_GATE.md
[observations]: https://github.com/hienne215/linux-log-agent/blob/4ae61dd1df8d551d3d996db0d575d79f3ce8353c/tests/week2_gate/OBSERVATIONS.json
[demo]: https://github.com/hienne215/linux-log-agent/blob/a7d5e94503a44f57781e3fd8ff54f5f6009d3730/docs/INTEGRATION_SYNTHETIC_DEMO.md
[main-tree]: https://github.com/hienne215/linux-log-agent/tree/990f41f68c8ef13218bf825bd57ff233a8c82f1c
[contracts-r2]: https://github.com/hienne215/linux-log-agent/blob/fd737fbc1a6fe991e55af27b0554c342a818022a/docs/SCRUM-12-review-R2-2026-11-15.md
[contracts-r3]: https://github.com/hienne215/linux-log-agent/blob/fd737fbc1a6fe991e55af27b0554c342a818022a/docs/SCRUM-12-review-R3-2026-11-15.md
[contract-diff]: https://github.com/hienne215/linux-log-agent/compare/d442479493d94c54a2d35698d3f920e7758b7744...fd737fbc1a6fe991e55af27b0554c342a818022a
[integration-r1]: https://github.com/hienne215/linux-log-agent/pull/6#issuecomment-6888584230
[security-r3]: https://github.com/hienne215/linux-log-agent/pull/6#pullrequestreview-5909961762
