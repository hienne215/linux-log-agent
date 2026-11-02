
from mcp.server import MCPServer

mcp = MCPServer("linux-log-agent-smoke-test")


@mcp.tool()
def ping(message: str) -> dict[str, str]:
    """Check that the MCP server can receive and respond to a tool call."""
    return {
        "status": "ok",
        "message": message,
        "server": "linux-log-agent-smoke-test",
    }


if __name__ == "__main__":
    mcp.run()
