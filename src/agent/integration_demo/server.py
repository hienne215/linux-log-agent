"""Synthetic SDK server. Fixed Python entrypoint; no diagnostic commands or files."""
import argparse
import asyncio
import os
from typing import Literal, Any
from mcp.server import MCPServer
# Direct script launch is intentional: SDK environment need not inherit PYTHONPATH.
from fixtures import SCENARIOS, observation

server = MCPServer('integration-demo-synthetic-only')
_scenario = 'active'
_fault = 'none'


async def result(tool):
    if _fault == 'timeout':
        await asyncio.sleep(30)
    if _fault == 'disconnect':
        os._exit(23)  # Only this synthetic subprocess, trusted test seam.
    return observation(_scenario, tool)


@server.tool()
async def service_status(unit: Literal['nginx.service']) -> dict[str, Any]:
    """Read synthetic service fixture only. Never systemctl."""
    return await result('service_status')


@server.tool()
async def listening_ports(unit: Literal['nginx.service']) -> dict[str, Any]:
    """Read synthetic port fixture only. Never ss/netstat."""
    return await result('listening_ports')


@server.tool()
async def query_journal(unit: Literal['nginx.service']) -> dict[str, Any]:
    """Read synthetic permission-denied fixture only. Never journalctl."""
    return await result('query_journal')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', choices=SCENARIOS, required=True)
    parser.add_argument('--fault', choices=('none', 'timeout', 'disconnect'), default='none')
    args = parser.parse_args()
    _scenario, _fault = args.scenario, args.fault
    server.run(transport='stdio')
