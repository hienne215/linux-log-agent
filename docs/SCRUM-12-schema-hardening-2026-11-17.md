# SCRUM-12 / P00-03: Trang schema-hardening follow-up

Review: https://github.com/hienne215/linux-log-agent/pull/6#pullrequestreview-5935965430
Reviewed base: `dc53ff6c3afd038f66e987bf1c7f7ad17f4b7829`.
Status: local candidate, NOT pushed or finalized; Trang re-review required.

## Findings 1–5

1. Replaced `$` on all seven input service-unit patterns with `(?![\s\S])`.
   Applied the same correction to both restart result-data branches, then generated
   derived result schemas. Python re.search/jsonschema now reject trailing newlines
   consistently. Existing ordinary service names remain valid. Date-time `Z$`
   expressions are not unit patterns and retain their existing format validation.
2. Source IDs are lowercase ASCII identifiers, initially alphanumeric and then
   alphanumeric/underscore/hyphen, max 128. No paths/dots/control/option-like IDs.
   This is syntax, not an invented production allowlist. The contract explicitly
   declares literal, case-sensitive, non-regex search; mock runtime stays synthetic.
3. Filesystem syntax preserves logical `root`, `/`, `/dev/mock` and safe absolute
   slash-separated alphanumeric/underscore/hyphen selectors. Max 255; no dot,
   traversal, empty components, controls or options. A syntax-valid path is NOT
   authorized: the isolated real disk core continues to accept only mapped `root`.
4. issues has maxItems=32 and strictly defined code/severity/message objects,
   additionalProperties=false, required fields and bounded text. No existing
   nonempty issue fixture was found; mock [] remains valid. No real verifier enabled.
5. action_id maxLength=128; baseline maxItems=32 and item maxLength=128;
   check_types maxItems=3; signals maxItems=4. Existing uniqueItems=true, enums,
   null/absent/empty semantics are preserved, including signals=[]. No new error codes.

## Recommended item 6: handled selectively

Audited every response-data string/array. Added justified bounds to nine tool
data schemas using input caps, 200-record collector limits, 50 anomaly findings,
3 check kinds, 32 evidence references, 2048 text and existing collector field caps.
Failed-unit names retain 255-character support including non-service units; do
not apply the input service-only regex to this output. Listening-port process=""
and unit=null remain valid; 128-character address bound covers the existing
IPv6 plus at most 64-character scope representation. No claim of process redaction.

Deferred optional maxItems for OOM/login events and optional date-time/common
envelope caps to avoid expanding the already scoped R3 approvals or limiting valid
RFC3339 precision without a separate decision. memory_oom_events/failed_logins
data/result schemas are byte-unchanged. restart data/result changes only the two
unit anchors; runtime, enums, TTL, executed semantics, coverage and fixtures of
all three R3 tools are byte-unchanged. The invalid trailing-newline restart unit
is intentionally no longer schema-valid; Trang should confirm this schema delta.

## Regression and validation evidence

Author-run Windows / Python 3.12.7, mcp 2.3.0, jsonschema 4.26.0.

Before changes, the exact nine P00-03 test paths listed in Trang's review reproduced
the baseline: 190 PASS, 0 FAIL/ERROR/SKIP. New required regression tests on the
old schema: 46 FAIL / 100 PASS (146 total), confirming all five reported gaps.
After fixes: 146/146 PASS; optional response boundary tests extend this to 159.
An additional output-boundary regression brings the final new-test count to 160.
The first full run had 348 PASS / 1 FAIL: the old near-cap SDK fixture put about
31 KiB into description, now intentionally invalid under the 2048-character cap.
Updated only that test fixture to distribute payload across bounded evidence
excerpts with unique synthetic IDs. The original >65500-byte near-cap assertion
and <=65536-byte frame checks remain unchanged. Runtime is not relaxed to make
the test pass; the new regression asserts an under-frame-cap 2049-character
description still fails output validation with existing EXECUTION_UNKNOWN.

The final suite adds tests/test_trang_schema_hardening.py to those nine paths:
**350 PASS, 0 FAIL, 0 ERROR, 0 SKIP** (190 original + 160 new), 97.85 seconds.
Standalone official SDK stdio smoke: **12/12 PASS**; generator --check, all
12 request/data/result schemas and git diff --check: **PASS**.
Tests cover strict positive/negative anchors across seven
tools, source/filesystem grammar, issue object boundaries, nullable IDs/refs,
array uniqueness/limits, optional data caps, safe-error metadata suppression,
restart result anchors and structural validation of all 36 tool schemas.

```text
python generate_result_schemas.py
python generate_result_schemas.py --check
python -m pytest -q tests/test_tool_schemas.py tests/test_mock_server.py tests/test_response_schema.py tests/test_response_limit.py tests/test_contract_audit.py tests/test_pr6_followup.py tests/test_trang_r2.py tests/test_trang_r3.py tests/test_trang_schema_hardening.py tests/mcp_smoke --basetemp=<writable-temp>
python tests/mcp_smoke/test_all_tools_stdio.py
git diff --check
```

## Expected dependent-PR impact (no changes to those branches)

| PR | Compatibility consequence |
|---|---|
| #14 scaffold | Must consume updated schema dependency. Valid fixture requests remain valid; formerly accepted newline unit/oversized refs now reject before worker. Baseline provenance behavior is unchanged. Revalidate independently after adopting candidate; no scaffold acceptance is claimed here. |
| #21 OOM | No changes to OOM request/data/result schema or runtime. Existing stateless cursor disagreement remains a separate collector gate. |
| #22 service_status | nginx.service and redacted description fit new bounds. Invalid newline unit now rejects. No code changes required for supported values. |
| #23 journal parser | Scalar entry mapping/200 records/2048 message caps retained. No API change; does not itself implement the full endpoint. |
| #24 disk core | root mapping is explicitly schema-valid. Caller paths outside its mapping remain unsupported/policy-denied; schema syntax grants no new filesystem support. |
| #25 failed units | 200 records, descriptions 2048, unit names 255 retained, including non-service systemd units. |
| #26 ports | 200 rows, process="", unit=null and IPv6/scope output remain valid. Caller unit newline now invalid. |
| #27 matcher | Literal semantics align; fixture source_id="fixture" remains valid. Primitive API is unchanged. |
| #28 offline audit | Remains reproducible on pinned OLD schemas. When auditing this new candidate, its synthetic.fixture.only ID must change to a valid ID such as synthetic-fixture-only and schema pins must update. Do not present old pinned PASS as testing the new candidate. |

Frozen contract snapshots under Hiền's tests are historical and are not silently
rewritten. Production/application-owned source/mount allowlists, Linux collectors,
blocked check_config verifiers and independent signoff are outside this patch.
Hiền's integration mapping acceptance at dc53ff6 is historical; it is not approval
of these new constraints. No PR metadata, review state or Jira changed; no history
rewrite. This follow-up is recorded as a normal local commit.

## Changed-file groups

- Nine request schemas: check_config, detect_anomaly, disk_usage, listening_ports,
  query_journal, restart_service, search_log, service_status, verify_fix.
- Ten data schemas and their ten generated result schemas: the same nine tools
  plus list_failed_units. All live under schemas/tools/ with the existing names.
- docs/mcp-tool-contracts.md and this follow-up document.
- tests/test_trang_schema_hardening.py and tests/mcp_smoke/adversarial_server.py.

Total: 33 files. Generator implementation, mock runtime, common schemas and the
two OOM/login tool schema triplets are unchanged. No push or Linux smoke performed.
