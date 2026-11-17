# P01-03 / SCRUM-17 — read-only service_status collector

Owner: Thủy. Status: **In Progress / local review candidate**, not independently accepted.
Current dependency for local adoption: pinned main
`f2a8f71b209c71349a9ba8d6f00ce5e380fac1dd`, containing PR #6 squash `f9adf8f`
(approved canonical revision `2502d44`), PR #14 scaffold squash `a0206a1`, and
merged P03-01 documentation. Branch: `codex/p01-03-main-dependency-adoption`;
original PR #22 branch/HEAD remain unchanged until a separately authorized publish.
Initial cefd523/656f8c3 dependencies describe the historical draft, not this
integrated candidate. Merged permission documentation does not itself grant
reader privileges or production deployment approval.
Only service_status is implemented here; no list_failed_units/query_journal, Agent
binder, restart, permissions provisioning or deployment changes.

## Read operation and policy

Default launch stays without real backends. `--fixtures` still enables the synthetic
service_status worker. `--linux-service-status` explicitly enables the separate Linux
worker; these flags are mutually exclusive. The executable/worker path comes from
trusted entrypoint code, never MCP arguments. Existing POSIX root refusal remains.

Only **nginx.service** is allowed by application policy. A different valid unit is
POLICY_DENIED before command lookup/dispatch; malformed units and extra fields fail
canonical validation. To extend this list, review code/policy rather than accepting
units, executable paths or command strings from tool arguments.

The collector resolves only /usr/bin/systemctl or /bin/systemctl and executes:

```text
/usr/bin/systemctl --no-pager --no-ask-password show --property=Id,LoadState,ActiveState,SubState -- nginx.service
```

