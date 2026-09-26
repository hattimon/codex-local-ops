from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from codex_local_ops import jobs, server
from codex_local_ops.processes import (
    SYNC_MAX_TIMEOUT_SECONDS,
    execution_resume_key,
    run,
    shell_execution_policy,
)


@pytest.mark.parametrize(
    ("command", "category"),
    [
        ("pytest -q", "pytest"),
        ("python -m pytest -q", "pytest"),
        ("py -3.12 -m pytest -q", "pytest"),
        ("python -m unittest discover -s tests", "unittest"),
        ("py -3.12 -m unittest discover -s tests", "unittest"),
        ("docker build .", "docker-build"),
        ("docker compose build", "docker-build"),
        ("python -m build", "build"),
        ("ffmpeg -i input.mp4 output.mp4", "media-transform"),
        ("python scripts/validate-long.py", "validation"),
    ],
)
def test_long_running_commands_are_classified_for_async(command: str, category: str) -> None:
    policy = shell_execution_policy(command, 120)

    assert policy["mode"] == "ASYNC"
    assert policy["category"] == category


def test_short_command_stays_synchronous_within_budget() -> None:
    policy = shell_execution_policy("echo ok", 2)

    assert policy["mode"] == "SYNC"
    assert policy["sync_budget_seconds"] == SYNC_MAX_TIMEOUT_SECONDS


def test_short_local_shell_command_still_executes_synchronously(monkeypatch) -> None:
    calls: list[tuple[list[str], int]] = []

    def fake_run(argv, *, cwd=None, timeout=60, **kwargs):
        calls.append((list(argv), timeout))
        return {
            "argv": list(argv),
            "exit_code": 0,
            "stdout": "ok\n",
            "stderr": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "timed_out": False,
            "timeout_seconds": timeout,
            "requested_timeout_seconds": timeout,
            "duration_ms": 1.0,
        }

    monkeypatch.setattr(server, "_shell_exe", lambda: "powershell.exe")
    monkeypatch.setattr(server, "run", fake_run)
    monkeypatch.setattr(
        server.jobs,
        "start_or_resume",
        lambda *args, **kwargs: pytest.fail("short command was incorrectly routed async"),
    )

    result = server._local_shell_action("Write-Output ok", None, 5)

    assert result["status"] == "OK"
    assert result["stdout"] == "ok\n"
    assert len(calls) == 1
    assert calls[0][1] == 5


def test_timeout_above_sync_budget_is_routed_async() -> None:
    policy = shell_execution_policy("echo ok", SYNC_MAX_TIMEOUT_SECONDS + 1)

    assert policy["mode"] == "ASYNC"
    assert "synchronous budget" in str(policy["reason"])


def test_process_timeout_returns_without_waiting_for_descendant_handles(tmp_path: Path) -> None:
    marker = tmp_path / "descendant-survived.txt"
    child_code = (
        "import time; from pathlib import Path; "
        f"time.sleep(2.0); Path({str(marker)!r}).write_text('alive', encoding='utf-8')"
    )
    parent_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "print('parent-started', flush=True); "
        "print('parent-stderr', file=sys.stderr, flush=True); "
        "time.sleep(10)"
    )

    started = time.monotonic()
    result = run([sys.executable, "-c", parent_code], timeout=1)
    elapsed = time.monotonic() - started

    assert result["timed_out"] is True
    assert result["exit_code"] is None
    assert "parent-started" in result["stdout"]
    assert "parent-stderr" in result["stderr"]
    assert elapsed < 6
    time.sleep(2.2)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="local_shell_run regression is Windows-specific")
def test_local_shell_run_timeout_is_structured_and_bounded(monkeypatch) -> None:
    monkeypatch.setattr(server, "require_raw_execution", lambda: None)
    monkeypatch.setattr(server, "emit", lambda *args, **kwargs: None)
    shell = server._shell_exe()
    assert shell
    if Path(shell).name.lower() in {"powershell.exe", "pwsh.exe"}:
        command = "Write-Output before-timeout; Start-Sleep -Seconds 6"
    else:
        command = "echo before-timeout & ping -n 7 127.0.0.1 >nul"

    started = time.monotonic()
    result = server.local_shell_run(command, timeout=2)
    elapsed = time.monotonic() - started

    assert result["status"] == "TIMED_OUT"
    assert result["timed_out"] is True
    assert result["timeout_seconds"] == 2
    assert elapsed < 8


def test_authorization_denial_remains_distinct_from_timeout(monkeypatch) -> None:
    def deny() -> None:
        raise PermissionError("authorization required")

    monkeypatch.setattr(server, "require_raw_execution", deny)
    monkeypatch.setattr(server, "emit", lambda *args, **kwargs: None)

    result = server.local_shell_run("echo should-not-run", timeout=1)

    assert result["status"] == "PERMISSION_DENIED"
    assert result["status"] != "TIMED_OUT"


