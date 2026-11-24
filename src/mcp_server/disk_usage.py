"""Isolated read-only statvfs core; no MCP envelope or evidence generation."""

import errno
import math
import os
from types import MappingProxyType


# Logical application identifier, not an inferred device name. Not caller-owned.
FILESYSTEMS = MappingProxyType({"root": "/"})
DEFAULT_FILESYSTEM = "root"
_MAX_VALUE = (1 << 64) - 1


class DiskUsageError(Exception):
    """Typed internal failure with a fixed code and no OS diagnostic/path."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _counter(stats, name):
    value = getattr(stats, name)
    if type(value) is not int or not 0 <= value <= _MAX_VALUE:
        raise DiskUsageError("EXECUTION_UNKNOWN")
    return value


def collect_disk_usage(filesystem: str | None = None, include_inodes: bool = True, *, _inode_details=None) -> dict:
    """Return canonical disk_usage data for an application-owned mount mapping.

    None is an internal default only: the canonical wire schema rejects explicit
    null. The future MCP boundary must validate raw requests before calling here.
    No path lookup, executable, mapping or statvfs implementation is taken from
    the request. A successful call observes the root of the current namespace,
    without claiming a physical device identity or host/container equivalence.
    """
    if type(include_inodes) is not bool:
        raise DiskUsageError("INVALID_INPUT")
    if _inode_details is not None and type(_inode_details) is not dict:
        raise DiskUsageError("INVALID_INPUT")
    if filesystem is None:
        filesystem = DEFAULT_FILESYSTEM
    elif type(filesystem) is not str or not filesystem or len(filesystem) > 255:
        raise DiskUsageError("INVALID_INPUT")
    if filesystem not in FILESYSTEMS:
        raise DiskUsageError("POLICY_DENIED")
    mount_point = FILESYSTEMS[filesystem]
    statvfs = getattr(os, "statvfs", None)
    if not callable(statvfs):
        raise DiskUsageError("UNSUPPORTED")
    try:
        stats = statvfs(mount_point)
        total = _counter(stats, "f_blocks")
        free = _counter(stats, "f_bfree")
        available = _counter(stats, "f_bavail")
        fragment = _counter(stats, "f_frsize")
        block = _counter(stats, "f_bsize")
        if total == 0 or block == 0 or not available <= free <= total:
            raise DiskUsageError("EXECUTION_UNKNOWN")
        unit_size = fragment or block
        available_bytes = available * unit_size
        if available_bytes > _MAX_VALUE:
            raise DiskUsageError("EXECUTION_UNKNOWN")
        used_percent = ((total - free) * 100) / total
        if not math.isfinite(used_percent) or not 0 <= used_percent <= 100:
            raise DiskUsageError("EXECUTION_UNKNOWN")
        if include_inodes:
            files = _counter(stats, "f_files")
            files_free = _counter(stats, "f_ffree")
            files_available = _counter(stats, "f_favail")
            if not files_available <= files_free <= files:
                raise DiskUsageError("EXECUTION_UNKNOWN")
    except PermissionError:
        raise DiskUsageError("PERMISSION_DENIED") from None
    except FileNotFoundError:
        raise DiskUsageError("NOT_FOUND") from None
    except NotImplementedError:
        raise DiskUsageError("UNSUPPORTED") from None
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EPERM}:
            code = "PERMISSION_DENIED"
        elif error.errno in {errno.ENOENT, errno.ENOTDIR}:
            code = "NOT_FOUND"
        elif error.errno in {errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}:
            code = "UNSUPPORTED"
        else:
            code = "EXECUTION_UNKNOWN"
        raise DiskUsageError(code) from None
    except DiskUsageError:
        raise
    except Exception:
        raise DiskUsageError("EXECUTION_UNKNOWN") from None
    if include_inodes and _inode_details is not None:
        # Same validated statvfs observation, not a second racy filesystem read.
        # Kept in canonical evidence while preserving v1 response-data shape.
        _inode_details.update(total=files,free=files_free,available=files_available)
    return {"filesystem": filesystem, "mount_point": mount_point,
            "used_percent": used_percent, "available_bytes": available_bytes,
            "inodes_included": include_inodes}
