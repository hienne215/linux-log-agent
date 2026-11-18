"""Opt-in application-owned launcher; existing server/contracts stay unchanged."""
import argparse
import asyncio
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.mcp_server.server import ServerScaffold


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--tool', required=True, choices=('list_failed_units', 'listening_ports'))
    args = parser.parse_args()
    worker = Path(__file__).with_name('diagnostic_worker.py').resolve()
    asyncio.run(ServerScaffold(backends={args.tool: (sys.executable, '-I', str(worker), args.tool)}).serve())


if __name__ == '__main__':
    main()
