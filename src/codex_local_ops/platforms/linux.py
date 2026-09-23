from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..models import unavailable
from ..processes import run
from .base import PlatformBackend


class LinuxBackend(PlatformBackend):
    name = "linux"

    def get_system_info(self) -> dict[str, Any]:
        data = super().get_system_info()
        os_release = {}
        p = Path("/etc/os-release")
        if p.exists():
            for line in p.read_text(errors="replace").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    os_release[k] = v.strip().strip('"')
        data.update({"distribution": os_release.get("NAME"), "distribution_version": os_release.get("VERSION_ID"), "systemd": Path("/run/systemd/system").exists()})
        return data

    def get_shell(self) -> dict[str, Any]:
        shell = os.environ.get("SHELL") or shutil.which("bash") or shutil.which("sh")
        return {"shell": Path(shell).name if shell else None, "executable": shell}

    def service_status(self, name: str) -> dict[str, Any]:
        if shutil.which("systemctl") and Path("/run/systemd/system").exists():
            res = run(["systemctl", "show", name, "--no-page", "--property=Id,LoadState,ActiveState,SubState,UnitFileState"])
            return {"status": "OK" if res["exit_code"] == 0 else "FAILED", **res}
        if shutil.which("service"):
            return run(["service", name, "status"])
        return unavailable("No systemd or service(8) backend detected", self.name)

    def service_action(self, name: str, action: str) -> dict[str, Any]:
        if action not in {"start", "stop", "restart"}:
            return {"status": "FAILED", "reason": "Unsupported service action"}
        if shutil.which("systemctl") and Path("/run/systemd/system").exists():
            return run(["systemctl", action, name])
        if shutil.which("service"):
            return run(["service", name, action])
        return unavailable("No supported service manager detected", self.name)

    def open_path(self, path: Path) -> dict[str, Any]:
        exe = shutil.which("xdg-open")
        if not exe:
            return unavailable("xdg-open is unavailable", self.name, "Install xdg-utils", feature="open_path")
        proc = subprocess.Popen([exe, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"status": "OK", "pid": proc.pid, "path": str(path)}

    def reveal_file(self, path: Path) -> dict[str, Any]:
        return self.open_path(path.parent)

    def get_desktop_info(self) -> dict[str, Any]:
        session = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
        display = os.environ.get("DISPLAY")
        wayland = os.environ.get("WAYLAND_DISPLAY")
        desktop = os.environ.get("XDG_CURRENT_DESKTOP")
        available = bool(display or wayland)
        return {
            "available": available,
            "session": session or ("wayland" if wayland else "x11" if display else "headless"),
            "desktop": desktop,
            "display": display,
            "wayland_display": wayland,
            "global_input": "restricted" if wayland else "available" if display else "unavailable",
            "portal": bool(shutil.which("gdbus") or shutil.which("busctl")),
            "pipewire": bool(shutil.which("pipewire") or shutil.which("pw-cli")),
        }

    def take_screenshot(self, output: Path) -> dict[str, Any]:
        info = self.get_desktop_info()
        if not info["available"]:
            return unavailable("Headless session", self.name)
        if info["session"] == "wayland":
            if shutil.which("gnome-screenshot"):
                return run(["gnome-screenshot", "-f", str(output)])
            return unavailable("Wayland capture requires an available desktop portal/screenshot implementation", self.name, "Use xdg-desktop-portal or the desktop environment's screenshot service")
        try:
            import mss
            from PIL import Image
        except ImportError:
            return unavailable(
                "Screenshot dependencies are optional and not installed",
                self.name,
                "Install codex-local-ops[desktop]",
                feature="desktop_screenshot",
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[0])
            Image.frombytes("RGB", shot.size, shot.rgb).save(output)
        return {"status": "OK", "path": str(output)}

    def list_windows(self) -> dict[str, Any]:
        info = self.get_desktop_info()
        if not info.get("available"):
            return unavailable("Headless session", self.name, feature="window_enumeration")
        if info.get("session") == "wayland":
            return unavailable(
                "Generic window enumeration is compositor-restricted on Wayland",
                self.name,
                "Use AT-SPI for accessible application trees or compositor-specific portal APIs",
                feature="window_enumeration",
            )
        exe = shutil.which("wmctrl")
        if not exe:
            return unavailable("wmctrl is unavailable for this X11 session", self.name, "Install wmctrl", feature="window_enumeration")
        result = run([exe, "-lx"])
        if result.get("exit_code") != 0:
            return {"status": "FAILED", **result}
        rows = []
        for line in result.get("stdout", "").splitlines():
            parts = line.split(None, 4)
            if len(parts) >= 5:
                rows.append({"handle": parts[0], "desktop": parts[1], "class": parts[2], "host": parts[3], "title": parts[4], "visible": True})
        return {"status": "OK", "windows": rows}

    def focus_window(self, window_id: str) -> dict[str, Any]:
        info = self.get_desktop_info()
        if info.get("session") == "wayland":
            return unavailable("Global window focus is compositor-restricted on Wayland", self.name, feature="window_focus")
        exe = shutil.which("wmctrl")
        if not exe:
            return unavailable("wmctrl is unavailable for this X11 session", self.name, "Install wmctrl", feature="window_focus")
        result = run([exe, "-ia", str(window_id)])
        return {"status": "OK" if result.get("exit_code") == 0 else "FAILED", **result}

    def window_action(self, action: str, window_id: str) -> dict[str, Any]:
        info = self.get_desktop_info()
        if info.get("session") == "wayland":
            return unavailable("Global window actions are compositor-restricted on Wayland", self.name, feature="window_action")
        exe = shutil.which("wmctrl")
        if not exe:
            return unavailable("wmctrl is unavailable for this X11 session", self.name, "Install wmctrl", feature="window_action")
        handle = str(window_id)
        if action == "close":
            args = [exe, "-ic", handle]
        elif action == "minimize":
            args = [exe, "-ir", handle, "-b", "add,hidden"]
        elif action == "maximize":
            args = [exe, "-ir", handle, "-b", "add,maximized_vert,maximized_horz"]
        elif action == "restore":
            args = [exe, "-ir", handle, "-b", "remove,hidden,maximized_vert,maximized_horz"]
        else:
            return {"status": "FAILED", "reason": f"Unsupported window action: {action}"}
        result = run(args)
        return {"status": "OK" if result.get("exit_code") == 0 else "FAILED", "action": action, "handle": handle, **result}

    def get_ssh_agent_info(self) -> dict[str, Any]:
        socket = os.environ.get("SSH_AUTH_SOCK")
        pid = os.environ.get("SSH_AGENT_PID")
        if not socket:
            return {"ready": False, "agent_type": None, "socket": None, "reason": "SSH_AUTH_SOCK is not set"}
        keys = run([shutil.which("ssh-add") or "ssh-add", "-l"])
        agent_type = "gpg-agent" if "gpg" in socket.lower() else "gnome-keyring" if "keyring" in socket.lower() else "ssh-agent-compatible"
        return {"ready": keys["exit_code"] in (0, 1), "agent_type": agent_type, "socket": socket, "pid": pid, "keys": keys}
