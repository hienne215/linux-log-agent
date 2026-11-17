"""Worker entrypoint for memory_oom_events: JSON request on stdin, JSON response on stdout.

Stdlib only, so it also runs with the VM's system Python 3.8. Run it through the server
scaffold with an absolute, code-owned argv, never from MCP arguments.
"""
import json
from pathlib import Path
import sys

# -I ignores inherited paths; this directory is fixed by code.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import oom_events


def main():
    raw = sys.stdin.buffer.read(16385)
    if len(raw) > 16384:
        return 2
    try:
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, dict):
            return 2
        response = oom_events.collect(request)
        sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
