from __future__ import annotations

import shutil

from .processes import run
from .safety import assert_trusted_path


def git(path: str, args: list[str], timeout: int = 300) -> dict:
    root = assert_trusted_path(path, must_exist=True)
    exe = shutil.which("git")
    if not exe:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": "git executable not found"}
    result = run([exe, "-C", str(root), *args], timeout=timeout)
    result["status"] = "OK" if result["exit_code"] == 0 else "FAILED"
    return result


def github(args: list[str], timeout: int = 300) -> dict:
    exe = shutil.which("gh")
    if not exe:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": "GitHub CLI (gh) not found"}
    result = run([exe, *args], timeout=timeout)
    result["status"] = "OK" if result["exit_code"] == 0 else "FAILED"
    return result
