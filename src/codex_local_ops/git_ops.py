from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Iterable

from . import jobs
from .processes import run
from .safety import assert_trusted_path


_REMOTE_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_GITHUB_TOKEN_ENV = ("GH_TOKEN", "GITHUB_TOKEN")
_GIT_UTF8_CONFIG = ("-c", "i18n.logOutputEncoding=utf-8")


def _ok(result: dict) -> dict:
    result["status"] = "OK" if result.get("exit_code") == 0 else "FAILED"
    return result


def _git_executable() -> str | None:
    return shutil.which("git")


def _gh_executable() -> str | None:
    return shutil.which("gh")


def _github_env() -> dict[str, str]:
    """Return the host environment without token overrides that can shadow host credentials."""
    env = os.environ.copy()
    for key in _GITHUB_TOKEN_ENV:
        env.pop(key, None)
    return env


def _git_run(
    exe: str,
    args: Iterable[str],
    *,
    cwd: Path | None = None,
    timeout: int = 60,
) -> dict:
    """Run Git with UTF-8 log output without changing user/global Git config."""
    return run(
        [exe, *_GIT_UTF8_CONFIG, *[str(arg) for arg in args]],
        cwd=cwd,
        timeout=timeout,
        env=_github_env(),
    )


def _repo_root(path: str | Path) -> tuple[Path, str]:
    requested = assert_trusted_path(path, must_exist=True)
    if not requested.is_dir():
        raise NotADirectoryError(str(requested))
    exe = _git_executable()
    if not exe:
        raise FileNotFoundError("git executable not found")
    probe = _git_run(exe, ["-C", str(requested), "rev-parse", "--show-toplevel"], timeout=20)
    if probe.get("exit_code") != 0:
        raise ValueError("Path is not inside a Git working tree")
    raw_root = str(probe.get("stdout", "")).strip()
    if not raw_root:
        raise ValueError("Git did not return a repository root")
    root = assert_trusted_path(Path(raw_root), must_exist=True)
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    return root, exe


def _validate_remote_name(remote: str) -> str:
    value = str(remote).strip()
    if not value or value.startswith("-") or not _REMOTE_RE.fullmatch(value):
        raise ValueError("remote must be a configured Git remote name")
    return value


def _validate_branch(exe: str, root: Path, branch: str) -> str:
    value = str(branch).strip()
    if not value or value.startswith(("-", "+")):
        raise ValueError("invalid branch/refspec")
    check = _git_run(exe, ["check-ref-format", "--branch", value], cwd=root, timeout=20)
    if check.get("exit_code") != 0:
        raise ValueError("invalid branch name")
    return value


def _configured_remotes(exe: str, root: Path) -> set[str]:
    result = _git_run(exe, ["-C", str(root), "remote"], timeout=20)
    if result.get("exit_code") != 0:
        return set()
    return {line.strip() for line in str(result.get("stdout", "")).splitlines() if line.strip()}


def _current_branch(exe: str, root: Path) -> str:
    result = _git_run(exe, ["-C", str(root), "symbolic-ref", "--quiet", "--short", "HEAD"], timeout=20)
    if result.get("exit_code") != 0:
        raise ValueError("Repository is in detached HEAD state; specify a branch")
    return _validate_branch(exe, root, str(result.get("stdout", "")).strip())


def git(path: str, args: list[str], timeout: int = 300) -> dict:
    try:
        root, exe = _repo_root(path)
    except FileNotFoundError:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": "git executable not found"}
    return _ok(_git_run(exe, ["-C", str(root), *args], timeout=timeout))


def log(path: str, limit: int = 20) -> dict:
    root, exe = _repo_root(path)
    count = max(1, min(int(limit), 200))
    return _ok(
        _git_run(
            exe,
            [
                "-C",
                str(root),
                "log",
                "--no-decorate",
                "--date=iso-strict",
                "--pretty=format:%H%x09%h%x09%an%x09%ad%x09%s",
                "-n",
                str(count),
            ],
            timeout=30,
        )
    )


