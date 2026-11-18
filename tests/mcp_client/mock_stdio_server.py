"""Synthetic test fixture, not the SCRUM-12 server or a project contract.

Only the trusted test launcher can select an events file in its temp directory.
Tool arguments cannot choose files, commands, or processes. All markers are fixed.
"""
import argparse
import asyncio
import os
from pathlib import Path

from mcp.server import MCPServer

server = MCPServer("scrum22-test-fixture")
_events_file: Path | None = None


def _emit(marker: str) -> None:
    if _events_file is not None:
        with _events_file.open("a", encoding="utf-8") as stream:
            stream.write(marker + "\n")


@server.tool()
def mock_echo(sample: str) -> dict[str, str]:
    """Return synthetic data unchanged. No external commands or I/O."""
    return {"sample": sample}


@server.tool()
async def mock_wait() -> dict[str, bool]:
    """Wait for cancellation in this synthetic server, never run diagnostics."""
    _emit("wait_started")
    try:
        await asyncio.sleep(30)
    except asyncio.CancelledError:
        _emit("wait_cancelled")
        raise
    _emit("wait_completed")
    return {"completed": True}


@server.tool()
def mock_disconnect() -> None:
    """Terminate only this fixture subprocess to simulate transport loss."""
    _emit("disconnect_started")
    os._exit(23)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-file", type=Path)
    _events_file = parser.parse_args().events_file
    server.run(transport="stdio")
