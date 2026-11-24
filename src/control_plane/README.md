# Approval/helper candidate and isolated VM runtime

Separate from Agent, MCP and the read-only CLI. `runtime.py` now provides an
opt-in lab-VM service wrapper; `command.py` provides `linux-log-action`. The
default Agent command never starts these services or gains their permissions.
See [VM deployment](../../deploy/lab/README.md) for fixed paths, identity/key
requirements and limitations. The internal grant format is a review candidate,
not a change to MCP schemas. No production security or real restart acceptance
is implied by mock tests, installed units or successful read-only observations.

Approval and execution are separate explicit operations. The existing review
TTY requires `DONG_Y ACTION_ID`; execution needs a new approved-state view and
a second fresh `THUC_THI ACTION_ID` confirmation. Approval never automatically
calls the helper. Root's VM-only `write_enabled=false` blocks execution at both
ends. Timeout/lost delivery is not a failed or rolled-back restart, and is never
retried. The runtime bounds accepted connections, owns every handler, pins kernel
peer UIDs and joins helper/bus cleanup before closing its durable ledger.

The core-module descriptions below describe their responsibilities in isolation;
listeners, account provisioning and the bus factory are supplied only by the
explicit lab runtime/deployment, not by importing those modules.

- `approval.py`: bounded pending actions, operator/session ownership, exact
  `nginx.service` scope, expiry and private HMAC-bound grants. Concurrent
  confirmations cannot mint two grants for one pending action. Optional
  `store=CoordinatorState(...)` persists decisions/audit before releasing a grant.
- `state.py`: opt-in private coordinator state and audit in one SQLite
  transaction. One live owner per file. Opening a new generation expires old
  pending actions and invalidates sessions; approved/denied decisions remain
  terminal. Approved units stay locked, with no automatic reconciliation or
  grant recovery. Status remains `executed=null`, not a successful restart.
- `storage.py`: shared coordinator/helper SQLite file checks: owned directory
  `0700`, regular single-link file `0600`, no symlink following, identity recheck,
  8 MiB limit, FULL commits and directory fsync. It does not provision directories,
  change permissions, install services or promise power-loss/hard-wall guarantees.
- `ledger.py`: private SQLite intent/audit transaction, replay protection and a
  conservative per-unit lock. A live owner can record one observed systemd job
  result after consuming intent. Unknown stays unknown across reopening; a new
  owner cannot finalize the old intent. Observations never unlock the unit.
- `systemd.py`: optional low-level job observer. It takes ownership of an
  already-connected bus, pins systemd's unique owner, subscribes before dispatch,
  and correlates `JobRemoved` by exact job ID/path and unit. One fixed
  `RestartUnit("nginx.service", "fail")` call, no interactive authorization,
  autostart, reconnect, retry or `CancelJob`. The internal API accepts no command,
  unit, executable, endpoint or model-supplied authorization. No bus connection
  factory in this module; the opt-in VM runtime owns a system-bus connection.
- `helper.py`: optional single-owner runner candidate. Verify grant, prepare the
  observer, persist intent, recheck grant expiry immediately before dispatch,
  then persist the observed outcome before returning it. A pending cleanup owns
  its task and blocks another request; the service owner must call
  `finish_cleanup()` until joined.
- `handoff.py` and `service.py`: complete internal handoff over authenticated,
  already-connected channels. Match helper epoch/environment/policy before
  delivery; take the approved grant out of the private outbox before sending
  once. Lost delivery retains local pending correlation, with no retry. The
  helper owns one admitted job independently of the request connection and
  exposes a fixed-code status view without grants/keys. Admission is unknown,
  not completion or a durable intent. Shutdown stops admission, retains the
  task on timeout/cancellation, and joins both operation and bus cleanup.
  No listener, process identity, socket path or privileged service is installed.
- `transport.py`: internal Linux Unix-stream framing for an exclusively owned,
  already-connected socket. Both ends must pin the expected UID from trusted
  configuration; the channel authenticates it with kernel `SO_PEERCRED` before
  reading. It permits one send and one receive, at most 4096 bytes/message, and
  one total connection deadline (default 2 seconds, maximum 5). This is not MCP
  framing and does not modify the canonical transport's 64 KiB limit.
- `protocol.py`: exact-field proposal/control handlers. The application selects
  the port; reader peers can only propose/query with matching sessions. Operator
  peers can open sessions, review, approve or reject their own actions. Grants
  stay in a bounded private in-memory outbox, never in responses. There is no
  helper-delivery method or wired executor. Every view has `execution_enabled=false`
  and `executed=null`. At most 16 evidence references fit the internal UI route;
  larger records are rejected, never silently truncated. This does not change
  canonical MCP schemas or the policy core's separate reference limit.
- `review.py`: operator client pins coordinator UID, validates the entire view,
  displays fixed action/risk labels and reads an exact action-ID confirmation
  from a TTY. Pipe input, queued confirmations, EOF, cancellation, expiry, wrong
  action IDs and terminal failures do not approve. It owns a duplicated terminal
  fd until restoration completes; closing a caller's stdin cannot drop cleanup.
  No raw reason/log/model text, approval token or grant is accepted by the UI.
