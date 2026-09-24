import json

import pytest

from codex_local_ops.setup_state import (
    BackupReference,
    ManagedAgentsState,
    RollbackMetadata,
    RuntimeRecord,
    SetupLock,
    SetupLockError,
    SetupState,
    SetupStateError,
    TransactionState,
    load_setup_state,
    new_transaction_id,
    save_setup_state,
    stable_runtime_path,
    stable_runtime_python,
)


def test_missing_setup_state_returns_absent(tmp_path):
    state = load_setup_state(tmp_path / "missing.json")
    assert state.status == "ABSENT"
    assert state.schema_version == 1
    assert state.active_runtime is None


def test_setup_state_round_trip_and_secret_redaction(tmp_path):
    path = tmp_path / "setup" / "state.json"
    state = SetupState(
        status="ROLLBACK_AVAILABLE",
        active_runtime=RuntimeRecord(
            package_version="0.2.0",
            runtime_path=str(tmp_path / "runtime"),
            source_commit="abc123",
            python_version="3.12.10",
            features=["browser", "windows"],
            known_good_at="2026-09-24T10:00:00Z",
        ),
        previous_runtime=RuntimeRecord(package_version="0.1.0"),
        config_backups=[
            BackupReference(
                kind="codex-config",
                backup_id="backup-1",
                target_path=str(tmp_path / "config.toml"),
                backup_path=str(tmp_path / "backup.toml"),
                sha256="deadbeef",
                existed=True,
            )
        ],
        agents=ManagedAgentsState(target_path=str(tmp_path / "AGENTS.md"), block_version=1, installed=True),
        rollback=RollbackMetadata(eligible=True, reason="known-good runtime available"),
        transaction=TransactionState(
            transaction_id="update-1",
            operation="UPDATE",
            phase="POSTFLIGHT",
            started_at="2026-09-24T10:00:00Z",
        ),
        requested_features=["browser"],
        connection_chain={"auth_token": "must-not-be-persisted", "native2": "READY"},
        validation_summary={"status": "PASS"},
        last_successful_operation="INSTALL",
    )

    save_setup_state(state, path)
    raw = path.read_text(encoding="utf-8")
    assert "must-not-be-persisted" not in raw
    assert "[REDACTED]" in raw

    loaded = load_setup_state(path)
    assert loaded.status == "ROLLBACK_AVAILABLE"
    assert loaded.active_runtime is not None
    assert loaded.active_runtime.package_version == "0.2.0"
    assert loaded.previous_runtime is not None
    assert loaded.previous_runtime.package_version == "0.1.0"
    assert loaded.config_backups[0].backup_id == "backup-1"
    assert loaded.rollback.eligible is True
    assert loaded.transaction.operation == "UPDATE"
    assert loaded.connection_chain["auth_token"] == "[REDACTED]"


@pytest.mark.parametrize(
    "content",
    ["{broken", json.dumps({"schema_version": 999, "status": "ABSENT"}), json.dumps({"schema_version": 1, "status": "UNKNOWN"})],
)
def test_invalid_or_corrupt_setup_state_fails_clearly(tmp_path, content):
    path = tmp_path / "state.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SetupStateError):
        load_setup_state(path)


def test_stable_runtime_paths_are_user_relative_and_platform_safe(tmp_path):
    assert stable_runtime_path(tmp_path) == tmp_path / ".codex-local-ops-runtime.venv"
    assert stable_runtime_python(tmp_path, platform_name="windows") == (
        tmp_path / ".codex-local-ops-runtime.venv" / "Scripts" / "python.exe"
    )
    assert stable_runtime_python(tmp_path, platform_name="posix") == (
        tmp_path / ".codex-local-ops-runtime.venv" / "bin" / "python"
    )


def test_transaction_ids_validate_operation():
    first = new_transaction_id("repair")
    second = new_transaction_id("REPAIR")
    assert first.startswith("repair-")
    assert first != second
    with pytest.raises(ValueError):
        new_transaction_id("PUBLISH")


def test_setup_lock_rejects_second_owner_until_release(tmp_path):
    path = tmp_path / "setup.lock"
    first = SetupLock(path)
    second = SetupLock(path)

    first.acquire()
    try:
        with pytest.raises(SetupLockError, match="SETUP_BUSY"):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()
