"""Opt-in test against installed SDK 2.3.0 and a synthetic stdio child."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from agent.mcp_client import StdioConfig, StdioMCPClient


@unittest.skipUnless(os.environ.get("RUN_MCP_STDIO_SMOKE") == "1", "opt-in: requires installed mcp==2.3.0")
class RealSDKStdioTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_mock_server(self):
        import anyio

        spawned = []
        open_process = anyio.open_process

        async def record_process(*args, **kwargs):
            process = await open_process(*args, **kwargs)
            spawned.append(process)
            return process

        config = StdioConfig(
            command=sys.executable,
            args=(str(Path(__file__).with_name("mock_stdio_server.py").resolve()),),
            allowed_tools=frozenset({"mock_echo"}),
        )
        client = StdioMCPClient(config)
        # Observe real SDK subprocess creation; do not replace its transport.
        with patch("anyio.open_process", side_effect=record_process):
            try:
                async with client:
                    self.assertTrue(client.connected)  # initialize completed
                    page = await client.list_tools()
                    self.assertIn("mock_echo", [tool["name"] for tool in page.tools])
                    result = await client.call_tool("mock_echo", {"sample": "synthetic-only"})
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.structured_content, {"sample": "synthetic-only"})
                    self.assertTrue(result.content)
                    self.assertEqual(result.content[0]["type"], "text")
            finally:
                await client.disconnect()
        self.assertFalse(client.connected)
        self.assertEqual(len(spawned), 1)
        # SDK has awaited/reaped the one server. Fixture never spawns children.
        self.assertIsNotNone(spawned[0].returncode, "orphan mock server")
