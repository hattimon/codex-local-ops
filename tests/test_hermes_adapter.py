from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_local_ops.agent_core import AgentPolicy, AgentPolicyError, AgentProvider, ProviderCost
from codex_local_ops.hermes_adapter import HermesAgentAdapter, discover_executable, parse_usage_file


def _adapter(tmp_path: Path, monkeypatch, process_result: dict | None = None) -> HermesAgentAdapter:
    executable = tmp_path / "hermes"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr("codex_local_ops.hermes_adapter.assert_trusted_path", lambda path, must_exist: tmp_path)
    if process_result is not None:
        monkeypatch.setattr("codex_local_ops.hermes_adapter.run", lambda argv, **kwargs: process_result)
    return HermesAgentAdapter(executable=executable)


def test_discovery_uses_configured_executable(tmp_path: Path) -> None:
    executable = tmp_path / "hermes"
    executable.write_text("", encoding="utf-8")
    assert discover_executable(executable) == executable


def test_discovery_returns_none_when_missing(monkeypatch) -> None:
    monkeypatch.setattr("codex_local_ops.hermes_adapter.shutil.which", lambda _: None)
    monkeypatch.setattr("codex_local_ops.hermes_adapter._known_executable_locations", lambda: ())
    assert discover_executable() is None


def test_run_builds_safe_list_argv_and_exact_provider_model(tmp_path: Path, monkeypatch) -> None:
    received: dict = {}

    def fake_run(argv, **kwargs):
        received.update(argv=argv, **kwargs)
        return {"exit_code": 0, "stdout": "ok", "stderr": "", "duration_ms": 2}

    adapter = _adapter(tmp_path, monkeypatch)
    monkeypatch.setattr("codex_local_ops.hermes_adapter.run", fake_run)
    result = adapter.run("quote ' ; $HOME", tmp_path, provider="ollama", model="devstral:24b")
    assert isinstance(received["argv"], list)
    assert received["argv"][1:3] == ["-z", "quote ' ; $HOME"]
    assert received["argv"][-4:] == ["--provider", "ollama", "--model", "devstral:24b"]
    assert "shell" not in received
    assert result["status"] == "COMPLETED"


def test_local_default_timeout_is_six_hundred_seconds(tmp_path: Path, monkeypatch) -> None:
    received: dict = {}
    adapter = _adapter(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "codex_local_ops.hermes_adapter.run",
        lambda argv, **kwargs: received.update(kwargs) or {"exit_code": 0, "stdout": "ok", "stderr": ""},
    )

    adapter.run("safe", tmp_path)

    assert adapter.local_timeout == 600
    assert received["timeout"] == 600


def test_agent_timeout_config_overrides_safe_local_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "codex_local_ops.hermes_adapter.load_config",
        lambda: {"agents": {"hermes": {"local_timeout": 700, "external_timeout": 120}}},
    )

    assert HermesAgentAdapter(executable=tmp_path / "hermes").local_timeout == 700


def test_explicit_timeout_overrides_local_default(tmp_path: Path, monkeypatch) -> None:
    received: dict = {}
    adapter = _adapter(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "codex_local_ops.hermes_adapter.run",
        lambda argv, **kwargs: received.update(kwargs) or {"exit_code": 0, "stdout": "ok", "stderr": ""},
    )

    adapter.run("safe", tmp_path, timeout=1)

    assert received["timeout"] == 1


def test_untrusted_cwd_is_rejected(tmp_path: Path, monkeypatch) -> None:
    adapter = HermesAgentAdapter(executable=tmp_path / "hermes")
    monkeypatch.setattr(
        "codex_local_ops.hermes_adapter.assert_trusted_path",
        lambda path, must_exist: (_ for _ in ()).throw(PermissionError("outside trusted roots")),
    )
    with pytest.raises(PermissionError):
        adapter.run("safe", tmp_path)


def test_symlink_escape_is_rejected_by_trusted_path(tmp_path: Path, monkeypatch) -> None:
    adapter = HermesAgentAdapter(executable=tmp_path / "hermes")
    monkeypatch.setattr(
        "codex_local_ops.hermes_adapter.assert_trusted_path",
        lambda path, must_exist: (_ for _ in ()).throw(PermissionError("symlink outside trusted root")),
    )
    with pytest.raises(PermissionError, match="symlink"):
        adapter.run("safe", tmp_path)


def test_usage_parsing_tolerates_custom_provider_and_bad_files(tmp_path: Path) -> None:
    usage = tmp_path / "usage.json"
    usage.write_text(json.dumps({"provider": "custom", "input_tokens": 4, "output_tokens": 5}), encoding="utf-8")
    assert parse_usage_file(usage)["provider"] == "custom"
    assert parse_usage_file(usage)["input_tokens"] == 4
    assert parse_usage_file(usage)["output_tokens"] == 5
    assert parse_usage_file(tmp_path / "missing.json") == {}
    usage.write_text("not json", encoding="utf-8")
    assert parse_usage_file(usage) == {}


def test_timeout_and_nonzero_result_are_structured(tmp_path: Path, monkeypatch) -> None:
    timeout = _adapter(tmp_path, monkeypatch, {"timed_out": True, "stdout": "", "stderr": "", "duration_ms": 8})
    assert timeout.run("safe", tmp_path)["status"] == "TIMEOUT"
    failed = _adapter(tmp_path, monkeypatch, {"exit_code": 7, "stdout": "", "stderr": "", "duration_ms": 8})
    assert failed.run("safe", tmp_path)["status"] == "FAILED"


def test_stderr_is_redacted(tmp_path: Path, monkeypatch) -> None:
    adapter = _adapter(
        tmp_path,
        monkeypatch,
        {
            "exit_code": 1,
            "stdout": "token=secret-value",
            "stderr": "Authorization: Bearer abcdefghijklmnop",
            "duration_ms": 1,
        },
    )
    result = adapter.run("safe", tmp_path)
    assert "secret-value" not in result["stdout"]
    assert "abcdefghijklmnop" not in result["stderr"]


@pytest.mark.parametrize("cost", [ProviderCost.PAID, ProviderCost.UNKNOWN])
def test_nonfree_provider_is_blocked_before_subprocess(tmp_path: Path, monkeypatch, cost: ProviderCost) -> None:
    blocked = AgentProvider("external", "model", False, cost)
    adapter = HermesAgentAdapter(
        providers=(blocked,),
        policy=AgentPolicy(),
        executable=tmp_path / "hermes",
    )
    monkeypatch.setattr(
        "codex_local_ops.hermes_adapter.run",
        lambda *args, **kwargs: pytest.fail("subprocess called"),
    )
    with pytest.raises(AgentPolicyError):
        adapter.run("safe", tmp_path, provider="external", model="model")


def test_async_start_and_cancel_reuse_jobs(tmp_path: Path, monkeypatch) -> None:
    adapter = _adapter(tmp_path, monkeypatch)
    seen: dict = {}
    monkeypatch.setattr(
        "codex_local_ops.hermes_adapter.jobs.start",
        lambda argv, **kwargs: seen.update(argv=argv, **kwargs)
        or {"status": "STARTED", "session_id": "job_" + "a" * 32},
    )
    monkeypatch.setattr(
        "codex_local_ops.hermes_adapter.jobs.cancel",
        lambda session_id: {"status": "CANCELLED", "session_id": session_id},
    )
    started = adapter.start("safe", tmp_path)
    assert started["status"] == "STARTED"
    assert seen["argv"][-1] == "qwen3-coder:30b"
    assert seen["timeout"] == adapter.local_timeout
    assert adapter.cancel(started["session_id"])["status"] == "CANCELLED"
