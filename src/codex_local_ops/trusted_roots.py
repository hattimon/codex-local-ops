from __future__ import annotations

import copy
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

import yaml

from .backup_ops import BackupRecord, restore_backup, transactional_write_bytes
from .config import DEFAULT_CONFIG, install_root
from .config import config_path as local_config_path
from .safety import sanitize


class TrustedRootError(RuntimeError):
    """Raised when a trusted-root change would be unsafe or ambiguous."""


@dataclass(frozen=True, slots=True)
class TrustedRootsInspection:
    path: str
    exists: bool
    valid: bool
    roots: tuple[str, ...]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return sanitize(asdict(self))


@dataclass(frozen=True, slots=True)
class TrustedRootResult:
    path: str
    root: str
    changed: bool
    roots: tuple[str, ...]
    backup: BackupRecord | None = None

    def to_dict(self) -> dict[str, Any]:
        return sanitize(
            {
                "path": self.path,
                "root": self.root,
                "changed": self.changed,
                "roots": list(self.roots),
                "backup": self.backup.to_dict() if self.backup is not None else None,
            }
        )


def trusted_roots_backup_root(root: Path | None = None) -> Path:
    return Path(root) if root is not None else install_root() / "setup" / "backups" / "local-config"


def is_broad_trusted_root(path: str | Path) -> bool:
    raw = str(path)
    windows_path = PureWindowsPath(raw)
    if windows_path.drive and not windows_path.root and len(windows_path.parts) == 1:
        return True
    if windows_path.drive and windows_path.root and windows_path.parent == windows_path:
        return True
    candidate = Path(path).expanduser()
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        resolved = candidate.absolute()
    return resolved.parent == resolved


def normalize_trusted_root(
    path: str | Path,
    *,
    require_exists: bool = True,
    allow_broad_root: bool = False,
) -> Path:
    if not str(path).strip():
        raise TrustedRootError("Trusted root must be explicitly selected")
    if not allow_broad_root and is_broad_trusted_root(path):
        raise TrustedRootError("Whole-drive or filesystem roots are rejected by default")
    candidate = Path(path).expanduser()
    if require_exists and not candidate.exists():
        raise FileNotFoundError(str(candidate))
    try:
        resolved = candidate.resolve(strict=require_exists)
    except OSError as exc:
        raise TrustedRootError(f"Cannot resolve trusted root {candidate}: {exc}") from exc
    if require_exists and not resolved.is_dir():
        raise NotADirectoryError(str(resolved))
    if not allow_broad_root and is_broad_trusted_root(resolved):
        raise TrustedRootError("Whole-drive or filesystem roots are rejected by default")
    return resolved


def _read_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise TrustedRootError(f"Cannot read Local Ops config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise TrustedRootError("Local Ops config must contain a mapping")
    return data


def inspect_trusted_roots(*, config_file: Path | None = None) -> TrustedRootsInspection:
    target = Path(config_file) if config_file is not None else local_config_path()
    try:
        data = _read_document(target)
        projects = data.get("projects") or {}
        if not isinstance(projects, dict):
            raise TrustedRootError("projects must be a mapping")
        roots = projects.get("trusted_roots") or []
        if not isinstance(roots, list) or any(not isinstance(item, str) for item in roots):
            raise TrustedRootError("projects.trusted_roots must be a list of paths")
    except TrustedRootError as exc:
        return TrustedRootsInspection(str(target), target.exists(), False, (), str(exc))
    return TrustedRootsInspection(str(target), target.exists(), True, tuple(roots))


def _canonical_key(value: str | Path) -> str:
    try:
        resolved = Path(value).expanduser().resolve(strict=False)
    except OSError:
        resolved = Path(value).expanduser().absolute()
    return os.path.normcase(str(resolved)).rstrip("\\/")


def _validate_written_config(path: Path, expected_root: str) -> None:
    inspection = inspect_trusted_roots(config_file=path)
    if not inspection.valid:
        raise TrustedRootError(inspection.error or "Trusted-root configuration validation failed")
    expected_key = _canonical_key(expected_root)
    if not any(_canonical_key(item) == expected_key for item in inspection.roots):
        raise TrustedRootError("Trusted root is missing after write")


def add_trusted_root(
    path: str | Path,
    *,
    config_file: Path | None = None,
    backup_root: Path | None = None,
    transaction_id: str | None = None,
    allow_broad_root: bool = False,
) -> TrustedRootResult:
    selected = normalize_trusted_root(path, allow_broad_root=allow_broad_root)
    target = Path(config_file) if config_file is not None else local_config_path()
    data = _read_document(target)
    projects = data.setdefault("projects", {})
    if not isinstance(projects, dict):
        raise TrustedRootError("projects must be a mapping")
    roots = projects.setdefault("trusted_roots", [])
    if not isinstance(roots, list) or any(not isinstance(item, str) for item in roots):
        raise TrustedRootError("projects.trusted_roots must be a list of paths")

    selected_value = str(selected)
    selected_key = _canonical_key(selected_value)
    if any(_canonical_key(item) == selected_key for item in roots):
        return TrustedRootResult(str(target), selected_value, False, tuple(roots))

    roots.append(selected_value)
    payload = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8")
    tx = transaction_id or f"trusted-root-{uuid.uuid4().hex}"
    backups = Path(backup_root) if backup_root is not None else trusted_roots_backup_root()
    backup = transactional_write_bytes(
        target,
        payload,
        backup_root=backups,
        transaction_id=tx,
        kind="trusted-roots",
        validator=lambda candidate: _validate_written_config(candidate, selected_value),
    )
    return TrustedRootResult(str(target), selected_value, True, tuple(roots), backup)


def restore_trusted_roots(backup: BackupRecord) -> Path:
    if backup.kind != "trusted-roots":
        raise TrustedRootError(f"Backup kind is not trusted-roots: {backup.kind}")
    return restore_backup(backup)
