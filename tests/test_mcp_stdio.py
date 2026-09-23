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
                assert {
                    "git_status",
                    "git_diff",
                    "git_log",
                    "git_branch_list",
                    "git_remote_list",
                    "github_auth_status",
                    "github_repo_view",
                    "github_workflow_list",
                    "github_workflow_runs",
                    "github_workflow_run_view",
                    "github_release_list",
                    "github_release_view",
                    "github_pr_list",
                    "github_pr_view",
                } <= names
                assert {
                    "git_add",
                    "git_commit",
                    "git_push",
                    "git_publish",
                    "git_tag_create",
                    "git_remote_modify",
                    "github_release_create",
                }.isdisjoint(names)
                result = await session.call_tool("platform_info", {})
                assert result.isError is False
    asyncio.run(exercise())
