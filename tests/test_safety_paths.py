import pytest

from codex_local_ops.config import load_config, save_config
from codex_local_ops.safety import assert_trusted_path


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
