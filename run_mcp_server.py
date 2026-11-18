"""MCP scaffold; real disk_usage is explicitly operator-enabled only."""
import argparse
import asyncio
from pathlib import Path
import sys

from src.mcp_server.server import ServerScaffold
from src.mcp_server.runner import WorkerRunner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    profile = parser.add_mutually_exclusive_group()
    profile.add_argument("--fixtures", action="store_true", help="enable one explicitly synthetic service_status worker")
    profile.add_argument("--linux-disk-usage", action="store_true", help="read only the configured root filesystem with statvfs")
    args = parser.parse_args()
    backends = {}
    if args.fixtures:
        worker = Path(__file__).resolve().parent / "src/mcp_server/fixture_worker.py"
        backends["service_status"] = (sys.executable, "-I", str(worker))
    elif args.linux_disk_usage:
        worker = Path(__file__).resolve().parent / "src/mcp_server/disk_usage_worker.py"
        backends["disk_usage"] = (sys.executable, "-I", str(worker))
    runner = WorkerRunner(timeout=5.0) if args.linux_disk_usage else None
    asyncio.run(ServerScaffold(backends=backends, runner=runner).serve())


if __name__ == "__main__":
    main()
