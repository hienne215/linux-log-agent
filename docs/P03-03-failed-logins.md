# P03-03 / SCRUM-32: failed_logins

Latest review follow-up: [journal permission classification and measured call
timing](evidence/p03-03-review-fixes-20261118.md), 165 author-run tests PASS.

Status: In Progress, design/implementation candidate, not accepted or deployed.
Owner: Trang. Support/review: Thủy; Hiền reviews Agent integration mapping.
Jira: https://vku-team-etxjrjs2.atlassian.net/browse/SCRUM-32
Jira requirements below were supplied verbatim by Trang because the connected
Jira tenant does not grant access to this site. No Jira status was changed.

## Scope and dependency gates

Read journal or auth.log, count failed logins by window/stable IP pseudonym,
avoid duplicate counting, make fallback explicit, keep raw IPs local and never
generate host brute-force attempts. Deliver the tool implementation and fixtures;
verify journal/auth fixtures, duplicate records, rotation and stable pseudonyms.
P03-01 and P01-01 must be accepted before final task acceptance. This candidate
started from main `f9adf8f`, with merged canonical PR #6 schemas. After Thủy's
design-direction response supplied by Trang, main `f2a8f71` was adopted through
normal merge `5030183` without conflict or history rewrite. That main includes
scaffold #14 squash `a0206a1` and permission task #13. No unfinished OOM #21
history is imported. Merged prerequisites do not certify this collector's binding.

New isolated modules:

- `src/mcp_server/failed_login_events.py`: parsers, reconstructed public events,
  HMAC pseudonyms, local per-IP count helper.
- `src/mcp_server/failed_login_sources.py`: fixed, bounded Linux reads.
- `src/mcp_server/failed_logins_adapter.py`: canonical envelopes, fallback,
  immutable sanitized snapshot paging and validation against current schemas.
- `src/mcp_server/failed_logins_owner.py`: persistent-owner and fixed-path key
  loader candidate, isolated from #14; not production-registered.
- `tests/fixtures/p03_03/`: synthetic journal JSONL and ISO auth.log records.
- `tests/test_p03_03_failed_logins.py` and `tests/test_p03_03_owner_policy.py`:
  reproducible automated evidence.

No shared runtime, entrypoint, schema or Agent file is changed. This is a callable
tool/backend implementation, **not yet registered in the published MCP server**.
No fresh-process JSON worker is added: cursor state must outlive a single call.
The parser uses Python 3.8-compatible syntax; adapter validation requires the
project environment (tested on Python 3.12), not the VM's bare Python 3.8 install.

## Fallback and coverage

The provider returns exactly `journal.auth` and `auth.log` coverage entries.
The adapter chooses one authoritative source for a frozen result set. Use journal
if readable, uncut, parsed completely and its oldest retained timestamp is known
and at/before since, even when zero failures matched. Otherwise select readable
auth.log. If auth.log also fails but journal is readable, retain journal's partial
results. Never union both copies of the same auth event. Missing/denied/unknown/
late journal history, parse gaps or acquisition cuts trigger fallback selection;
an empty event list alone never does. No fallback source gets invented coverage.
Evidence.source identifies the selected source; coverage preserves both statuses.
This avoids cross-source double counting without guessing an event's identity
from IP/time. The tradeoff is no history union: unknown/incomplete source history
remains partial, rather than pretending another source repairs every coverage gap.

Acquisition and selection are distinct: the current candidate probes both fixed
sources only in a deployment authorized to read both; selecting journal does not
mean auth.log was skipped. `LinuxLoginSources.acquisition_trace` records bounded
local enum-only tuples (source, attempted, reason), including a source not tried
because the shared read budget expired or the platform is unsupported. No paths,
raw values or key bytes enter the trace. Public coverage uses existing canonical
statuses only: inaccessible/not-attempted sources are unavailable with unknown
retention, never fabricated permission_denied/ok. The current canonical coverage
object has no attempted/reason fields; do not add unreviewed fields. Exposing the
distinction to Agent beyond existing statuses requires separate contract-owner
agreement. This representation is explicitly a review point, not approved.

- No readable source: safe PERMISSION_DENIED if both deny, otherwise UNSUPPORTED.
- Some source missing/denied/unknown retention/incomplete: partial, even zero events.
- Complete readable coverage for both sources, no cuts: ok.
- Acquisition cut: partial/OUTPUT_LIMIT/truncated=true, next_cursor=null. No fake
  cursor claims to recover discarded source bytes. Narrow the window or improve
  trusted acquisition in a separately reviewed follow-up.
- Bad inputs/cursors: safe placeholders, no reflected metadata/secrets; provider
  is never called for a rejected request. Valid source errors preserve IDs.

Journal reads the retained auth-facility records, not just matching login events;
oldest coverage derives from retained record timestamps. Auth file coverage is
conservative: current and .1 alone cannot prove full retention, so oldest is null
and coverage is incomplete. No claims of complete compressed-history support.

## Parsing, identity and rotation

