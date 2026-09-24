from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Self

from .config import install_root
from .safety import sanitize

STATE_SCHEMA_VERSION = 1
VALID_SETUP_STATUSES = {
    "ABSENT",
    "STAGING",
    "ACTIVE",
    "DEGRADED",
    "ROLLBACK_AVAILABLE",
    "RECOVERY_REQUIRED",
}
VALID_OPERATIONS = {"INSTALL", "REPAIR", "UPDATE", "ROLLBACK", "DIAGNOSTICS"}


class SetupStateError(RuntimeError):
    """Raised when persisted Setup Assistant state is missing required structure or is corrupt."""


class SetupLockError(RuntimeError):
    """Raised when another Setup Assistant mutation already owns the lifecycle lock."""


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def setup_root(root: Path | None = None) -> Path:
    return Path(root) if root is not None else install_root() / "setup"


def setup_state_path(root: Path | None = None) -> Path:
    return setup_root(root) / "state.json"


def stable_runtime_path(user_home: Path | None = None) -> Path:
    home = Path(user_home) if user_home is not None else Path.home()
    return home / ".codex-local-ops-runtime.venv"


def stable_runtime_python(user_home: Path | None = None, *, platform_name: str | None = None) -> Path:
    runtime = stable_runtime_path(user_home)
    name = (platform_name or os.name).lower()
    if name in {"nt", "windows", "win32"}:
        return runtime / "Scripts" / "python.exe"
    return runtime / "bin" / "python"


def new_transaction_id(operation: str) -> str:
    normalized = operation.upper()
    if normalized not in VALID_OPERATIONS:
        raise ValueError(f"Unsupported setup operation: {operation}")
    return f"{normalized.lower()}-{uuid.uuid4().hex}"


@dataclass(slots=True)
class RuntimeRecord:
    package_version: str | None = None
    runtime_path: str | None = None
    source_commit: str | None = None
    python_version: str | None = None
    features: list[str] = field(default_factory=list)
    artifact_name: str | None = None
    artifact_sha256: str | None = None
    validation_status: str | None = None
    known_good_at: str | None = None


@dataclass(slots=True)
class BackupReference:
    kind: str
    backup_id: str
    target_path: str
    backup_path: str | None
    sha256: str | None
    existed: bool
    transaction_id: str | None = None
    size: int = 0
    created_at: str | None = None


@dataclass(slots=True)
class ManagedAgentsState:
    target_path: str | None = None
    block_version: int | None = None
    installed: bool = False
    backup_id: str | None = None


@dataclass(slots=True)
class RollbackMetadata:
    eligible: bool = False
    reason: str | None = None
    config_backup_id: str | None = None
    agents_backup_id: str | None = None
    setup_config_backup_id: str | None = None
    previous_runtime_path: str | None = None
    activation_transaction_id: str | None = None
    activation_record_path: str | None = None


@dataclass(slots=True)
class TransactionState:
    transaction_id: str | None = None
    operation: str | None = None
    phase: str | None = None
    started_at: str | None = None


@dataclass(slots=True)
class SetupState:
    schema_version: int = STATE_SCHEMA_VERSION
    status: str = "ABSENT"
    active_runtime: RuntimeRecord | None = None
    previous_runtime: RuntimeRecord | None = None
    config_backups: list[BackupReference] = field(default_factory=list)
    agents: ManagedAgentsState = field(default_factory=ManagedAgentsState)
    rollback: RollbackMetadata = field(default_factory=RollbackMetadata)
    transaction: TransactionState = field(default_factory=TransactionState)
    requested_features: list[str] = field(default_factory=list)
    mcp_registration: dict[str, Any] = field(default_factory=dict)
    connection_chain: dict[str, Any] = field(default_factory=dict)
    validation_summary: dict[str, Any] = field(default_factory=dict)
    last_successful_operation: str | None = None
    updated_at: str = field(default_factory=utc_timestamp)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["updated_at"] = utc_timestamp()
        return sanitize(data)


def _runtime_from(value: Any) -> RuntimeRecord | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SetupStateError("runtime record must be an object or null")
    try:
        return RuntimeRecord(**value)
    except TypeError as exc:
        raise SetupStateError(f"invalid runtime record: {exc}") from exc


def _backup_refs(value: Any) -> list[BackupReference]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SetupStateError("config_backups must be a list")
    try:
        return [BackupReference(**item) for item in value]
    except (TypeError, AttributeError) as exc:
        raise SetupStateError(f"invalid backup metadata: {exc}") from exc


def state_from_dict(data: dict[str, Any]) -> SetupState:
    if not isinstance(data, dict):
        raise SetupStateError("setup state must be a JSON object")
    if data.get("schema_version") != STATE_SCHEMA_VERSION:
        raise SetupStateError(f"Unsupported setup state schema: {data.get('schema_version')}")
    status = str(data.get("status", ""))
    if status not in VALID_SETUP_STATUSES:
        raise SetupStateError(f"Invalid setup status: {status}")
    transaction = data.get("transaction") or {}
    if not isinstance(transaction, dict):
        raise SetupStateError("transaction must be an object")
    operation = transaction.get("operation")
    if operation is not None and operation not in VALID_OPERATIONS:
        raise SetupStateError(f"Invalid setup operation: {operation}")
    try:
        return SetupState(
            schema_version=STATE_SCHEMA_VERSION,
            status=status,
            active_runtime=_runtime_from(data.get("active_runtime")),
            previous_runtime=_runtime_from(data.get("previous_runtime")),
            config_backups=_backup_refs(data.get("config_backups")),
            agents=ManagedAgentsState(**(data.get("agents") or {})),
            rollback=RollbackMetadata(**(data.get("rollback") or {})),
            transaction=TransactionState(**transaction),
            requested_features=list(data.get("requested_features") or []),
            mcp_registration=dict(data.get("mcp_registration") or {}),
            connection_chain=dict(data.get("connection_chain") or {}),
            validation_summary=dict(data.get("validation_summary") or {}),
            last_successful_operation=data.get("last_successful_operation"),
            updated_at=str(data.get("updated_at") or utc_timestamp()),
        )
    except (TypeError, ValueError) as exc:
        raise SetupStateError(f"Invalid setup state structure: {exc}") from exc


def load_setup_state(path: Path | None = None) -> SetupState:
    target = Path(path) if path is not None else setup_state_path()
    if not target.exists():
        return SetupState()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SetupStateError(f"Cannot read setup state {target}: {exc}") from exc
    return state_from_dict(raw)


def save_setup_state(state: SetupState, path: Path | None = None) -> Path:
    target = Path(path) if path is not None else setup_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return target


class SetupLock:
    """Cross-process user-level lock for future setup mutations."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else setup_root() / "setup.lock"
        self._handle: Any | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0)
            if not handle.read(1):
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise SetupLockError("SETUP_BUSY") from exc
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    def __iter__(self) -> Iterator[SetupLock]:
        with self:
            yield self
