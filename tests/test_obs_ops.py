from __future__ import annotations

from codex_local_ops import obs_ops


def test_obs_stream_start_requires_public_streaming_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(
        obs_ops,
        "load_config",
        lambda: {"obs": {"enabled": True, "allow_public_streaming": False}},
    )
    monkeypatch.setattr(obs_ops, "_client", lambda: (_ for _ in ()).throw(AssertionError("client must not be opened")))

    result = obs_ops.call("start_streaming")

    assert result["status"] == "PERMISSION_DENIED"
    assert "allow_public_streaming" in result["reason"]


def test_obs_disabled_returns_permission_denied_without_connecting(monkeypatch) -> None:
    monkeypatch.setattr(obs_ops, "load_config", lambda: {"obs": {"enabled": False}})
    monkeypatch.setattr(obs_ops, "_client", lambda: (_ for _ in ()).throw(AssertionError("client must not be opened")))

    result = obs_ops.call("status")

    assert result["status"] == "PERMISSION_DENIED"
