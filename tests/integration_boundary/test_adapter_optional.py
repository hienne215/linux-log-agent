"""Merged Adapter + bridge + in-memory MCP, mocked HTTP only.

SCRUM-22's branch does not yet contain merged LLM files. Run in an isolated test
composition with origin/main, as documented. Never run inference here.
"""
import json
import unittest

from agent.integration_boundary import BoundaryError, ToolBridge
from agent.mcp_client import StdioConfig, StdioMCPClient
from fixtures import Peer, sample_tool

try:
    from agent.llm.interface import AdapterError, Message, ToolDefinition
    from agent.llm.ollama import OllamaAdapter, OllamaConfig
    from agent.llm.redaction import _clean
    HAS_ADAPTER = True
except ModuleNotFoundError as error:
    if error.name not in {"agent.llm", "agent.llm.interface"}:
        raise
    HAS_ADAPTER = False


class MockHTTP:
    def __init__(self):
        self.requests = []
        self.arguments = {"sample": "safe"}

    def post(self, url, body, timeout, limit):
        self.requests.append(json.loads(body))
        message = {"role": "assistant", "content": "synthetic conclusion"}
        if len(self.requests) == 1:
            message = {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "mock_echo", "arguments": self.arguments}}]}
        return json.dumps({"model": "qwen3:4b-instruct-2507-q4_K_M", "done": True,
                           "done_reason": "stop", "message": message,
                           "eval_count": 12, "prompt_eval_count": 32}).encode()


@unittest.skipUnless(HAS_ADAPTER, "requires merged Adapter in isolated test composition")
class AdapterBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.peer = Peer()
        self.client = StdioMCPClient(StdioConfig("/trusted/python", allowed_tools=frozenset({"mock_echo"})),
                                    peer_factory=self.peer.factory)
        await self.client.connect()
        await self.client.list_tools()
        self.bridge = ToolBridge(self.client, (sample_tool(),), redact=_clean)
        self.tools = tuple(ToolDefinition(t.name, t.description, t.parameters) for t in self.bridge.definitions())
        self.http = MockHTTP()
        self.adapter = OllamaAdapter(OllamaConfig(), transport=self.http)

    async def asyncTearDown(self):
        await self.client.disconnect()

    async def test_explicit_one_step_roundtrip_with_mock_http(self):
        request = Message("user", "Inspect the synthetic sample")
        completion = self.adapter.complete([request], self.tools, require_tool=True)
        call = completion.tool_calls[0]
        message = await self.bridge.execute(call.name, call.arguments)
        history = [request, Message("assistant", tool_calls=completion.tool_calls), Message(**message.message_fields())]
        answer = self.adapter.complete(history, self.tools)
        self.assertEqual(answer.kind, "text")
        self.assertEqual(answer.text, "synthetic conclusion")
        self.assertEqual(len(self.peer.calls), 1)
        self.assertEqual(len(self.http.requests), 2)
        self.assertNotIn("think", self.http.requests[0])
        self.assertEqual(self.http.requests[1]["messages"][-1]["role"], "tool")

    async def test_real_adapter_redactor_prevents_secret_in_http(self):
        self.peer.result["content"][0]["text"] = "db_password=synthetic-value"
        self.peer.result["structuredContent"]["client_secret"] = "synthetic-value"
        message = await self.bridge.execute("mock_echo", {"sample": "safe"})
        self.http.requests.append({"fixture": "skip proposal"})
        self.adapter.complete([Message("user", "Summarize"), Message(**message.message_fields())], self.tools)
        self.assertNotIn("synthetic-value", json.dumps(self.http.requests[-1]))
        self.assertEqual(json.loads(message.content)["structuredContent"]["evidence"][0]["id"], "e-1")

    async def test_adapter_rejects_invalid_native_arguments(self):
        self.http.arguments = {"sample": 123}
        with self.assertRaises(AdapterError) as caught:
            self.adapter.complete([Message("user", "fixture")], self.tools, require_tool=True)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENTS")
        self.assertEqual(self.peer.calls, [])

    async def test_actual_redactor_rejects_secret_arguments_before_mcp(self):
        with self.assertRaises(BoundaryError) as caught:
            await self.bridge.execute("mock_echo", {"sample": "password=synthetic-value"})
        self.assertEqual(caught.exception.code, "DATA_POLICY")
        self.assertEqual(self.peer.calls, [])
