"""Real SDK stdio + boundary; existing Hiền-owned synthetic fixture only."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent.integration_boundary import BoundaryError, BridgeBudget, ToolBridge, TrustedTool
from agent.mcp_client import StdioConfig, StdioMCPClient
from fixtures import redact_fixture, sample_tool


@unittest.skipUnless(os.environ.get("RUN_MCP_STDIO_SMOKE") == "1", "opt-in real SDK stdio")
class BridgeStdioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import anyio
        self.processes = []
        original = anyio.open_process

        async def observe(*args, **kwargs):
            process = await original(*args, **kwargs)
            self.processes.append(process)
            return process

        observer = patch("anyio.open_process", side_effect=observe)
        observer.start()
        self.addCleanup(observer.stop)
        temporary = tempfile.TemporaryDirectory(prefix="scrum22-boundary-stdio-")
        self.addCleanup(temporary.cleanup)
        self.events = Path(temporary.name) / "events.txt"
        server = Path(__file__).resolve().parents[1] / "mcp_client" / "mock_stdio_server.py"
        self.client = StdioMCPClient(StdioConfig(command=sys.executable,
            args=(str(server), "--events-file", str(self.events)),
            allowed_tools=frozenset({"mock_echo", "mock_wait", "mock_disconnect"})))
        await self.client.connect()
        page = await self.client.list_tools()
        self.assertIn("mock_echo", {t["name"] for t in page.tools})
        self.tools = (sample_tool(), *(TrustedTool(name, "Synthetic lifecycle fixture", {
            "type": "object", "properties": {}, "additionalProperties": False,
        }, True) for name in ("mock_wait", "mock_disconnect")))

    async def asyncTearDown(self):
        try:
            await self.client.disconnect()
            self.assertIsNone(self.client._owner)
        finally:
            self.assertEqual(len(self.processes), 1)
            self.assertIsNotNone(self.processes[0].returncode, "orphan synthetic server")

    async def wait_marker(self, marker):
        async with asyncio.timeout(5):
            while not self.events.exists() or marker not in self.events.read_text().splitlines():
                await asyncio.sleep(.01)

    def assert_bridge_cleanup(self, bridge, owner):
        self.assertTrue(owner.done())
        self.assertIsNone(self.client._owner)
        self.assertIsNone(bridge._operation)
        self.assertEqual(bridge.cleanup_state, "confirmed")
        self.assertEqual(bridge.execution_state, "unknown")
        self.assertIsNone(bridge.safe_result)
        self.assertIsNotNone(self.processes[0].returncode)

    async def test_bridge_real_sdk_echo_and_cleanup(self):
        bridge = ToolBridge(self.client, self.tools, redact=redact_fixture)
        message = await bridge.execute("mock_echo", {"sample": "synthetic"})
        self.assertFalse(message.is_error)
        self.assertIn("synthetic", message.content)

    async def test_bridge_real_sdk_cancellation_and_cleanup(self):
        bridge = ToolBridge(self.client, self.tools, redact=redact_fixture)
        owner = self.client._owner
        task = asyncio.create_task(bridge.execute("mock_wait", {}))
        try:
            await self.wait_marker("wait_started")
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await self.wait_marker("wait_cancelled")
            self.assertNotIn("wait_completed", self.events.read_text().splitlines())
            self.assertFalse(self.client.connected)
            self.assertEqual(bridge.calls_used, 1)
            self.assert_bridge_cleanup(bridge, owner)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_bridge_real_sdk_timeout_and_cleanup(self):
        owner = self.client._owner
        bridge = ToolBridge(self.client, self.tools, redact=redact_fixture,
                            budget=BridgeBudget(request_seconds=.3))
        with self.assertRaises(BoundaryError) as caught:
            await bridge.execute("mock_wait", {})
        self.assertEqual((caught.exception.code, caught.exception.layer), ("TIMEOUT", "transport"))
        self.assertIn("wait_started", self.events.read_text().splitlines())
        self.assertFalse(self.client.connected)
        self.assertEqual(bridge.calls_used, 1)
        self.assert_bridge_cleanup(bridge, owner)

    async def test_bridge_real_sdk_disconnect_no_retry(self):
        owner = self.client._owner
        bridge = ToolBridge(self.client, self.tools, redact=redact_fixture)
        with self.assertRaises(BoundaryError) as caught:
            await bridge.execute("mock_disconnect", {})
        self.assertEqual(caught.exception.code, "DISCONNECTED")
        self.assertEqual(self.events.read_text().splitlines(), ["disconnect_started"])
        self.assertEqual(self.processes[0].returncode, 23)
        self.assertEqual(bridge.calls_used, 1)
        self.assert_bridge_cleanup(bridge, owner)
