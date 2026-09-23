from __future__ import annotations

import re
import shutil
from pathlib import Path

from .processes import run


def _wsl() -> str | None:
    return shutil.which("wsl") or shutil.which("wsl.exe")


def list_distros() -> dict:
    exe = _wsl()
    if not exe:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": "WSL executable not found"}
    result = run([exe, "-l", "-v"], timeout=30)
    # wsl.exe may emit UTF-16-ish NUL characters through redirected stdout.
    # Keep tool transport JSON/text friendly while preserving the human output.
    result["stdout"] = result.get("stdout", "").replace("\x00", "")
    result["stderr"] = result.get("stderr", "").replace("\x00", "")
    result["status"] = "OK" if result.get("exit_code") == 0 else "FAILED"
    result["distros"] = _parse_distros(result.get("stdout", "")) if result["status"] == "OK" else []
    return result


def info(distro: str | None = None) -> dict:
    exe = _wsl()
    if not exe:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": "WSL executable not found"}
    args = [exe, "--status"] if not distro else [exe, "-d", distro, "--", "uname", "-a"]
    result = run(args, timeout=30)
    result["status"] = "OK" if result.get("exit_code") == 0 else "FAILED"
    return result


def exec_wsl(distro: str, command: str, timeout: int = 120) -> dict:
    exe = _wsl()
    if not exe:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": "WSL executable not found"}
    result = run([exe, "-d", distro, "--", "sh", "-lc", command], timeout=timeout)
    result["status"] = "OK" if result.get("exit_code") == 0 else "FAILED"
    return result


def path_translate(path: str) -> dict:
    p = Path(path).expanduser().resolve(strict=False)
    drive_match = re.match(r"^([A-Za-z]):[\\/](.*)$", str(p))
    if not drive_match:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": "Only absolute Windows drive paths can be translated", "path": str(p)}
    rest = drive_match.group(2).replace("\\", "/")
    return {"status": "OK", "windows_path": str(p), "wsl_path": f"/mnt/{drive_match.group(1).lower()}/{rest}"}


def _parse_distros(stdout: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in stdout.splitlines():
        line = line.replace("\x00", "").strip().lstrip("*").strip()
        if not line or line.lower().startswith("name"):
            continue
        columns = re.split(r"\s{2,}", line)
        if len(columns) >= 3:
            rows.append({"name": columns[0], "state": columns[1], "version": columns[2]})
    return rows
