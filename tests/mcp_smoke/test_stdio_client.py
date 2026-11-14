
import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters


SERVER_FILE = Path(__file__).with_name("mock_server.py").resolve()


async def main():
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_FILE)],
    )

    async with Client(server) as client:
        print("1. STDIO CONNECTION: OK")
        print("2. PROTOCOL VERSION:", client.protocol_version)
        print("3. SERVER INFO:", client.server_info)

        tools_result = await client.list_tools()
        tool_names = [tool.name for tool in tools_result.tools]

        print("4. DISCOVERED TOOLS:", tool_names)
        assert "ping" in tool_names

        result = await client.call_tool(
            "ping",
            {"message": "Hello from SCRUM-12"},
        )

        print("5. CALL TOOL RESULT")
        print("is_error:", result.is_error)
        print("structured_content:", result.structured_content)
        print("content:", result.content)

        assert result.is_error is False
        assert result.structured_content is not None
        assert result.structured_content["status"] == "ok"
        assert result.structured_content["message"] == "Hello from SCRUM-12"

        print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    asyncio.run(main())
