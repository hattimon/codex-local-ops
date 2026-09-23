import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_handshake_and_bootstrap_call():
    async def exercise():
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root / "src") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        params = StdioServerParameters(command=sys.executable, args=["-m", "codex_local_ops.server"], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                inventory = await session.list_tools()
                names = {tool.name for tool in inventory.tools}
                assert "platform_info" in names
                result = await session.call_tool("platform_info", {})
                assert result.isError is False
    asyncio.run(exercise())
