# SCRUM-42 — offline metrics and error analysis

Standalone stdlib Python tool on main `990f41f68c8ef13218bf825bd57ff233a8c82f1c`;
no dependency on unmerged Agent PRs. It **never runs an Agent, benchmark, inference,
MCP server or Linux tool**. JSON/Markdown output goes to stdout; no implicit writes.

Planning read read-only: local docs/TASKS.md P04-06, ROADMAP.md evaluation section,
DEFINITION_OF_DONE.md final gate and DEPENDENCIES.md P04-05 -> P04-06. Those planning
files are currently untracked outside this branch; they were not copied/changed.
Required metrics: root-cause and evidence correctness, median tool count/latency,
outside-allowlist executions, unauthorized-write rejection and verification success.
Tool-selection/citation/error/cleanup metrics support the Agent evaluation. The
>=80% diagnostic target is **not a measured result or a gate this script declares**.
The formal S1-S5 x 3 evaluation, locked protocol and reviewer ground truth remain
pending from P04-05. This toolkit can be prepared independently; it is not SCRUM-42
final experimental acceptance.

## Run / reproduce

From repository root (Python 3.12 recommended; no new packages):

```bash
python -B tests/evaluation/metrics.py --input normalized-runs.json --format json
python -B tests/evaluation/metrics.py --input normalized-runs.json --format markdown
python -B tests/evaluation/metrics.py --benchmark-example --format json
python -B tests/evaluation/metrics.py --benchmark-example --format markdown
python -B -m unittest discover -s tests/evaluation -v
python -B -m unittest discover -s tests/benchmarks -p test_benchmark_ollama.py -v
```

Inputs are bounded to 8 MiB / 10,000 records; duplicate JSON keys, nonfinite values,
unknown fields and invalid types fail closed with a generic error, no raw payload
or private path. The tool does not scrape test PASS counts or infer missing data.
No network/fetch/install occurs. CLI works outside repository cwd too. The pinned
example requires Git revision 990f41f locally; a future shallow clone may need an
explicit fetch by the user before running that example.

## Normalized input v1.0 (evaluation format, not MCP contracts)

Root: `{"schema_version":"1.0","runs":[...]}`; optional `protocols` is described below.
Existing v1.0 inputs and metric names remain supported; output metadata is additive.
Test constructors in test_metrics.py
are artificial unit fixtures, **not measurements** or a formal evaluation dataset.
Do not label them real Linux experiments even when testing mode separation.

Every run has these required fields:

| Field | Meaning |
|---|---|
| run_id | Stable 64 lowercase hex ID for the actual physical run; retain across exports/suites |
| mode | synthetic / live_llm_mock_tools / live_llm_synthetic_mcp / real_linux |
| protocol | Non-secret ASCII slug identifying the declared evaluation protocol; not proof it was locked/reviewed |
| config_sha256 | SHA-256 of locked model/prompt/options/environment manifest |
| revision | 40 hex Git revision; prefer actual runtime revision; document if artifact snapshot only |
| latency_scope | model_request or diagnosis_session; never pooled together |
| provenance | artifact_sha256, record_pointer (/rows/N or /runs/N; canonical nonnegative index, no zero padding), snapshot_sha256 (null or SHA-256; required for real_linux) |

The input producer must validate artifacts/trace and preserve the original run ID;
changed assertions under the same ID are rejected. Identical records deduplicate;
conflicting IDs or multiple IDs aliasing the same source record reject the dataset.
A distinct source file with a falsely changed run ID cannot be detected as the same
physical event: **do not rename runs or re-export baseline subfields as new runs**.
No parser can establish authenticity of a hash claim. Normalized hashes/reviewer
references are declared attestations, not signature verification. Reviewer must
resolve them against retained artifacts, protocol and independent ground truth.
Only the pinned example importer reads/verifies local Git artifacts directly.
No model prompt/reasoning, log text, credentials or machine inventory is needed.
Keep protocol labels non-sensitive; arguments are used for matching, never rendered.

Optional fields omitted/null mean unknown, never zero/success:

| Field | Shape / eligibility |
|---|---|
| selection | expected: {name,arguments} or null; actual: array of native {name,arguments}; ground_truth_sha256: SHA-256 or null |
| diagnosis / evidence_assessment | correct: bool/null, ground_truth_sha256, reviewer_sha256, rubric_sha256 (hash/null); all four needed for a provisional calculation, never sufficient for verification |
| citations / evidence_ids | Arrays of IDs; only provided nonempty citations with a known ledger are scored |
| latency_s | Finite nonnegative observed elapsed seconds; include measured failed runs, do not impute missing latency |
| timeout / cleanup_success | Observed bool/null; do not infer from process silence, test PASS or a backend kill |
| errors | Array of observed categories below, [] for observed absence, null/omitted for unknown |
| tool_dispatches | Actual MCP dispatch count, not model proposals, unittest count or expected tool steps |
| outside_allowlist_executions | Independently observed execution count, not attempted proposals |
| unauthorized_write_attempts / unauthorized_writes_rejected | Paired observed counts; rejected <= attempts |
| verification | approved_fix: bool, outcome: pass/fail/unknown; approved_fix must refer to an evidenced approved fix eligible for verification, not local synthetic UI approval |

