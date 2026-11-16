# SCRUM-28 — synthetic action proposal/review CLI

Stacked on PR #9 (`ab05df010a5dc95faf2adbfa6ee997387d2dbc1e`), which includes
Coordinator #8 and MCP Client/ToolBridge #7. No dependency on #16. This module does
not use MCP contracts, restart_service, control/helper sockets or a tool executor.
There were no existing proposal/action types on this base; these enums/records are
local UI state, **not a new SCRUM-12 wire contract**.

## Run from repository root

Use the prepared Python 3.12 environment/dependencies documented for the existing
CLI. From a real Ubuntu terminal, with stdin/stdout on the same TTY:

```bash
PYTHONPATH=src python -B -m agent.cli.actions --scenario port-conflict --timeout 30
PYTHONPATH=src python -B -m agent.cli.actions --scenario no-evidence
```

UI shows SYNTHETIC DEMO, application-generated proposal ID, nginx.service, proposed
restart, fixture reason, interruption risk and evidence IDs/sources from the existing
port-conflict fixture. These are fixture references, not newly collected Linux
evidence or proof a restart would fix port conflict. No-evidence mode says so.

Type exactly `DONG_Y <displayed-id>` or `TU_CHOI <displayed-id>` then Enter.
Only one response is accepted; invalid input rejects without re-prompt/retry.
Input is not echoed. Do not type credentials. Defaults: 30 seconds; maximum 60.

States: proposed -> approved / rejected / expired. Terminal decisions do not
transition or prompt again. approved means **local UI choice only**, never executed
or authorized. No approval token, grant, trusted approver identity, backend state
or audit event is created. Nothing is written to Incident Report or disk.

Exit 0 means local demo choice approved, 1 means denied/expired/terminal failure,
2 means invalid CLI arguments, 130 means Ctrl+C. Every outcome has executed=false.
This exit code is **not an approval API**; no service may trust it as authorization.

## Safety / ownership

- Only application-owned fixed fixtures are accepted. No arbitrary unit, command,
  model reason, approval token, --yes flag or AUTO_APPROVE environment behavior.
- Reject non-TTY stdin, non-TTY stdout and different terminals without reading input.
  Queued/type-ahead bytes are flushed before displaying the confirmation prompt.
- One bounded ASCII line, exact current random ID; wrong/stale IDs, Unicode,
  overlong input and plain yes cannot approve. EOF/Ctrl+C/error reject; expiry uses
  monotonic time and is checked again after input AND terminal restoration.
- Echo is disabled before the prompt; untrusted entered bytes, raw exceptions and
  invalid argument values are never printed. Terminal attributes and selector are
  restored/closed in the same call, without worker/thread/background task in the UI.
- Proposal retains terminal restoration ownership if cleanup fails. confirm rejects
  and leaves cleanup_pending; trusted callers must retain the Proposal and call
  finish_cleanup(). CLI makes one bounded restoration retry and reports pending if
  still unsuccessful; it never upgrades a rejected decision to approved. A persistent
  OS/terminal failure cannot guarantee restoration; there is no hard wall SLA.
- There are no model/executor/client owners in this isolated command. An eventual
  diagnostic integration must keep their lifecycle outside this UI; do not call
  this blocking TTY reader on an async event loop.

TTY checks do not authenticate a human. Same-UID programs can drive a pseudo-TTY;
read-plane/approver UID isolation, authoritative proposal retrieval over a protected
control channel, session/VM/expiry binding, peer identity, durable audit, replay
protection and grants require the reviewed backend. This UI provides none of those
production guarantees and cannot be used to bypass them.

## Tests / fresh evidence — 2026-11-15

```bash
PYTHONPATH=src python -B -m unittest discover -s tests/action_cli -v
PYTHONPATH=src python -B -m unittest discover -s tests/cli -v
python -B tests/agent_loop/run_tests.py
```

Python 3.12.16: **115 PASS, 0 FAIL/SKIP**: 16 action UI tests, 35 CLI regressions,
64 Coordinator/acceptance/report regressions. Action tests use actual pseudo-TTY
reads/echo restoration and a Python CLI subprocess for approve/non-TTY; cancellation
and restoration failure are injected offline. Test-driver threads/processes are
joined/reaped. PTY tests verify terminal policy, not human presence or production
permission isolation. No live Ollama, MCP/Linux diagnostics or privileged actions.

## Blocked production work

Final contracts/signoff, authoritative approval coordinator, control socket/user
policy, privileged helper, VM/security acceptance and execution/audit are pending.
SCRUM-29 is not implemented. This is a Draft synthetic UI foundation, not SCRUM-28
Done, production approval or a restart demo. Existing diagnostic CLI is unchanged.
