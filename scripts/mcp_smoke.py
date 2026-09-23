"""Run a read-only STDIO smoke test against the installed Local Ops server."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "codex_local_ops.server"], env=env)
    report: dict[str, object] = {}
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            report["tool_count"] = len(tools.tools)
            for tool in (
                "platform_info",
                "platform_capabilities",
                "local_system_info",
                "ssh_agent_status",
                "ssh_agent_keys",
                "wsl_list",
                "docker_info",
                "docker_ps",
            ):
                response = await session.call_tool(tool, {})
                report[tool] = {
                    "is_error": response.isError,
                    "content": [getattr(item, "text", str(item)) for item in response.content],
                }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
