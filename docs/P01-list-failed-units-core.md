# Isolated list_failed_units core — Thủy

Branch: codex/p01-list-failed-units-core. Base: PR #14 at
656f8c365a300270418e15b34ae1046d12fbcb44. Reference: PR #6 at
dc53ff6c3afd038f66e987bf1c7f7ad17f4b7829. Request/data schemas are identical
at these revisions. This is local core work for the list_failed_units portion of
P01-03, not a complete ticket or MCP integration delivery.

## API, operation and fixed output profile

`await collect_failed_units(limit=100)` returns exactly canonical data or raises
`FailedUnitsError(code)`. `parse_failed_units(bytes, limit)` is the isolated parser;
empty bytes alone do not establish command success. The collector checks exit and
stderr before allowing an empty observation.

The sole trusted executable/profile is:

```text
/usr/bin/systemctl --system --no-pager --no-ask-password --no-legend --plain --full --all --state=failed list-units
```

The system manager is queried read-only; no unit pattern, executable, path, shell,
sudo/helper or environment values are accepted from callers. No service action.
Only LC_ALL=C, LANG=C, SYSTEMD_COLORS=0 and empty SYSTEMD_PAGER are passed, with
stdin DEVNULL and cwd=/. No inherited environment secrets or arbitrary file reads.

The baseline targets the systemd 245/247 family of the lab. A JSON list-units
interface is not assumed for that baseline. Instead use a strictly validated fixed
format as documented in the [systemctl 247 manual](https://www.freedesktop.org/software/systemd/man/247/systemctl.html):
no legend/header/footer, full unellipsized names/descriptions, no bullets/colors,
C-locale load/active/sub tokens. Actual compatibility needs a Linux smoke.

Every line must contain unit, known load state, failed active state, failed substate
and optional description. Only literal ASCII canonical names up to 255 characters
with known systemd suffixes are supported, including nonempty @ instances. No
case-folding, guessed unescaping or synthesized name. Escaped names, templates,
duplicates, unknown states, localized/header output, invalid UTF-8, controls and
ellipsis markers fail closed. Nonempty output must end in newline; no partial line
is accepted. Printable Unicode descriptions are preserved, with outer ASCII spaces
removed; length >2048 rejects. No fabricated description or generic replacement.

Descriptions/unit names remain untrusted and this formatting validation is NOT a
secret redactor. There is no MCP/evidence/log/LLM sink here; exposure requires later
reviewed privacy/provenance integration. A literal ellipsis in valid description or
unsupported escaped name can cause conservative rejection, never silent omission.

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
validated listing can yield zero failed units. This observes the current system
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
