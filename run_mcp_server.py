"""P01-01 draft stdio entrypoint; real collectors are deliberately unavailable."""
import argparse
import asyncio
from pathlib import Path
import sys

from src.mcp_server.server import ServerScaffold


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", action="store_true", help="enable one explicitly synthetic service_status worker")
    args = parser.parse_args()
    backends = {}
    if args.fixtures:
        worker = Path(__file__).resolve().parent / "src/mcp_server/fixture_worker.py"
        backends["service_status"] = (sys.executable, "-I", str(worker))
    asyncio.run(ServerScaffold(backends=backends).serve())


if __name__ == "__main__":
    main()
