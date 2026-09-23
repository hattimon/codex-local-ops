from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import load_config, save_config


VALID_LOCAL_PROFILES = {"SAFE", "DEVELOPER", "FULL"}
VALID_COMPUTER_MODES = {"OFF", "SAFE", "INTERACTIVE", "FULL"}


def first_run_status() -> dict[str, Any]:
    cfg = load_config()
    return {
        "completed": bool(cfg.get("first_run", {}).get("completed", False)),
        "trusted_roots": list(cfg.get("projects", {}).get("trusted_roots", [])),
        "local_profile": str(cfg.get("permissions", {}).get("local_profile", "SAFE")).upper(),
        "computer_mode": str(cfg.get("computer_control", {}).get("mode", "SAFE")).upper(),
        "import_ssh_config": bool(cfg.get("ssh", {}).get("import_ssh_config", True)),
    }


def configure_first_run(
    trusted_roots: list[str],
    *,
    local_profile: str = "DEVELOPER",
    computer_mode: str = "SAFE",
    import_ssh_config: bool = True,
    browser_enabled: bool = True,
    media_enabled: bool = True,
    obs_enabled: bool = True,
) -> dict[str, Any]:
    profile = local_profile.upper()
    mode = computer_mode.upper()
    if profile not in VALID_LOCAL_PROFILES:
        raise ValueError("local_profile must be SAFE, DEVELOPER, or FULL")
    if mode not in VALID_COMPUTER_MODES:
        raise ValueError("computer_mode must be OFF, SAFE, INTERACTIVE, or FULL")

    roots: list[str] = []
    for raw in trusted_roots:
        path = Path(raw).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise NotADirectoryError(str(path))
        value = str(path)
        if value not in roots:
            roots.append(value)
    if not roots:
        raise ValueError("At least one trusted project root is required")

    cfg = load_config()
    cfg.setdefault("projects", {})["trusted_roots"] = roots
    cfg.setdefault("permissions", {})["local_profile"] = profile
    cfg.setdefault("computer_control", {})["mode"] = mode
    cfg.setdefault("ssh", {})["import_ssh_config"] = bool(import_ssh_config)
    cfg.setdefault("browser", {})["enabled"] = bool(browser_enabled)
    cfg.setdefault("media", {})["enabled"] = bool(media_enabled)
    cfg.setdefault("obs", {})["enabled"] = bool(obs_enabled)
    cfg.setdefault("first_run", {})["completed"] = True
    path = save_config(cfg)
    return {"status": "OK", "config": str(path), **first_run_status()}
