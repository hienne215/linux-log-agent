"""P01-01 draft stdio entrypoint; real collectors are deliberately unavailable unless an operator flag enables them."""
import argparse
import asyncio
from pathlib import Path
import sys

from src.mcp_server.server import ServerScaffold

ROOT = Path(__file__).resolve().parent


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", action="store_true", help="enable one explicitly synthetic service_status worker")
    parser.add_argument("--linux-oom", action="store_true",
                        help="enable the read-only memory_oom_events collector (reads journalctl and dmesg); off by default")
    return parser


def build_backends(fixtures=False, linux_oom=False):
    """Worker argv is fixed here, in code. No MCP argument can change the executable or the script."""
    backends = {}
    if fixtures:
        backends["service_status"] = (sys.executable, "-I", str(ROOT / "src/mcp_server/fixture_worker.py"))
    if linux_oom:
        backends["memory_oom_events"] = (sys.executable, "-I", str(ROOT / "src/mcp_server/oom_worker.py"))
    return backends


def main():
    args = build_parser().parse_args()
    asyncio.run(ServerScaffold(backends=build_backends(args.fixtures, args.linux_oom)).serve())


if __name__ == "__main__":
    main()
