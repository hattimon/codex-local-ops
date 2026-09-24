from __future__ import annotations

import copy
import os
import shutil
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "first_run": {"completed": False},
    "projects": {"trusted_roots": []},
    "permissions": {"local_profile": "DEVELOPER", "expert_mode": False},
    "computer_control": {"mode": "SAFE"},
    "ssh": {"use_agent": True, "import_ssh_config": True, "hosts": []},
    "docker": {"enabled": True},
    "browser": {"enabled": True, "headless": False, "channel": "chromium"},
    "media": {"enabled": True},
    "obs": {"enabled": True, "host": "127.0.0.1", "port": 4455, "allow_public_streaming": False},
    "agents": {
        "free_only": True,
        "endpoint": "http://127.0.0.1:11434/v1",
        "privacy": {"allow_external_public": True, "allow_external_private": False},
        "hermes": {"executable": None, "local_timeout": 600, "external_timeout": 120},
    },
    "privacy": {
        "excluded_apps": [],
        "excluded_window_titles": [],
        "excluded_directories": [],
        "excluded_domains": [],
    },
    "logging": {"level": "INFO"},
}


def install_root() -> Path:
    return Path(os.environ.get("CODEX_LOCAL_OPS_HOME", Path.home() / ".codex-local-ops")).expanduser()


def config_path() -> Path:
    return install_root() / "config" / "config.yaml"


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or config_path()
    if not path.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("config.yaml must contain a mapping")
    cfg = _merge(DEFAULT_CONFIG, data)
    if int(cfg.get("version", 0)) != 1:
        raise ValueError(f"Unsupported config version: {cfg.get('version')}")
    return cfg


def save_config(cfg: dict[str, Any], path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    tmp.replace(path)
    return path


def ensure_config() -> Path:
    path = config_path()
    legacy_path = install_root() / "config.yaml"
    if not path.exists() and legacy_path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_path, path)
    if not path.exists():
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        save_config(cfg, path)
    return path


def update_config(mutator) -> dict[str, Any]:
    """Atomically load, mutate and save the user configuration."""
    cfg = load_config()
    mutator(cfg)
    save_config(cfg)
    return cfg
