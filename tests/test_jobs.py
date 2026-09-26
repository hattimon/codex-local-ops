from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from codex_local_ops import jobs


def _wait(session_id: str, timeout: float = 8.0) -> dict:
    deadline = time.monotonic() + timeout
    last = jobs.status(session_id)
    while time.monotonic() < deadline and last["status"] not in jobs.TERMINAL_STATES:
        time.sleep(0.05)
        last = jobs.status(session_id)
    return last


def test_async_job_lifecycle_and_cancel(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(tmp_path / "home"))
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        assert shell
        argv = [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Write-Output started; Start-Sleep -Milliseconds 800; Write-Output finished",
        ]
        long_argv = [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Write-Output waiting; Start-Sleep -Seconds 20",
        ]
    else:
        argv = ["/bin/sh", "-c", "printf 'started\\n'; sleep 0.8; printf 'finished\\n'"]
        long_argv = ["/bin/sh", "-c", "printf 'waiting\\n'; sleep 20"]

    started = jobs.start(argv, cwd=tmp_path, label="lifecycle")
    assert started["status"] == "STARTED"
    session_id = started["session_id"]

    seen_running = False
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = jobs.status(session_id)
        if current["status"] == "RUNNING":
            seen_running = True
            break
        time.sleep(0.03)
    assert seen_running

    completed = _wait(session_id)
    assert completed["status"] == "COMPLETED"
    assert completed["exit_code"] == 0
    assert completed["cleanup_complete"] is True
    output = jobs.output(session_id)
    assert "started" in output["stdout"]
    assert "finished" in output["stdout"]
    second = jobs.start(long_argv, cwd=tmp_path, label="cancel")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and jobs.status(second["session_id"])["status"] != "RUNNING":
        time.sleep(0.03)
    cancelled = jobs.cancel(second["session_id"])
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["cleanup_complete"] is True

    listed = jobs.list_jobs()
    ids = {row["session_id"] for row in listed["jobs"]}
    assert session_id in ids
    assert second["session_id"] in ids


def _short_shell_command(command: str) -> list[str]:
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        assert shell
        return [shell, "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/sh", "-c", command]


def test_finished_exit_unknown_for_orphaned_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(tmp_path / "home"))
    session_id = "job_" + "a" * 32
    session_dir = jobs._root() / session_id
    session_dir.mkdir(parents=True)
    cleanup = tmp_path / "orphan-cleanup.txt"
    cleanup.write_text("remove me", encoding="utf-8")
    jobs._write_json(
        session_dir / "state.json",
        {
            "status": "RUNNING",
            "session_id": session_id,
            "label": "orphan",
            "argv": ["dummy"],
            "cwd": None,
            "created_at": jobs._now(),
            "child_pid": 999_999_999,
            "exit_code": None,
            "cleanup_complete": False,
            "_cleanup_paths": [str(cleanup)],
        },
    )

    state = jobs.status(session_id)

    assert state["status"] == "FINISHED_EXIT_UNKNOWN"
    assert state["exit_code"] is None
    assert state["cleanup_complete"] is True
    assert "_cleanup_paths" not in state
    assert not cleanup.exists()


def test_cleanup_paths_removed_after_completion(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(tmp_path / "home"))
    cleanup = tmp_path / "cleanup-after-complete.txt"
    cleanup.write_text("remove me", encoding="utf-8")
    command = "Write-Output done" if os.name == "nt" else "printf 'done\\n'"

    started = jobs.start(_short_shell_command(command), cleanup_paths=[cleanup])
    final = _wait(started["session_id"])

    assert final["status"] == "COMPLETED"
    assert final["cleanup_complete"] is True
    assert not cleanup.exists()


def test_cancel_and_watcher_do_not_overwrite_terminal_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(tmp_path / "home"))
    cleanup = tmp_path / "cleanup-after-cancel.txt"
    cleanup.write_text("remove me", encoding="utf-8")
    command = "Write-Output waiting; Start-Sleep -Seconds 5" if os.name == "nt" else "printf 'waiting\\n'; sleep 5"

    started = jobs.start(_short_shell_command(command), cleanup_paths=[cleanup])
    cancelled = jobs.cancel(started["session_id"])
    time.sleep(0.2)
    settled = jobs.status(started["session_id"])

    assert cancelled["status"] == "CANCELLED"
    assert settled["status"] == "CANCELLED"
    assert settled["cleanup_complete"] is True
    assert not cleanup.exists()


def test_job_timeout_terminates_process_tree(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(tmp_path / "home"))
    command = "Start-Sleep -Seconds 5" if os.name == "nt" else "sleep 5"

    started = jobs.start(_short_shell_command(command), timeout=1)
    final = _wait(started["session_id"], timeout=5)

    assert final["status"] == "TIMEOUT"
    assert final["exit_code"] is None
    assert final["cleanup_complete"] is True
