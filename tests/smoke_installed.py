"""Exercise the Android Control MCP entry point installed from a wheel."""

import asyncio
import sysconfig
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def smoke() -> None:
    executable = Path(sysconfig.get_path("scripts")) / "android-control-mcp"
    parameters = StdioServerParameters(command=str(executable))
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {"list_devices", "foreground", "snapshot"}
            result = await session.call_tool("foreground", {})
            assert result.isError
            assert "explicit valid device serial" in result.content[0].text


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(smoke(), timeout=20))
    print("Installed Android Control MCP entry point passed.")