A failed/text-only selection with known expected tool has actual=[]; it counts as
incorrect. If expected/ground-truth source is missing, selection is NOT_EVALUABLE.
Malformed model output belongs in errors=['malformed_output'] with a normalized
empty actual list; do not inject raw malformed output into the metrics format.
The tool never evaluates semantic diagnosis text automatically. A reviewer must
score the frozen rubric/ground truth, record disagreements, and reference evidence.
An ID existing in a ledger is not a semantic correctness assessment.

### Assessment provenance and official reporting

`_assessment()` checks structure/completeness only. Any producer can supply a valid
hash or `correct=true`; neither establishes that the artifact exists or that a
reviewer checked it. `EVALUATED` means the arithmetic is available from declared
inputs. Diagnosis/evidence scores remain `verification_status=UNVERIFIED`, and
every cohort has `official_report_eligibility.status=UNVERIFIED`. Missing assessment
references yield `NOT_EVALUABLE`; complete references alone never lift UNVERIFIED.
The evaluator accepts no self-issued reviewer approval/verified flag and does not
authenticate identities, resolve external artifacts, or authorize official use.

Before a result can support **formal acceptance**, an independent reviewer must:

1. Resolve retained original run, ground-truth, rubric and protocol artifacts;
   compare bytes/hashes, run identities and normalized assertions with those
   originals. A hash of reviewer metadata is not proof of reviewer identity/review.
2. Confirm the frozen protocol, required cases/repetitions, expected eligible-run
   counts and explicitly approved coverage/performance gates. Reconcile omissions,
   censoring, duplicates and case allocation; a count ratio alone cannot do this.
3. Review diagnosis/evidence semantics against ground truth, record disagreements
   and retain an independent signed-off review record outside this self-declared
   dataset. Link that record and original artifacts in the formal report.

Until those checks are evidenced, results are provisional analytical outputs, not
acceptance evidence. This tool does not invent a review record, upgrade a status
to verified, or claim that the historical example has completed these checks.

## Denominators / coverage

Rates are fractions 0..1, with numerator, denominator, eligible/missing runs. No
cross-cohort overall accuracy is emitted: mode, protocol, config, revision and
latency_scope all partition results. Formal eligibility requires protocol-defined
coverage and independent review, not simply a high value on the scored subset.

An optional root `protocols` map keys the protocol slug to exactly:

```json
{
  "expected_runs": 15,
  "minimum_coverage": null,
  "protocol_sha256": null
}
```

This is a **format illustration**, not an approved protocol or measured dataset.
Supply the required/planned run count from the actual protocol; do not choose it
from the successful subset. A supplied non-null `minimum_coverage` must be a finite
fraction 0..1 defined by that protocol, never an evaluator default. The protocol
hash is a declared reference, subject to the independent comparison above.
The same definition applies separately to each mode/config/revision/latency cohort;
use different protocol slugs if planned counts/gates differ. It never pools cohorts.

Each metric gains `coverage`: `eligible_runs / expected_runs`, with observed,
expected and unobserved counts. Rate denominators (e.g. cited IDs or unauthorized
attempts) remain unchanged and are **not** used as run counts. Legacy `missing_runs`
means observed records missing that metric; `unobserved_runs` means planned records
absent entirely. Deduplication happens first.

- Missing protocol/count: `PENDING_PROTOCOL_GATE`, ratio null.
- Known count but missing threshold or protocol reference: numeric coverage is
  shown, gate remains `PENDING_PROTOCOL_GATE`. No threshold is invented.
- Below an explicitly supplied threshold: `INSUFFICIENT_DATA`, not a failed
  diagnosis or fabricated zero accuracy. Available subset scores remain visible.
- Too many observed runs: `PENDING_PROTOCOL_GATE / UNEXPECTED_RUN_COUNT`; reconcile
  the cohort instead of selecting favorable runs or capping the ratio.
- `MET` means the declared **count coverage** is met, not accuracy, balanced scenario
  coverage, authenticated provenance, or formal acceptance. Even 100% accuracy and
  count coverage leave official-report eligibility UNVERIFIED.

- Tool selection: exactly one native proposed tool with expected name / runs with
  selection ground truth. Joint accuracy additionally requires exact JSON args;
  bool/int and numeric representations are distinct under canonical JSON matching.
- Diagnostic/evidence correctness: provisional declared rubric scores with ground
  truth/reviewer references; missing references -> NOT_EVALUABLE, not correct.
  Complete references still remain UNVERIFIED until independent checks above.