Supported authoritative failures: sshd Failed password/publickey/keyboard-
interactive/pam, and PAM authentication failure for sudo/login/su. Accepted SSH
successes and Invalid-user context lines are not failures. sshd PAM companion
records are not counted twice; PAM-only sshd failures are currently unsupported
and mark the journal snapshot incomplete. Do not claim every login format works.

Journal record identity is __CURSOR. Duplicate delivery of the same cursor counts
once, while distinct cursors retain repeat attempts even in the same second.
Fixed auth paths are `/var/log/auth.log.1` then `/var/log/auth.log`. Open with
O_NOFOLLOW/O_NONBLOCK, require root-owned regular files, bound reads and compare
fstat metadata before/after. File-record identity uses device/inode/ctime witness
and byte position. An identical device/inode/ctime exposed twice is read once;
same inode with a different ctime is retained as a distinct generation, not
silently discarded. ctime is a conservative witness, not a filesystem generation
number; rotation ambiguity still marks coverage incomplete. Equal text in distinct files is **not** sufficient proof of identity;
retain both and report incomplete rather than dropping real failures. Copytruncate,
missing/rotated-away files, compressed history and yearless syslog prevent full
coverage claims. Operator supplies the traditional syslog year and timezone;
no implicit host timezone/year inference is accepted. DST/year rollover needs
operator review; traditional records always remain incomplete in this version.

## Privacy and local configuration

`Privacy(key, scope)` requires at least 32 secret bytes and an evaluation scope,
both operator-owned configuration. Use a random, locally provisioned key;
fixtures use an explicitly synthetic key only. Key provisioning/storage and read
permissions require P03-01/deployment review; no insecure default key or key file
is created. The candidate loader uses the fixed path
`/etc/linux-log-agent/failed-logins/pseudonym.key`: walk directory file descriptors
with O_NOFOLLOW, require root ownership/no group or world writes on parents;
dedicated directories should be root:<reader-group> 0750. The file must be a
root:<reader-group> regular file, exactly 0640 and exactly 32 random bytes;
reject symlinks, wrong ownership/permissions, missing/short/unstable keys. Admin
provisions it outside tool/runtime; no key in argv, MCP metadata, logs or Git.
No installation command is executed here. Same key+scope keeps HMAC-SHA-256 IP pseudonyms stable across calls and
restarts. Changing scope or key deliberately unlinks them. Normalize IPv6 text;
reject hostnames, control characters and interface-zone identifiers as addresses.
There is no reverse map persisted. Raw source data stays transient/local and is
never included in public events, errors, cursor snapshots or reports.

Rotation proposal: administrator atomically replaces the provisioned file, then
controlled restart (preferred) or trusted owner.rotate(load_privacy(...)) starts a
new key epoch. New calls never auto-reload key bytes mid-snapshot. Rotation is
serialized with calls, clears all old cursors, then installs a fresh adapter;
missing/invalid replacement closes the owner instead of reverting to an old key.
Explicit re-query is required after rotation; pseudonyms from different epochs
must not be joined automatically. Python object references are not secure memory
erasure. Same-UID processes may read the provisioned key: this does not claim OS
isolation from another compromised process under the same reader UID.

Public user is always `[REDACTED]`. user_class is root/existing/unknown based on
the recognized log form, not an OS account query. Service is a fixed enum and
messages/excerpts are reconstructed from fixed templates. Never copy raw kernel,
PAM, username, IP, path or command text into evidence. An unrecognized format
cannot silently count as proof of no failures.

Per-IP/window stats are available through the local aggregate helper; do not add
unreviewed stats fields to the canonical wire schema, which returns events/count.

## Cursor implementation and resource bounds

Proposed persistent owner: one FailedLoginsOwner attached to the dedicated stdio
server connection, created by a future trusted binder. Binder supplies the trusted
principal and application session binding, not fields copied from raw MCP JSON.
The owner contains one FailedLoginsAdapter across multiple calls, not one per
WorkerRunner.run() subprocess. Access policy is mandatory and rechecked on every
page. Revocation clears snapshots and returns POLICY_DENIED; restoration cannot
revive old tokens. Close on EOF/restart/disconnect clears state and refuses new
work. Owner rotation/close are locked against concurrent calls. Standalone tests
verify these methods; **no existing server EOF/cancellation hook or authenticated
principal acquisition has been integrated or demonstrated through SDK traffic**.

The future binding must also decide the acquisition execution boundary. The
current standalone provider performs bounded reads directly when invoked; do
not silently put it on the shared async dispatch path or claim it uses #14's
per-call WorkerRunner. A worker-to-owner bounded sanitized snapshot protocol,
async scheduling/cancellation and exact SDK frame/cleanup behavior need a
separately reviewed integration design. Neither a new service nor a socket nor
a shared server dispatch change is introduced by this candidate.

Instantiate one FailedLoginsAdapter per dedicated server process/session owner.
Call `.call(validated_raw_request)` and wrap the complete returned envelope using
the existing native structuredContent/text/isError consistency rules. Shared
server registration is a separate reviewable integration change, not performed
by this branch. Do not instantiate the adapter in a fresh worker for every call.

