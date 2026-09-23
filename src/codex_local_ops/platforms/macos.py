from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..models import unavailable
from ..processes import run
from .base import PlatformBackend


class MacOSBackend(PlatformBackend):
    name = "macos"

    def get_system_info(self) -> dict[str, Any]:
        data = super().get_system_info()
        data.update({"homebrew": shutil.which("brew"), "launchd": bool(shutil.which("launchctl")), "architecture": data.get("architecture")})
        return data

    def get_shell(self) -> dict[str, Any]:
        shell = os.environ.get("SHELL") or shutil.which("zsh") or shutil.which("bash")
        return {"shell": Path(shell).name if shell else None, "executable": shell}

    def service_status(self, name: str) -> dict[str, Any]:
        if not shutil.which("launchctl"):
            return unavailable("launchctl unavailable", self.name)
        return run(["launchctl", "print", name])

    def service_action(self, name: str, action: str) -> dict[str, Any]:
        if not shutil.which("launchctl"):
            return unavailable("launchctl unavailable", self.name)
        if action == "restart":
            return run(["launchctl", "kickstart", "-k", name])
        return unavailable("Generic launchd start/stop requires a launchd domain/target and is not inferred from a bare service name", self.name, "Use a complete launchd target such as gui/UID/label")

    def open_path(self, path: Path) -> dict[str, Any]:
        exe = shutil.which("open")
        if not exe:
            return unavailable("open command unavailable", self.name, feature="open_path")
        proc = subprocess.Popen([exe, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"status": "OK", "pid": proc.pid, "path": str(path)}

    def reveal_file(self, path: Path) -> dict[str, Any]:
        exe = shutil.which("open")
        if not exe:
            return unavailable("open command unavailable", self.name, feature="reveal_file")
        proc = subprocess.Popen([exe, "-R", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"status": "OK", "pid": proc.pid, "path": str(path)}

    def get_desktop_info(self) -> dict[str, Any]:
        logged_in = bool(os.environ.get("TERM_PROGRAM") or os.environ.get("DISPLAY") or os.environ.get("SSH_CONNECTION") is None)
        return {
            "available": logged_in,
            "session": "aqua" if logged_in else "headless",
            "accessibility_permission": "runtime-check-required",
            "screen_recording_permission": "runtime-check-required",
            "automation_permission": "runtime-check-required",
        }

    def take_screenshot(self, output: Path) -> dict[str, Any]:
        if not shutil.which("screencapture"):
            return unavailable("screencapture unavailable", self.name)
        output.parent.mkdir(parents=True, exist_ok=True)
        res = run(["screencapture", "-x", str(output)])
        if res["exit_code"] != 0:
            res["suggested_setup"] = "Enable Screen Recording for the terminal/Codex host in System Settings > Privacy & Security"
        return res

    def list_windows(self) -> dict[str, Any]:
        exe = shutil.which("osascript")
        if not exe:
            return unavailable("osascript unavailable", self.name, feature="window_enumeration")
        script = r'''const se=Application("System Events"); const rows=[]; const ps=se.applicationProcesses.whose({backgroundOnly:false})(); for (const p of ps) { let pid,name,ws; try { pid=Number(p.unixId()); name=String(p.name()); ws=p.windows(); } catch(e) { continue; } for (let i=0;i<ws.length;i++) { let title=""; try { title=String(ws[i].name()||""); } catch(e) {} rows.push({handle:String(pid)+":"+String(i+1),pid:pid,process:name,title:title,visible:true}); } } JSON.stringify(rows);'''
        result = run([exe, "-l", "JavaScript", "-e", script])
        if result.get("exit_code") != 0:
            result["suggested_setup"] = "Grant Accessibility permission to the terminal/Codex host in System Settings > Privacy & Security"
            return {"status": "FAILED", **result}
        try:
            rows = json.loads((result.get("stdout") or "[]").strip() or "[]")
        except (TypeError, json.JSONDecodeError) as exc:
            return {"status": "FAILED", "reason": f"Could not parse osascript window inventory: {exc}"}
        return {"status": "OK", "windows": rows if isinstance(rows, list) else []}

    def focus_window(self, window_id: str) -> dict[str, Any]:
        exe = shutil.which("osascript")
        if not exe:
            return unavailable("osascript unavailable", self.name, feature="window_focus")
        raw = str(window_id)
        try:
            pid_raw, index_raw = raw.split(":", 1)
            pid, index = int(pid_raw), int(index_raw)
            if pid <= 0 or index <= 0:
                raise ValueError
            script = (
                'tell application "System Events"\n'
                f'  set targetProcess to first application process whose unix id is {pid}\n'
                '  set frontmost of targetProcess to true\n'
                f'  perform action "AXRaise" of window {index} of targetProcess\n'
                'end tell'
            )
        except ValueError:
            safe_name = raw.replace("\\", "\\\\").replace('"', '\\"')
            script = f'tell application "System Events" to set frontmost of process "{safe_name}" to true'
        result = run([exe, "-e", script])
        if result.get("exit_code") != 0:
            result["suggested_setup"] = "Grant Accessibility permission to the terminal/Codex host in System Settings > Privacy & Security"
        return {"status": "OK" if result.get("exit_code") == 0 else "FAILED", "handle": raw, **result}

    def window_action(self, action: str, window_id: str) -> dict[str, Any]:
        if action not in {"close", "minimize", "maximize", "restore"}:
            return {"status": "FAILED", "reason": f"Unsupported window action: {action}"}
        exe = shutil.which("osascript")
        if not exe:
            return unavailable("osascript unavailable", self.name, feature="window_action")
        raw = str(window_id)
        try:
            pid_raw, index_raw = raw.split(":", 1)
            pid, index = int(pid_raw), int(index_raw)
            if pid <= 0 or index <= 0:
                raise ValueError
        except ValueError:
            return {"status": "FAILED", "reason": "macOS window handle must use pid:index from desktop_windows"}
        if action == "close":
            command = 'perform action "AXClose" of targetWindow'
        elif action == "minimize":
            command = 'set value of attribute "AXMinimized" of targetWindow to true'
        elif action == "maximize":
            command = 'set value of attribute "AXFullScreen" of targetWindow to true'
        else:
            command = 'set value of attribute "AXMinimized" of targetWindow to false\ntry\nset value of attribute "AXFullScreen" of targetWindow to false\nend try'
        script = (
            'tell application "System Events"\n'
            f'  set targetProcess to first application process whose unix id is {pid}\n'
            f'  set targetWindow to window {index} of targetProcess\n'
            + "\n".join(f"  {line}" for line in command.splitlines())
            + '\nend tell'
        )
        result = run([exe, "-e", script])
        if result.get("exit_code") != 0:
            result["suggested_setup"] = "Grant Accessibility permission to the terminal/Codex host in System Settings > Privacy & Security"
        return {"status": "OK" if result.get("exit_code") == 0 else "FAILED", "action": action, "handle": raw, **result}
    def get_ssh_agent_info(self) -> dict[str, Any]:
        socket = os.environ.get("SSH_AUTH_SOCK")
        keys = run([shutil.which("ssh-add") or "ssh-add", "-l"]) if socket else None
        return {"ready": bool(socket and keys and keys["exit_code"] in (0, 1)), "agent_type": "macOS OpenSSH", "socket": socket, "keys": keys}
