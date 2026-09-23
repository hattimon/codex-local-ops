"""Manual interactive Windows validation for Codex Local Ops.

Run this only from a normal, interactive Windows desktop session. The script
deliberately leaves the Local Ops Manager open after validation so the user can
inspect it. It stores only capability/status information and never persists the
enumerated window titles.
"""
from __future__ import annotations

import argparse
import ctypes
import importlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Any


ALLOWED_STATUSES = {"PASS", "FAIL", "SKIPPED", "CAPABILITY_UNAVAILABLE"}
MANAGER_TITLE = "Codex Local Ops Manager"
SAFE_TEST_TITLE = "Codex Local Ops Screenshot Test"
DEFAULT_GUI_WAIT_SECONDS = 20.0
POLL_INTERVAL_SECONDS = 0.25


def _reason(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _module_version(module_name: str, distribution: str | None = None) -> str | None:
    try:
        module = importlib.import_module(module_name)
        version = getattr(module, "__version__", None)
        if version:
            return str(version)
    except Exception:
        return None
    try:
        return metadata.version(distribution or module_name)
    except metadata.PackageNotFoundError:
        return None


def _check_import(module_name: str, distribution: str | None = None) -> dict[str, Any]:
    try:
        importlib.import_module(module_name)
        result: dict[str, Any] = {"status": "PASS"}
        version = _module_version(module_name, distribution)
        if version:
            result["version"] = version
        return result
    except ModuleNotFoundError as exc:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": _reason(exc)}
    except Exception as exc:
        return {"status": "FAIL", "reason": _reason(exc)}


def _load_clops_entry_point(project_root: Path) -> tuple[str, str, str]:
    pyproject = project_root / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    raw = str(data["project"]["scripts"]["clops"])
    if ":" not in raw:
        raise ValueError(f"Unsupported clops entry point: {raw}")
    module_name, function_name = raw.split(":", 1)
    if not module_name or not function_name:
        raise ValueError(f"Invalid clops entry point: {raw}")
    return raw, module_name, function_name


def _desktop_checks() -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        from codex_local_ops import desktop_ops

        info = desktop_ops.info()
        backend_status = "PASS" if bool(info.get("available")) else "CAPABILITY_UNAVAILABLE"
        backend = {
            "status": backend_status,
            "available": bool(info.get("available")),
            "session": info.get("session"),
            "ui_automation": info.get("ui_automation"),
            "computer_control_mode": info.get("computer_control_mode"),
        }

        windows_result = desktop_ops.list_windows()
        windows = windows_result.get("windows", []) if isinstance(windows_result, dict) else []
        visible_count = sum(1 for item in windows if bool(item.get("visible")))
        if windows_result.get("status") == "OK" and windows:
            enumeration = {
                "status": "PASS",
                "count": len(windows),
                "visible_count": visible_count,
            }
        elif windows_result.get("status") == "CAPABILITY_UNAVAILABLE":
            enumeration = {
                "status": "CAPABILITY_UNAVAILABLE",
                "count": 0,
                "reason": str(windows_result.get("reason", "Window enumeration unavailable")),
            }
        else:
            enumeration = {
                "status": "FAIL",
                "count": len(windows),
                "reason": str(windows_result.get("reason", "No ordinary windows detected")),
            }
        return backend, enumeration
    except Exception as exc:
        failure = {"status": "FAIL", "reason": _reason(exc)}
        return failure, {**failure, "count": 0}


def _window_metadata(hwnd: int, detection_backend: str) -> dict[str, Any]:
    try:
        import win32gui

        title = win32gui.GetWindowText(hwnd)
        class_name = win32gui.GetClassName(hwnd)
    except Exception:
        title = ""
        class_name = ""
    owner_pid = ctypes.c_ulong(0)
    try:
        ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(owner_pid))
    except Exception:
        owner_pid.value = 0
    return {
        "hwnd": int(hwnd),
        "pid": int(owner_pid.value),
        "title": str(title),
        "class_name": str(class_name),
        "detection_backend": detection_backend,
    }


def _title_matches(value: str, title: str, *, regex: bool) -> bool:
    if regex:
        return re.search(rf".*{re.escape(title)}.*", value, flags=re.IGNORECASE) is not None
    return value == title


def _find_window_win32(title: str, *, regex: bool) -> dict[str, Any] | None:
    try:
        import win32gui
    except Exception:
        return None

    matches: list[int] = []

    def callback(hwnd: int, _extra: Any) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            window_title = win32gui.GetWindowText(hwnd)
            if _title_matches(str(window_title), title, regex=regex):
                matches.append(int(hwnd))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except Exception:
        return None
    if not matches:
        return None
    return _window_metadata(matches[0], "win32-enum-regex" if regex else "win32-enum-exact")


