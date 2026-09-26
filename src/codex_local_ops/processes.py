from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path

import psutil

from .safety import redact_text

SYNC_MAX_TIMEOUT_SECONDS = 180
PROCESS_TERMINATION_GRACE_SECONDS = 2.0


def shell_execution_policy(command: str, timeout: int) -> dict[str, object]:
    requested_timeout = int(timeout)
    if requested_timeout < 1:
        raise ValueError("timeout must be at least one second")
    normalized = " ".join(str(command).lower().split())
    category: str | None = None

    python_module = re.search(
        r"\b(?:py(?:\s+-\d+(?:\.\d+)*)?|python(?:\.exe)?|python\d+(?:\.\d+)*)\s+-m\s+(pytest|unittest)\b",
        normalized,
    )
    if python_module:
        category = python_module.group(1)
    elif re.search(r"(?:^|[;&|]\s*)(?:pytest|py\.test)(?:\.exe)?(?:\s|$)", normalized):
        category = "pytest"
    elif re.search(r"\bunittest\s+discover\b", normalized):
        category = "unittest"
    elif re.search(r"\bdocker(?:\.exe)?\s+(?:compose\s+)?build\b", normalized):
        category = "docker-build"
    elif re.search(
        r"\b(?:python(?:\.exe)?\s+-m\s+build|pip(?:\.exe)?\s+(?:install|wheel)|poetry\s+build|"
        r"npm\s+(?:run\s+)?build|pnpm\s+(?:run\s+)?build|yarn\s+build|cargo\s+build|dotnet\s+build|msbuild)\b",
        normalized,
    ):
        category = "build"
    elif re.search(r"(?:^|[;&|]\s*)ffmpeg(?:\.exe)?(?:\s|$)", normalized):
        category = "media-transform"
    elif re.search(r"\b(?:validate|validation)[-_][\w.-]+\.(?:py|ps1|cmd|bat|sh)\b", normalized):
        category = "validation"

    if category:
        reason = f"long-running command category: {category}"
    elif requested_timeout > SYNC_MAX_TIMEOUT_SECONDS:
        reason = f"requested timeout exceeds synchronous budget ({SYNC_MAX_TIMEOUT_SECONDS}s)"
    else:
        reason = None

    return {
        "mode": "ASYNC" if reason else "SYNC",
        "category": category,
        "reason": reason,
        "requested_timeout_seconds": requested_timeout,
        "sync_budget_seconds": SYNC_MAX_TIMEOUT_SECONDS,
    }


def execution_resume_key(argv: Iterable[str], cwd: Path | None = None) -> str:
    payload = json.dumps(
        {"argv": [str(x) for x in argv], "cwd": str(cwd.resolve()) if cwd else None},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def terminate_process_tree(pid: int | None, *, grace_seconds: float = PROCESS_TERMINATION_GRACE_SECONDS) -> None:
    if not pid:
        return
    try:
        root = psutil.Process(int(pid))
    except (psutil.Error, TypeError, ValueError):
        return

    targets = root.children(recursive=True)
    targets.append(root)
    for proc in reversed(targets):
        try:
            proc.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(targets, timeout=max(0.1, float(grace_seconds)))
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            pass
    if alive:
        psutil.wait_procs(alive, timeout=max(0.1, float(grace_seconds)))


def _read_capture(handle: object, limit: int) -> tuple[str, bool]:
    handle.seek(0)
    return _limit(handle.read(), limit)


def run(
    argv: Iterable[str],
    *,
    cwd: Path | None = None,
    timeout: int = 60,
    env: dict[str, str] | None = None,
    check: bool = False,
    max_output_bytes: int = 1_000_000,
) -> dict:
    started = time.monotonic()
    args = list(argv)
    requested_timeout = max(1, int(timeout))
    effective_timeout = min(requested_timeout, SYNC_MAX_TIMEOUT_SECONDS)
    try:
        with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
            proc = subprocess.Popen(
                args,
                cwd=str(cwd) if cwd else None,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                close_fds=True,
                creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
                if os.name == "nt"
                else 0,
                start_new_session=os.name != "nt",
            )
            try:
                exit_code = proc.wait(timeout=effective_timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                exit_code = None
                terminate_process_tree(proc.pid)
                try:
                    proc.wait(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except OSError:
                        pass

            stdout, stdout_truncated = _read_capture(stdout_file, max_output_bytes)
            stderr, stderr_truncated = _read_capture(stderr_file, max_output_bytes)
    except OSError as exc:
        return {
            "argv": [redact_text(str(x)) for x in args],
            "exit_code": None,
            "stdout": "",
            "stderr": redact_text(str(exc)),
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
        }
    result = {
        "argv": [redact_text(str(x)) for x in args],
        "exit_code": exit_code,
        "stdout": redact_text(stdout),
        "stderr": redact_text(stderr),
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "timed_out": timed_out,
        "timeout_seconds": effective_timeout,
        "requested_timeout_seconds": requested_timeout,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
    }
    if check and not timed_out and exit_code != 0:
        raise RuntimeError(result)
    return result


def _limit(value: str | bytes, limit: int) -> tuple[str, bool]:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", errors="replace") + "\n[OUTPUT_TRUNCATED]", True


def split_command(command: str) -> list[str]:
    return shlex.split(command, posix=os.name != "nt")
