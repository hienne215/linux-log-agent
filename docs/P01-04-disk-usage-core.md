# SCRUM-18 / P01-04: isolated read-only disk_usage core

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