def _find_window_pywinauto(title: str, backend: str, *, regex: bool) -> dict[str, Any] | None:
    try:
        from pywinauto import Desktop
    except Exception:
        return None
    try:
        windows = Desktop(backend=backend).windows()
    except Exception:
        return None
    for window in windows:
        try:
            if not window.is_visible():
                continue
            window_title = str(window.window_text())
            if not _title_matches(window_title, title, regex=regex):
                continue
            hwnd = int(window.handle)
            result = _window_metadata(hwnd, f"pywinauto-{backend}-regex" if regex else f"pywinauto-{backend}-exact")
            if not result["title"]:
                result["title"] = window_title
            try:
                if not result["class_name"]:
                    result["class_name"] = str(window.class_name())
            except Exception:
                pass
            try:
                if not result["pid"]:
                    result["pid"] = int(window.element_info.process_id)
            except Exception:
                pass
            return result
        except Exception:
            continue
    return None


def _find_window(title: str, timeout: float) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for regex in (False, True):
            found = _find_window_win32(title, regex=regex)
            if found is not None:
                return found
            for backend in ("uia", "win32"):
                found = _find_window_pywinauto(title, backend, regex=regex)
                if found is not None:
                    return found
        time.sleep(POLL_INTERVAL_SECONDS)
    return None


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil

        return bool(psutil.pid_exists(pid))
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False


