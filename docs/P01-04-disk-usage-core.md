# SCRUM-18 / P01-04: isolated read-only disk_usage core

## Integrated main service_status + disk_usage candidate — 18/11/2026

Adopted approved main `af1c3f16bdde5b04b838b86ab0b72f8248647477` by normal
merge on the existing PR #24 branch. Main's service_status collector/worker/tests,
canonical schemas, scaffold/runner and security fixtures are preserved unchanged.
The disk core/worker/evidence mapping and existing tests are also unchanged from
`90c65374f60698eeb9286d22f581c719d8fa6fdc`.

Entrypoint profiles are explicitly **mutually exclusive**:

| Selection | Registered backend | Resource policy |
|---|---|---|
| No flag | None | Approved default scaffold runner |
| `--fixtures` | Synthetic service_status only | Approved default scaffold runner |
| `--linux-service-status` | Fixed service_status worker, nginx.service allowlist | Unchanged main runner/collector policy |
| `--linux-disk-usage` | Fixed disk worker, root -> / only | Existing 5-second WorkerRunner policy |

Combining any profiles, passing unknown flags or supplying a filesystem path as
a CLI option value/positional argument is rejected before backend construction.
This integration does not enable simultaneous real collectors, expand either
allowlist, change subprocess commands or introduce a new authorization mode.
`tests/test_disk_usage_profiles.py` checks exact registration and deadlines,
invalid combinations, SDK behavior and native frame sizes. Its SDK real-profile
launches run only on non-Linux test hosts to avoid real diagnostics; they are
explicitly skipped on Linux. Separate synthetic disk/service SDK tests continue
to cover successful observations. No existing test is weakened or modified.

### Independent evidence and remaining VM gate

