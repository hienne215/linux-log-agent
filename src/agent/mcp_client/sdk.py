"""Exact MCP v2.3.0 stdio binding; loaded only for a real connection."""
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
import os

from .client import MCPClientError, StdioConfig


def _child_environment(config, inherited, *, platform=None, parent=None):
    """Blank SDK inheritance, retaining only required Windows runtime metadata.

    Never inherit USERPROFILE/HOME, tokens, proxy settings or the parent's PATH.
    This is launcher configuration, not model-controlled request data.
    """
    env = dict.fromkeys(inherited, "")
    env.update({"PATH": "/usr/bin:/bin", "PYTHONUNBUFFERED": "1"})
    if (platform or os.name) == "nt":
        import ntpath
        source = os.environ if parent is None else parent
        root = next((value for key, value in source.items() if key.upper() == "SYSTEMROOT"), None)
        if (not isinstance(root, str) or not root or "\x00" in root
                or not ntpath.isabs(root)):
            raise MCPClientError("INVALID_CONFIG")
        for key in tuple(env):
            if key.upper() == "SYSTEMROOT":
                del env[key]
        env["SYSTEMROOT"] = root
        env["PATH"] = ntpath.join(root, "System32")
    env.update(config.env)
    return env


@asynccontextmanager
async def sdk_peer(config: StdioConfig):
    try:
        installed = version("mcp")
    except PackageNotFoundError:
        raise MCPClientError("SDK_UNAVAILABLE") from None
    if installed != "2.3.0":
        raise MCPClientError("SDK_VERSION_MISMATCH")
    # Verified against the official v2.3.0 tag, not a v1 example.
    try:
        from mcp.client.stdio import DEFAULT_INHERITED_ENV_VARS, StdioServerParameters, stdio_client
        from mcp.client.session import ClientSession
        from mcp_types import PaginatedRequestParams
    except ImportError:
        raise MCPClientError("SDK_UNAVAILABLE") from None

    # SDK merges its baseline inherited env. Override every baseline entry, then
    # permit only configured noncredential variables; never mutate os.environ.
    env = _child_environment(config, DEFAULT_INHERITED_ENV_VARS)
    params = StdioServerParameters(command=config.command, args=list(config.args), env=env)

    class SessionPeer:
        async def list_tools(self, cursor):
            return await session.list_tools(params=PaginatedRequestParams(cursor=cursor))

        async def call_tool(self, name, arguments):
            # One send only. Do not use Client.call_tool's recovery/input loop.
            try:
                return await session.call_tool(name, arguments, read_timeout_seconds=config.request_timeout, allow_input_required=True)
            except RuntimeError:
                # v2.3.0 reports output-schema failures as RuntimeError, which
                # may contain the rejected value. Expose only our safe code.
                raise MCPClientError("MALFORMED_RESULT") from None

    with open(os.devnull, "w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            # The SDK default also governs initialize. A short request budget
            # must not shorten connect_timeout. Wrapper deadlines bound each
            # phase; call_tool additionally receives its own SDK read timeout.
            async with ClientSession(read, write, read_timeout_seconds=max(config.connect_timeout, config.request_timeout)) as session:
                # Explicit supported handshake path. Modern auto-discover is
                # deliberately deferred until the server launch contract is set.
                await session.initialize()
                yield SessionPeer()
