from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from codex_local_ops.trusted_roots import (
    TrustedRootError,
    add_trusted_root,
    inspect_trusted_roots,
    is_broad_trusted_root,
    normalize_trusted_root,
    restore_trusted_roots,
)


def test_add_trusted_root_preserves_unrelated_yaml_and_restores_exact_bytes(tmp_path: Path):
    config = tmp_path / "config" / "config.yaml"
    existing = tmp_path / "existing-project"
    selected = tmp_path / "selected-project"
    existing.mkdir()
    selected.mkdir()
    config.parent.mkdir(parents=True)
    original = (
        "version: 1\n"
        "custom:\n"
        "  nested: keep-me\n"
        "projects:\n"
        f"  trusted_roots:\n    - {existing}\n"
        "logging:\n"
        "  level: DEBUG\n"
    ).encode()
    config.write_bytes(original)

    result = add_trusted_root(
        selected,
        config_file=config,
        backup_root=tmp_path / "backups",
        transaction_id="trusted-root-add",
    )

    assert result.changed is True
    assert result.backup is not None
    document = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert document["custom"] == {"nested": "keep-me"}
    assert document["logging"] == {"level": "DEBUG"}
    assert document["projects"]["trusted_roots"] == [str(existing), str(selected.resolve())]

    restore_trusted_roots(result.backup)
    assert config.read_bytes() == original


def test_existing_trusted_root_is_idempotent_and_creates_no_backup(tmp_path: Path):
    config = tmp_path / "config.yaml"
    selected = tmp_path / "project"
    selected.mkdir()
    config.write_text(f"projects:\n  trusted_roots:\n    - {selected.resolve()}\n", encoding="utf-8")
    before = config.read_bytes()

    result = add_trusted_root(
        selected,
        config_file=config,
        backup_root=tmp_path / "backups",
        transaction_id="trusted-root-idempotent",
    )

    assert result.changed is False
    assert result.backup is None
    assert config.read_bytes() == before
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("value", ["/", "C:\\", "C:"])
def test_broad_or_ambiguous_roots_are_rejected(value: str):
    assert is_broad_trusted_root(value) is True
    with pytest.raises(TrustedRootError, match="roots are rejected"):
        normalize_trusted_root(value, require_exists=False)


def test_inspection_rejects_invalid_trusted_root_shape(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text("projects:\n  trusted_roots: D:/CODEX\n", encoding="utf-8")

    inspection = inspect_trusted_roots(config_file=config)

    assert inspection.valid is False
    assert inspection.roots == ()
    assert "must be a list" in (inspection.error or "")
