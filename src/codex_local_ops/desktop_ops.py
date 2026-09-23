from __future__ import annotations

import os
import platform
import shutil
import time
from pathlib import Path
from typing import Any

from .config import install_root, load_config
from .models import unavailable
from .platforms import current_backend
from .processes import run
from .safety import assert_managed_or_trusted_path, computer_mode, require_mode


def _privacy_blocked(title: str = "", app: str = "") -> bool:
    privacy = load_config().get("privacy", {})
    apps = [str(x).lower() for x in privacy.get("excluded_apps", [])]
    titles = [str(x).lower() for x in privacy.get("excluded_window_titles", [])]
    return any(x in app.lower() for x in apps) or any(x in title.lower() for x in titles)


def info() -> dict[str, Any]:
    data = current_backend().get_desktop_info()
    data["computer_control_mode"] = computer_mode()
    return data


def list_windows() -> dict[str, Any]:
    return current_backend().list_windows()


def find_window(query: str) -> dict[str, Any]:
    result = list_windows()
    windows = result.get("windows", []) if isinstance(result, dict) else []
    matches = [w for w in windows if query.lower() in str(w.get("title", "")).lower()]
    return {"status": "OK", "matches": matches}


def focus_window(window_id: str) -> dict[str, Any]:
    require_mode("INTERACTIVE", "FULL")
    return current_backend().focus_window(window_id)


def screenshot(output: str | None = None) -> dict[str, Any]:
    p = assert_managed_or_trusted_path(output) if output else install_root() / "screenshots" / f"desktop-{int(time.time() * 1000)}.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    return current_backend().take_screenshot(p)


def _windows_window(action: str, handle: str) -> dict[str, Any]:
    try:
        from pywinauto import Desktop
        require_mode("FULL")
        w = Desktop(backend="uia").window(handle=int(handle, 0))
        title = w.window_text()
        if _privacy_blocked(title):
            return {"status": "PERMISSION_DENIED", "reason": "Window excluded by privacy settings"}
        if action == "minimize": w.minimize()
        elif action == "maximize": w.maximize()
        elif action == "restore": w.restore()
        elif action == "close": w.close()
        return {"status": "OK", "handle": handle, "action": action}
    except Exception as exc:
        return unavailable(f"Windows UI Automation action unavailable: {exc}", "windows")


def window_action(action: str, handle: str) -> dict[str, Any]:
    if platform.system() == "Windows":
        return _windows_window(action, handle)
    require_mode("FULL")
    return current_backend().window_action(action, handle)


def list_controls(handle: str) -> dict[str, Any]:
    if platform.system() != "Windows":
        return unavailable("Structured control enumeration requires an accessibility backend available to this session", platform.system().lower(), "Use AT-SPI on Linux or Accessibility permission on macOS")
    try:
        from pywinauto import Desktop
        w = Desktop(backend="uia").window(handle=int(handle, 0))
        title = w.window_text()
        if _privacy_blocked(title):
            return {"status": "PERMISSION_DENIED", "reason": "Window excluded by privacy settings"}
        rows = []
        for c in w.descendants():
            try:
                ei = c.element_info
                rows.append({"name": c.window_text(), "control_type": ei.control_type, "automation_id": ei.automation_id, "handle": getattr(c, "handle", None)})
            except Exception:
                continue
        return {"status": "OK", "controls": rows[:5000]}
    except Exception as exc:
        return unavailable(f"UI Automation unavailable: {exc}", "windows")


def control_action(handle: str, query: str, action: str, value: str | None = None) -> dict[str, Any]:
    require_mode("INTERACTIVE", "FULL")
    if platform.system() != "Windows":
        return unavailable("Structured control interaction requires a configured accessibility backend", platform.system().lower())
    try:
        from pywinauto import Desktop
        w = Desktop(backend="uia").window(handle=int(handle, 0))
        if _privacy_blocked(w.window_text()):
            return {"status": "PERMISSION_DENIED", "reason": "Window excluded by privacy settings"}
        c = w.child_window(title_re=f".*{query}.*")
        if action == "click": c.click_input()
        elif action == "get_text": return {"status": "OK", "text": c.window_text()}
        elif action == "set_text": c.set_edit_text(value or "")
        else: return {"status": "FAILED", "reason": f"Unknown control action: {action}"}
        return {"status": "OK", "action": action}
    except Exception as exc:
        return {"status": "FAILED", "reason": str(exc)}


def input_action(action: str, **kwargs) -> dict[str, Any]:
    require_mode("INTERACTIVE", "FULL")
    system = platform.system()
    if system == "Windows":
        try:
            from pywinauto import keyboard, mouse
            if action == "press": keyboard.send_keys(str(kwargs["keys"]), with_spaces=True)
            elif action == "hotkey": keyboard.send_keys(str(kwargs["keys"]), with_spaces=True)
            elif action == "move": mouse.move(coords=(int(kwargs["x"]), int(kwargs["y"])))
            elif action == "click": mouse.click(coords=(int(kwargs["x"]), int(kwargs["y"])), button=str(kwargs.get("button", "left")))
            elif action == "scroll": mouse.scroll(coords=(int(kwargs.get("x", 0)), int(kwargs.get("y", 0))), wheel_dist=int(kwargs.get("delta", 1)))
            elif action == "type": keyboard.send_keys(str(kwargs["text"]), with_spaces=True)
            return {"status": "OK", "action": action}
        except Exception as exc:
            return unavailable(f"pywinauto input unavailable: {exc}", "windows")
    if system == "Linux":
        if info().get("session") == "wayland":
            return unavailable("Global input injection is restricted on Wayland", "linux", "Use desktop accessibility/portal mechanisms supported by the active compositor")
        xdotool = shutil.which("xdotool")
        if not xdotool:
            return unavailable("xdotool is unavailable for this X11 session", "linux")
        mapping = {
            "press": [xdotool, "key", str(kwargs["keys"])],
            "hotkey": [xdotool, "key", str(kwargs["keys"])],
            "move": [xdotool, "mousemove", str(kwargs["x"]), str(kwargs["y"])],
            "click": [xdotool, "mousemove", str(kwargs["x"]), str(kwargs["y"]), "click", "1"],
            "type": [xdotool, "type", "--", str(kwargs["text"])],
        }
        if action not in mapping:
            return unavailable(f"Input action {action} is unavailable", "linux")
        return run(mapping[action])
    return unavailable("Direct desktop input requires Accessibility/Automation integration for this platform", system.lower(), "Enable Accessibility permission and use the supported application automation path")