No shell, PATH lookup, interactive authentication, start/stop/restart/enable,
configuration write or helper. `show --property` is the machine-readable read
interface described in the [official systemctl manual](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html#show%20%5BPATTERN%E2%80%A6%7CJOB%E2%80%A6%5D).
The executable path, service-manager availability and reader access still need VM
verification. OS code/binaries must be administrator-controlled; a same-UID Agent
is not isolated from mutable worker files by this Python policy.

## R3 mapping and privacy

No canonical schema changes. Successful data contains exactly unit, active_state,
sub_state, load_state, description. Reads of inactive/failed services are successful
observations, never substituted with active. No synthetic success fallback exists.

- Id must equal the requested canonical allowlisted unit. Aliases/mismatched IDs,
  unknown property names, duplicate/missing properties, malformed UTF-8 and unknown
  state values fail closed as EXECUTION_UNKNOWN; raw values are not reflected.
- Description is deliberately not requested: data.description=[REDACTED]. Evidence
  is reconstructed from allowlisted unit and known state enums, redacted=true.
  This avoids copying workload-controlled free text; it is not a general redactor.
- Evidence source is collector.service_status.systemctl.v1, unique random evidence
  ID, unit=nginx.service and observed_at=UTC snapshot collection time. It is not a
  service transition timestamp. started_at/completed_at/duration_ms record collection.
- Only a completed command plus validated properties can emit this real-source
  evidence. Tests using a mocked Linux API are explicitly simulations, not VM proof.
- A validated not-found LoadState maps to NOT_FOUND (including nonzero command
  return with complete matching not-found properties). Other nonzero exits/systemd
  bus failures map to UNSUPPORTED; permission diagnostics in forced C locale or OS
  PermissionError map to PERMISSION_DENIED. They never become inactive success.
- TIMEOUT / OUTPUT_LIMIT / EXECUTION_UNKNOWN use the existing safe error envelope,
  no raw stderr/exception, no evidence, truncated=false. Accepted success/partial
  correlation follows scaffold; service error metadata uses its safe placeholders.
- Unlike log tools, the service_status data schema has no coverage field. Missing
  access/data is represented by errors, not an invented coverage extension.
- Exit status/restart counters in architecture proposals are not added because
  canonical R3 does not permit those fields.

## Resources and lifecycle

Systemctl I/O budget: 5 seconds after spawn, stdout retained <=8192 bytes, stderr
<=4096 bytes. Concurrent bounded stream reads avoid stdout/stderr deadlock; errors
are static. Minimal C-locale environment, stdin DEVNULL, fixed cwd=/.

On timeout/cap/cancellation, kill only the owned read child, drain/discard pipes and
wait for confirmed exit. Pending spawn is shielded and retained for cleanup. The
Linux worker routes SIGTERM into task cancellation so the collector can reap its
child. Parent scaffold retains its existing 10-second worker deadline/limits.

This is not a hard wall SLA: OS spawn/kill/reap may exceed budgets. A forcibly
SIGKILLed worker, kernel-stuck child or repeated termination can defeat cooperative
nested cleanup; no universal process-tree/orphan guarantee or production sandbox
is claimed. The worker is a fixed read profile, does not spawn a shell/helper, and
never changes privileges. Deployment/process-group supervision requires separate
review before claiming stronger guarantees.

## Tests

`tests/test_service_status_collector.py`: injected Linux API responses, state/error
mapping, allowlist-before-execution, schema/evidence/time/correlation, redaction,
byte caps, timeout/cancellation/pending-spawn and owned-child cleanup using async
process doubles. Fixed argv and minimal environment are asserted.

`tests/service_status_stdio_fixture.py`: test-only simulated Linux API behind the
real SDK stdio + scaffold + isolated Python worker boundary; no real systemctl.
Production code exposes no test flag/platform override in MCP or CLI input.

The actual CLI worker on Windows returns UNSUPPORTED; tests do not pretend this is
Linux collection. No Linux smoke, real systemctl invocation, service mutation or
permission change was performed during implementation. See delivery logs for exact
PASS/FAIL/ERROR/SKIP and the final local candidate SHA.

## Handoff: Hiền's authorized read-only Linux smoke

Use an existing reviewed Linux reader account, Python 3.12 venv and immutable
checkout. Do not install/configure a service, change groups, sudo or restart it for
this smoke. If nginx is absent, NOT_FOUND is a valid observation; choose another
unit only after a separate allowlist review.

1. Launch via official SDK stdio: absolute venv python, args=[absolute
   run_mcp_server.py, --linux-service-status]. Do not paste diagnostic commands into
   MCP stdin or enable --fixtures.
2. Discover service_status; compare canonical input/output schemas. Call with:

```json
{"schema_version":"1.0.0","session_id":"linux-read-smoke","trace_id":"trace-1","logical_tool_call_id":"service-read-1","attempt_id":"attempt-1","unit":"nginx.service"}
```

3. Independently read the fixed systemctl show command above under the same reader
   identity; compare unit/load/active/sub. These are separate live snapshots and can
   legitimately differ during a transition. Record times/versions and differences.
4. Validate structuredContent and JSON text against canonical result schema and
   isError mapping. Check real source/observed_at only on success, no raw Description,
   error != inactive, and no automatic retry/reconnect. Check SDK disconnect/reap.
5. An additional request for sshd.service should be POLICY_DENIED without querying
   it. Do not simulate active/failed/permission cases by changing the host; those
   cases already have mocked tests. Mark any unavailable live scenario NOT RUN.

Minimal SDK smoke from the repository, using the already prepared Python 3.12
venv as the reader (this command has **not** been executed on Linux here):

```bash
python - <<'PY'
import asyncio, json, sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from jsonschema import Draft202012Validator, FormatChecker

async def main():
    params = StdioServerParameters(command=sys.executable,
        args=[str(Path('run_mcp_server.py').resolve()), '--linux-service-status'])
    validator = Draft202012Validator(json.loads(
        Path('schemas/tools/service_status.result.schema.json').read_text()),
        format_checker=FormatChecker())
    async with asyncio.timeout(30), stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=10) as session:
            await session.initialize()
            result = await session.call_tool('service_status', dict(
                schema_version='1.0.0', session_id='linux-read-smoke', trace_id='trace-1',
                logical_tool_call_id='service-read-1', attempt_id='attempt-1', unit='nginx.service'),
                read_timeout_seconds=10)
            validator.validate(result.structured_content)
            assert json.loads(result.content[0].text) == result.structured_content
            assert result.is_error == (result.structured_content['status'] == 'error')
            print(json.dumps(result.structured_content, indent=2))
asyncio.run(main())
PY
```

Return exact collector/scaffold/contract SHAs, Linux/systemd/Python versions, reader
UID/groups, the read-only response and canonical validation/cleanup result. Do not
publish raw credentials/logs. Label the evidence as Linux smoke only after actual
execution; local Windows tests alone do not finalize this task.

## Dependency-adoption evidence boundaries

[Hiền's readiness review](https://github.com/hienne215/linux-log-agent/pull/22#issuecomment-6950623314)
reports an earlier Ubuntu SDK NOT_FOUND path at published HEAD `8b530f5`; it is
not a positive observation of a running nginx service. That historical evidence
is not re-executed or relabeled by this Windows/offline integration. Preserve
exact SHAs and independent executor scope. Current candidate tests/logs and
versions are supplied separately; final independent review and appropriate
read-only Linux positive observation remain pending. Do not change service state,
allowlist or reader permissions to manufacture acceptance.
