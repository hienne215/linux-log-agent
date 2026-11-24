"""Fixed root-only statvfs worker. Isolation/deadline belong to WorkerRunner."""
import json
from pathlib import Path
import secrets
import sys
import time

# -I ignores inherited import configuration; this location is repository-owned.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import mock_mcp_server as contracts
from src.mcp_server.disk_usage import collect_disk_usage, DiskUsageError

TOOL = "disk_usage"
SOURCE = "collector.disk_usage.statvfs.v1"
MAX_INPUT = 16384


def collect_response(arguments):
    """Only validated observations may become successful data or evidence."""
    if not contracts.TOOLS[TOOL][3].is_valid(arguments):
        return contracts.safe_error(TOOL, {}, rule="validation")
    # Syntax is not authorization. Reject before platform/API access.
    if arguments.get("filesystem", "root") != "root":
        return contracts.safe_error(TOOL, {}, "POLICY_DENIED", rule="filesystem_allowlist")
    if sys.platform != "linux":
        return contracts.safe_error(TOOL, {}, "UNSUPPORTED", rule="platform")
    started = contracts.current_time()
    tick = time.monotonic()
    try:
        inode_details={}
        data = collect_disk_usage(arguments.get("filesystem"), arguments.get("include_inodes", True),_inode_details=inode_details)
        json.dumps(data, allow_nan=False)
        observed = contracts.current_time()
        response = contracts.make_response(TOOL, arguments["trace_id"],
            arguments["logical_tool_call_id"], arguments["attempt_id"], data,
            arguments["session_id"])
        # Full schema plus trusted binding checks, before formatting any evidence.
        if (not contracts.TOOLS[TOOL][4].is_valid(response)
                or data["filesystem"] != "root" or data["mount_point"] != "/"
                or data["inodes_included"] is not arguments.get("include_inodes", True)):
            return contracts.safe_error(TOOL, {}, "EXECUTION_UNKNOWN", rule="observation")
        response["evidence"] = [dict(evidence_id="disk-" + secrets.token_hex(16),
            observed_at=observed, source=SOURCE, unit=None,
            excerpt=("filesystem=root mount_point=/ "
                     f"used_percent={data['used_percent']} available_bytes={data['available_bytes']} "
                     f"inodes_included={data['inodes_included']}"+
                     (f" inodes_total={inode_details['total']} inodes_free={inode_details['free']} inodes_available={inode_details['available']}" if data['inodes_included'] else '')),
            redacted=True, truncated=False)]
        response.update(started_at=started, completed_at=contracts.current_time(),
                        duration_ms=int((time.monotonic() - tick) * 1000))
        if not contracts.TOOLS[TOOL][4].is_valid(response):
            return contracts.safe_error(TOOL, {}, "EXECUTION_UNKNOWN", rule="output_validation")
        return response
    except DiskUsageError as error:
        code = error.code if error.code in {"INVALID_INPUT", "POLICY_DENIED", "UNSUPPORTED",
            "PERMISSION_DENIED", "NOT_FOUND", "EXECUTION_UNKNOWN"} else "EXECUTION_UNKNOWN"
        return contracts.safe_error(TOOL, {}, code, rule="collection")
    except Exception:
        return contracts.safe_error(TOOL, {}, "EXECUTION_UNKNOWN", rule="collection")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _constant(value):
    raise ValueError


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            return 2
        try:
            arguments = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                                   parse_constant=_constant)
        except (ValueError, UnicodeError):
            response = contracts.safe_error(TOOL, {}, rule="json")
        else:
            response = collect_response(arguments)
        # Parent validates again and bounds the complete native JSON-RPC frame.
        payload = json.dumps(response, ensure_ascii=True, allow_nan=False).encode("utf-8")
        if len(payload) > contracts.MAX_RESPONSE_BYTES:
            response = contracts.safe_error(TOOL, {}, "OUTPUT_LIMIT", rule="worker_output")
            payload = json.dumps(response, ensure_ascii=True, allow_nan=False).encode("utf-8")
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        return 2  # No diagnostics or exception text on either pipe.


if __name__ == "__main__":
    raise SystemExit(main())
