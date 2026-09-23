from __future__ import annotations

from codex_local_ops import cli
from codex_local_ops.config import load_config, save_config


def _profile(monkeypatch, tmp_path, profile: str) -> None:
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(tmp_path / "home"))
    cfg = load_config()
    cfg["permissions"]["local_profile"] = profile
    save_config(cfg)


def test_local_write_requires_developer_profile_and_is_audited(monkeypatch, tmp_path) -> None:
    events = []
    monkeypatch.setattr(cli, "emit", lambda *args, **kwargs: events.append((args, kwargs)))
    _profile(monkeypatch, tmp_path, "SAFE")

    blocked = cli._local_action(
        "cli_git_commit",
        {"path": str(tmp_path), "message_supplied": True},
        lambda: {"status": "OK"},
        write=True,
    )
    assert blocked["status"] == "PERMISSION_DENIED"
    assert events[-1][1]["approval_class"] == "local-write"

    _profile(monkeypatch, tmp_path, "DEVELOPER")
    allowed = cli._local_action(
        "cli_git_commit",
        {"path": str(tmp_path), "message_supplied": True},
        lambda: {"status": "OK"},
        write=True,
    )
    assert allowed["status"] == "OK"
    assert events[-1][1]["approval_class"] == "local-write"


def test_cli_git_commit_does_not_put_message_in_audit_args(monkeypatch, tmp_path, capsys) -> None:
    _profile(monkeypatch, tmp_path, "DEVELOPER")
    seen = {}
    secret_message = "commit message should not be audited verbatim"
    monkeypatch.setattr(cli.git_ops, "commit", lambda path, message: {"status": "OK", "exit_code": 0})
    monkeypatch.setattr(cli, "emit", lambda tool, args, result, started, **kwargs: seen.update(args))
    monkeypatch.setattr(
        "sys.argv",
        ["clops", "git", "commit", "--path", str(tmp_path), "--message", secret_message],
    )

    cli.main()

    assert seen["message_supplied"] is True
    assert secret_message not in repr(seen)
    assert '"status": "OK"' in capsys.readouterr().out
