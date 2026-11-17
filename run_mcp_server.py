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
    parser.add_argument("--oom-session-id", help="operator-owned session binding; required with --linux-oom")
    return parser


def build_backends(fixtures=False):
    """Worker argv is fixed here, in code. No MCP argument can change the executable or the script."""
    backends = {}
    if fixtures:
        backends["service_status"] = (sys.executable, "-I", str(ROOT / "src/mcp_server/fixture_worker.py"))
    return backends


def build_server(fixtures=False, linux_oom=False, oom_session_id=None):
    if linux_oom:
        from src.mcp_server.oom_server import OomServer
        return OomServer(oom_session_id,backends=build_backends(fixtures))
    return ServerScaffold(backends=build_backends(fixtures))


def main():
    parser=build_parser()
    args=parser.parse_args()
    if args.linux_oom and not args.oom_session_id:
        parser.error('--linux-oom requires --oom-session-id')
    if args.oom_session_id and not args.linux_oom:
        parser.error('--oom-session-id requires --linux-oom')
    asyncio.run(build_server(args.fixtures,args.linux_oom,args.oom_session_id).serve())


if __name__ == "__main__":
    main()