def test_long_local_shell_command_routes_to_persistent_async_job(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_start_or_resume(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return {
            "status": "STARTED",
            "session_id": "job_" + "a" * 32,
            "child_pid": 1234,
            "created_at": "2026-09-26T00:00:00+00:00",
            "resumed": False,
        }

    monkeypatch.setattr(server, "_shell_exe", lambda: "powershell.exe")
    monkeypatch.setattr(server.jobs, "start_or_resume", fake_start_or_resume)

    result = server._local_shell_action("py -3.12 -m unittest discover -s tests", None, 120)

    assert result["status"] == "STARTED"
    assert result["execution_mode"] == "async"
    assert result["requested_timeout_seconds"] == 120
    assert result["session_id"] == "job_" + "a" * 32
    assert captured["timeout"] == 120
    assert captured["resume_key"]


def test_resume_supervisor_recovers_same_running_job_without_duplicate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(tmp_path / "home"))
    argv = [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(5)"]
    key = execution_resume_key(argv, tmp_path)

    first = jobs.start_or_resume(argv, cwd=tmp_path, label="resume-test", timeout=20, resume_key=key)
    second = jobs.start_or_resume(argv, cwd=tmp_path, label="resume-test", timeout=20, resume_key=key)

    try:
        assert first["session_id"] == second["session_id"]
        assert first["child_pid"] == second["child_pid"]
        assert first["resumed"] is False
        assert second["resumed"] is True
        persisted = jobs.status(first["session_id"])
        assert persisted["status"] == "RUNNING"
    finally:
        jobs.cancel(first["session_id"])


def test_async_shell_route_deduplicates_immediately(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(tmp_path / "home"))
    argv = [sys.executable, "-c", "import time; time.sleep(5)"]
    launches: list[tuple[str, ...]] = []

    def fake_shell_argv(command: str) -> list[str]:
        launches.append((command,))
        return argv

    monkeypatch.setattr(server, "_shell_argv", fake_shell_argv)
    monkeypatch.setattr(server, "assert_trusted_path", lambda path, must_exist=False: Path(path).resolve())
    command = "py -3.12 -m unittest discover -s tests"
    first = server._local_shell_action(command, str(tmp_path), 120)
    second = server._local_shell_action(command, str(tmp_path), 120)

    try:
        assert first["session_id"] == second["session_id"]
        assert first["resumed"] is False
        assert second["resumed"] is True
        assert len(launches) == 2  # route resolution; only one process is actually started
        assert jobs.list_jobs()["count"] == 1
    finally:
        jobs.cancel(first["session_id"])


@pytest.mark.skipif(os.name != "nt", reason="public MCP subprocess regression is Windows-specific")
def test_public_local_shell_run_resumes_across_server_processes(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "shared-local-ops-home"
    cwd = tmp_path / "working-directory"
    cwd.mkdir()
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(home))
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
    command = "Start-Sleep -Seconds 30"
    resume_key = "public-boundary-fixed-key"
    invoke = (
        "import json; from pathlib import Path; "
        "from codex_local_ops import server; "
        "server.require_raw_execution = lambda: None; "
        "server.emit = lambda *args, **kwargs: None; "
        "server.assert_trusted_path = lambda path, must_exist=False: Path(path).resolve(); "
        "payload=json.loads(__import__('sys').stdin.read()); "
        "print(json.dumps(server.local_shell_run(**payload)))"
    )

    def call_in_new_server_process() -> dict:
        result = subprocess.run(
            [sys.executable, "-c", invoke],
            input=json.dumps({"command": command, "cwd": str(cwd), "timeout": 181, "resume_key": resume_key}),
            text=True,
            capture_output=True,
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            timeout=15,
            check=True,
        )
        return json.loads(result.stdout)

    first = call_in_new_server_process()
    second = call_in_new_server_process()
    try:
        assert first["session_id"] == second["session_id"]
        assert first["resumed"] is False
        assert second["resumed"] is True
        persisted = jobs.list_jobs()
        assert persisted["count"] == 1
        matching = [job for job in persisted["jobs"] if job["session_id"] == first["session_id"]]
        assert len(matching) == 1
        assert matching[0]["status"] == "RUNNING"
        assert jobs._root() == home / "jobs"
    finally:
        jobs.cancel(first["session_id"])


def test_resume_key_scope_and_terminal_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(tmp_path / "home"))
    running = [sys.executable, "-c", "import time; time.sleep(5)"]
    first = jobs.start_or_resume(running, cwd=tmp_path, resume_key="key-one")
    other_key = jobs.start_or_resume(running, cwd=tmp_path, resume_key="key-two")
    without_key_one = jobs.start(running, cwd=tmp_path)
    without_key_two = jobs.start(running, cwd=tmp_path)

    try:
        assert len({first["session_id"], other_key["session_id"]}) == 2
        assert without_key_one["session_id"] != without_key_two["session_id"]
    finally:
        for result in (first, other_key, without_key_one, without_key_two):
            jobs.cancel(result["session_id"])

    completed_cmd = [sys.executable, "-c", "pass"]
    completed = jobs.start_or_resume(completed_cmd, cwd=tmp_path, resume_key="completed-key")
    deadline = time.monotonic() + 5
    while jobs.status(completed["session_id"])["status"] not in jobs.TERMINAL_STATES:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    restarted = jobs.start_or_resume(completed_cmd, cwd=tmp_path, resume_key="completed-key")
    try:
        assert restarted["session_id"] != completed["session_id"]
        assert restarted["resumed"] is False
    finally:
        jobs.cancel(restarted["session_id"])

    cancelled = jobs.start_or_resume(running, cwd=tmp_path, resume_key="cancelled-key")
    jobs.cancel(cancelled["session_id"])
    restarted_cancelled = jobs.start_or_resume(running, cwd=tmp_path, resume_key="cancelled-key")
    try:
        assert restarted_cancelled["session_id"] != cancelled["session_id"]
        assert restarted_cancelled["resumed"] is False
    finally:
        jobs.cancel(restarted_cancelled["session_id"])