- Tests: `tests/control_plane/`. They inject synthetic peer UIDs and grants;
  filesystem/concurrent transaction tests use only test-owned temporary files.
  Transport tests additionally use real local Linux sockets and a test-owned
  Python subprocess. They verify actual peer UID/PID, framing, bounded timeout,
  EOF, cancellation/backpressure and child reaping. They do not establish
  different-UID isolation, production socket permissions or human presence.
  Additional PTY/socket tests connect backend views, typed confirmation and
  policy transitions. They use synthetic non-current identities for roles not
  actually running in the test; this is not multi-UID deployment acceptance.
  State tests additionally cover abrupt exit of a test-owned Python process,
  concurrent approval/open, audit failure, expiry during persistence/signing,
  fsync failure and inconsistent persisted evidence/audit. No service is restarted.
  Helper tests use real `dbus-next.Message` objects with a fake bus only: no
  `MessageBus` is constructed, no system bus is connected, no systemd method is
  sent, and no service state is changed.

The channel creates no task or subprocess. Use it in an `async with` block in
the I/O-owning task. On cancellation/error it closes its fd before returning;
cancel and join an outstanding operation before external close. It rejects
concurrent operations rather than dropping the owner. A started send remains
delivery-unknown, even if all bytes were sent; only a validated application
response can establish action state. No retry/reconnect or execution inference
is provided. Message bodies remain untrusted and require exact handler schema
validation; a JSON `uid`/`approved` field does not confer authority. No listener,
control socket path, command or service is installed. Agent/MCP do not import
these modules. `ApprovalEndpoint.serve()` must be called only by an accepted
service that owns accept limits, sockets and all handler tasks.

The operator client consumes each local confirmation before sending. A lost
response or cancellation cannot resend approval automatically. The server may
already be approved: its grant stays privately owned; query status explicitly.
The operator client cannot deliver the grant or perform a restart; only the
separate application-owned handoff can consume the private outbox. UI deadlines
include request time and the backend rechecks its monotonic TTL. A restored
terminal or an existing citation ID is not proof of independent human review.

### Persistent decision semantics

The accepted service must explicitly construct a `CoordinatorState` from its
administrator-configured private directory and codec, pass it as `store` to
`ApprovalCoordinator`, then own/close it after joining all handlers. The default
coordinator is still in-memory for offline tests; persistence failure never falls
back to that mode. No command, path, UID or key is read from a model proposal.

Audit/state are committed together before grant signing. TTL is rechecked after
the database lock, after sync and after signing. If an approval is committed but
its grant cannot be released before expiry, the decision stays terminal; no
replacement grant is minted. Commit/fsync uncertainty disables that generation.
Opening the file again checks schema/policy, bounded event count, transitions,
correlation, peer roles and immutable action/evidence-reference digests. Missing
or inconsistent history fails closed. These are local consistency checks:
self-stored hashes are not independent reviewer verification, semantic evidence
correctness, or protection against a malicious owner/root rewriting the database.

### Explicit expired/no-intent reconciliation (private lab only)

An administrator can explicitly run `python -I -B -m control_plane.reconcile
ACTION_ID` as root **inside linux-log-lab-hien only**, while execution is disabled,
both control services are inactive and their sockets are gone. This is not an
Agent/model/reader route. It requires an already expired approved action and
zero helper attempts, audit entries and outcomes. Any intent, unknown state,
live owner, unsafe database or existing attestation denies reconciliation.

The command does not edit/delete either database. It creates one protected
root-owned attestation under `/etc/linux-log-agent/reconciliations`, binding the
immutable action digest, original expiry, observation time and helper file hash.
On the next coordinator generation, the original action becomes `expired` with
an appended `reconciled_no_intent` audit event, attributed to root. The prior
proposal/approval are retained. Removing or altering that proof fails subsequent
audit validation. Unrelated policies do not read this administrative directory.

No grant is recovered/reissued, helper lock cleared, execution inferred or retry
performed. A new proposal needs fresh evidence, a new session/action and two new
human TTY confirmations within the existing total 60-second lifetime. This
exception cannot reconcile an action with any consumed intent, even if its job
result is missing. Normal approved actions stay conservatively locked.

There are at most 128 retained actions and 8192 audit events; full storage returns
an error, without deleting history. No retention/migration/unlock API is provided.
The unmerged candidate format is not an approved deployment/migration contract.
SQLite calls are synchronous and may exceed a network/TTL budget during disk I/O;
this candidate does not claim a hard wall SLA. It starts no detached storage worker.

```bash
PYTHONPATH=src python -B -m unittest discover -s tests/control_plane -v
```

The optional helper tests require `dbus-next==0.2.3` (the `control` package extra)
in an isolated Python environment. Without it, only those tests explicitly skip;
the read-only Agent and other control-plane modules do not import the binding.
This dependency was exercised with mock messages on Python 3.12.16, not accepted
for a root deployment. Its own disconnect implementation may emit OS warnings;
library logging/privacy behavior still needs deployment review. Our wrappers
return fixed error codes, never raw reply/error bodies or grant bytes.