- Citation validity: valid unique cited IDs / all unique cited IDs, deduplicated
  within each run. Missing/no citations are unscored with coverage reported, not
  silently 100%. It is reference membership only, not evidence semantic quality.
  The legacy field name `citation_validity` is retained for API compatibility;
  JSON labels its meaning `REFERENCE_RESOLUTION_ONLY` and semantic correctness
  `NOT_EVALUATED`. A resolved citation ID does not prove the evidence supports the
  conclusion, that the evidence itself is true, or that the diagnosis is correct.
- Latency and actual tool dispatches: mean/median/min/max of observed values, n and
  missing count; seconds and dispatches are separate. No outlier removal or invented
  zero for timeouts; censored/missing measurements must be recorded as such.
- Timeout/cleanup: affected/successful runs / runs with that observed bool.
- Error rate: runs with any classified error / runs with known error labels or
  directly derived failure. Unknown error coverage remains excluded and visible.
- Safety: sum outside-allowlist executions, with observed/missing coverage. A known
  zero is not equivalent to no safety data. Unauthorized rejection uses attempted
  unauthorized writes as its own denominator; zero attempts -> NOT_EVALUABLE.
- Verify success: pass / eligible approved-fix verifications; fail and unknown both
  non-success, unknown separately counted. Unapproved cases never enter denominator.

## Error taxonomy

Multi-label counts may overlap; they must not be summed as disjoint failed runs.
Per-error rows include stable run ID and source artifact SHA/pointer, not raw errors.

| Category | Source |
|---|---|
| tool_selection | Wrong name/args, absent/multiple calls under known selection ground truth |
| malformed_output | Observed malformed native response; explicit trace label |
| transport | Explicit observed transport/protocol failure label |
| permission | Explicit permission-denied label; not automatically a wrong diagnosis |
| insufficient_evidence | Explicit lack-of-evidence label; not automatically semantic incorrectness |
| cleanup | Observed cleanup_success=false or explicit cleanup error |
| timeout | Observed timeout=true or explicit timeout label |
| citation | At least one citation absent from a known ledger |
| diagnostic / evidence | Provisional complete declared assessment scored false; not an independently verified failure |

Labels identify operational incidences, not proof of root causes. Review all
conflicts/disagreements against raw traces; do not reverse labels to meet a target.

## Real historical example (not new inference)

EXAMPLE_BENCHMARK.json/.md are deterministic recomputations of the **2026-11-01**
SCRUM-14 Instruct benchmark, from committed native tool_calls + expected calls.
Source revision is an **artifact snapshot**, not proof the runtime used that commit:
[raw results](https://github.com/hienne215/linux-log-agent/blob/990f41f68c8ef13218bf825bd57ff233a8c82f1c/tests/benchmarks/instruct_full_results/20261101T170502Z/results.json),
[ground-truth cases](https://github.com/hienne215/linux-log-agent/blob/990f41f68c8ef13218bf825bd57ff233a8c82f1c/tests/benchmarks/cases.json).
Importer verifies the cases hash against recorded suite_sha256 and each row expected
against the cases, hashes full raw/config artifacts, and reads only the 18 rows.
It does not count embedded Thinking baseline comparisons or cached summary flags
as additional trials. It is intentionally limited to this archived completed
format; other runs require an explicit reviewed normalizer, not guessed labels.

Recomputed: native tool name **18/18**, joint exact args **18/18**, median model
request latency **3.173447s**, mean **4.556067s**, observed timeout/errors **0/18**.
These were **live LLM + mock tool definitions**, with no MCP dispatch. They are not
Live LLM + Synthetic MCP integrated smoke, 15-run Linux evaluation or diagnostic
accuracy. Cleanup, citations, semantic correctness, safety and verify metrics are
NOT_EVALUABLE because this archive does not supply their required observations.
No evaluable error rows exist in this example; error taxonomy is unit-tested, not
filled with invented failures. The 14/11 integrated smoke is not imported because
there is no reviewed normalized trace bundled here; the tool does not extrapolate
one successful scenario into reliability or Linux accuracy.

The example preserves the archive's planned count of 18, so selection coverage is
18/18. The archive supplies no coverage-gate threshold/reference used by this
evaluator: `PENDING_PROTOCOL_GATE`, not acceptance. Raw benchmark data, the numeric
metrics and mode are unchanged; JSON/Markdown examples add only evaluation metadata.

Review-fix validation: Python 3.12.16, **30 targeted metrics tests PASS** (20 existing
+ 10 regression tests). No harness or repository-wide rerun was needed for these
evaluator-only changes. Historical 20 metrics + 15 harness PASS is prior evidence,
not a new run. Unit PASS counts validate code, never experimental accuracy.
No inference, production Linux, new benchmark or Jira operation.
