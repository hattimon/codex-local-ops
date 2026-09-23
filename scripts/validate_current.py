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
    from codex_local_ops import git_ops, ssh_ops
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
        "git_status", "git_diff", "git_log", "git_branch_list", "git_remote_list",
        "github_auth_status", "github_repo_view", "github_workflow_list",
        "github_workflow_runs", "github_workflow_run_view", "github_release_list",
        "github_release_view", "github_pr_list", "github_pr_view",
        "animation_backends", "animation_render", "health_check",
    }
    missing = sorted(required - names)
    report["missing_tools"] = missing
    forbidden_git_github = {
        "git_add", "git_commit", "git_push", "git_publish", "git_tag_create",
        "git_remote_modify", "github_release_create",
    }
    exposed_mutating = sorted(forbidden_git_github & names)
    report["mutating_git_github_mcp_tools"] = exposed_mutating

    sanitized = sanitize({"password": "secret-value", "safe": "visible"})
    report["secret_redaction"] = sanitized.get("password") == "[REDACTED]" and sanitized.get("safe") == "visible"
    old_gh_token = os.environ.get("GH_TOKEN")
    old_github_token = os.environ.get("GITHUB_TOKEN")
    try:
        os.environ["GH_TOKEN"] = "validator-gh-token"
        os.environ["GITHUB_TOKEN"] = "validator-github-token"
        child_env = git_ops._github_env()
        report["github_credential_isolation"] = (
            "GH_TOKEN" not in child_env
            and "GITHUB_TOKEN" not in child_env
            and os.environ.get("GH_TOKEN") == "validator-gh-token"
            and os.environ.get("GITHUB_TOKEN") == "validator-github-token"
        )
    finally:
        if old_gh_token is None:
            os.environ.pop("GH_TOKEN", None)
        else:
            os.environ["GH_TOKEN"] = old_gh_token
        if old_github_token is None:
            os.environ.pop("GITHUB_TOKEN", None)
        else:
            os.environ["GITHUB_TOKEN"] = old_github_token
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
        and not exposed_mutating
        and report["secret_redaction"]
        and report["github_credential_isolation"]
        and report["ssh_read_only_guard"]
        and report["ssh_operations_guard"]
        and pytest_ok
    )
    report["status"] = "PASS" if ok else "FAIL"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
