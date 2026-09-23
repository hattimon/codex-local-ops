from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Iterable

from .safety import redact_text


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
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            timeout=max(1, min(int(timeout), 3600)),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": [redact_text(str(x)) for x in args],
            "exit_code": None,
            "stdout": _limit(exc.stdout or "", max_output_bytes)[0],
            "stderr": _limit(exc.stderr or "", max_output_bytes)[0],
            "timed_out": True,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
        }
    except OSError as exc:
        return {
            "argv": [redact_text(str(x)) for x in args],
            "exit_code": None,
            "stdout": "",
            "stderr": redact_text(str(exc)),
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
        }
    stdout, stdout_truncated = _limit(proc.stdout, max_output_bytes)
    stderr, stderr_truncated = _limit(proc.stderr, max_output_bytes)
    result = {
        "argv": [redact_text(str(x)) for x in args],
        "exit_code": proc.returncode,
        "stdout": redact_text(stdout),
        "stderr": redact_text(stderr),
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
    }
    if check and proc.returncode != 0:
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
