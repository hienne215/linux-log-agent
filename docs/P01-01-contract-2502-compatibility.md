# SCRUM-15 / P01-01: compatibility audit against schema hardening

Scaffold reference: `656f8c365a300270418e15b34ae1046d12fbcb44` (PR #14).
Canonical reference: `2502d44e803db9082581581659964450da6b46d9` (PR #6).
Author-run offline audit on Windows / Python 3.12.7, MCP SDK 2.3.0.
No production/Linux acceptance or independent reviewer signoff is implied.

## Isolation and exact composition

Audit branch starts at exact scaffold SHA in a separate temporary worktree.
The 33-file PR #6 delta from dc53ff6 to 2502d44 is copied byte-for-byte from
local Git objects into this audit worktree. Every schema, including unchanged
common/OOM/login schemas, is checked against the canonical commit. The copied
contracts are temporary audit inputs, NOT changes authored or committed here.

Keep the scaffold's own mock_mcp_server integration helpers (native_builder and
coverage_error) intact. They are part of PR #14, not defects: replacing the whole
file with PR #6's standalone mock would erase them and test an artificial broken
combination. PR #6 did not change that runtime between its dependency and new HEAD.
No merge/rebase/reset or direct PR #6/#14 branch update was used for this audit.

## Findings

| Boundary | Finding |
|---|---|
| Discovery/registration | All 12 updated canonical input/full-output descriptors remain exposed; availability is independent of discovery. Default backends remain disabled; no restart backend can be registered. |
| Input validation | Raw canonical validation precedes worker and backend selection. All seven newline-unit requests, unsafe source IDs/selectors and oversized verify inputs reject as INVALID_INPUT with safe placeholders. |
| Output validation | Full structuredContent is validated before forwarding; invalid worker output returns existing EXECUTION_UNKNOWN, never its text/data. Native text JSON and isError remain consistent. |
| Correlation | Successful accepted fixture metadata roundtrips; rejected input/output uses invalid-input/session_id=null. R3 coverage errors retain accepted correlation only after validation. |
| New data bounds | 2048-character descriptions, 255-character returned failed-unit names (including .socket), IPv6 with 64-character scope, process="", unit=null and 32 issue objects are accepted at supported bounds. Overflows fail closed. |
| Baseline provenance | 32 external baseline IDs of length 128 remain accepted without response evidence; mismatched/missing/added/reordered worker baselines still reject in the existing scaffold suite. |
| Restart | No execution backend added. Both updated restart data-unit branches enforce strict anchors; execution_unknown executed=null, TTL and lifecycle are checked by the unchanged contract/R3 tests. |
| 64 KiB | Schema-invalid output and transport overflow remain distinct. Bounded, schema-valid large atomic data must still return OUTPUT_LIMIT; partial responses keep whole records. Frame sizing uses scaffold-native metadata. |
| Evidence/Linux claims | Only synthetic fixture workers run. Evidence IDs, references and source labels remain validated; no Linux reader or invented real observations. |

No confirmed production scaffold defect was found. Previously permitted newline
units, path/dot source IDs, unsafe filesystem selectors and oversized IDs/arrays
are intentionally rejected by the new contracts; they are not scaffold regressions.

## Necessary test fixture correction

Initial unchanged scaffold suite against the new schemas: **62 PASS / 2 FAIL**.
Both failures are large_atomic (direct worker and SDK), which expected OUTPUT_LIMIT
but received EXECUTION_UNKNOWN. The old fixture assigned 12000 Unicode characters
to description. That worker output is now schema-invalid, so rejecting it before
frame bounding is correct. Do not loosen validation or change error semantics.

The owned test fixture now uses a short description and 16 synthetic evidence
items with distinct IDs and bounded excerpts. A dedicated regression proves the
raw worker JSON is <=65536 bytes, the full result schema accepts it, and the
serialized native response is >65536 bytes before bounding. Original OUTPUT_LIMIT,
safe-error and SDK connection-survival assertions remain unchanged.

The dedicated regression failed before this fixture fix and passed afterwards.
Focused validation after correction: **40 PASS** (38 new compatibility cases plus
the two formerly failing cases), 0 FAIL/ERROR/SKIP. Existing tests also cover
timeouts, stdout/stderr floods, cancellation/reap, privacy and provenance failures.
Final combined suite: **452 PASS, 0 FAIL, 0 ERROR, 0 SKIP** in 205.98 seconds:
64 original scaffold + 38 new compatibility + 350 P00-03 cases. Thus all 64
original scaffold cases pass after the fixture correction. Generator --check,
all 36 exact canonical tool schemas and diff whitespace checks: PASS. Standalone
official SDK mock smoke: 12/12 PASS; scaffold stdio paths are tested separately
by the existing scaffold suite and new compatibility SDK test. No Linux smoke.

Owned changes suitable for a future PR #14 follow-up are only:
- tests/p01_worker_fixture.py (necessary fixture correction)
- tests/test_p01_contract_compatibility.py (new regressions, including SDK stdio)
- this audit document

No src/mcp_server source, entrypoint, mock runtime, canonical schema, generator or
other branch is part of this audit commit. Adoption of the new canonical dependency
is a separate reviewed Git integration decision; this patch does not merge it.

## Reproduction

Requires both exact revisions locally, the existing Python 3.12 environment and
a separate worktree based on scaffold 656f8c3. Apply the audit fixture/tests commit
there, then copy only the canonical follow-up delta using this Python snippet:

```python
from pathlib import Path
import subprocess
old = 'dc53ff6c3afd038f66e987bf1c7f7ad17f4b7829'
new = '2502d44e803db9082581581659964450da6b46d9'
paths = subprocess.check_output(['git', 'diff', '--name-only', old, new], text=True).splitlines()
assert len(paths) == 33
allowed = {'docs/mcp-tool-contracts.md', 'docs/SCRUM-12-schema-hardening-2026-11-17.md',
           'tests/test_trang_schema_hardening.py', 'tests/mcp_smoke/adversarial_server.py'}
assert all(p.startswith('schemas/tools/') or p in allowed for p in paths)
for p in paths:
    target = Path(p)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(subprocess.check_output(['git', 'show', new + ':' + p]))
```

```text
python generate_result_schemas.py --check
python -m pytest -q tests/test_p01_scaffold.py tests/test_p01_contract_compatibility.py tests/test_tool_schemas.py tests/test_mock_server.py tests/test_response_schema.py tests/test_response_limit.py tests/test_contract_audit.py tests/test_pr6_followup.py tests/test_trang_r2.py tests/test_trang_r3.py tests/test_trang_schema_hardening.py tests/mcp_smoke --basetemp=<writable-temp>
git diff --check
```

The new compatibility tests deliberately require updated schemas; running this
audit branch without the documented overlay tests the old dependency instead.
Do not commit the temporary contract overlay as part of the fixture follow-up.

## Remaining gates

PR #6 still needs Trang's re-review and Hiền's delta acknowledgment according to
the supplied task state. PR #14 needs independent current-dependency review; this
author-run audit is not signoff or authorization to merge. PR #28 remains pinned
to old contracts and its dotted source ID needs a separate follow-up when updated.
Production reader policies, real collectors/authorization, Agent integration and
real Linux smoke are external and NOT performed by this scaffold audit.