def _launch_manager(project_root: Path, wait_seconds: float) -> tuple[dict[str, Any], dict[str, Any] | None]:
    try:
        raw_entry, module_name, function_name = _load_clops_entry_point(project_root)
    except Exception as exc:
        return {"status": "FAIL", "reason": _reason(exc)}, None

    runner = (
        "import importlib,sys; "
        "module_name=sys.argv[1]; function_name=sys.argv[2]; "
        "sys.argv=['clops','manager']; "
        "getattr(importlib.import_module(module_name), function_name)()"
    )
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", runner, module_name, function_name],
            cwd=str(project_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except Exception as exc:
        return {
            "status": "FAIL",
            "entry_point": raw_entry,
            "reason": _reason(exc),
        }, None

    window = _find_window(MANAGER_TITLE, wait_seconds)
    launcher_running = process.poll() is None
    window_found = window is not None
    owner_pid = int(window.get("pid", 0)) if window else 0
    owner_running = _pid_is_running(owner_pid)
    running = launcher_running or owner_running
    status = "PASS" if running and window_found else "FAIL"
    result: dict[str, Any] = {
        "status": status,
        "entry_point": raw_entry,
        "launcher_pid": process.pid,
        "pid": owner_pid or process.pid,
        "launcher_running": launcher_running,
        "running": running,
        "window_found": window_found,
        "window_title": window.get("title") if window else MANAGER_TITLE,
        "left_open_for_user": running,
    }
    if window is not None:
        result.update(
            {
                "hwnd": window.get("hwnd"),
                "owner_pid": window.get("pid"),
                "title": window.get("title"),
                "class_name": window.get("class_name"),
                "detection_backend": window.get("detection_backend"),
            }
        )
    if not running:
        result["exit_code"] = process.returncode
        result["reason"] = "Manager process exited before validation completed"
    elif not window_found:
        result["reason"] = f"Manager window was not found within {wait_seconds:g} seconds"
    return result, window


def _capture_window_with_pywinauto(hwnd: int, output: Path, backend: str) -> None:
    from pywinauto import Desktop

    window = Desktop(backend=backend).window(handle=int(hwnd))
    image = window.capture_as_image()
    image.save(output, format="PNG")


def _capture_window_with_mss(hwnd: int, output: Path) -> None:
    import mss
    from PIL import Image
    import win32gui

    try:
        win32gui.SetForegroundWindow(int(hwnd))
    except Exception:
        pass
    time.sleep(0.4)
    left, top, right, bottom = win32gui.GetWindowRect(int(hwnd))
    width = int(right - left)
    height = int(bottom - top)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Invalid window rectangle: {width}x{height}")
    monitor = {"left": int(left), "top": int(top), "width": width, "height": height}
    with mss.mss() as sct:
        shot = sct.grab(monitor)
        Image.frombytes("RGB", shot.size, shot.rgb).save(output, format="PNG")


def _capture_desktop_with_mss(output: Path) -> None:
    import mss
    from PIL import Image

    with mss.mss() as sct:
        monitor = sct.monitors[0]
        shot = sct.grab(monitor)
        Image.frombytes("RGB", shot.size, shot.rgb).save(output, format="PNG")


def _spawn_safe_test_window(
    project_root: Path, wait_seconds: float
) -> tuple[dict[str, Any], subprocess.Popen[Any] | None, dict[str, Any] | None]:
    code = (
        "import tkinter as tk; "
        "root=tk.Tk(); "
        f"root.title({SAFE_TEST_TITLE!r}); "
        "root.geometry('800x500+120+120'); "
        "root.attributes('-topmost', True); "
        "label=tk.Label(root,text='Codex Local Ops - safe screenshot test',font=('Segoe UI',18)); "
        "label.pack(expand=True); root.mainloop()"
    )
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=str(project_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except Exception as exc:
        return {"status": "FAIL", "reason": _reason(exc)}, None, None
    window = _find_window(SAFE_TEST_TITLE, wait_seconds)
    if window is None:
        running = process.poll() is None
        if process.poll() is None:
            process.terminate()
        return {
            "status": "FAIL",
            "pid": process.pid,
            "running": running,
            "window_found": False,
            "reason": f"Safe test window was not found within {wait_seconds:g} seconds",
        }, None, None
    return {
        "status": "PASS",
        "pid": int(window.get("pid", process.pid)),
        "hwnd": int(window["hwnd"]),
        "title": window.get("title"),
        "class_name": window.get("class_name"),
        "detection_backend": window.get("detection_backend"),
        "window_found": True,
    }, process, window


def _validate_png(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "FAIL", "path": str(path), "reason": "PNG was not created"}
    size = path.stat().st_size
    if size <= 0:
        return {
            "status": "FAIL",
            "path": str(path),
            "size_bytes": size,
            "reason": "PNG file size is zero",
        }
    try:
        from PIL import Image, ImageChops

        with Image.open(path) as image:
            detected_format = image.format
            width, height = image.size
            image.load()
            rgb = image.convert("RGB")
            baseline = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
            nonblank = ImageChops.difference(rgb, baseline).getbbox() is not None
    except Exception as exc:
        return {
            "status": "FAIL",
            "path": str(path),
            "size_bytes": size,
            "pillow_openable": False,
            "reason": _reason(exc),
        }
    if detected_format != "PNG" or width <= 0 or height <= 0 or not nonblank:
        return {
            "status": "FAIL",
            "path": str(path),
            "size_bytes": size,
            "width": int(width),
            "height": int(height),
            "format": detected_format,
            "pillow_openable": True,
            "nonblank": bool(nonblank),
            "reason": "PNG format, dimensions, or nonblank validation failed",
        }
    return {
        "status": "PASS",
        "path": str(path),
        "size_bytes": size,
        "width": int(width),
        "height": int(height),
        "format": detected_format,
        "pillow_openable": True,
        "nonblank": bool(nonblank),
    }


def _screenshot(
    manager_window: dict[str, Any] | None,
    project_root: Path,
    output: Path,
    wait_seconds: float,
) -> dict[str, dict[str, Any]]:
    output.parent.mkdir(parents=True, exist_ok=True)
    test_window_creation: dict[str, Any]
    target = manager_window
    safe_process: subprocess.Popen[Any] | None = None
    if target is not None:
        test_window_creation = {
            "status": "SKIPPED",
            "reason": "Manager window is available as the safe screenshot target",
        }
    else:
        test_window_creation, safe_process, target = _spawn_safe_test_window(project_root, wait_seconds)

    window_result: dict[str, Any]
    if target is None:
        window_result = {
            "status": "SKIPPED",
            "path": str(output),
            "reason": "No safe window target was available for window capture",
        }
    else:
        attempts: list[str] = []
        hwnd = int(target["hwnd"])
        source = str(target.get("title") or "safe window")
        window_result = {"status": "FAIL", "path": str(output), "source": source, "hwnd": hwnd}
        for backend in ("uia", "win32"):
            try:
                _capture_window_with_pywinauto(hwnd, output, backend)
                candidate = _validate_png(output)
                candidate.update({"source": source, "hwnd": hwnd, "capture_backend": f"pywinauto-{backend}"})
                window_result = candidate
                if candidate["status"] == "PASS":
                    break
            except Exception as exc:
                attempts.append(f"pywinauto-{backend}: {_reason(exc)}")
        if window_result.get("status") != "PASS":
            try:
                _capture_window_with_mss(hwnd, output)
                candidate = _validate_png(output)
                candidate.update({"source": source, "hwnd": hwnd, "capture_backend": "mss-window-crop"})
                window_result = candidate
            except Exception as exc:
                attempts.append(f"mss-window-crop: {_reason(exc)}")
        if window_result.get("status") != "PASS":
            window_result["reason"] = "; ".join(attempts) or window_result.get("reason", "Window capture failed")

    desktop_result: dict[str, Any]
    if window_result.get("status") == "PASS":
        desktop_result = {
            "status": "SKIPPED",
            "path": str(output),
            "reason": "Window screenshot passed; full desktop fallback was not needed",
        }
    else:
        try:
            _capture_desktop_with_mss(output)
            desktop_result = _validate_png(output)
            desktop_result["capture_backend"] = "mss-full-desktop"
        except ModuleNotFoundError as exc:
            desktop_result = {
                "status": "CAPABILITY_UNAVAILABLE",
                "path": str(output),
                "reason": _reason(exc),
            }
        except Exception as exc:
            desktop_result = {"status": "FAIL", "path": str(output), "reason": _reason(exc)}

    try:
        if safe_process is not None and safe_process.poll() is None:
            safe_process.terminate()
    except Exception:
        pass

    overall_status = "PASS" if "PASS" in {window_result.get("status"), desktop_result.get("status")} else "FAIL"
    screenshot = {
        "status": overall_status,
        "path": str(output),
        "successful_backend": (
            window_result.get("capture_backend")
            if window_result.get("status") == "PASS"
            else desktop_result.get("capture_backend")
        ),
    }
    return {
        "test_window_creation": test_window_creation,
        "window_screenshot": window_result,
        "desktop_screenshot": desktop_result,
        "screenshot": screenshot,
    }


def _first_run_check() -> dict[str, Any]:
    try:
        from codex_local_ops.wizard import first_run_status

        current = first_run_status()
        completed = bool(current.get("completed"))
        return {
            "status": "PASS" if completed else "FAIL",
            "first_run_completed": completed,
            "trusted_root_count": len(current.get("trusted_roots", [])),
            "local_profile": current.get("local_profile"),
            "computer_mode": current.get("computer_mode"),
        }
    except Exception as exc:
        return {"status": "FAIL", "first_run_completed": False, "reason": _reason(exc)}


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _exit_code(report: dict[str, Any]) -> int:
    required = (
        "python",
        "venv",
        "pywinauto",
        "pywin32",
        "desktop_backend",
        "window_enumeration",
        "first_run",
        "gui_manager",
        "screenshot",
    )
    return 0 if all(report.get(key, {}).get("status") == "PASS" for key in required) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Codex Local Ops in an interactive Windows desktop session")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--gui-wait-seconds", type=float, default=DEFAULT_GUI_WAIT_SECONDS)
    args = parser.parse_args()

    project_root = args.project_root.expanduser().resolve()
    artifacts = project_root / "artifacts" / "interactive-validation"
    screenshot_path = artifacts / "desktop-test.png"
    report_path = artifacts / "result.json"

    in_venv = sys.prefix != sys.base_prefix
    win32gui_check = _check_import("win32gui", "pywin32")
    win32api_check = _check_import("win32api", "pywin32")
    pywin32_status = (
        "PASS"
        if win32gui_check["status"] == "PASS" and win32api_check["status"] == "PASS"
        else "CAPABILITY_UNAVAILABLE"
        if "CAPABILITY_UNAVAILABLE" in {win32gui_check["status"], win32api_check["status"]}
        else "FAIL"
    )

    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": {
            "status": "PASS" if os.name == "nt" else "FAIL",
            "executable": sys.executable,
            "version": sys.version,
        },
        "venv": {
            "status": "PASS" if in_venv else "FAIL",
            "prefix": sys.prefix,
            "base_prefix": sys.base_prefix,
        },
        "pywinauto": _check_import("pywinauto", "pywinauto"),
        "pywin32": {
            "status": pywin32_status,
            "version": _module_version("win32gui", "pywin32"),
            "modules": {"win32gui": win32gui_check["status"], "win32api": win32api_check["status"]},
        },
        "win32gui": win32gui_check,
        "win32api": win32api_check,
        "PIL": _check_import("PIL", "Pillow"),
        "mss": _check_import("mss", "mss"),
    }
    if os.name != "nt":
        report["python"]["reason"] = "Interactive Windows validation must run on Windows"

    backend, enumeration = _desktop_checks()
    report["desktop_backend"] = backend
    report["window_enumeration"] = enumeration
    report["first_run"] = _first_run_check()
    report["first_run_completed"] = bool(report["first_run"].get("first_run_completed"))

    gui_result, manager_window = _launch_manager(project_root, args.gui_wait_seconds)
    report["gui_manager"] = gui_result
    screenshot_results = _screenshot(manager_window, project_root, screenshot_path, args.gui_wait_seconds)
    report.update(screenshot_results)

    for value in report.values():
        if isinstance(value, dict) and "status" in value and value["status"] not in ALLOWED_STATUSES:
            value["status"] = "FAIL"
            value.setdefault("reason", "Validator produced an unsupported status")

    _write_report(report_path, report)
    print(json.dumps({"status": "PASS" if _exit_code(report) == 0 else "FAIL", "report": str(report_path), "screenshot": str(screenshot_path)}, ensure_ascii=False))
    return _exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
