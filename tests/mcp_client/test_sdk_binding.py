"""API-shaped doubles: these do not establish real SDK compatibility."""
import asyncio
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from agent.mcp_client import MCPClientError, StdioConfig, StdioMCPClient
from agent.mcp_client.sdk import sdk_peer


class BindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_and_wrong_sdk_version_fail_closed(self):
        for value in [None, "1.26.0", "2.3.1"]:
            with patch("agent.mcp_client.sdk.version", side_effect=PackageNotFoundError if value is None else None, return_value=value):
                with self.assertRaises(MCPClientError) as caught:
                    async with sdk_peer(StdioConfig("/trusted/python")):
                        self.fail("must not connect")
                self.assertEqual(caught.exception.code, "SDK_UNAVAILABLE" if value is None else "SDK_VERSION_MISMATCH")

    async def test_stdio_wiring_env_timeout_single_send_and_cleanup(self):
        events = []
        captured = {}

        @asynccontextmanager
        async def transport(params, *, errlog):
            captured["params"] = params
            captured["stderr"] = errlog.name
            events.append(("transport_enter", asyncio.current_task()))
            try:
                yield ("read", "write")
            finally:
                events.append(("transport_exit", asyncio.current_task()))

        class Session:
            def __init__(self, read, write, *, read_timeout_seconds):
                captured["streams"] = (read, write)
                captured["timeout"] = read_timeout_seconds

            async def __aenter__(self):
                events.append(("session_enter", asyncio.current_task()))
                return self

            async def __aexit__(self, *args):
                events.append(("session_exit", asyncio.current_task()))

            async def initialize(self):
                captured["initialized"] = True

            async def list_tools(self, *, params):
                captured["cursor"] = params.cursor
                return {"tools": [{"name": "mock_echo", "inputSchema": {}}]}

            async def call_tool(self, name, arguments, *, read_timeout_seconds, allow_input_required):
                captured.setdefault("calls", []).append((name, arguments, read_timeout_seconds, allow_input_required))
                if arguments.get("invalid_output"):
                    raise RuntimeError("synthetic_private_value")
                return {"content": []}

        modules = {name: ModuleType(name) for name in ["mcp", "mcp.client", "mcp.client.stdio", "mcp.client.session", "mcp_types"]}
        modules["mcp.client.stdio"].stdio_client = transport
        modules["mcp.client.stdio"].StdioServerParameters = lambda **kw: SimpleNamespace(**kw)
        modules["mcp.client.stdio"].DEFAULT_INHERITED_ENV_VARS = ["HOME", "PATH", "USER"]
        modules["mcp.client.session"].ClientSession = Session
        modules["mcp_types"].PaginatedRequestParams = lambda **kw: SimpleNamespace(**kw)
        config = StdioConfig("/trusted/python", ("mock.py",), frozenset({"mock_echo"}))
        with patch.dict(sys.modules, modules), patch("agent.mcp_client.sdk.version", return_value="2.3.0"):
            async with StdioMCPClient(config) as client:
                await client.list_tools()
                await client.call_tool("mock_echo", {})
                with self.assertRaises(MCPClientError) as caught:
                    await client.call_tool("mock_echo", {"invalid_output": True})
                self.assertEqual(str(caught.exception), "MALFORMED_RESULT")
        self.assertEqual(captured["params"].env["HOME"], "")
        self.assertEqual(captured["params"].env["PATH"], "/usr/bin:/bin")
        self.assertEqual(captured["stderr"], "/dev/null")
        self.assertEqual(captured["streams"], ("read", "write"))
        self.assertTrue(captured["initialized"])
        self.assertEqual(captured["calls"], [("mock_echo", {}, 10.0, True), ("mock_echo", {"invalid_output": True}, 10.0, True)])
        self.assertEqual([name for name, _ in events], ["transport_enter", "session_enter", "session_exit", "transport_exit"])
        self.assertEqual(len({id(task) for _, task in events}), 1)
