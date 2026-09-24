from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import tomlkit

from .backup_ops import BackupRecord, restore_backup, transactional_write_bytes
from .config import install_root
from .setup_state import stable_runtime_python


class CodexConfigError(RuntimeError):
    """Raised when Codex configuration cannot be parsed or safely managed."""


@dataclass(frozen=True, slots=True)
class CodexConfigResult:
    path: str
    changed: bool
    runtime_python: str
    backup: BackupRecord | None = None


def codex_config_path(user_home: Path | None = None) -> Path:
    home = Path(user_home) if user_home is not None else Path.home()
    return home / ".codex" / "config.toml"


def codex_config_backup_root(root: Path | None = None) -> Path:
    return Path(root) if root is not None else install_root() / "setup" / "backups" / "codex-config"


def _read_text(path: Path) -> str:
    try:
        if not path.exists():
            return ""
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeError) as exc:
        raise CodexConfigError(f"Cannot read Codex config {path}: {exc}") from exc


def _parse(text: str, path: Path):
    try:
        return tomlkit.parse(text)
    except Exception as exc:
        raise CodexConfigError(f"Invalid TOML in {path}: {exc}") from exc


def _desired_runtime_python(user_home: Path | None, runtime_python: Path | None) -> Path:
    if runtime_python is not None:
        return Path(runtime_python)
    platform_name = "windows" if os.name == "nt" else os.name
    return stable_runtime_python(user_home, platform_name=platform_name)


def _registration_matches(document, runtime_python: Path) -> bool:
    mcp_servers = document.get("mcp_servers")
    if mcp_servers is None:
        return False
    if not hasattr(mcp_servers, "get"):
        return False
    entry = mcp_servers.get("codexLocalOps")
    if entry is None:
        return False
    try:
        command = str(entry.get("command"))
        args = list(entry.get("args") or [])
    except (AttributeError, TypeError):
        return False
    return command == str(runtime_python) and args == ["-m", "codex_local_ops.server"]


def _set_registration(document, runtime_python: Path) -> None:
    mcp_servers = document.get("mcp_servers")
    if mcp_servers is None:
        mcp_servers = tomlkit.table()
        document["mcp_servers"] = mcp_servers
    elif not hasattr(mcp_servers, "get") or not hasattr(mcp_servers, "__setitem__"):
        raise CodexConfigError("mcp_servers must be a TOML table")

    entry = tomlkit.table()
    entry.add("command", str(runtime_python))
    entry.add("args", ["-m", "codex_local_ops.server"])
    mcp_servers["codexLocalOps"] = entry


def _validate_registration_file(path: Path, runtime_python: Path) -> None:
    document = _parse(_read_text(path), path)
    if not _registration_matches(document, runtime_python):
        raise CodexConfigError("codexLocalOps MCP registration validation failed")


def update_codex_mcp(
    *,
    path: Path | None = None,
    user_home: Path | None = None,
    runtime_python: Path | None = None,
    backup_root: Path | None = None,
    transaction_id: str | None = None,
) -> CodexConfigResult:
    target = Path(path) if path is not None else codex_config_path(user_home)
    runtime = _desired_runtime_python(user_home, runtime_python)
    document = _parse(_read_text(target), target)

    if _registration_matches(document, runtime):
        return CodexConfigResult(path=str(target), changed=False, runtime_python=str(runtime))

    _set_registration(document, runtime)
    rendered = tomlkit.dumps(document).encode("utf-8")
    transaction = transaction_id or f"codex-config-{uuid.uuid4().hex}"
    backups = Path(backup_root) if backup_root is not None else codex_config_backup_root()
    backup = transactional_write_bytes(
        target,
        rendered,
        backup_root=backups,
        transaction_id=transaction,
        kind="codex-config",
        validator=lambda candidate: _validate_registration_file(candidate, runtime),
    )
    return CodexConfigResult(
        path=str(target),
        changed=True,
        runtime_python=str(runtime),
        backup=backup,
    )


def restore_codex_config(backup: BackupRecord) -> Path:
    if backup.kind != "codex-config":
        raise CodexConfigError(f"Backup kind is not codex-config: {backup.kind}")
    return restore_backup(backup)
