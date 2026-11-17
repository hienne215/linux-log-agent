# Dependency review readiness — 16/11/2026

Scope: P00-03 / SCRUM-12 / PR #6, then P01-01 / SCRUM-15 / PR #14.
**Current state — 17/11/2026:** PR #6 is merged at `f9adf8f46dec32759be558a5b45bb229e64a3208`,
with canonical schemas identical to approved `2502d44`. PR #14 was squash merged
at `a0206a1`; P03-01 docs were merged in main `f2a8f71`. These merges do not imply
production/Linux acceptance. PR #22 now requires validation and independent review
of its separate P01-03 collector integration.

- [Trang scoped schema-hardening ACCEPTED](https://github.com/hienne215/linux-log-agent/pull/6#pullrequestreview-5938306480): five fixes, optional bounds and both restart result anchors.
- [Hiền integration-mapping delta ACCEPTED](https://github.com/hienne215/linux-log-agent/pull/6#issuecomment-6947807969): source/contract scope only.
- [Hiền adoption-plan review](https://github.com/hienne215/linux-log-agent/pull/14#issuecomment-6947817438): planning only; not independent acceptance of the unpublished audit or new scaffold HEAD.

Current adoption uses a normal merge of the approved main squash commit, preserving
scaffold helpers and both documentation streams, followed by the owned three-file
audit patch. Do not merge the old PR #6 topic history or replay its schema overlay.

## Historical readiness record — 16/11/2026

The pending statements below describe that earlier snapshot and are superseded
by the current scoped record above. No Linux/production acceptance is inferred.

**REVIEW/SIGNOFF PENDING. Not finalized, not production-ready; no merge authorized.**

## Verified review record

| Reviewer / revision | Actual confirmation | Still required |
|---|---|---|
| Hiền, R1 d442479 against runtime 917d9f0 | [ACCEPTED — Integration R1 only](https://github.com/hienne215/linux-log-agent/pull/6#issuecomment-6888584230) | Confirm changed R3/current input/output/error/evidence/correlation mapping. This is not approval of subsequent revisions. |
| Trang, R3 fd737fb | [Three-tool mock security/privacy accepted](https://github.com/hienne215/linux-log-agent/pull/6#pullrequestreview-5909961762): memory_oom_events, failed_logins, restart_service | Detailed result schemas, transport, remaining nine tools and any changed follow-up behavior are not covered. No full-PR acceptance. |
| PR #14 | No independent review/comment observed at audit time | Hiền reviews exact updated scaffold commit and its dependency. Local automated tests are not reviewer signoff. |

No inline review threads were returned for either PR. Historical R2 changes-requested reviews remain in the timeline; the later scoped R3 confirmation supersedes their three-tool mock findings, not the whole approval gate.

## PR #6 follow-up

- Replace contradictory restart status wording with `Synthetic status fixture.`; synthetic succeeded/failed can have executed=true without claiming a real command ran or did not run. No lifecycle, TTL, schema or execution endpoint changes.
- Update eight published status examples, preserving their snapshot timestamps.
- Check manifest paths against all twelve registered input/full-output/data schemas and exercise neutral wording through official SDK stdio.
- The existing mock pagination contract stays intact. A downstream real backend that does not paginate must obtain a separately reviewed contract/ADR decision or implement continuation. This audit does not silently relax the contract for PR #21.
- Scoped signoff is recorded above; no reviewer approval is invented.

## PR #14 gate

After PR #6 technical verification, merge its local follow-up into the existing scaffold history without rebase or force-push. Re-run scaffold compatibility, worker limits/privacy/lifecycle and official SDK checks. Default remains without real collectors; fixtures only enable synthetic service_status. No collector from PR #21 is copied or modified.

## Review and merge sequence

1. Thủy supplies exact local SHA, changed-file list, targeted/full tests and transport evidence.
2. Hiền reconfirms current contract integration mapping. Trang or a designated independent reviewer confirms the uncovered schema/security/transport scope. Preserve all scope exclusions.
3. Only after the appropriate current-revision confirmations may a documentation change record finalization; merging is a separate authorized action.
4. PR #14 is reviewed independently and may be considered for merge only after PR #6 is accepted/merged. Do not mark Jira Done automatically.

GitHub PR bodies are stale: #6 still advertises 30 tests, #14 contains an unfilled template. Updated PR-body drafts and exact audit evidence are supplied as local output artifacts; publishing them requires Thủy's separate authorization.

## Evidence boundaries

Tests use Python 3.12, mcp==2.3.0, canonical Draft 2020-12 validators and synthetic data on Windows. Pytest basetemp is explicitly placed in the writable workspace. Discovery is real SDK traffic; fixture data is not Linux evidence. No VM smoke, real OOM, sudo, group changes, service restart or Agent binder change is performed.

## PR #14 audit finding and correction

Schema validation alone accepted dangling event/check evidence references and duplicate evidence IDs from a worker. Two isolated-worker-result regressions failed before correction (missing reference and duplicate ID). Scaffold now validates unique evidence IDs and resolution of data evidence_id/evidence_refs, while leaving explicitly supplied baseline_evidence_refs external. Valid records still pass. All twelve valid canonical requests are additionally exercised over the official scaffold stdio entrypoint with no backend. This does not attest Linux truth or general-purpose redaction.

## P01-03 dependency adoption — 17/11/2026

The local PR #22 integration uses pinned main `f2a8f71b209c71349a9ba8d6f00ce5e380fac1dd`,
not the historical scaffold topic. Approved canonical schemas and shared runtime
are retained unchanged. Only the operator opt-in service_status worker is added;
no default real backend, new reader privileges or blocked verifier is enabled.
[Hiền's readiness review](https://github.com/hienne215/linux-log-agent/pull/22#issuecomment-6950623314)
is source/dependency review, not final approval or positive Linux observation.
Prior Ubuntu NOT_FOUND evidence stays scoped to its pinned HEAD; a positive read
and independent review of the integrated candidate remain separate gates.
