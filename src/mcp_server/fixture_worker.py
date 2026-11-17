"""One fixed synthetic worker, explicitly enabled by --fixtures; no Linux reads."""
import json
from pathlib import Path
import sys

# -I ignores inherited paths; this repository-owned location is fixed by code.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import mock_mcp_server as contracts


def main():
    raw = sys.stdin.buffer.read(16385)
    if len(raw) > 16384:
        return 2
    try:
        arguments = json.loads(raw.decode("utf-8"))
        if not contracts.TOOLS["service_status"][3].is_valid(arguments):
            return 2
        response = contracts.service_status(**arguments)
        sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
