# Bounded read-only diagnostic extension

Stacked on the two-tool Agent PR #31 (d2ed93f). Adds `list_failed_units` and
`listening_ports` without changing canonical schemas, server, WorkerRunner,
Coordinator, Adapter APIs or the member branches.

Collector cores are copied verbatim from Thuy's PR #25 at
0600c5bdaf7cbf3fdc501e90815d14fa2e40e5cb and PR #26 at
6d33c845d300f330a319ed92a00667da8caa0053. Their tests are retained; one IPv4-mapped
IPv6 assertion compares numeric equivalence because Python patch versions differ
in string formatting. This is not a new collector algorithm or an upstream merge.

## Scope and privacy

- Failed units: application owns limit=100; no cursor is exposed to the model.
  More than the complete cap returns OUTPUT_LIMIT, not a fabricated complete page.
  Every Description is replaced with [REDACTED] before worker IPC.
- Sockets: TCP port 80 only. No unit or process attribution; no remote connection.
  LAN/WAN IPs and interface identifiers are withheld. Wildcard and the fixed
  loopback addresses remain visible. Listening is not HTTP health.
- Both collectors execute fixed absolute argv without shell, sudo or restart.
- Each selected tool gets a distinct stdio profile; authorization is independent
  of canonical discovery. All requests/results validate full canonical schemas,
  correlation, provenance, counts and expected scope. No retry or fallback.
- One dispatch per selected tool, at most four in diagnostics scope; at most two
  model HTTP requests, 45 seconds each including worker startup, 120 seconds for
  diagnosis. Coordinator can use five turns to consume a validated native batch.
  Cleanup retains its own owner and is not a hard wall completion SLA.
- The worker converts SIGTERM into cancellation so core finally blocks kill/wait
  their nested children. Tests exercise synthetic nested-child cancellation and
  outer WorkerRunner timeout. No guarantee is claimed against arbitrary external
  SIGKILL of a parent, machine failure or stopped Ollama backend inference.

## Run

Use Python 3.12 with pinned MCP 2.3.0 and the existing dependencies. From root:

```bash
PYTHONPATH=src .venv/bin/python -B -m agent.read_only --scope diagnostics
PYTHONPATH=src .venv/bin/python -B -m agent.read_only --scope failed
PYTHONPATH=src .venv/bin/python -B -m agent.read_only --scope ports
```

The original default remains two tools until this extension's acceptance.
`--backend mock` explicitly uses scripted model responses with the real Linux
collectors. It is not evidence of live model reasoning. `--report new-name.md`
exports through the existing private no-overwrite report store.

Questions: “Có unit nào đang failed không?”, “Cổng TCP 80 có đang lắng nghe không?”,
or a question requesting all four diagnostics. No HTTP request or remediation
is performed. Journal/search need a separately reviewed source and reader/privacy
policy; they are not silently exposed here.

## Verification of this extension

Python 3.12.16 / MCP 2.3.0: focused regression **467 PASS, 0 FAIL, 0 SKIP**,
with 248 subtests reported separately (not added to the test count). This includes
the inherited core suites, Agent/Adapter/Bridge/Coordinator, official SDK stdio,
full four-tool fixture pipeline and nested-child cancellation/outer-timeout tests.

Actual laptop observations through official SDK stdio with **scripted mock HTTP**:
nginx active/running, root disk counters, zero failed units and two TCP port 80
sockets. All four evidence IDs were cited and normal cleanup completed. Individual
failed-unit and port sessions also completed. These are read-only observations,
not VM reader acceptance or live-model reasoning evidence.

One **live Ollama four-tool attempt FAILED**: preflight passed, but the first
request failed INVALID_ARGUMENTS before dispatch. One inference, zero tool calls,
23.141 seconds total, minimum sampled available RAM 3.888 GiB; model and SDK owners
released, cleanup_pending=false. No retry or second inference was sent. Raw model
arguments/reasoning were not retained, so the exact offending tool/field is not
proven. Do not guess a cause or relax schemas to conceal this failure.

The existing two-tool live PASS in PR #31 remains separate evidence. Four-tool
live readiness is **BLOCKED pending a diagnosed and reviewed fix**. No new default launcher is installed by this extension. For reproducible
four-tool runtime inspection without live inference:

```bash
PYTHONPATH=src .venv/bin/python -B -m agent.read_only --backend mock --scope diagnostics \
  --question 'Kiểm tra nginx, ổ đĩa root, unit failed và cổng TCP 80' \
  --report four-tools-new-name.md
```

Runtime reports, PID files, sampled host metrics and test logs stay outside Git.
This is a Draft integration boundary, not 12-tool acceptance or production-ready.

### Safe invalid-argument diagnostics

The Adapter now attaches a fixed category to INVALID_ARGUMENTS (unknown tool,
non-object arguments, malformed call, required/extra argument, type/value/schema
failure). Parent/worker accept only the enum corresponding to that primary code.
No arbitrary model tool name, field path, argument value, prompt or reasoning
crosses IPC as diagnostic text. Admission and validation behaviour are unchanged.
Mock HTTP tests prove extra arguments are rejected before dispatch, and show the
worker emits only `{ok:false,code:INVALID_ARGUMENTS,reason:UNEXPECTED_ARGUMENT}`.
This improves the next authorized investigation; it does not retroactively
identify the failed live call or claim a behavioural fix. No further inference
was run after the failed four-tool session.