def branches(path: str) -> dict:
    root, exe = _repo_root(path)
    return _ok(
        _git_run(exe, ["-C", str(root), "branch", "--list", "--no-color", "--verbose", "--verbose"], timeout=20)
    )


def remotes(path: str) -> dict:
    try:
        root, exe = _repo_root(path)
    except FileNotFoundError:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": "git executable not found"}
    result = _ok(_git_run(exe, ["-C", str(root), "remote", "-v"], timeout=20))
    if result["status"] == "OK":
        result["repo_root"] = str(root)
    return result


def stage(path: str, paths: Iterable[str] | None = None) -> dict:
    root, exe = _repo_root(path)
    selected = [str(item) for item in (paths or ["."])]
    if not selected or any((not item) or item.startswith("-") or "\x00" in item for item in selected):
        raise ValueError("paths must contain safe Git pathspecs")
    return _ok(_git_run(exe, ["-C", str(root), "add", "--", *selected], timeout=120))


def commit(path: str, message: str) -> dict:
    root, exe = _repo_root(path)
    text = str(message).strip()
    if not text:
        raise ValueError("commit message must not be empty")
    if len(text) > 20_000 or "\x00" in text:
        raise ValueError("commit message is invalid")
    return _ok(_git_run(exe, ["-C", str(root), "commit", "-m", text, "--"], timeout=180))


def push(path: str, remote: str = "origin", branch: str | None = None, timeout: int = 300) -> dict:
    root, exe = _repo_root(path)
    remote_name = _validate_remote_name(remote)
    if remote_name not in _configured_remotes(exe, root):
        raise ValueError(f"Unknown configured Git remote: {remote_name}")
    branch_name = _validate_branch(exe, root, branch) if branch else _current_branch(exe, root)
    # No force flag and no leading '+' refspec are accepted by this narrow API.
    return _ok(
        _git_run(exe, ["-C", str(root), "push", "--", remote_name, branch_name], timeout=timeout)
    )


def publish(path: str, remote: str = "origin", branch: str | None = None, timeout: int = 300) -> dict:
    root, exe = _repo_root(path)
    remote_name = _validate_remote_name(remote)
    if remote_name not in _configured_remotes(exe, root):
        raise ValueError(f"Unknown configured Git remote: {remote_name}")
    branch_name = _validate_branch(exe, root, branch) if branch else _current_branch(exe, root)
    return _ok(
        _git_run(
            exe,
            ["-C", str(root), "push", "--set-upstream", "--", remote_name, branch_name],
            timeout=timeout,
        )
    )


def push_async(path: str, remote: str = "origin", branch: str | None = None) -> dict:
    root, exe = _repo_root(path)
    remote_name = _validate_remote_name(remote)
    if remote_name not in _configured_remotes(exe, root):
        raise ValueError(f"Unknown configured Git remote: {remote_name}")
    branch_name = _validate_branch(exe, root, branch) if branch else _current_branch(exe, root)
    return jobs.start(
        [exe, *_GIT_UTF8_CONFIG, "-C", str(root), "push", "--", remote_name, branch_name],
        cwd=root,
        label=f"git push {remote_name}/{branch_name}",
        env=_github_env(),
    )


def github(args: list[str], timeout: int = 300, *, cwd: Path | None = None) -> dict:
    exe = _gh_executable()
    if not exe:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": "GitHub CLI (gh) not found"}
    return _ok(run([exe, *args], cwd=cwd, timeout=timeout, env=_github_env()))


def github_auth_status(hostname: str = "github.com") -> dict:
    host = str(hostname).strip().lower()
    if host != "github.com":
        raise ValueError("Only github.com host auth is supported")
    return github(["auth", "status", "--hostname", host], timeout=30)


def github_repo_view(path: str) -> dict:
    root, _ = _repo_root(path)
    result = github(
        ["repo", "view", "--json", "nameWithOwner,url,defaultBranchRef,isPrivate"],
        timeout=30,
        cwd=root,
    )
    if result.get("status") == "OK":
        result["repo_root"] = str(root)
    return result


