from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .animation_ops import backends as animation_backends
from .browser_ops import browser
from .config import config_path, install_root, load_config
from .docker_ops import health as docker_health
from .platforms import current_backend
from .ssh_ops import hosts


def _exe(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    return {"status": "PASS" if path else "WARNING", "path": path}


def run_diagnostics() -> dict[str, Any]:
    backend = current_backend()
    cfg = load_config()
    checks: dict[str, Any] = {
        "platform": {"status": "PASS", **backend.get_system_info()},
        "config": {"status": "PASS" if config_path().exists() else "WARNING", "path": str(config_path())},
        "python": {"status": "PASS", "version": platform.python_version(), "executable": os.sys.executable},
        "venv": {"status": "PASS" if os.sys.prefix != getattr(os.sys, "base_prefix", os.sys.prefix) else "WARNING", "prefix": os.sys.prefix},
        "shell": {"status": "PASS", **backend.get_shell()},
        "git": _exe("git"), "github": _exe("gh"), "docker_cli": _exe("docker"), "ssh": _exe("ssh"),
        "ssh_add": _exe("ssh-add"), "scp": _exe("scp"), "sftp": _exe("sftp"),
        "ffmpeg": _exe("ffmpeg"), "ffprobe": _exe("ffprobe"), "node": _exe("node"), "npm": _exe("npm"),
        "browser": {"status": "PASS" if browser.status()["package"] else "FAIL", **browser.status()},
        "desktop": {"status": "PASS" if backend.get_desktop_info().get("available") else "NOT APPLICABLE", **backend.get_desktop_info()},
        "ssh_agent": {"status": "PASS", **backend.get_ssh_agent_info()},
        "animation": {"status": "PASS", **animation_backends()},
        "trusted_roots": {"status": "PASS" if cfg.get("projects", {}).get("trusted_roots") else "WARNING", "roots": cfg.get("projects", {}).get("trusted_roots", [])},
        "remote_hosts": {"status": "PASS", "count": len(hosts())},
        "logs": {"status": "PASS", "path": str(install_root() / "logs")},
    }
    try:
        dh = docker_health()
        checks["docker"] = {"status": "PASS" if dh.get("exit_code") == 0 else "WARNING", "detail": dh}
    except Exception as exc:
        checks["docker"] = {"status": "WARNING", "reason": str(exc)}
    system = platform.system()
    if system == "Windows":
        checks["wsl"] = _exe("wsl")
        checks["ui_automation"] = {"status": "PASS" if importlib.util.find_spec("pywinauto") else "WARNING"}
    elif system == "Linux":
        info = backend.get_desktop_info()
        checks["systemd"] = {"status": "PASS" if Path("/run/systemd/system").exists() else "NOT APPLICABLE"}
        for key in ["portal", "pipewire"]:
            checks[key] = {"status": "PASS" if info.get(key) else "WARNING" if info.get("available") else "NOT APPLICABLE"}
    elif system == "Darwin":
        for name in ["accessibility", "screen_recording", "automation"]:
            checks[name] = {"status": "WARNING", "reason": "macOS TCC permission must be verified interactively on the target Mac"}
    summary = {name: 0 for name in ["PASS", "WARNING", "FAIL", "NOT APPLICABLE"]}
    for value in checks.values():
        status = value.get("status", "WARNING")
        summary[status] = summary.get(status, 0) + 1
    return {"checks": checks, "summary": summary}
