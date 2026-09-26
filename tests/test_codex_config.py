from pathlib import Path

import pytest
import tomlkit

from codex_local_ops.codex_config import CodexConfigError, restore_codex_config, update_codex_mcp


def _runtime(tmp_path: Path) -> Path:
    return tmp_path / "home" / ".codex-local-ops-runtime.venv" / "Scripts" / "python.exe"


def test_add_codex_local_ops_when_absent_and_preserve_existing_config(tmp_path):
    config = tmp_path / "home" / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        'model = "gpt-test"\n\n[mcp_servers.other]\ncommand = "other-python"\nargs = ["server.py"]\n',
        encoding="utf-8",
    )

    result = update_codex_mcp(
        path=config,
        runtime_python=_runtime(tmp_path),
        backup_root=tmp_path / "backups",
        transaction_id="install-1",
    )
    document = tomlkit.parse(config.read_text(encoding="utf-8"))

    assert result.changed is True
    assert document["model"] == "gpt-test"
    assert document["mcp_servers"]["other"]["command"] == "other-python"
    assert document["mcp_servers"]["codexLocalOps"]["command"] == str(_runtime(tmp_path))
    assert list(document["mcp_servers"]["codexLocalOps"]["args"]) == ["-m", "codex_local_ops.server"]
    assert result.backup is not None


def test_update_wrong_codex_local_ops_path_preserves_other_mcp_entries(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.codexLocalOps]\ncommand = "C:\\\\old\\\\python.exe"\nargs = ["-m", "codex_local_ops.server"]\n\n'
        '[mcp_servers.keepMe]\ncommand = "keep"\nargs = ["x"]\n',
        encoding="utf-8",
    )

    update_codex_mcp(
        path=config,
        runtime_python=_runtime(tmp_path),
        backup_root=tmp_path / "backups",
        transaction_id="repair-1",
    )
    document = tomlkit.parse(config.read_text(encoding="utf-8"))

    assert document["mcp_servers"]["codexLocalOps"]["command"] == str(_runtime(tmp_path))
    assert document["mcp_servers"]["keepMe"]["command"] == "keep"


def test_malformed_toml_fails_without_mutation_or_backup(tmp_path):
    config = tmp_path / "config.toml"
    original = b"[broken\nvalue = 1\n"
    config.write_bytes(original)
    backups = tmp_path / "backups"

    with pytest.raises(CodexConfigError):
        update_codex_mcp(
            path=config,
            runtime_python=_runtime(tmp_path),
            backup_root=backups,
            transaction_id="repair-bad",
        )

    assert config.read_bytes() == original
    assert not backups.exists()


def test_non_table_mcp_servers_fails_cleanly_without_mutation_or_backup(tmp_path):
    config = tmp_path / "config.toml"
    original = b'mcp_servers = "invalid"\nmodel = "keep"\n'
    config.write_bytes(original)
    backups = tmp_path / "backups"

    with pytest.raises(CodexConfigError, match="mcp_servers must be a TOML table"):
        update_codex_mcp(
            path=config,
            runtime_python=_runtime(tmp_path),
            backup_root=backups,
            transaction_id="repair-shape",
        )

    assert config.read_bytes() == original
    assert not backups.exists()


def test_codex_config_second_run_is_idempotent(tmp_path):
    config = tmp_path / "config.toml"
    backups = tmp_path / "backups"
    first = update_codex_mcp(
        path=config,
        runtime_python=_runtime(tmp_path),
        backup_root=backups,
        transaction_id="install-repeat",
    )
    before = config.read_bytes()
    second = update_codex_mcp(
        path=config,
        runtime_python=_runtime(tmp_path),
        backup_root=backups,
        transaction_id="install-repeat-2",
    )

    assert first.changed is True
    assert second.changed is False
    assert second.backup is None
    assert config.read_bytes() == before


def test_codex_config_backup_can_restore_previous_file_exactly(tmp_path):
    config = tmp_path / "config.toml"
    original = b'# keep this comment\r\nmodel = "original"\r\n'
    config.write_bytes(original)

    result = update_codex_mcp(
        path=config,
        runtime_python=_runtime(tmp_path),
        backup_root=tmp_path / "backups",
        transaction_id="install-restore",
    )
    assert result.backup is not None
    restore_codex_config(result.backup)

    assert config.read_bytes() == original


def test_codex_config_uses_portable_stable_runtime_for_supplied_home(tmp_path):
    home = tmp_path / "isolated-home"
    config = home / ".codex" / "config.toml"
    result = update_codex_mcp(
        path=config,
        user_home=home,
        runtime_python=home / ".codex-local-ops-runtime.venv" / "Scripts" / "python.exe",
        backup_root=tmp_path / "backups",
        transaction_id="install-home",
    )
    assert result.runtime_python.startswith(str(home))
    assert Path(result.path) == config
