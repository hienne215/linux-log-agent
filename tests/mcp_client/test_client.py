import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
import unittest

from agent.mcp_client import MCPClientError, StdioConfig, StdioMCPClient


class FakeServer:
    def __init__(self):
        self.page = {"tools": [{"name": "mock_echo", "inputSchema": {"type": "object"}}]}
        self.result = {"content": [{"type": "text", "text": "mock evidence"}], "structuredContent": {"sample": True}, "isError": False}
        self.calls = []
        self.entered = self.exited = 0
        self.enter_task = self.exit_task = None
        self.error = None
        self.wait = False
        self.open_wait = False
        self.started = asyncio.Event()
        self.close_wait = None

    @asynccontextmanager
    async def factory(self, config):
        self.entered += 1
        self.enter_task = asyncio.current_task()
        try:
            if self.open_wait:
                await asyncio.Event().wait()
            yield self
        finally:
            if self.close_wait is not None:
                await self.close_wait.wait()
            self.exited += 1
            self.exit_task = asyncio.current_task()

    async def list_tools(self, cursor):
        self.calls.append(("list", cursor))
        return self.page

    async def call_tool(self, name, arguments):
        self.calls.append(("call", name, arguments))
        self.started.set()
        if self.error:
            raise self.error
        if self.wait:
            await asyncio.Event().wait()
        return self.result


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = FakeServer()
        self.config = StdioConfig("/trusted/python", allowed_tools=frozenset({"mock_echo"}), request_timeout=.03, connect_timeout=.03, cleanup_timeout=.1)
        self.client = StdioMCPClient(self.config, peer_factory=self.server.factory)

    async def asyncTearDown(self):
        if self.server.close_wait is not None:
            self.server.close_wait.set()
        await self.client.disconnect()

    async def ready(self):
        await self.client.connect()
        await self.client.list_tools()

    async def assert_code(self, code, awaitable):
        with self.assertRaises(MCPClientError) as caught:
            await awaitable
        self.assertEqual(caught.exception.code, code)

    async def test_connect_disconnect_idempotent(self):
        await self.client.connect()
        await self.client.connect()
        self.assertTrue(self.client.connected)
        await self.client.disconnect()
        await self.client.disconnect()
        self.assertEqual((self.server.entered, self.server.exited), (1, 1))

    async def test_same_task_owns_sdk_lifecycle(self):
        async with self.client:
            self.assertNotEqual(self.server.enter_task, asyncio.current_task())
        self.assertIs(self.server.enter_task, self.server.exit_task)

    async def test_tool_discovery(self):
        await self.client.connect()
        page = await self.client.list_tools()
        self.assertEqual(page.tools[0]["inputSchema"], {"type": "object"})

    async def test_discovery_pagination_is_explicit(self):
        await self.client.connect()
        self.server.page["nextCursor"] = "opaque"
        page = await self.client.list_tools("incoming")
        self.assertEqual(page.next_cursor, "opaque")
        self.assertEqual(self.server.calls, [("list", "incoming")])

    async def test_mock_call_normalizes(self):
        await self.ready()
        result = await self.client.call_tool("mock_echo", {"sample": "ok"})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content, {"sample": True})

    async def test_sdk_model_dump_aliases(self):
        await self.ready()
        self.server.result = SimpleNamespace(model_dump=lambda **kw: {"content": [], "isError": False})
        result = await self.client.call_tool("mock_echo", {})
        self.assertEqual(result.content, ())

    async def test_is_error_is_business_result(self):
        await self.ready()
        self.server.result["isError"] = True
        self.assertTrue((await self.client.call_tool("mock_echo", {})).is_error)
        self.assertTrue(self.client.connected)

    async def test_disconnected_server_normalized(self):
        await self.ready()
        self.server.error = EOFError("synthetic private payload")
        await self.assert_code("DISCONNECTED", self.client.call_tool("mock_echo", {}))
        self.assertFalse(self.client.connected)
        self.assertEqual(self.server.exited, 1)

    async def test_request_timeout_cleans_up(self):
        await self.ready()
        self.server.wait = True
        await self.assert_code("TIMEOUT", self.client.call_tool("mock_echo", {}))
        self.assertEqual(self.server.exited, 1)
        self.assertFalse(self.client.connected)

    async def test_connect_timeout_cleans_up(self):
        self.server.open_wait = True
        await self.assert_code("TIMEOUT", self.client.connect())
        self.assertEqual(self.server.exited, 1)

    async def test_cancel_request_propagates_and_cleans(self):
        await self.ready()
        self.server.wait = True
        task = asyncio.create_task(self.client.call_tool("mock_echo", {}))
        await self.server.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.server.exited, 1)

    async def test_cancel_connect_cleans(self):
        self.server.open_wait = True
        task = asyncio.create_task(self.client.connect())
        while not self.server.entered:
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.server.exited, 1)

    async def test_busy_has_no_second_request(self):
        await self.ready()
        self.server.wait = True
        task = asyncio.create_task(self.client.call_tool("mock_echo", {}))
        await self.server.started.wait()
        await self.assert_code("BUSY", self.client.list_tools())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_no_retry_even_for_write_named_mock(self):
        self.client = StdioMCPClient(replace(self.config, allowed_tools=frozenset({"mock_write"})), peer_factory=self.server.factory)
        self.server.page["tools"][0]["name"] = "mock_write"
        await self.ready()
        self.server.error = ConnectionError("synthetic private payload")
        await self.assert_code("DISCONNECTED", self.client.call_tool("mock_write", {}))
        self.assertEqual(sum(c[0] == "call" for c in self.server.calls), 1)
        self.assertEqual(self.server.entered, 1)

    async def test_malformed_results(self):
        await self.ready()
        for raw in [None, [], {"content": "bad"}, {"content": [], "isError": "false"}, {"content": [], "structuredContent": []}, {"content": [{"type": "text", "text": 2}]}, {"content": ["bad"]}]:
            with self.subTest(shape=type(raw).__name__):
                self.server.result = raw
                await self.assert_code("MALFORMED_RESULT", self.client.call_tool("mock_echo", {}))

    async def test_input_required_never_replayed(self):
        await self.ready()
        self.server.result = {"resultType": "input_required", "content": []}
        await self.assert_code("UNSUPPORTED_RESULT", self.client.call_tool("mock_echo", {}))
        self.assertEqual(sum(c[0] == "call" for c in self.server.calls), 1)

    async def test_bad_discovery(self):
        await self.client.connect()
        for page in [{}, {"tools": [None]}, {"tools": [{"name": "x"}]}, {"tools": [], "nextCursor": 1}]:
            self.server.page = page
            await self.assert_code("MALFORMED_RESULT", self.client.list_tools())

    async def test_discovery_does_not_authorize(self):
        self.server.page["tools"].append({"name": "other", "inputSchema": {}})
        await self.ready()
        await self.assert_code("TOOL_NOT_ALLOWED", self.client.call_tool("other", {}))

    async def test_discovery_required(self):
        await self.client.connect()
        await self.assert_code("TOOL_NOT_ALLOWED", self.client.call_tool("mock_echo", {}))

    async def test_invalid_arguments_never_sent(self):
        await self.ready()
        for args in [[], {1: "value"}, {"nested": {1: "value"}}, {"n": float("nan")}, {"x": object()}]:
            await self.assert_code("INVALID_ARGUMENTS", self.client.call_tool("mock_echo", args))
        self.assertEqual(self.server.calls, [("list", None)])

    async def test_utf8_output_cap(self):
        await self.ready()
        self.server.result = {"content": [{"type": "text", "text": "漢" * 23000}]}
        await self.assert_code("RESULT_TOO_LARGE", self.client.call_tool("mock_echo", {}))

    async def test_safe_errors_and_repr(self):
        await self.ready()
        self.server.result["content"][0]["text"] = "synthetic_private_value"
        result = await self.client.call_tool("mock_echo", {})
        self.assertNotIn("synthetic_private_value", repr(result))
        self.server.error = RuntimeError("synthetic_private_value")
        with self.assertRaises(MCPClientError) as caught:
            await self.client.call_tool("mock_echo", {})
        self.assertNotIn("synthetic_private_value", str(caught.exception))

    async def test_cleanup_timeout_retains_ownership(self):
        self.server.close_wait = asyncio.Event()
        await self.client.connect()
        await self.assert_code("CLEANUP_TIMEOUT", self.client.disconnect())
        await self.assert_code("INVALID_STATE", self.client.connect())
        self.server.close_wait.set()
        await self.client.disconnect()
        self.assertEqual(self.server.exited, 1)

    async def test_not_connected(self):
        await self.assert_code("NOT_CONNECTED", self.client.list_tools())

    async def test_reconnect_clears_discovery(self):
        await self.ready()
        await self.client.disconnect()
        await self.client.connect()
        await self.assert_code("TOOL_NOT_ALLOWED", self.client.call_tool("mock_echo", {}))

    async def test_timeout_does_not_retry_write_named_mock(self):
        self.client = StdioMCPClient(replace(self.config, allowed_tools=frozenset({"mock_write"})), peer_factory=self.server.factory)
        self.server.page["tools"][0]["name"] = "mock_write"
        self.server.wait = True
        await self.ready()
        await self.assert_code("TIMEOUT", self.client.call_tool("mock_write", {}))
        self.assertEqual(sum(c[0] == "call" for c in self.server.calls), 1)

    async def test_spawn_failure_is_safe(self):
        @asynccontextmanager
        async def fail(config):
            raise FileNotFoundError("synthetic private path")
            yield
        self.client = StdioMCPClient(self.config, peer_factory=fail)
        await self.assert_code("TRANSPORT_ERROR", self.client.connect())
        self.assertFalse(self.client.connected)

    async def test_cleanup_failure_is_safe(self):
        @asynccontextmanager
        async def fail(config):
            try:
                yield self.server
            finally:
                raise RuntimeError("synthetic private payload")
        self.client = StdioMCPClient(self.config, peer_factory=fail)
        await self.client.connect()
        await self.assert_code("TRANSPORT_ERROR", self.client.disconnect())

    async def test_duplicate_tool_names_rejected(self):
        await self.client.connect()
        self.server.page["tools"] *= 2
        await self.assert_code("MALFORMED_RESULT", self.client.list_tools())

    async def test_invalid_cursor_never_sent(self):
        await self.client.connect()
        await self.assert_code("INVALID_ARGUMENTS", self.client.list_tools(3))
        self.assertEqual(self.server.calls, [])

    async def test_nested_transport_exception_group(self):
        await self.ready()
        self.server.error = ExceptionGroup("private", [EOFError("private")])
        await self.assert_code("DISCONNECTED", self.client.call_tool("mock_echo", {}))

    async def test_disconnected_during_discovery(self):
        async def failed(cursor):
            raise EOFError("synthetic private payload")
        self.server.list_tools = failed
        await self.client.connect()
        await self.assert_code("DISCONNECTED", self.client.list_tools())
        self.assertEqual(self.server.exited, 1)

    async def test_cancel_idle_closes_session(self):
        await self.client.connect()
        await self.client.disconnect()
        self.assertEqual(self.server.exited, 1)

    async def test_concurrent_disconnect_is_idempotent(self):
        await self.client.connect()
        await asyncio.gather(self.client.disconnect(), self.client.disconnect())
        self.assertEqual(self.server.exited, 1)

    async def test_discovery_refresh_removes_stale_tool(self):
        await self.ready()
        self.server.page = {"tools": []}
        await self.client.list_tools()
        await self.assert_code("TOOL_NOT_ALLOWED", self.client.call_tool("mock_echo", {}))

    async def assert_sdk_transport_code(self, code, expected):
        class MCPError(Exception):
            def __init__(self):
                self.code = code
                super().__init__("synthetic_private_value")
        await self.ready()
        self.server.error = MCPError()
        await self.assert_code(expected, self.client.call_tool("mock_echo", {}))
        self.assertEqual(self.server.exited, 1)

    async def test_sdk_connection_closed_code(self):
        await self.assert_sdk_transport_code(-32000, "DISCONNECTED")

    async def test_sdk_request_timeout_code(self):
        await self.assert_sdk_transport_code(-32001, "TIMEOUT")

    async def test_sdk_other_protocol_error_code(self):
        await self.assert_sdk_transport_code(-32602, "PROTOCOL_ERROR")


class ConfigTests(unittest.TestCase):
    def test_invalid_config(self):
        for change in [{"command": "python"}, {"args": ("bad\narg",)}, {"request_timeout": 0}, {"connect_timeout": float("nan")}, {"cleanup_timeout": 61}, {"env": {"API_KEY": "synthetic"}}, {"max_result_bytes": True}]:
            with self.subTest(fields=list(change)):
                with self.assertRaises(MCPClientError):
                    StdioConfig(**({"command": "/trusted/python"} | change))

    def test_env_snapshot(self):
        env = {"LANG": "C.UTF-8"}
        config = StdioConfig("/trusted/python", env=env)
        env["LANG"] = "changed"
        self.assertEqual(config.env["LANG"], "C.UTF-8")
