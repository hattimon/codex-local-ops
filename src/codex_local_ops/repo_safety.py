"""Non-destructive repository locking and Git change capture for agent runs."""
from __future__ import annotations

import hashlib
import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import install_root
from .processes import run
from .safety import redact_text, sanitize


class RepoBusyError(RuntimeError):
    code = "REPO_BUSY"

    def __init__(self) -> None:
        super().__init__(self.code)


_LOCAL_LOCKS: set[str] = set()
_LOCAL_LOCKS_GUARD = threading.RLock()


class RepositoryMutationLock:
    """An exclusive, canonical-repository lock held for one agent run.

    The in-process guard makes contention deterministic for threads.  The
    platform file lock additionally protects independent Local Ops processes.
    """

    def __init__(self, repo: Path) -> None:
        self.repo = repo.resolve(strict=True)
        self.key = str(self.repo).casefold() if os.name == "nt" else str(self.repo)
        digest = hashlib.sha256(self.key.encode("utf-8")).hexdigest()
        self.path = install_root() / "repo-locks" / f"{digest}.lock"
        self._handle: Any | None = None
        self.acquired = False

    def acquire(self) -> None:
        with _LOCAL_LOCKS_GUARD:
            if self.key in _LOCAL_LOCKS:
                raise RepoBusyError()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
            try:
                handle.seek(0)
                if not handle.read(1):
                    handle.seek(0)
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                raise RepoBusyError() from None
            _LOCAL_LOCKS.add(self.key)
            self._handle = handle
            self.acquired = True

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            assert self._handle is not None
            self._handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
        finally:
            with _LOCAL_LOCKS_GUARD:
                _LOCAL_LOCKS.discard(self.key)
            self._handle = None
            self.acquired = False

    def __enter__(self) -> "RepositoryMutationLock":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


@dataclass(frozen=True, slots=True)
class GitSnapshot:
    base_head: str | None = None
    branch: str | None = None
    entries: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    available: bool = False


def _git(repo: Path, args: list[str], *, max_output_bytes: int = 1_000_000) -> dict[str, Any]:
    executable = shutil.which("git")
    if executable is None:
        return {"exit_code": None, "stderr": "git executable not found"}
    return run([executable, "-C", str(repo), *args], timeout=20, max_output_bytes=max_output_bytes)


def _file_hash(repo: Path, relative: str) -> str | None:
    path = repo / relative
    if not path.is_file() or path.is_symlink():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _status_entries(repo: Path) -> dict[str, tuple[str, str | None]] | None:
    result = _git(repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    if result.get("exit_code") != 0:
        return None
    fields = str(result.get("stdout", "")).split("\0")
    entries: dict[str, tuple[str, str | None]] = {}
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        if len(field) < 3 or field[2] != " ":
            continue
        x, y, path = field[0], field[1], field[3:]
        status = x + y
        # Porcelain -z emits the source path as an additional NUL field for
        # rename/copy entries.  Keep both paths observable without parsing a
        # user-visible diff.
        if {x, y} & {"R", "C"} and index < len(fields):
            source = fields[index]
            index += 1
            entries[source] = ("D ", None)
        entries[path] = (status, _file_hash(repo, path))
    return entries


def capture_snapshot(repo: Path) -> GitSnapshot:
    entries = _status_entries(repo)
    if entries is None:
        return GitSnapshot()
    head = _git(repo, ["rev-parse", "HEAD"])
    branch = _git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    base_head = None
    if head.get("exit_code") == 0:
        base_head = str(head.get("stdout", "")).strip() or None
    current_branch = "DETACHED"
    if branch.get("exit_code") == 0:
        current_branch = str(branch.get("stdout", "")).strip() or "DETACHED"
    return GitSnapshot(
        base_head=base_head,
        branch=current_branch,
        entries=entries,
        available=True,
    )


def change_capture(before: GitSnapshot, repo: Path) -> dict[str, Any]:
    after = capture_snapshot(repo)
    if not before.available or not after.available:
        return {
            "base_head": before.base_head,
            "branch": before.branch,
            "changed_files": [],
            "added_files": [],
            "modified_files": [],
            "deleted_files": [],
            "untracked_files": [],
            "preexisting_files": [],
            "diff_summary": {"files_changed": 0, "insertions": 0, "deletions": 0, "paths": []},
            "git_capture_status": "UNAVAILABLE",
        }

    changed: list[str] = []
    preexisting: list[str] = []
    added: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    untracked: list[str] = []
    for path in sorted(set(before.entries) | set(after.entries)):
        previous, current = before.entries.get(path), after.entries.get(path)
        if previous == current:
            preexisting.append(path)
            continue
        if current is None:
            changed.append(path)
            deleted.append(path)
            continue
        changed.append(path)
        x, y = current[0]
        if x == y == "?":
            untracked.append(path)
        elif x == "D" or y == "D":
            deleted.append(path)
        elif x == "A":
            added.append(path)
        else:
            modified.append(path)

    insertions = deletions = 0
    if changed:
        numstat = _git(repo, ["diff", "--numstat", "HEAD", "--", *changed], max_output_bytes=200_000)
        if numstat.get("exit_code") == 0:
            for line in str(numstat.get("stdout", "")).splitlines():
                parts = line.split("\t", 2)
                if len(parts) >= 2:
                    insertions += int(parts[0]) if parts[0].isdigit() else 0
                    deletions += int(parts[1]) if parts[1].isdigit() else 0
    return sanitize(
        {
            "base_head": before.base_head,
            "branch": before.branch,
            "changed_files": changed,
            "added_files": added,
            "modified_files": modified,
            "deleted_files": deleted,
            "untracked_files": untracked,
            "preexisting_files": preexisting,
            "diff_summary": {
                "files_changed": len(changed),
                "insertions": insertions,
                "deletions": deletions,
                "paths": changed[:200],
                "paths_truncated": len(changed) > 200,
            },
            "git_capture_status": "OK",
        }
    )