def github_workflow_list(path: str, limit: int = 50) -> dict:
    root, _ = _repo_root(path)
    count = max(1, min(int(limit), 100))
    return github(
        ["workflow", "list", "--limit", str(count), "--json", "id,name,path,state"],
        timeout=60,
        cwd=root,
    )


def github_run_list(path: str, limit: int = 20) -> dict:
    root, _ = _repo_root(path)
    count = max(1, min(int(limit), 100))
    return github(
        [
            "run",
            "list",
            "--limit",
            str(count),
            "--json",
            "databaseId,status,conclusion,name,workflowName,headBranch,headSha,event,createdAt,updatedAt,url",
        ],
        timeout=60,
        cwd=root,
    )


def github_run_view(path: str, run_id: int) -> dict:
    root, _ = _repo_root(path)
    value = int(run_id)
    if value <= 0:
        raise ValueError("run_id must be a positive integer")
    return github(
        ["run", "view", str(value), "--json", "databaseId,status,conclusion,name,workflowName,headBranch,headSha,event,createdAt,updatedAt,url,jobs"],
        timeout=60,
        cwd=root,
    )


def github_release_list(path: str, limit: int = 30) -> dict:
    root, _ = _repo_root(path)
    count = max(1, min(int(limit), 100))
    return github(
        [
            "release",
            "list",
            "--limit",
            str(count),
            "--json",
            "name,tagName,isDraft,isPrerelease,publishedAt,url",
        ],
        timeout=60,
        cwd=root,
    )


def github_release_view(path: str, tag: str) -> dict:
    root, _ = _repo_root(path)
    value = str(tag).strip()
    if not value or value.startswith("-") or "\x00" in value:
        raise ValueError("invalid release tag")
    return github(
        [
            "release",
            "view",
            value,
            "--json",
            "name,tagName,isDraft,isPrerelease,publishedAt,url,targetCommitish",
        ],
        timeout=60,
        cwd=root,
    )


def github_pr_list(path: str, limit: int = 30, state: str = "open") -> dict:
    root, _ = _repo_root(path)
    count = max(1, min(int(limit), 100))
    pr_state = str(state).strip().lower()
    if pr_state not in {"open", "closed", "merged", "all"}:
        raise ValueError("state must be open, closed, merged, or all")
    return github(
        [
            "pr",
            "list",
            "--state",
            pr_state,
            "--limit",
            str(count),
            "--json",
            "number,title,state,author,headRefName,baseRefName,isDraft,updatedAt,url",
        ],
        timeout=60,
        cwd=root,
    )


def github_pr_view(path: str, number: int) -> dict:
    root, _ = _repo_root(path)
    value = int(number)
    if value <= 0:
        raise ValueError("PR number must be a positive integer")
    return github(
        [
            "pr",
            "view",
            str(value),
            "--json",
            "number,title,state,author,headRefName,baseRefName,isDraft,mergeable,reviewDecision,updatedAt,url",
        ],
        timeout=60,
        cwd=root,
    )


def github_release_create(
    path: str,
    tag: str,
    *,
    title: str | None = None,
    notes: str | None = None,
    draft: bool = False,
) -> dict:
    root, _ = _repo_root(path)
    value = str(tag).strip()
    if not value or value.startswith("-") or "\x00" in value:
        raise ValueError("invalid release tag")
    args = ["release", "create", value, "--verify-tag"]
    if title:
        args.extend(["--title", str(title)])
    if notes:
        args.extend(["--notes", str(notes)])
    if draft:
        args.append("--draft")
    return github(args, timeout=180, cwd=root)


def github_run_watch_async(path: str, run_id: int, interval: int = 5) -> dict:
    root, _ = _repo_root(path)
    exe = _gh_executable()
    if not exe:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": "GitHub CLI (gh) not found"}
    value = int(run_id)
    if value <= 0:
        raise ValueError("run_id must be a positive integer")
    seconds = max(3, min(int(interval), 60))
    return jobs.start(
        [exe, "run", "watch", str(value), "--interval", str(seconds), "--exit-status"],
        cwd=root,
        label=f"gh run watch {value}",
        env=_github_env(),
    )
