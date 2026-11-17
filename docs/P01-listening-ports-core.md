# Isolated listening_ports core — Thủy

Branch: codex/p01-listening-ports-core. Base: PR #14
656f8c365a300270418e15b34ae1046d12fbcb44. Reference: PR #6
dc53ff6c3afd038f66e987bf1c7f7ad17f4b7829. Canonical request/data schemas
are identical at these revisions. Core only, not complete MCP or Linux acceptance.

## Operation and parser

`await collect_listening_ports(protocol=None, port=None, unit=None)` returns
canonical ports/count or typed `ListeningPortsError`. Fixed trusted read profile:

```text
/usr/bin/ss --no-header --numeric --listening --tcp --udp --oneline
```

No process flag, resolver, arbitrary executable/options, shell, sudo, helper, network
probe/connection or write operation. ss reads local kernel socket diagnostics; no
remote endpoint is contacted. Only LC_ALL=C/LANG=C are passed; no inherited secrets.
cwd=/, stdin DEVNULL, shell=False. Both protocols are always queried; filters cannot
alter argv. See the [ss manual](https://man7.org/linux/man-pages/man8/ss.8.html).

`parse_listening_ports(bytes, protocol=None, port=None, unit=None)` accepts the fixed
six-column Netid/State/Recv-Q/Send-Q/Local/Peer profile without headers/extra process
fields. Strict UTF-8 and printable lines, finite bounded decimal queues, numeric IP
addresses and local ports 1..65535; malformed, truncated or localized output rejects
the whole observation even if a bad record would be filtered out. Nonempty input
must end in newline. Empty bytes alone are only a parsed list; collection additionally
requires exit 0 and empty stderr. Any diagnostic/nonzero exit is a safe typed error.
Source-level audit is limited to upstream iproute2 **v5.5.0**:
[ss.c](https://github.com/iproute2/iproute2/blob/v5.5.0/misc/ss.c), specifically
sock_state_print, inet_addr_print, sock_addr_print and final column selection.
With both TCP/UDP and LISTEN/UNCONN selected, Netid/State columns stay enabled;
no -p/-e/-i/-o options add trailing metadata. This supports the fixed profile only,
not every distribution build/version. Actual ss binary/grammar/permissions still
require Linux smoke; unsupported layouts reject rather than guessing fields.

IPv4, bracketed/unbracketed IPv6, mapped IPv6 and wildcard '*' are supported. IPs
are normalized with ipaddress; '%' interface scope is validated and preserved,
including IPv4 interface labels. Accept both [fe80::1]%eth0 and
[fe80::1%eth0], normalizing to fe80::1%eth0; ambiguous duplicate scopes reject.
'*' and '*%eth0' retain wildcard/interface meaning: do not guess address family or
dual-stack behavior. Local wildcard IPs 0.0.0.0 and :: retain their distinct meaning.
Peers must be unspecified address/'*' and wildcard/zero port. No hostnames or service
name lookup. Queues are not forwarded. Sorting by protocol/address/port makes
canonical data deterministic without inventing socket identity.

## UDP and attribution policy

TCP rows require LISTEN. UDP rows require UNCONN, nonzero local port and wildcard
peer: this is an observation of a bound unconnected datagram socket. It is NOT
proof of an application serving traffic, service health, reachability or ownership.
Connected UDP/other unexpected states invalidate the snapshot, never become proof
of a listening service or disappear silently.

The command deliberately does not query process metadata. Every record uses
process="" (no attributed process name) and unit=null, both permitted by canonical
R3. Never infer nginx.service from port 80, process name, PID or an empty field.
There is no PID or guessed '[unknown]' process name. The empty string is only an
absence-of-attribution value; consumers must not interpret it as evidence of no
owner. This conservative core does not establish production ownership semantics.

Valid unit filters always fail UNSUPPORTED before spawn, including caller claims
that a unit is known/verified. This core has no trusted attribution provider, so no
successful verified-unit-filter path is implemented. Malformed unit/protocol/port
filters fail INVALID_INPUT. None is an internal omission/default; explicit null is
not added to the canonical request schema. A future MCP boundary validates raw input.

Duplicate endpoint rows remain separate: SO_REUSEPORT sockets can have equal bind
tuples. Dropping them would invent socket identity. count is returned row count,
not a count of unique services or a system-wide total.

## Bounds, errors and lifecycle

TIMEOUT=5 seconds covers pending spawn/read/wait. Concurrent bounded readers retain
<=65536 stdout and <=4096 stderr bytes, with <=4096-byte reads and remaining+1
overflow detection; the extra byte is rejected without retention. Asyncio stream
limit=4096 provides backpressure. OS/asyncio buffers and temporary copies add bounded
overhead beyond retained payload; no universal total-process memory cap is claimed.
At most 200 raw rows, before filters. Any byte/row overflow raises OUTPUT_LIMIT,
never partial-success slicing. No cursor or incomplete canonical page is invented.

Pending spawn and owned child stay attached to cleanup on timeout/cancellation/cap:
kill, cancel readers, drain/discard and reap. No retry/reconnect. Cleanup can exceed
the collection deadline; no hard wall or universal descendant/process-tree guarantee
exists. Repeated cancellation, kill failure or forced worker termination needs
separate deployment supervision. Transport pattern is kept local to this file
because shared utilities are outside this task's three-file scope.

Static errors: INVALID_INPUT, UNSUPPORTED, PERMISSION_DENIED, TIMEOUT, OUTPUT_LIMIT,
EXECUTION_UNKNOWN. No raw stderr, submitted value, OS exception text, sensitive path
or traceback is printed or included in a business result. No envelope/evidence,
correlation IDs, worker/dispatch, production authorization or synthetic fallback.

## Validation and pending acceptance

Offline Python 3.12 tests use deterministic byte/pipe/process doubles. No actual ss,
Linux smoke, network activity, sudo or service mutation. Targeted command:

```text
python -m pytest -q tests/test_listening_ports_collector.py --basetemp=<writable-directory>
```

Remaining: independent review of absent process attribution and UDP interpretation,
real Linux ss grammar/access/namespace smoke, reviewed ownership provider for any
unit-filter success, finalized #6/#14 dependencies, MCP integration and evidence/
correlation/authorization/cleanup supervision. A silent kernel/bus policy restriction
cannot be disproved by an empty list; reader/deployment visibility must be verified.

Local verification: Windows / Python 3.12.7, **83 PASS, 0 FAIL, 0 ERROR,
0 SKIP**. Canonical data schema checks are in the targeted suite; three additional
empty/TCP/UDP samples validated against the exact PR #6 dc53ff6 schema. Linux smoke
NOT RUN; all byte/process/pipe observations are synthetic fixtures/doubles.

## Final source audit follow-up

The original 9fb0250 parser rejected the external-bracket scope and interface-bound
wildcard rendered by iproute2 v5.5.0. A normal follow-up fixes only endpoint parsing,
adds three source-format positive regressions and five negative scope cases, and
preserves the command, attribution policy, bounds and fail-closed behavior.
Regression before fix: 3 FAIL / 5 PASS; after fix: all targeted cases pass.

Fresh Windows / Python 3.12.7 validation: **91 PASS, 0 FAIL, 0 ERROR, 0 SKIP**,
separate from the historical 83 PASS above. Six empty/IPv4/IPv6/TCP/UDP/scoped
samples passed the exact PR #6 dc53ff6 response-data schema. Linux smoke NOT RUN.
