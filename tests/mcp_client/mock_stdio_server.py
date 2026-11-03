"""Local test fixture only. Not the SCRUM-12 server or a project contract."""
from mcp.server import MCPServer

server = MCPServer("scrum22-test-fixture")


@server.tool()
def mock_echo(sample: str) -> dict[str, str]:
    """Return synthetic data unchanged. No external commands or I/O."""
    return {"sample": sample}


if __name__ == "__main__":
    server.run(transport="stdio")
