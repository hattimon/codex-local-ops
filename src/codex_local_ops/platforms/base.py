from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Any

import psutil

from ..models import unavailable
from ..processes import run


class PlatformBackend:
    name = "generic"

    def get_system_info(self) -> dict[str, Any]:
        return {
            "platform": self.name,
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "hostname": platform.node(),
        }

    def get_shell(self) -> dict[str, Any]:
        shell = os.environ.get("SHELL") or os.environ.get("COMSPEC")
        return {"shell": shell, "executable": shutil.which(Path(shell).name) if shell else None}

    def run_command(self, argv: list[str], *, cwd: Path | None = None, timeout: int = 60) -> dict:
        return run(argv, cwd=cwd, timeout=timeout)

    def list_processes(self) -> list[dict[str, Any]]:
        rows = []
        for proc in psutil.process_iter(["pid", "name", "username", "status", "create_time"]):
            try:
                rows.append(proc.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return rows

    def service_status(self, name: str) -> dict[str, Any]:
        return unavailable("Service management backend unavailable", self.name)

    def service_action(self, name: str, action: str) -> dict[str, Any]:
        return unavailable(f"Service action {action} unavailable", self.name)

    def open_path(self, path: Path) -> dict[str, Any]:
        return unavailable("Open path not implemented by this platform backend", self.name)

    def reveal_file(self, path: Path) -> dict[str, Any]:
        return self.open_path(path.parent)

    def get_desktop_info(self) -> dict[str, Any]:
        return {"available": False, "session": "headless"}

    def take_screenshot(self, output: Path) -> dict[str, Any]:
        return unavailable("Screenshot backend unavailable", self.name)

    def list_windows(self) -> dict[str, Any]:
        return unavailable("Window enumeration unavailable", self.name)

    def focus_window(self, window_id: str) -> dict[str, Any]:
        return unavailable("Window focus unavailable", self.name)

    def window_action(self, action: str, window_id: str) -> dict[str, Any]:
        return unavailable(f"Window action {action} unavailable", self.name)

    def get_ssh_agent_info(self) -> dict[str, Any]:
        return unavailable("SSH agent inspection unavailable", self.name)

    def capabilities(self) -> dict[str, Any]:
        return {
            "platform": self.name,
            "desktop": self.get_desktop_info(),
            "executables": {name: bool(shutil.which(name)) for name in ["git", "gh", "docker", "ssh", "scp", "sftp", "ffmpeg", "ffprobe", "node", "npm"]},
        }
