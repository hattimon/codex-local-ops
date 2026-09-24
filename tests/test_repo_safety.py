from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codex_local_ops.agent_runtime import AgentRuntime
from codex_local_ops.repo_safety import RepoBusyError, RepositoryMutationLock, capture_snapshot, change_capture


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Tests")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_lock_rejects_same_canonical_repo_and_allows_different_repo(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(tmp_path / "home"))
    repo = _repo(tmp_path)
    other = _repo(tmp_path / "other-parent")
    first = RepositoryMutationLock(repo)
    first.acquire()
    try:
        with pytest.raises(RepoBusyError):
            RepositoryMutationLock(repo / ".").acquire()
        second = RepositoryMutationLock(other)
        second.acquire()
        second.release()
    finally:
        first.release()
    # Successful cleanup makes the canonical lock reusable.
    reusable = RepositoryMutationLock(repo)
    reusable.acquire()
    reusable.release()


def test_capture_distinguishes_preexisting_and_new_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "tracked.txt").write_text("preexisting\n", encoding="utf-8")
    before = capture_snapshot(repo)
    (repo / "tracked.txt").write_text("changed during run\n", encoding="utf-8")
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    result = change_capture(before, repo)

    assert result["base_head"]
    assert result["branch"]
    assert result["changed_files"] == ["new.txt", "tracked.txt"]
    assert result["untracked_files"] == ["new.txt"]
    assert result["modified_files"] == ["tracked.txt"]
    assert result["preexisting_files"] == []
    assert result["diff_summary"]["files_changed"] == 2
    assert result["diff_summary"]["insertions"] >= 1


def test_capture_reports_added_deleted_and_unchanged_preexisting_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "preexisting.txt").write_text("keep\n", encoding="utf-8")
    before = capture_snapshot(repo)
    (repo / "added.txt").write_text("added\n", encoding="utf-8")
    _git(repo, "add", "added.txt")
    (repo / "tracked.txt").unlink()
    result = change_capture(before, repo)

    assert result["added_files"] == ["added.txt"]
    assert result["deleted_files"] == ["tracked.txt"]
    assert result["preexisting_files"] == ["preexisting.txt"]
    assert "preexisting.txt" not in result["changed_files"]


def test_capture_reports_staged_tracked_deletion(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    before = capture_snapshot(repo)
    (repo / "tracked.txt").unlink()
    _git(repo, "add", "-u")

    result = change_capture(before, repo)

    assert result["deleted_files"] == ["tracked.txt"]
    assert result["modified_files"] == []


def test_capture_classifies_index_worktree_and_space_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "staged.txt").write_text("base\n", encoding="utf-8")
    (repo / "unstaged.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "staged.txt", "unstaged.txt")
    _git(repo, "commit", "-m", "add status fixtures")
    before = capture_snapshot(repo)
    (repo / "tracked.txt").write_text("index change\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")  # M
    (repo / "tracked.txt").write_text("worktree change\n", encoding="utf-8")  # MM
    (repo / "unstaged.txt").write_text("changed\n", encoding="utf-8")  #  M
    (repo / "staged.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "staged.txt")  # M
    (repo / "file with spaces.txt").write_text("added\n", encoding="utf-8")
    _git(repo, "add", "file with spaces.txt")  # A
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    result = change_capture(before, repo)

    assert result["added_files"] == ["file with spaces.txt"]
    assert result["modified_files"] == ["staged.txt", "tracked.txt", "unstaged.txt"]
    assert result["untracked_files"] == ["untracked.txt"]
    assert result["deleted_files"] == []


def test_capture_no_changes_is_empty(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    before = capture_snapshot(repo)
    result = change_capture(before, repo)
    assert result["changed_files"] == []
    assert result["diff_summary"]["files_changed"] == 0


@pytest.mark.parametrize("mode", ["completed", "failed", "timeout", "cancelled", "exception"])
def test_runtime_captures_changes_and_releases_lock_for_all_terminal_paths(tmp_path: Path, monkeypatch, mode: str) -> None:
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(tmp_path / "home"))
    repo = _repo(tmp_path)
    monkeypatch.setattr("codex_local_ops.agent_runtime.assert_trusted_path", lambda path, must_exist: repo)

    class EditingAdapter:
        def run(self, task, repo_path, **kwargs):
            (repo_path / "edited.txt").write_text("edit\n", encoding="utf-8")
            if mode == "exception":
                raise RuntimeError("adapter broke")
            status = {"completed": "COMPLETED", "failed": "FAILED", "timeout": "TIMEOUT", "cancelled": "CANCELLED"}[mode]
            return {"status": status, "exit_code": 0 if status == "COMPLETED" else None, "stdout": "", "stderr": "", "error": mode, "duration_ms": 1}

    result = AgentRuntime(adapter=EditingAdapter()).run("task", repo)
    assert "edited.txt" in result["changed_files"]
    assert result["repo_lock_status"] == "ACQUIRED"
    assert result["git_capture_status"] == "OK"

    # A new acquisition proves cleanup after every terminal status/exception.
    lock = RepositoryMutationLock(repo)
    lock.acquire()
    lock.release()


def test_second_runtime_for_same_repo_returns_repo_busy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(tmp_path / "home"))
    repo = _repo(tmp_path)
    monkeypatch.setattr("codex_local_ops.agent_runtime.assert_trusted_path", lambda path, must_exist: repo)
    held = RepositoryMutationLock(repo)
    held.acquire()
    try:
        class NeverRunAdapter:
            def run(self, *args, **kwargs):
                pytest.fail("busy repository reached adapter")

        result = AgentRuntime(adapter=NeverRunAdapter()).run("task", repo)
    finally:
        held.release()
    assert result["status"] == "FAILED"
    assert result["error"] == "REPO_BUSY"
    assert result["repo_lock_status"] == "REPO_BUSY"
