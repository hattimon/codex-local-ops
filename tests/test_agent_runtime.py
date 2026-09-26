from __future__ import annotations

from pathlib import Path

import pytest

from codex_local_ops.agent_core import AgentPolicy, AgentProvider, ProviderCost, Sensitivity
from codex_local_ops.agent_runtime import AgentRuntime


class FakeAdapter:
    def __init__(self, results: list[dict], cancel_after_first: list[bool] | None = None) -> None:
        self.results = list(results)
        self.calls: list[dict] = []
        self.cancel_after_first = cancel_after_first

    def run(self, task, repo, *, provider, model, timeout=None):
        self.calls.append({"task": task, "repo": repo, "provider": provider, "model": model, "timeout": timeout})
        if self.cancel_after_first is not None:
            self.cancel_after_first[0] = True
        return self.results.pop(0)


def _ok(**extra) -> dict:
    return {"status": "COMPLETED", "exit_code": 0, "stdout": "ok", "stderr": "", "duration_ms": 3, **extra}


def _failed(error: str, *, status: str = "FAILED", exit_code: int | None = 1) -> dict:
    return {"status": status, "exit_code": exit_code, "stdout": "", "stderr": "", "error": error, "duration_ms": 2}


def _runtime(tmp_path: Path, monkeypatch, results: list[dict], **kwargs) -> tuple[AgentRuntime, FakeAdapter]:
    monkeypatch.setattr("codex_local_ops.agent_runtime.assert_trusted_path", lambda path, must_exist: tmp_path)
    adapter = FakeAdapter(results, kwargs.pop("cancel_after_first", None))
    return AgentRuntime(adapter=adapter, **kwargs), adapter


def test_qwen_succeeds_without_fallback_and_propagates_usage(tmp_path: Path, monkeypatch) -> None:
    runtime, adapter = _runtime(tmp_path, monkeypatch, [_ok(usage={"input_tokens": 4}, input_tokens=4)])

    result = runtime.run("task", tmp_path, sensitivity=Sensitivity.SENSITIVE, timeout=17)

    assert result["status"] == "COMPLETED"
    assert result["selected_provider"] == "ollama"
    assert len(result["attempts"]) == 1
    assert result["fallback_history"] == []
    assert result["usage"] == {"input_tokens": 4}
    assert result["input_tokens"] == 4
    assert adapter.calls[0]["model"] == "qwen3-coder:30b"
    assert adapter.calls[0]["timeout"] == 17


def test_missing_usage_does_not_fail_completed_run(tmp_path: Path, monkeypatch) -> None:
    runtime, _ = _runtime(tmp_path, monkeypatch, [_ok()])

    result = runtime.run("task", tmp_path)

    assert result["status"] == "COMPLETED"
    assert result["usage"] == {}
    assert result["input_tokens"] is None


@pytest.mark.parametrize(
    "failure",
    [
        _failed("timeout", status="TIMEOUT", exit_code=None),
        _failed("provider unavailable"),
        _failed("auth unavailable"),
        _failed("429 quota exceeded"),
        _failed("upstream 503"),
        _failed("invalid response"),
    ],
)
def test_retryable_qwen_failures_fall_back_to_devstral(tmp_path: Path, monkeypatch, failure: dict) -> None:
    runtime, adapter = _runtime(tmp_path, monkeypatch, [failure, _ok()])

    result = runtime.run("task", tmp_path)

    assert result["status"] == "COMPLETED"
    assert [call["model"] for call in adapter.calls] == ["qwen3-coder:30b", "devstral:24b"]
    assert result["fallback_history"][0]["to_model"] == "devstral:24b"
    assert len(result["attempts"]) == 2


def test_nonretryable_policy_error_does_not_fallback(tmp_path: Path, monkeypatch) -> None:
    runtime, adapter = _runtime(tmp_path, monkeypatch, [_failed("PAID_PROVIDER_NOT_ALLOWED")])

    result = runtime.run("task", tmp_path)

    assert result["status"] == "FAILED"
    assert len(adapter.calls) == 1
    assert result["fallback_history"] == []


def test_cancellation_prevents_fallback(tmp_path: Path, monkeypatch) -> None:
    cancelled = [False]
    runtime, adapter = _runtime(
        tmp_path,
        monkeypatch,
        [_failed("timeout", status="TIMEOUT", exit_code=None)],
        cancel_after_first=cancelled,
    )

    result = runtime.run("task", tmp_path, is_cancelled=lambda: cancelled[0])

    assert result["status"] == "CANCELLED"
    assert len(adapter.calls) == 1
    assert result["fallback_history"] == []


def test_both_providers_failing_returns_all_unavailable(tmp_path: Path, monkeypatch) -> None:
    runtime, _ = _runtime(tmp_path, monkeypatch, [_failed("timeout", status="TIMEOUT", exit_code=None), _failed("timeout", status="TIMEOUT", exit_code=None)])

    result = runtime.run("task", tmp_path)

    assert result["status"] == "FAILED"
    assert result["error"] == "ALL_FREE_PROVIDERS_UNAVAILABLE"
    assert len(result["attempts"]) == 2


def test_untrusted_repo_is_rejected_before_adapter(tmp_path: Path, monkeypatch) -> None:
    runtime, adapter = _runtime(tmp_path, monkeypatch, [_ok()])
    monkeypatch.setattr(
        "codex_local_ops.agent_runtime.assert_trusted_path",
        lambda path, must_exist: (_ for _ in ()).throw(PermissionError("outside trusted roots")),
    )

    with pytest.raises(PermissionError):
        runtime.run("task", tmp_path)
    assert adapter.calls == []


@pytest.mark.parametrize("cost", [ProviderCost.PAID, ProviderCost.UNKNOWN])
def test_disallowed_preferred_provider_never_reaches_adapter(tmp_path: Path, monkeypatch, cost: ProviderCost) -> None:
    blocked = AgentProvider("external", "model", False, cost)
    runtime, adapter = _runtime(tmp_path, monkeypatch, [_ok()], providers=(blocked,), policy=AgentPolicy())

    result = runtime.run("task", tmp_path, preferred_provider="external", preferred_model="model")

    assert result["status"] == "FAILED"
    assert adapter.calls == []


def test_handoff_is_compact_and_redacted(tmp_path: Path, monkeypatch) -> None:
    runtime, adapter = _runtime(tmp_path, monkeypatch, [_failed("Authorization: Bearer secret-value"), _ok()])

    result = runtime.run("token=top-secret", tmp_path)

    assert result["status"] == "COMPLETED"
    handoff_task = adapter.calls[1]["task"]
    assert handoff_task.startswith("Compact handoff:\n")
    assert "secret-value" not in handoff_task
    assert "top-secret" not in handoff_task
