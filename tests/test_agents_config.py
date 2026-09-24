
import pytest

from codex_local_ops.agents_config import (
    BEGIN_MARKER,
    END_MARKER,
    AgentsConfigError,
    render_managed_block,
    restore_managed_agents,
    update_managed_agents,
)


def test_create_agents_when_absent_and_restore_missing_state(tmp_path):
    agents = tmp_path / "home" / ".codex" / "AGENTS.md"
    result = update_managed_agents(
        path=agents,
        backup_root=tmp_path / "backups",
        transaction_id="install-agents",
    )

    assert result.changed is True
    assert result.backup is not None
    assert result.backup.existed is False
    assert BEGIN_MARKER in agents.read_text(encoding="utf-8")
    restore_managed_agents(result.backup)
    assert not agents.exists()


def test_append_managed_block_preserves_user_content(tmp_path):
    agents = tmp_path / "AGENTS.md"
    original = "# My instructions\n\nKeep this exactly.\n"
    agents.write_text(original, encoding="utf-8")

    update_managed_agents(
        path=agents,
        backup_root=tmp_path / "backups",
        transaction_id="agents-append",
    )
    text = agents.read_text(encoding="utf-8")

    assert text.startswith(original)
    assert text.count(BEGIN_MARKER) == 1
    assert text.count(END_MARKER) == 1


def test_update_existing_managed_block_preserves_content_before_and_after(tmp_path):
    agents = tmp_path / "AGENTS.md"
    prefix = "# User policy\n\n"
    suffix = "\n\n# User footer\nDo not alter.\n"
    agents.write_text(
        prefix + BEGIN_MARKER + "\nold managed text\n" + END_MARKER + suffix,
        encoding="utf-8",
    )

    result = update_managed_agents(
        path=agents,
        backup_root=tmp_path / "backups",
        transaction_id="agents-update",
    )
    text = agents.read_text(encoding="utf-8")

    assert result.changed is True
    assert text.startswith(prefix)
    assert text.endswith(suffix)
    assert "old managed text" not in text
    assert text.count(BEGIN_MARKER) == 1
    assert text.count(END_MARKER) == 1


def test_agents_second_run_is_idempotent_and_does_not_duplicate_block(tmp_path):
    agents = tmp_path / "AGENTS.md"
    backups = tmp_path / "backups"
    first = update_managed_agents(path=agents, backup_root=backups, transaction_id="agents-first")
    before = agents.read_bytes()
    second = update_managed_agents(path=agents, backup_root=backups, transaction_id="agents-second")

    assert first.changed is True
    assert second.changed is False
    assert second.backup is None
    assert agents.read_bytes() == before
    assert before.decode("utf-8").count(BEGIN_MARKER) == 1


def test_agents_backup_restores_original_exactly(tmp_path):
    agents = tmp_path / "AGENTS.md"
    original = b"# Custom\r\nUser-owned text.\r\n"
    agents.write_bytes(original)

    result = update_managed_agents(
        path=agents,
        backup_root=tmp_path / "backups",
        transaction_id="agents-restore",
    )
    assert result.backup is not None
    restore_managed_agents(result.backup)

    assert agents.read_bytes() == original


def test_agents_update_preserves_existing_crlf_style(tmp_path):
    agents = tmp_path / "AGENTS.md"
    original = b"# Windows policy\r\n\r\nKeep CRLF.\r\n"
    agents.write_bytes(original)

    update_managed_agents(
        path=agents,
        backup_root=tmp_path / "backups",
        transaction_id="agents-crlf",
    )
    rendered = agents.read_bytes()

    assert rendered.startswith(original)
    assert b"\r\n" in rendered
    assert b"\n" not in rendered.replace(b"\r\n", b"")


def test_duplicate_or_unbalanced_markers_fail_without_mutation(tmp_path):
    agents = tmp_path / "AGENTS.md"
    original = f"{BEGIN_MARKER}\n{BEGIN_MARKER}\n{END_MARKER}\n"
    agents.write_text(original, encoding="utf-8")

    with pytest.raises(AgentsConfigError):
        update_managed_agents(
            path=agents,
            backup_root=tmp_path / "backups",
            transaction_id="agents-conflict",
        )

    assert agents.read_text(encoding="utf-8") == original
    assert not (tmp_path / "backups").exists()


def test_managed_policy_contains_placeholder_and_host_safety_rules():
    policy = render_managed_block()
    for placeholder in ("MyProject", "ProjectName", "ExampleProject", "YourProject"):
        assert placeholder in policy
    assert "codexLocalOps" in policy
    assert "Native2" in policy
    assert "Do not bootstrap or reinstall Local Ops" in policy
    assert "Paid AI APIs require explicit user approval" in policy
    assert "Public or remote mutations require approval" in policy
