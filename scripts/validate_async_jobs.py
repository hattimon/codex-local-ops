from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

from codex_local_ops import jobs


def main() -> int:
    report: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="clops-job-") as tmp:
        root = Path(tmp)
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell is None:
            raise RuntimeError("PowerShell not found")
        started = jobs.start(
            [
                shell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Write-Output async-start; Start-Sleep -Seconds 1; Write-Output async-end",
            ],
            cwd=root,
            label="async-validation",
        )
        report["start"] = started
        sid = str(started["session_id"])

        seen_running = False
        deadline = time.monotonic() + 4
        current = jobs.status(sid)
        while time.monotonic() < deadline:
            current = jobs.status(sid)
            if current["status"] == "RUNNING":
                seen_running = True
                break
            if current["status"] in jobs.TERMINAL_STATES:
                break
            time.sleep(0.05)
        report["running_seen"] = seen_running
        report["running_status"] = current

        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            current = jobs.status(sid)
            if current["status"] in jobs.TERMINAL_STATES:
                break
            time.sleep(0.05)
        report["final_status"] = current
        report["output"] = jobs.output(sid)
        report["list_contains_session"] = sid in {row["session_id"] for row in jobs.list_jobs(limit=20)["jobs"]}

    ok = (
        report["start"]["status"] == "STARTED"
        and report["running_seen"] is True
        and report["final_status"]["status"] == "COMPLETED"
        and report["final_status"]["cleanup_complete"] is True
        and "async-start" in report["output"]["stdout"]
        and "async-end" in report["output"]["stdout"]
        and report["list_contains_session"] is True
    )
    report["status"] = "PASS" if ok else "FAIL"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
