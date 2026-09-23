from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _payload(response) -> dict:
    for item in response.content:
        text = getattr(item, "text", "")
        if text:
            return json.loads(text)
    raise RuntimeError("MCP response had no JSON text content")


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    parameters = StdioServerParameters(
        command=os.environ.get("CODEX_ASYNC_SMOKE_PYTHON", os.sys.executable),
        args=["-m", "codex_local_ops.server"],
        env=env,
    )
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        raise RuntimeError("PowerShell not found")

    report: dict[str, object] = {}
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            required = {"job_start", "run_script_async", "job_status", "job_output", "job_cancel", "job_list"}
            report["missing_tools"] = sorted(required - names)

            start_response = await session.call_tool(
                "job_start",
                {
                    "argv": [
                        shell,
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "Write-Output mcp-start; Start-Sleep -Seconds 1; Write-Output mcp-end",
                    ],
                    "label": "mcp-async-smoke",
                },
            )
            started = _payload(start_response)
            report["start"] = started
            sid = started["session_id"]

            running = False
            current: dict = {}
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                current = _payload(await session.call_tool("job_status", {"session_id": sid}))
                if current.get("status") == "RUNNING":
                    running = True
                    break
                if current.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                await asyncio.sleep(0.05)
            report["running_seen"] = running
            report["running_status"] = current

            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                current = _payload(await session.call_tool("job_status", {"session_id": sid}))
                if current.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                await asyncio.sleep(0.05)
            report["final_status"] = current
            report["output"] = _payload(await session.call_tool("job_output", {"session_id": sid}))
            listed = _payload(await session.call_tool("job_list", {"limit": 20}))
            report["listed"] = sid in {row["session_id"] for row in listed["jobs"]}

            cancel_start = _payload(
                await session.call_tool(
                    "job_start",
                    {
                        "argv": [
                            shell,
                            "-NoProfile",
                            "-NonInteractive",
                            "-Command",
                            "Write-Output cancel-start; Start-Sleep -Seconds 20",
                        ],
                        "label": "mcp-cancel-smoke",
                    },
                )
            )
            report["cancel"] = _payload(
                await session.call_tool("job_cancel", {"session_id": cancel_start["session_id"]})
            )

    ok = (
        not report["missing_tools"]
        and report["start"]["status"] == "STARTED"
        and report["running_seen"] is True
        and report["final_status"]["status"] == "COMPLETED"
        and report["final_status"]["cleanup_complete"] is True
        and "mcp-start" in report["output"]["stdout"]
        and "mcp-end" in report["output"]["stdout"]
        and report["listed"] is True
        and report["cancel"]["status"] == "CANCELLED"
    )
    report["status"] = "PASS" if ok else "FAIL"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
