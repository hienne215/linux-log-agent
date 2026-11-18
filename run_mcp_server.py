"""Draft stdio server; real service_status is explicitly operator-enabled only."""
import argparse
import asyncio
from pathlib import Path
import sys

from src.mcp_server.server import ServerScaffold


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    profile = parser.add_mutually_exclusive_group()
    profile.add_argument("--fixtures", action="store_true", help="enable one explicitly synthetic service_status worker")
    profile.add_argument("--linux-service-status", action="store_true", help="read allowlisted Linux systemd service state")
    args = parser.parse_args()
    backends = {}
    if args.fixtures:
        worker = Path(__file__).resolve().parent / "src/mcp_server/fixture_worker.py"
        backends["service_status"] = (sys.executable, "-I", str(worker))
    elif args.linux_service_status:
        worker = Path(__file__).resolve().parent / "src/mcp_server/service_status_worker.py"
        backends["service_status"] = (sys.executable, "-I", str(worker))
    asyncio.run(ServerScaffold(backends=backends).serve())


if __name__ == "__main__":
    main()