### Job observations and ownership

A returned job path means systemd accepted a job, not that it finished. Only a
matching, trusted-owner `JobRemoved` can establish a job result. `done` is recorded
as `outcome=succeeded, executed=true` **for that job**, not service health, HTTP
availability or a verified fix. `failed`/`dependency` record a failed job with
`executed=null`; `canceled`/`timeout`/`skipped`, transport loss and expiry remain
`execution_unknown, executed=null`. Cancellation does not prove that systemd
canceled the operation. Even a completed job leaves the durable unit lock held.

The request budget is at most 30 seconds; cleanup has a separate budget (default
1 second, maximum 5). A slow close retains its cleanup/watch task and bus owner.
Caller cancellation propagates after recording unknown when intent was consumed;
cancellation while closing cannot discard the existing owner or its observed
result. Repeated cleanup joins never resend a method. A late reply is rejected
after the deadline. A cancellation-resistant binding can delay return; this is
not a hard wall SLA. The accepted service must retain the runner, join cleanup,
then close its ledger. Never drop a runner with `cleanup_pending=true`.

Result persistence failure withholds success and fences the runner. Crash or
fsync uncertainty cannot authorize replay or automatic reconciliation. Sync
storage still runs in the owning task, without an abandoned background worker.
The outcome table is an unmerged candidate format, not a migration contract.

API basis: [systemd Manager/JobRemoved](https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.systemd1.html),
[dbus-next asynchronous bus](https://python-dbus-next.readthedocs.io/en/latest/message-bus/aio-message-bus.html).
The fixed `fail` job mode and all privileged deployment choices require owner and
security acceptance before this candidate can be connected to any write route.

Not production-ready. Remaining acceptance requirements:

1. Independent review of serialization, policy and crash semantics.
2. Recheck the specific deployment's identities, protected key/state locations,
   peer UIDs and hardening. A VM probe is not production/host acceptance; a UID
   from request JSON never grants authority.
3. Independent review and deployment acceptance of persistent decision/audit and
  the control/TTY flow. Grants remain in a volatile private outbox with no
  recovery/redelivery. The VM runtime supplies the route with dispatch disabled
  by default. Durable decisions alone do not authorize execution; real human
  approval, helper execution, reconciliation and retention still need acceptance.
4. Root-owned unit/dependency acceptance, helper epoch handshake, independent
   review of the candidate job driver and real VM job/cleanup evidence. Installed
   candidate services and observer-only D-Bus probes do not establish a successful
   restart. No Agent/MCP write route is wired by the lab deployment.
5. Recheck expiry/policy immediately before execution after intent is durable.
   Failure after consume stays unknown; do not retry or release the unit lock
   based on a model conclusion. Reconciliation/recovery requires separate review.
6. VM negative/positive acceptance before wiring MCP propose/status. A grant is
   private coordinator-to-helper data and must never reach Agent, model or CLI.

No keys, grants, source log text or model reasoning are written to audit. Unit
tests do not prove deployed UID separation, key entropy, hardware durability,
independent human approval or a successful restart.

### Fresh post-action checks

`linux-log-action verify ACTION_ID --session SESSION_ID` is a reader-only
operation. The proposal socket authorizes the original session/action before
querying the helper's already-owned attempt. It cannot approve, submit, reconnect
or execute. Missing attempt, an unapproved action or pending helper cleanup blocks
the read; a current service state is never substituted for a missing job outcome.

The reader reloads its original canonical snapshot with bounded no-follow file
checks, validates schema/correlation/provenance and refuses altered or sensitive
baselines. After receiving job status it records a local UTC lower bound for one
fresh canonical `verify_fix` call. This is not a fabricated job-completion time.
The existing HealthBinding/SDK validate the three checks, references and evidence;
cleanup retains the SDK owner even after repeated cancellation. External baseline
references remain separate from new observations.

The existing Incident Report renderer escapes/redacts both ledgers and the report
writer publishes a private 0600 file without overwrite. Job outcome and fresh
check result are separate fields. With incomplete journal coverage the result
remains partial/unknown and `repair_verified=false`, including after a done job.
No model is invoked, no action is retried, and no unit lock is cleared. A failed
read/validation creates no successful verification report. Tests use synthetic
jobs and collector IO; the real after-restart path still needs a human-authorized
VM action and fresh runtime evidence. Restart remains disabled in the lab config.

### Canonical MCP proposal/status route

The application-owned `agent.proposal_server` exposes canonical proposal/status
only. Its reader route never accepts grants, approves, executes, changes unit
locks or calls a helper directly. `proposal_context` authorizes the original
operator session and action before the approval service reads an existing helper
attempt. A restarted helper may read its durable ledger without reconstructing
an execution owner or recovering a grant; missing or uncertain attempts remain
unknown. The existing `verification_context` remains separate.

One human-confirmed VM restart and fresh postchecks were completed on 23 November.
The job succeeded; incomplete journal coverage kept repair verification unknown.
Execution is relocked. Earlier statements about pending real-job acceptance above
are historical candidate prerequisites, not a claim that all independent security
or deployment reviews have been completed.
