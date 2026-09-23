from __future__ import annotations

import json
import shutil

from .processes import run
from .safety import assert_trusted_path


def docker(args: list[str], *, path: str | None = None, timeout: int = 600) -> dict:
    exe = shutil.which("docker")
    if not exe:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": "docker executable not found"}
    cwd = assert_trusted_path(path, must_exist=True) if path else None
    result = run([exe, *args], cwd=cwd, timeout=timeout)
    result["status"] = "OK" if result["exit_code"] == 0 else "FAILED"
    return result


def compose_detect(path: str) -> dict:
    root = assert_trusted_path(path, must_exist=True)
    for name in ["compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"]:
        p = root / name
        if p.exists():
            return {"status": "OK", "path": str(p)}
    return {"status": "CAPABILITY_UNAVAILABLE", "reason": "No Compose file found", "project": str(root)}


def health() -> dict:
    result = docker(["info", "--format", "{{json .}}"], timeout=30)
    if result.get("exit_code") == 0:
        try:
            result["info"] = json.loads(result.get("stdout", "{}"))
        except json.JSONDecodeError:
            pass
    return result