[Hiền's independent review/smoke](https://github.com/hienne215/linux-log-agent/pull/24#issuecomment-6982947277)
applies to old `90c6537`: 151 targeted PASS and one positive non-root Ubuntu
laptop disk SDK call under UID1000. This is valid prior evidence, not a fresh
execution or acceptance of this integrated candidate. The entrypoint selection
delta requires independent review; Linux smoke of the new candidate is NOT RUN.

Trang must use an already authorized least-privilege VM reader and the exact final
candidate SHA; record `id`, Python/MCP versions and namespace scope. Do not change
permissions/groups, install services, use sudo or manufacture nginx state for this
check. Run each real profile separately: one connection and one SDK call per
profile, no retries/reconnects. Use the disk snippet below and the approved
[service_status handoff](P01-03-service-status.md), changing only the checkout SHA
being attested. Both flags must be present in `--help`, and combined flags must
exit2 without spawning a collector. Canonical error responses remain actual errors,
not positive acceptance.

For disk, verify root binding, finite values, inode flag, evidence source/time,
correlation and text/structuredContent/isError consistency. For service, compare
the four allowed properties under that same reader and verify nginx.service
binding/provenance; if absent or denied, record the real error without host changes.
Retain SDK context closure and scoped owned-process snapshots; cooperative closure
does not prove every exceptional descendant-cleanup case. Keep PR #24 Draft and
SCRUM-18 acceptance pending independent integrated-HEAD review and VM evidence.

## Prior MCP integration candidate — 18/11/2026 (90c6537)

The original core below is preserved unchanged. The following current candidate
supersedes its historical "remaining MCP work" statements; it is author-run work,
not independent review or SCRUM-18 Done. Normal main dependency adoption uses
`dbd279aa87876141767a0ce1a8e851cdcc83f34d` (approved #6/#14 already merged).
All 40 schema-tree files and shared scaffold/security fixtures remain equal to
that main. No journal/P03-01 policy dependency is needed for statvfs.

`python run_mcp_server.py --linux-disk-usage` registers exactly the fixed
`sys.executable -I <repository>/src/mcp_server/disk_usage_worker.py` backend.
This operator option is mutually exclusive with `--fixtures`. Without it, disk
remains UNSUPPORTED; Linux is required for real observations. No path, executable,
mapping or command option comes from MCP input. Only selector `root` is authorized;
wire null/traversal is INVALID_INPUT, while schema-valid `/` or `/tmp` is
POLICY_DENIED before API access. Omission maps to the original core default.

The child validates canonical input, calls the existing core, validates returned
data/full envelope and checks the fixed binding before generating evidence.
Success preserves all four correlation IDs. Evidence has random unique IDs,
source `collector.disk_usage.statvfs.v1`, unit=null and a timestamp after the API
returns. Its bounded excerpt is built only from validated root/numeric/boolean
fields. `redacted=true` denotes this deliberately restricted projection, not a
general redactor. No raw OS diagnostics or synthetic fallback is returned.
The root is the reader process's namespace root, not proof of a host/device ID.

Failures reuse the approved safe-error boundary: static code, no evidence,
session_id=null and correlation placeholders `invalid-input`. IDs are preserved
on successful validated observations, not error reflection. `isError` and text /
structuredContent mapping remain owned by ServerScaffold. The worker bounds input
to 16,384 bytes and output to 65,536 bytes; the parent enforces stdout 65,536,
stderr 4,096 and the full native JSON-RPC response cap of 65,536 bytes.
Duplicate JSON keys/nonstandard constants and invalid UTF-8 fail closed.

The approved WorkerRunner provides a 5-second execution deadline and direct-child
timeout/cancellation termination, drain/reap and retained cleanup ownership. This
is not a hard wall-clock guarantee: process creation or an uninterruptible kernel
statvfs call can delay confirmed cleanup; the parent refuses reuse until cleanup
is confirmed. No shared runtime duplication or relaxed assertions are introduced.

Offline tests use statvfs doubles and real SDK/child Python processes. Positive
stdio fixtures label their source `synthetic.disk_usage.statvfs.test.v1` and never
read the host filesystem. Permission/missing/unsupported failures, bad input,
observation validation, bounds, timeout/cancellation/reaping and wire consistency
are included. Real Linux smoke: **NOT RUN**. Independent review remains pending.

### Safe Linux review smoke (not executed here)

Use an approved non-root reader account, Python 3.12 and the pinned requirements
(`mcp==2.3.0`). No sudo, permission change or package/service mutation is required.
Run from an exact candidate checkout; save SHA and `pip freeze` separately. If
the target cannot meet these prerequisites, stop rather than changing privileges.
Run the snippet once; do not resend or reconnect after timeout/disconnect. An
unknown outcome is retained until worker cleanup is confirmed.

```python
import asyncio
import json
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import mock_mcp_server as contracts

async def smoke():
    params = StdioServerParameters(command=sys.executable, args=[
        str(Path('run_mcp_server.py').resolve()), '--linux-disk-usage'])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool('disk_usage', {
                'schema_version': '1.0.0', 'session_id': 'linux-disk-review',
                'trace_id': 'disk-trace', 'logical_tool_call_id': 'disk-call',
                'attempt_id': 'disk-attempt', 'filesystem': 'root',
                'include_inodes': True})
            data = result.structured_content
            contracts.TOOLS['disk_usage'][4].validate(data)
            assert json.loads(result.content[0].text) == data
            assert result.is_error == (data['status'] == 'error')
            assert contracts.wire_size(result, 1) <= 65536
            if not result.is_error:
                assert data['data']['filesystem'] == 'root'
                assert data['data']['mount_point'] == '/'
                assert data['evidence'][0]['source'] == 'collector.disk_usage.statvfs.v1'
            print(json.dumps(data, ensure_ascii=True))

asyncio.run(asyncio.wait_for(smoke(), 30))
```

A successful smoke must be separately attested at the exact candidate SHA; an
UNSUPPORTED/error response is not positive Linux acceptance. Numeric data is a
snapshot and may change between calls. No production topology or authorization
beyond this fixed root mapping is certified by the offline tests.

## Historical isolated-core record

Owner: Thủy. Local implementation candidate, not independent acceptance.
Branch: codex/p01-04-disk-usage-core. Base: scaffold PR #14
656f8c365a300270418e15b34ae1046d12fbcb44. Reference contracts: PR #6
dc53ff6c3afd038f66e987bf1c7f7ad17f4b7829. The disk_usage input/data schemas
are identical between these two revisions; no merge or schema change is needed.

## API and identity

`collect_disk_usage(filesystem=None, include_inodes=True)` calls only Python's
read-only [`os.statvfs('/')`](https://docs.python.org/3.12/library/os.html#os.statvfs).
It returns canonical data or `DiskUsageError(code)`.
The immutable application mapping is `root -> /`, with root as the default logical
filesystem ID. This is a trusted application alias for the root filesystem of the
current process namespace, not a discovered device name. It does not claim /dev
identity, filesystem type, or host-versus-container equivalence. Root is the only
configured mount; arbitrary paths, traversal strings and device names are rejected
before any API call. The mapping/executable cannot be supplied by MCP arguments.

Internal None selects the configured default. Explicit null is NOT allowed by the
canonical wire input schema; a future MCP boundary must validate raw requests and
translate an omitted filesystem into this internal default, without permitting null.

## Arithmetic and bounds

- Validate all block counters/sizes as integers (not bool), in [0, 2^64-1]. Require
  f_blocks > 0, f_bsize > 0 and 0 <= f_bavail <= f_bfree <= f_blocks.
- Unit size = f_frsize when positive; otherwise f_bsize. Fragment size cannot be
  negative. This measures fragment allocation units rather than assuming I/O block
  size equals allocation size.
- available_bytes = f_bavail * unit size, using blocks available to an unprivileged
  user. A product above 2^64-1 is an internal bounds failure, never wrapped/clamped.
- used_percent = 100 * (f_blocks - f_bfree) / f_blocks. Denominator is total blocks,
  including reserved space. Free-but-reserved blocks are not counted as used. This
  deliberately differs from a df-style denominator based on used + available.
  Integer intermediate arithmetic avoids overflow; final float must be finite and
  within 0..100. No result is corrected by clamping invalid observations.
- include_inodes=True validates f_favail <= f_ffree <= f_files, with the same integer
  bounds. False does not inspect inode counters. Zero inode counters are permitted;
  no inode percentage is computed. inodes_included reflects the input boolean. The
  schema has no inode count/usage fields, so none are added to the returned data.

Returned keys are exactly filesystem, mount_point, used_percent, available_bytes,
inodes_included. No evidence, synthetic fallback or CommonResponse is created.

## Safe internal errors

Wrong parameter type/length: INVALID_INPUT. Non-allowlisted filesystem: POLICY_DENIED.
Missing statvfs, NotImplementedError or ENOSYS/ENOTSUP/EOPNOTSUPP: UNSUPPORTED.
PermissionError/EACCES/EPERM: PERMISSION_DENIED. FileNotFoundError/ENOENT/ENOTDIR:
NOT_FOUND. Invalid/missing/inconsistent stats, byte overflow and other API failures:
EXECUTION_UNKNOWN. Only the fixed code is exposed; raw exception text, filenames
and traceback context are not printed or included in a business result. These are
internal errors; mapping to an MCP envelope remains separate scaffold work.

## Verification and remaining work

Targeted tests use mocked os.statvfs on Windows/Python 3.12. No call observes the
developer's filesystem, runs df/shell/subprocess/sudo or changes permissions.
Tests validate the canonical data schema, exact keys, identity, allowlist-before-API,
arithmetic, reserved space, fragment/block sizes, bounds, typed errors and safe text.

```text
python -m pytest -q tests/test_disk_usage_collector.py --basetemp=<writable-directory>
```

Still pending: independent review, fixed worker/registry dispatch and canonical
request validation, evidence/provenance/correlation and envelope mapping, process
isolation/deadline/cancellation/cleanup policy, SDK integration and authorized Linux
smoke. os.statvfs is synchronous and can block on a filesystem; this core provides
no timeout guarantee. No P03-01 or journal access dependency is introduced. No MCP
integration, Linux acceptance, production readiness or full P01-04 completion claim.

Local targeted verification: Windows, Python 3.12.7; **113 PASS, 0 FAIL,
0 ERROR, 0 SKIP** using statvfs doubles. Canonical data-schema validation is
included in the suite. No real Linux observation or independent signoff.
