from __future__ import annotations

import hashlib
import json

import pytest

from codex_local_ops import cli
from codex_local_ops.config import load_config, save_config


def _configure(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(tmp_path / "home"))
    cfg = load_config()
    cfg["permissions"]["local_profile"] = "DEVELOPER"
    save_config(cfg)
    monkeypatch.setattr(cli, "emit", lambda *args, **kwargs: None)


def _runtime_result(**overrides) -> dict:
    return {
        "job_id": "agent_test",
        "status": "COMPLETED",
        "selected_provider": "ollama",
        "selected_model": "qwen3-coder:30b",
        "attempts": [{"provider": "ollama", "model": "qwen3-coder:30b"}],
        "fallback_history": [],
        "stdout": "done",
        "stderr": "",
        "duration_ms": 12,
        "usage": {},
        "error": None,
        **overrides,
    }


class FakeRuntime:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    def run(self, task, repo, **kwargs):
        self.calls.append({"task": task, "repo": repo, **kwargs})
        return self.result


def _invoke(monkeypatch, tmp_path, capsys, runtime_result: dict | None = None, *extra: str):
    _configure(monkeypatch, tmp_path)
    fake = FakeRuntime(runtime_result or _runtime_result())
    monkeypatch.setattr(cli, "AgentRuntime", lambda: fake)
    monkeypatch.setattr(cli.git_ops, "_repo_root", lambda path: (tmp_path, "git"))
    monkeypatch.setattr("sys.argv", ["clops", "agent", "run", "--repo", str(tmp_path), "--task", "safe task", *extra])
    cli.main()
    return fake, capsys.readouterr().out


def test_agent_run_is_registered() -> None:
    args = cli._build_parser().parse_args(["agent", "run", "--repo", "repo", "--task", "task"])
    assert args.command == "agent"
    assert args.agent_command == "run"
    assert args.sensitivity == "private"


def test_agent_run_uses_trusted_git_root_and_router_defaults(monkeypatch, tmp_path, capsys) -> None:
    fake, output = _invoke(monkeypatch, tmp_path, capsys)

    assert fake.calls[0]["task"] == "safe task"
    assert fake.calls[0]["repo"] == tmp_path
    assert fake.calls[0]["sensitivity"] == "private"
    assert fake.calls[0]["preferred_provider"] is None
    assert fake.calls[0]["preferred_model"] is None
    assert "Status: COMPLETED" in output


@pytest.mark.parametrize("model", ["qwen3-coder:30b", "devstral:24b"])
def test_explicit_local_profile_is_forwarded_to_runtime(monkeypatch, tmp_path, capsys, model: str) -> None:
    fake, _ = _invoke(monkeypatch, tmp_path, capsys, None, "--provider", "ollama", "--model", model)

    assert fake.calls[0]["preferred_provider"] == "ollama"
    assert fake.calls[0]["preferred_model"] == model


@pytest.mark.parametrize("error", ["PAID_PROVIDER_NOT_ALLOWED", "PROVIDER_COST_UNKNOWN"])
def test_policy_rejection_is_nonzero(monkeypatch, tmp_path, capsys, error: str) -> None:
    with pytest.raises(SystemExit) as exc:
        _invoke(monkeypatch, tmp_path, capsys, _runtime_result(status="FAILED", error=error))
    assert exc.value.code == 1


@pytest.mark.parametrize("message", ["outside trusted roots", "symlink outside trusted root"])
def test_untrusted_repo_is_rejected_before_runtime(monkeypatch, tmp_path, capsys, message: str) -> None:
    _configure(monkeypatch, tmp_path)
    fake = FakeRuntime(_runtime_result())
    monkeypatch.setattr(cli, "AgentRuntime", lambda: fake)
    monkeypatch.setattr(
        cli.git_ops,
        "_repo_root",
        lambda path: (_ for _ in ()).throw(PermissionError(message)),
    )
    monkeypatch.setattr("sys.argv", ["clops", "agent", "run", "--repo", str(tmp_path), "--task", "safe task"])

    with pytest.raises(SystemExit):
        cli.main()
    assert fake.calls == []


@pytest.mark.parametrize("status", ["FAILED", "TIMEOUT", "CANCELLED"])
def test_noncompleted_status_has_nonzero_exit(monkeypatch, tmp_path, capsys, status: str) -> None:
    with pytest.raises(SystemExit) as exc:
        _invoke(monkeypatch, tmp_path, capsys, _runtime_result(status=status, error=status))
    assert exc.value.code == 1


def test_fallback_is_displayed_and_json_is_machine_readable(monkeypatch, tmp_path, capsys) -> None:
    result = _runtime_result(
        fallback_history=[{"from_model": "qwen3-coder:30b", "to_model": "devstral:24b", "reason": "timeout"}]
    )
    _, human = _invoke(monkeypatch, tmp_path, capsys, result)
    assert "Fallback used: yes" in human

    _, output = _invoke(monkeypatch, tmp_path, capsys, result, "--json")
    parsed = json.loads(output)
    assert set(parsed) == {
        "job_id", "status", "provider", "model", "attempts", "fallback_history",
        "stdout", "stderr", "duration", "usage", "error", "base_head", "branch",
        "changed_files", "diff_summary", "repo_lock_status",
    }
    assert parsed["fallback_history"][0]["to_model"] == "devstral:24b"


@pytest.mark.parametrize("json_output", [False, True])
def test_output_is_redacted(monkeypatch, tmp_path, capsys, json_output: bool) -> None:
    extra = ("--json",) if json_output else ()
    _, output = _invoke(monkeypatch, tmp_path, capsys, _runtime_result(stdout="token=not-for-output"), *extra)

    assert "not-for-output" not in output
    if json_output:
        assert json.loads(output)["stdout"] == "token=[REDACTED]"


def test_agent_run_does_not_invoke_git_mutation(monkeypatch, tmp_path, capsys) -> None:
    _configure(monkeypatch, tmp_path)
    fake = FakeRuntime(_runtime_result())
    monkeypatch.setattr(cli, "AgentRuntime", lambda: fake)
    monkeypatch.setattr(cli.git_ops, "_repo_root", lambda path: (tmp_path, "git"))
    for name in ("stage", "commit", "push", "publish"):
        monkeypatch.setattr(cli.git_ops, name, lambda *args, **kwargs: pytest.fail(f"git {name} called"))
    monkeypatch.setattr("sys.argv", ["clops", "agent", "run", "--repo", str(tmp_path), "--task", "safe task"])

    cli.main()
    assert fake.calls


def test_agent_audit_uses_task_hash_not_task_content(monkeypatch, tmp_path, capsys) -> None:
    _configure(monkeypatch, tmp_path)
    seen: dict = {}
    task = "do not store this task verbatim"
    fake = FakeRuntime(_runtime_result())
    monkeypatch.setattr(cli, "AgentRuntime", lambda: fake)
    monkeypatch.setattr(cli.git_ops, "_repo_root", lambda path: (tmp_path, "git"))
    monkeypatch.setattr(cli, "emit", lambda tool, args, result, started, **kwargs: seen.update(args))
    monkeypatch.setattr("sys.argv", ["clops", "agent", "run", "--repo", str(tmp_path), "--task", task])

    cli.main()

    assert seen["task_sha256"] == hashlib.sha256(task.encode("utf-8")).hexdigest()
    assert task not in repr(seen)
