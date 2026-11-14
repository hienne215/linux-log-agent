"""Trusted test-only child faults; never selectable through MCP arguments."""
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mock_mcp_server as contracts

mode = sys.argv[1]
arguments = json.loads(sys.stdin.buffer.read().decode("utf-8"))
if mode == "sleep":
    time.sleep(30)
elif mode == "stdout_flood":
    sys.stdout.buffer.write(b"x" * 131072)
elif mode == "infinite_stdout":
    while True:
        sys.stdout.buffer.write(b"x" * 4096)
        sys.stdout.buffer.flush()
elif mode == "stderr_flood":
    sys.stderr.buffer.write(b"SYNTHETIC_CHILD_SECRET" * 4096)
elif mode == "failure":
    sys.stderr.write("SYNTHETIC_CHILD_SECRET")
    raise SystemExit(3)
elif mode == "malformed":
    sys.stdout.write("SYNTHETIC_CHILD_SECRET")
else:
    response = (contracts.query_journal(**arguments) if mode == "partial"
                else contracts.service_status(**arguments))
    if mode == "wrong_ids":
        response["trace_id"] = "forged-SYNTHETIC_CHILD_SECRET"
    elif mode in {"semantic", "permission"}:
        response.update(status="error", error_code="INVALID_INPUT" if mode == "semantic" else "PERMISSION_DENIED",
                        data={"message": "SYNTHETIC_CHILD_SECRET"})
    elif mode == "large_atomic":
        response["data"]["description"] = "漢🙂" * 6000
    elif mode == "partial":
        response["data"]["entries"] = [{"timestamp": "2026-11-02T09:30:00Z", "priority": 3,
                                           "message": "whole-record-" + "漢" * 150} for _ in range(100)]
        response["data"]["count"] = 100
    elif mode == "invalid_output":
        response["data"] = {"SYNTHETIC_CHILD_SECRET": True}
    elif mode == "environment":
        response["data"]["description"] = ("SYNTHETIC_CHILD_SECRET" if os.environ.get("OPENAI_API_KEY")
                                               else "environment-sanitized")
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
