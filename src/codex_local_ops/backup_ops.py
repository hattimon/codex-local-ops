from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from .setup_state import utc_timestamp


class BackupError(RuntimeError):
    """Raised when backup metadata or backup content cannot be trusted."""


class TransactionWriteError(RuntimeError):
    """Raised when a validated transactional write fails and has been rolled back."""


@dataclass(frozen=True, slots=True)
class BackupRecord:
    backup_id: str
    transaction_id: str
    kind: str
    target_path: str
    backup_path: str | None
    existed: bool
    sha256: str | None
    size: int
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_id(target: Path, transaction_id: str, original_hash: str | None) -> str:
    raw = f"{transaction_id}|{target.absolute()}|{original_hash or 'MISSING'}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def create_backup(
    target: Path,
    backup_root: Path,
    *,
    transaction_id: str,
    kind: str,
) -> BackupRecord:
    target = Path(target)
    backup_root = Path(backup_root)
    if target.exists() and not target.is_file():
        raise BackupError(f"Backup target is not a regular file: {target}")

    existed = target.exists()
    data = target.read_bytes() if existed else b""
    original_hash = _sha256_bytes(data) if existed else None
    backup_id = _backup_id(target, transaction_id, original_hash)
    backup_path = backup_root / f"{backup_id}.bak" if existed else None

    if backup_path is not None:
        backup_root.mkdir(parents=True, exist_ok=True)
        if backup_path.exists():
            if file_sha256(backup_path) != original_hash:
                raise BackupError(f"Existing backup content mismatch: {backup_path}")
        else:
            shutil.copy2(target, backup_path)
            if file_sha256(backup_path) != original_hash:
                backup_path.unlink(missing_ok=True)
                raise BackupError(f"Backup verification failed: {backup_path}")

    return BackupRecord(
        backup_id=backup_id,
        transaction_id=transaction_id,
        kind=kind,
        target_path=str(target.absolute()),
        backup_path=str(backup_path.absolute()) if backup_path is not None else None,
        existed=existed,
        sha256=original_hash,
        size=len(data),
        created_at=utc_timestamp(),
    )


def restore_backup(record: BackupRecord) -> Path:
    target = Path(record.target_path)
    if not record.existed:
        if target.exists():
            if not target.is_file():
                raise BackupError(f"Refusing to remove non-file target during restore: {target}")
            target.unlink()
        return target

    if not record.backup_path or not record.sha256:
        raise BackupError("Backup metadata is incomplete")
    source = Path(record.backup_path)
    if not source.is_file():
        raise BackupError(f"Backup file is missing: {source}")
    if file_sha256(source) != record.sha256:
        raise BackupError(f"Backup hash mismatch: {source}")

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.restore.tmp")
    try:
        shutil.copy2(source, tmp)
        if file_sha256(tmp) != record.sha256:
            raise BackupError(f"Restore staging hash mismatch: {tmp}")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def transactional_write_bytes(
    target: Path,
    data: bytes,
    *,
    backup_root: Path,
    transaction_id: str,
    kind: str,
    validator: Callable[[Path], None] | None = None,
) -> BackupRecord:
    target = Path(target)
    backup = create_backup(target, backup_root, transaction_id=transaction_id, kind=kind)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.write.tmp")
    replaced = False
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if validator is not None:
            validator(tmp)
        os.replace(tmp, target)
        replaced = True
        if validator is not None:
            validator(target)
        return backup
    except Exception as exc:
        if replaced:
            try:
                restore_backup(backup)
            except (BackupError, OSError) as restore_exc:
                raise TransactionWriteError(
                    f"Write failed for {target} and rollback also failed: {restore_exc}"
                ) from exc
        raise TransactionWriteError(f"Write failed for {target}: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)
