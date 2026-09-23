from __future__ import annotations

from pathlib import Path

from codex_local_ops import animation_ops


def test_remotion_render_async_uses_job_manager(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(animation_ops, "assert_trusted_path", lambda path, must_exist=False: Path(path))
    monkeypatch.setattr(animation_ops.shutil, "which", lambda name: "npx" if name == "npx" else None)

    def fake_start(argv, *, cwd=None, label=None, cleanup_paths=None):
        captured.update({"argv": argv, "cwd": cwd, "label": label})
        return {"status": "STARTED", "session_id": "job_1234"}

    monkeypatch.setattr(animation_ops.jobs, "start", fake_start)

    result = animation_ops.render_async(str(tmp_path), "remotion")

    assert result["status"] == "STARTED"
    assert result["session_id"] == "job_1234"
    assert captured["argv"] == ["npx", "remotion", "render"]
    assert captured["cwd"] == tmp_path
    assert captured["label"] == "animation-remotion"
