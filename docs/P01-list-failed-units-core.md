# Isolated list_failed_units core — Thủy

Branch: codex/p01-list-failed-units-core. Base: PR #14 at
656f8c365a300270418e15b34ae1046d12fbcb44. Reference: PR #6 at
dc53ff6c3afd038f66e987bf1c7f7ad17f4b7829. Request/data schemas are identical
at these revisions. This is local core work for the list_failed_units portion of
P01-03, not a complete ticket or MCP integration delivery.

## API, operation and fixed output profile

`await collect_failed_units(limit=100)` returns exactly canonical data or raises
`FailedUnitsError(code)`. `parse_failed_units(bytes, limit)` is the isolated parser;
only a JSON [] listing is empty; empty bytes are malformed. The collector checks
exit and stderr before allowing an empty observation.

The sole trusted executable/profile is:

```text
/usr/bin/systemctl --system --no-pager --no-ask-password --no-legend --plain --full --all --state=failed --output=json list-units
```

The system manager is queried read-only; no unit pattern, executable, path, shell,
sudo/helper or environment values are accepted from callers. No service action.
Only LC_ALL=C, LANG=C, SYSTEMD_COLORS=0 and empty SYSTEMD_PAGER are passed, with
stdin DEVNULL and cwd=/. No inherited environment secrets or arbitrary file reads.

The JSON profile was checked against upstream systemd v247 source:
[argument parsing](https://github.com/systemd/systemd/blob/v247/src/systemctl/systemctl.c),
[list-units fields](https://github.com/systemd/systemd/blob/v247/src/systemctl/systemctl-list-units.c),
[JSON table output](https://github.com/systemd/systemd/blob/v247/src/systemctl/systemctl-util.c).
This is source-level evidence, not a Linux smoke or proof for the lab's actual
binary/version. An older/different build without this JSON profile is unsupported;
there is no fallback to human columns or synthetic success.

Parse exactly one JSON array of records with unit, load, active, sub, description
and optional job. Reject missing/extra fields, duplicate JSON keys, NaN/Infinity,
exponent-overflow numeric values, non-string fields, invalid UTF-8, unpaired
surrogates, controls, malformed/truncated JSON or concatenated documents. Known
load tokens and active=sub=failed are required. Optional job is validated printable
text <=64 characters but never forwarded into description or canonical data.

Only literal ASCII canonical names up to 255 characters with known systemd suffixes
are supported, including nonempty @ instances. No case-folding, guessed unescaping
or synthesized name. Escaped names, templates, duplicate units and unknown states
fail closed. Printable Unicode descriptions are preserved, removing outer ASCII
spaces; length >2048 rejects. Empty descriptions stay empty; no '-' placeholder is
invented. Literal ellipsis in a complete JSON description is data, not truncation.

Descriptions/unit names remain untrusted and this formatting validation is NOT a
secret redactor. There is no MCP/evidence/log/LLM sink here; exposure requires later
reviewed privacy/provenance integration. Unsupported escaped names may cause
conservative rejection, never silent omission.

## Final source audit correction

The original ed4f3b text parser assumed five columns. Upstream v247 conditionally
adds a JOB column when any listed unit has a pending job. The original parser
accepted `nginx.service loaded failed failed start Actual description` and returned
`start Actual description`, incorrectly folding JOB into the description. Human
format also uses display placeholders for empty text. This follow-up uses fixed
JSON output to preserve distinct job/description fields, rejects ambiguous legacy
text, and adds regression fixtures. Only the three core task files are changed.

## Bounds and lifecycle

Async create_subprocess_exec with shell=False avoids subprocess.run's unbounded
capture_output. Concurrent pipe readers retain <=65536 stdout bytes and <=4096
stderr bytes, reading at most remaining budget +1 (chunks <=4096) to detect overflow.
The +1 overflow byte is rejected without retention. StreamReader limit=4096 bounds
normal transport buffering/backpressure; OS/asyncio pipe buffers are additional to
the retained payload budget. No universal total-process memory cap is claimed.

TIMEOUT=5 seconds covers pending spawn and command/read/wait. On timeout, overflow
or cancellation, retain pending spawn, kill the owned child, cancel readers,
drain/discard remaining pipes and confirm exit before returning. No retry.
Cleanup can exceed the deadline: spawn/kill/reap and OS-stuck processes are not a
hard wall SLA. Repeated cancellation, failed kill or forced worker termination can
defeat cooperative cleanup; this is not a deployment supervisor/process-tree claim.

At most 200 records. Invalid limit (including bool) is rejected before spawn.
If records exceed requested limit, record budget, byte cap or description cap, raise
OUTPUT_LIMIT; do not return an incomplete success. No silent slicing/truncation,
next_cursor, cursor state or CommonResponse is created. Later MCP integration must
implement reviewed snapshot/pagination and partial semantics before exposing this
all-or-error core through the canonical cursor contract.

## Safe errors and limits of observation

INVALID_INPUT: invalid limit. UNSUPPORTED: executable/API missing, systemd/bus
unavailable or unsupported command option. PERMISSION_DENIED: OS permission failure
or explicit forced-C access diagnostic. TIMEOUT: deadline expiry. OUTPUT_LIMIT:
bounded output/records exceeded. EXECUTION_UNKNOWN: malformed/unsafe/truncated
output, unknown diagnostics or nonzero unexpected command exit. Messages contain
only the fixed code, never stderr, arbitrary output or exception text.

Even exit 0 with any stderr is rejected. Only exit 0, empty stderr and a completely
validated JSON listing can yield zero failed units. This observes the current system
manager's visible in-memory units, not every installed unit/other namespace or an
independent proof of a bus policy's completeness. Silent policy filtering cannot be
detected by this core alone; deployment reader policy still requires verification.

## Verification/handoff

Offline Python 3.12 tests mock subprocess and pipe/process lifecycle; no Linux
command or service mutation is executed. Canonical data schema validation, positive
and negative parsing, bounds, limits, safe errors, timeouts, cancellation and pending
spawn cleanup are covered. Run only:

```text
python -m pytest -q tests/test_list_failed_units_collector.py --basetemp=<writable-directory>
```

Pending: independent review, real Linux output/permission smoke, finalized contracts,
scaffold worker/dispatch, privacy policy, evidence/correlation/envelope, secure cursor
and deployment isolation/resource supervision. No Linux smoke or MCP acceptance is
claimed by synthetic fixtures. No dependency branch is modified or declared final.

Local verification: Windows / Python 3.12.7, **66 PASS, 0 FAIL, 0 ERROR,
0 SKIP** in the targeted suite, including canonical data schema checks. All
command/pipe/process observations are test doubles, not Linux smoke evidence.

Fresh final-audit verification after the JSON correction: Windows / Python 3.12.7,
**85 PASS, 0 FAIL, 0 ERROR, 0 SKIP**. The earlier 66-PASS report above describes
the original text-profile revision. Linux smoke remains NOT RUN.
