"""Opt-in official SDK tests with real stdio and synthetic data only."""
import asyncio
from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent.mcp_client import MCPClientError, StdioConfig, StdioMCPClient


@unittest.skipUnless(os.environ.get("RUN_MCP_STDIO_SMOKE") == "1", "opt-in: requires installed mcp==2.3.0")
class RealSDKStdioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import anyio

        self.spawned = []
        self.tempdir = tempfile.TemporaryDirectory(prefix="scrum22-stdio-")
        self.addCleanup(self.tempdir.cleanup)
        self.events_file = Path(self.tempdir.name) / "events.txt"
        open_process = anyio.open_process

        async def record_process(*args, **kwargs):
            process = await open_process(*args, **kwargs)
            self.spawned.append(process)
            return process

        # Observe real process creation without replacing SDK stdio or session.
        observer = patch("anyio.open_process", side_effect=record_process)
        observer.start()
        self.addCleanup(observer.stop)
        self.config = StdioConfig(
            command=sys.executable,
            args=(str(Path(__file__).with_name("mock_stdio_server.py").resolve()),
                  "--events-file", str(self.events_file)),
            allowed_tools=frozenset({"mock_echo", "mock_wait", "mock_disconnect"}),
        )
        self.client = StdioMCPClient(self.config)

    async def asyncTearDown(self):
        try:
            await self.client.disconnect()
        finally:
            self.assertEqual(len(self.spawned), 1)
            # A returncode means SDK has awaited/reaped the fixture process.
            self.assertIsNotNone(self.spawned[0].returncode, "orphan mock server")

    async def connect(self):
        await self.client.connect()
        self.assertTrue(self.client.connected)  # initialize completed
        page = await self.client.list_tools()
        self.assertIn("mock_echo", [tool["name"] for tool in page.tools])

    async def wait_for_marker(self, marker):
        async with asyncio.timeout(5):
            while True:
                if self.events_file.exists() and marker in self.events_file.read_text().splitlines():
                    return
                await asyncio.sleep(.01)

    async def test_local_mock_server(self):
        await self.connect()
        result = await self.client.call_tool("mock_echo", {"sample": "synthetic-only"})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content, {"sample": "synthetic-only"})
        self.assertTrue(result.content)
        self.assertEqual(result.content[0]["type"], "text")
        await self.client.disconnect()
        self.assertFalse(self.client.connected)

    async def test_real_cancellation(self):
        await self.connect()
        call = asyncio.create_task(self.client.call_tool("mock_wait", {}))
        try:
            # Prove the real server entered the handler before canceling.
            await self.wait_for_marker("wait_started")
            call.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await call
            self.assertFalse(self.client.connected)
            await self.wait_for_marker("wait_cancelled")
            self.assertNotIn("wait_completed", self.events_file.read_text().splitlines())
        finally:
            if not call.done():
                call.cancel()
            await asyncio.gather(call, return_exceptions=True)

    async def test_real_server_disconnect(self):
        await self.connect()
        with self.assertRaises(MCPClientError) as caught:
            await self.client.call_tool("mock_disconnect", {})
        self.assertEqual(caught.exception.code, "DISCONNECTED")
        self.assertFalse(self.client.connected)
        self.assertEqual(self.spawned[0].returncode, 23)
        self.assertEqual(self.events_file.read_text().splitlines(), ["disconnect_started"])

    async def test_real_request_timeout(self):
        # Keep connect budget unchanged; only the tool request budget is short.
        self.client = StdioMCPClient(replace(self.config, request_timeout=.5))
        await self.connect()
        with self.assertRaises(MCPClientError) as caught:
            await self.client.call_tool("mock_wait", {})
        self.assertEqual(caught.exception.code, "TIMEOUT")
        self.assertFalse(self.client.connected)
        markers = self.events_file.read_text().splitlines()
        self.assertIn("wait_started", markers)
        self.assertNotIn("wait_completed", markers)
