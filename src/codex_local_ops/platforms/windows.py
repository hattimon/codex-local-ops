from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..models import failed, unavailable
from ..processes import run
from .base import PlatformBackend


class WindowsBackend(PlatformBackend):
    name = "windows"

    def get_system_info(self) -> dict[str, Any]:
        data = super().get_system_info()
        data.update({"build": platform.version().split(".")[-1], "win32": True, "powershell": shutil.which("powershell"), "pwsh": shutil.which("pwsh")})
        return data

    def get_shell(self) -> dict[str, Any]:
        shell = shutil.which("pwsh") or shutil.which("powershell") or os.environ.get("COMSPEC")
        return {"shell": "PowerShell" if "powershell" in str(shell).lower() or "pwsh" in str(shell).lower() else "cmd", "executable": shell}

    def service_status(self, name: str) -> dict[str, Any]:
        ps = shutil.which("pwsh") or shutil.which("powershell")
        if not ps:
            return unavailable("PowerShell unavailable", self.name)
        cmd = f"Get-Service -Name '{name.replace(chr(39), chr(39)*2)}' | Select-Object Name,Status,StartType | ConvertTo-Json -Compress"
        res = run([ps, "-NoProfile", "-NonInteractive", "-Command", cmd])
        return {"status": "OK" if res["exit_code"] == 0 else "FAILED", **res}

    def service_action(self, name: str, action: str) -> dict[str, Any]:
        verbs = {"start": "Start-Service", "stop": "Stop-Service", "restart": "Restart-Service"}
        if action not in verbs:
            return failed("Unsupported service action", self.name)
        ps = shutil.which("pwsh") or shutil.which("powershell")
        if not ps:
            return unavailable("PowerShell unavailable", self.name)
        escaped = name.replace("'", "''")
        return run([ps, "-NoProfile", "-NonInteractive", "-Command", f"{verbs[action]} -Name '{escaped}' -ErrorAction Stop"])

    def open_path(self, path: Path) -> dict[str, Any]:
        proc = subprocess.Popen(["explorer.exe", str(path)])
        return {"status": "OK", "pid": proc.pid}

    def reveal_file(self, path: Path) -> dict[str, Any]:
        proc = subprocess.Popen(["explorer.exe", "/select,", str(path)])
        return {"status": "OK", "pid": proc.pid}

    def get_desktop_info(self) -> dict[str, Any]:
        return {"available": True, "session": "win32", "ui_automation": _can_import("pywinauto")}

    def take_screenshot(self, output: Path) -> dict[str, Any]:
        try:
            import mss
            from PIL import Image

            output.parent.mkdir(parents=True, exist_ok=True)
            with mss.mss() as sct:
                monitor = sct.monitors[0]
                shot = sct.grab(monitor)
                Image.frombytes("RGB", shot.size, shot.rgb).save(output)
            return {"status": "OK", "path": str(output), "width": monitor["width"], "height": monitor["height"]}
        except ImportError:
            return unavailable("Screenshot dependencies are optional and not installed", self.name, "Install codex-local-ops[desktop]")

    def list_windows(self) -> dict[str, Any]:
        try:
            from pywinauto import Desktop
            rows = []
            for w in Desktop(backend="uia").windows():
                try:
                    rows.append({"title": w.window_text(), "handle": hex(w.handle), "visible": w.is_visible(), "enabled": w.is_enabled()})
                except Exception:
                    continue
            return {"status": "OK", "windows": rows}
        except Exception as exc:
            return unavailable(f"Windows UI Automation unavailable: {exc}", self.name, "Install the windows extra: pip install codex-local-ops[windows]")

    def focus_window(self, window_id: str) -> dict[str, Any]:
        try:
            hwnd = int(window_id, 0)
            ok = bool(ctypes.windll.user32.SetForegroundWindow(hwnd))
            return {"status": "OK" if ok else "FAILED", "handle": window_id}
        except Exception as exc:
            return failed(str(exc), self.name)

    def get_ssh_agent_info(self) -> dict[str, Any]:
        ps = shutil.which("pwsh") or shutil.which("powershell")
        service = None
        if ps:
            service = run([ps, "-NoProfile", "-NonInteractive", "-Command", "Get-Service ssh-agent | Select Name,Status,StartType | ConvertTo-Json -Compress"])
        keys = run([shutil.which("ssh-add") or "ssh-add", "-l"])
        return {"agent_type": "Windows OpenSSH Agent", "service": service, "keys": keys, "ready": keys["exit_code"] in (0, 1)}


def _can_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False