First call freezes sanitized events and coverage. Follow-up pages never reread
rotated or retained logs. Random opaque tokens bind tool, normalized UTC window,
session_id, effective limit and offset. TTL is 300 monotonic seconds from initial
creation and does not renew. Forged/mismatched/cross-instance tokens reject;
known expired tokens return EXPIRED. Pruned tokens reject as unknown. Replaying a
continuation token returns the same records/next token, preserving each call's
metadata. Returned dicts cannot mutate the frozen cache. `.close()` drops state.
App session IDs are correlation, not proof of OS identity. Dedicated process
ownership/authentication and cleanup on EOF/cancellation still need binder review.

Acquisition: 8-second shared budget; journal stdout 4 MiB, stderr 4 KiB; auth files
4 MiB combined. Fixed journalctl argv, no shell/sudo. Child killed/waited on cap
or timeout, reader threads joined. File growth/change is incomplete, not atomic.
State: 1 MiB per sanitized snapshot, 8 MiB total, at most 256 live tokens.
Pages: at most requested 1-200 records and a conservative 24,000-byte local
payload budget. Smaller byte-bound pages can occur even with limit=200. The
server must independently enforce its **exact** 65,536-byte serialized native
JSON-RPC frame cap; the adapter budget is not a replacement for transport checks.
Resource rejection uses canonical OUTPUT_LIMIT errors, without claiming success.

## Verification and remaining acceptance

Pre-main-adoption author-run execution by Codex on Trang's behalf, Windows/Python 3.12.7
(not independent reviewer signoff):

```text
python -m pytest -q tests/test_p03_03_failed_logins.py tests/test_p03_03_owner_policy.py tests/test_tool_schemas.py tests/test_response_schema.py --basetemp=.p03-03-owner-final
153 passed in 2.84s
```

This includes 99 task tests and 54 existing schema tests. Historical 120-test
evidence remains separate; it is not relabeled as testing this follow-up.
Fixtures are synthetic,
including documentation-range IPv4/IPv6 addresses, not captured real auth logs.
Tests cover redaction/pseudonym scope and restart stability, source fallback,
no-readable-source errors, per-source retention, journal identity, file-rotation
identity, immutable pagination after retention change, >200 records, page-byte
bounds, malformed/mismatched/expired cursors, normalized filters, replay,
resource caps, fixed argv, bounded child stdout and timeout/reaping.
New cases cover explicit fallback triggers/covered-empty journal, key FD policy,
directory-fd fixed-path walk, key rotation failure, peer/session ownership,
permission revocation between pages, modelled EOF/close and inode reuse witnesses.
They use synthetic keys and mocked OS metadata, not actual VM key provisioning
or real ACL revocation. Evidence: `evidence/p03-03-owner-policy-20261117.txt`.

### Post-main adoption validation

Runtime/test source: merge `5030183c118d0d40d356643d03445b7f0089ecd0`, main
`f2a8f71b209c71349a9ba8d6f00ce5e380fac1dd`. Subsequent review-publication changes
are documentation/evidence only. Windows/Python 3.12.7, MCP 2.3.0, pinned packages.

- Full selected run from OneDrive: **550 PASS, 1 FAIL**, no errors/skips, 389.24s.
  Scaffold `oom_bad_source` expected EXECUTION_UNKNOWN but received TIMEOUT.
- Focused unchanged four-case recheck: **4 PASS**, 21.42s.
- Separate unchanged scaffold run from OneDrive: **60 PASS, 4 FAIL**, 267.51s;
  worker timeout-related assertions. These failures are preserved, not hidden.
- Exported identical committed source to an ASCII directory outside OneDrive;
  task + entire scaffold: **163 PASS**, no errors/skips, 176.60s. No timeout,
  assertion, fixture or source change was used to obtain this result.
- Existing mock SDK stdio: **12/12 PASS**. This does not exercise the unregistered
  real failed_logins endpoint. Generator check and diff check: PASS.

No claim that a single full 551-case run passed. Path/environment correlation
is observed; the root cause of the intermittent worker timeouts is not proven
and no shared scaffold fix is claimed. Source review may proceed as Draft, while
runtime reliability and real binding/VM acceptance remain open. Durable logs,
all JUnit files, environment/source blob IDs, summary and integrity manifest are
under `docs/evidence/p03-03-main-validation/`. These are author-run artifacts,
not independent attestation or GitHub CI. No real auth records/secrets are used.

Thủy's latest design-direction response was supplied by Trang in chat: retain
schema 1.0.0, keep attempted/reason in enum-only internal trace, use admin-managed
HMAC keys and a connection-lifetime persistent owner; publish a separate exact-SHA
PR for source review. This is not source/SDK/Linux approval. No GitHub comment is
invented as evidence of that private response.

Still required before acceptance: independent Thủy/Hiền source/runtime review,
deployment key/reader policy for this tool, scaffold registration
with exact SDK framing/lifecycle checks, and a reviewed VM smoke under the reader.
No VM auth reads, password attempts, brute force, sudo/group changes, key
provisioning, service changes, GitHub publication or Jira Done performed here.
