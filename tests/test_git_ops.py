from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from codex_local_ops import git_ops, jobs
from codex_local_ops.config import load_config, save_config
from codex_local_ops.safety import redact_text


def _git() -> str:
    exe = shutil.which("git")
    if not exe:
        pytest.skip("git is not installed")
    return exe


def _run(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_git(), "-C", str(path), *args],
        check=True,
        text=True,
        capture_output=True,
    )


def _trust(monkeypatch, tmp_path: Path, root: Path) -> None:
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(tmp_path / "local-ops-home"))
    cfg = load_config()
    cfg["projects"]["trusted_roots"] = [str(root)]
    save_config(cfg)


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _run(path, "init")
    _run(path, "config", "user.name", "Codex Local Ops Test")
    _run(path, "config", "user.email", "codex-local-ops@example.invalid")
    _run(path, "checkout", "-b", "main")
    return path


def _commit_file(repo: Path, name: str = "file.txt", content: str = "one\n") -> None:
    (repo / name).write_text(content, encoding="utf-8")
    _run(repo, "add", "--", name)
    _run(repo, "commit", "-m", "initial")


def test_trusted_repo_and_untrusted_repo(monkeypatch, tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    repo = _repo(trusted / "repo")
    outside = _repo(tmp_path / "outside")
    _trust(monkeypatch, tmp_path, trusted)

    assert git_ops.git(str(repo), ["status", "--short"])["status"] == "OK"
    with pytest.raises(PermissionError):
        git_ops.git(str(outside), ["status", "--short"])


def test_stage_commit_and_cross_platform_safe_argument_quoting(monkeypatch, tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    repo = _repo(trusted / "repo")
    _trust(monkeypatch, tmp_path, trusted)
    name = "file with spaces.txt"
    message = 'spaces & | ; $() \'single\' "double" unicode-✓ ąćęłńóśźż'
    (repo / name).write_text("content\n", encoding="utf-8")

    assert git_ops.stage(str(repo), [name])["status"] == "OK"
    assert git_ops.commit(str(repo), message)["status"] == "OK"
    logged = git_ops.git(str(repo), ["log", "-1", "--pretty=%B"])
    assert logged["status"] == "OK"
    assert logged["stdout"].strip() == message


def test_push_to_local_remote_force_refspec_rejected_and_async_push(monkeypatch, tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    repo = _repo(trusted / "repo")
    _trust(monkeypatch, tmp_path, trusted)
    _commit_file(repo)
    remote = tmp_path / "remote.git"
    subprocess.run([_git(), "init", "--bare", str(remote)], check=True, text=True, capture_output=True)
    _run(repo, "remote", "add", "origin", str(remote))

    assert git_ops.push(str(repo), "origin", "main")["status"] == "OK"
    with pytest.raises(ValueError):
        git_ops.push(str(repo), "origin", "+main")

    (repo / "file.txt").write_text("two\n", encoding="utf-8")
    _run(repo, "add", "--", "file.txt")
    _run(repo, "commit", "-m", "second")
    started = git_ops.push_async(str(repo), "origin", "main")
    deadline = time.monotonic() + 10
    state = jobs.status(started["session_id"])
    while time.monotonic() < deadline and state["status"] not in jobs.TERMINAL_STATES:
        time.sleep(0.05)
        state = jobs.status(started["session_id"])
    assert state["status"] == "COMPLETED"
    assert state["exit_code"] == 0


def test_github_child_env_drops_stale_tokens_without_mutating_parent(monkeypatch) -> None:
    first = "gh" + "_token_for_test_only"
    second = "github" + "_token_for_test_only"
    monkeypatch.setenv("GH_TOKEN", first)
    monkeypatch.setenv("GITHUB_TOKEN", second)
    seen = {}

    def fake_run(argv, **kwargs):
        seen["env"] = kwargs.get("env")
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(git_ops, "_gh_executable", lambda: "gh")
    monkeypatch.setattr(git_ops, "run", fake_run)
    assert git_ops.github(["auth", "status"])["status"] == "OK"
    assert "GH_TOKEN" not in seen["env"]
    assert "GITHUB_TOKEN" not in seen["env"]
    assert os.environ["GH_TOKEN"] == first
    assert os.environ["GITHUB_TOKEN"] == second


def test_async_git_push_child_env_drops_stale_tokens_without_mutating_parent(monkeypatch, tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    repo = _repo(trusted / "repo")
    _trust(monkeypatch, tmp_path, trusted)
    _commit_file(repo)
    _run(repo, "remote", "add", "origin", str(tmp_path / "remote.git"))
    first = "gh" + "_token_for_test_only"
    second = "github" + "_token_for_test_only"
    monkeypatch.setenv("GH_TOKEN", first)
    monkeypatch.setenv("GITHUB_TOKEN", second)
    seen = {}

    def fake_start(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs.get("env")
        return {"status": "STARTED", "session_id": "job_test"}

    monkeypatch.setattr(jobs, "start", fake_start)
    result = git_ops.push_async(str(repo), "origin", "main")

    assert result["status"] == "STARTED"
    assert "GH_TOKEN" not in seen["env"]
    assert "GITHUB_TOKEN" not in seen["env"]
    assert os.environ["GH_TOKEN"] == first
    assert os.environ["GITHUB_TOKEN"] == second
    assert "--force" not in seen["argv"]
    assert "+main" not in seen["argv"]


def test_github_release_create_requires_existing_tag_and_uses_gh(monkeypatch, tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    repo = _repo(trusted / "repo")
    _trust(monkeypatch, tmp_path, trusted)
    seen = {}

    def fake_github(args, timeout=300, *, cwd=None):
        seen["args"] = args
        seen["cwd"] = cwd
        return {"status": "OK", "exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(git_ops, "github", fake_github)
    result = git_ops.github_release_create(
        str(repo),
        "v1.2.3",
        title="Release title",
        notes="Release notes",
        draft=True,
    )

    assert result["status"] == "OK"
    assert seen["cwd"] == repo.resolve()
    assert seen["args"][:4] == ["release", "create", "v1.2.3", "--verify-tag"]
    assert "--draft" in seen["args"]


def test_github_token_redaction() -> None:
    first = "ghp_" + "A" * 30
    second = "github_pat_" + "B" * 30
    redacted = redact_text(f"first={first} second={second}")
    assert first not in redacted
    assert second not in redacted
    assert "[REDACTED_GITHUB_TOKEN]" in redacted
