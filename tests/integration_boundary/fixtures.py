"""Synthetic, non-project contracts; no I/O or Linux commands."""
import asyncio
from contextlib import asynccontextmanager

from agent.integration_boundary import TrustedTool


def sample_tool(*, read_only=True):
    return TrustedTool("mock_echo", "Echo synthetic sample", {
        "type": "object", "properties": {"sample": {"type": "string", "maxLength": 80}},
        "required": ["sample"], "additionalProperties": False,
    }, read_only)


def redact_fixture(text):
    # Test sentinel only. Not a production secret detector or identity fallback.
    return text.replace("synthetic-sensitive", "[REDACTED]")


class Peer:
    def __init__(self):
        self.calls = []
        self.result = {"content": [{"type": "text", "text": "synthetic evidence"}],
                       "structuredContent": {"evidence": [{"id": "e-1", "source": "fixture"}]},
                       "isError": False}
        self.error = None
        self.wait = False
        self.started = asyncio.Event()
        self.closed = False

    @asynccontextmanager
    async def factory(self, config):
        try:
            yield self
        finally:
            self.closed = True

    async def list_tools(self, cursor):
        return {"tools": [{"name": "mock_echo", "inputSchema": sample_tool().parameters}]}

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        self.started.set()
        if self.error:
            raise self.error
        if self.wait:
            await asyncio.Event().wait()
        return self.result
