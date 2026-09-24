from pathlib import Path

import pytest

from codex_local_ops.backup_ops import (
    BackupError,
    TransactionWriteError,
    create_backup,
    restore_backup,
    transactional_write_bytes,
)


def test_backup_preserves_exact_bytes_and_restores(tmp_path):
    target = tmp_path / "config.toml"
    original = b"alpha\r\nbeta\x00tail\n"
    target.write_bytes(original)
    backups = tmp_path / "backups"

    record = create_backup(target, backups, transaction_id="tx-1", kind="test")
    target.write_bytes(b"changed")
    restore_backup(record)

    assert record.existed is True
    assert Path(record.backup_path).read_bytes() == original
    assert target.read_bytes() == original


def test_transactional_write_creates_backup_before_validation(tmp_path):
    target = tmp_path / "file.txt"
    target.write_bytes(b"before")
    backups = tmp_path / "backups"
    observed = []

    def validator(_candidate: Path) -> None:
        current = list(backups.glob("*.bak"))
        assert len(current) == 1
        assert current[0].read_bytes() == b"before"
        observed.append(True)

    record = transactional_write_bytes(
        target,
        b"after",
        backup_root=backups,
        transaction_id="tx-before",
        kind="test",
        validator=validator,
    )

    assert record.existed is True
    assert target.read_bytes() == b"after"
    assert observed == [True, True]


def test_failed_post_replace_validation_restores_original(tmp_path):
    target = tmp_path / "file.txt"
    target.write_bytes(b"original")
    backups = tmp_path / "backups"

    def validator(candidate: Path) -> None:
        if candidate == target:
            raise ValueError("post-write validation failure")

    with pytest.raises(TransactionWriteError):
        transactional_write_bytes(
            target,
            b"new data",
            backup_root=backups,
            transaction_id="tx-fail",
            kind="test",
            validator=validator,
        )

    assert target.read_bytes() == b"original"


def test_repeated_backup_in_same_transaction_is_idempotent(tmp_path):
    target = tmp_path / "file.txt"
    target.write_bytes(b"same")
    backups = tmp_path / "backups"

    first = create_backup(target, backups, transaction_id="tx-repeat", kind="test")
    second = create_backup(target, backups, transaction_id="tx-repeat", kind="test")

    assert first.backup_id == second.backup_id
    assert first.backup_path == second.backup_path
    assert len(list(backups.glob("*.bak"))) == 1


def test_restore_of_previously_missing_file_removes_only_created_target(tmp_path):
    target = tmp_path / "new.txt"
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")
    record = create_backup(target, tmp_path / "backups", transaction_id="tx-new", kind="test")
    target.write_text("created", encoding="utf-8")

    restore_backup(record)

    assert not target.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_corrupt_backup_refuses_restore_without_touching_target_or_unrelated_file(tmp_path):
    target = tmp_path / "file.txt"
    unrelated = tmp_path / "keep.txt"
    target.write_bytes(b"original")
    unrelated.write_bytes(b"keep")

    record = create_backup(target, tmp_path / "backups", transaction_id="tx-corrupt", kind="test")
    assert record.backup_path is not None
    Path(record.backup_path).write_bytes(b"corrupt")
    target.write_bytes(b"current")

    with pytest.raises(BackupError, match="hash mismatch"):
        restore_backup(record)

    assert target.read_bytes() == b"current"
    assert unrelated.read_bytes() == b"keep"
