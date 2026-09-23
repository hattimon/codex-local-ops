"""Validate the current source tree using the Python that runs codexLocalOps."""
from __future__ import annotations

import compileall
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
sys.path.insert(0, str(SRC))


def main() -> int:
    report: dict[str, object] = {
        "python": sys.executable,
        "compile": False,
        "import": False,
        "tool_count": 0,
        "pytest": "not-run",
    }

    report["compile"] = bool(compileall.compile_dir(str(SRC), quiet=1))
    if not report["compile"]:
        print(json.dumps(report, indent=2))
        return 1

    from codex_local_ops.safety import sanitize
    from codex_local_ops import ssh_ops
    from codex_local_ops.server import mcp

    report["import"] = True
    names = set(mcp._tool_manager._tools)
    report["tool_count"] = len(names)
    required = {
        "platform_info", "config_status", "trusted_roots_list",
        "job_start", "run_script_async", "job_status", "job_output", "job_cancel", "job_list",
        "browser_status", "browser_download_click", "browser_screenshot",
        "desktop_info", "desktop_screenshot", "video_info", "video_contact_sheet",
        "screen_recording_status", "obs_status", "obs_streaming_start",
        "animation_backends", "animation_render", "health_check",
    }
    missing = sorted(required - names)
    report["missing_tools"] = missing

    sanitized = sanitize({"password": "secret-value", "safe": "visible"})
    report["secret_redaction"] = sanitized.get("password") == "[REDACTED]" and sanitized.get("safe") == "visible"
    report["ssh_read_only_guard"] = not ssh_ops._command_allowed("service nginx restart", "READ_ONLY")
    report["ssh_operations_guard"] = (
        ssh_ops._command_allowed("service nginx restart", "OPERATIONS")
        and not ssh_ops._command_allowed("docker restart web && reboot", "OPERATIONS")
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    try:
        import pytest  # noqa: F401
    except Exception:
        report["pytest"] = "not-installed"
        pytest_ok = True
    else:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(TESTS)],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        report["pytest"] = {
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-12000:],
            "stderr": proc.stderr[-12000:],
        }
        pytest_ok = proc.returncode == 0

    ok = bool(
        report["compile"]
        and report["import"]
        and not missing
        and report["secret_redaction"]
        and report["ssh_read_only_guard"]
        and report["ssh_operations_guard"]
        and pytest_ok
    )
    report["status"] = "PASS" if ok else "FAIL"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
