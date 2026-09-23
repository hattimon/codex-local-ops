"""STDIO MCP bootstrap server for Codex Local Ops.

This module intentionally keeps tool results JSON-shaped: failures are returned as
structured results so a client does not lose the MCP session after one bad call.
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
import time
import logging
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from . import __version__
from . import (
    animation_ops,
    browser_ops,
    desktop_ops,
    docker_ops,
    files_ops,
    git_ops,
    jobs,
    media_ops,
    obs_ops,
    projects,
    ssh_ops,
    wizard,
    wsl_ops,
)
from .audit import emit
from .config import ensure_config, install_root, load_config, save_config
from .diagnostics import run_diagnostics
from .models import failed
from .platforms import current_backend
from .processes import run
from .safety import (
    assert_trusted_path,
    computer_mode,
    permission_profile,
    require_mode,
    require_raw_execution,
    sanitize,
    trusted_roots,
)
from .secrets import current_secret_store


# The transport exclusively owns stdout; audit records are written to a file.
logging.getLogger("mcp").setLevel(logging.WARNING)

mcp = FastMCP("codexLocalOps", instructions="Local Windows operations bridge. Tool results are structured JSON objects.")


def _result(tool: str, args: dict[str, Any], callback: Callable[[], dict[str, Any]], *, raw: bool = False) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if raw:
            require_raw_execution()
        result = callback()
        if not isinstance(result, dict):
            result = {"status": "OK", "data": result}
    except PermissionError as exc:
        result = {"status": "PERMISSION_DENIED", "reason": str(exc)}
    except FileNotFoundError as exc:
        result = {"status": "NOT_FOUND", "reason": str(exc)}
    except Exception as exc:  # boundary: never crash an MCP session for one call
        result = failed(str(exc), platform.system().lower())
    result = sanitize(result)
    try:
        emit(tool, args, result, started, approval_class="raw" if raw else "standard")
    except Exception:
        pass
    return result


def _shell_exe() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell") or os.environ.get("COMSPEC")


def _raw_shell(command: str, cwd: str | None, timeout: int) -> dict:
    root = assert_trusted_path(cwd, must_exist=True) if cwd else None
    executable = _shell_exe()
    if not executable:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": "No local shell executable found"}
    if Path(executable).name.lower() in {"powershell.exe", "pwsh.exe"}:
        args = [executable, "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        args = [executable, "/d", "/s", "/c", command]
    result = run(args, cwd=root, timeout=timeout)
    result["status"] = "OK" if result.get("exit_code") == 0 else "FAILED"
    return result


def _powershell(script: str, cwd: str | None, timeout: int) -> dict:
    root = assert_trusted_path(cwd, must_exist=True) if cwd else None
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if not executable:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": "PowerShell not found"}
    result = run([executable, "-NoProfile", "-NonInteractive", "-Command", script], cwd=root, timeout=timeout)
    result["status"] = "OK" if result.get("exit_code") == 0 else "FAILED"
    return result


@mcp.tool(name="platform_info")
def platform_info() -> dict:
    return _result("platform_info", {}, lambda: {"status": "OK", "server": "codexLocalOps", "package_version": __version__, **current_backend().get_system_info()})


@mcp.tool(name="platform_capabilities")
def platform_capabilities() -> dict:
    return _result("platform_capabilities", {}, lambda: {"status": "OK", **current_backend().capabilities()})


@mcp.tool(name="local_system_info")
def local_system_info() -> dict:
    return _result("local_system_info", {}, lambda: {"status": "OK", **current_backend().get_system_info(), "install_root": str(install_root())})


@mcp.tool(name="local_shell_info")
def local_shell_info() -> dict:
    return _result("local_shell_info", {}, lambda: {"status": "OK", **current_backend().get_shell(), "permission_profile": permission_profile()})


@mcp.tool(name="local_shell_run")
def local_shell_run(command: str, cwd: str | None = None, timeout: int = 120) -> dict:
    return _result("local_shell_run", {"command": command, "path": cwd}, lambda: _raw_shell(command, cwd, timeout), raw=True)


@mcp.tool(name="powershell_run")
def powershell_run(script: str, cwd: str | None = None, timeout: int = 120) -> dict:
    return _result("powershell_run", {"script": script, "path": cwd}, lambda: _powershell(script, cwd, timeout), raw=True)


@mcp.tool(name="project_current")
def project_current() -> dict:
    def action() -> dict:
        current = Path.cwd().resolve()
        return {"status": "OK", "path": str(current), "trusted": any(current.is_relative_to(root) for root in trusted_roots())}
    return _result("project_current", {}, action)


@mcp.tool(name="project_detect")
def project_detect(path: str) -> dict:
    return _result("project_detect", {"path": path}, lambda: {"status": "OK", **projects.detect(path)})


@mcp.tool(name="local_read_file")
def local_read_file(path: str, max_bytes: int = 1_000_000) -> dict:
    return _result("local_read_file", {"path": path}, lambda: {"status": "OK", **files_ops.read_file(path, max_bytes=max(1, min(max_bytes, 5_000_000)))})


@mcp.tool(name="local_write_file")
def local_write_file(path: str, content: str, overwrite: bool = True) -> dict:
    return _result("local_write_file", {"path": path, "content": content}, lambda: files_ops.write_file(path, content, overwrite=overwrite))


@mcp.tool(name="local_list_dir")
def local_list_dir(path: str) -> dict:
    return _result("local_list_dir", {"path": path}, lambda: {"status": "OK", **files_ops.list_dir(path)})


@mcp.tool(name="config_status")
def config_status() -> dict:
    def action() -> dict:
        cfg = load_config()
        return {
            "status": "OK",
            "first_run_completed": bool(cfg.get("first_run", {}).get("completed", False)),
            "trusted_roots": [str(x) for x in cfg.get("projects", {}).get("trusted_roots", [])],
            "permission_profile": permission_profile(),
            "computer_control_mode": computer_mode(),
            "browser_enabled": bool(cfg.get("browser", {}).get("enabled", True)),
            "media_enabled": bool(cfg.get("media", {}).get("enabled", True)),
            "obs_enabled": bool(cfg.get("obs", {}).get("enabled", True)),
        }
    return _result("config_status", {}, action)


@mcp.tool(name="first_run_status")
def first_run_status() -> dict:
    return _result("first_run_status", {}, lambda: {"status": "OK", **wizard.first_run_status()})


@mcp.tool(name="first_run_configure")
def first_run_configure(
    trusted_roots: list[str],
    local_profile: str = "DEVELOPER",
    computer_mode: str = "SAFE",
    import_ssh_config: bool = True,
    browser_enabled: bool = True,
    media_enabled: bool = True,
    obs_enabled: bool = True,
) -> dict:
    return _result(
        "first_run_configure",
        {
            "trusted_roots": trusted_roots,
            "local_profile": local_profile,
            "computer_mode": computer_mode,
            "import_ssh_config": import_ssh_config,
            "browser_enabled": browser_enabled,
            "media_enabled": media_enabled,
            "obs_enabled": obs_enabled,
        },
        lambda: wizard.configure_first_run(
            trusted_roots,
            local_profile=local_profile,
            computer_mode=computer_mode,
            import_ssh_config=import_ssh_config,
            browser_enabled=browser_enabled,
            media_enabled=media_enabled,
            obs_enabled=obs_enabled,
        ),
    )


@mcp.tool(name="trusted_roots_list")
def trusted_roots_list() -> dict:
    return _result("trusted_roots_list", {}, lambda: {"status": "OK", "roots": [str(x) for x in trusted_roots()]})


@mcp.tool(name="trusted_root_add")
def trusted_root_add(path: str) -> dict:
    def action() -> dict:
        root = Path(path).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(str(root))
        cfg = load_config()
        rows = [str(Path(x).expanduser().resolve(strict=False)) for x in cfg.setdefault("projects", {}).get("trusted_roots", [])]
        if str(root) not in rows:
            rows.append(str(root))
        cfg["projects"]["trusted_roots"] = rows
        save_config(cfg)
        return {"status": "OK", "root": str(root), "roots": rows}
    return _result("trusted_root_add", {"path": path}, action)


@mcp.tool(name="trusted_root_remove")
def trusted_root_remove(path: str) -> dict:
    def action() -> dict:
        target = str(Path(path).expanduser().resolve(strict=False))
        cfg = load_config()
        rows = [str(Path(x).expanduser().resolve(strict=False)) for x in cfg.setdefault("projects", {}).get("trusted_roots", [])]
        kept = [x for x in rows if x != target]
        cfg["projects"]["trusted_roots"] = kept
        save_config(cfg)
        return {"status": "OK", "removed": len(rows) - len(kept), "roots": kept}
    return _result("trusted_root_remove", {"path": path}, action)


@mcp.tool(name="computer_control_mode_set")
def computer_control_mode_set(mode: str) -> dict:
    def action() -> dict:
        value = mode.upper()
        if value not in {"OFF", "SAFE", "INTERACTIVE", "FULL"}:
            raise ValueError("mode must be OFF, SAFE, INTERACTIVE, or FULL")
        cfg = load_config()
        cfg.setdefault("computer_control", {})["mode"] = value
        save_config(cfg)
        return {"status": "OK", "mode": value}
    return _result("computer_control_mode_set", {"mode": mode}, action)


@mcp.tool(name="service_status")
def service_status(name: str) -> dict:
    return _result("service_status", {"name": name}, lambda: current_backend().service_status(name))


@mcp.tool(name="service_action")
def service_action(name: str, action: str) -> dict:
    return _result("service_action", {"name": name, "action": action}, lambda: current_backend().service_action(name, action), raw=True)


@mcp.tool(name="run_script")
def run_script(path: str, arguments: list[str] | None = None, timeout: int = 300) -> dict:
    def action() -> dict:
        script = assert_trusted_path(path, must_exist=True)
        if not script.is_file():
            raise IsADirectoryError(str(script))
        argv = _script_argv(script, arguments or [])
        if argv is None:
            return {"status": "CAPABILITY_UNAVAILABLE", "reason": "Supported scripts: .ps1, .py, .cmd, .bat, .sh"}
        if not argv:
            return {"status": "CAPABILITY_UNAVAILABLE", "reason": "Interpreter for script was not found"}
        result = run(argv, cwd=script.parent, timeout=timeout)
        result["status"] = "OK" if result.get("exit_code") == 0 else "FAILED"
        return result
    return _result("run_script", {"path": path, "arguments": arguments or []}, action, raw=True)


def _script_argv(script: Path, arguments: list[str]) -> list[str] | None:
    suffix = script.suffix.lower()
    args = [str(x) for x in arguments]
    if suffix == ".ps1":
        exe = shutil.which("pwsh") or shutil.which("powershell")
        return [exe, "-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-File", str(script), *args] if exe else []
    if suffix in {".py", ".pyw"}:
        return [sys.executable, str(script), *args]
    if suffix in {".cmd", ".bat"}:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(script), *args]
    if suffix == ".sh":
        exe = shutil.which("bash")
        return [exe, str(script), *args] if exe else []
    return None


@mcp.tool(name="job_start")
def job_start(argv: list[str], cwd: str | None = None, label: str | None = None) -> dict:
    def action() -> dict:
        root = assert_trusted_path(cwd, must_exist=True) if cwd else None
        if root is not None and not root.is_dir():
            raise NotADirectoryError(str(root))
        return jobs.start(argv, cwd=root, label=label)
    return _result("job_start", {"argv": argv, "cwd": cwd, "label": label}, action, raw=True)


@mcp.tool(name="run_script_async")
def run_script_async(path: str, arguments: list[str] | None = None, label: str | None = None) -> dict:
    def action() -> dict:
        script = assert_trusted_path(path, must_exist=True)
        if not script.is_file():
            raise IsADirectoryError(str(script))
        argv = _script_argv(script, arguments or [])
        if argv is None:
            return {"status": "CAPABILITY_UNAVAILABLE", "reason": "Supported scripts: .ps1, .py, .cmd, .bat, .sh"}
        if not argv:
            return {"status": "CAPABILITY_UNAVAILABLE", "reason": "Interpreter for script was not found"}
        return jobs.start(argv, cwd=script.parent, label=label or script.name)
    return _result("run_script_async", {"path": path, "arguments": arguments or [], "label": label}, action, raw=True)


@mcp.tool(name="job_status")
def job_status(session_id: str) -> dict:
    return _result("job_status", {"session_id": session_id}, lambda: jobs.status(session_id))


@mcp.tool(name="job_output")
def job_output(session_id: str, max_bytes: int = 200_000) -> dict:
    return _result(
        "job_output",
        {"session_id": session_id, "max_bytes": max_bytes},
        lambda: jobs.output(session_id, max_bytes=max_bytes),
    )


@mcp.tool(name="job_cancel")
def job_cancel(session_id: str) -> dict:
    return _result("job_cancel", {"session_id": session_id}, lambda: jobs.cancel(session_id), raw=True)


@mcp.tool(name="job_list")
def job_list(limit: int = 50) -> dict:
    return _result("job_list", {"limit": limit}, lambda: jobs.list_jobs(limit=limit))


@mcp.tool(name="ssh_agent_status")
def ssh_agent_status() -> dict:
    return _result("ssh_agent_status", {}, lambda: {"status": "OK", **current_backend().get_ssh_agent_info()})


@mcp.tool(name="ssh_agent_keys")
def ssh_agent_keys() -> dict:
    def action() -> dict:
        result = run([shutil.which("ssh-add") or "ssh-add", "-l"], timeout=20)
        result["status"] = "OK" if result.get("exit_code") == 0 else "FAILED"
        result["identities"] = [line for line in result.get("stdout", "").splitlines() if line.strip()]
        return result
    return _result("ssh_agent_keys", {}, action)


@mcp.tool(name="ssh_hosts")
def ssh_hosts() -> dict:
    return _result("ssh_hosts", {}, lambda: {"status": "OK", "hosts": ssh_ops.hosts()})


@mcp.tool(name="ssh_host_add")
def ssh_host_add(host_id: str, hostname: str, user: str, port: int = 22, permission_profile: str = "READ_ONLY", trusted: bool = False) -> dict:
    item = {"id": host_id, "name": host_id, "alias": host_id, "hostname": hostname, "user": user, "port": port, "permission_profile": permission_profile, "trusted": trusted}
    return _result("ssh_host_add", {"host_id": host_id, "hostname": hostname, "user": user, "port": port, "permission_profile": permission_profile, "trusted": trusted}, lambda: ssh_ops.add_host(item))


@mcp.tool(name="ssh_host_remove")
def ssh_host_remove(host_id: str) -> dict:
    return _result("ssh_host_remove", {"host_id": host_id}, lambda: ssh_ops.remove_host(host_id))


@mcp.tool(name="ssh_hosts_import")
def ssh_hosts_import(path: str | None = None, persist: bool = False) -> dict:
    return _result("ssh_hosts_import", {"path": path, "persist": persist}, lambda: ssh_ops.import_hosts(path, persist=persist))


@mcp.tool(name="ssh_test")
def ssh_test(host: str, timeout: int = 15) -> dict:
    return _result("ssh_test", {"host": host}, lambda: ssh_ops.test(host, timeout=timeout))


@mcp.tool(name="ssh_exec")
def ssh_exec(host: str, command: str, timeout: int = 120) -> dict:
    return _result("ssh_exec", {"host": host, "command": command}, lambda: ssh_ops.exec_remote(host, command, timeout=timeout), raw=True)


@mcp.tool(name="ssh_upload")
def ssh_upload(host: str, local_path: str, remote_path: str) -> dict:
    return _result("ssh_upload", {"host": host, "path": local_path, "target": remote_path}, lambda: ssh_ops.transfer(host, local_path, remote_path, upload=True))


@mcp.tool(name="ssh_download")
def ssh_download(host: str, remote_path: str, local_path: str) -> dict:
    return _result("ssh_download", {"host": host, "path": local_path, "target": remote_path}, lambda: ssh_ops.transfer(host, local_path, remote_path, upload=False))


@mcp.tool(name="wsl_list")
def wsl_list() -> dict:
    return _result("wsl_list", {}, wsl_ops.list_distros)


@mcp.tool(name="wsl_info")
def wsl_info(distro: str | None = None) -> dict:
    return _result("wsl_info", {"distro": distro}, lambda: wsl_ops.info(distro))


@mcp.tool(name="wsl_exec")
def wsl_exec(distro: str, command: str, timeout: int = 120) -> dict:
    return _result("wsl_exec", {"distro": distro, "command": command}, lambda: wsl_ops.exec_wsl(distro, command, timeout), raw=True)


@mcp.tool(name="wsl_path_translate")
def wsl_path_translate(path: str) -> dict:
    return _result("wsl_path_translate", {"path": path}, lambda: wsl_ops.path_translate(path))


@mcp.tool(name="browser_status")
def browser_status() -> dict:
    return _result("browser_status", {}, lambda: {"status": "OK", **browser_ops.browser.status()})


@mcp.tool(name="browser_start")
def browser_start(headless: bool | None = None, record_video: bool = False) -> dict:
    return _result("browser_start", {"headless": headless, "record_video": record_video}, lambda: browser_ops.browser.start(headless=headless, record_video=record_video))


@mcp.tool(name="browser_stop")
def browser_stop() -> dict:
    return _result("browser_stop", {}, browser_ops.browser.stop)


@mcp.tool(name="browser_tabs")
def browser_tabs() -> dict:
    return _result("browser_tabs", {}, browser_ops.browser.tabs)


@mcp.tool(name="browser_open")
def browser_open(url: str | None = None) -> dict:
    return _result("browser_open", {"url": url}, lambda: browser_ops.browser.open(url))


@mcp.tool(name="browser_close")
def browser_close(index: int | None = None) -> dict:
    return _result("browser_close", {"index": index}, lambda: browser_ops.browser.close(index))


@mcp.tool(name="browser_navigate")
def browser_navigate(url: str, wait_until: str = "domcontentloaded", timeout_ms: int = 30000) -> dict:
    return _result("browser_navigate", {"url": url}, lambda: browser_ops.browser.navigate(url, wait_until=wait_until, timeout_ms=timeout_ms))


@mcp.tool(name="browser_reload")
def browser_reload() -> dict:
    return _result("browser_reload", {}, lambda: browser_ops.browser.simple("reload"))


@mcp.tool(name="browser_back")
def browser_back() -> dict:
    return _result("browser_back", {}, lambda: browser_ops.browser.simple("back"))


@mcp.tool(name="browser_forward")
def browser_forward() -> dict:
    return _result("browser_forward", {}, lambda: browser_ops.browser.simple("forward"))


@mcp.tool(name="browser_wait")
def browser_wait(selector: str | None = None, timeout_ms: int = 1000) -> dict:
    timeout_ms = max(0, min(int(timeout_ms), 60000))
    return _result(
        "browser_wait",
        {"selector": selector, "timeout_ms": timeout_ms},
        lambda: browser_ops.browser.wait(selector, timeout_ms),
    )


@mcp.tool(name="browser_snapshot")
def browser_snapshot() -> dict:
    return _result("browser_snapshot", {}, browser_ops.browser.snapshot)


@mcp.tool(name="browser_get_text")
def browser_get_text(selector: str = "body") -> dict:
    return _result("browser_get_text", {"selector": selector}, lambda: browser_ops.browser.get_text(selector))


@mcp.tool(name="browser_find")
def browser_find(text: str) -> dict:
    return _result("browser_find", {"text": text}, lambda: browser_ops.browser.find(text))


def _browser_interaction(tool: str, args: dict[str, Any], callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    def action() -> dict[str, Any]:
        require_mode("INTERACTIVE", "FULL")
        return callback()
    return _result(tool, args, action)


@mcp.tool(name="browser_click")
def browser_click(selector: str) -> dict:
    return _browser_interaction("browser_click", {"selector": selector}, lambda: browser_ops.browser.click(selector))


@mcp.tool(name="browser_fill")
def browser_fill(selector: str, value: str) -> dict:
    return _browser_interaction("browser_fill", {"selector": selector, "value": value}, lambda: browser_ops.browser.fill(selector, value))


@mcp.tool(name="browser_type")
def browser_type(selector: str, value: str, delay_ms: int = 0) -> dict:
    return _browser_interaction("browser_type", {"selector": selector, "value": value}, lambda: browser_ops.browser.type_text(selector, value, delay_ms))


@mcp.tool(name="browser_press")
def browser_press(key: str, selector: str | None = None) -> dict:
    return _browser_interaction("browser_press", {"key": key, "selector": selector}, lambda: browser_ops.browser.press(key, selector))


@mcp.tool(name="browser_select")
def browser_select(selector: str, value: str) -> dict:
    return _browser_interaction("browser_select", {"selector": selector, "value": value}, lambda: browser_ops.browser.select(selector, value))


@mcp.tool(name="browser_scroll")
def browser_scroll(x: int = 0, y: int = 600) -> dict:
    return _browser_interaction("browser_scroll", {"x": x, "y": y}, lambda: browser_ops.browser.scroll(x, y))


@mcp.tool(name="browser_upload")
def browser_upload(selector: str, path: str) -> dict:
    return _browser_interaction("browser_upload", {"selector": selector, "path": path}, lambda: browser_ops.browser.upload(selector, path))


@mcp.tool(name="browser_downloads")
def browser_downloads() -> dict:
    return _result("browser_downloads", {}, browser_ops.browser.downloads)


@mcp.tool(name="browser_download_click")
def browser_download_click(selector: str, output: str | None = None, timeout_ms: int = 30000) -> dict:
    return _browser_interaction(
        "browser_download_click",
        {"selector": selector, "output": output, "timeout_ms": timeout_ms},
        lambda: browser_ops.browser.download_click(selector, output, timeout_ms),
    )


@mcp.tool(name="browser_screenshot")
def browser_screenshot(output: str | None = None, full_page: bool = False, selector: str | None = None, image_type: str = "png") -> dict:
    return _result("browser_screenshot", {"output": output, "full_page": full_page, "selector": selector, "image_type": image_type}, lambda: browser_ops.browser.screenshot(output, full_page=full_page, selector=selector, image_type=image_type))


@mcp.tool(name="browser_console")
def browser_console() -> dict:
    return _result("browser_console", {}, browser_ops.browser.console)


@mcp.tool(name="browser_network_errors")
def browser_network_errors() -> dict:
    return _result("browser_network_errors", {}, browser_ops.browser.network_errors)


@mcp.tool(name="browser_recording_start")
def browser_recording_start(headless: bool | None = None) -> dict:
    return _result("browser_recording_start", {"headless": headless}, lambda: browser_ops.browser.video_start(headless=headless), raw=True)


@mcp.tool(name="browser_recording_stop")
def browser_recording_stop() -> dict:
    return _result("browser_recording_stop", {}, browser_ops.browser.video_stop, raw=True)


@mcp.tool(name="desktop_info")
def desktop_info() -> dict:
    return _result("desktop_info", {}, lambda: {"status": "OK", **desktop_ops.info()})


@mcp.tool(name="desktop_windows")
def desktop_windows() -> dict:
    return _result("desktop_windows", {}, desktop_ops.list_windows)


@mcp.tool(name="desktop_find_window")
def desktop_find_window(query: str) -> dict:
    return _result("desktop_find_window", {"query": query}, lambda: desktop_ops.find_window(query))


@mcp.tool(name="desktop_focus_window")
def desktop_focus_window(window_id: str) -> dict:
    return _result("desktop_focus_window", {"window_id": window_id}, lambda: desktop_ops.focus_window(window_id))


@mcp.tool(name="desktop_screenshot")
def desktop_screenshot(output: str | None = None) -> dict:
    return _result("desktop_screenshot", {"output": output}, lambda: desktop_ops.screenshot(output))


@mcp.tool(name="desktop_window_action")
def desktop_window_action(action: str, handle: str) -> dict:
    return _result("desktop_window_action", {"action": action, "handle": handle}, lambda: desktop_ops.window_action(action, handle))


@mcp.tool(name="desktop_controls")
def desktop_controls(handle: str) -> dict:
    return _result("desktop_controls", {"handle": handle}, lambda: desktop_ops.list_controls(handle))


@mcp.tool(name="desktop_control_action")
def desktop_control_action(handle: str, query: str, action: str, value: str | None = None) -> dict:
    return _result("desktop_control_action", {"handle": handle, "query": query, "action": action}, lambda: desktop_ops.control_action(handle, query, action, value))


@mcp.tool(name="desktop_press")
def desktop_press(keys: str) -> dict:
    return _result("desktop_press", {"keys": keys}, lambda: desktop_ops.input_action("press", keys=keys))


@mcp.tool(name="desktop_hotkey")
def desktop_hotkey(keys: str) -> dict:
    return _result("desktop_hotkey", {"keys": keys}, lambda: desktop_ops.input_action("hotkey", keys=keys))


@mcp.tool(name="desktop_type")
def desktop_type(text: str) -> dict:
    return _result("desktop_type", {"text": text}, lambda: desktop_ops.input_action("type", text=text))


@mcp.tool(name="desktop_move")
def desktop_move(x: int, y: int) -> dict:
    return _result("desktop_move", {"x": x, "y": y}, lambda: desktop_ops.input_action("move", x=x, y=y))


@mcp.tool(name="desktop_click")
def desktop_click(x: int, y: int, button: str = "left") -> dict:
    return _result("desktop_click", {"x": x, "y": y, "button": button}, lambda: desktop_ops.input_action("click", x=x, y=y, button=button))


@mcp.tool(name="desktop_scroll")
def desktop_scroll(delta: int, x: int = 0, y: int = 0) -> dict:
    return _result("desktop_scroll", {"delta": delta, "x": x, "y": y}, lambda: desktop_ops.input_action("scroll", delta=delta, x=x, y=y))


@mcp.tool(name="video_info")
def video_info(path: str) -> dict:
    return _result("video_info", {"path": path}, lambda: media_ops.video_info(path))


@mcp.tool(name="video_convert")
def video_convert(src: str, output: str) -> dict:
    return _result("video_convert", {"src": src, "output": output}, lambda: media_ops.convert(src, output), raw=True)


@mcp.tool(name="video_convert_async")
def video_convert_async(src: str, output: str) -> dict:
    return _result("video_convert_async", {"src": src, "output": output}, lambda: media_ops.convert_async(src, output), raw=True)


@mcp.tool(name="video_trim")
def video_trim(src: str, output: str, start: float, duration: float) -> dict:
    return _result("video_trim", {"src": src, "output": output, "start": start, "duration": duration}, lambda: media_ops.trim(src, output, start, duration), raw=True)


@mcp.tool(name="video_trim_async")
def video_trim_async(src: str, output: str, start: float, duration: float) -> dict:
    return _result("video_trim_async", {"src": src, "output": output, "start": start, "duration": duration}, lambda: media_ops.trim_async(src, output, start, duration), raw=True)


@mcp.tool(name="video_concat")
def video_concat(paths: list[str], output: str) -> dict:
    return _result("video_concat", {"paths": paths, "output": output}, lambda: media_ops.concat(paths, output), raw=True)


@mcp.tool(name="video_concat_async")
def video_concat_async(paths: list[str], output: str) -> dict:
    return _result("video_concat_async", {"paths": paths, "output": output}, lambda: media_ops.concat_async(paths, output), raw=True)


@mcp.tool(name="video_resize")
def video_resize(src: str, output: str, width: int, height: int) -> dict:
    return _result("video_resize", {"src": src, "output": output, "width": width, "height": height}, lambda: media_ops.resize(src, output, width, height), raw=True)


@mcp.tool(name="video_resize_async")
def video_resize_async(src: str, output: str, width: int, height: int) -> dict:
    return _result("video_resize_async", {"src": src, "output": output, "width": width, "height": height}, lambda: media_ops.resize_async(src, output, width, height), raw=True)


@mcp.tool(name="video_change_fps")
def video_change_fps(src: str, output: str, fps: float) -> dict:
    return _result("video_change_fps", {"src": src, "output": output, "fps": fps}, lambda: media_ops.change_fps(src, output, fps), raw=True)


@mcp.tool(name="video_change_fps_async")
def video_change_fps_async(src: str, output: str, fps: float) -> dict:
    return _result("video_change_fps_async", {"src": src, "output": output, "fps": fps}, lambda: media_ops.change_fps_async(src, output, fps), raw=True)


@mcp.tool(name="video_extract_frame")
def video_extract_frame(src: str, output: str, timestamp: float) -> dict:
    return _result("video_extract_frame", {"src": src, "output": output, "timestamp": timestamp}, lambda: media_ops.extract_frame(src, output, timestamp), raw=True)


@mcp.tool(name="video_extract_frame_async")
def video_extract_frame_async(src: str, output: str, timestamp: float) -> dict:
    return _result("video_extract_frame_async", {"src": src, "output": output, "timestamp": timestamp}, lambda: media_ops.extract_frame_async(src, output, timestamp), raw=True)


@mcp.tool(name="video_sample_frames")
def video_sample_frames(video: str, count: int = 6) -> dict:
    return _result("video_sample_frames", {"video": video, "count": count}, lambda: media_ops.sample_frames(video, count), raw=True)


@mcp.tool(name="video_contact_sheet")
def video_contact_sheet(video: str, output: str, count: int = 6, columns: int = 3, width: int = 320) -> dict:
    return _result("video_contact_sheet", {"video": video, "output": output, "count": count, "columns": columns, "width": width}, lambda: media_ops.contact_sheet(video, output, count, columns, width), raw=True)


@mcp.tool(name="video_remove_audio")
def video_remove_audio(src: str, output: str) -> dict:
    return _result("video_remove_audio", {"src": src, "output": output}, lambda: media_ops.remove_audio(src, output), raw=True)


@mcp.tool(name="video_remove_audio_async")
def video_remove_audio_async(src: str, output: str) -> dict:
    return _result("video_remove_audio_async", {"src": src, "output": output}, lambda: media_ops.remove_audio_async(src, output), raw=True)


@mcp.tool(name="video_extract_audio")
def video_extract_audio(src: str, output: str) -> dict:
    return _result("video_extract_audio", {"src": src, "output": output}, lambda: media_ops.extract_audio(src, output), raw=True)


@mcp.tool(name="video_extract_audio_async")
def video_extract_audio_async(src: str, output: str) -> dict:
    return _result("video_extract_audio_async", {"src": src, "output": output}, lambda: media_ops.extract_audio_async(src, output), raw=True)


@mcp.tool(name="video_add_audio")
def video_add_audio(video: str, audio: str, output: str) -> dict:
    return _result("video_add_audio", {"video": video, "audio": audio, "output": output}, lambda: media_ops.add_audio(video, audio, output), raw=True)


@mcp.tool(name="video_add_audio_async")
def video_add_audio_async(video: str, audio: str, output: str) -> dict:
    return _result("video_add_audio_async", {"video": video, "audio": audio, "output": output}, lambda: media_ops.add_audio_async(video, audio, output), raw=True)


@mcp.tool(name="video_add_subtitles")
def video_add_subtitles(video: str, subtitles: str, output: str) -> dict:
    return _result("video_add_subtitles", {"video": video, "subtitles": subtitles, "output": output}, lambda: media_ops.add_subtitles(video, subtitles, output), raw=True)


@mcp.tool(name="video_add_subtitles_async")
def video_add_subtitles_async(video: str, subtitles: str, output: str) -> dict:
    return _result("video_add_subtitles_async", {"video": video, "subtitles": subtitles, "output": output}, lambda: media_ops.add_subtitles_async(video, subtitles, output), raw=True)


@mcp.tool(name="video_add_text")
def video_add_text(video: str, output: str, text: str, x: str = "(w-text_w)/2", y: str = "h-text_h-40") -> dict:
    return _result("video_add_text", {"video": video, "output": output, "text": text}, lambda: media_ops.add_text(video, output, text, x, y), raw=True)


@mcp.tool(name="video_overlay_image")
def video_overlay_image(video: str, image: str, output: str, x: int = 0, y: int = 0) -> dict:
    return _result("video_overlay_image", {"video": video, "image": image, "output": output, "x": x, "y": y}, lambda: media_ops.overlay_image(video, image, output, x, y), raw=True)


@mcp.tool(name="video_to_gif")
def video_to_gif(src: str, output: str, fps: float = 12, width: int = 720) -> dict:
    return _result("video_to_gif", {"src": src, "output": output, "fps": fps, "width": width}, lambda: media_ops.to_gif(src, output, fps, width), raw=True)


@mcp.tool(name="video_to_gif_async")
def video_to_gif_async(src: str, output: str, fps: float = 12, width: int = 720) -> dict:
    return _result("video_to_gif_async", {"src": src, "output": output, "fps": fps, "width": width}, lambda: media_ops.to_gif_async(src, output, fps, width), raw=True)


@mcp.tool(name="gif_to_video")
def gif_to_video(src: str, output: str) -> dict:
    return _result("gif_to_video", {"src": src, "output": output}, lambda: media_ops.gif_to_video(src, output), raw=True)


@mcp.tool(name="gif_to_video_async")
def gif_to_video_async(src: str, output: str) -> dict:
    return _result("gif_to_video_async", {"src": src, "output": output}, lambda: media_ops.gif_to_video_async(src, output), raw=True)


@mcp.tool(name="screen_recording_status")
def screen_recording_status() -> dict:
    return _result("screen_recording_status", {}, lambda: {"status": "OK", **media_ops.recorder.status()})


@mcp.tool(name="screen_recording_start")
def screen_recording_start(kind: str = "screen", fps: int = 15, output: str | None = None, monitor: int = 0, window: str | None = None, region: str | None = None, audio: bool = False) -> dict:
    return _result("screen_recording_start", {"kind": kind, "fps": fps, "output": output, "monitor": monitor, "window": window, "region": region, "audio": audio}, lambda: media_ops.recorder.start(kind, fps=fps, output=output, monitor=monitor, window=window, region=region, audio=audio), raw=True)


@mcp.tool(name="screen_recording_stop")
def screen_recording_stop() -> dict:
    return _result("screen_recording_stop", {}, media_ops.recorder.stop, raw=True)


@mcp.tool(name="animation_backends")
def animation_backends() -> dict:
    return _result("animation_backends", {}, lambda: {"status": "OK", "backends": animation_ops.backends()})


@mcp.tool(name="animation_project_create")
def animation_project_create(path: str, backend: str) -> dict:
    return _result(
        "animation_project_create",
        {"path": path, "backend": backend},
        lambda: animation_ops.project_create(path, backend),
        raw=True,
    )


@mcp.tool(name="animation_render")
def animation_render(path: str, backend: str, output: str | None = None, frame: int | None = None) -> dict:
    return _result(
        "animation_render",
        {"path": path, "backend": backend, "output": output, "frame": frame},
        lambda: animation_ops.render(path, backend, output=output, frame=frame),
        raw=True,
    )


@mcp.tool(name="animation_render_async")
def animation_render_async(path: str, backend: str, output: str | None = None, frame: int | None = None) -> dict:
    return _result(
        "animation_render_async",
        {"path": path, "backend": backend, "output": output, "frame": frame},
        lambda: animation_ops.render_async(path, backend, output=output, frame=frame),
        raw=True,
    )


@mcp.tool(name="obs_status")
def obs_status() -> dict:
    return _result("obs_status", {}, lambda: obs_ops.call("status"))


@mcp.tool(name="obs_scenes")
def obs_scenes() -> dict:
    return _result("obs_scenes", {}, lambda: obs_ops.call("scenes"))


@mcp.tool(name="obs_current_scene")
def obs_current_scene() -> dict:
    return _result("obs_current_scene", {}, lambda: obs_ops.call("current_scene"))


@mcp.tool(name="obs_set_scene")
def obs_set_scene(scene: str) -> dict:
    def action() -> dict:
        require_mode("INTERACTIVE", "FULL")
        return obs_ops.call("set_scene", scene=scene)
    return _result("obs_set_scene", {"scene": scene}, action)


@mcp.tool(name="obs_sources")
def obs_sources(scene: str | None = None) -> dict:
    return _result("obs_sources", {"scene": scene}, lambda: obs_ops.call("sources", scene=scene))


@mcp.tool(name="obs_source_visibility")
def obs_source_visibility(source: str, visible: bool, scene: str | None = None) -> dict:
    def action() -> dict:
        require_mode("INTERACTIVE", "FULL")
        return obs_ops.call("source_show" if visible else "source_hide", source=source, scene=scene)
    return _result("obs_source_visibility", {"source": source, "visible": visible, "scene": scene}, action)


@mcp.tool(name="obs_recording_status")
def obs_recording_status() -> dict:
    return _result("obs_recording_status", {}, lambda: obs_ops.call("recording_status"))


@mcp.tool(name="obs_recording_start")
def obs_recording_start() -> dict:
    return _result("obs_recording_start", {}, lambda: obs_ops.call("start_recording"), raw=True)


@mcp.tool(name="obs_recording_stop")
def obs_recording_stop() -> dict:
    return _result("obs_recording_stop", {}, lambda: obs_ops.call("stop_recording"), raw=True)


@mcp.tool(name="obs_recording_pause")
def obs_recording_pause() -> dict:
    return _result("obs_recording_pause", {}, lambda: obs_ops.call("pause_recording"), raw=True)


@mcp.tool(name="obs_recording_resume")
def obs_recording_resume() -> dict:
    return _result("obs_recording_resume", {}, lambda: obs_ops.call("resume_recording"), raw=True)


@mcp.tool(name="obs_streaming_status")
def obs_streaming_status() -> dict:
    return _result("obs_streaming_status", {}, lambda: obs_ops.call("streaming_status"))


@mcp.tool(name="obs_streaming_start")
def obs_streaming_start(confirm_public_streaming: bool = False) -> dict:
    def action() -> dict:
        if not confirm_public_streaming:
            return {
                "status": "PERMISSION_DENIED",
                "reason": "Starting a public stream requires confirm_public_streaming=true for this call.",
            }
        return obs_ops.call("start_streaming")
    return _result("obs_streaming_start", {"confirm_public_streaming": confirm_public_streaming}, action, raw=True)


@mcp.tool(name="obs_streaming_stop")
def obs_streaming_stop() -> dict:
    return _result("obs_streaming_stop", {}, lambda: obs_ops.call("stop_streaming"), raw=True)


@mcp.tool(name="obs_stats")
def obs_stats() -> dict:
    return _result("obs_stats", {}, lambda: obs_ops.call("stats"))


@mcp.tool(name="obs_secret_info")
def obs_secret_info() -> dict:
    return _result("obs_secret_info", {}, lambda: {"status": "OK", **current_secret_store().info()})


@mcp.tool(name="obs_password_set")
def obs_password_set(password: str) -> dict:
    def action() -> dict:
        if not password:
            raise ValueError("password must not be empty")
        current_secret_store().set("obs-websocket-password", password)
        return {"status": "OK", "stored": True}
    return _result("obs_password_set", {"password": password}, action)


@mcp.tool(name="obs_password_delete")
def obs_password_delete() -> dict:
    def action() -> dict:
        current_secret_store().delete("obs-websocket-password")
        return {"status": "OK", "deleted": True}
    return _result("obs_password_delete", {}, action)


@mcp.tool(name="docker_info")
def docker_info() -> dict:
    return _result("docker_info", {}, lambda: docker_ops.docker(["info", "--format", "{{json .}}"], timeout=30))


@mcp.tool(name="docker_ps")
def docker_ps(all_containers: bool = True) -> dict:
    args = ["ps", "--no-trunc"] + (["-a"] if all_containers else [])
    return _result("docker_ps", {}, lambda: docker_ops.docker(args, timeout=30))


@mcp.tool(name="docker_logs")
def docker_logs(container: str, tail: int = 200) -> dict:
    return _result("docker_logs", {"container": container}, lambda: docker_ops.docker(["logs", "--tail", str(max(1, min(tail, 5000))), container], timeout=60))


def _compose(path: str, command: list[str]) -> dict:
    root = assert_trusted_path(path, must_exist=True)
    return docker_ops.docker(["compose", *command], path=str(root), timeout=600)


@mcp.tool(name="docker_compose_up")
def docker_compose_up(path: str, detach: bool = True) -> dict:
    return _result("docker_compose_up", {"path": path}, lambda: _compose(path, ["up", "-d"] if detach else ["up"]), raw=True)


@mcp.tool(name="docker_compose_down")
def docker_compose_down(path: str) -> dict:
    return _result("docker_compose_down", {"path": path}, lambda: _compose(path, ["down"]), raw=True)


@mcp.tool(name="docker_compose_restart")
def docker_compose_restart(path: str) -> dict:
    return _result("docker_compose_restart", {"path": path}, lambda: _compose(path, ["restart"]), raw=True)


@mcp.tool(name="git_status")
def git_status(path: str) -> dict:
    return _result("git_status", {"path": path}, lambda: git_ops.git(path, ["status", "--short", "--branch"]))


@mcp.tool(name="git_diff")
def git_diff(path: str, staged: bool = False) -> dict:
    args = ["diff", "--no-ext-diff"] + (["--staged"] if staged else [])
    return _result("git_diff", {"path": path}, lambda: git_ops.git(path, args))


@mcp.tool(name="git_log")
def git_log(path: str, limit: int = 20) -> dict:
    return _result("git_log", {"path": path, "limit": limit}, lambda: git_ops.log(path, limit))


@mcp.tool(name="git_branch_list")
def git_branch_list(path: str) -> dict:
    return _result("git_branch_list", {"path": path}, lambda: git_ops.branches(path))


@mcp.tool(name="git_remote_list")
def git_remote_list(path: str) -> dict:
    return _result("git_remote_list", {"path": path}, lambda: git_ops.remotes(path))


@mcp.tool(name="github_auth_status")
def github_auth_status() -> dict:
    return _result("github_auth_status", {}, git_ops.github_auth_status)


@mcp.tool(name="github_repo_view")
def github_repo_view(path: str) -> dict:
    return _result("github_repo_view", {"path": path}, lambda: git_ops.github_repo_view(path))


@mcp.tool(name="github_workflow_list")
def github_workflow_list(path: str, limit: int = 50) -> dict:
    return _result(
        "github_workflow_list",
        {"path": path, "limit": limit},
        lambda: git_ops.github_workflow_list(path, limit),
    )


@mcp.tool(name="github_workflow_runs")
def github_workflow_runs(path: str, limit: int = 20) -> dict:
    return _result(
        "github_workflow_runs",
        {"path": path, "limit": limit},
        lambda: git_ops.github_run_list(path, limit),
    )


@mcp.tool(name="github_workflow_run_view")
def github_workflow_run_view(path: str, run_id: int) -> dict:
    return _result(
        "github_workflow_run_view",
        {"path": path, "run_id": run_id},
        lambda: git_ops.github_run_view(path, run_id),
    )


@mcp.tool(name="github_release_list")
def github_release_list(path: str, limit: int = 30) -> dict:
    return _result(
        "github_release_list",
        {"path": path, "limit": limit},
        lambda: git_ops.github_release_list(path, limit),
    )


@mcp.tool(name="github_release_view")
def github_release_view(path: str, tag: str) -> dict:
    return _result(
        "github_release_view",
        {"path": path, "tag": tag},
        lambda: git_ops.github_release_view(path, tag),
    )


@mcp.tool(name="github_pr_list")
def github_pr_list(path: str, limit: int = 30, state: str = "open") -> dict:
    return _result(
        "github_pr_list",
        {"path": path, "limit": limit, "state": state},
        lambda: git_ops.github_pr_list(path, limit, state),
    )


@mcp.tool(name="github_pr_view")
def github_pr_view(path: str, number: int) -> dict:
    return _result(
        "github_pr_view",
        {"path": path, "number": number},
        lambda: git_ops.github_pr_view(path, number),
    )


@mcp.tool(name="health_check")
def health_check() -> dict:
    return _result("health_check", {}, lambda: {"status": "OK", **run_diagnostics()})


def main() -> None:
    ensure_config()
    # FastMCP itself owns stdout. Do not configure logging to stdout here.
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
