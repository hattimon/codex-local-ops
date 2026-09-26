import pytest

from codex_local_ops.config import load_config, save_config
from codex_local_ops.safety import assert_trusted_path, sanitize


def test_trusted_root_blocks_traversal(monkeypatch, tmp_path):
    home = tmp_path / "user-home"
    root = tmp_path / "trusted"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(home / ".codex-local-ops"))
    config = load_config()
    config["projects"]["trusted_roots"] = [str(root)]
    save_config(config)
    with pytest.raises(PermissionError):
        assert_trusted_path(root / ".." / outside.name)


def test_usage_token_metrics_are_not_secrets_but_credentials_are() -> None:
    result = sanitize(
        {
            "input_tokens": 1,
            "output_tokens": 2,
            "total_tokens": 3,
            "reasoning_tokens": 4,
            "cache_read_tokens": 5,
            "cache_write_tokens": 6,
            "GITHUB_TOKEN": "github-secret",
            "API_TOKEN": "api-secret",
            "OAUTH_TOKEN": "oauth-secret",
        }
    )

    assert [result[key] for key in ("input_tokens", "output_tokens", "total_tokens")] == [1, 2, 3]
    assert [result[key] for key in ("reasoning_tokens", "cache_read_tokens", "cache_write_tokens")] == [4, 5, 6]
    assert result["GITHUB_TOKEN"] == "[REDACTED]"
    assert result["API_TOKEN"] == "[REDACTED]"
    assert result["OAUTH_TOKEN"] == "[REDACTED]"
